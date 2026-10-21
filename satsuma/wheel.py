"""The format wheel that pops up under the cursor, plus a preview of it for the settings window."""
from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .converters import Tool, tools_for
from .settings import WHEEL_SIZES, Settings
from .theme import Theme

CENTER = -2  # hover value for the cancel area in the middle
POP = 7      # how far the hovered slice grows


def inner_radius(R: float) -> float:
    return round(R * 0.36)


def paint_wheel(p: QPainter, c: QPointF, R: float, labels: list[str], hover: int,
                line1: str, line2: str, t: Theme):
    r = inner_radius(R)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)

    # Base disc. Painted everywhere (never fully transparent) so Windows sends drops to it.
    p.setPen(Qt.NoPen)
    p.setBrush(t.wheel_bg)
    p.drawEllipse(c, R, R)

    n = len(labels)
    hole = QPainterPath()
    hole.addEllipse(c, r, r)
    label_font = QFont("Segoe UI", max(8, round(R / 17)))
    if n:
        seg = 360 / n
        for i, label in enumerate(labels):
            hovered = i == hover
            rr = R + (POP if hovered else 0)
            mid = i * seg
            pie = QPainterPath(c)
            pie.arcTo(QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr), 90 - mid - seg / 2, seg)
            pie.closeSubpath()
            stripe = QColor(t.wheel_stripe)
            if i % 2 == 0:
                stripe.setAlpha(stripe.alpha() // 2)
            p.setBrush(t.accent if hovered else stripe)
            p.setPen(QPen(t.wheel_line, 1))
            p.drawPath(pie.subtracted(hole))

            m, a = (R + r) / 2 + 3, math.radians(mid)
            pos = QPointF(c.x() + m * math.sin(a), c.y() - m * math.cos(a))
            label_font.setWeight(QFont.Weight.Bold if hovered else QFont.Weight.DemiBold)
            p.setFont(label_font)
            p.setPen(t.on_accent if hovered else t.text)
            p.drawText(QRectF(pos.x() - 50, pos.y() - 13, 100, 26), Qt.AlignCenter, label)

    rim = QColor(t.accent)
    rim.setAlpha(150)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(rim, 2))
    p.drawEllipse(c, R - 1, R - 1)

    # middle: what's being dropped + what will happen
    p.setPen(QPen(t.wheel_line, 1))
    p.setBrush(t.wheel_center)
    p.drawEllipse(c, r - 4, r - 4)
    width = int(2 * r - 18)
    f1 = QFont("Segoe UI", max(7, round(R / 21)))
    f1.setWeight(QFont.Weight.DemiBold)
    f2 = QFont("Segoe UI", max(7, round(R / 22)))
    p.setFont(f1)
    p.setPen(t.text)
    p.drawText(QRectF(c.x() - r, c.y() - 19, 2 * r, 17), Qt.AlignCenter,
               QFontMetrics(f1).elidedText(line1, Qt.ElideMiddle, width))
    p.setFont(f2)
    p.setPen(t.accent if hover >= 0 else t.muted)
    p.drawText(QRectF(c.x() - r + 7, c.y(), 2 * r - 14, r - 4),
               Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, line2)


def ease_out_back(x: float) -> float:
    c1 = 1.70158
    return 1 + (c1 + 1) * (x - 1) ** 3 + c1 * (x - 1) ** 2


