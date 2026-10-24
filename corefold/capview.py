"""Corefold Capture's windows: the small editor a screenshot opens in (arrows, boxes, a marker, text,
blur, crop, picking the words in it), pinned pictures and the timer's countdown."""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QKeySequence, QPainter, QPainterPath, QPen, \
    QPolygonF, QShortcut, QTransform
from PySide6.QtWidgets import QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, \
    QPushButton, QVBoxLayout, QWidget

from . import icons
from .core import APP_NAME
from .i18n import tr
from .theme import round_menu
from .winutil import reveal_in_explorer, set_title_bar_dark

if TYPE_CHECKING:
    from .app import Controller

log = logging.getLogger("corefold")

TOOLS = [("select", "Select text"), ("arrow", "Arrow"), ("box", "Box"), ("marker", "Marker"), ("pen", "Pen"), ("text", "Text"),
         ("step", "Steps"), ("blur", "Blur"), ("crop", "Crop")]
COLORS = ["#e5484d", "#f5a524", "#30a46c", "#0090ff", "#111111", "#ffffff"]


class Shape:
    def __init__(self, kind: str, color: QColor, width: float, points: list[QPointF], text: str = ""):
        self.kind, self.color, self.width, self.points, self.text = kind, QColor(color), width, points, text

    def rect(self) -> QRectF:
        a, b = self.points[0], self.points[-1]
        return QRectF(a, b).normalized()


