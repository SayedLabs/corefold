"""Rakiza Core PDF's page editor: the pages of one PDF, big, to write on. Type new text anywhere or change
the PDF's own text, add pictures and a signature, highlight, underline or cross out words, draw, add
shapes, cover things up, leave notes and fill in forms. Saving makes a new copy (pdfedit.py writes it);
the PDF that was opened is never changed.

The pages are drawn by PDFium on a thread of their own, only the ones in sight and at the size they're
shown, so a long PDF opens at once and scrolls smoothly. Nothing here is loaded until the editor is
opened."""
from __future__ import annotations

import logging
import math
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFontMetricsF, QIcon, QImage, QKeySequence, QPainter,
                           QPainterPath, QPen, QPixmap, QShortcut, QTextCursor, QTransform)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QFileDialog, QFrame, QGraphicsItem,
                               QGraphicsObject, QGraphicsScene, QGraphicsTextItem, QGraphicsView, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
                               QStyleOptionGraphicsItem, QStyle, QToolButton, QVBoxLayout, QWidget)

from . import icons, pdfedit, pdffonts, pdftools, sheet, work
from .atomic import tokens
from .atomic.widgets import Dropdown, FlowLayout, IconButton
from .core import IMAGE_EXT, norm, short_name
from .i18n import tr
from .theme import round_menu

if TYPE_CHECKING:
    from .pdfpage import PdfPage

log = logging.getLogger("corefold")

GAP = 18                 # between pages, in points
MAX_PIXELS = 22_000_000  # the biggest picture of one page kept (a page zoomed far in is drawn a little soft)
KEEP_NEAR = 2            # pages kept sharp around the ones in sight
THUMBS = 60              # small pictures kept of pages scrolled past (so they're never blank when back)
ZOOMS = (0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0)
# the tools: (key, words on the button, icon). Some have kinds, picked in the panel at the side.
TOOLS = [("select", "Select", "cursor"), ("edit", "Edit text", "text_edit_style"), ("text", "Add text", "textbox"),
         ("image", "Picture", "image_add"), ("sign", "Sign", "signature"), ("mark", "Highlight", "highlight"),
         ("draw", "Draw", "pen"), ("shape", "Shapes", "shapes"), ("note", "Note", "note"), ("link", "Link", "link"),
         ("cover", "Cover", "redact"), ("fill", "Fill in", "form"), ("stamp", "Stamp", "sticker"),
         ("form", "Form box", "textbox")]
TOOL_GROUPS = (("select",), ("edit", "text", "image", "sign", "fill", "form"), ("mark", "draw", "shape", "note", "stamp", "link"),
               ("cover",))
MARKS = [("highlight", "Highlight", "highlight"), ("underline", "Underline", "text_underline"),
         ("strike", "Cross out", "text_strikethrough")]
SHAPES = [("rect", "Rectangle", "rectangle_landscape"), ("ellipse", "Circle", "circle"),
          ("line", "Line", "line_horizontal_1"), ("arrow", "Arrow", "arrow_up_right")]
# Fill in: marks put on a form that isn't a real one (a printed form scanned or made into a PDF), with a click
FILL_KINDS = [("check", "Tick", "checkmark"), ("cross", "Cross", "dismiss"), ("dot", "Dot", "circle"),
              ("date", "Today's date", "calendar"), ("initials", "Initials", "signature")]
# stamps: (key, words on it, colour, with the date)
STAMPS = [("approved", "APPROVED", "#2e7d32", False), ("received", "RECEIVED", "#6a1b9a", True),
          ("draft", "DRAFT", "#1565c0", False), ("confidential", "CONFIDENTIAL", "#c62828", False),
          ("paid", "PAID", "#2e7d32", True), ("rejected", "REJECTED", "#c62828", True),
          ("custom", "Your own words…", "#c62828", False)]
# Form box: a real form box people type or tick in, in any PDF app
FORM_KINDS = [("formtext", "Box to type in", "textbox"), ("formcheck", "Box to tick", "checkmark")]
FORM_SIZES = {"formtext": (160.0, 20.0), "formcheck": (14.0, 14.0)}
STAMP_KINDS = [(k, words if k == "custom" else words.capitalize(), "sticker") for k, words, _c, _d in STAMPS]
MARK_KEYS = {k for k, _, _ in MARKS}
SHAPE_KEYS = {k for k, _, _ in SHAPES}
FILL_KEYS = {k for k, _, _ in FILL_KINDS}
STAMP_KEYS = {k for k, _, _ in STAMP_KINDS}
FORM_KEYS = {k for k, _, _ in FORM_KINDS}
FILL_MARK = 12.0         # how big a tick, cross or dot is, in points (it can be made bigger or smaller at the side)
HINTS = {"select": "Click something to select it and drag it to move it. Double-click text to change it.",
         "edit": "Click a paragraph or a line of the PDF's own text to change it. What can be changed has a box "
                 "around it.",
         "text": "Click where the text should go, then type. Drag the square at its corner to make it wrap.",
         "highlight": "Drag across words to highlight them.",
         "underline": "Drag across words to underline them.",
         "strike": "Drag across words to cross them out.",
         "draw": "Draw with the mouse or a pen.",
         "shape": "Drag to draw the shape.",
         "cover": "Drag over what should be hidden. What's under it is taken out of the file, not just hidden.",
         "note": "Click where the note should go.",
         "link": "Drag over the words or the place that should open the link, then type where it goes.",
         "fill": "Pick a mark at the side, then click where it goes on the form.",
         "stamp": "Pick a stamp at the side, then click where it goes.",
         "form": "Pick a box at the side, then click where it goes. Anyone can then type or tick in it, in any "
                 "PDF app."}
# what the panel at the side is called for each thing (selected, or the tool's)
TITLES = {"select": "Select", "edit": "Edit text", "text": "Text", "image": "Picture", "move": "Moved part",
          "cover": "Cover", "shape": "Shape", "draw": "Drawing", "highlight": "Highlight", "underline": "Underline",
          "strike": "Cross out", "note": "Note", "link": "Link", "line": "The PDF's own text",
          "picture": "The PDF's own picture", "drawn": "The PDF's own shape", "comment": "The PDF's own comment",
          "pdflink": "Link", "fill": "Fill in", "stamp": "Stamp", "form": "Form box", "formbox": "Form box"}
ALIGN_KEYS = [("left", "Align left", "align_left"), ("center", "Centre", "align_center"),
              ("right", "Align right", "align_right"), ("justify", "Justify", "align_justify")]
# line spacing: (words, from one line to the next in text sizes; None: the font's own)
SPACINGS = [("Tight", 1.0), ("Single", None), ("1.5 lines", 1.75), ("Double", 2.3)]
WIDTHS = {"Thin": 1.0, "Medium": 2.5, "Thick": 5.0}
FILLS = ["", "#ffffff", "#000000", "#ffe066", "#f8bbd0", "#bbdefb", "#c8e6c9"]
# what can be changed in the panel, for each kind of thing
STYLE_KEYS = {"text": ("font", "size", "color", "bold", "italic", "underline", "align", "spacing", "opacity"),
              "draw": ("color", "width", "opacity"), "shape": ("color", "width", "fill", "opacity"),
              "image": ("opacity",), "stamp": ("color", "opacity")}
OPACITY_KINDS = {"text", "draw", "shape", "image", "stamp"}
SELECTED_HINTS = {"text": "Double-click to change the words. Drag the square at its corner to make the lines wrap.",
                  "line": "Double-click to change these words, or drag them somewhere else.",
                  "picture": "Drag it to move it. Delete takes it out of the file.",
                  "image": "Drag the square at its corner to make it bigger or smaller.",
                  "link": "Drag the square at its corner to make the link's place bigger or smaller.",
                  "cover": "What's under it is taken out of the file when it's saved.",
                  "drawn": "Drag it to move it, or pick another colour. Delete takes it out of the file.",
                  "comment": "Drag it to move it. Double-click a note to change its words. Delete takes it away.",
                  "pdflink": "Type where it goes, or drag it to move it. Delete takes the link away.",
                  "stamp": "Drag the square at its corner to make it bigger or smaller.",
                  "formbox": "Drag the square at its corner to make it bigger or smaller. Its name is what a "
                             "form's list of answers shows."}
INSPECTOR_WIDTH = 240
ITEMS_MIME = "application/x-corefold-pdf-items"  # (what's added on the pages, copied)
COPYABLE = {"text", "image", "cover", "rect", "ellipse", "line", "arrow", "ink", "highlight", "underline", "strike",
            "note", "link", "stamp", "formbox"}
INK = ["#000000", "#1f3a93", "#d32f2f", "#2e7d32", "#f57c00", "#7b1fa2", "#ffffff"]
PENS = ["#ffd400", "#7cf27c", "#ff8ad8", "#7ec8ff", "#ffb35c"]
FIELD_TINT = QColor(70, 130, 255, 38)
THUMB = QSize(72, 96)    # the small pictures in the list of pages


# ----------------------------------------------------------------------------- drawing pages off the window's thread

class _Signals(QObject):
    opened = Signal(object)        # (sizes, fields) or an Exception
    image = Signal(int, float, object, object)  # page, scale, hidden pieces, QImage
    layout = Signal(int, object)
    thumb = Signal(int, object)                 # page, a small QImage of it (for the list of pages)
    read = Signal(int, object)                  # page, the words read from its picture (pdfedit.ocr_lines)


class Renderer:
    """One thread that draws pages and reads what's on them: the newest wishes first, old ones dropped."""

    def __init__(self, src: Path, password: str):
        self.src, self.password = src, password
        self.sig = _Signals()
        self.cv = threading.Condition()
        self.renders: dict[int, tuple[float, tuple]] = {}
        self.layouts: list[int] = []
        self.thumbs: list[tuple[int, float]] = []
        self.reads: list[int] = []
        self.stopped = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="corefold-pdf-editor")
        self.thread.start()

    def want(self, wishes: list[tuple[int, float, tuple]]):
        with self.cv:
            self.renders = {i: (s, h) for i, s, h in wishes}
            self.cv.notify()

    def want_layout(self, pages: list[int]):
        with self.cv:
            for i in pages:
                if i not in self.layouts:
                    self.layouts.append(i)
            self.cv.notify()

    def want_read(self, pages: list[int]):
        """Reads the words in these pages' pictures (Windows' own reader): scans, and fonts that don't say
        which letters they have."""
        with self.cv:
            for i in pages:
                if i not in self.reads:
                    self.reads.append(i)
            self.cv.notify()

    def want_thumbs(self, wishes: list[tuple[int, float]]):
        with self.cv:
            self.thumbs = list(wishes)
            self.cv.notify()

    def close(self):
        with self.cv:
            self.stopped = True
            self.renders.clear()
            self.layouts.clear()
            self.thumbs.clear()
            self.reads.clear()
            self.cv.notify()

    def _run(self):
        import pypdfium2 as pdfium
        from pypdf import PdfReader
        doc = None
        try:
            with pdftools.PDFIUM:
                doc = pdfium.PdfDocument(str(self.src), password=self.password or None)
                sizes = [tuple(doc.get_page_size(i)) for i in range(len(doc))]
            try:
                reader = PdfReader(str(self.src))
                if reader.is_encrypted:
                    reader.decrypt(self.password or "")
                fields = pdfedit.read_fields(reader)
            except Exception:
                log.debug("the form couldn't be read", exc_info=True)
                fields = []
            self.sig.opened.emit((sizes, fields))
            last = ""
            while True:
                with self.cv:
                    while not self.stopped and not self.renders and not self.layouts and not self.thumbs \
                            and not self.reads:
                        self.cv.wait()
                    if self.stopped:
                        return
                    if self.layouts and (not self.renders or last == "render"):
                        # (a page's pieces are read between two drawings: wishes to draw that keep coming,
                        # while the window settles, never leave them unread)
                        job = ("layout", self.layouts.pop(0))
                    elif self.renders:
                        i = next(iter(self.renders))
                        scale, hide = self.renders.pop(i)
                        job = ("render", i, scale, hide)
                    elif self.layouts:
                        job = ("layout", self.layouts.pop(0))
                    elif self.thumbs:
                        job = ("thumb", *self.thumbs.pop(0))
                    else:
                        job = ("read", self.reads.pop(0))
                    last = job[0]
                try:
                    if job[0] == "render":
                        _, i, scale, look = job
                        hide, paint = look
                        with pdftools.PDFIUM:
                            data, w, h, stride = pdfedit.render(doc, i, scale, hide, {
                                name: {"color": color, "fill": fill} for name, color, fill in paint})
                        img = QImage(data, w, h, stride, QImage.Format.Format_RGBX8888).copy()
                        self.sig.image.emit(i, scale, look, img)
                    elif job[0] == "thumb":
                        _, i, scale = job
                        with pdftools.PDFIUM:
                            data, w, h, stride = pdfedit.render(doc, i, scale, ())
                        self.sig.thumb.emit(i, QImage(data, w, h, stride, QImage.Format.Format_RGBX8888).copy())
                    elif job[0] == "read":
                        from .extras import read_lines
                        with pdftools.PDFIUM:
                            pic, scale = pdfedit.page_picture(doc, job[1])
                        try:
                            read = pdfedit.ocr_lines(read_lines(pic, enlarge=False), scale)
                        except Exception:  # (no reading language in Windows, an old Windows…)
                            log.debug("the words of page %s couldn't be read", job[1] + 1, exc_info=True)
                            read = None
                        self.sig.read.emit(job[1], read)
                    else:
                        with pdftools.PDFIUM:
                            layout = pdfedit.read_layout(doc, job[1])
                        self.sig.layout.emit(job[1], layout)
                except pdfium.PdfiumError:  # (a RuntimeError too: one page PDFium can't read isn't a closed editor)
                    log.debug("page %s couldn't be drawn or read", job[1] + 1, exc_info=True)
                except RuntimeError:
                    return  # (the editor was closed)
                except Exception:
                    log.debug("page %s couldn't be drawn or read", job[1] + 1, exc_info=True)
        except Exception as e:
            log.warning("the PDF couldn't be opened for editing: %s", e)
            try:
                self.sig.opened.emit(e)
            except RuntimeError:
                pass
        finally:
            if doc is not None:
                with pdftools.PDFIUM:
                    doc.close()


# ----------------------------------------------------------------------------- the pages