class Wheel(QWidget):
    chosen = Signal(object, list)  # Tool, files

    def __init__(self, settings: Settings, theme: Theme):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.settings, self.theme = settings, theme
        self.R = WHEEL_SIZES["medium"]
        self.scale = 1.0
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(170)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.valueChanged.connect(self._animate)
        self.reset()

    def reset(self):
        self.files: list[Path] = []
        self.tools: list[Tool] = []
        self.hover = CENTER
        self.title = ""
        self.empty_reason = ""

    def _animate(self, v):
        v = float(v)
        self.scale = 0.82 + 0.18 * ease_out_back(v)
        self.setWindowOpacity(min(1.0, v * 1.8))
        self.update()

    def popup_at_cursor(self):
        if self.isVisible():
            return
        self.reset()
        self.R = WHEEL_SIZES.get(self.settings.wheel_size, WHEEL_SIZES["medium"])
        side = 2 * (self.R + POP) + 14
        self.setFixedSize(side, side)
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        x = min(max(pos.x() - side // 2, g.left()), g.right() - side)
        y = min(max(pos.y() - side // 2, g.top()), g.bottom() - side)
        self.move(x, y)
        if self.settings.animations:
            self.scale = 0.82
            self.setWindowOpacity(0.0)
            self.show()
            self.anim.stop()
            self.anim.start()
        else:
            self.scale = 1.0
            self.setWindowOpacity(1.0)
            self.show()
        self.raise_()

    def hit(self, pos: QPointF) -> int:
        dx, dy = pos.x() - self.width() / 2, pos.y() - self.height() / 2
        dist = math.hypot(dx, dy)
        if dist < inner_radius(self.R):
            return CENTER
        if dist > self.R + POP or not self.tools:
            return -1
        seg = 360 / len(self.tools)
        angle = math.degrees(math.atan2(dx, -dy)) % 360  # 0 = top, clockwise
        return int(((angle + seg / 2) % 360) // seg) % len(self.tools)

    # drag & drop
    def dragEnterEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if not paths:
            e.ignore()
            return
        self.files, self.tools = tools_for(paths, set(self.settings.hidden_tools))
        if not self.tools:
            self.title = paths[0].name
            if self.files:
                self.empty_reason = "All tools for this type are hidden in Settings"
            elif paths[0].is_dir():
                self.empty_reason = "Folders can't be converted"
            else:
                self.empty_reason = f"Can't convert {paths[0].suffix.lower() or 'this file'}"
        elif len(self.files) == 1:
            self.title = self.files[0].name
        else:
            self.title = f"{len(self.files)} files"
        e.setDropAction(Qt.CopyAction)  # Shift normally means "move"; Satsuma always copies
        e.accept()
        self.update()

    def dragMoveEvent(self, e):
        self.hover = self.hit(e.position())
        if self.hover >= 0:
            e.setDropAction(Qt.CopyAction)
            e.accept()
        else:
            e.ignore()  # middle or outside = cancel
        self.update()

    def dragLeaveEvent(self, e):
        self.hover = -1
        self.update()

    def dropEvent(self, e):
        idx = self.hit(e.position())
        if 0 <= idx < len(self.tools):
            e.setDropAction(Qt.CopyAction)
            e.accept()
            self.chosen.emit(self.tools[idx], list(self.files))
        else:
            e.ignore()
        self.hide()

    def paintEvent(self, _):
        p = QPainter(self)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self.scale != 1.0:
            p.translate(c)
            p.scale(self.scale, self.scale)
            p.translate(-c)
        if not self.title:
            line1, line2 = "Satsuma", "Drop on a format"
        elif not self.tools:
            line1, line2 = self.title, self.empty_reason
        elif self.hover >= 0:
            line1, line2 = self.title, self.tools[self.hover].description
        elif self.hover == CENTER:
            line1, line2 = self.title, "Let go here to cancel"
        else:
            line1, line2 = self.title, "Drop on a format"
        paint_wheel(p, c, self.R, [t.label for t in self.tools], self.hover, line1, line2, self.theme)
        p.end()


class WheelPreview(QWidget):
    """A live, non-interactive wheel for the Look settings page."""
    DEMO = [("PNG", "Convert to PNG"), ("JPG", "Convert to JPG"), ("WEBP", "Convert to WebP"),
            ("PDF", "Convert to PDF"), ("Small", "Shrink for sharing"), ("Clean", "Remove location & camera info")]

    def __init__(self, settings: Settings, theme: Theme):
        super().__init__()
        self.settings, self.theme = settings, theme
        self.hover = 1
        side = int(2 * (WHEEL_SIZES["large"] + POP) * 0.62) + 16
        self.setFixedSize(side, side)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._step)
        self.timer.start(1100)

    def _step(self):
        if self.settings.animations:
            self.hover = (self.hover + 1) % len(self.DEMO)
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        R = WHEEL_SIZES.get(self.settings.wheel_size, WHEEL_SIZES["medium"]) * 0.62
        c = QPointF(self.width() / 2, self.height() / 2)
        paint_wheel(p, c, R, [d[0] for d in self.DEMO], self.hover, "photo.heic", self.DEMO[self.hover][1],
                    self.theme)
        p.end()
