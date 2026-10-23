"""Corefold Capture's windows: the small editor a screenshot opens in (arrows, boxes, a marker, text,
blur, crop), and the bar that shows while the screen is being recorded."""
from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QKeySequence, QPainter, QPainterPath, QPen, \
    QPolygonF, QShortcut
from PySide6.QtWidgets import QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, \
    QPushButton, QVBoxLayout, QWidget

from . import icons
from .core import APP_NAME
from .i18n import tr
from .theme import round_menu
from .winutil import reveal_in_explorer, set_title_bar_dark

if TYPE_CHECKING:
    from .app import Controller

TOOLS = [("arrow", "Arrow"), ("box", "Box"), ("marker", "Marker"), ("pen", "Pen"), ("text", "Text"),
         ("blur", "Blur"), ("crop", "Crop")]
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


class Canvas(QWidget):
    """The picture, fitted to the window, with the marks on top."""
    changed = Signal()

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
        pt = self.to_image(e.position())
        if self.tool == "text":
            text, ok = QInputDialog.getText(self, tr("Text"), tr("Type the text to put on the picture:"))
            if ok and text.strip():
                self._add(Shape("text", self.color, self.pen_width, [pt], text.strip()))
            return
        if self.tool == "crop":
            self.crop_rect = QRectF(pt, pt)
        else:
            self.current = Shape(self.tool, self.color, self.pen_width, [pt, pt])
        self.update()

    def mouseMoveEvent(self, e):
        pt = self.to_image(e.position())
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
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 8, 12, 8)
        bar.setSpacing(4)
        self.tools = QButtonGroup(self)
        for key, text in TOOLS:
            b = QPushButton(tr(text))
            b.setObjectName("subtab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(key == "arrow")
            b.clicked.connect(lambda _=False, k=key: self._tool(k))
            self.tools.addButton(b)
            bar.addWidget(b)
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
        self.b_text = QPushButton(tr("Copy text"))
        self.b_text.setCursor(Qt.PointingHandCursor)
        self.b_text.setToolTip(tr("Reads the words in the picture and copies them"))
        self.b_text.clicked.connect(self.copy_text)
        bar.addWidget(self.b_text)
        copy = QPushButton(tr("Copy"))
        copy.setCursor(Qt.PointingHandCursor)
        copy.clicked.connect(self.copy)
        bar.addWidget(copy)
        self.b_save = QPushButton(tr("Save"))
        self.b_save.setObjectName("primary")
        self.b_save.setCursor(Qt.PointingHandCursor)
        self.b_save.clicked.connect(self.save)
        bar.addWidget(self.b_save)
        more = QPushButton(tr("More"))
        more.setCursor(Qt.PointingHandCursor)
        menu = QMenu(more)
        round_menu(menu)
        menu.addAction(tr("Save as…"), self.save_as)
        menu.addAction(tr("Show in folder"), self._reveal)
        menu.addAction(tr("Open in the Viewer"), self._viewer)
        more.setMenu(menu)
        bar.addWidget(more)
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
        foot.setContentsMargins(14, 6, 14, 8)
        foot.addWidget(self.status, 1)
        lay.addLayout(foot)
        for keys, fn in (("Ctrl+Z", self.canvas.undo), ("Ctrl+Y", self.canvas.redo), ("Ctrl+Shift+Z", self.canvas.redo),
                         ("Ctrl+C", self.copy), ("Ctrl+S", self.save), ("Ctrl+Shift+S", self.save_as),
                         ("Esc", self.close)):
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
        w = min(screen.width() - 80, max(720, image.width() / ratio + 60))
        h = min(screen.height() - 80, max(460, image.height() / ratio + 130))
        self.resize(int(w), int(h))

    def _tool(self, key: str):
        self.canvas.tool = key
        self.canvas.setCursor(Qt.IBeamCursor if key == "text" else Qt.CrossCursor)

    def _color(self, color: str):
        self.canvas.color = QColor(color)

    def _changed(self):
        self.dirty = True
        self._update_status()

    def _update_status(self):
        img = self.canvas.base
        bits = [f"{img.width()} × {img.height()}"]
        if self.saved is not None:
            bits.append(tr("Saved as {name}", name=self.saved.name) if not self.dirty else
                        tr("Changes not saved yet"))
        self.status.setText("   ·   ".join(bits))

    def copy(self):
        QGuiApplication.clipboard().setImage(self.canvas.result())
        self.c.toast.notify(tr("Copied"), tr("Paste the picture anywhere with Ctrl+V."), linger_ms=3500)

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
        """Windows' own text reading, like the Converter's "Copy the text"."""
        import tempfile
        from .converters import catalog
        tool = next((t for kind in catalog().values() for t in kind if t.key.endswith(":ocr")), None)
        if tool is None:
            self.c.tell(tr("Can't read text here"), tr("Reading text needs Windows 10 or 11"))
            return
        folder = Path(tempfile.mkdtemp(prefix="corefold-capture-"))
        png = folder / "screenshot.png"
        self.canvas.result().save(str(png), "PNG")
        self.c.start_job(tool, [png])

    def apply_theme(self, theme):
        set_title_bar_dark(self, theme.dark)
        for b in (self.b_undo, self.b_redo):
            b.setIcon(icons.icon(b.property("icon_name"), theme.text))

    def closeEvent(self, e):
        if self.dirty and self.saved is not None:
            self._write(self.saved)  # (marks made after saving are kept in the file)
        super().closeEvent(e)


# ----------------------------------------------------------------------------- recording

class RecordFrame(QWidget):
    """A dashed line around what's being recorded (left out of the recording itself), with a
    3-2-1 countdown in the middle before it starts."""

    def __init__(self, rect: QRect):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setGeometry(rect.adjusted(-3, -3, 3, 3))
        self.count = 0

    def showEvent(self, e):
        super().showEvent(e)
        from .capture import exclude_from_capture
        exclude_from_capture(self)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#e5484d"), 2, Qt.DashLine))
        p.drawRect(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        if self.count:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(20, 22, 24, 200))
            c = QRectF(self.rect()).center()
            p.drawEllipse(c, 46, 46)
            f = QFont(self.font())
            f.setPointSizeF(30)
            f.setWeight(QFont.Weight.Bold)
            p.setFont(f)
            p.setPen(QColor("#fff"))
            p.drawText(QRectF(c.x() - 46, c.y() - 46, 92, 92), Qt.AlignCenter, str(self.count))


class RecordBar(QWidget):
    """Shows while recording: the time, and Stop (left out of the recording itself)."""
    stop = Signal()

    def __init__(self, rect: QRect, theme):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        frame = QFrame(self)
        frame.setObjectName("toast")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(14, 8, 8, 8)
        lay.setSpacing(10)
        dot = QLabel("●")
        dot.setStyleSheet("color: #e5484d; font-size: 12pt;")
        self.time = QLabel("0:00")
        self.time.setStyleSheet("font-weight: 600;")
        b = QPushButton(tr("Stop"))
        b.setObjectName("primary")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(self.stop.emit)
        lay.addWidget(dot)
        lay.addWidget(self.time)
        lay.addWidget(b)
        self.adjustSize()
        screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = rect.center().x() - self.width() // 2
        y = rect.top() - self.height() - 10
        if y < area.top() + 4:
            y = rect.bottom() + 10
        if y + self.height() > area.bottom():
            y = area.top() + 10  # (the whole screen: on top of it, left out of the recording)
        self.move(max(area.left() + 4, min(x, area.right() - self.width() - 4)), y)

    def showEvent(self, e):
        super().showEvent(e)
        from .capture import exclude_from_capture
        exclude_from_capture(self)

    def set_seconds(self, s: float):
        s = int(s)
        self.time.setText(f"{s // 60}:{s % 60:02d}" if s < 3600 else f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}")