class PageItem(QGraphicsItem):
    """One page: its picture (sharp when in sight, a small one when not), white until it's drawn."""

    def __init__(self, index: int, w: float, h: float):
        super().__init__()
        self.index, self.w, self.h = index, w, h
        self.pm: QPixmap | None = None
        self.pm_key: tuple | None = None       # (scale, (hidden pieces, new colours)) the picture was drawn with
        self.thumb: QPixmap | None = None
        self.layout: dict | None = None
        self.asked_layout = False
        self.pieces: list[OldPiece] = []
        self.blank = False                     # a blank page added in the editor (nothing of the PDF's on it)
        self.setZValue(0)

    def boundingRect(self) -> QRectF:
        return QRectF(-3, -3, self.w + 6, self.h + 6)

    def paint(self, p: QPainter, option, widget=None):
        rect = QRectF(0, 0, self.w, self.h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawRect(rect.translated(0, 1.5))
        p.fillRect(rect, QColor("#ffffff"))
        pm = self.pm or self.thumb
        if pm is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawPixmap(rect, pm, QRectF(pm.rect()))

    def keep_small(self):
        """Lets go of the sharp picture, keeping a small one."""
        if self.pm is not None:
            self.thumb = self.pm.scaled(max(1, self.pm.width() // 4), max(1, self.pm.height() // 4),
                                        Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            self.pm, self.pm_key = None, None


class Handle(QGraphicsItem):
    """The little square in a selected box's corner: drag it to make the box bigger or smaller."""
    SIZE = 10

    def __init__(self, parent):
        super().__init__(parent)
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self.setCursor(Qt.SizeFDiagCursor)
        self.setZValue(10)
        self.setVisible(False)

    def boundingRect(self) -> QRectF:
        s = self.SIZE
        return QRectF(-s / 2, -s / 2, s, s)

    def paint(self, p, option, widget=None):
        t = tokens.current()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#ffffff"), 1.5))
        p.setBrush(t.accent)
        p.drawRoundedRect(self.boundingRect().adjusted(1, 1, -1, -1), 2, 2)

    def mousePressEvent(self, e):
        e.accept()

    def mouseMoveEvent(self, e):
        parent = self.parentItem()
        parent.resize_to(parent.mapFromScene(e.scenePos()))

    def mouseReleaseEvent(self, e):
        parent = self.parentItem()
        if hasattr(parent, "resized"):
            parent.resized()
        parent.editor.commit()


def _selection_pen() -> QPen:
    pen = QPen(tokens.current().accent, 1.2, Qt.DashLine)
    pen.setCosmetic(True)
    return pen


class Mark(QGraphicsObject):
    """Something added on a page (all but text): moved by dragging when the Select tool is on."""
    kind = ""

    def __init__(self, editor: "PdfEditor", page: PageItem, d: dict):
        super().__init__(page)
        self.editor, self.page, self.d = editor, page, dict(d)
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(2)
        self.setAcceptHoverEvents(True)
        self.setOpacity(_opacity(self.d))

    def restyle(self):
        self.setOpacity(_opacity(self.d))
        self.prepareGeometryChange()
        self.update()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.page is not None:
            # stays on its page
            r = self.boundingRect()
            x = min(max(value.x(), -r.left() - r.width() + 8), self.page.w - r.left() - 8)
            y = min(max(value.y(), -r.top() - r.height() + 8), self.page.h - r.top() - 8)
            return QPointF(x, y)
        if change == QGraphicsItem.ItemSelectedHasChanged:
            for child in self.childItems():
                if isinstance(child, Handle):
                    child.setVisible(bool(value))
            self.editor.selection_changed()
        return super().itemChange(change, value)

    def data(self) -> dict:
        return dict(self.d, page=self.page.index)

    def _outline(self, p):
        if self.isSelected():
            p.setPen(_selection_pen())
            p.setBrush(Qt.NoBrush)
            p.drawRect(self.boundingRect().adjusted(1, 1, -1, -1))


class BoxMark(Mark):
    """A picture, a signature, a cover, or a piece of the PDF that was moved: a box that can be resized."""

    def __init__(self, editor, page, d: dict, pixmap: QPixmap | None = None):
        super().__init__(editor, page, d)
        self.kind = d["kind"]
        self.w, self.h = float(d["w"]), float(d["h"])
        self.ratio = self.w / self.h if self.h else 1
        self.pixmap = pixmap
        if self.pixmap is None and self.kind == "image":
            img = QImage(str(d.get("path", "")))
            if not img.isNull():
                most = 1600
                if max(img.width(), img.height()) > most:
                    img = img.scaled(most, most, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.pixmap = QPixmap.fromImage(img)
        self.setPos(float(d["x"]), float(d["y"]))
        self.handle = Handle(self)
        self.handle.setPos(self.w, self.h)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.w, self.h)

    def paint(self, p, option, widget=None):
        rect = QRectF(0, 0, self.w, self.h)
        if self.kind == "link":
            accent = tokens.current().accent
            p.fillRect(rect, tokens.alpha(accent, 34))
            pen = QPen(accent if self.d.get("url") or self.d.get("target") is not None else QColor("#d32f2f"),
                       1, Qt.DashLine)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.drawRect(rect)
            side = min(12.0, self.h - 2, self.w - 2)
            if side > 5:
                icons.paint(p, QRectF(2, 2, side, side), "link", accent)
        elif self.kind == "stamp":
            pdfedit.draw_stamp(p, rect, self.d)
        elif self.kind == "formbox":
            p.fillRect(rect, FIELD_TINT)
            pen = QPen(tokens.current().accent, 1)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.drawRect(rect)
            if self.d.get("form") == "formcheck":
                side = min(rect.width(), rect.height()) * 0.7
                icons.paint(p, QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side),
                            "checkmark", QColor(0, 0, 0, 70))
            elif rect.height() > 7:
                f = p.font()
                f.setPointSizeF(max(4.0, min(9.0, rect.height() * 0.5)))
                p.setFont(f)
                p.setPen(QColor(0, 0, 0, 110))
                p.drawText(rect.adjusted(3, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft, str(self.d.get("name", "")))
        elif self.kind == "cover":
            p.fillRect(rect, QColor(self.d.get("color", "#ffffff")))
            if self.d.get("color", "#ffffff").lower() == "#ffffff" and not self.isSelected():
                pen = QPen(QColor(0, 0, 0, 30), 1, Qt.DotLine)
                pen.setCosmetic(True)
                p.setPen(pen)
                p.drawRect(rect)
        elif self.pixmap is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            turn = int(self.d.get("turn", 0)) % 360
            if turn:
                p.save()
                p.translate(rect.center())
                p.rotate(turn)
                w, h = (rect.height(), rect.width()) if turn in (90, 270) else (rect.width(), rect.height())
                p.drawPixmap(QRectF(-w / 2, -h / 2, w, h), self.pixmap, QRectF(self.pixmap.rect()))
                p.restore()
            else:
                p.drawPixmap(rect, self.pixmap, QRectF(self.pixmap.rect()))
        self._outline(p)

    def resize_to(self, pt: QPointF):
        w, h = max(6.0, pt.x()), max(6.0, pt.y())
        if self.kind == "formbox" and self.d.get("form") == "formcheck":  # (a box to tick stays square)
            w = h = max(w, h)
        elif self.kind in ("image", "move", "stamp"):  # (pictures and stamps keep their shape)
            if w / self.ratio > h:
                h = w / self.ratio
            else:
                w = h * self.ratio
        self.prepareGeometryChange()
        self.w, self.h = w, h
        self.handle.setPos(w, h)
        self.update()

    def data(self) -> dict:
        d = dict(self.d, page=self.page.index, x=round(self.x(), 2), y=round(self.y(), 2), w=round(self.w, 2),
                 h=round(self.h, 2))
        return d


class ShapeMark(Mark):
    """A rectangle, circle, line or arrow between two points."""

    def __init__(self, editor, page, d: dict):
        super().__init__(editor, page, d)
        self.kind = d["kind"]
        x1, y1, x2, y2 = (float(v) for v in d["points"])
        self.a, self.b = QPointF(x1, y1), QPointF(x2, y2)

    def set_end(self, pt: QPointF):
        self.prepareGeometryChange()
        self.b = pt
        self.update()

    def boundingRect(self) -> QRectF:
        pad = float(self.d.get("width", 2)) * 3 + 6
        return QRectF(self.a, self.b).normalized().adjusted(-pad, -pad, pad, pad)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        if self.kind in ("line", "arrow"):
            from PySide6.QtGui import QPainterPathStroker
            line = QPainterPath(self.a)
            line.lineTo(self.b)
            stroker = QPainterPathStroker()
            stroker.setWidth(max(8.0, float(self.d.get("width", 2)) + 6))
            return stroker.createStroke(line)
        path.addRect(QRectF(self.a, self.b).normalized().adjusted(-4, -4, 4, 4))
        return path

    def paint(self, p, option, widget=None):
        d = dict(self.d, points=[self.a.x(), self.a.y(), self.b.x(), self.b.y()])
        d.pop("opacity", None)  # (the whole item is see-through already)
        pdfedit.draw_mark(p, d)
        if self.isSelected():
            p.setPen(_selection_pen())
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(self.a, self.b).normalized().adjusted(-3, -3, 3, 3))

    def data(self) -> dict:
        o = self.pos()
        return dict(self.d, page=self.page.index, points=[round(self.a.x() + o.x(), 2), round(self.a.y() + o.y(), 2),
                                                          round(self.b.x() + o.x(), 2), round(self.b.y() + o.y(), 2)])


class InkMark(Mark):
    """A line drawn by hand."""
    kind = "ink"

    def __init__(self, editor, page, d: dict):
        super().__init__(editor, page, d)
        self.strokes = [[QPointF(*pt) for pt in s] for s in d.get("strokes", [])]
        self._path()

    def _path(self):
        self.prepareGeometryChange()
        path = QPainterPath()
        for s in self.strokes:
            if s:
                path.moveTo(s[0])
                for pt in s[1:]:
                    path.lineTo(pt)
                if len(s) == 1:
                    path.lineTo(s[0] + QPointF(0.01, 0.01))
        self.path = path
        self.update()

    def add_point(self, pt: QPointF):
        last = self.strokes[-1][-1]
        if (pt - last).manhattanLength() < 0.6:
            return
        self.strokes[-1].append(pt)
        self._path()

    def boundingRect(self) -> QRectF:
        pad = float(self.d.get("width", 2)) + 4
        return self.path.boundingRect().adjusted(-pad, -pad, pad, pad)

    def shape(self) -> QPainterPath:
        from PySide6.QtGui import QPainterPathStroker
        stroker = QPainterPathStroker()
        stroker.setWidth(float(self.d.get("width", 2)) + 8)
        return stroker.createStroke(self.path)

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(self.d.get("color", "#000000")), float(self.d.get("width", 2)), Qt.SolidLine,
                      Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(self.path)
        self._outline(p)

    def data(self) -> dict:
        o = self.pos()
        return dict(self.d, page=self.page.index,
                    strokes=[[[round(pt.x() + o.x(), 2), round(pt.y() + o.y(), 2)] for pt in s] for s in self.strokes])


class MarkupMark(Mark):
    """A highlight, underline or crossing out over words."""

    def __init__(self, editor, page, d: dict):
        super().__init__(editor, page, d)
        self.kind = d["kind"]
        self.setFlag(QGraphicsItem.ItemIsMovable, False)
        self.setZValue(1.5)
        self.set_rects(d.get("rects", []))

    def set_rects(self, rects):
        self.prepareGeometryChange()
        self.rects = [QRectF(*r) for r in rects]
        self.update()

    def boundingRect(self) -> QRectF:
        r = QRectF()
        for x in self.rects:
            r = r.united(x)
        return r.adjusted(-2, -2, 2, 2)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        for r in self.rects:
            path.addRect(r)
        return path

    def paint(self, p, option, widget=None):
        color = QColor(self.d.get("color", "#ffd400"))
        if self.kind == "highlight":
            p.setCompositionMode(QPainter.CompositionMode_Multiply)
            for r in self.rects:
                p.fillRect(r, color)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        else:
            pdfedit.draw_mark(p, dict(self.d, rects=[[r.x(), r.y(), r.width(), r.height()] for r in self.rects]))
        if self.isSelected():
            p.setPen(_selection_pen())
            p.setBrush(Qt.NoBrush)
            for r in self.rects:
                p.drawRect(r.adjusted(-1, -1, 1, 1))

    def data(self) -> dict:
        return dict(self.d, page=self.page.index,
                    rects=[[round(r.x(), 2), round(r.y(), 2), round(r.width(), 2), round(r.height(), 2)]
                           for r in self.rects])


class NoteMark(Mark):
    """A sticky note: shown as a small note on the page, its words when the mouse is on it."""
    kind = "note"
    SIZE = 20

    def __init__(self, editor, page, d: dict):
        super().__init__(editor, page, d)
        self.setPos(float(d["x"]), float(d["y"]))
        self.setToolTip(d.get("text", ""))

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.SIZE, self.SIZE)

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#b38f00"), 0.8))
        p.setBrush(QColor("#ffe066"))
        p.drawRoundedRect(QRectF(1, 1, self.SIZE - 2, self.SIZE - 2), 2.5, 2.5)
        icons.paint(p, QRectF(3, 3, self.SIZE - 6, self.SIZE - 6), "note", QColor("#5c4a00"))
        self._outline(p)

    def mouseDoubleClickEvent(self, e):
        self.editor.edit_note(self)

    def data(self) -> dict:
        return dict(self.d, page=self.page.index, x=round(self.x(), 2), y=round(self.y(), 2))


class TextMark(QGraphicsTextItem):
    """Text typed on a page (new, or a line of the PDF's own text being changed)."""
    kind = "text"

    def __init__(self, editor: "PdfEditor", page: PageItem, d: dict):
        super().__init__(page)
        self.editor, self.page, self.d = editor, page, dict(d)
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable |
                      QGraphicsItem.ItemIsFocusable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(3)
        self.anchor = None  # right edge kept in place while typing right-to-left text
        self.document().setPlainText(d.get("text", ""))
        self.restyle()
        self.setPos(float(d["x"]), float(d["y"]))
        if d.get("turn"):  # (a turned line of the PDF's own: turned around where its words start)
            self.setTransformOriginPoint(QPointF(*(float(v) for v in d.get("pivot") or (0, 0))))
            self.setRotation(float(d["turn"]))
        if d.get("rtl"):
            self.anchor = self.x() + self.boundingRect().width()
        self._fonting = False
        self.handle = Handle(self)
        self.handle.setCursor(Qt.SizeHorCursor)
        self.document().contentsChanged.connect(self._grown)
        self.document().contentsChanged.connect(self._arabic_font)
        self._grown()

    def _arabic_font(self):
        """Arabic typed in a line whose Arabic words have a font of their own goes in that font."""
        if self.d.get("font_ar") and not self._fonting:
            self._fonting = True
            try:
                pdfedit.arabic_font(self.d, self.document())
            finally:
                self._fonting = False

    def restyle(self):
        pdfedit.text_document(self.d, self.document())
        self.setDefaultTextColor(QColor(self.d.get("color", "#000000")))
        self.setFont(pdfedit.font_for(self.d))
        self.setOpacity(_opacity(self.d))
        self._grown()

    def _grown(self):
        doc = self.document()
        if self.d.get("width"):  # (a box of its own width: the words wrap in it, like a paragraph's)
            doc.setTextWidth(float(self.d["width"]))
        else:
            doc.setTextWidth(-1)
            doc.setTextWidth(max(4.0, doc.idealWidth()))
        if self.anchor is not None:
            self.setX(self.anchor - self.boundingRect().width())
        handle = getattr(self, "handle", None)
        if handle is not None:
            r = self.boundingRect()
            handle.setPos(r.right(), r.bottom())

    def resize_to(self, pt: QPointF):
        """The square at the corner was dragged: the box is that wide, and the words wrap in it."""
        if self.anchor is not None:  # (right-to-left text kept its right edge: it now has a width of its own)
            self.anchor = None
            self.d.setdefault("align", "right")
        self.d["width"] = round(max(24.0, pt.x()), 2)
        self.restyle()

    def resized(self):
        self.editor.selection_changed()

    def text(self) -> str:
        return self.document().toPlainText()

    def editing(self) -> bool:
        return bool(self.textInteractionFlags() & Qt.TextEditorInteraction)

    def start_edit(self, at: QPointF | None = None):
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        if not self.editor.view.hasFocus():
            self.editor.view.setFocus(Qt.MouseFocusReason)
        self.setFocus(Qt.MouseFocusReason)
        cur = self.textCursor()
        if at is not None:
            pos = self.document().documentLayout().hitTest(self.mapFromScene(at), Qt.FuzzyHit)
            cur.setPosition(max(0, pos))
        else:
            cur.movePosition(QTextCursor.End)
        self.setTextCursor(cur)
        self.editor.selection_changed()

    def end_edit(self):
        if not self.editing():
            return
        cur = self.textCursor()
        cur.clearSelection()
        self.setTextCursor(cur)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        if not self.text().strip() and not self.d.get("replaces"):
            self.editor.drop_item(self)
            return
        if self.d.get("replaces") and self.text() == self.d.get("was", self.text() + "?"):
            # a line opened but not changed: the PDF's own line stays as it was
            self.editor.drop_item(self)
            return
        self.editor.commit()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        # (a menu opening, or another window coming to the front, doesn't stop the typing)
        if e.reason() not in (Qt.PopupFocusReason, Qt.ActiveWindowFocusReason):
            QTimer.singleShot(0, self._maybe_end)

    def _maybe_end(self):
        # (the options at the side, or the list of fonts, being used for the words being typed: still typing)
        if self.editor.keeps_typing(QApplication.focusWidget()):
            return
        self.end_edit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.clearFocus()
            self.end_edit()
            e.accept()
            return
        super().keyPressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if not self.editing():
            self.start_edit(e.scenePos())
            return
        super().mouseDoubleClickEvent(e)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.page is not None:
            r = self.boundingRect()
            x = min(max(value.x(), -r.width() + 8), self.page.w - 8)
            y = min(max(value.y(), -r.height() + 8), self.page.h - 8)
            return QPointF(x, y)
        if change == QGraphicsItem.ItemPositionHasChanged and self.anchor is not None and \
                self.editor.dragging_items:
            self.anchor = self.x() + self.boundingRect().width()
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self.handle.setVisible(bool(value))
            self.editor.selection_changed()
        return super().itemChange(change, value)

    def paint(self, p, option, widget=None):
        opt = QStyleOptionGraphicsItem(option)
        opt.state &= ~(QStyle.State_Selected | QStyle.State_HasFocus)
        super().paint(p, opt, widget)
        if self.isSelected() or self.editing():
            p.setPen(_selection_pen())
            p.setBrush(Qt.NoBrush)
            p.drawRect(self.boundingRect().adjusted(-1, -1, 1, 1))

    def data(self) -> dict:
        d = dict(self.d, page=self.page.index, text=self.text(), x=round(self.x(), 2), y=round(self.y(), 2))
        d.pop("rtl", None)
        if self.anchor is not None:
            d["rtl"] = True
        return d


class OldPiece(QGraphicsObject):
    """Something that's already in the PDF: a line of text, a picture, a drawn shape or an annotation (a
    note, a highlight, a link…). Shows a box when it can be changed, and is turned into a text box (to change
    the words) or moved when it's clicked."""

    def __init__(self, editor: "PdfEditor", page: PageItem, kind: str, d: dict):
        super().__init__(page)
        self.editor, self.page, self.kind, self.d = editor, page, kind, d
        self.objs = tuple(d.get("objs") or (d["obj"],))  # (a bunch of small shapes: all of them)
        # (the page's background: not picked by a click or a box drawn inside the page, only by a box across
        # its edge or by Select all, so it isn't dragged or deleted by mistake)
        self.background = bool(d.get("background"))
        self.rect = QRectF(*d["rect"])
        self.setPos(self.rect.topLeft())
        self.rect.moveTo(0, 0)
        # (on top: annotations, then text, then pictures, then shapes, the smaller ones over the bigger ones)
        self.setZValue({"annot": 1.3, "line": 1.0, "image": 0.9}.get(kind, 0.3 + 0.5 * (1 - float(d.get("share", 0)))))
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.hover = False
        self.press: QPointF | None = None
        self.moving: BoxMark | None = None
        self.setCursor(Qt.IBeamCursor if kind == "line" else Qt.SizeAllCursor)
        tip = d.get("url") or d.get("text") if kind == "annot" else ""
        if tip:
            self.setToolTip(str(tip)[:300])

    def tools(self) -> tuple:
        """The tools it can be clicked with: text with Edit text too; shapes and annotations only with Select."""
        if self.background:
            return ()
        return ("select", "edit") if self.kind in ("line", "image") else ("select",)

    def boundingRect(self) -> QRectF:
        return self.rect.adjusted(-3, -3, 3, 3)  # (a thin line is still easy to pick up)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.boundingRect())
        if self.background:  # (only its edge)
            path.addRect(self.rect.adjusted(6, 6, -6, -6))
            path.setFillRule(Qt.OddEvenFill)
        return path

    def paint(self, p, option, widget=None):
        tool = self.editor.tool
        hot = self.hover or self.editor.para_hot(self)
        if self.isSelected():
            p.setPen(_selection_pen())
        elif hot or (tool == "edit" and self.kind == "line"):
            pen = QPen(tokens.alpha(tokens.current().accent, 230 if hot else 110), 1,
                       Qt.SolidLine if hot else Qt.DashLine)
            pen.setCosmetic(True)
            p.setPen(pen)
        else:
            return
        p.setBrush(tokens.alpha(tokens.current().accent, 26) if hot else Qt.NoBrush)
        p.drawRect(self.rect.adjusted(-1, -1, 1, 1))

    def hoverEnterEvent(self, e):
        self.hover = True
        self.editor.hover_piece(self, True)
        self.update()

    def hoverLeaveEvent(self, e):
        self.hover = False
        self.editor.hover_piece(self, False)
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            e.ignore()
            return
        if self.editor.tool == "edit" and self.kind == "line":
            self.editor.edit_line(self, e.scenePos())
            e.accept()
            return
        self.editor.scene.clearSelection()
        self.setSelected(True)
        self.press = e.scenePos()
        e.accept()

    def mouseMoveEvent(self, e):
        if self.press is None:
            return
        delta = self.page.mapFromScene(e.scenePos()) - self.page.mapFromScene(self.press)
        if self.moving is None:
            if delta.manhattanLength() < 3:
                return
            self.moving = self.editor.move_piece(self)
        if self.moving is not None:
            self.moving.setPos(self.pos() + delta)

    def mouseReleaseEvent(self, e):
        if self.moving is not None:
            item, self.moving = self.moving, None
            self.setOpacity(1)
            self.editor._pieces_shown(self.page)
            self.editor.scene.clearSelection()
            item.setSelected(True)
            self.editor.commit()
        self.press = None

    def mouseDoubleClickEvent(self, e):
        if self.kind == "line":
            self.editor.edit_line(self, e.scenePos())
        elif self.kind == "annot":
            self.editor.open_annot(self)


class FieldItem(QGraphicsObject):
    """A box of the PDF's form: type in it, tick it, or pick from its list."""

    def __init__(self, editor: "PdfEditor", page: PageItem, f: dict):
        super().__init__(page)
        self.editor, self.page, self.f = editor, page, f
        x, y, w, h = f["rect"]
        self.setPos(x, y)
        self.w, self.h = w, h
        self.setZValue(4)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.IBeamCursor if f["kind"] == "text" else Qt.PointingHandCursor)
        self.setToolTip(f["name"])
        self.typing: QGraphicsTextItem | None = None

    def value(self) -> str:
        return self.editor.fields.get(self.f["name"], self.f.get("value", ""))

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.w, self.h)

    def _size(self) -> float:
        return self.f.get("size") or min(12.0, max(6.0, self.h * 0.66))

    def paint(self, p, option, widget=None):
        rect = QRectF(0, 0, self.w, self.h)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(rect, FIELD_TINT)
        pen = QPen(tokens.alpha(tokens.current().accent, 160), 1)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        kind, value = self.f["kind"], self.value()
        if kind in ("check", "radio"):
            side = min(self.w, self.h)
            box = QRectF((self.w - side) / 2, (self.h - side) / 2, side, side).adjusted(1, 1, -1, -1)
            if kind == "radio":
                p.drawEllipse(box)
            else:
                p.drawRect(box)
            if value == self.f["on"]:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#111111"))
                if kind == "radio":
                    p.drawEllipse(box.adjusted(side * 0.27, side * 0.27, -side * 0.27, -side * 0.27))
                else:
                    tick = QPainterPath(QPointF(box.left() + side * 0.2, box.center().y()))
                    tick.lineTo(box.left() + side * 0.42, box.bottom() - side * 0.22)
                    tick.lineTo(box.right() - side * 0.18, box.top() + side * 0.22)
                    p.setPen(QPen(QColor("#111111"), max(1.0, side * 0.12), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    p.setBrush(Qt.NoBrush)
                    p.drawPath(tick)
            return
        p.drawRect(rect)
        if self.typing is not None:
            return
        font = pdfedit.font_for({"font": "Arial", "size": self._size()})
        p.setFont(font)
        p.setPen(QColor("#111111"))
        shown = "•" * len(value) if self.f.get("secret") else value
        flags = Qt.AlignVCenter if not self.f.get("multiline") else Qt.AlignTop | Qt.TextWordWrap
        flags |= (Qt.AlignRight if pdfedit._strong(shown) == "R" else Qt.AlignLeft) | Qt.AlignAbsolute
        p.drawText(rect.adjusted(2, 1, -2, -1), int(flags), shown)
        if kind == "choice":
            p.setPen(QColor("#555555"))
            p.drawText(rect.adjusted(2, 0, -3, 0), int(Qt.AlignVCenter | Qt.AlignRight | Qt.AlignAbsolute), "▼")

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            e.ignore()
            return
        e.accept()
        kind = self.f["kind"]
        if kind == "check":
            self.editor.set_field(self.f, "/Off" if self.value() == self.f["on"] else self.f["on"])
        elif kind == "radio":
            self.editor.set_field(self.f, self.f["on"])
        elif kind == "choice":
            m = QMenu(self.editor)
            round_menu(m)
            for opt in self.f.get("options", []):
                act = m.addAction(opt)
                act.setCheckable(True)
                act.setChecked(opt == self.value())
                act.triggered.connect(lambda _=False, o=opt: self.editor.set_field(self.f, o))
            m.aboutToHide.connect(lambda: QTimer.singleShot(0, m.deleteLater))
            self.editor.field_menu = m
            view = self.editor.view
            m.popup(view.mapToGlobal(view.mapFromScene(self.mapToScene(QPointF(0, self.h)))))
        else:
            self.start_typing()

    def start_typing(self):
        if self.typing is not None:
            return
        if not self.editor.view.hasFocus():
            self.editor.view.setFocus(Qt.MouseFocusReason)
        t = _FieldText(self)
        self.typing = t
        self.update()
        t.setFocus(Qt.MouseFocusReason)

    def stop_typing(self, keep: bool = True):
        t, self.typing = self.typing, None
        if t is None:
            return
        text = t.toPlainText()
        if not self.f.get("multiline"):
            text = text.replace("\n", " ")
        if self.f.get("limit"):
            text = text[:self.f["limit"]]
        t.setParentItem(None)
        if t.scene() is not None:
            t.scene().removeItem(t)
        if keep and text != self.value():
            self.editor.set_field(self.f, text)
        self.update()


class _FieldText(QGraphicsTextItem):
    """Typing in a form's text box."""

    def __init__(self, field: FieldItem):
        super().__init__(field)
        self.field = field
        d = {"font": "Arial", "size": field._size()}
        self.document().setPlainText(field.value())
        pdfedit.text_document(d, self.document())
        self.document().setDocumentMargin(1)
        self.setDefaultTextColor(QColor("#111111"))
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setPos(1, max(0.0, (field.h - self.boundingRect().height()) / 2) if not field.f.get("multiline") else 0)
        cur = self.textCursor()
        cur.movePosition(QTextCursor.End)
        self.setTextCursor(cur)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not self.field.f.get("multiline"):
            self.clearFocus()
            QTimer.singleShot(0, self.field.stop_typing)  # (once the key is done with this item)
            return
        if e.key() == Qt.Key_Escape:
            QTimer.singleShot(0, lambda: self.field.stop_typing(keep=False))
            return
        if e.key() == Qt.Key_Tab:
            self.clearFocus()
            field = self.field
            QTimer.singleShot(0, lambda: (field.stop_typing(), field.editor.next_field(field)))
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if e.reason() not in (Qt.PopupFocusReason, Qt.ActiveWindowFocusReason):
            QTimer.singleShot(0, lambda: self.field.stop_typing())


# ----------------------------------------------------------------------------- the view

class FoundMarks(QGraphicsItem):
    """What Find found on one page: a soft box over each, a stronger one over the one it's on."""

    def __init__(self, page: PageItem, rects: list, current: int):
        super().__init__(page)
        self.rects, self.current = rects, current
        self.setZValue(4)
        self.setAcceptedMouseButtons(Qt.NoButton)

    def boundingRect(self) -> QRectF:
        r = QRectF()
        for x in self.rects:
            r = r.united(x)
        return r.adjusted(-3, -3, 3, 3)

    def paint(self, p, option, widget=None):
        accent = tokens.current().accent
        for n, r in enumerate(self.rects):
            on = n == self.current
            p.setPen(QPen(accent, 1.4 if on else 0.6))
            p.setBrush(QColor(255, 196, 0, 150 if on else 70))
            p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 1.5, 1.5)


class PagesList(QListWidget):
    """The list of pages at the side. Delete deletes the picked pages, Ctrl + ↑ ↓ move them (only while the list
    itself has the keyboard: elsewhere those keys are the pages' own)."""

    def __init__(self, editor: "PdfEditor"):
        super().__init__()
        self.editor = editor

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete:
            if self.selectedItems():  # (only pages picked here: never the page that just happens to be in sight)
                self.editor.delete_pages()
            return
        if e.modifiers() & Qt.ControlModifier and e.key() in (Qt.Key_Up, Qt.Key_Down):
            self.editor.move_pages(-1 if e.key() == Qt.Key_Up else 1)
            return
        super().keyPressEvent(e)


class EditorView(QGraphicsView):
    """The pages one under the other, with the tools' mouse work."""

    def __init__(self, editor: "PdfEditor"):
        super().__init__(editor.scene)
        self.editor = editor
        self.setObjectName("pdfEditorView")
        self.setFrameShape(QFrame.NoFrame)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)
        self.drag: dict | None = None
        self.panning: QPointF | None = None

    def drawBackground(self, p, rect):
        p.fillRect(rect, tokens.current().canvas)

    # ---- the mouse

    def _page_at(self, pos) -> tuple[PageItem | None, QPointF]:
        sp = self.mapToScene(pos)
        page = self.editor.page_at(sp)
        return page, (page.mapFromScene(sp) if page else sp)

    def mousePressEvent(self, e):
        ed = self.editor
        if e.button() == Qt.MiddleButton:
            self.panning = e.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        if e.button() != Qt.LeftButton:
            super().mousePressEvent(e)
            return
        tool = ed.active_tool()
        item = self.itemAt(e.position().toPoint())
        while item is not None and isinstance(item, Handle) is False and item.parentItem() is not None and \
                not isinstance(item, (Mark, TextMark, OldPiece, FieldItem, _FieldText, Handle)):
            item = item.parentItem()
        if isinstance(item, (FieldItem, _FieldText, Handle)) or \
                (isinstance(item, TextMark) and item.editing()):
            super().mousePressEvent(e)
            return
        ed.end_text_edits()
        if tool in ("select", "edit"):
            ed.dragging_items = True
            super().mousePressEvent(e)
            return
        page, pt = self._page_at(e.position().toPoint())
        if tool == "text":
            if isinstance(item, TextMark):
                ed.scene.clearSelection()
                item.setSelected(True)
                item.start_edit(self.mapToScene(e.position().toPoint()))
                return
            if page is not None:
                ed.new_text(page, pt)
            return
        if page is None:
            return
        if tool == "note":
            ed.new_note(page, pt)
            return
        if tool == "fill":
            ed.new_fill(page, pt)
            return
        if tool == "stamp":
            ed.new_stamp(page, pt)
            return
        if tool == "form":
            ed.new_form_box(page, pt)
            return
        ed.scene.clearSelection()
        self.drag = {"page": page, "start": pt, "tool": tool, "item": None}
        if tool == "draw":
            st = ed.style["draw"]
            self.drag["item"] = ed.add_item({"kind": "ink", "page": page.index, "strokes": [[[pt.x(), pt.y()]]],
                                             "color": st["color"], "width": st["width"]}, commit=False)

    def mouseMoveEvent(self, e):
        if self.panning is not None:
            delta = e.position() - self.panning
            self.panning = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - round(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - round(delta.y()))
            return
        if self.drag is None:
            super().mouseMoveEvent(e)
            return
        ed, page = self.editor, self.drag["page"]
        pt = page.mapFromScene(self.mapToScene(e.position().toPoint()))
        pt = QPointF(min(max(pt.x(), 0), page.w), min(max(pt.y(), 0), page.h))
        tool, item = self.drag["tool"], self.drag["item"]
        if tool == "draw":
            item.add_point(pt)
            return
        if (pt - self.drag["start"]).manhattanLength() < 2 and item is None:
            return
        if tool in ("highlight", "underline", "strike"):
            rects = ed.text_rects(page, self.drag["start"], pt, tool)
            if item is None:
                self.drag["item"] = ed.add_item({"kind": tool, "page": page.index, "rects": rects,
                                                 "color": ed.style[tool]["color"]}, commit=False)
            else:
                item.set_rects(rects)
        elif tool in ("cover", "link"):
            r = QRectF(self.drag["start"], pt).normalized()
            if item is not None:
                ed.drop_item(item, commit=False)
            d = {"kind": tool, "page": page.index, "x": r.x(), "y": r.y(), "w": max(r.width(), 1),
                 "h": max(r.height(), 1)}
            if tool == "cover":
                d["color"] = ed.style["cover"]["color"]
            self.drag["item"] = ed.add_item(d, commit=False)
        elif tool in ("rect", "ellipse", "line", "arrow"):
            if item is None:
                st = ed.style["shape"]
                s = self.drag["start"]
                self.drag["item"] = ed.add_item({"kind": tool, "page": page.index,
                                                 "points": [s.x(), s.y(), pt.x(), pt.y()],
                                                 "color": st["color"], "width": st["width"]}, commit=False)
            else:
                item.set_end(pt)

    def mouseReleaseEvent(self, e):
        if self.panning is not None and e.button() == Qt.MiddleButton:
            self.panning = None
            self.viewport().unsetCursor()
            return
        ed = self.editor
        if self.drag is None:
            super().mouseReleaseEvent(e)
            if ed.dragging_items:
                ed.dragging_items = False
                ed.commit()
            return
        drag, self.drag = self.drag, None
        item = drag["item"]
        if item is None:
            return
        if drag["tool"] in ("highlight", "underline", "strike") and not item.rects:
            ed.drop_item(item)
            return
        if drag["tool"] in ("rect", "ellipse", "line", "arrow", "cover", "link"):
            if drag["tool"] in ("cover", "link") and (item.w < 3 or item.h < 3):
                ed.drop_item(item)
                return
            if drag["tool"] not in ("cover", "link") and (item.a - item.b).manhattanLength() < 3:
                ed.drop_item(item)
                return
        ed.commit()
        if drag["tool"] == "link":  # (where it goes is typed next, at the side)
            ed.scene.clearSelection()
            item.setSelected(True)
            ed.inspector.setVisible(True)
            ed.link_box.setFocus()

    def mouseDoubleClickEvent(self, e):
        if self.editor.active_tool() in ("select", "edit", "text"):
            super().mouseDoubleClickEvent(e)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            steps = e.angleDelta().y() / 120
            if steps:
                self.editor.zoom_by(1.1 ** steps, e.position())
            return
        super().wheelEvent(e)

    def keyPressEvent(self, e):
        focus = self.scene().focusItem()
        if isinstance(focus, (QGraphicsTextItem,)) and focus.textInteractionFlags() & Qt.TextEditorInteraction:
            super().keyPressEvent(e)
            return
        ed = self.editor
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            ed.delete_selected()
            return
        if e.matches(QKeySequence.SelectAll):
            ed.select_all()
            return
        if e.matches(QKeySequence.Copy) and ed.copy_selected():
            return
        if e.keyCombination().toCombined() == QKeySequence("Ctrl+D")[0].toCombined():
            ed.duplicate_selected()
            return
        for keys, fn in ((QKeySequence.Undo, ed.undo), (QKeySequence.Redo, ed.redo), (QKeySequence("Ctrl+Y"), ed.redo)):
            if e.matches(keys) if isinstance(keys, QKeySequence.StandardKey) else \
                    e.keyCombination().toCombined() == keys[0].toCombined():
                fn()
                return
        steps = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
        # (the PDF's own pieces are moved by dragging them: the arrows would move only their box)
        movable = [i for i in ed.selected_items() if not isinstance(i, OldPiece)]
        if e.key() in steps and movable:
            dx, dy = steps[e.key()]
            far = 10 if e.modifiers() & Qt.ShiftModifier else 1
            for item in movable:
                page = item.parentItem()
                d = page.mapFromScene(QPointF(dx * far, dy * far)) - page.mapFromScene(QPointF(0, 0)) \
                    if page is not None else QPointF(dx * far, dy * far)
                item.moveBy(d.x(), d.y())
            ed.commit()
            return
        if e.key() == Qt.Key_Escape:
            e.ignore()  # (the window's Esc: the editor's go_back)
            return
        super().keyPressEvent(e)

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        if work.alive(self.editor):
            self.editor.view_moved()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.editor.view_resized()