def draw_shape(p: QPainter, s: Shape, base: QImage | None = None):
    """Draws a mark in the picture's own pixels."""
    if len(s.points) < 1:
        return
    if s.kind == "blur" and base is not None:
        r = s.rect().toRect().intersected(base.rect())
        if r.width() > 1 and r.height() > 1:
            block = max(6, min(r.width(), r.height()) // 8, round(max(base.width(), base.height()) / 180))
            small = base.copy(r).scaled(max(1, r.width() // block), max(1, r.height() // block),
                                        Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            p.drawImage(r, small.scaled(r.size(), Qt.IgnoreAspectRatio, Qt.FastTransformation))
        return
    pen = QPen(s.color, s.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    if s.kind == "marker":
        c = QColor(s.color)
        c.setAlpha(110)
        p.setPen(QPen(c, s.width * 4, Qt.SolidLine, Qt.FlatCap, Qt.RoundJoin))
        path = QPainterPath(s.points[0])
        for pt in s.points[1:]:
            path.lineTo(pt)
        p.setCompositionMode(QPainter.CompositionMode_Multiply if s.color.lightness() < 200
                             else QPainter.CompositionMode_SourceOver)
        p.drawPath(path)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        return
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    if s.kind == "pen":
        path = QPainterPath(s.points[0])
        for pt in s.points[1:]:
            path.lineTo(pt)
        p.drawPath(path)
    elif s.kind == "box":
        p.drawRoundedRect(s.rect(), s.width, s.width)
    elif s.kind == "arrow":
        a, b = s.points[0], s.points[-1]
        dx, dy = b.x() - a.x(), b.y() - a.y()
        length = math.hypot(dx, dy)
        if length < 2:
            return
        head = max(10.0, s.width * 4.2)
        ux, uy = dx / length, dy / length
        back = QPointF(b.x() - ux * head, b.y() - uy * head)
        p.drawLine(a, QPointF(b.x() - ux * head * 0.6, b.y() - uy * head * 0.6))
        left = QPointF(back.x() - uy * head * 0.55, back.y() + ux * head * 0.55)
        right = QPointF(back.x() + uy * head * 0.55, back.y() - ux * head * 0.55)
        p.setBrush(s.color)
        p.setPen(QPen(s.color, 1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolygon(QPolygonF([b, left, right]))
    elif s.kind == "step" and s.text:  # a numbered circle: 1, 2, 3 ... where you click
        r = max(11.0, s.width * 3.2)
        c = s.points[0]
        p.setPen(QPen(QColor("#ffffff"), max(1.5, r / 7)))
        p.setBrush(s.color)
        p.drawEllipse(c, r, r)
        f = QFont("Segoe UI")
        f.setPixelSize(max(10, round(r * (1.15 if len(s.text) < 2 else 0.9))))
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(QColor("#111111") if s.color.lightness() > 170 else QColor("#ffffff"))
        p.drawText(QRectF(c.x() - r, c.y() - r, r * 2, r * 2), Qt.AlignCenter, s.text)
    elif s.kind == "text" and s.text:
        f = QFont("Segoe UI")
        f.setPixelSize(max(12, round(s.width * 6)))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        box = p.fontMetrics().boundingRect(QRect(0, 0, 4000, 4000), Qt.TextWordWrap, s.text)
        box.moveTopLeft(s.points[0].toPoint())
        pad = max(4, f.pixelSize() // 4)
        back = QColor("#ffffff") if s.color.lightness() < 140 else QColor("#111111")
        back.setAlpha(200)
        p.setPen(Qt.NoPen)
        p.setBrush(back)
        p.drawRoundedRect(QRectF(box).adjusted(-pad, -pad / 2, pad, pad / 2), pad, pad)
        p.setPen(s.color)
        p.drawText(box, Qt.TextWordWrap, s.text)


class TextLayer:
    """The words Windows read in the picture, to pick with the mouse like text in a document: drag over
    them (across lines too), double-click one word, triple-click a line. Right to left in Arabic."""

    def __init__(self, lines: list):
        self.lines = [ln for ln in lines if ln.words]
        self.words: list[tuple[int, object]] = []  # (line, word), in reading order
        self.first: list[int] = []                 # where each line starts in self.words
        self.to_pic: list[QTransform] = []         # level text -> the picture (for turned text)
        self.to_level: list[QTransform] = []
        self.bands: list[QRectF] = []              # each line's box, level
        for n, ln in enumerate(self.lines):
            self.first.append(len(self.words))
            self.words.extend((n, w) for w in ln.words)
            t = QTransform()
            if abs(ln.angle) > 0.05:
                t.translate(ln.cx, ln.cy)
                t.rotate(ln.angle)
                t.translate(-ln.cx, -ln.cy)
            self.to_pic.append(t)
            self.to_level.append(t.inverted()[0])
            band = QRectF()
            for w in ln.words:
                band = band.united(QRectF(w.x, w.y, w.w, w.h))
            self.bands.append(band)
        self.anchor: int | None = None
        self.focus: int | None = None
        self._holes: QPainterPath | None = None

    def __bool__(self) -> bool:
        return bool(self.words)

    # ---- which words
    def word_at(self, pt: QPointF, near_only: bool = False) -> int | None:
        """The word under (or nearest to) a point in the picture. near_only: None unless it's on or
        right next to a word."""
        best, best_d = None, None
        for n, band in enumerate(self.bands):
            q = self.to_level[n].map(pt)
            dy = max(band.top() - q.y(), 0.0, q.y() - band.bottom())
            dx = max(band.left() - q.x(), 0.0, q.x() - band.right())
            d = (dy, dx)
            if best_d is None or d < best_d:
                best, best_d = n, d
        if best is None:
            return None
        h = self.bands[best].height() or 1.0
        if near_only and (best_d[0] > h * 0.35 or best_d[1] > h * 0.8):
            return None
        q = self.to_level[best].map(pt)
        ln = self.lines[best]
        pick, pick_d = 0, None
        for i, w in enumerate(ln.words):
            d = max(w.x - q.x(), 0.0, q.x() - (w.x + w.w))
            if pick_d is None or d < pick_d:
                pick, pick_d = i, d
        if near_only and pick_d > h * 0.8:
            return None
        return self.first[best] + pick

    def picked(self) -> tuple[int, int] | None:
        if self.anchor is None or self.focus is None:
            return None
        return min(self.anchor, self.focus), max(self.anchor, self.focus)

    def pick(self, a: int | None, b: int | None = None):
        self.anchor, self.focus = a, (a if b is None else b)

    def pick_all(self):
        self.pick(0, len(self.words) - 1)

    def pick_line(self, index: int):
        n = self.words[index][0]
        self.pick(self.first[n], self.first[n] + len(self.lines[n].words) - 1)

    def text(self, span: tuple[int, int] | None = None) -> str:
        """The picked words (or all of them), a line of text for each line in the picture."""
        from .extras import join_words
        a, b = span or (0, len(self.words) - 1)
        rows: list[list] = []
        last = None
        for n, w in self.words[a:b + 1]:
            if n != last:
                rows.append([])
                last = n
            rows[-1].append(w)
        return "\n".join(join_words(r) for r in rows)

    # ---- drawing
    @staticmethod
    def _pad(r: QRectF) -> QRectF:
        h = r.height()
        return r.adjusted(-h * 0.14, -h * 0.16, h * 0.14, h * 0.16)

    def holes(self) -> QPainterPath:
        """Where the words are (lit up while the rest of the picture is a little darker)."""
        if self._holes is None:
            path = QPainterPath()
            for n, ln in enumerate(self.lines):
                for r in self._spans(n, ln.words):
                    box = QPainterPath()
                    r = self._pad(r)
                    box.addRoundedRect(r, r.height() * 0.2, r.height() * 0.2)
                    path.addPath(self.to_pic[n].map(box))
            self._holes = path.simplified()
        return self._holes

    @staticmethod
    def _spans(n: int, words: list, picked=None) -> list[QRectF]:
        """Words next to each other (in the picture, left to right) as one box: gaps between them
        are filled when both sides are picked (or, for the lit-up area, when the gap is small)."""
        geo = sorted(range(len(words)), key=lambda i: words[i].x)
        spans: list[QRectF] = []
        prev = None
        for i in geo:
            w = words[i]
            if picked is not None and i not in picked:
                prev = None
                continue
            r = QRectF(w.x, w.y, w.w, w.h)
            if spans and prev is not None and (picked is not None or r.left() - spans[-1].right() < w.h * 0.9):
                spans[-1] = spans[-1].united(r)
            else:
                spans.append(r)
            prev = i
        return spans

    def picked_boxes(self) -> list[tuple[int, QRectF]]:
        span = self.picked()
        if span is None:
            return []
        out = []
        for n, ln in enumerate(self.lines):
            lo, hi = max(span[0], self.first[n]), min(span[1], self.first[n] + len(ln.words) - 1)
            if lo > hi:
                continue
            chosen = {i - self.first[n] for i in range(lo, hi + 1)}
            out.extend((n, self._pad(r)) for r in self._spans(n, ln.words, chosen))
        return out

    def picked_bounds(self) -> QRectF:
        box = QRectF()
        for n, r in self.picked_boxes():
            box = box.united(self.to_pic[n].mapRect(r))
        return box

    def paint(self, p: QPainter, size: QRectF, accent: QColor, scale: float):
        dim = QPainterPath()
        dim.addRect(size)
        p.fillPath(dim.subtracted(self.holes()), QColor(0, 0, 0, 56))
        fill = QColor(accent)
        fill.setAlpha(105)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        base = p.transform()
        for n, r in self.picked_boxes():
            p.setTransform(self.to_pic[n] * base)
            radius = min(r.height() * 0.22, 6 / max(scale, 0.01))
            p.drawRoundedRect(r, radius, radius)
        p.setTransform(base)


class Canvas(QWidget):
    """The picture, fitted to the window, with the marks on top. With "Select text", the words in it
    can be picked and copied."""
    changed = Signal()
    picked = Signal()       # the picked words changed
    wants_text = Signal()   # "Select text" is on and the words haven't been read yet

    def __init__(self, image: QImage):
        super().__init__()
        self.base = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        self.shapes: list[Shape] = []
        self.undone: list = []      # for redo: shapes, or ("crop", old base, old shapes)
        self.history: list = []     # what undo takes back: "shape" or ("crop", old base, old shapes)
        self.tool = "arrow"
        self.color = QColor(COLORS[0])
        self.pen_width = 4.0
        self.current: Shape | None = None
        self.crop_rect: QRectF | None = None
        self.text: TextLayer | None = None  # the words read in the picture as it is now
        self.accent = QColor("#3cc7b2")
        self._clicks = (0.0, -1, 0)  # when the last click was, on which word, how many in a row
        self._picking = False
        self.changed.connect(self._forget_text)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setMinimumSize(360, 240)

    # picture <-> widget
    def _scale(self) -> tuple[float, QPointF]:
        ratio = self.devicePixelRatioF() or 1.0
        w, h = self.base.width() / ratio, self.base.height() / ratio
        s = min(1.0, (self.width() - 24) / w if w else 1, (self.height() - 24) / h if h else 1) / ratio
        s = max(s, 0.05)
        off = QPointF((self.width() - self.base.width() * s) / 2, (self.height() - self.base.height() * s) / 2)
        return s, off

    def to_image(self, pos: QPointF) -> QPointF:
        s, off = self._scale()
        return QPointF((pos.x() - off.x()) / s, (pos.y() - off.y()) / s)

    def to_widget(self, r: QRectF) -> QRectF:
        s, off = self._scale()
        return QRectF(off.x() + r.x() * s, off.y() + r.y() * s, r.width() * s, r.height() * s)

    # ---- the words in the picture
    def selecting(self) -> bool:
        return self.tool == "select"

    def set_text(self, layer: "TextLayer | None"):
        self.text = layer
        self.picked.emit()
        self.update()

    def _forget_text(self):  # (the picture changed: the words are read again when needed)
        if self.text is not None:
            self.set_text(None)

    def picked_text(self) -> str:
        span = self.text.picked() if self.text else None
        return self.text.text(span) if span else ""

    def pick_all(self):
        if self.text:
            self.text.pick_all()
            self.picked.emit()
            self.update()

    def unpick(self) -> bool:
        if self.text and self.text.picked():
            self.text.pick(None)
            self.picked.emit()
            self.update()
            return True
        return False

    def _press_select(self, e):
        if not self.text:
            self.wants_text.emit()
            return
        pt = self.to_image(e.position())
        at = self.text.word_at(pt, near_only=True)
        if at is None:
            self.unpick()
            return
        import time
        last, word, n = self._clicks
        now = time.monotonic()
        quick = now - last < QGuiApplication.styleHints().mouseDoubleClickInterval() / 1000
        n = n + 1 if quick and (word == at or n >= 2 and self.text.words[word][0] == self.text.words[at][0]) else 1
        self._clicks = (now, at, n)
        if n == 2:
            self.text.pick(at)
        elif n >= 3:
            self.text.pick_line(at)
        else:
            if e.modifiers() & Qt.ShiftModifier and self.text.picked():
                self.text.focus = at
            else:
                self.text.pick(at)
            self._picking = True
        self.picked.emit()
        self.update()

    def mouseDoubleClickEvent(self, e):
        if self.selecting() and e.button() == Qt.LeftButton:
            self._press_select(e)  # (Qt sends the second click of a double-click here)
        else:
            super().mouseDoubleClickEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        s, off = self._scale()
        target = QRectF(off.x(), off.y(), self.base.width() * s, self.base.height() * s)
        p.fillRect(target.adjusted(-1, -1, 1, 1), QColor(0, 0, 0, 40))
        p.drawImage(target, self.base)
        p.save()
        p.translate(off)
        p.scale(s, s)
        p.setClipRect(QRectF(0, 0, self.base.width(), self.base.height()))
        for shape in self.shapes + ([self.current] if self.current else []):
            draw_shape(p, shape, self.base)
        if self.selecting() and self.text:
            self.text.paint(p, QRectF(0, 0, self.base.width(), self.base.height()), self.accent, s)
        if self.crop_rect is not None:
            path = QPainterPath()
            path.addRect(QRectF(0, 0, self.base.width(), self.base.height()))
            hole = QPainterPath()
            hole.addRect(self.crop_rect)
            p.fillPath(path.subtracted(hole), QColor(0, 0, 0, 120))
            p.setPen(QPen(QColor("#ffffff"), 1.5 / s, Qt.DashLine))
            p.drawRect(self.crop_rect)
        p.restore()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self.selecting():
            self._press_select(e)
            return
        pt = self.to_image(e.position())
        if self.tool == "text":
            text, ok = QInputDialog.getText(self, tr("Text"), tr("Type the text to put on the picture:"))
            if ok and text.strip():
                self._add(Shape("text", self.color, self.pen_width, [pt], text.strip()))
            return
        if self.tool == "step":
            n = sum(1 for sh in self.shapes if sh.kind == "step") + 1
            self._add(Shape("step", self.color, self.pen_width, [pt], str(n)))
            return
        if self.tool == "crop":
            self.crop_rect = QRectF(pt, pt)
        else:
            self.current = Shape(self.tool, self.color, self.pen_width, [pt, pt])
        self.update()

    def mouseMoveEvent(self, e):
        pt = self.to_image(e.position())
        if self.selecting():
            if self._picking and e.buttons() & Qt.LeftButton and self.text:
                at = self.text.word_at(pt)
                if at is not None and at != self.text.focus:
                    self.text.focus = at
                    self.picked.emit()
                    self.update()
            on_word = bool(self.text) and self.text.word_at(pt, near_only=True) is not None
            self.setCursor(Qt.IBeamCursor if on_word or self._picking else Qt.ArrowCursor)
            return
        if self.crop_rect is not None and e.buttons() & Qt.LeftButton:
            self.crop_rect = QRectF(self.crop_rect.topLeft(), pt)
            self.update()
        elif self.current is not None:
            if self.current.kind in ("pen", "marker"):
                self.current.points.append(pt)
            else:
                self.current.points[-1] = pt
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self.selecting():
            if self._picking:
                self._picking = False
                self.picked.emit()
            return
        if self.crop_rect is not None:
            r = self.crop_rect.normalized().toRect().intersected(self.base.rect())
            self.crop_rect = None
            if r.width() > 4 and r.height() > 4:
                self.crop(r)
            self.update()
            return
        shape, self.current = self.current, None
        if shape is None:
            return
        size = shape.rect()
        if shape.kind in ("pen", "marker") and len(shape.points) > 1 or max(size.width(), size.height()) > 4:
            self._add(shape)
        self.update()

    def _add(self, shape: Shape):
        self.shapes.append(shape)
        self.history.append("shape")
        self.undone.clear()
        self.changed.emit()
        self.update()

    def crop(self, r: QRect):
        old = (self.base, list(self.shapes))
        self.base = self.base.copy(r)
        for s in self.shapes:
            s.points = [QPointF(pt.x() - r.x(), pt.y() - r.y()) for pt in s.points]
        self.history.append(("crop",) + old + (r,))
        self.undone.clear()
        self.changed.emit()
        self.update()

    def undo(self):
        if not self.history:
            return
        step = self.history.pop()
        if step == "shape":
            self.undone.append(self.shapes.pop())
        else:
            _, base, shapes, r = step
            for s in shapes:
                s.points = [QPointF(pt.x() + r.x(), pt.y() + r.y()) for pt in s.points]
            self.undone.append(("crop", r))
            self.base, self.shapes = base, shapes
        self.changed.emit()
        self.update()

    def redo(self):
        if not self.undone:
            return
        step = self.undone.pop()
        undone = list(self.undone)
        if isinstance(step, Shape):
            self._add(step)
        else:
            self.crop(step[1])
        self.undone = undone
        self.update()

    def result(self) -> QImage:
        """The picture with the marks drawn in."""
        out = self.base.copy()
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        for shape in self.shapes:
            draw_shape(p, shape, self.base)
        p.end()
        return out.convertToFormat(QImage.Format_RGB32) if not out.hasAlphaChannel() else out


class CaptureEditor(QWidget):
    """A screenshot, ready to mark up, copy or save."""

    def __init__(self, controller: "Controller", image: QImage, saved: Path | None = None):
        super().__init__()
        self.c = controller
        self.saved = saved
        self.dirty = False
        self.setObjectName("window")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowIcon(controller.icon)
        self.setWindowTitle(f"{tr('Screenshot')} - {APP_NAME}")
        self.canvas = Canvas(image)
        self.canvas.changed.connect(self._changed)
        self.canvas.picked.connect(self._picked)
        self.canvas.wants_text.connect(self.read_words)
        self.reading = False
        self._copy_after = False  # "Copy text" was pressed before the words were read
        self.pill = TextPill(self.canvas)
        self.pill.copy.connect(self.copy_text)
        self.pill.all.connect(self.canvas.pick_all)
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 8, 12, 8)
        bar.setSpacing(4)
        self.tools = QButtonGroup(self)
        self.tool_buttons: dict[str, QPushButton] = {}
        for key, text in TOOLS:
            b = QPushButton(tr(text))
            b.setObjectName("subtab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(key == "arrow")
            b.clicked.connect(lambda _=False, k=key: self._tool(k))
            b.setMinimumWidth(b.fontMetrics().horizontalAdvance(b.text()) + 30)
            self.tools.addButton(b)
            self.tool_buttons[key] = b
            bar.addWidget(b)
            if key == "select":
                b.setToolTip(tr("Pick words in the picture to copy, like text in a document"))
                bar.addSpacing(8)
        bar.addSpacing(8)
        self.swatches = QButtonGroup(self)
        for color in COLORS:
            b = QPushButton()
            b.setCheckable(True)
            b.setFixedSize(22, 22)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"QPushButton {{ background: {color}; border: 2px solid transparent; border-radius: 11px;"
                            f" padding: 0; }} QPushButton:checked {{ border-color: palette(text); }}")
            b.setChecked(color == COLORS[0])
            b.clicked.connect(lambda _=False, c=color: self._color(c))
            self.swatches.addButton(b)
            bar.addWidget(b)
        bar.addSpacing(8)
        self.b_undo = self._icon_button("arrow_rotate_counterclockwise", tr("Undo (Ctrl+Z)"), self.canvas.undo)
        self.b_redo = self._icon_button("arrow_rotate_clockwise", tr("Redo (Ctrl+Y)"), self.canvas.redo)
        bar.addWidget(self.b_undo)
        bar.addWidget(self.b_redo)
        bar.addStretch()
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.b_text = QPushButton(tr("Copy text"))
        self.b_text.setCursor(Qt.PointingHandCursor)
        self.b_text.setToolTip(tr("Copies the words you picked, or all the words in the picture"))
        self.b_text.clicked.connect(self.copy_text)
        actions.addWidget(self.b_text)
        pin = QPushButton(tr("Pin"))
        pin.setCursor(Qt.PointingHandCursor)
        pin.setToolTip(tr("Keeps the picture on top of everything, to look at while you work (P)"))
        pin.clicked.connect(self.pin)
        actions.addWidget(pin)
        copy = QPushButton(tr("Copy"))
        copy.setCursor(Qt.PointingHandCursor)
        copy.clicked.connect(self.copy)
        actions.addWidget(copy)
        self.b_save = QPushButton(tr("Save"))
        self.b_save.setObjectName("primary")
        self.b_save.setCursor(Qt.PointingHandCursor)
        self.b_save.clicked.connect(self.save)
        actions.addWidget(self.b_save)
        more = QPushButton(tr("More"))
        more.setCursor(Qt.PointingHandCursor)
        menu = QMenu(more)
        round_menu(menu)
        menu.addAction(tr("Save as…"), self.save_as)
        menu.addAction(tr("Show in folder"), self._reveal)
        menu.addAction(tr("Open in the Viewer"), self._viewer)
        more.setMenu(menu)
        actions.addWidget(more)
        top = QFrame()
        top.setObjectName("viewerBar")
        top.setLayout(bar)
        self.status = QLabel()
        self.status.setObjectName("muted")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(top)
        lay.addWidget(self.canvas, 1)
        foot = QHBoxLayout()
        foot.setContentsMargins(14, 8, 14, 10)
        foot.addWidget(self.status, 1)
        foot.addLayout(actions)
        lay.addLayout(foot)
        for keys, fn in (("Ctrl+Z", self.canvas.undo), ("Ctrl+Y", self.canvas.redo), ("Ctrl+Shift+Z", self.canvas.redo),
                         ("Ctrl+C", self.copy), ("Ctrl+S", self.save), ("Ctrl+Shift+S", self.save_as),
                         ("Ctrl+A", self.pick_all), ("P", self.pin), ("Esc", self._esc)):
            QShortcut(QKeySequence(keys), self).activated.connect(fn)
        self._size_to(image)
        self._update_status()
        self.apply_theme(controller.theme)

    def _icon_button(self, name: str, tip: str, fn) -> QPushButton:
        b = QPushButton()
        b.setObjectName("small")
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setIconSize(QSize(18, 18))
        b.setProperty("icon_name", name)
        b.clicked.connect(fn)
        return b

    def _size_to(self, image: QImage):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        ratio = self.devicePixelRatioF() or 1.0
        w = min(screen.width() - 80, max(860, image.width() / ratio + 60))
        h = min(screen.height() - 80, max(460, image.height() / ratio + 130))
        self.resize(int(w), int(h))

    def _tool(self, key: str):
        self.canvas.tool = key
        self.canvas.setCursor(Qt.IBeamCursor if key == "text" else Qt.ArrowCursor if key == "select" else
                              Qt.CrossCursor)
        b = self.tool_buttons.get(key)
        if b is not None and not b.isChecked():
            b.setChecked(True)
        if key == "select" and self.canvas.text is None:
            self.read_words()
        self._picked()
        self.canvas.update()

    def _esc(self):
        if not self.canvas.unpick():
            self.close()

    def pick_all(self):
        if not self.canvas.selecting():
            self._tool("select")
        self.canvas.pick_all()

    def _picked(self):
        """The picked words changed: the small Copy button goes next to them."""
        cv = self.canvas
        span = cv.text.picked() if cv.selecting() and cv.text else None
        if span is None or cv._picking:
            self.pill.hide()
        else:
            self.pill.show_by(cv.to_widget(cv.text.picked_bounds()))
        self._update_status()

    def _color(self, color: str):
        self.canvas.color = QColor(color)

    def _changed(self):
        self.dirty = True
        self._update_status()

    def _update_status(self):
        img = self.canvas.base
        bits = [f"{img.width()} × {img.height()}"]
        if self.canvas.selecting():
            if self.reading:
                bits.append(tr("Reading the text…"))
            elif self.canvas.text is not None and not self.canvas.text:
                bits.append(tr("No text found in the picture"))
            elif self.canvas.text:
                span = self.canvas.text.picked()
                bits.append(tr("Words picked: {count}", count=span[1] - span[0] + 1) if span else
                            tr("Drag over the words you want · double-click: one word · Ctrl+A: all"))
            self.status.setText("   ·   ".join(bits))
            return
        if self.saved is not None:
            bits.append(tr("Saved as {name}", name=self.saved.name) if not self.dirty else
                        tr("Changes not saved yet"))
        self.status.setText("   ·   ".join(bits))

    def copy(self):
        if self.canvas.picked_text():
            self.copy_text()
            return
        QGuiApplication.clipboard().setImage(self.canvas.result())
        self.c.toast.notify(tr("Copied"), tr("Paste the picture anywhere with Ctrl+V."), linger_ms=3500)

    def pin(self):
        """The picture (with its marks) in a small window that stays on top; the editor closes."""
        pin_picture(self.c, self.canvas.result(), self.geometry().center())
        self.close()

    def _write(self, path: Path) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        fmt = "JPG" if path.suffix.lower() in (".jpg", ".jpeg") else "PNG"
        ok = self.canvas.result().save(str(path), fmt, 92 if fmt == "JPG" else -1)
        if not ok:
            self.c.tell(tr("Couldn't save"), str(path))
        return ok

    def save(self) -> bool:
        from .capture import new_name, screenshots_folder
        path = self.saved or new_name(screenshots_folder(), ".png")
        if not self._write(path):
            return False
        self.saved = path
        self.dirty = False
        self._update_status()
        self.c.toast.notify(tr("Saved"), path.name, tr("Show in folder"), lambda p=path: reveal_in_explorer(p),
                            linger_ms=4000)
        return True

    def save_as(self):
        from .capture import new_name, screenshots_folder
        start = self.saved or new_name(screenshots_folder(), ".png")
        name, _ = QFileDialog.getSaveFileName(self, tr("Save as"), str(start), "PNG (*.png);;JPG (*.jpg)")
        if name:
            path = Path(name)
            if self._write(path):
                self.saved = path
                self.dirty = False
                self._update_status()

    def _reveal(self):
        if self.saved is None and not self.save():
            return
        reveal_in_explorer(self.saved)

    def _viewer(self):
        if self.saved is None or self.dirty:
            if not self.save():
                return
        self.c.open_viewer(self.saved)

    def copy_text(self):
        """Copies the picked words; with none picked, all the words (and turns on "Select text", to pick
        only some next time)."""
        cv = self.canvas
        if not cv.selecting():
            self._tool("select")
        if cv.text is None:
            self._copy_after = True
            self.read_words()
            return
        text = cv.picked_text() or cv.text.text()
        if not text.strip():
            self.c.toast.notify(tr("No text found in the picture"), "", linger_ms=3000)
            return
        QGuiApplication.clipboard().setText(text)
        import re
        preview = re.sub(r"\s+", " ", text).strip()
        preview = preview if len(preview) <= 70 else preview[:67] + "..."
        self.c.toast.notify(tr("Copied"), preview, linger_ms=3500)

    def read_words(self):
        """Reads the words in the picture with Windows' own OCR, away from the window's thread."""
        from .extras import ocr_available
        if self.reading:
            return
        if not ocr_available():
            self._copy_after = False
            self.c.tell(tr("Can't read text here"), tr("Reading text needs Windows 10 or 11"))
            return
        self.reading = True
        self._update_status()
        image = self.canvas.result()
        reader = self._reader = _Reader(self)
        reader.done.connect(lambda lines, error, img=image: self._words_read(img, lines, error))
        reader.start(image)

    def _words_read(self, image: QImage, lines, error: str):
        self.reading = False
        if error:
            self._copy_after = False
            self._update_status()
            self.c.tell(tr("Can't read text here"), error)
            return
        if image.size() != self.canvas.base.size() or self.canvas.text is not None:
            if self.canvas.text is None and self.canvas.selecting():
                self.read_words()  # (cropped meanwhile: read the new picture)
            return
        self.canvas.set_text(TextLayer(lines or []))
        if self._copy_after:
            self._copy_after = False
            self.copy_text()
        self._update_status()

    def apply_theme(self, theme):
        set_title_bar_dark(self, theme.dark)
        self.canvas.accent = QColor(theme.accent)
        for b in (self.b_undo, self.b_redo):
            b.setIcon(icons.icon(b.property("icon_name"), theme.text))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._picked()

    def closeEvent(self, e):
        if self.dirty and self.saved is not None:
            self._write(self.saved)  # (marks made after saving are kept in the file)
        super().closeEvent(e)


class TextPill(QFrame):
    """The small Copy · Select all bar next to the picked words."""
    copy = Signal()
    all = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("toast")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        for text, sig in ((tr("Copy"), self.copy), (tr("Select all"), self.all)):
            b = QPushButton(text)
            b.setObjectName("small")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, s=sig: s.emit())
            lay.addWidget(b)
        self.hide()

    def show_by(self, words: QRectF):
        """Above the picked words (below them when there's no room), inside the picture's area."""
        self.adjustSize()
        area = self.parentWidget().rect()
        x = round(words.center().x() - self.width() / 2)
        y = round(words.top() - self.height() - 8)
        if y < 4:
            y = round(words.bottom() + 8)
        if y + self.height() > area.height() - 4:
            y = max(4, round(words.top()) + 4)
        self.move(max(4, min(x, area.width() - self.width() - 4)), y)
        self.show()
        self.raise_()


class _Reader(QObject):
    """Reads the words in a picture on a thread of its own."""
    done = Signal(object, str)

    def start(self, image: QImage):
        import threading

        def work():
            lines, error = None, ""
            try:
                from PIL import Image
                from PySide6.QtCore import QBuffer, QByteArray, QIODevice
                data = QByteArray()
                buf = QBuffer(data)
                buf.open(QIODevice.WriteOnly)
                image.save(buf, "PNG")
                buf.close()
                import io

                from .extras import read_lines
                lines = read_lines(Image.open(io.BytesIO(bytes(data))))
            except Exception as e:  # (no OCR language, an old Windows...)
                log.warning("reading the text failed", exc_info=True)
                error = str(e) or type(e).__name__
            try:
                self.done.emit(lines, error)
            except RuntimeError:  # the editor was closed meanwhile
                pass
        threading.Thread(target=work, daemon=True, name="corefold-read-text").start()


# ----------------------------------------------------------------------------- pinned pictures

_pins: list = []


def pin_picture(controller, image: QImage, center=None):
    pin = PinnedPicture(controller, image)
    _pins[:] = [w for w in _pins if _alive(w)] + [pin]
    if center is not None:
        pin.move(center.x() - pin.width() // 2, center.y() - pin.height() // 2)
    pin.show()
    return pin


def _alive(widget) -> bool:
    try:
        from shiboken6 import isValid
        return isValid(widget)
    except ImportError:
        return True


class PinnedPicture(QWidget):
    """A screenshot that stays on top of every window. Drag it anywhere, the mouse wheel makes it
    bigger or smaller (Ctrl+wheel: see-through), double-click or Esc closes it."""

    def __init__(self, controller, image: QImage):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.c = controller
        self.image = image
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"{tr('Pinned picture')} - {APP_NAME}")
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip(tr("Drag to move · wheel: size · Ctrl+wheel: see-through · double-click: close"))
        self._drag = None
        ratio = self.devicePixelRatioF() or 1.0
        screen = QGuiApplication.primaryScreen().availableGeometry()
        w, h = image.width() / ratio, image.height() / ratio
        self.zoom = min(1.0, (screen.width() * 0.6) / max(1, w), (screen.height() * 0.6) / max(1, h))
        self._resize()
        QShortcut(QKeySequence("Esc"), self).activated.connect(self.close)
        QShortcut(QKeySequence("Ctrl+C"), self).activated.connect(self.copy)

    def _resize(self):
        ratio = self.devicePixelRatioF() or 1.0
        self.resize(max(40, round(self.image.width() / ratio * self.zoom)) + 2,
                    max(30, round(self.image.height() / ratio * self.zoom)) + 2)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(QRectF(1, 1, self.width() - 2, self.height() - 2), self.image)
        p.setPen(QPen(QColor(60, 199, 178), 1))
        p.drawRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        elif e.button() == Qt.RightButton:
            menu = QMenu(self)
            round_menu(menu)
            menu.addAction(tr("Copy"), self.copy)
            menu.addAction(tr("Mark it up"), self.edit)
            menu.addAction(tr("Actual size"), self.actual_size)
            menu.addSeparator()
            menu.addAction(tr("Close"), self.close)
            menu.exec(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        self.close()

    def wheelEvent(self, e):
        step = 1 if e.angleDelta().y() > 0 else -1
        if e.modifiers() & Qt.ControlModifier:
            self.setWindowOpacity(min(1.0, max(0.25, self.windowOpacity() + step * 0.1)))
            return
        center = self.geometry().center()
        self.zoom = min(4.0, max(0.1, self.zoom * (1.12 if step > 0 else 1 / 1.12)))
        self._resize()
        self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)

    def actual_size(self):
        center = self.geometry().center()
        self.zoom = 1.0
        self._resize()
        self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)

    def copy(self):
        QGuiApplication.clipboard().setImage(self.image)
        self.c.toast.notify(tr("Copied"), tr("Paste the picture anywhere with Ctrl+V."), linger_ms=3000)

    def edit(self):
        cap = self.c.capture
        if cap is not None:
            cap.edit(self.image)
            self.close()


class Countdown(QWidget):
    """5, 4, 3, 2, 1 at the top of the screen before a screenshot (left out of the screenshot)."""

    def __init__(self, seconds: int, done):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.left, self.done = seconds, done
        self.resize(84, 84)
        from PySide6.QtGui import QCursor
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        self.move(g.center().x() - 42, g.top() + 40)
        from PySide6.QtCore import QTimer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)

    def showEvent(self, e):
        super().showEvent(e)
        from .capture import exclude_from_capture
        exclude_from_capture(self)

    def _tick(self):
        self.left -= 1
        if self.left <= 0:
            self.timer.stop()
            self.hide()
            self.done()
            self.close()
            self.deleteLater()
            return
        self.update()

    def cancel(self):
        self.timer.stop()
        self.close()
        self.deleteLater()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 22, 24, 210))
        p.drawEllipse(QRectF(self.rect()).adjusted(2, 2, -2, -2))
        f = QFont(self.font())
        f.setPointSizeF(26)
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(QColor("#fff"))
        p.drawText(QRectF(self.rect()), Qt.AlignCenter, str(self.left))
