"""Corefold PDF's page editor: the pages of one PDF, big, to write on. Type new text anywhere or change
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

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFontDatabase, QFontMetricsF, QImage, QKeySequence, QPainter,
                           QPainterPath, QPen, QPixmap, QShortcut, QTextCursor, QTransform)
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QFileDialog, QFrame, QGraphicsItem,
                               QGraphicsObject, QGraphicsScene, QGraphicsTextItem, QGraphicsView, QHBoxLayout,
                               QLabel, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
                               QStyleOptionGraphicsItem, QStyle, QVBoxLayout, QWidget)

from . import icons, pdfedit, pdftools, work
from .atomic import tokens
from .atomic.widgets import Dropdown, FlowLayout, IconButton
from .core import IMAGE_EXT, short_name
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
# the tools: (key, words on the button, icon). Some have kinds, picked in the bar under them.
TOOLS = [("select", "Select", "cursor"), ("edit", "Edit text", "text_edit_style"), ("text", "Add text", "textbox"),
         ("image", "Picture", "image_add"), ("sign", "Sign", "signature"), ("mark", "Highlight", "highlight"),
         ("draw", "Draw", "pen"), ("shape", "Shapes", "shapes"), ("cover", "Cover", "eraser"),
         ("note", "Note", "note")]
MARKS = [("highlight", "Highlight", "highlight"), ("underline", "Underline", "text_underline"),
         ("strike", "Cross out", "text_strikethrough")]
SHAPES = [("rect", "Rectangle", "rectangle_landscape"), ("ellipse", "Circle", "circle"),
          ("line", "Line", "line_horizontal_1"), ("arrow", "Arrow", "arrow_up_right")]
MARK_KEYS = {k for k, _, _ in MARKS}
SHAPE_KEYS = {k for k, _, _ in SHAPES}
HINTS = {"select": "Click something to select it and drag it to move it. Double-click text to change it.",
         "edit": "Click a line of the PDF's own text to change it. Lines that can be changed have a box around them.",
         "text": "Click where the text should go, then type.",
         "highlight": "Drag across words to highlight them.",
         "underline": "Drag across words to underline them.",
         "strike": "Drag across words to cross them out.",
         "draw": "Draw with the mouse or a pen.",
         "shape": "Drag to draw the shape.",
         "cover": "Drag over what should be hidden. What's under it is taken out of the file, not just hidden.",
         "note": "Click where the note should go."}
WIDTHS = {"Thin": 1.0, "Medium": 2.5, "Thick": 5.0}
INK = ["#000000", "#1f3a93", "#d32f2f", "#2e7d32", "#f57c00", "#7b1fa2", "#ffffff"]
PENS = ["#ffd400", "#7cf27c", "#ff8ad8", "#7ec8ff", "#ffb35c"]
FIELD_TINT = QColor(70, 130, 255, 38)


# ----------------------------------------------------------------------------- drawing pages off the window's thread

class _Signals(QObject):
    opened = Signal(object)        # (sizes, fields) or an Exception
    image = Signal(int, float, object, object)  # page, scale, hidden pieces, QImage
    layout = Signal(int, object)


class Renderer:
    """One thread that draws pages and reads what's on them: the newest wishes first, old ones dropped."""

    def __init__(self, src: Path, password: str):
        self.src, self.password = src, password
        self.sig = _Signals()
        self.cv = threading.Condition()
        self.renders: dict[int, tuple[float, tuple]] = {}
        self.layouts: list[int] = []
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

    def close(self):
        with self.cv:
            self.stopped = True
            self.renders.clear()
            self.layouts.clear()
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
            while True:
                with self.cv:
                    while not self.stopped and not self.renders and not self.layouts:
                        self.cv.wait()
                    if self.stopped:
                        return
                    if self.renders:
                        i = next(iter(self.renders))
                        scale, hide = self.renders.pop(i)
                        job = ("render", i, scale, hide)
                    else:
                        job = ("layout", self.layouts.pop(0))
                try:
                    if job[0] == "render":
                        _, i, scale, hide = job
                        with pdftools.PDFIUM:
                            data, w, h, stride = pdfedit.render(doc, i, scale, hide)
                        img = QImage(data, w, h, stride, QImage.Format.Format_RGBX8888).copy()
                        self.sig.image.emit(i, scale, hide, img)
                    else:
                        with pdftools.PDFIUM:
                            layout = pdfedit.read_layout(doc, job[1])
                        self.sig.layout.emit(job[1], layout)
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
        self.pm_key: tuple | None = None       # (scale, hidden pieces) the picture was drawn with
        self.thumb: QPixmap | None = None
        self.layout: dict | None = None
        self.asked_layout = False
        self.pieces: list[OldPiece] = []
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
        self.parentItem().editor.commit()


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
        if self.kind == "cover":
            p.fillRect(rect, QColor(self.d.get("color", "#ffffff")))
            if self.d.get("color", "#ffffff").lower() == "#ffffff" and not self.isSelected():
                pen = QPen(QColor(0, 0, 0, 30), 1, Qt.DotLine)
                pen.setCosmetic(True)
                p.setPen(pen)
                p.drawRect(rect)
        elif self.pixmap is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawPixmap(rect, self.pixmap, QRectF(self.pixmap.rect()))
        self._outline(p)

    def resize_to(self, pt: QPointF):
        w, h = max(6.0, pt.x()), max(6.0, pt.y())
        if self.kind in ("image", "move"):  # (pictures keep their shape)
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
        pdfedit.draw_mark(p, dict(self.d, points=[self.a.x(), self.a.y(), self.b.x(), self.b.y()]))
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
        if d.get("rtl"):
            self.anchor = self.x() + self.boundingRect().width()
        self.document().contentsChanged.connect(self._grown)

    def restyle(self):
        pdfedit.text_document(self.d, self.document())
        self.setDefaultTextColor(QColor(self.d.get("color", "#000000")))
        self.setFont(pdfedit.font_for(self.d))
        self._grown()

    def _grown(self):
        doc = self.document()
        doc.setTextWidth(-1)
        doc.setTextWidth(max(4.0, doc.idealWidth()))
        if self.anchor is not None:
            self.setX(self.anchor - self.boundingRect().width())

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
        if self.text().strip() and self.d.get("replaces") and self.text() == self.d.get("was"):
            # a line opened but not changed: the PDF's own line stays as it was
            self.editor.drop_item(self)
            return
        self.editor.commit()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        # (a menu opening, or another window coming to the front, doesn't stop the typing)
        if e.reason() not in (Qt.PopupFocusReason, Qt.ActiveWindowFocusReason):
            QTimer.singleShot(0, self.end_edit)

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
    """A line of text or a picture that's already in the PDF: shows a box when it can be changed, and is
    turned into a text box (to change the words) or moved when it's clicked."""

    def __init__(self, editor: "PdfEditor", page: PageItem, kind: str, d: dict):
        super().__init__(page)
        self.editor, self.page, self.kind, self.d = editor, page, kind, d
        self.objs = tuple(d["objs"]) if kind == "line" else (d["obj"],)
        self.rect = QRectF(*d["rect"])
        self.setPos(self.rect.topLeft())
        self.rect.moveTo(0, 0)
        self.setZValue(1)
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.hover = False
        self.press: QPointF | None = None
        self.moving: BoxMark | None = None
        self.setCursor(Qt.IBeamCursor if kind == "line" else Qt.SizeAllCursor)

    def boundingRect(self) -> QRectF:
        return self.rect.adjusted(-2, -2, 2, 2)

    def paint(self, p, option, widget=None):
        tool = self.editor.tool
        if self.isSelected():
            p.setPen(_selection_pen())
        elif self.hover or (tool == "edit" and self.kind == "line"):
            pen = QPen(tokens.alpha(tokens.current().accent, 230 if self.hover else 110), 1,
                       Qt.SolidLine if self.hover else Qt.DashLine)
            pen.setCosmetic(True)
            p.setPen(pen)
        else:
            return
        p.setBrush(tokens.alpha(tokens.current().accent, 26) if self.hover else Qt.NoBrush)
        p.drawRect(self.rect.adjusted(-1, -1, 1, 1))

    def hoverEnterEvent(self, e):
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self.hover = False
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
        delta = e.scenePos() - self.press
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
            p.drawText(rect.adjusted(2, 0, -3, 0), int(Qt.AlignVCenter | Qt.AlignRight | Qt.AlignAbsolute), "▾")

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
        elif tool == "cover":
            r = QRectF(self.drag["start"], pt).normalized()
            if item is not None:
                ed.drop_item(item, commit=False)
            self.drag["item"] = ed.add_item({"kind": "cover", "page": page.index, "x": r.x(), "y": r.y(),
                                             "w": max(r.width(), 1), "h": max(r.height(), 1),
                                             "color": ed.style["cover"]["color"]}, commit=False)
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
        if drag["tool"] in ("rect", "ellipse", "line", "arrow", "cover"):
            r = item.boundingRect()
            if drag["tool"] == "cover" and (item.w < 3 or item.h < 3):
                ed.drop_item(item)
                return
            if drag["tool"] != "cover" and (item.a - item.b).manhattanLength() < 3:
                ed.drop_item(item)
                return
            del r
        ed.commit()

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
        for keys, fn in ((QKeySequence.Undo, ed.undo), (QKeySequence.Redo, ed.redo), (QKeySequence("Ctrl+Y"), ed.redo)):
            if e.matches(keys) if isinstance(keys, QKeySequence.StandardKey) else \
                    e.keyCombination().toCombined() == keys[0].toCombined():
                fn()
                return
        steps = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
        if e.key() in steps and ed.selected_items():
            dx, dy = steps[e.key()]
            far = 10 if e.modifiers() & Qt.ShiftModifier else 1
            for item in ed.selected_items():
                item.moveBy(dx * far, dy * far)
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