# ----------------------------------------------------------------------------- picking a font

class FontPanel(QFrame):
    """The list of fonts, opened under the font button inside the window: a box to search, then the font the
    PDF used, the ones used lately, the Arabic ones and all of them, each name written in its own font."""

    def __init__(self, picker: "FontPicker", host: QWidget):
        super().__init__(host)
        self.picker = picker
        self.setObjectName("pdfFontPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Find a font"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill)
        self.search.installEventFilter(self)
        v.addWidget(self.search)
        self.list = QListWidget()
        self.list.setObjectName("pdfFontList")
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # (a long name is cut, not scrolled)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.setUniformItemSizes(False)
        self.list.itemClicked.connect(self._picked)
        self.list.itemActivated.connect(self._picked)
        v.addWidget(self.list, 1)
        self.hide()

    def open_under(self, button: QWidget):
        self._fill("")
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        host = self.parentWidget()
        w = max(button.width() + 60, 280)
        h = min(420, max(220, host.height() - 40))
        at = button.mapTo(host, QPoint(0, button.height() + 4))
        x = at.x() + button.width() - w if self.layoutDirection() == Qt.RightToLeft else at.x()
        x = max(6, min(x, host.width() - w - 6))
        y = at.y()
        if y + h > host.height() - 6:  # (no room below: above the button)
            y = max(6, button.mapTo(host, QPoint(0, 0)).y() - h - 4)
        self.setGeometry(x, y, w, h)
        self.show()
        self.raise_()
        self.search.setFocus(Qt.PopupFocusReason)
        QApplication.instance().installEventFilter(self)
        cur = self.picker.family
        for n in range(self.list.count()):
            it = self.list.item(n)
            if it.data(Qt.UserRole) == cur:
                self.list.setCurrentItem(it)
                self.list.scrollToItem(it, QListWidget.PositionAtCenter)
                break

    def close_panel(self):
        QApplication.instance().removeEventFilter(self)
        self.hide()

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.MouseButtonPress and self.isVisible():
            w = obj if isinstance(obj, QWidget) else None
            if w is not None and not (w is self or self.isAncestorOf(w)) and w is not self.picker:
                self.close_panel()
        elif t == QEvent.KeyPress and obj is self.search:
            if e.key() == Qt.Key_Escape:
                self.close_panel()
                self.picker.setFocus()
                return True
            if e.key() in (Qt.Key_Down, Qt.Key_Up):
                step = 1 if e.key() == Qt.Key_Down else -1
                n = self.list.currentRow()
                while True:
                    n += step
                    if not 0 <= n < self.list.count():
                        break
                    it = self.list.item(n)
                    if it.flags() & Qt.ItemIsSelectable:
                        self.list.setCurrentItem(it)
                        break
                return True
            if e.key() in (Qt.Key_Return, Qt.Key_Enter):
                it = self.list.currentItem()
                if it is None or not it.flags() & Qt.ItemIsSelectable:
                    it = next((self.list.item(n) for n in range(self.list.count())
                               if self.list.item(n).flags() & Qt.ItemIsSelectable), None)
                if it is not None:
                    self._picked(it)
                return True
        return False

    def _fill(self, query: str):
        from PySide6.QtGui import QFont, QFontDatabase
        query = query.strip().casefold()
        self.list.clear()
        fonts = self.picker.fonts()
        arabic = set(QFontDatabase.families(QFontDatabase.Arabic))
        sections = []
        if self.picker.matched:
            sections.append((tr("The PDF's font"), [self.picker.matched]))
        if self.picker.recent:
            sections.append((tr("Used lately"), [f for f in self.picker.recent if f in fonts]))
        sections.append((tr("Arabic"), [f for f in fonts if f in arabic]))
        sections.append((tr("All fonts"), fonts))
        for title, names in sections:
            names = [f for f in names if not query or query in f.casefold()]
            if not names:
                continue
            head = QListWidgetItem(title)
            head.setFlags(Qt.NoItemFlags)
            f = QFont(self.font())
            f.setBold(True)
            f.setPointSizeF(max(7.0, f.pointSizeF() - 1))
            head.setFont(f)
            head.setForeground(self.picker.muted)
            self.list.addItem(head)
            for fam in names:
                text = fam + ("   أبجد هوز" if fam in arabic and fam not in self.picker.symbol else "")
                it = QListWidgetItem(text)
                it.setData(Qt.UserRole, fam)
                it.setToolTip(fam)
                face = QFont(fam)
                face.setPointSizeF(11)
                if fam not in self.picker.symbol:  # (a font of signs only: its name in the list's font)
                    it.setFont(face)
                self.list.addItem(it)

    def _picked(self, it: QListWidgetItem):
        fam = it.data(Qt.UserRole)
        if not fam:
            return
        self.close_panel()
        self.picker.choose(fam)


class FontPicker(QPushButton):
    """The font button: shows the font's name in that font, and opens the list of fonts under it."""
    chosen = Signal(str)

    def __init__(self, editor: "PdfEditor"):
        super().__init__()
        self.editor = editor
        self.setObjectName("pdfFont")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tr("Font"))
        self.setAccessibleName(tr("Font"))
        self.family = ""
        self.matched = ""       # the font a line of the PDF is shown in (first in the list)
        self.recent: list[str] = []
        self.symbol: set = set()
        self.muted = QColor(editor.c.theme.muted)
        self.panel: FontPanel | None = None
        self.clicked.connect(self.open_list)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = 14
        x = 8 if self.layoutDirection() == Qt.RightToLeft else self.width() - side - 8
        icons.paint(p, QRectF(x, (self.height() - side) / 2, side, side), "chevron_down", self.muted)
        p.end()

    def fonts(self) -> list[str]:
        return self.editor.fonts

    def set_family(self, fam: str):
        from PySide6.QtGui import QFont
        self.family = fam
        f = QFont(fam)
        f.setPointSizeF(max(8.0, self.font().pointSizeF()))
        if fam in self.symbol:
            f = QFont(self.font())
        self.setFont(f)
        self.setText(fam)

    def open_list(self):
        host = sheet.host_for(self) or self.window()
        if self.panel is None or self.panel.parentWidget() is not host:
            self.panel = FontPanel(self, host)
        if self.panel.isVisible():
            self.panel.close_panel()
            return
        self.panel.open_under(self)

    def choose(self, fam: str):
        self.recent = ([fam] + [f for f in self.recent if f != fam])[:6]
        self.set_family(fam)
        self.chosen.emit(fam)
        typing = self.editor.typing()
        if typing is not None:
            self.editor.view.setFocus()  # (back to the words being typed)
            typing.setFocus(Qt.OtherFocusReason)


# ----------------------------------------------------------------------------- the editor

def _swatch(color: str, size: int = 18) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(0, 0, 0, 70), 1))
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(1.5, 1.5, size - 3, size - 3))
    p.end()
    return pm


def _no_fill(size: int = 18) -> QPixmap:
    """The swatch for "no fill": an empty circle crossed out."""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(0, 0, 0, 70), 1))
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(QRectF(1.5, 1.5, size - 3, size - 3))
    p.setPen(QPen(QColor("#d32f2f"), 1.6))
    p.drawLine(QPointF(4, size - 4), QPointF(size - 4, 4))
    p.end()
    return pm


def _words_rect(d: dict, at: int, n: int) -> QRectF | None:
    """Where letters at..at+n of a line of the PDF's own text are, from where its words are (inside a word,
    each letter is taken as the same width)."""
    import unicodedata
    text = d.get("text", "")
    x, y, w, h = d["rect"]
    left = right = None
    for start, end, x0, x1 in d.get("words") or ():
        a, b = max(at, start), min(at + n, end)
        if a >= b:
            continue
        each = (x1 - x0) / max(1, end - start)
        word = text[start:end]
        rtl = sum(unicodedata.bidirectional(ch) in ("R", "AL") for ch in word) > len(word) / 2
        if rtl:
            l, r = x1 - (b - start) * each, x1 - (a - start) * each
        else:
            l, r = x0 + (a - start) * each, x0 + (b - start) * each
        left = l if left is None else min(left, l)
        right = r if right is None else max(right, r)
    if left is None:
        return None
    return QRectF(left, y, max(1.0, right - left), h)


def _opacity(d: dict) -> float:
    if d.get("kind") in ("highlight", "cover"):
        return 1.0
    try:
        return min(1.0, max(0.05, float(d.get("opacity", 1.0) or 1.0)))
    except (TypeError, ValueError):
        return 1.0


def fill_strokes(kind: str, x: float, y: float, size: float) -> list[list[list[float]]]:
    """A tick or a cross, size points wide, its middle at (x, y), as pen strokes."""
    h = size / 2
    if kind == "check":
        pts = [(-0.5, 0.02), (-0.15, 0.4), (0.5, -0.45)]
        return [[[round(x + a * size, 2), round(y + b * size, 2)] for a, b in pts]]
    return [[[round(x - h, 2), round(y - h, 2)], [round(x + h, 2), round(y + h, 2)]],
            [[round(x + h, 2), round(y - h, 2)], [round(x - h, 2), round(y + h, 2)]]]


class PdfEditor(QWidget):
    """The editor, shown on Rakiza Core PDF's page instead of the cards."""

    closed = Signal()

    def __init__(self, pdf_page: "PdfPage"):
        super().__init__()
        self.pdf = pdf_page
        self.c = pdf_page.c
        self.src: Path | None = None
        self.password = ""
        self.renderer: Renderer | None = None
        self.pages: list[PageItem] = []
        self.offsets: list[float] = []
        self.fields: dict[str, str] = {}
        self.field_list: list[dict] = []
        self.field_items: list[FieldItem] = []
        self.removed: list[dict] = []       # the PDF's own pieces deleted: {"page", "objs"}
        self.restyled: list[dict] = []      # the PDF's own shapes and annotations changed: {"page", "objs", …}
        self.history: list[dict] = []
        self.future: list[dict] = []
        self.current: dict = {"items": [], "removed": [], "fields": {}}
        self.saved: dict | None = None
        self._saving: dict | None = None  # (what a save that's still running will have saved)
        self.order: list[int] = []          # the pages in the order shown (a deleted one isn't in it)
        self.turns: dict[int, int] = {}     # pages turned: degrees clockwise
        self.position: dict[int, int] = {}  # where each page is in the order
        self.thumb_imgs: dict[int, QImage] = {}
        self.thumb_items: dict[int, QListWidgetItem] = {}
        self.matches: list = []             # what Find found: (page, piece or text box, start)
        self.match_at = -1
        self.found_marks: list[FoundMarks] = []
        self.zoom = 1.0
        self.fit = True                     # the zoom follows the window's width
        self.tool = "select"
        self.mark_kind = "highlight"
        self.shape_kind = "rect"
        self.fill_kind = "check"
        self.stamp_kind = "approved"
        self.form_kind = "formtext"
        self.dragging_items = False
        self.thumbs: list[PageItem] = []
        self.kept: set = set()              # every added thing on the pages
        self.field_menu = None              # (a form's list, while it's open)
        self.temp = None
        self.move_pics: dict[tuple, QPixmap] = {}  # (page, pieces) -> the picture of a moved piece (for undo)
        self.style = {"text": {"font": "Arial", "size": 12.0, "color": "#000000", "bold": False, "italic": False,
                               "underline": False},
                      "highlight": {"color": PENS[0]}, "underline": {"color": "#d32f2f"},
                      "strike": {"color": "#d32f2f"}, "draw": {"color": "#1f3a93", "width": 2.5},
                      "shape": {"color": "#d32f2f", "width": 2.5}, "cover": {"color": "#ffffff"},
                      "image": {"opacity": 1.0}}
        self.doc_pages = 0                  # the PDF's own pages (blank pages added come after them)
        self.hot_para: tuple | None = None  # (page, paragraph) under the mouse, with Edit text
        self.copied_at = 0                  # (pasting the same things again puts them a little further on)
        self.scene = QGraphicsScene(self)
        self.scene.selectionChanged.connect(self.selection_changed)
        self.visible_timer = QTimer(self)
        self.visible_timer.setSingleShot(True)
        self.visible_timer.setInterval(30)
        self.visible_timer.timeout.connect(self._ask_pages)
        self._build()

    # ---- building

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        muted = self.c.theme.muted
        text_color = self.c.theme.text
        faded = tokens.alpha(QColor(muted), 90)
        # the top: the PDF's name, undo and redo, find, close and save
        top = QHBoxLayout()
        top.setSpacing(6)
        doc_icon = QLabel()
        doc_icon.setPixmap(icons.pixmap("document_pdf", 20, muted, self.devicePixelRatioF()))
        top.addWidget(doc_icon)
        self.name = QLabel()
        self.name.setObjectName("cardTitle")
        self.name.setMinimumWidth(40)
        top.addWidget(self.name)
        self.count_label = QLabel()
        self.count_label.setObjectName("muted")
        top.addWidget(self.count_label, 1)
        self.undo_btn = IconButton(tr("Undo (Ctrl + Z)"))
        self.undo_btn.setIcon(icons.icon("arrow_undo", muted, faded))
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn = IconButton(tr("Redo (Ctrl + Y)"))
        self.redo_btn.setIcon(icons.icon("arrow_redo", muted, faded))
        self.redo_btn.clicked.connect(self.redo)
        top.addWidget(self.undo_btn)
        top.addWidget(self.redo_btn)
        self.find_btn = IconButton(tr("Find and replace (Ctrl + F)"))
        self.find_btn.setIcon(icons.icon("search", muted))
        self.find_btn.clicked.connect(lambda: self.show_find())
        top.addWidget(self.find_btn)
        top.addSpacing(6)
        self.back_btn = QPushButton(tr("Close"))
        self.back_btn.setToolTip(tr("Close the editor"))
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.clicked.connect(lambda: self.close_editor())
        top.addWidget(self.back_btn)
        self.save_btn = QPushButton(tr("Save as a new PDF"))
        self.save_btn.setObjectName("primary")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setToolTip(tr("Save as a new PDF (Ctrl + S)"))
        self.save_btn.clicked.connect(self.save)
        top.addWidget(self.save_btn)
        v.addLayout(top)

        # the tools, in groups, each with its name under its picture
        self.toolbar = QFrame()
        self.toolbar.setObjectName("pdfToolbar")
        tl = QHBoxLayout(self.toolbar)
        tl.setContentsMargins(4, 4, 4, 4)
        tl.setSpacing(2)
        self.tool_group = QButtonGroup(self)
        self.tools_short = False
        self.tool_btns: dict[str, QToolButton] = {}
        self.tool_seps: list[QFrame] = []
        words = {k: w for k, w, _ in TOOLS}
        icon_of = {k: i for k, _, i in TOOLS}
        for n, group in enumerate(TOOL_GROUPS):
            if n:
                sep = QFrame()
                sep.setObjectName("pdfToolSep")
                sep.setFixedWidth(1)
                tl.addWidget(sep)
                self.tool_seps.append(sep)
            for key in group:
                b = QToolButton()
                b.setObjectName("pdfTool")
                b.setText(tr(words[key]))
                b.setToolTip(tr(words[key]))
                b.setAccessibleName(tr(words[key]))
                b.setCheckable(True)
                b.setAutoRaise(True)
                b.setCursor(Qt.PointingHandCursor)
                b.setIcon(icons.icon(icon_of[key], text_color))
                b.setIconSize(QSize(20, 20))
                b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                b.clicked.connect(lambda _=False, k=key: self.set_tool(k))
                self.tool_group.addButton(b)
                self.tool_btns[key] = b
                tl.addWidget(b)
        tl.addStretch()
        self.tool_btns["select"].setChecked(True)
        self.tools_wide = self.toolbar.sizeHint().width()
        v.addWidget(self.toolbar)

        self._build_find(v)
        body = QHBoxLayout()
        body.setSpacing(8)
        # the pages, small, at the side
        self.side = QWidget()
        self.side.setObjectName("pdfPagesSide")
        sl = QVBoxLayout(self.side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(4)
        self.thumb_list = PagesList(self)
        self.thumb_list.setObjectName("pdfThumbs")
        self.thumb_list.setViewMode(QListView.IconMode)
        self.thumb_list.setFlow(QListView.TopToBottom)
        self.thumb_list.setWrapping(False)
        self.thumb_list.setResizeMode(QListView.Adjust)
        self.thumb_list.setIconSize(THUMB)
        self.thumb_list.setGridSize(QSize(THUMB.width() + 28, THUMB.height() + 34))
        self.thumb_list.setSpacing(2)
        self.thumb_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.thumb_list.setDragDropMode(QListWidget.InternalMove)
        self.thumb_list.setDefaultDropAction(Qt.MoveAction)
        self.thumb_list.setMovement(QListView.Snap)
        self.thumb_list.setToolTip(tr("Drag a page to move it. Right-click for more."))
        self.thumb_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.thumb_list.customContextMenuRequested.connect(self._page_menu)
        self.thumb_list.itemClicked.connect(lambda it: self.go_to_page(it.data(Qt.UserRole)))
        self.thumb_list.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self._pages_dragged))
        self.thumb_list.verticalScrollBar().valueChanged.connect(lambda _v: self._want_thumbs())
        self.thumb_list.setFixedWidth(THUMB.width() + 48)
        sl.addWidget(self.thumb_list, 1)
        acts = QHBoxLayout()
        acts.setSpacing(0)
        for icon_name, words_, fn in (("arrow_rotate_counterclockwise", tr("Turn left"), lambda: self.turn_pages(-90)),
                                      ("arrow_rotate_clockwise", tr("Turn right"), lambda: self.turn_pages(90)),
                                      ("page_add", tr("Add a blank page after it"), lambda: self.add_blank_page()),
                                      ("delete", tr("Delete the page"), self.delete_pages)):
            b = IconButton(words_, 30)
            b.setIcon(icons.icon(icon_name, muted, faded))
            b.clicked.connect(fn)
            acts.addWidget(b)
        acts.addStretch()
        sl.addLayout(acts)
        body.addWidget(self.side)
        self.view = EditorView(self)
        body.addWidget(self.view, 1)
        body.addWidget(self._build_inspector())
        v.addLayout(body, 1)
        # the foot: where you are, what just happened, the pages list, the zoom
        foot = QHBoxLayout()
        foot.setSpacing(4)
        self.where = QLabel()
        self.where.setObjectName("muted")
        self.where.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)  # (never cut short by the note)
        foot.addWidget(self.where)
        self.note = QLabel()
        self.note.setObjectName("muted")
        self.note_timer = QTimer(self)
        self.note_timer.setSingleShot(True)
        self.note_timer.setInterval(6000)
        self.note_timer.timeout.connect(self.note.clear)
        foot.addSpacing(10)
        foot.addWidget(self.note, 1)
        self.pages_btn = IconButton(tr("The pages: turn, delete and move them"), 30)
        self.pages_btn.setIcon(icons.icon("document_multiple", muted))
        self.pages_btn.setCheckable(True)
        self.pages_btn.setChecked(True)
        self.pages_btn.clicked.connect(lambda _on: self._side_shown())
        self.options_btn = IconButton(tr("The options at the side"), 30)
        self.options_btn.setIcon(icons.icon("settings", muted))
        self.options_btn.setCheckable(True)
        self.options_btn.setChecked(True)
        self.options_btn.clicked.connect(lambda _on: self._side_shown())
        foot.addWidget(self.pages_btn)
        foot.addWidget(self.options_btn)
        foot.addSpacing(6)
        out_btn = IconButton(tr("Zoom out (Ctrl + -)"), 30)
        out_btn.setIcon(icons.icon("zoom_out", muted))
        out_btn.clicked.connect(lambda: self.zoom_step(-1))
        self.zoom_btn = QPushButton("100%")
        self.zoom_btn.setObjectName("flat")
        self.zoom_btn.setCursor(Qt.PointingHandCursor)
        self.zoom_btn.setToolTip(tr("Fit the width (Ctrl + 0)"))
        self.zoom_btn.setMinimumWidth(52)
        self.zoom_btn.clicked.connect(self.fit_width)
        in_btn = IconButton(tr("Zoom in (Ctrl + +)"), 30)
        in_btn.setIcon(icons.icon("zoom_in", muted))
        in_btn.clicked.connect(lambda: self.zoom_step(1))
        for b in (out_btn, self.zoom_btn, in_btn):
            foot.addWidget(b)
        v.addLayout(foot)
        self._filling = False
        taken = set()  # (one shortcut per key: two on the same key (Windows' Redo is Ctrl + Y too) do nothing)
        for keys, fn in ((QKeySequence.Undo, self.undo), (QKeySequence.Redo, self.redo),
                         ("Ctrl+Y", self.redo), (QKeySequence.Save, self.save),
                         ("Ctrl+V", self.paste), (QKeySequence.ZoomIn, lambda: self.zoom_step(1)),
                         ("Ctrl+=", lambda: self.zoom_step(1)),
                         (QKeySequence.ZoomOut, lambda: self.zoom_step(-1)), ("Ctrl+0", self.fit_width),
                         (QKeySequence.Find, self.show_find), ("Ctrl+H", lambda: self.show_find(replace=True)),
                         (QKeySequence.FindNext, lambda: self.next_match(1)),
                         (QKeySequence.FindPrevious, lambda: self.next_match(-1))):
            for seq in QKeySequence.keyBindings(keys) if not isinstance(keys, str) else [QKeySequence(keys)]:
                if seq.isEmpty() or seq.toString() in taken:
                    continue
                taken.add(seq.toString())
                sc = QShortcut(seq, self)
                sc.setContext(Qt.WidgetWithChildrenShortcut)
                sc.activated.connect(fn)
        self._fill_fonts()
        self._refresh()

    def _section(self, title: str | None) -> tuple[QWidget, QVBoxLayout]:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        if title:
            head = QLabel(title)
            head.setObjectName("pdfGroup")
            lay.addWidget(head)
        self.insp_layout.addWidget(box)
        return box, lay

    def _choice(self, words: str, icon_name: str, tip: str = "") -> QPushButton:
        b = QPushButton(" " + words if words else "")
        b.setObjectName("pdfChoice")
        b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setIcon(icons.icon(icon_name, self.c.theme.text))
        b.setIconSize(QSize(16, 16))
        b.setToolTip(tip or words)
        b.setAccessibleName(tip or words)
        return b

    def _build_inspector(self) -> QWidget:
        """The options at the side: what the tool or the selected thing looks like, in sections with names."""
        text_color = self.c.theme.text
        self.inspector = QScrollArea()
        self.inspector.setObjectName("pdfInspector")
        self.inspector.setWidgetResizable(True)
        self.inspector.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inspector.setFixedWidth(INSPECTOR_WIDTH)
        inner = QWidget()
        inner.setObjectName("pdfInspectorInside")
        self.insp_layout = QVBoxLayout(inner)
        self.insp_layout.setContentsMargins(14, 14, 14, 14)
        self.insp_layout.setSpacing(16)
        self.inspector.setWidget(inner)

        head, hl = self._section(None)
        hl.setSpacing(4)
        self.insp_title = QLabel()
        self.insp_title.setObjectName("cardTitle")
        self.insp_title.setWordWrap(True)
        self.hint = QLabel()
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        hl.addWidget(self.insp_title)
        hl.addWidget(self.hint)
        self.head_box = head

        # the kind of highlight or shape
        self.kind_box, kl = self._section(tr("Kind"))
        grid = QGridLayout()
        grid.setSpacing(4)
        self.kind_group = QButtonGroup(self)
        self.kind_btns: dict[str, QPushButton] = {}
        for group in (MARKS, SHAPES, FILL_KINDS, STAMP_KINDS, FORM_KINDS):
            for m, (key, words, icon_name) in enumerate(group):
                b = self._choice(tr(words), icon_name if icons.has(icon_name) else "form")
                b.clicked.connect(lambda _=False, k=key: self.set_kind(k))
                self.kind_group.addButton(b)
                self.kind_btns[key] = b
                if group is STAMP_KINDS:  # (the words of a stamp are long: one a row)
                    grid.addWidget(b, 10 + m, 0, 1, 2)
                elif group is FORM_KINDS:
                    grid.addWidget(b, 30 + m, 0, 1, 2)
                else:
                    grid.addWidget(b, m // 2, m % 2)
        kl.addLayout(grid)
        self.kind_strip = self.kind_box

        # Cover: find private things on every page and cover them
        self.find_section, fsl = self._section(tr("Find and cover"))
        find_note = QLabel(tr("Phone numbers, email addresses, ID and card numbers, or your own words, on every "
                              "page."))
        find_note.setObjectName("muted")
        find_note.setWordWrap(True)
        fsl.addWidget(find_note)
        self.find_cover_btn = QPushButton(tr("Find and cover…"))
        self.find_cover_btn.setCursor(Qt.PointingHandCursor)
        self.find_cover_btn.clicked.connect(self.find_and_cover)
        fsl.addWidget(self.find_cover_btn)

        # how to change the PDF's own text
        self.para_box, pl = self._section(tr("Change"))
        self.para_check = QCheckBox(tr("Whole paragraphs"))
        self.para_check.setChecked(True)
        self.para_check.setToolTip(tr("The lines of a paragraph are changed together, and the words flow from "
                                      "line to line as you type. Off: one line at a time."))
        self.para_check.toggled.connect(self._paras_toggled)
        pl.addWidget(self.para_check)
        para_note = QLabel(tr("Off: one line at a time."))
        para_note.setObjectName("muted")
        para_note.setWordWrap(True)
        pl.addWidget(para_note)

        # the font
        self.font_section, fl = self._section(tr("Font"))
        self.font_box = FontPicker(self)
        self.font_box.chosen.connect(lambda fam: self.set_style(font=fam))
        fl.addWidget(self.font_box)
        self.font_note = QLabel()
        self.font_note.setObjectName("muted")
        self.font_note.setWordWrap(True)
        fl.addWidget(self.font_note)
        row = QHBoxLayout()
        row.setSpacing(2)
        self.size_box = QSpinBox()
        self.size_box.setRange(4, 144)
        self.size_box.setToolTip(tr("Text size"))
        self.size_box.setAccessibleName(tr("Text size"))
        self.size_box.setSuffix(" pt")
        self.size_box.valueChanged.connect(lambda n: None if self._filling else self.set_style(size=float(n)))
        row.addWidget(self.size_box, 1)
        self.bold_btn = IconButton(tr("Bold"), 30)
        self.bold_btn.setIcon(icons.icon("text_bold", text_color))
        self.bold_btn.setCheckable(True)
        self.bold_btn.clicked.connect(lambda on: self.set_style(bold=bool(on)))
        self.italic_btn = IconButton(tr("Italic"), 30)
        self.italic_btn.setIcon(icons.icon("text_italic", text_color))
        self.italic_btn.setCheckable(True)
        self.italic_btn.clicked.connect(lambda on: self.set_style(italic=bool(on)))
        self.underline_btn = IconButton(tr("Underline"), 30)
        self.underline_btn.setIcon(icons.icon("text_underline", text_color))
        self.underline_btn.setCheckable(True)
        self.underline_btn.clicked.connect(lambda on: self.set_style(underline=bool(on)))
        for b in (self.bold_btn, self.italic_btn, self.underline_btn):
            row.addWidget(b)
        fl.addLayout(row)

        # the lines of the text: lined up how, how far apart
        self.align_section, al = self._section(tr("Paragraph"))
        arow = QHBoxLayout()
        arow.setSpacing(2)
        self.align_group = QButtonGroup(self)
        self.align_btns: dict[str, IconButton] = {}
        for key, words, icon_name in ALIGN_KEYS:
            b = IconButton(tr(words), 30)
            b.setIcon(icons.icon(icon_name, text_color))
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.set_style(align=k))
            self.align_group.addButton(b)
            self.align_btns[key] = b
            arow.addWidget(b)
        arow.addStretch()
        al.addLayout(arow)
        srow = QHBoxLayout()
        srow.setSpacing(6)
        sp_icon = QLabel()
        sp_icon.setPixmap(icons.pixmap("line_spacing", 18, self.c.theme.muted, self.devicePixelRatioF()))
        sp_icon.setToolTip(tr("Line spacing"))
        srow.addWidget(sp_icon)
        self.spacing_box = Dropdown()
        self.spacing_box.setToolTip(tr("Line spacing"))
        self.spacing_box.setAccessibleName(tr("Line spacing"))
        self.spacing_box.activated.connect(lambda _i: self.set_style(spacing=self.spacing_box.currentData()))
        srow.addWidget(self.spacing_box, 1)
        al.addLayout(srow)

        # the colour
        self.color_section, cl0 = self._section(tr("Colour"))
        self.colors = QWidget()
        cl = FlowLayout(self.colors, 4)
        self.color_btns: list[IconButton] = []
        for _ in range(7):
            b = IconButton("", 28)
            b.setIconSize(QSize(18, 18))
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, b=b: self.set_style(color=b.property("color")))
            self.color_btns.append(b)
            cl.addWidget(b)
        more = IconButton(tr("More colours…"), 28)
        more.setIcon(icons.icon("color", text_color))
        more.clicked.connect(self._pick_color)
        cl.addWidget(more)
        cl0.addWidget(self.colors)

        # the inside of a rectangle or circle
        self.fill_section, fil = self._section(tr("Fill"))
        self.fills = QWidget()
        fl2 = FlowLayout(self.fills, 4)
        self.fill_btns: list[IconButton] = []
        for _ in range(len(FILLS)):
            b = IconButton("", 28)
            b.setIconSize(QSize(18, 18))
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, b=b: self.set_style(fill=b.property("color") or None))
            self.fill_btns.append(b)
            fl2.addWidget(b)
        fil.addWidget(self.fills)

        # how thick the lines are
        self.width_section, wl = self._section(tr("Line width"))
        self.width_box = Dropdown()
        self.width_box.setToolTip(tr("Line width"))
        self.width_box.setAccessibleName(tr("Line width"))
        for words, w in WIDTHS.items():
            self.width_box.addItem(tr(words), w)
        self.width_box.activated.connect(lambda _i: self.set_style(width=float(self.width_box.currentData())))
        wl.addWidget(self.width_box)

        # see-through
        self.opacity_section, ol = self._section(tr("Opacity"))
        orow = QHBoxLayout()
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(10, 100)
        self.opacity_slider.setSingleStep(5)
        self.opacity_slider.setPageStep(10)
        self.opacity_slider.setAccessibleName(tr("Opacity"))
        self.opacity_label = QLabel("100%")
        self.opacity_label.setObjectName("muted")
        self.opacity_label.setMinimumWidth(40)
        self.opacity_slider.valueChanged.connect(self._opacity_moved)
        self.opacity_slider.sliderReleased.connect(lambda: self.commit())
        orow.addWidget(self.opacity_slider, 1)
        orow.addWidget(self.opacity_label)
        ol.addLayout(orow)

        # where a link goes
        self.link_section, ll = self._section(tr("Opens"))
        self.link_box = QLineEdit()
        self.link_box.setPlaceholderText(tr("A web address or a page number"))
        self.link_box.setAccessibleName(tr("A web address or a page number"))
        self.link_box.editingFinished.connect(self._link_typed)
        ll.addWidget(self.link_box)
        self.link_note = QLabel()
        self.link_note.setObjectName("muted")
        self.link_note.setWordWrap(True)
        ll.addWidget(self.link_note)

        # a picture: put another in its place, turn it, keep a copy
        self.picture_section, pil = self._section(tr("Picture"))
        self.replace_pic_btn = QPushButton(tr("Replace…"))
        self.replace_pic_btn.setIcon(icons.icon("image_add", text_color))
        self.replace_pic_btn.clicked.connect(self.replace_picture)
        self.turn_pic_btn = QPushButton(tr("Turn"))
        self.turn_pic_btn.setIcon(icons.icon("arrow_rotate_clockwise", text_color))
        self.turn_pic_btn.clicked.connect(self.turn_picture)
        self.save_pic_btn = QPushButton(tr("Save the picture…"))
        self.save_pic_btn.setIcon(icons.icon("arrow_download", text_color))
        self.save_pic_btn.clicked.connect(self.save_picture)
        for b in (self.replace_pic_btn, self.turn_pic_btn, self.save_pic_btn):
            b.setCursor(Qt.PointingHandCursor)
            b.setObjectName("pdfAction")
            pil.addWidget(b)

        # the selected things: copy them, delete them
        self.actions_section, acl = self._section(None)
        self.duplicate_btn = QPushButton(tr("Duplicate"))
        self.duplicate_btn.setIcon(icons.icon("copy", text_color))
        self.duplicate_btn.setToolTip(tr("Duplicate (Ctrl + D)"))
        self.duplicate_btn.clicked.connect(self.duplicate_selected)
        self.delete_btn = QPushButton(tr("Delete"))
        self.delete_btn.setIcon(icons.icon("delete", text_color))
        self.delete_btn.setToolTip(tr("Delete (Del)"))
        self.delete_btn.clicked.connect(self.delete_selected)
        for b in (self.duplicate_btn, self.delete_btn):
            b.setCursor(Qt.PointingHandCursor)
            b.setObjectName("pdfAction")
            acl.addWidget(b)
        self.insp_layout.addStretch(1)
        self.props = self.inspector
        return self.inspector

    def _fill_fonts(self):
        from PySide6.QtGui import QFontDatabase
        self.fonts = pdffonts.offered()
        self.font_box.symbol = {f for f in self.fonts
                                if QFontDatabase.Symbol in QFontDatabase.writingSystems(f) and
                                QFontDatabase.Latin not in QFontDatabase.writingSystems(f)}
        if self.style["text"]["font"] not in self.fonts:
            self.style["text"]["font"] = self.fonts[0]
        self.font_box.set_family(self.style["text"]["font"])

    # ---- opening and closing

    def open(self, src: Path, password: str = ""):
        self.close_renderer()
        self.pages, self.offsets, self.thumbs = [], [], []
        self.order, self.turns, self.position = [], {}, {}
        self.thumb_imgs, self.thumb_items = {}, {}
        self.thumb_list.clear()
        self.matches, self.match_at, self.found_marks = [], -1, []
        self.find_bar.hide()
        self.scene.clear()
        self.kept.clear()
        self.fields, self.field_list, self.field_items, self.removed = {}, [], [], []
        self.restyled = []
        self.history, self.future = [], []
        self.current = {"items": [], "removed": [], "fields": {}}
        self.saved = self._saving = None
        self.move_pics = {}
        self.src, self.password = src, password
        self.doc_pages = 0
        self.count_label.setText("")
        self.name.setText(short_name(src.name, 60))
        self.name.setToolTip(str(src))
        self.where.setText(tr("Opening…"))
        self.fit = True
        self.set_tool("select")
        self.renderer = Renderer(src, password)
        self.renderer.sig.opened.connect(self._opened)
        self.renderer.sig.image.connect(self._image)
        self.renderer.sig.layout.connect(self._layout)
        self.renderer.sig.thumb.connect(self._thumb)
        self.renderer.sig.read.connect(self._read)
        self._tidy_temp()

    def _opened(self, result):
        if not work.alive(self):
            return
        if isinstance(result, Exception):
            self.c.tell(tr("Rakiza Core PDF"), tr("{name} couldn't be read. It may be damaged.",
                                                 name=short_name(self.src.name)))
            self.close_editor(ask=False)
            return
        sizes, fields = result
        self.doc_pages = len(sizes)
        self.count_label.setText("·  " + (tr("1 page") if len(sizes) == 1 else tr("{count} pages", count=len(sizes))))
        for i, (w, h) in enumerate(sizes):
            page = PageItem(i, w, h)
            self.scene.addItem(page)
            self.pages.append(page)
        self.order = list(range(len(self.pages)))
        self._place_pages()
        self._fill_thumbs()
        self.field_list = fields
        for f in fields:
            if 0 <= f["page"] < len(self.pages):
                self.field_items.append(FieldItem(self, self.pages[f["page"]], f))
        self.current = self.model()
        self.saved = self.current
        QTimer.singleShot(0, self.fit_width)
        self._refresh()

    def close_renderer(self):
        if self.renderer is not None:
            self.renderer.close()
            for s in (self.renderer.sig.opened, self.renderer.sig.image, self.renderer.sig.layout,
                      self.renderer.sig.thumb, self.renderer.sig.read):
                try:
                    s.disconnect()
                except (RuntimeError, TypeError):
                    pass
            self.renderer = None

    def unsaved(self) -> bool:
        self.end_text_edits()
        return self.saved is not None and self.model() != self.saved

    def close_editor(self, ask: bool = True) -> bool:
        if ask and self.unsaved():
            box = QMessageBox(QMessageBox.Question, tr("Close the editor?"),
                              tr("Your changes aren't saved yet. The PDF itself is fine."), parent=self)
            save = box.addButton(tr("Save as a new PDF"), QMessageBox.AcceptRole)
            close = box.addButton(tr("Close without saving"), QMessageBox.DestructiveRole)
            box.addButton(tr("Cancel"), QMessageBox.RejectRole)
            sheet.run(box)
            chosen = box.clickedButton()
            box.deleteLater()
            if chosen is save:
                # (closed once the new PDF is really there: if saving fails, the changes are still here)
                self.save(then_close=True)
                return False
            if chosen is not close:
                return False
        self.close_renderer()
        self.pages, self.offsets, self.thumbs, self.field_items = [], [], [], []
        self.order, self.position, self.matches, self.found_marks = [], {}, [], []
        self.thumb_imgs, self.thumb_items = {}, {}
        self.thumb_list.clear()
        self.scene.clear()
        self.kept.clear()
        self.move_pics = {}
        self.src, self.saved, self._saving = None, None, None  # (nothing left unsaved once it's closed)
        self.closed.emit()
        return True

    def go_back(self) -> bool:
        """Esc: closes Find, stops typing, then goes back to the Select tool, then closes the editor."""
        if self.find_bar.isVisible():
            self.hide_find()
            return True
        if self.scene.focusItem() is not None or self.typing() is not None:
            self.end_text_edits()
            self.scene.setFocusItem(None)
            return True
        if self.tool != "select":
            self.set_tool("select")
            return True
        if self.scene.selectedItems():
            self.scene.clearSelection()
            return True
        self.close_editor()
        return True

    def _tidy_temp(self):
        """Pictures pasted in earlier editors (kept until their PDF was saved) are cleared after a day."""
        import shutil
        try:
            for d in Path(tempfile.gettempdir()).glob("corefold-pdfedit-*"):
                if d.is_dir() and time.time() - d.stat().st_mtime > 86400:
                    shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass

    # ---- pages in sight

    def page_at(self, sp: QPointF) -> PageItem | None:
        import bisect
        k = bisect.bisect_right(self.offsets, sp.y()) - 1
        for n in (k, k + 1):
            if 0 <= n < len(self.order):
                page = self.pages[self.order[n]]
                if page.sceneBoundingRect().adjusted(3, 3, -3, -3).contains(sp):
                    return page
        return None

    def view_moved(self):
        self.visible_timer.start()
        self._where()

    def _side_shown(self):
        """The list of pages and the options: as their buttons say, unless the window is too narrow for them
        (the pages go first)."""
        pages = self.pages_btn.isChecked() and self.width() >= 720
        self.side.setVisible(pages)
        # (on a narrow window the options come only when there's something to set, and only when the page
        # itself keeps at least 320 px beside the list of pages)
        room = self.width() - (self.side.sizeHint().width() if pages else 0)
        wide = self.width() >= 860 or (getattr(self, "insp_needed", True) and room >= 560)
        self.inspector.setVisible(self.options_btn.isChecked() and wide)
        QTimer.singleShot(0, self._want_thumbs)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._side_shown()
        short = self.tools_wide > self.width() - 8  # (no room for the tools' names: just their pictures)
        if short != self.tools_short:
            self.tools_short = short
            self._tool_names()

    def _tool_names(self):
        style = Qt.ToolButtonIconOnly if self.tools_short else Qt.ToolButtonTextUnderIcon
        for b in self.tool_btns.values():
            b.setToolButtonStyle(style)

    def view_resized(self):
        if self.fit and self.pages:
            self.fit_width()
        self.visible_timer.start()

    def _in_sight(self) -> list[int]:
        import bisect
        if not self.order:
            return []
        r = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        first = max(0, bisect.bisect_right(self.offsets, r.top()) - 1)
        out = []
        for n in range(first, len(self.order)):
            if self.offsets[n] > r.bottom():
                break
            out.append(self.order[n])
        return out

    def _scale(self) -> float:
        dpr = self.devicePixelRatioF()
        return round(self.view.transform().m11() * dpr, 3)

    def hidden(self, index: int) -> tuple:
        """The PDF's own pieces on this page that were changed, moved or deleted (not drawn)."""
        out = set()
        for d in self.model_items():
            if d.get("page") != index:
                continue
            if d.get("replaces"):  # (a changed line or paragraph, a picture put in another's place)
                out.update(d.get("replaces") or ())
            if d.get("kind") == "move":
                out.update(d.get("objs") or ())
        for d in self.removed:
            if d["page"] == index:
                out.update(d["objs"])
        return tuple(sorted(out, key=str))

    def painted(self, index: int) -> tuple:
        """The PDF's own shapes on this page given other colours: ((piece, line colour, fill), …)."""
        return tuple(sorted(((d["objs"][0], d.get("color", ""), d.get("fill", "")) for d in self.restyled
                             if d["page"] == index and (d.get("color") or d.get("fill"))), key=str))

    def look(self, index: int) -> tuple:
        """How a page is drawn: without the pieces changed, moved or deleted, with shapes' new colours."""
        return self.hidden(index), self.painted(index)

    def _near(self, sight: list[int]) -> list[int]:
        at = [self.position[i] for i in sight if i in self.position]
        if not at:
            return []
        return self.order[max(0, min(at) - KEEP_NEAR):max(at) + KEEP_NEAR + 1]

    def _ask_pages(self):
        if self.renderer is None or not self.pages:
            return
        sight = self._in_sight()
        if not sight:
            return
        near = self._near(sight)
        scale = self._scale()
        middle = sight[len(sight) // 2]
        wishes = []
        mid = self.position.get(middle, 0)
        for i in sorted(near, key=lambda k: (k not in sight, abs(self.position.get(k, 0) - mid))):
            page = self.pages[i]
            s = min(scale, math.sqrt(MAX_PIXELS / max(1.0, page.w * page.h)))
            key = (round(s, 3), self.look(i))
            if page.pm_key != key and not page.blank:
                wishes.append((i, key[0], key[1]))
        self.renderer.want(wishes)
        self.renderer.want_layout([i for i in sight if not self.pages[i].asked_layout and not self.pages[i].blank])
        for i in sight:
            self.pages[i].asked_layout = True
        for page in self.pages:
            if page.pm is not None and page.index not in near:
                page.keep_small()
                self.thumbs.append(page)
        if len(self.thumbs) > THUMBS:
            for page in self.thumbs[:-THUMBS]:
                if page.pm is None and page.index not in near:
                    page.thumb = None
            self.thumbs = self.thumbs[-THUMBS:]

    def _image(self, i: int, scale: float, hide: tuple, img: QImage):
        if not work.alive(self) or i >= len(self.pages):
            return
        page = self.pages[i]
        page.pm, page.pm_key = QPixmap.fromImage(img), (scale, hide)
        page.thumb = None
        sight = self._in_sight()
        if sight and i not in self._near(sight):  # (drawn for where the view was: only a small copy is kept)
            page.keep_small()
            self.thumbs.append(page)
        page.update()

    def _layout(self, i: int, layout: dict):
        if not work.alive(self) or i >= len(self.pages):
            return
        page = self.pages[i]
        page.layout = layout
        for k, d in enumerate(layout.get("lines", [])):
            piece = OldPiece(self, page, "line", d)
            piece.k = k
            page.pieces.append(piece)
        for d in layout.get("images", []):
            page.pieces.append(OldPiece(self, page, "image", d))
        for d in layout.get("shapes", []):
            page.pieces.append(OldPiece(self, page, "shape", d))
        for d in layout.get("annots", []):
            page.pieces.append(OldPiece(self, page, "annot", d))
        for piece in page.pieces:
            piece.setAcceptedMouseButtons(Qt.LeftButton if self.tool in piece.tools() else Qt.NoButton)
        self._pieces_shown(page)
        self._read_if_needed([i])
        if self.tool == "edit":
            self._refresh()
        if self.find_bar.isVisible() and self.find_box.text():
            self.find_timer.start()

    def _read_if_needed(self, pages: list[int]):
        """With Edit text, the words of scans (and of fonts that don't say which letters they have) are read
        from the pages' pictures, so they can be changed too (Windows 10 and 11)."""
        if self.renderer is None or self.tool != "edit" or not self.can_read():
            return
        want = []
        for i in pages:
            page = self.pages[i] if 0 <= i < len(self.pages) else None
            if page is None or page.layout is None or page.blank or getattr(page, "read_state", None):
                continue
            if pdfedit.needs_reading(page.layout):
                page.read_state = "reading"
                want.append(i)
        if want:
            self.renderer.want_read(want)
            self._refresh()

    def can_read(self) -> bool:
        from .extras import ocr_available
        return ocr_available()

    def _read(self, i: int, read):
        """The words read from a page's picture: lines that can be changed (see pdfedit.with_read_words)."""
        if not work.alive(self) or i >= len(self.pages):
            return
        page = self.pages[i]
        page.read_state = "read" if read is not None else "failed"
        if page.layout is None or not read:
            self._refresh()
            return
        made = pdfedit.with_read_words(page.layout, read)
        start = len(page.layout.get("lines", []))
        for n, d in enumerate(made):
            d["objs"] = [f"s{start + n}"]
            bg, fg = self._sample_colors(page, d["rect"])
            d["color"] = fg
            d["paper"] = bg
            page.layout.setdefault("lines", []).append(d)
            piece = OldPiece(self, page, "line", d)
            piece.k = start + n
            piece.setAcceptedMouseButtons(Qt.LeftButton if self.tool in piece.tools() else Qt.NoButton)
            page.pieces.append(piece)
        self._pieces_shown(page)
        self._refresh()
        if self.find_bar.isVisible() and self.find_box.text():
            self.find_timer.start()

    def _sample_colors(self, page: PageItem, rect) -> tuple[str, str]:
        """(the paper's colour around a place on the page, the colour of the words in it), from its picture."""
        pm = page.pm or page.thumb
        if pm is None:
            return "#ffffff", "#000000"
        img = pm.toImage()
        k = img.width() / max(1.0, page.w)
        x, y, w, h = rect
        x0, y0 = max(0, int((x - 2) * k)), max(0, int((y - 2) * k))
        x1, y1 = min(img.width() - 1, int((x + w + 2) * k)), min(img.height() - 1, int((y + h + 2) * k))
        if x1 <= x0 or y1 <= y0:
            return "#ffffff", "#000000"
        edge, inside = [], []
        step = max(1, (x1 - x0) // 60)
        for px in range(x0, x1 + 1, step):
            edge += [img.pixelColor(px, y0), img.pixelColor(px, y1)]
        for py in range(y0, y1 + 1, max(1, (y1 - y0) // 20)):
            edge += [img.pixelColor(x0, py), img.pixelColor(x1, py)]
            for px in range(x0, x1 + 1, step):
                inside.append(img.pixelColor(px, py))

        def middle(cs, f):
            vals = sorted(f(c) for c in cs)
            return vals[len(vals) // 2]
        bg = QColor(middle(edge, QColor.red), middle(edge, QColor.green), middle(edge, QColor.blue))
        light = bg.lightness()
        ink = sorted(inside, key=lambda c: -abs(c.lightness() - light))[:max(1, len(inside) // 12)]
        fg = QColor(middle(ink, QColor.red), middle(ink, QColor.green), middle(ink, QColor.blue)) if ink else \
            QColor("#000000")
        if abs(fg.lightness() - light) < 60:
            fg = QColor("#000000" if light > 128 else "#ffffff")
        return bg.name(), fg.name()

    def _pieces_shown(self, page: PageItem):
        hidden = set(self.hidden(page.index))
        for piece in page.pieces:
            piece.setVisible(not hidden.intersection(piece.objs))

    def _where(self):
        sight = self._in_sight()
        if sight and self.pages:
            r = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
            best = max(sight, key=lambda i: QRectF(self.pages[i].sceneBoundingRect()).intersected(r).height())
            self.where.setText(tr("Page {number} of {count}", number=self.position.get(best, 0) + 1,
                                  count=len(self.order)))
            it = self.thumb_items.get(best)
            if it is not None and not self.thumb_list.selectedItems():
                from PySide6.QtCore import QItemSelectionModel
                self.thumb_list.selectionModel().setCurrentIndex(self.thumb_list.indexFromItem(it),
                                                                 QItemSelectionModel.NoUpdate)
                self.thumb_list.scrollToItem(it)

    # ---- the pages: their order, turned, deleted (the list at the side)

    def _shown_size(self, i: int) -> tuple[float, float]:
        page = self.pages[i]
        return (page.h, page.w) if self.turns.get(i, 0) % 180 else (page.w, page.h)

    def _place_pages(self):
        """Puts the pages one under the other in their order, each turned as it should be."""
        shown = set(self.order)
        for page in self.pages:
            page.setVisible(page.index in shown)
        y, width = 0.0, 0.0
        self.offsets, self.position = [], {}
        for n, i in enumerate(self.order):
            page = self.pages[i]
            dw, dh = self._shown_size(i)
            page.setTransformOriginPoint(page.w / 2, page.h / 2)
            page.setRotation(self.turns.get(i, 0))
            page.setPos(-page.w / 2, y + dh / 2 - page.h / 2)  # (turned around its middle)
            self.offsets.append(y)
            self.position[i] = n
            y += dh + GAP
            width = max(width, dw)
        width = width or 600
        self.scene.setSceneRect(QRectF(-width / 2 - 40, -GAP, width + 80, max(y, GAP) + GAP))
        if self.fit:
            QTimer.singleShot(0, self.fit_width)
        self.visible_timer.start()
        self._where()

    def _fill_thumbs(self):
        picked = {it.data(Qt.UserRole) for it in self.thumb_list.selectedItems()}
        self.thumb_list.blockSignals(True)
        self.thumb_list.model().blockSignals(True)
        self.thumb_list.clear()
        self.thumb_items = {}
        for n, i in enumerate(self.order):
            it = QListWidgetItem(str(n + 1))
            it.setData(Qt.UserRole, i)
            it.setTextAlignment(Qt.AlignCenter)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
            self.thumb_list.addItem(it)
            self.thumb_items[i] = it
            self._thumb_icon(i)
            it.setSelected(i in picked)
        self.thumb_list.model().blockSignals(False)
        self.thumb_list.blockSignals(False)
        self.thumb_list.viewport().update()
        QTimer.singleShot(0, self._want_thumbs)

    def _thumb_icon(self, i: int):
        it = self.thumb_items.get(i)
        if it is None:
            return
        page = self.pages[i]
        img = self.thumb_imgs.get(i)
        dpr = self.devicePixelRatioF()
        if img is None:  # (white until it's drawn)
            k = min(THUMB.width() / page.w, THUMB.height() / page.h)
            pm = QPixmap(max(1, round(page.w * k * dpr)), max(1, round(page.h * k * dpr)))
            pm.fill(QColor("#ffffff"))
        else:
            pm = QPixmap.fromImage(img)
        turn = self.turns.get(i, 0)
        if turn:
            pm = pm.transformed(QTransform().rotate(turn), Qt.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        icon = QIcon(pm)
        icon.addPixmap(pm, QIcon.Selected)  # (not tinted when picked: the row shows it)
        it.setIcon(icon)

    def _want_thumbs(self):
        if self.renderer is None or not self.order or not self.side.isVisible():
            return
        lst = self.thumb_list
        first = lst.indexAt(QPoint(4, 4)).row()
        last = lst.indexAt(QPoint(4, lst.viewport().height() - 4)).row()
        first = max(0, first if first >= 0 else 0)
        last = last if last >= 0 else len(self.order) - 1
        dpr = self.devicePixelRatioF()
        wishes = []
        for n in range(max(0, first - 6), min(len(self.order), last + 7)):
            i = self.order[n]
            if i not in self.thumb_imgs and not self.pages[i].blank:
                page = self.pages[i]
                wishes.append((i, min(THUMB.width() / page.w, THUMB.height() / page.h) * dpr))
        self.renderer.want_thumbs(wishes)

    def _thumb(self, i: int, img: QImage):
        if not work.alive(self) or i >= len(self.pages):
            return
        self.thumb_imgs[i] = img
        self._thumb_icon(i)

    def go_to_page(self, i: int):
        if i not in self.position:
            return
        page = self.pages[i]
        r = page.sceneBoundingRect()
        bar = self.view.verticalScrollBar()
        top = self.view.mapFromScene(QPointF(r.center().x(), r.top() - GAP / 2)).y()
        bar.setValue(bar.value() + top)

    def picked_pages(self) -> list[int]:
        """The pages picked in the list (or the one in sight), in their order."""
        picked = [it.data(Qt.UserRole) for it in self.thumb_list.selectedItems()]
        if not picked:
            sight = self._in_sight()
            if sight:
                r = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
                picked = [max(sight, key=lambda i: QRectF(self.pages[i].sceneBoundingRect()).intersected(r).height())]
        return sorted(picked, key=lambda i: self.position.get(i, 0))

    def turn_pages(self, degrees: int, pages: list[int] | None = None):
        pages = self.picked_pages() if pages is None else pages
        if not pages:
            return
        self.end_text_edits()
        for i in pages:
            self.turns[i] = (self.turns.get(i, 0) + degrees) % 360
        self._place_pages()
        for i in pages:
            self._thumb_icon(i)
        self.commit()

    def delete_pages(self, pages: list[int] | None = None):
        pages = self.picked_pages() if pages is None else pages
        if not pages:
            return
        if len(pages) >= len(self.order):
            self.c.tell(tr("Rakiza Core PDF"), tr("A PDF needs at least one page."))
            return
        self.end_text_edits()
        self.scene.clearSelection()
        self.order = [i for i in self.order if i not in pages]
        self._place_pages()
        self._fill_thumbs()
        self.commit()
        self._say(tr("Deleted. Undo (Ctrl + Z) brings it back."))

    def move_pages(self, step: int, pages: list[int] | None = None):
        """Earlier (-1) or later (+1) by one place."""
        pages = self.picked_pages() if pages is None else pages
        if not pages:
            return
        order = list(self.order)
        for i in (pages if step < 0 else reversed(pages)):
            n = order.index(i)
            m = n + step
            if 0 <= m < len(order) and order[m] not in pages:
                order[n], order[m] = order[m], order[n]
        if order != self.order:
            self.order = order
            self._place_pages()
            self._fill_thumbs()
            self.commit()

    def _pages_dragged(self):
        order = [self.thumb_list.item(n).data(Qt.UserRole) for n in range(self.thumb_list.count())]
        if order != self.order and sorted(order) == sorted(self.order):
            self.order = order
            self._place_pages()
            self._fill_thumbs()
            self.commit()

    def _page_menu(self, pos):
        it = self.thumb_list.itemAt(pos)
        if it is not None and not it.isSelected():
            self.thumb_list.clearSelection()
            it.setSelected(True)
        pages = self.picked_pages()
        if not pages:
            return
        menu = QMenu(self)
        round_menu(menu)
        menu.addAction(tr("Turn right"), lambda: self.turn_pages(90, pages))
        menu.addAction(tr("Turn left"), lambda: self.turn_pages(-90, pages))
        menu.addAction(tr("Turn upside down"), lambda: self.turn_pages(180, pages))
        menu.addSeparator()
        up = menu.addAction(tr("Move up"), lambda: self.move_pages(-1, pages))
        up.setEnabled(self.position.get(pages[0], 0) > 0)
        down = menu.addAction(tr("Move down"), lambda: self.move_pages(1, pages))
        down.setEnabled(self.position.get(pages[-1], 0) < len(self.order) - 1)
        menu.addSeparator()
        menu.addAction(tr("Add a blank page after it"), lambda: self.add_blank_page(pages[-1]))
        menu.addSeparator()
        gone = menu.addAction(tr("Delete the page") if len(pages) == 1 else
                              tr("Delete {count} pages", count=len(pages)), lambda: self.delete_pages(pages))
        gone.setEnabled(len(pages) < len(self.order))
        sheet.menu(menu, self.thumb_list.viewport().mapToGlobal(pos))

    # ---- find and replace

    def _build_find(self, v):
        self.find_bar = QFrame()
        self.find_bar.setObjectName("pdfFindBar")
        fl = FlowLayout(self.find_bar, 6)
        muted = self.c.theme.muted
        self.find_box = QLineEdit()
        self.find_box.setPlaceholderText(tr("Find in this PDF"))
        self.find_box.setClearButtonEnabled(True)
        self.find_box.setMinimumWidth(200)
        self.find_timer = QTimer(self)
        self.find_timer.setSingleShot(True)
        self.find_timer.setInterval(180)
        self.find_timer.timeout.connect(self.find_all)
        self.find_box.textChanged.connect(lambda _t: self.find_timer.start())
        self.find_box.returnPressed.connect(lambda: self.next_match(1))
        self.case_box = QCheckBox(tr("Match case"))
        self.case_box.toggled.connect(lambda _on: self.find_all())
        prev_btn = IconButton(tr("Previous (Shift + F3)"))
        prev_btn.setIcon(icons.icon("chevron_left", muted))
        prev_btn.clicked.connect(lambda: self.next_match(-1))
        next_btn = IconButton(tr("Next (F3)"))
        next_btn.setIcon(icons.icon("chevron_right", muted))
        next_btn.clicked.connect(lambda: self.next_match(1))
        self.found_label = QLabel()
        self.found_label.setObjectName("muted")
        self.found_label.setMinimumWidth(90)
        self.replace_box = QLineEdit()
        self.replace_box.setPlaceholderText(tr("Replace with"))
        self.replace_box.setMinimumWidth(160)
        self.replace_box.returnPressed.connect(self.replace_one)
        self.replace_btn = QPushButton(tr("Replace"))
        self.replace_btn.setCursor(Qt.PointingHandCursor)
        self.replace_btn.clicked.connect(self.replace_one)
        self.replace_all_btn = QPushButton(tr("Replace all"))
        self.replace_all_btn.setCursor(Qt.PointingHandCursor)
        self.replace_all_btn.clicked.connect(self.replace_all)
        self.cover_all_btn = QPushButton(tr("Cover all"))
        self.cover_all_btn.setIcon(icons.icon("redact", self.c.theme.text))
        self.cover_all_btn.setCursor(Qt.PointingHandCursor)
        self.cover_all_btn.setToolTip(tr("Cover every place these words are: they're taken out of the file when "
                                         "it's saved"))
        self.cover_all_btn.clicked.connect(self.cover_matches)
        close = IconButton(tr("Close (Esc)"))
        close.setIcon(icons.icon("dismiss", muted))
        close.clicked.connect(self.hide_find)
        for w in (self.find_box, prev_btn, next_btn, self.found_label, self.case_box, self.replace_box,
                  self.replace_btn, self.replace_all_btn, self.cover_all_btn, close):
            fl.addWidget(w)
        self.find_bar.hide()
        v.addWidget(self.find_bar)

    def show_find(self, replace: bool = False):
        if not self.pages:
            return
        self.end_text_edits()
        self.find_bar.show()
        # every page's words are needed (read one by one, in the background)
        if self.renderer is not None:
            need = [i for i in self.order if not self.pages[i].asked_layout and not self.pages[i].blank]
            self.renderer.want_layout(need)
            for i in need:
                self.pages[i].asked_layout = True
        box = self.replace_box if replace and self.find_box.text() else self.find_box
        box.setFocus()
        box.selectAll()
        if self.find_box.text():
            self.find_all()

    def hide_find(self):
        self.find_bar.hide()
        self.matches, self.match_at = [], -1
        self._show_matches()
        self.view.setFocus()

    def _reading(self) -> int:
        """How many pages' words are still being read."""
        return sum(1 for i in self.order if self.pages[i].layout is None)

    def _spans(self, text: str, query: str) -> list[int]:
        if not query:
            return []
        hay, needle = (text, query) if self.case_box.isChecked() else (text.casefold(), query.casefold())
        if len(hay) != len(text):  # (a letter that changes length when its case is folded)
            hay, needle = text.lower(), query.lower()
        out, at = [], hay.find(needle)
        while at >= 0:
            out.append(at)
            at = hay.find(needle, at + max(1, len(needle)))
        return out

    def find_all(self):
        """Looks for the words on every page: the PDF's own lines, and text added or changed here."""
        query = self.find_box.text()
        keep = self.matches[self.match_at] if 0 <= self.match_at < len(self.matches) else None
        found = []
        if query and self.find_bar.isVisible():
            for i in self.order:
                page = self.pages[i]
                hidden = set(self.hidden(i))
                for piece in page.pieces:
                    if piece.kind == "line" and not hidden.intersection(piece.objs):
                        for at in self._spans(piece.d.get("read") or piece.d.get("text", ""), query):
                            found.append((page, piece, at))
                for item in page.childItems():
                    if isinstance(item, TextMark):
                        for at in self._spans(item.text(), query):
                            found.append((page, item, at))
            found.sort(key=lambda f: (self.position.get(f[0].index, 0), round(self._match_rect(*f).y() / 4),
                                      self._match_rect(*f).x()))
        self.matches = found
        if keep is not None and keep in found:
            self.match_at = found.index(keep)
        else:
            self.match_at = 0 if found else -1
        self._show_matches()

    def _match_rect(self, page, obj, at: int) -> QRectF:
        """Where the words are on the page (each letter taken as the same width: close enough to show)."""
        n = len(self.find_box.text())
        if isinstance(obj, OldPiece) and obj.d.get("words") and not obj.d.get("read"):
            exact = _words_rect(obj.d, at, n)
            if exact is not None:
                return exact
        if isinstance(obj, OldPiece):
            text = obj.d.get("read") or obj.d.get("text", "")
            x, y, w, h = obj.d["rect"]
            rtl = bool(obj.d.get("rtl"))
        else:
            text = obj.text()
            r = obj.mapRectToParent(obj.boundingRect()).adjusted(pdfedit.TEXT_MARGIN, pdfedit.TEXT_MARGIN,
                                                                 -pdfedit.TEXT_MARGIN, -pdfedit.TEXT_MARGIN)
            x, y, w, h = r.x(), r.y(), r.width(), r.height()
            rtl = obj.anchor is not None
            lines = text.split("\n")
            if len(lines) > 1:  # (which line it's on)
                k = text.count("\n", 0, at)
                h = h / len(lines)
                y += h * k
                start = text.rfind("\n", 0, at) + 1
                at -= start
                text = lines[k]
        each = w / max(1, len(text))
        left = x + w - (at + n) * each if rtl else x + at * each
        return QRectF(left, y, max(2.0, n * each), h)

    def _show_matches(self):
        for m in self.found_marks:
            if m.scene() is not None:
                self.scene.removeItem(m)
        self.found_marks = []
        by_page: dict = {}
        for n, (page, obj, at) in enumerate(self.matches):
            rects, current = by_page.setdefault(page.index, ([], [-1]))
            if n == self.match_at:
                current[0] = len(rects)
            rects.append(self._match_rect(page, obj, at))
        for i, (rects, current) in by_page.items():
            self.found_marks.append(FoundMarks(self.pages[i], rects, current[0]))
        query = self.find_box.text()
        reading = self._reading()
        if not query:
            text = ""
        elif self.matches:
            text = tr("{number} of {count}", number=self.match_at + 1, count=len(self.matches))
        else:
            text = tr("Reading the pages…") if reading else tr("Not found")
        if reading and self.matches:
            text += " …"
        self.found_label.setText(text)
        self.replace_btn.setEnabled(bool(self.matches))
        self.replace_all_btn.setEnabled(bool(self.matches) and not reading)
        self.replace_all_btn.setToolTip(tr("Reading the pages…") if reading else "")
        self.cover_all_btn.setEnabled(bool(self.matches) and not reading and
                                      any(isinstance(m[1], OldPiece) for m in self.matches))

    def next_match(self, step: int):
        if not self.find_bar.isVisible():
            self.show_find()
            return
        if not self.matches:
            return
        self.match_at = (self.match_at + step) % len(self.matches)
        self._show_matches()
        self._see_match()

    def _see_match(self):
        if not 0 <= self.match_at < len(self.matches):
            return
        page, obj, at = self.matches[self.match_at]
        r = page.mapRectToScene(self._match_rect(page, obj, at))
        view = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        if not view.adjusted(0, 30, 0, -30).contains(r):
            self.view.centerOn(QPointF(view.center().x() if self.fit else r.center().x(), r.center().y()))

    def _replaced(self, text: str, at: int | None) -> str:
        """The words with what was found replaced: at one place, or everywhere (at None)."""
        query, new = self.find_box.text(), self.replace_box.text()
        if at is not None:
            return text[:at] + new + text[at + len(query):]
        out, last = [], 0
        for k in self._spans(text, query):
            out.append(text[last:k] + new)
            last = k + len(query)
        return "".join(out) + text[last:]

    def _put(self, obj, text: str):
        if isinstance(obj, OldPiece):
            item = self.line_to_text(obj, text)
            self._pieces_shown(obj.page)
            return item
        obj.document().setPlainText(text)
        obj.d["text"] = text
        obj._grown()
        return obj

    def replace_one(self):
        if not 0 <= self.match_at < len(self.matches):
            return
        self.end_text_edits()
        page, obj, at = self.matches[self.match_at]
        text = (obj.d.get("read") or obj.d.get("text", "")) if isinstance(obj, OldPiece) else obj.text()
        self._put(obj, self._replaced(text, at))
        self.commit()
        self._ask_pages()
        n = self.match_at
        self.matches = []  # (the next one is where this one was)
        self.find_all()
        if self.matches:
            self.match_at = min(n, len(self.matches) - 1)
            self._show_matches()
            self._see_match()

    def replace_all(self):
        if not self.matches or self._reading():
            return
        self.end_text_edits()
        done, count = set(), 0
        for _page, obj, _at in list(self.matches):
            if id(obj) in done:
                continue
            done.add(id(obj))
            text = (obj.d.get("read") or obj.d.get("text", "")) if isinstance(obj, OldPiece) else obj.text()
            count += len(self._spans(text, self.find_box.text()))
            self._put(obj, self._replaced(text, None))
        self.commit()
        self._ask_pages()
        self.matches = []
        self.find_all()
        self.found_label.setText(tr("Replaced {count}", count=count))

    # ---- zoom

    def set_zoom(self, zoom: float, anchor: QPointF | None = None):
        zoom = min(max(zoom, ZOOMS[0]), ZOOMS[-1])
        vp = self.view.viewport()
        anchor = anchor if anchor is not None else QPointF(vp.width() / 2, vp.height() / 2)
        before = self.view.mapToScene(anchor.toPoint())
        self.zoom = zoom
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff if self.fit else Qt.ScrollBarAsNeeded)
        s = zoom * self.logicalDpiX() / 72
        self.view.setTransform(QTransform.fromScale(s, s))
        after = self.view.mapToScene(anchor.toPoint())
        delta = (after - before) * s
        self.view.horizontalScrollBar().setValue(self.view.horizontalScrollBar().value() - round(delta.x()))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().value() - round(delta.y()))
        self.zoom_btn.setText(f"{round(zoom * 100)}%")
        self.visible_timer.start()

    def zoom_by(self, factor: float, anchor: QPointF | None = None):
        self.fit = False
        self.set_zoom(self.zoom * factor, anchor)

    def zoom_step(self, step: int):
        self.fit = False
        now = self.zoom
        if step > 0:
            nxt = next((z for z in ZOOMS if z > now + 0.01), ZOOMS[-1])
        else:
            nxt = next((z for z in reversed(ZOOMS) if z < now - 0.01), ZOOMS[0])
        self.set_zoom(nxt)

    def fit_width(self):
        if not self.pages:
            return
        self.fit = True
        width = max((self._shown_size(i)[0] for i in self.order), default=600)
        room = max(200, self.view.viewport().width() - 40)
        zoom = room / (width * self.logicalDpiX() / 72)
        self.set_zoom(min(zoom, 2.0))

    # ---- tools

    def set_tool(self, key: str):
        self.end_text_edits()
        if key == "image":
            self.tool_btns[self.tool].setChecked(True)
            QTimer.singleShot(0, self.pick_picture)
            return
        if key == "sign":
            self.tool_btns[self.tool].setChecked(True)
            QTimer.singleShot(0, self.sign)
            return
        self.tool = key
        self.tool_btns[key].setChecked(True)
        if key not in ("select", "edit"):
            self.scene.clearSelection()
        drag = QGraphicsView.RubberBandDrag if key == "select" else QGraphicsView.NoDrag
        self.view.setDragMode(drag)
        cursor = {"text": Qt.IBeamCursor, "draw": Qt.CrossCursor, "shape": Qt.CrossCursor, "cover": Qt.CrossCursor,
                  "note": Qt.PointingHandCursor, "mark": Qt.IBeamCursor, "link": Qt.CrossCursor,
                  "fill": Qt.CrossCursor, "stamp": Qt.PointingHandCursor, "form": Qt.CrossCursor}.get(key)
        if cursor is None:
            self.view.viewport().unsetCursor()
        else:
            self.view.viewport().setCursor(cursor)
        for page in self.pages:
            for piece in page.pieces:
                piece.setAcceptedMouseButtons(Qt.LeftButton if key in piece.tools() else Qt.NoButton)
                piece.update()
        if key == "edit":
            self.view.setDragMode(QGraphicsView.NoDrag)
            self._read_if_needed(self._in_sight())
        self.view.setFocus()  # (Delete, the arrows and typing go to the pages)
        self._refresh()

    def active_tool(self) -> str:
        if self.tool == "mark":
            return self.mark_kind
        if self.tool == "shape":
            return self.shape_kind
        return self.tool

    def set_kind(self, key: str):
        if key in MARK_KEYS:
            self.mark_kind = key
        elif key in FILL_KEYS:
            self.fill_kind = key
        elif key in STAMP_KEYS:
            self.stamp_kind = key
        elif key in FORM_KEYS:
            self.form_kind = key
        else:
            self.shape_kind = key
        self._refresh()

    def style_key(self) -> str | None:
        """Which look the panel shows: the selected thing's, else the tool's."""
        items = self.selected_items()
        if items:
            kinds = {self._kind_of(i) for i in items}
            if len(kinds) == 1:
                return next(iter(kinds))
            return None
        t = self.active_tool()
        if t in ("rect", "ellipse", "line", "arrow"):
            return "shape"
        return t if t in self.style else ("text" if t == "edit" else None)

    @staticmethod
    def _kind_of(item) -> str | None:
        kind = getattr(item, "kind", None)
        if kind in ("rect", "ellipse", "line", "arrow"):
            return "shape"
        if kind == "ink":
            return "draw"
        if isinstance(item, OldPiece):
            if kind == "annot":
                return "pdflink" if item.d.get("kind") == "link" else "comment"
            return {"line": "line", "shape": "drawn"}.get(kind, "picture")
        return kind

    def selected_items(self) -> list:
        return [i for i in self.scene.selectedItems() if isinstance(i, (Mark, TextMark, OldPiece))]

    def selection_changed(self):
        if not work.alive(self.insp_title):  # (the scene let go of its items as the editor itself went)
            return
        if self.scene.selectedItems() and self.thumb_list.selectedItems():
            self.thumb_list.clearSelection()  # (Delete is for what's picked on the page now)
            if self.thumb_list.hasFocus():
                self.view.setFocus()
        if not self._filling:
            self._refresh()

    def _look_of(self, key: str | None, items: list) -> dict | None:
        """The look the panel shows: the first selected thing's (or the text being typed), else the tool's."""
        focus = self.typing()
        if focus is not None and not items:
            return focus.d
        if items and hasattr(items[0], "d") and not isinstance(items[0], OldPiece):
            return items[0].d
        if items and isinstance(items[0], OldPiece) and key in ("drawn", "pdflink", "comment"):
            return self.piece_look(items[0])
        return self.style.get(key) if key else None

    def piece_look(self, piece: OldPiece) -> dict:
        """A shape's or annotation's look and words as they are now (the PDF's own, as changed here)."""
        look = {k: piece.d[k] for k in ("color", "fill", "text", "url", "target") if k in piece.d}
        for d in self.restyled:
            if d["page"] == piece.page.index and d["objs"] == list(piece.objs):
                look.update({k: v for k, v in d.items() if k not in ("page", "objs")})
                if "url" in d:
                    look.pop("target", None)
        return look

    def restyle(self, piece: OldPiece, commit: bool = True, **change):
        """The PDF's own shape or annotation given another colour, words or link (kept until it's saved)."""
        for d in self.restyled:
            if d["page"] == piece.page.index and d["objs"] == list(piece.objs):
                d.update(change)
                break
        else:
            self.restyled.append(dict(change, page=piece.page.index, objs=list(piece.objs)))
        if "url" in change or "text" in change:
            piece.setToolTip(str(change.get("url") or change.get("text") or "")[:300])
        if "color" in change or "fill" in change:
            self._ask_pages()
        if commit:
            self.commit()

    def _refresh(self):
        """Shows what fits the tool or what's selected."""
        self._filling = True
        try:
            items = self.selected_items()
            focus = self.typing()
            typing = focus is not None
            key = "text" if typing and not items else self.style_key()
            tool = self.tool
            st = self._look_of(key, items)
            # the name at the top and what to do
            if items:
                title = TITLES.get(key or "", "") if key else ""
                if not title:
                    title = tr("{count} things", count=len(items))
                elif len(items) > 1:
                    title = tr(title) + f"  ·  {len(items)}"
                else:
                    title = tr(title)
            elif typing:
                title = tr(TITLES["line"] if focus.d.get("replaces") else TITLES["text"])
            else:
                t = self.active_tool()
                title = tr({"mark": "Highlight", "shape": "Shape"}.get(t, TITLES.get(t, "")) or "")
                if tool == "mark":
                    title = tr(TITLES.get(self.mark_kind, "Highlight"))
                elif tool == "shape":
                    title = tr(TITLES["shape"])
            self.insp_title.setText(title)
            show = {}
            show["kind"] = tool in ("mark", "shape", "fill", "stamp", "form") and not items
            for k, b in self.kind_btns.items():
                b.setVisible((tool == "mark" and k in MARK_KEYS) or (tool == "shape" and k in SHAPE_KEYS) or
                             (tool == "fill" and k in FILL_KEYS) or (tool == "stamp" and k in STAMP_KEYS) or
                             (tool == "form" and k in FORM_KEYS))
                b.setChecked(k in (self.mark_kind, self.shape_kind, self.fill_kind, self.stamp_kind, self.form_kind))
            show["find"] = tool == "cover" and not items
            show["para"] = tool == "edit" and not items and not typing
            text_on = key == "text" and st is not None
            show["font"] = show["align"] = text_on
            if text_on:
                font = st.get("font", "Arial")
                self.font_box.matched = st.get("matched", "") if st.get("pdf_font") else ""
                self.font_box.set_family(font)
                pdf_font = st.get("pdf_font")
                if pdf_font and pdffonts.key(pdf_font) == pdffonts.key(st.get("matched", "")):
                    self.font_note.setText(tr("The PDF's own font: {name}.", name=pdf_font))
                elif pdf_font:
                    self.font_note.setText(tr("The PDF used {name}, which isn't on this PC: {font} looks the "
                                              "closest.", name=pdf_font, font=st.get("matched") or font))
                else:
                    self.font_note.setText("")
                self.font_note.setVisible(bool(self.font_note.text()))
                self.size_box.setValue(round(float(st.get("size", 12))))
                self.bold_btn.setChecked(bool(st.get("bold")))
                self.italic_btn.setChecked(bool(st.get("italic")))
                self.underline_btn.setChecked(bool(st.get("underline")))
                align = st.get("align") or ""
                self.align_group.setExclusive(False)
                for k, b in self.align_btns.items():
                    b.setChecked(k == align)
                self.align_group.setExclusive(True)
                self._fill_spacings(st.get("spacing"))
            show["width"] = key in ("draw", "shape") and st is not None
            if show["width"]:
                w = float(st.get("width", 2.5))
                best = min(range(self.width_box.count()), key=lambda k: abs(self.width_box.itemData(k) - w))
                self.width_box.setCurrentIndex(best)
            shape_kind = items[0].kind if items and key == "shape" else self.shape_kind
            show["fill"] = (key == "shape" and st is not None and shape_kind in ("rect", "ellipse")) or \
                (key == "drawn" and st is not None and "fill" in st)
            if show["fill"]:
                for b, color in zip(self.fill_btns, FILLS):
                    if b.property("filled") is None:
                        b.setIcon(_swatch(color) if color else _no_fill())
                        b.setToolTip(color or tr("No fill"))
                        b.setAccessibleName(color or tr("No fill"))
                        b.setProperty("color", color or "")
                        b.setProperty("filled", True)
                    b.setChecked(QColor(color or "#00000000") == QColor(st.get("fill") or "#00000000")
                                 if color else not st.get("fill"))
            show["color"] = st is not None and "color" in st and key not in ("move", "image", "link")
            if show["color"]:
                palette = PENS if key == "highlight" else INK
                for b, color in zip(self.color_btns, palette + [None] * 7):
                    b.setVisible(color is not None)
                    if color is None:
                        continue
                    if b.property("color") != color:
                        b.setIcon(_swatch(color))
                        b.setToolTip(color)
                        b.setAccessibleName(color)
                        b.setProperty("color", color)
                    b.setChecked(QColor(color) == QColor(st.get("color", "")))
            show["opacity"] = st is not None and key in OPACITY_KINDS
            if show["opacity"]:
                op = round(100 * float(st.get("opacity", 1.0) or 1.0))
                self.opacity_slider.setValue(op)
                self.opacity_label.setText(f"{op}%")
            link = items[0] if len(items) == 1 and (getattr(items[0], "kind", "") == "link" or key == "pdflink") \
                else None
            show["link"] = link is not None
            if link is not None and not self.link_box.hasFocus():
                said = st if key == "pdflink" else link.d
                self.link_box.setText(self._link_text(said))
                self._link_said(said)
            pic = items[0] if len(items) == 1 and key in ("image", "picture", "move") else None
            show["picture"] = pic is not None
            if pic is not None:
                self.replace_pic_btn.setVisible(key in ("image", "picture"))
                self.turn_pic_btn.setVisible(key == "image")
                self.save_pic_btn.setVisible(key == "picture")
            show["actions"] = bool(items)
            self.duplicate_btn.setVisible(any(not isinstance(i, OldPiece) and getattr(i, "kind", "") != "move"
                                              for i in items))
            for name, box in (("kind", self.kind_box), ("find", self.find_section), ("para", self.para_box), ("font", self.font_section),
                              ("align", self.align_section), ("color", self.color_section),
                              ("fill", self.fill_section), ("width", self.width_section),
                              ("opacity", self.opacity_section), ("link", self.link_section),
                              ("picture", self.picture_section), ("actions", self.actions_section)):
                box.setVisible(bool(show.get(name)))
            needed = any(show.values())
            if needed != getattr(self, "insp_needed", None):
                self.insp_needed = needed
                self._side_shown()
            hint = HINTS.get(self.active_tool() if tool in ("mark", "shape") else tool, "")
            if items:
                hint = SELECTED_HINTS.get(key or "", "")
            if tool == "edit" and any(getattr(self.pages[i], "read_state", None) == "reading"
                                      for i in self._in_sight() if i < len(self.pages)):
                hint = tr("Reading the words on this page so they can be changed…")
            elif tool == "edit" and self.pages and not any(p.pieces for p in self.pages if p.layout) and \
                    any(p.layout for p in self.pages if not p.blank):
                hint = tr("The text on these pages can't be changed directly (it may be a scan). "
                          "Use Cover, then Add text over it.")
            elif self.field_items and tool in ("select", "text", "edit") and not items:
                hint = tr("This PDF has a form: click its boxes to fill them in.") + "  " + tr(hint)
            else:
                hint = tr(hint) if hint else ""
            self.hint.setText(hint)
            self.hint.setVisible(bool(hint))
            self.undo_btn.setEnabled(bool(self.history))
            self.redo_btn.setEnabled(bool(self.future))
            self.save_btn.setEnabled(self.saved is not None and self.current != self.saved and self._saving is None)
        finally:
            self._filling = False

    def _fill_spacings(self, now):
        self.spacing_box.clear()
        found = False
        for words, value in SPACINGS:
            self.spacing_box.addItem(tr(words), value)
            if (value is None and not now) or (value is not None and now and abs(float(now) - value) < 0.01):
                self.spacing_box.setCurrentIndex(self.spacing_box.count() - 1)
                found = True
        if not found:
            self.spacing_box.addItem(tr("As in the PDF ({number})", number=f"{float(now):.2f}"), float(now))
            self.spacing_box.setCurrentIndex(self.spacing_box.count() - 1)

    def _opacity_moved(self, value: int):
        self.opacity_label.setText(f"{value}%")
        if self._filling:
            return
        self.set_style(opacity=round(value / 100, 2), commit=not self.opacity_slider.isSliderDown())

    def set_style(self, commit: bool = True, **change):
        """The panel was used: the selected things change, and new ones will look like that."""
        if self._filling:
            return
        drawn = [i for i in self.selected_items() if isinstance(i, OldPiece) and i.kind == "shape"]
        for piece in drawn:  # (the PDF's own shapes: their line and fill colours)
            look = {k: v for k, v in change.items() if k in ("color", "fill") and v and k in piece.d}
            if look:
                self.restyle(piece, commit=False, **look)
        if drawn and commit:
            self.commit()
        items = [i for i in self.selected_items() if not isinstance(i, OldPiece)]
        focus = self.typing()
        if focus is not None and focus not in items:
            items.append(focus)
        if items:
            for item in items:
                kind = self._kind_of(item)
                keys = STYLE_KEYS.get(kind, ("color",))
                for k, val in change.items():
                    if k in keys and hasattr(item, "d"):
                        if val is None and k in ("spacing", "fill", "align"):
                            item.d.pop(k, None)
                        else:
                            item.d[k] = val
                if "font" in change and hasattr(item, "d"):
                    item.d.pop("font_ar", None)  # (a font picked: for all of the words)
                if isinstance(item, TextMark):
                    item.restyle()
                else:
                    item.restyle() if hasattr(item, "restyle") else item.update()
            if commit:
                self.commit()
        key = self.style_key() if not items else self._kind_of(items[0])
        if key in self.style:
            self.style[key].update({k: v for k, v in change.items() if k in STYLE_KEYS.get(key, ("color",))})
        if "font" in change:
            self.font_box.recent = ([change["font"]] + [f for f in self.font_box.recent if f != change["font"]])[:6]
        if commit:
            self._refresh()

    def _pick_color(self):
        key = self.style_key()
        items = self.selected_items()
        now = items[0].d.get("color") if items and hasattr(items[0], "d") else \
            (self.style.get(key, {}).get("color") if key else "#000000")
        color = sheet.get_color(QColor(now or "#000000"), self, tr("More colours…").rstrip("…"))
        if color.isValid():
            self.set_style(color=color.name())

    # ---- making things

    def add_item(self, d: dict, commit: bool = True, pixmap: QPixmap | None = None):
        page = self.pages[int(d["page"])]
        kind = d["kind"]
        if kind == "text":
            item = TextMark(self, page, d)
        elif kind in ("image", "cover", "move", "link", "stamp", "formbox"):
            key = (page.index, tuple(d.get("objs") or ()))
            if kind == "move" and pixmap is not None:
                self.move_pics[key] = pixmap
            elif kind == "move":  # (made again by undo or redo: the piece's picture, kept when it was moved)
                pixmap = self.move_pics.get(key)
            item = BoxMark(self, page, d, pixmap)
        elif kind in ("rect", "ellipse", "line", "arrow"):
            item = ShapeMark(self, page, d)
        elif kind == "ink":
            item = InkMark(self, page, d)
        elif kind in ("highlight", "underline", "strike"):
            item = MarkupMark(self, page, d)
        elif kind == "note":
            item = NoteMark(self, page, d)
        else:
            raise ValueError(kind)
        self.kept.add(item)  # (Qt lets go of an item nothing in Python holds on to)
        if commit:
            self.commit()
        return item

    def drop_item(self, item, commit: bool = True):
        page = getattr(item, "page", None)
        if item.scene() is not None:
            self.scene.removeItem(item)
        self.kept.discard(item)
        if commit:
            self.commit()
        if page is not None:
            self._pieces_shown(page)
            self._ask_pages()

    def new_text(self, page: PageItem, pt: QPointF):
        st = self.style["text"]
        fm = QFontMetricsF(pdfedit.font_for(st), pdfedit.layout_device())
        d = dict(st, kind="text", page=page.index, text="", x=pt.x(), y=pt.y() - fm.ascent() * 0.75)
        item = self.add_item(d, commit=False)
        self.scene.clearSelection()
        item.setSelected(True)
        item.start_edit()

    def edit_line(self, piece: OldPiece, at: QPointF | None = None):
        """A line of the PDF's own text becomes a text box with its words, ready to change (the whole
        paragraph it's in, when paragraphs are changed whole)."""
        pieces = self._para_pieces(piece)
        if len(pieces) > 1:
            self.edit_para(pieces, at)
            self.set_tool_quiet("select") if self.tool not in ("edit", "text") else None
            return
        item = self.line_to_text(piece)
        self._ask_pages()
        self.scene.clearSelection()
        item.setSelected(True)
        item.start_edit(at)
        self.set_tool_quiet("select") if self.tool not in ("edit", "text") else None

    def line_to_text(self, piece: OldPiece, text: str | None = None) -> "TextMark":
        """A line of the PDF's own text as a text box in its place (with other words, for Replace). A turned
        line stays turned; words of a scan (or hidden words over one) paint over the old words in the paper's
        colour; words in a font that doesn't say which letters it has are the ones read from the page."""
        page, d = piece.page, piece.d
        own = d.get("read") or ("" if d.get("unknown") else d["text"])
        was = own
        if text is None:
            text = own
        family, arabic = pdffonts.for_line(dict(d, text=text))
        size = float(d["size"])
        st = {"font": family, "size": size, "color": d["color"], "bold": d["bold"], "italic": d["italic"],
              "pdf_font": d.get("font", ""), "matched": family}
        if d.get("scan") or d.get("unknown") and not d.get("font"):
            st.pop("pdf_font")
        if arabic:
            st["font_ar"] = arabic  # (its Arabic words were in a font of their own)
        fm = QFontMetricsF(pdfedit.font_for(st), pdfedit.layout_device())
        x, y, w, h = d["rect"]
        top = d["base"] - fm.ascent() - pdfedit.TEXT_MARGIN if d.get("base") else y
        extra = {}
        if d.get("hidden") or d.get("scan"):
            # (what shows is the scan's picture: the old words are painted over, and taken out of it)
            paper, ink = (d["paper"], d["color"]) if d.get("paper") else self._sample_colors(page, d["rect"])
            st["color"] = ink
            extra["patch"] = [round(x - 1, 2), round(y - 1, 2), round(w + 2, 2), round(h + 2, 2), paper]
        if d.get("turn") is not None:
            ox, oy = d["origin"]
            pivot = [pdfedit.TEXT_MARGIN, round(pdfedit.TEXT_MARGIN + fm.ascent(), 2)]
            extra.update(turn=d["turn"], pivot=pivot)
            x, top = ox - pivot[0] + pdfedit.TEXT_MARGIN, oy - pivot[1]
        item = self.add_item(dict(st, kind="text", page=page.index, text=text, was=was, x=x - pdfedit.TEXT_MARGIN,
                                  y=top, replaces=list(piece.objs), rtl=bool(d.get("rtl")), **extra), commit=False)
        if d.get("rtl") and d.get("turn") is None:
            item.anchor = x + w + pdfedit.TEXT_MARGIN
            item._grown()
        piece.setVisible(False)
        return item

    def set_tool_quiet(self, key: str):
        self.tool = key
        self.tool_btns[key].setChecked(True)

    def move_piece(self, piece: OldPiece) -> BoxMark | None:
        """A piece of the PDF is being dragged: it becomes a box that can be moved and resized."""
        page = piece.page
        r = QRectF(piece.pos(), piece.rect.size())
        pm = None
        source = page.pm or page.thumb
        if source is not None:
            k = source.width() / page.w
            pm = source.copy(QRectF(r.x() * k, r.y() * k, r.width() * k, r.height() * k).toRect())
        item = self.add_item({"kind": "move", "page": page.index, "objs": list(piece.objs),
                              "orig": [round(r.x(), 2), round(r.y(), 2), round(r.width(), 2), round(r.height(), 2)],
                              "x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()}, commit=False, pixmap=pm)
        piece.setSelected(False)
        piece.setOpacity(0)  # (hidden once let go: until then it's still being dragged)
        self._ask_pages()
        return item

    def new_fill(self, page: PageItem, pt: QPointF):
        """Fill in: a tick, a cross or a dot centred where it was clicked; today's date or your initials as text
        starting there."""
        kind = self.fill_kind
        color = self.style["text"].get("color", "#000000")
        s = FILL_MARK
        x, y = pt.x(), pt.y()
        if kind in ("check", "cross"):
            strokes = fill_strokes(kind, x, y, s)
            item = self.add_item({"kind": "ink", "page": page.index, "strokes": strokes, "color": color,
                                  "width": round(s / 7, 2)}, commit=True)
        elif kind == "dot":
            r = s / 4
            item = self.add_item({"kind": "ellipse", "page": page.index, "points": [x - r, y - r, x + r, y + r],
                                  "color": color, "fill": color, "width": 0.5}, commit=True)
        else:
            if kind == "date":
                from PySide6.QtCore import QDate, QLocale
                from . import i18n
                words = QLocale(i18n.language()).toString(
                    QDate.currentDate(), QLocale.FormatType.ShortFormat)
            else:
                words = self.initials()
                if not words:
                    return
            st = self.style["text"]
            fm = QFontMetricsF(pdfedit.font_for(st), pdfedit.layout_device())
            item = self.add_item(dict(st, kind="text", page=page.index, text=words, x=x,
                                      y=y - fm.ascent() * 0.75), commit=True)
        self.scene.clearSelection()
        item.setSelected(True)
        self.view.setFocus()

    def initials(self, ask: bool = False) -> str:
        """Your initials (asked for the first time, and kept on this PC)."""
        kept = str(getattr(self.c.settings, "pdf_initials", "") or "").strip()
        if kept and not ask:
            return kept
        text, ok = sheet.get_text(self, tr("Initials"), tr("Your initials, put on forms with a click:"), kept)
        text = (text or "").strip()[:12]
        if not ok or not text:
            return ""
        self.c.settings.pdf_initials = text
        self.c.settings.save()
        return text

    def new_stamp(self, page: PageItem, pt: QPointF):
        """A stamp, its middle where it was clicked (Your own words… asks for them first)."""
        key, words, color, dated = next((s for s in STAMPS if s[0] == self.stamp_kind), STAMPS[0])
        if key == "custom":
            text, ok = sheet.get_text(self, tr("Stamp"), tr("The words on the stamp:"),
                                      str(getattr(self.c.settings, "pdf_stamp_words", "") or ""))
            text = (text or "").strip()[:40]
            if not ok or not text:
                return
            self.c.settings.pdf_stamp_words = text
            words = text
        else:
            words = tr(words)
        when = ""
        if dated:
            from PySide6.QtCore import QDateTime, QLocale
            from . import i18n
            when = QLocale(i18n.language()).toString(
                QDateTime.currentDateTime(), QLocale.FormatType.ShortFormat)
        w, h = pdfedit.stamp_size(words, when)
        if w > page.w - 8:  # (a long one on a small page: made to fit)
            k = (page.w - 8) / w
            w, h = w * k, h * k
        x = min(max(0.0, pt.x() - w / 2), max(0.0, page.w - w))
        y = min(max(0.0, pt.y() - h / 2), max(0.0, page.h - h))
        item = self.add_item({"kind": "stamp", "page": page.index, "text": words, "date": when, "color": color,
                              "x": round(x, 2), "y": round(y, 2), "w": w, "h": h}, commit=True)
        self.scene.clearSelection()
        item.setSelected(True)
        self.view.setFocus()

    def new_form_box(self, page: PageItem, pt: QPointF):
        """A form box (to type in, or to tick), its top left corner where it was clicked, named after the
        ones before it (Text 1, Tick 1…)."""
        kind = self.form_kind if self.form_kind in FORM_KEYS else "formtext"
        w, h = FORM_SIZES[kind]
        word = tr("Text") if kind == "formtext" else tr("Tick")
        taken = {str(i.d.get("name", "")) for i in self.kept if getattr(i, "kind", "") == "formbox"}
        taken |= {str(f.get("name", "")) for f in self.field_list if isinstance(f, dict)}
        n = 1
        while f"{word} {n}" in taken:
            n += 1
        x = min(max(0.0, pt.x() - (w / 2 if kind == "formcheck" else 2)), max(0.0, page.w - w))
        y = min(max(0.0, pt.y() - h / 2), max(0.0, page.h - h))
        item = self.add_item({"kind": "formbox", "page": page.index, "form": kind, "name": f"{word} {n}",
                              "x": round(x, 2), "y": round(y, 2), "w": w, "h": h}, commit=True)
        self.scene.clearSelection()
        item.setSelected(True)
        self.view.setFocus()

    def find_and_cover(self):
        """Find and cover: phone numbers, email addresses, ID and card numbers… (and words of one's own) found
        on every page and covered, to be looked over before saving."""
        if self.src is None or not self.pages:
            return
        dlg = FindCoverDialog(self.c.icon, self.c.theme.dark,
                              str(getattr(self.c.settings, "pdf_cover_kinds", "") or "").split(","))
        try:
            if not sheet.run(dlg):
                return
            kinds, words = dlg.kinds(), dlg.words()
        finally:
            dlg.deleteLater()
        if not kinds and not words:
            return
        self.c.settings.pdf_cover_kinds = ",".join(kinds)
        from . import pdfreader
        src, password = self.src, self.password or ""
        self.find_cover_btn.setEnabled(False)
        self.find_cover_btn.setText(tr("Looking…"))

        def done(result):
            if not work.alive(self):
                return
            self.find_cover_btn.setEnabled(True)
            self.find_cover_btn.setText(tr("Find and cover…"))
            if isinstance(result, Exception):
                log.warning("find and cover: %s", result)
                self.c.tell(tr("Find and cover"), tr("The words of this PDF couldn't be read."))
                return
            self.add_found_covers(result)
        self._find_job = work.run(lambda _p: pdfreader.find_private(src, password, kinds, words), lambda _: None,
                                  done, name="corefold-pdf-find-cover")

    def add_found_covers(self, found: list) -> int:
        """Covers over what was found ([(page, [(x, y, w, h)])]), all taken back with one Undo."""
        color = self.style["cover"]["color"]
        added = 0
        for page_index, boxes in found:
            if not 0 <= page_index < len(self.pages):
                continue
            page = self.pages[page_index]
            for x, y, w, h in boxes:
                x, y = max(0.0, x), max(0.0, y)
                w, h = min(w, page.w - x), min(h, page.h - y)
                if w < 1 or h < 1:
                    continue
                self.add_item({"kind": "cover", "page": page_index, "x": round(x, 2), "y": round(y, 2),
                               "w": round(w, 2), "h": round(h, 2), "color": color}, commit=False)
                added += 1
        if added:
            self.commit()
            first = found[0][0]
            if 0 <= first < len(self.pages):
                self.view.centerOn(self.pages[first])
            self.c.tell(tr("Find and cover"), tr("{count} found and covered. Look them over, then save.",
                                                 count=added))
        else:
            self.c.tell(tr("Find and cover"), tr("Nothing like that was found. Scanned pages need Make searchable "
                                                 "first."))
        return added

    def new_note(self, page: PageItem, pt: QPointF):
        item = self.add_item({"kind": "note", "page": page.index, "x": pt.x() - 10, "y": pt.y() - 10, "text": ""},
                             commit=False)
        if not self.edit_note(item):
            self.drop_item(item, commit=False)

    def edit_note(self, item: NoteMark) -> bool:
        dlg = NoteDialog(item.d.get("text", ""), self.c.icon, self.c.theme.dark)
        try:
            if not sheet.run(dlg):
                return False
            text = dlg.text()
        finally:
            dlg.deleteLater()
        if not text.strip():
            self.drop_item(item)
            return False
        item.d["text"] = text
        item.setToolTip(text)
        self.commit()
        return True

    def _place_box(self, kind_d: dict, w: float, h: float):
        """A picture or signature goes in the middle of the page in sight, not bigger than half of it."""
        sight = self._in_sight()
        if not sight:
            return
        r = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        index = max(sight, key=lambda i: QRectF(self.pages[i].sceneBoundingRect()).intersected(r).height())
        page = self.pages[index]
        most = page.w * 0.45
        if w > most:
            h, w = h * most / w, most
        if h > page.h * 0.45:
            w, h = w * page.h * 0.45 / h, page.h * 0.45
        center = page.mapFromScene(r.intersected(page.sceneBoundingRect()).center())
        x = min(max(center.x() - w / 2, 0), page.w - w)
        y = min(max(center.y() - h / 2, 0), page.h - h)
        item = self.add_item(dict(kind_d, page=index, x=round(x, 2), y=round(y, 2), w=round(w, 2), h=round(h, 2)))
        self.set_tool("select")
        self.scene.clearSelection()
        item.setSelected(True)
        self.view.setFocus()

    def pick_picture(self):
        kinds = " ".join(f"*{ext}" for ext in sorted(IMAGE_EXT | {".jpeg", ".jfif", ".tif", ".heif"}))
        name, _ = QFileDialog.getOpenFileName(self, tr("Pick a picture"), str(Path.home()),
                                              f"{tr('Pictures')} ({kinds})")
        if name:
            self.add_picture(Path(name))

    def add_picture(self, path: Path, signature: bool = False):
        path = self._readable_picture(path)
        if path is None:
            return
        img = QImage(str(path))
        # its size as it would be printed (pictures without a size: 96 dpi)
        dpm = img.dotsPerMeterX() or 3780
        w = img.width() / (dpm * 0.0254) * 72
        h = img.height() / (dpm * 0.0254) * 72
        if signature:
            w, h = 160.0, 160.0 * img.height() / max(1, img.width())
        self._place_box({"kind": "image", "path": str(path)}, w, h)

    def _readable_picture(self, path: Path) -> Path | None:
        """The picture as a file Qt reads, upright (a copy, for kinds Qt doesn't read or a phone photo
        kept on its side), or None (and the user is told) when it can't be read."""
        img = QImage(str(path))
        if img.isNull() and path.is_file():  # (HEIC, WebP… that Qt doesn't read: through Pillow)
            try:
                from .converters import load_image
                im = load_image(path)
                path = self._temp_file(".png")
                im.save(path)
                img = QImage(str(path))
            except Exception:
                log.debug("couldn't read %s", path, exc_info=True)
        if img.isNull():
            self.c.tell(tr("Rakiza Core PDF"), tr("That picture couldn't be read."))
            return None
        from PySide6.QtGui import QImageIOHandler, QImageReader
        reader = QImageReader(str(path))
        if reader.transformation() != QImageIOHandler.Transformation.TransformationNone:
            # (a phone photo kept on its side, with a note to turn it: Qt doesn't turn it, so an upright copy
            # is used, on the page and in the saved PDF)
            reader.setAutoTransform(True)
            upright = reader.read()
            if not upright.isNull():
                path = self._temp_file(".png")
                upright.save(str(path))
        return path

    def sign(self):
        from .settings import CONFIG_DIR
        dlg = SignPadDialog(CONFIG_DIR / "signature.png", self.c.icon, self.c.theme.dark)
        try:
            if not sheet.run(dlg):
                return
            out = self._temp_file(".png")
            dlg.save_to(out)
        finally:
            dlg.deleteLater()
        self.add_picture(out, signature=True)

    def _temp_file(self, ext: str) -> Path:
        if self.temp is None or not Path(self.temp).is_dir():
            self.temp = tempfile.mkdtemp(prefix="corefold-pdfedit-")
        n = len(list(Path(self.temp).iterdir()))
        return Path(self.temp) / f"picture-{n + 1}{ext}"

    def paste(self):
        from PySide6.QtWidgets import QApplication
        focus = self.scene.focusItem()
        if isinstance(focus, QGraphicsTextItem) and focus.textInteractionFlags() & Qt.TextEditorInteraction:
            focus.paste() if hasattr(focus, "paste") else None
            return
        mime = QApplication.clipboard().mimeData()
        if mime is None or not self.pages:
            return
        if mime.hasFormat(ITEMS_MIME) and self._paste_items(mime):
            return
        if mime.hasImage():
            img = QImage(mime.imageData())
            if not img.isNull():
                path = self._temp_file(".png")
                img.save(str(path))
                self.add_picture(path)
                return
        if mime.hasUrls():
            for u in mime.urls():
                p = Path(u.toLocalFile())
                if norm(p.suffix) in IMAGE_EXT:  # (.jpeg, .tif… are their usual names here)
                    self.add_picture(p)
                    return
        if mime.hasText() and mime.text().strip():
            sight = self._in_sight()
            if not sight:
                return
            page = self.pages[sight[0]]
            r = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
            c = page.mapFromScene(r.intersected(page.sceneBoundingRect()).center())
            item = self.add_item(dict(self.style["text"], kind="text", page=page.index, text=mime.text().strip(),
                                      x=max(0, c.x() - 60), y=c.y()))
            self.scene.clearSelection()
            item.setSelected(True)

    def text_rects(self, page: PageItem, a: QPointF, b: QPointF, kind: str) -> list[list[float]]:
        """The words between two points, as one box per line (or just the box dragged, away from words)."""
        chars = (page.layout or {}).get("chars") or []

        def nearest(pt):
            best, best_d = None, 1e9
            for n, (x, y, w, h) in enumerate(chars):
                dx = max(x - pt.x(), 0, pt.x() - x - w)
                dy = max(y - pt.y(), 0, pt.y() - y - h)
                d = dx * dx + dy * dy * 4
                if d < best_d:
                    best, best_d = n, d
                    if d == 0:
                        break
            if best is not None:
                h = chars[best][3]
                if best_d > (h * 0.8) ** 2:
                    return None
            return best
        i, j = nearest(a), nearest(b)
        if i is None or j is None:
            r = QRectF(a, b).normalized()
            if kind == "highlight":
                if r.width() < 3 or r.height() < 3:
                    return []
                return [[round(r.x(), 2), round(r.y(), 2), round(r.width(), 2), round(r.height(), 2)]]
            if r.width() < 3:
                return []
            h = max(r.height(), 10.0)
            return [[round(r.x(), 2), round(r.center().y() - h / 2, 2), round(r.width(), 2), round(h, 2)]]
        if i > j:
            i, j = j, i
        rects: list[list[float]] = []
        for x, y, w, h in chars[i:j + 1]:
            if rects:
                last = rects[-1]
                same_line = abs((last[1] + last[3] / 2) - (y + h / 2)) < max(last[3], h) * 0.5
                if same_line:
                    l, t = min(last[0], x), min(last[1], y)
                    r_, b_ = max(last[0] + last[2], x + w), max(last[1] + last[3], y + h)
                    rects[-1] = [l, t, r_ - l, b_ - t]
                    continue
            rects.append([x, y, w, h])
        return [[round(v, 2) for v in r] for r in rects]

    # ---- paragraphs of the PDF's own text

    def typing(self) -> "TextMark | None":
        """The text box being typed in (while the options at the side are used too), or None."""
        for item in list(self.kept):
            if isinstance(item, TextMark) and work.alive(item) and item.editing():
                return item
        return None

    def keeps_typing(self, w: QWidget | None) -> bool:
        """Whether focus going to w (the options at the side, the list of fonts) leaves the typing as it is."""
        if w is None:
            return False
        panel = self.font_box.panel
        return self.inspector.isAncestorOf(w) or (panel is not None and (w is panel or panel.isAncestorOf(w)))

    def para_of(self, piece: "OldPiece") -> dict | None:
        """The paragraph a line of the PDF's own text is in (when whole paragraphs are changed), or None."""
        if piece.kind != "line" or not self.para_check.isChecked():
            return None
        n = piece.d.get("para")
        paras = (piece.page.layout or {}).get("paras") or []
        if n is None or not 0 <= n < len(paras):
            return None
        return paras[n]

    def para_hot(self, piece: "OldPiece") -> bool:
        return self.tool == "edit" and self.hot_para is not None and \
            self.hot_para == (piece.page.index, piece.d.get("para")) and piece.kind == "line"

    def hover_piece(self, piece: "OldPiece", on: bool):
        if self.tool != "edit" or self.para_of(piece) is None:
            return
        self.hot_para = (piece.page.index, piece.d.get("para")) if on else None
        for other in piece.page.pieces:
            if other.d.get("para") == piece.d.get("para"):
                other.update()

    def _paras_toggled(self, _on: bool):
        self.hot_para = None
        for page in self.pages:
            for piece in page.pieces:
                piece.update()

    def _para_pieces(self, piece: "OldPiece") -> list["OldPiece"]:
        para = self.para_of(piece)
        if para is None:
            return [piece]
        by_k = {getattr(p, "k", -1): p for p in piece.page.pieces if p.kind == "line"}
        pieces = [by_k[k] for k in para["lines"] if k in by_k]
        if len(pieces) < 2 or any(not p.isVisible() for p in pieces):
            return [piece]  # (a line of it was changed on its own already: just this line)
        return pieces

    def edit_para(self, pieces: list["OldPiece"], at: QPointF | None = None) -> "TextMark":
        """A paragraph of the PDF's own text becomes one text box as wide as it, with its words: they flow
        from line to line again as they're changed, lined up and spaced as they were."""
        page, first = pieces[0].page, pieces[0].d
        para = self.para_of(pieces[0])
        text = ""
        for p in pieces:
            words = p.d["text"].strip()
            if text and not text.endswith("-"):
                text += " "
            text += words
        family, arabic = pdffonts.for_line(dict(first, text=text))
        size = float(first["size"])
        st = {"font": family, "size": size, "color": first["color"], "bold": first["bold"],
              "italic": first["italic"], "pdf_font": first.get("font", ""), "matched": family,
              "align": para["align"], "spacing": para["spacing"]}
        if arabic:
            st["font_ar"] = arabic
        fm = QFontMetricsF(pdfedit.font_for(st), pdfedit.layout_device())
        x, y, w, h = para["rect"]
        top = first["base"] - fm.ascent() - pdfedit.TEXT_MARGIN if first.get("base") else y
        objs = sorted({k for p in pieces for k in p.objs}, key=str)
        item = self.add_item(dict(st, kind="text", page=page.index, text=text, was=text, x=x - pdfedit.TEXT_MARGIN,
                                  y=top, width=round(w + 2 * pdfedit.TEXT_MARGIN + 1, 2), replaces=objs),
                             commit=False)
        for p in pieces:
            p.setVisible(False)
        self._ask_pages()
        self.scene.clearSelection()
        item.setSelected(True)
        item.start_edit(at)
        return item

    # ---- pages added

    def add_blank_page(self, after: int | None = None):
        """A blank page after the page picked (or the one in sight), as big as it."""
        if not self.pages:
            return
        if after is None:
            picked = self.picked_pages()
            after = picked[-1] if picked else self.order[-1]
        self.end_text_edits()
        w, h = self._shown_size(after)
        page = PageItem(len(self.pages), w, h)
        page.blank = True
        page.layout = {"size": (w, h), "lines": [], "paras": [], "images": [], "chars": []}
        page.asked_layout = True
        self.scene.addItem(page)
        self.pages.append(page)
        n = self.order.index(after) + 1 if after in self.order else len(self.order)
        self.order.insert(n, page.index)
        self._place_pages()
        self._fill_thumbs()
        self.commit()
        self.go_to_page(page.index)
        self._say(tr("A blank page was added after page {number}.", number=n))

    # ---- pictures

    def _one_selected(self):
        items = self.selected_items()
        return items[0] if len(items) == 1 else None

    def replace_picture(self):
        """Another picture in the place of the selected one (the PDF's own picture is taken out of the file)."""
        item = self._one_selected()
        if item is None:
            return
        kinds = " ".join(f"*{ext}" for ext in sorted(IMAGE_EXT | {".jpeg", ".jfif", ".tif", ".heif"}))
        name, _ = QFileDialog.getOpenFileName(self, tr("Pick a picture"), str(Path.home()),
                                              f"{tr('Pictures')} ({kinds})")
        if not name:
            return
        path = self._readable_picture(Path(name))
        if path is None:
            return
        if isinstance(item, OldPiece):
            page = item.page
            x, y = item.pos().x(), item.pos().y()
            w, h = item.rect.width(), item.rect.height()
            d = {"kind": "image", "path": str(path), "page": page.index, "x": round(x, 2), "y": round(y, 2),
                 "w": round(w, 2), "h": round(h, 2), "replaces": list(item.objs)}
            item.setSelected(False)
            new = self.add_item(d)
            self._pieces_shown(page)
            self._ask_pages()
        else:
            d = item.data()
            d["path"] = str(path)
            d.pop("turn", None)
            self.drop_item(item, commit=False)
            new = self.add_item(d)
        self.scene.clearSelection()
        new.setSelected(True)

    def turn_picture(self):
        item = self._one_selected()
        if not isinstance(item, BoxMark) or item.kind != "image":
            return
        c = item.pos() + QPointF(item.w / 2, item.h / 2)
        item.prepareGeometryChange()
        item.d["turn"] = (int(item.d.get("turn", 0)) + 90) % 360
        item.w, item.h = item.h, item.w
        item.ratio = item.w / item.h if item.h else 1
        item.handle.setPos(item.w, item.h)
        item.setPos(c - QPointF(item.w / 2, item.h / 2))
        item.update()
        self.commit()

    def save_picture(self):
        """A copy of the PDF's own picture, as a file."""
        item = self._one_selected()
        if not isinstance(item, OldPiece) or item.kind != "image" or self.src is None:
            return
        name, _ = QFileDialog.getSaveFileName(self, tr("Save the picture…").rstrip("…"),
                                              str(self.src.with_name(f"{self.src.stem} picture.png")), "PNG (*.png)")
        if not name:
            return
        try:
            pdfedit.save_picture(self.src, self.password, item.page.index, item.objs[0], Path(name))
        except Exception:
            log.debug("couldn't save the picture", exc_info=True)
            self.c.tell(tr("Rakiza Core PDF"), tr("That picture couldn't be saved."))
            return
        self._say(tr("Saved {name}", name=short_name(Path(name).name)))

    # ---- copies of what was added

    def _copyable(self) -> list[dict]:
        out = []
        for item in self.selected_items():
            if isinstance(item, OldPiece) or getattr(item, "kind", "") == "move":
                continue
            d = item.data()
            for k in ("replaces", "was", "pdf_font", "matched"):
                d.pop(k, None)
            out.append(d)
        return out

    @staticmethod
    def _moved(d: dict, dx: float, dy: float, page: int | None = None) -> dict:
        d = dict(d)
        if page is not None:
            d["page"] = page
        for k, step in (("x", dx), ("y", dy)):
            if k in d:
                d[k] = round(float(d[k]) + step, 2)
        if "points" in d:
            x1, y1, x2, y2 = d["points"]
            d["points"] = [x1 + dx, y1 + dy, x2 + dx, y2 + dy]
        if "strokes" in d:
            d["strokes"] = [[[x + dx, y + dy] for x, y in s] for s in d["strokes"]]
        if "rects" in d:
            d["rects"] = [[x + dx, y + dy, w, h] for x, y, w, h in d["rects"]]
        return d

    def _add_copies(self, ds: list[dict], dx: float, dy: float, page: int | None = None):
        self.end_text_edits()
        made = []
        for d in ds:
            if not 0 <= int(page if page is not None else d.get("page", -1)) < len(self.pages):
                continue
            made.append(self.add_item(self._moved(d, dx, dy, page), commit=False))
        if not made:
            return
        self.commit()
        self.scene.clearSelection()
        for item in made:
            item.setSelected(True)
        self.set_tool_quiet("select")
        self.view.setFocus()

    def duplicate_selected(self):
        ds = self._copyable()
        if ds:
            self._add_copies(ds, 12, 12)

    def copy_selected(self) -> bool:
        import json
        from PySide6.QtCore import QMimeData
        ds = self._copyable()
        if not ds:
            return False
        mime = QMimeData()
        mime.setData(ITEMS_MIME, json.dumps(ds).encode("utf-8"))
        words = "\n".join(d.get("text", "") for d in ds if d.get("kind") == "text").strip()
        if words:
            mime.setText(words)
        QApplication.clipboard().setMimeData(mime)
        self.copied_at = 0
        self._say(tr("Copied. Ctrl + V puts a copy on the page in sight."))
        return True

    def _paste_items(self, mime) -> bool:
        import json
        try:
            ds = json.loads(bytes(mime.data(ITEMS_MIME)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return False
        if not isinstance(ds, list) or not ds:
            return False
        sight = self._in_sight()
        if not sight:
            return False
        r = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        page = max(sight, key=lambda i: QRectF(self.pages[i].sceneBoundingRect()).intersected(r).height())
        self.copied_at += 1
        same = all(int(d.get("page", -1)) == page for d in ds)
        step = 20 * self.copied_at if same else 0
        self._add_copies([d for d in ds if isinstance(d, dict) and d.get("kind") in COPYABLE], step, step, page)
        return True

    # ---- links

    def _link_text(self, d: dict) -> str:
        if d.get("target") is not None and d["target"] in self.position:
            return str(self.position[d["target"]] + 1)
        return str(d.get("url", ""))

    def _link_said(self, d: dict):
        if d.get("target") is not None:
            text = tr("Goes to page {number} of this PDF.", number=self.position.get(d["target"], 0) + 1) \
                if d["target"] in self.position else tr("That page was deleted.")
        elif d.get("url"):
            text = tr("Opens {address}", address=pdfedit.web_address(d["url"]))
        else:
            text = tr("Type a web address, like example.com, or the number of a page in this PDF.")
        self.link_note.setText(text)

    def _link_typed(self):
        item = self._one_selected()
        if isinstance(item, OldPiece) and item.kind == "annot" and item.d.get("kind") == "link":
            self._old_link_typed(item)
            return
        if not isinstance(item, BoxMark) or item.kind != "link":
            return
        text = self.link_box.text().strip()
        d = dict(item.d)
        d.pop("url", None)
        d.pop("target", None)
        if text.isdigit() and 1 <= int(text) <= len(self.order):
            d["target"] = self.order[int(text) - 1]
        elif text and pdfedit.web_address(text):
            d["url"] = text
        elif text:
            self.link_note.setText(tr("That isn't a web address or a page of this PDF."))
            return
        if d != item.d:
            item.d = d
            item.setToolTip(self._link_text(d))
            item.update()
            self.commit()
        self._link_said(item.d)

    def _old_link_typed(self, piece: OldPiece):
        """A link that was already in the PDF, sent somewhere else."""
        text = self.link_box.text().strip()
        if text.isdigit() and 1 <= int(text) <= len(self.order):
            change = {"target": self.order[int(text) - 1], "url": ""}
        elif text and pdfedit.web_address(text):
            change = {"url": text, "target": None}
        elif text:
            self.link_note.setText(tr("That isn't a web address or a page of this PDF."))
            return
        else:
            return
        now = self.piece_look(piece)
        if (now.get("url") or "", now.get("target")) != (change["url"], change["target"]):
            self.restyle(piece, **change)
        self._link_said(self.piece_look(piece))

    def open_annot(self, piece: OldPiece):
        """An annotation double-clicked: a note's words can be changed; typed-in text becomes text on the page
        that can be changed like any other."""
        kind = piece.d.get("kind")
        if kind == "freetext":
            self._freetext_to_text(piece)
            return
        if kind == "link":
            self.scene.clearSelection()
            piece.setSelected(True)
            self.link_box.setFocus()
            self.link_box.selectAll()
            return
        dlg = NoteDialog(self.piece_look(piece).get("text", ""), self.c.icon, self.c.theme.dark)
        try:
            if not sheet.run(dlg):
                return
            text = dlg.text()
        finally:
            dlg.deleteLater()
        if text != self.piece_look(piece).get("text", ""):
            self.restyle(piece, text=text)

    def _freetext_to_text(self, piece: OldPiece):
        """Words typed in a box by another PDF app: the box goes, and its words become text on the page, in
        its place, ready to change."""
        x, y, w, h = piece.d["rect"]
        text = self.piece_look(piece).get("text", "")
        st = dict(self.style["text"])
        st["size"] = max(6.0, min(36.0, round(h * 0.45))) if "\n" not in text else 11.0
        item = self.add_item(dict(st, kind="text", page=piece.page.index, text=text, x=x, y=y,
                                  width=round(max(24.0, w), 2), replaces=list(piece.objs), was=text), commit=False)
        piece.setVisible(False)
        self._ask_pages()
        self.scene.clearSelection()
        item.setSelected(True)
        item.start_edit()

    # ---- covering every place the words are found

    def cover_matches(self):
        """Every place what's typed in Find is on the pages gets a cover: those words are taken out of the
        file when it's saved (like a name or a number that mustn't be sent)."""
        if not self.matches or self._reading():
            return
        self.end_text_edits()
        color = self.style["cover"]["color"]
        count = 0
        for page, obj, at in list(self.matches):
            if not isinstance(obj, OldPiece):
                continue  # (words typed here: change them instead)
            r = self._match_rect(page, obj, at).adjusted(-0.6, -0.6, 0.6, 0.6)
            self.add_item({"kind": "cover", "page": page.index, "x": round(r.x(), 2), "y": round(r.y(), 2),
                           "w": round(r.width(), 2), "h": round(r.height(), 2), "color": color}, commit=False)
            count += 1
        if not count:
            return
        self.commit()
        self.found_label.setText(tr("Covered {count}", count=count))
        self._say(tr("Covered {count}. What's under the covers is taken out when the PDF is saved.", count=count))

    def _say(self, text: str):
        """A short note at the bottom (what just happened), gone after a few seconds."""
        self.note.setText(text)
        self.note_timer.start()

    def select_all(self):
        """Everything on the page in the middle of the view picked (Ctrl+A): the PDF's own pieces, its
        background too, and what was added."""
        sight = self._in_sight()
        if not sight:
            return
        middle = self.view.mapToScene(self.view.viewport().rect().center())
        page = self.page_at(middle) or self.pages[sight[len(sight) // 2]]
        if self.tool != "select":
            self.set_tool("select")
        self.scene.clearSelection()
        for item in page.childItems():
            if isinstance(item, (Mark, TextMark, OldPiece)) and item.isVisible() and \
                    item.flags() & QGraphicsItem.ItemIsSelectable:
                item.setSelected(True)

    # ---- deleting

    def delete_selected(self):
        items = self.selected_items()
        if not items:
            return
        pages = set()
        for item in items:
            if isinstance(item, OldPiece):
                self.removed.append({"page": item.page.index, "objs": list(item.objs)})
                item.setSelected(False)
                pages.add(item.page)
            else:
                if isinstance(item, TextMark) and item.d.get("replaces"):
                    # a changed line deleted: the PDF's own line goes too
                    self.removed.append({"page": item.page.index, "objs": list(item.d["replaces"])})
                elif isinstance(item, BoxMark) and item.kind == "move":
                    self.removed.append({"page": item.page.index, "objs": list(item.d["objs"])})
                pages.add(item.page)
                self.scene.removeItem(item)
                self.kept.discard(item)
        for page in pages:
            self._pieces_shown(page)
        self.commit()
        self._ask_pages()

    # ---- forms

    def set_field(self, f: dict, value: str):
        self.fields[f["name"]] = value
        for item in self.field_items:
            if item.f["name"] == f["name"]:
                item.update()
        self.commit()

    def next_field(self, field: FieldItem):
        texts = [i for i in self.field_items if i.f["kind"] == "text"]
        if field in texts and len(texts) > 1:
            nxt = texts[(texts.index(field) + 1) % len(texts)]
            self.view.ensureVisible(nxt.sceneBoundingRect(), 40, 40)
            nxt.start_typing()

    # ---- what's been done, undo and redo

    def model_items(self) -> list[dict]:
        out = []
        for page in self.pages:
            for item in page.childItems():
                if isinstance(item, (Mark, TextMark)):
                    out.append(item.data())
        return out

    def model(self) -> dict:
        changed = {k: v for k, v in self.fields.items()
                   if any(f["name"] == k and f.get("value", "") != v for f in self.field_list)}
        return {"items": self.model_items(), "removed": [dict(d) for d in self.removed],
                "restyled": [dict(d) for d in self.restyled], "fields": changed,
                "order": list(self.order), "turns": {str(k): v for k, v in sorted(self.turns.items()) if v % 360}}

    def end_text_edits(self):
        for page in self.pages:
            for item in page.childItems():
                if isinstance(item, TextMark) and item.editing():
                    item.end_edit()
                elif isinstance(item, FieldItem) and item.typing is not None:
                    item.stop_typing()

    def commit(self):
        if self.saved is None:
            return
        now = self.model()
        if now != self.current:
            self.history = (self.history + [self.current])[-200:]
            self.future = []
            self.current = now
        self._refresh()

    def undo(self):
        self.end_text_edits()
        if not self.history:
            return
        self.future.append(self.current)
        self.current = self.history.pop()
        self._restore(self.current)

    def redo(self):
        self.end_text_edits()
        if not self.future:
            return
        self.history.append(self.current)
        self.current = self.future.pop()
        self._restore(self.current)

    def _restore(self, state: dict):
        self.scene.clearSelection()
        for page in self.pages:
            for item in list(page.childItems()):
                if isinstance(item, (Mark, TextMark)):
                    self.scene.removeItem(item)
        self.kept.clear()
        for d in state["items"]:
            if 0 <= int(d.get("page", -1)) < len(self.pages):
                self.add_item(d, commit=False)
        self.removed = [dict(d) for d in state["removed"]]
        self.restyled = [dict(d) for d in state.get("restyled", [])]
        self.fields = {f["name"]: f.get("value", "") for f in self.field_list}
        self.fields.update(state["fields"])
        for item in self.field_items:
            item.update()
        order = state.get("order") or list(range(len(self.pages)))
        turns = {int(k): v for k, v in (state.get("turns") or {}).items()}
        if order != self.order or turns != self.turns:
            self.order, self.turns = list(order), turns
            self._place_pages()
            self._fill_thumbs()
        for page in self.pages:
            self._pieces_shown(page)
        self._ask_pages()
        if self.find_bar.isVisible():
            self.find_all()
        self._refresh()

    # ---- saving

    def save(self, then_close: bool = False):
        self.end_text_edits()
        if self.src is None or self.saved is None or self._saving is not None:
            return
        state = self.model()
        if state == self.saved:
            if then_close:
                self.close_editor(ask=False)
            return
        edits = [d for d in state["items"] if not (d.get("kind") == "text" and not d.get("text", "").strip()
                                                    and not d.get("replaces"))]
        edits += [dict(d, kind="remove") for d in state["removed"]]
        edits += [dict(d, kind="restyle") for d in state.get("restyled", [])]
        params = {"edits": edits, "fields": state["fields"], "password": self.password}
        if state["order"] != list(range(len(self.pages))):
            params["order"] = state["order"]
        if state["turns"]:
            params["turns"] = state["turns"]
        blanks = [[round(p.w, 2), round(p.h, 2)] for p in self.pages[self.doc_pages:] if p.blank]
        if blanks:
            params["blanks"] = blanks
        src = self.src
        self._saving = state
        self.save_btn.setEnabled(False)

        def done(ok: bool):
            # (the changes count as saved only once the new PDF was really written: a save that failed, was
            # stopped or had no room leaves them unsaved, Save still on and closing still asks)
            if not work.alive(self):
                return
            self._saving = None
            if ok and self.src == src:
                self.saved = state
                if then_close:
                    self.close_editor(ask=False)
                    return
            self._refresh()
        self.c.start_job(pdfedit.EDIT, [src], params, then=done)


# ----------------------------------------------------------------------------- small windows

def _note_dialog_class():
    from .dialogs import Base

    class _NoteDialog(Base):
        def __init__(self, text, icon, dark):
            super().__init__(tr("Note"), tr("Shown when the mouse is on it, in most PDF apps."), icon, dark)
            self.box = QPlainTextEdit()
            self.box.setPlainText(text)
            self.box.setMinimumSize(360, 140)
            self.lay.addWidget(self.box)
            self.buttons(tr("Save note"))
            self.box.setFocus()

        def text(self) -> str:
            return self.box.toPlainText()
    return _NoteDialog


def _sign_dialog_class():
    from .dialogs import Base
    from .dialogs2 import SignaturePad

    class _SignPadDialog(Base):
        def __init__(self, kept: Path, icon, dark):
            super().__init__(tr("Sign"), tr("Draw your signature, or use a picture of it."), icon, dark)
            self.kept = kept
            self.pad = SignaturePad()
            self.pad.changed = self._changed
            self.lay.addWidget(self.pad)
            row = QHBoxLayout()
            clear = QPushButton(tr("Clear"))
            clear.clicked.connect(lambda: (self.pad.clear(), self._changed()))
            pick = QPushButton(tr("Use a picture of it…"))
            pick.clicked.connect(self._pick)
            row.addWidget(clear)
            row.addWidget(pick)
            row.addStretch()
            self.lay.addLayout(row)
            self.remember = QCheckBox(tr("Remember my signature on this PC"))
            self.lay.addWidget(self.remember)
            if kept.exists():
                self.pad.picture = QImage(str(kept))
                self.remember.setChecked(True)
            self.buttons(tr("Add signature"))
            self._changed()

        def _changed(self):
            if hasattr(self, "ok"):
                self.ok.setEnabled(not self.pad.empty())

        def _pick(self):
            name, _ = QFileDialog.getOpenFileName(self, tr("Use a picture of it…").rstrip("…"), "",
                                                  tr("Images") + " (*.png *.jpg *.jpeg *.webp *.bmp)")
            if name:
                img = QImage(name)
                if not img.isNull():
                    from .pdfstamp import signature_picture
                    try:  # (a photo of a signature on paper: only the ink is kept)
                        im = signature_picture(Path(name))
                        out = Path(tempfile.mkdtemp(prefix="corefold-pdfedit-")) / "signature.png"
                        im.save(out)
                        img = QImage(str(out))
                    except Exception:
                        log.debug("couldn't cut the signature out", exc_info=True)
                    self.pad.strokes, self.pad.picture = [], img
                    self.pad.update()
                    self._changed()

        def save_to(self, out: Path):
            if self.pad.picture.isNull():
                self.pad.save(out)
                from .pdfstamp import signature_picture
                try:
                    signature_picture(out).save(out)  # (cut to the ink)
                except Exception:
                    log.debug("couldn't cut the signature", exc_info=True)
            else:
                self.pad.picture.save(str(out), "PNG")
            try:
                if self.remember.isChecked():
                    self.kept.parent.mkdir(parents=True, exist_ok=True)
                    QImage(str(out)).save(str(self.kept), "PNG")
                else:
                    self.kept.unlink(missing_ok=True)
            except OSError:
                pass
    return _SignPadDialog


def NoteDialog(text, icon, dark):  # (the dialog classes are made when first needed)
    return _note_dialog_class()(text, icon, dark)


def SignPadDialog(kept, icon, dark):
    return _sign_dialog_class()(kept, icon, dark)


def _find_cover_dialog_class():
    from .dialogs import Base
    from .pdfreader import PRIVATE_KINDS

    class _FindCoverDialog(Base):
        def __init__(self, icon, dark, kept):
            super().__init__(tr("Find and cover"), tr("What's found is covered on every page. You can look the "
                                                      "covers over, and take any away, before you save."), icon,
                             dark)
            kept = set(kept)
            self.boxes = {}
            for key, words in PRIVATE_KINDS.items():
                box = QCheckBox(tr(words))
                box.setChecked(key in kept)
                self.lay.addWidget(box)
                self.boxes[key] = box
            self.lay.addWidget(QLabel(tr("Your own words, with commas between them:")))
            self.own = QLineEdit()
            self.own.setPlaceholderText(tr("For example: a name, an address"))
            self.lay.addWidget(self.own)
            note = QLabel(tr("Only words the PDF really has are found: scanned pages need Make searchable first."))
            note.setObjectName("muted")
            note.setWordWrap(True)
            self.lay.addWidget(note)
            self.buttons(tr("Find and cover"))

        def kinds(self) -> list[str]:
            return [k for k, b in self.boxes.items() if b.isChecked()]

        def words(self) -> list[str]:
            return [w.strip() for w in self.own.text().replace("،", ",").split(",") if w.strip()]
    return _FindCoverDialog


def FindCoverDialog(icon, dark, kept):
    return _find_cover_dialog_class()(icon, dark, kept)