class PdfEditor(QWidget):
    """The editor, shown on Corefold PDF's page instead of the cards."""

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
        self.history: list[dict] = []
        self.future: list[dict] = []
        self.current: dict = {"items": [], "removed": [], "fields": {}}
        self.saved: dict | None = None
        self.zoom = 1.0
        self.fit = True                     # the zoom follows the window's width
        self.tool = "select"
        self.mark_kind = "highlight"
        self.shape_kind = "rect"
        self.dragging_items = False
        self.thumbs: list[PageItem] = []
        self.kept: set = set()              # every added thing on the pages
        self.field_menu = None              # (a form's list, while it's open)
        self.temp = None
        self.style = {"text": {"font": "Arial", "size": 12.0, "color": "#000000", "bold": False, "italic": False},
                      "highlight": {"color": PENS[0]}, "underline": {"color": "#d32f2f"},
                      "strike": {"color": "#d32f2f"}, "draw": {"color": "#1f3a93", "width": 2.5},
                      "shape": {"color": "#d32f2f", "width": 2.5}, "cover": {"color": "#ffffff"}}
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
        top = QHBoxLayout()
        top.setSpacing(6)
        self.back_btn = IconButton(tr("Close the editor"))
        self.back_btn.setIcon(icons.icon("arrow_right" if self.layoutDirection() == Qt.RightToLeft else "arrow_left",
                                         muted))
        self.back_btn.clicked.connect(lambda: self.close_editor())
        top.addWidget(self.back_btn)
        self.name = QLabel()
        self.name.setObjectName("cardTitle")
        self.name.setMinimumWidth(40)
        top.addWidget(self.name, 1)
        self.undo_btn = IconButton(tr("Undo (Ctrl + Z)"))
        self.undo_btn.setIcon(icons.icon("arrow_undo", muted, tokens.alpha(QColor(muted), 90)))
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn = IconButton(tr("Redo (Ctrl + Y)"))
        self.redo_btn.setIcon(icons.icon("arrow_redo", muted, tokens.alpha(QColor(muted), 90)))
        self.redo_btn.clicked.connect(self.redo)
        top.addWidget(self.undo_btn)
        top.addWidget(self.redo_btn)
        top.addSpacing(6)
        out_btn = IconButton(tr("Zoom out (Ctrl + -)"))
        out_btn.setIcon(icons.icon("zoom_out", muted))
        out_btn.clicked.connect(lambda: self.zoom_step(-1))
        self.zoom_btn = QPushButton("100%")
        self.zoom_btn.setObjectName("flat")
        self.zoom_btn.setCursor(Qt.PointingHandCursor)
        self.zoom_btn.setToolTip(tr("Fit the width (Ctrl + 0)"))
        self.zoom_btn.setMinimumWidth(52)
        self.zoom_btn.clicked.connect(self.fit_width)
        in_btn = IconButton(tr("Zoom in (Ctrl + +)"))
        in_btn.setIcon(icons.icon("zoom_in", muted))
        in_btn.clicked.connect(lambda: self.zoom_step(1))
        for b in (out_btn, self.zoom_btn, in_btn):
            top.addWidget(b)
        top.addSpacing(6)
        self.save_btn = QPushButton(tr("Save as a new PDF"))
        self.save_btn.setObjectName("primary")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setToolTip(tr("Save as a new PDF (Ctrl + S)"))
        self.save_btn.clicked.connect(self.save)
        top.addWidget(self.save_btn)
        v.addLayout(top)

        # the tools
        tools_box = QWidget()
        flow = FlowLayout(tools_box, 6)
        strip = QFrame()
        strip.setObjectName("segmented")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.tool_group = QButtonGroup(self)
        self.tools_short = False
        self.tool_group.buttonToggled.connect(lambda *_: self.tools_short and self._tool_names())
        self.tool_btns: dict[str, QPushButton] = {}
        text_color = self.c.theme.text
        for key, words, icon_name in TOOLS:
            b = QPushButton(" " + tr(words))
            b.setToolTip(tr(words))
            b.setObjectName("segment")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIcon(icons.icon(icon_name, text_color))
            b.setIconSize(QSize(16, 16))
            b.clicked.connect(lambda _=False, k=key: self.set_tool(k))
            self.tool_group.addButton(b)
            self.tool_btns[key] = b
            sl.addWidget(b)
        self.tool_btns["select"].setChecked(True)
        self.tools_wide = strip.sizeHint().width()
        flow.addWidget(strip)
        v.addWidget(tools_box)

        # what the tool (or what's selected) looks like
        self.props = QWidget()
        self.props.setObjectName("pdfEditProps")
        pl = FlowLayout(self.props, 6)
        self.kind_strip = QFrame()
        self.kind_strip.setObjectName("segmented")
        kl = QHBoxLayout(self.kind_strip)
        kl.setContentsMargins(3, 3, 3, 3)
        kl.setSpacing(2)
        self.kind_group = QButtonGroup(self)
        self.kind_btns: dict[str, QPushButton] = {}
        for key, words, icon_name in MARKS + SHAPES:
            b = QPushButton(" " + tr(words))
            b.setToolTip(tr(words))
            b.setObjectName("segment")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIcon(icons.icon(icon_name, text_color))
            b.setIconSize(QSize(16, 16))
            b.clicked.connect(lambda _=False, k=key: self.set_kind(k))
            self.kind_group.addButton(b)
            self.kind_btns[key] = b
            kl.addWidget(b)
        pl.addWidget(self.kind_strip)
        self.font_box = Dropdown()
        self.font_box.setToolTip(tr("Font"))
        self.font_box.setMinimumWidth(150)
        self.font_box.activated.connect(lambda _i: self.set_style(font=self.font_box.currentText()))
        pl.addWidget(self.font_box)
        self.size_box = QSpinBox()
        self.size_box.setRange(4, 144)
        self.size_box.setToolTip(tr("Text size"))
        self.size_box.setSuffix(" pt")
        self.size_box.valueChanged.connect(lambda n: None if self._filling else self.set_style(size=float(n)))
        pl.addWidget(self.size_box)
        self.bold_btn = IconButton(tr("Bold"))
        self.bold_btn.setIcon(icons.icon("text_bold", text_color))
        self.bold_btn.setCheckable(True)
        self.bold_btn.clicked.connect(lambda on: self.set_style(bold=bool(on)))
        self.italic_btn = IconButton(tr("Italic"))
        self.italic_btn.setIcon(icons.icon("text_italic", text_color))
        self.italic_btn.setCheckable(True)
        self.italic_btn.clicked.connect(lambda on: self.set_style(italic=bool(on)))
        pl.addWidget(self.bold_btn)
        pl.addWidget(self.italic_btn)
        self.width_box = Dropdown()
        self.width_box.setToolTip(tr("Line width"))
        for words, w in WIDTHS.items():
            self.width_box.addItem(tr(words), w)
        self.width_box.activated.connect(lambda _i: self.set_style(width=float(self.width_box.currentData())))
        pl.addWidget(self.width_box)
        self.colors = QWidget()
        cl = QHBoxLayout(self.colors)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(2)
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
        pl.addWidget(self.colors)
        self.delete_btn = QPushButton(tr("Delete"))
        self.delete_btn.setIcon(icons.icon("delete", text_color))
        self.delete_btn.setCursor(Qt.PointingHandCursor)
        self.delete_btn.setToolTip(tr("Delete (Del)"))
        self.delete_btn.clicked.connect(self.delete_selected)
        pl.addWidget(self.delete_btn)
        v.addWidget(self.props)

        self.view = EditorView(self)
        v.addWidget(self.view, 1)
        foot = QHBoxLayout()
        self.where = QLabel()
        self.where.setObjectName("muted")
        foot.addWidget(self.where)
        self.hint = QLabel()
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        foot.addWidget(self.hint, 1)
        v.addLayout(foot)
        self._filling = False
        taken = set()  # (one shortcut per key: two on the same key (Windows' Redo is Ctrl + Y too) do nothing)
        for keys, fn in ((QKeySequence.Undo, self.undo), (QKeySequence.Redo, self.redo),
                         ("Ctrl+Y", self.redo), (QKeySequence.Save, self.save),
                         ("Ctrl+V", self.paste), (QKeySequence.ZoomIn, lambda: self.zoom_step(1)),
                         ("Ctrl+=", lambda: self.zoom_step(1)),
                         (QKeySequence.ZoomOut, lambda: self.zoom_step(-1)), ("Ctrl+0", self.fit_width)):
            for seq in QKeySequence.keyBindings(keys) if not isinstance(keys, str) else [QKeySequence(keys)]:
                if seq.isEmpty() or seq.toString() in taken:
                    continue
                taken.add(seq.toString())
                sc = QShortcut(seq, self)
                sc.setContext(Qt.WidgetWithChildrenShortcut)
                sc.activated.connect(fn)
        self._fill_fonts()
        self._refresh()

    def _fill_fonts(self):
        have = set(QFontDatabase.families())
        self.fonts = [f for f in pdfedit.FONTS if f in have] or sorted(have)[:12] or ["Arial"]
        self.font_box.clear()
        for f in self.fonts:
            self.font_box.addItem(f)
        if self.style["text"]["font"] not in self.fonts:
            self.style["text"]["font"] = self.fonts[0]

    # ---- opening and closing

    def open(self, src: Path, password: str = ""):
        self.close_renderer()
        self.pages, self.offsets, self.thumbs = [], [], []
        self.scene.clear()
        self.kept.clear()
        self.fields, self.field_list, self.field_items, self.removed = {}, [], [], []
        self.history, self.future = [], []
        self.current = {"items": [], "removed": [], "fields": {}}
        self.saved = None
        self.src, self.password = src, password
        self.name.setText(short_name(src.name, 60))
        self.name.setToolTip(str(src))
        self.where.setText(tr("Opening…"))
        self.fit = True
        self.set_tool("select")
        self.renderer = Renderer(src, password)
        self.renderer.sig.opened.connect(self._opened)
        self.renderer.sig.image.connect(self._image)
        self.renderer.sig.layout.connect(self._layout)
        self._tidy_temp()

    def _opened(self, result):
        if not work.alive(self):
            return
        if isinstance(result, Exception):
            self.c.tell(tr("Corefold PDF"), tr("{name} couldn't be read. It may be damaged.",
                                                 name=short_name(self.src.name)))
            self.close_editor(ask=False)
            return
        sizes, fields = result
        y = 0.0
        for i, (w, h) in enumerate(sizes):
            page = PageItem(i, w, h)
            page.setPos(-w / 2, y)
            self.scene.addItem(page)
            self.pages.append(page)
            self.offsets.append(y)
            y += h + GAP
        width = max((w for w, _ in sizes), default=600)
        self.scene.setSceneRect(QRectF(-width / 2 - 40, -GAP, width + 80, y + GAP))
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
            for s in (self.renderer.sig.opened, self.renderer.sig.image, self.renderer.sig.layout):
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
            box.exec()
            chosen = box.clickedButton()
            box.deleteLater()
            if chosen is save:
                self.save()
            elif chosen is not close:
                return False
        self.close_renderer()
        self.pages, self.offsets, self.thumbs, self.field_items = [], [], [], []
        self.scene.clear()
        self.kept.clear()
        self.src = None
        self.closed.emit()
        return True

    def go_back(self) -> bool:
        """Esc: stops typing, then goes back to the Select tool, then closes the editor."""
        if self.scene.focusItem() is not None:
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
        for i in (k, k + 1):
            if 0 <= i < len(self.pages):
                page = self.pages[i]
                if page.sceneBoundingRect().adjusted(3, 3, -3, -3).contains(sp):
                    return page
        return None

    def view_moved(self):
        self.visible_timer.start()
        self._where()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        short = self.tools_wide > self.width() - 8  # (no room for all the tools' names: just the chosen one's)
        if short != self.tools_short:
            self.tools_short = short
            self._tool_names()

    def _tool_names(self):
        for key, words, _ in TOOLS:
            b = self.tool_btns[key]
            b.setText(" " + tr(words) if not self.tools_short or b.isChecked() else "")

    def view_resized(self):
        if self.fit and self.pages:
            self.fit_width()
        self.visible_timer.start()

    def _in_sight(self) -> list[int]:
        import bisect
        if not self.pages:
            return []
        r = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        first = max(0, bisect.bisect_right(self.offsets, r.top()) - 1)
        out = []
        for i in range(first, len(self.pages)):
            if self.offsets[i] > r.bottom():
                break
            out.append(i)
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
            if d.get("kind") == "text":
                out.update(d.get("replaces") or ())
            elif d.get("kind") == "move":
                out.update(d.get("objs") or ())
        for d in self.removed:
            if d["page"] == index:
                out.update(d["objs"])
        return tuple(sorted(out))

    def _near(self, sight: list[int]) -> list[int]:
        return list(range(max(0, sight[0] - KEEP_NEAR), min(len(self.pages), sight[-1] + KEEP_NEAR + 1)))

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
        for i in sorted(near, key=lambda k: (k not in sight, abs(k - middle))):
            page = self.pages[i]
            s = min(scale, math.sqrt(MAX_PIXELS / max(1.0, page.w * page.h)))
            key = (round(s, 3), self.hidden(i))
            if page.pm_key != key:
                wishes.append((i, key[0], key[1]))
        self.renderer.want(wishes)
        self.renderer.want_layout([i for i in sight if not self.pages[i].asked_layout])
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
        for d in layout.get("lines", []):
            page.pieces.append(OldPiece(self, page, "line", d))
        for d in layout.get("images", []):
            page.pieces.append(OldPiece(self, page, "image", d))
        self._pieces_shown(page)
        if self.tool == "edit":
            self._refresh()

    def _pieces_shown(self, page: PageItem):
        hidden = set(self.hidden(page.index))
        for piece in page.pieces:
            piece.setVisible(not hidden.intersection(piece.objs))

    def _where(self):
        sight = self._in_sight()
        if sight and self.pages:
            r = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
            best = max(sight, key=lambda i: QRectF(self.pages[i].sceneBoundingRect()).intersected(r).height())
            self.where.setText(tr("Page {number} of {count}", number=best + 1, count=len(self.pages)))

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
        width = max(p.w for p in self.pages)
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
                  "note": Qt.PointingHandCursor, "mark": Qt.IBeamCursor}.get(key)
        if cursor is None:
            self.view.viewport().unsetCursor()
        else:
            self.view.viewport().setCursor(cursor)
        for page in self.pages:
            for piece in page.pieces:
                piece.setAcceptedMouseButtons(Qt.LeftButton if key in ("select", "edit") else Qt.NoButton)
                piece.update()
        if key == "edit":
            self.view.setDragMode(QGraphicsView.NoDrag)
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
        else:
            self.shape_kind = key
        self._refresh()

    def style_key(self) -> str | None:
        """Which look the bar shows: the selected thing's, else the tool's."""
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
        return kind

    def selected_items(self) -> list:
        return [i for i in self.scene.selectedItems() if isinstance(i, (Mark, TextMark, OldPiece))]

    def selection_changed(self):
        if not self._filling:
            self._refresh()

    def _refresh(self):
        """Shows what fits the tool or what's selected."""
        self._filling = True
        try:
            key = self.style_key()
            tool = self.tool
            items = self.selected_items()
            st = self.style.get(key) if key else None
            if items and key in ("text",) and isinstance(items[0], TextMark):
                st = items[0].d
            elif items and key and isinstance(items[0], Mark):
                st = items[0].d
            self.kind_strip.setVisible(tool in ("mark", "shape") and not items)
            for k, b in self.kind_btns.items():
                b.setVisible((tool == "mark" and k in MARK_KEYS) or (tool == "shape" and k in SHAPE_KEYS))
                b.setChecked(k in (self.mark_kind, self.shape_kind))
            text_on = key == "text" and st is not None
            for w in (self.font_box, self.size_box, self.bold_btn, self.italic_btn):
                w.setVisible(text_on)
            if text_on:
                font = st.get("font", "Arial")
                if self.font_box.findText(font) < 0:
                    self.font_box.addItem(font)
                self.font_box.setCurrentText(font)
                self.size_box.setValue(round(float(st.get("size", 12))))
                self.bold_btn.setChecked(bool(st.get("bold")))
                self.italic_btn.setChecked(bool(st.get("italic")))
            width_on = key in ("draw", "shape") and st is not None
            self.width_box.setVisible(width_on)
            if width_on:
                w = float(st.get("width", 2.5))
                best = min(range(self.width_box.count()), key=lambda k: abs(self.width_box.itemData(k) - w))
                self.width_box.setCurrentIndex(best)
            colors_on = st is not None and "color" in st and key != "move"
            self.colors.setVisible(colors_on)
            if colors_on:
                palette = PENS if key == "highlight" else INK
                for b, color in zip(self.color_btns, palette + [None] * 7):
                    b.setVisible(color is not None)
                    if color is None:
                        continue
                    if b.property("color") != color:
                        b.setIcon(_swatch(color))
                        b.setToolTip(color)
                        b.setProperty("color", color)
                    b.setChecked(QColor(color) == QColor(st.get("color", "")))
            self.delete_btn.setVisible(bool(items))
            self.props.setVisible(any(w.isVisibleTo(self.props) for w in (
                self.kind_strip, self.font_box, self.width_box, self.colors, self.delete_btn)))
            hint = HINTS.get(self.active_tool() if tool in ("mark", "shape") else tool, "")
            if tool == "edit" and self.pages and not any(p.pieces for p in self.pages if p.layout) and \
                    any(p.layout for p in self.pages):
                hint = tr("The text on these pages can't be changed directly (it may be a scan). "
                          "Use Cover, then Add text over it.")
            elif self.field_items and tool in ("select", "text", "edit"):
                hint = tr("This PDF has a form: click its boxes to fill them in.") + "  " + tr(hint)
            else:
                hint = tr(hint)
            self.hint.setText(hint)
            self.undo_btn.setEnabled(bool(self.history))
            self.redo_btn.setEnabled(bool(self.future))
            self.save_btn.setEnabled(self.saved is not None and self.current != self.saved)
        finally:
            self._filling = False

    def set_style(self, **change):
        """The bar was used: the selected things change, and new ones will look like that."""
        if self._filling:
            return
        items = self.selected_items()
        focus = self.scene.focusItem()
        if isinstance(focus, TextMark) and focus not in items:
            items.append(focus)
        if items:
            for item in items:
                kind = self._kind_of(item)
                keys = {"text": ("font", "size", "color", "bold", "italic"), "draw": ("color", "width"),
                        "shape": ("color", "width")}.get(kind, ("color",))
                for k, val in change.items():
                    if k in keys and hasattr(item, "d"):
                        item.d[k] = val
                if isinstance(item, TextMark):
                    item.restyle()
                else:
                    item.update()
            self.commit()
        key = self.style_key() if not items else self._kind_of(items[0])
        if key in self.style:
            self.style[key].update({k: v for k, v in change.items()})
        self._refresh()

    def _pick_color(self):
        key = self.style_key()
        items = self.selected_items()
        now = items[0].d.get("color") if items and hasattr(items[0], "d") else \
            (self.style.get(key, {}).get("color") if key else "#000000")
        color = QColorDialog.getColor(QColor(now or "#000000"), self, tr("More colours…").rstrip("…"))
        if color.isValid():
            self.set_style(color=color.name())

    # ---- making things

    def add_item(self, d: dict, commit: bool = True, pixmap: QPixmap | None = None):
        page = self.pages[int(d["page"])]
        kind = d["kind"]
        if kind == "text":
            item = TextMark(self, page, d)
        elif kind in ("image", "cover", "move"):
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
        """A line of the PDF's own text becomes a text box with its words, ready to change."""
        page, d = piece.page, piece.d
        family = d["font"]
        if family not in set(QFontDatabase.families()):
            family = "Times New Roman" if d.get("serif") else "Arial"
        size = float(d["size"])
        st = {"font": family, "size": size, "color": d["color"], "bold": d["bold"], "italic": d["italic"]}
        fm = QFontMetricsF(pdfedit.font_for(st), pdfedit.layout_device())
        x, y, w, h = d["rect"]
        top = d["base"] - fm.ascent() - pdfedit.TEXT_MARGIN if d.get("base") else y
        item = self.add_item(dict(st, kind="text", page=page.index, text=d["text"], was=d["text"],
                                  x=x - pdfedit.TEXT_MARGIN, y=top, replaces=list(piece.objs),
                                  rtl=bool(d.get("rtl"))), commit=False)
        if d.get("rtl"):
            item.anchor = x + w + pdfedit.TEXT_MARGIN
            item._grown()
        piece.setVisible(False)
        self._ask_pages()
        self.scene.clearSelection()
        item.setSelected(True)
        item.start_edit(at)
        self.set_tool_quiet("select") if self.tool not in ("edit", "text") else None

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

    def new_note(self, page: PageItem, pt: QPointF):
        item = self.add_item({"kind": "note", "page": page.index, "x": pt.x() - 10, "y": pt.y() - 10, "text": ""},
                             commit=False)
        if not self.edit_note(item):
            self.drop_item(item, commit=False)

    def edit_note(self, item: NoteMark) -> bool:
        dlg = NoteDialog(item.d.get("text", ""), self.c.icon, self.c.theme.dark)
        try:
            if not dlg.exec():
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
        kinds = " ".join(f"*{ext}" for ext in sorted(IMAGE_EXT | {".jpeg", ".jfif"}))
        name, _ = QFileDialog.getOpenFileName(self, tr("Pick a picture"), str(Path.home()),
                                              f"{tr('Pictures')} ({kinds})")
        if name:
            self.add_picture(Path(name))

    def add_picture(self, path: Path, signature: bool = False):
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
            self.c.tell(tr("Corefold PDF"), tr("That picture couldn't be read."))
            return
        # its size as it would be printed (pictures without a size: 96 dpi)
        dpm = img.dotsPerMeterX() or 3780
        w = img.width() / (dpm * 0.0254) * 72
        h = img.height() / (dpm * 0.0254) * 72
        if signature:
            w, h = 160.0, 160.0 * img.height() / max(1, img.width())
        self._place_box({"kind": "image", "path": str(path)}, w, h)

    def sign(self):
        from .settings import CONFIG_DIR
        dlg = SignPadDialog(CONFIG_DIR / "signature.png", self.c.icon, self.c.theme.dark)
        try:
            if not dlg.exec():
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
                if p.suffix.lower() in IMAGE_EXT:
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
        return {"items": self.model_items(), "removed": [dict(d) for d in self.removed], "fields": changed}

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
        self.fields = {f["name"]: f.get("value", "") for f in self.field_list}
        self.fields.update(state["fields"])
        for item in self.field_items:
            item.update()
        for page in self.pages:
            self._pieces_shown(page)
        self._ask_pages()
        self._refresh()

    # ---- saving

    def save(self):
        self.end_text_edits()
        if self.src is None or self.saved is None:
            return
        state = self.model()
        if state == self.saved:
            return
        edits = [d for d in state["items"] if not (d.get("kind") == "text" and not d.get("text", "").strip()
                                                    and not d.get("replaces"))]
        edits += [dict(d, kind="remove") for d in state["removed"]]
        params = {"edits": edits, "fields": state["fields"], "password": self.password}
        self.c.start_job(pdfedit.EDIT, [self.src], params)
        self.saved = state
        self._refresh()


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
