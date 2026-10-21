"""The format wheel that pops up under the cursor, plus a preview of it for the settings window.

Two ways to use it:
- drag mode: hold Shift while dragging a file; drop it on a slice.
- click mode: right-click a file → "Convert with Satsuma"; the wheel appears and you click a slice.
Slices that can't be used for the file are drawn dimmed; hovering one shows why.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import winutil
from .converters import Slot, tools_for
from .settings import WHEEL_SIZES, Settings
from .theme import Theme

CENTER = -2  # hover value for the cancel area in the middle
POP = 7      # how far the hovered slice grows
INNER = {"ring": 0.36, "petals": 0.34, "slim": 0.5}


@dataclass
class Item:
    label: str
    ok: bool = True
    favorite: bool = False


def inner_radius(R: float, style: str) -> float:
    return round(R * INNER.get(style, 0.36))


def slice_path(c: QPointF, r_in: float, r_out: float, start: float, span: float) -> QPainterPath:
    """Ring segment; angles in degrees, 0 = top, clockwise."""
    outer = QRectF(c.x() - r_out, c.y() - r_out, 2 * r_out, 2 * r_out)
    inner = QRectF(c.x() - r_in, c.y() - r_in, 2 * r_in, 2 * r_in)
    a0 = 90 - start  # Qt: 0 = 3 o'clock, counter-clockwise
    path = QPainterPath()
    path.arcMoveTo(outer, a0)
    path.arcTo(outer, a0, -span)
    path.arcTo(inner, a0 - span, span)
    path.closeSubpath()
    return path


def paint_wheel(p: QPainter, c: QPointF, R: float, items: list[Item], hover: int,
                line1: str, line2: str, t: Theme, style: str = "ring"):
    r = inner_radius(R, style)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    petals = style == "petals"

    # Base disc. Never fully transparent, so Windows sends drops to it (even in the petal gaps).
    base = QColor(t.wheel_bg)
    if petals:
        base.setAlpha(3)
    p.setPen(Qt.NoPen)
    p.setBrush(base)
    p.drawEllipse(c, R + POP, R + POP) if petals else p.drawEllipse(c, R, R)

    n = len(items)
    size = max(8, round(R / 17))
    if n > 9:
        size = max(7, round(size * 9 / n + 1))
    label_font = QFont("Segoe UI", size)
    if n:
        seg = 360 / n
        # petals: a gap of ~9 px at the inner edge, widening outwards
        gap = math.degrees(9 / max(r + 3, 1)) if petals and n > 1 else 0
        for i, item in enumerate(items):
            hovered = i == hover
            usable_hover = hovered and item.ok
            rr = R + (POP if usable_hover else 0)
            mid = i * seg
            path = slice_path(c, r + (3 if petals else 0), rr - (2 if petals else 0),
                              mid - seg / 2 + gap / 2, seg - gap)
            if usable_hover:
                fill = QColor(t.accent)
            elif petals:
                fill = QColor(t.wheel_bg)
                if hovered:
                    fill = fill.lighter(115) if t.dark else fill.darker(106)
            else:
                fill = QColor(t.wheel_stripe)
                if i % 2 == 0:
                    fill.setAlpha(fill.alpha() // 2)
                if hovered:
                    fill.setAlpha(fill.alpha() + 18)
            p.setBrush(fill)
            if petals:
                p.setPen(QPen(fill, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            else:
                p.setPen(QPen(t.wheel_line, 1))
            p.drawPath(path)

            m, a = (rr + r) / 2 + (2 if style == "slim" else 3), math.radians(mid)
            pos = QPointF(c.x() + m * math.sin(a), c.y() - m * math.cos(a))
            label_font.setWeight(QFont.Weight.Bold if usable_hover else QFont.Weight.DemiBold)
            label_font.setStrikeOut(False)
            p.setFont(label_font)
            if usable_hover:
                color = QColor(t.on_accent)
            elif item.ok:
                color = QColor(t.text)
            else:
                color = QColor(t.muted)
                color.setAlpha(150 if hovered else 95)
            p.setPen(color)
            text = ("★ " if item.favorite and item.ok else "") + item.label
            p.drawText(QRectF(pos.x() - 52, pos.y() - 13, 104, 26), Qt.AlignCenter, text)

    if not petals:
        rim = QColor(t.accent)
        rim.setAlpha(150)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(rim, 2))
        p.drawEllipse(c, R - 1, R - 1)

    # middle: what's being dropped + what will happen
    p.setPen(QPen(t.wheel_line, 1))
    center_bg = QColor(t.wheel_center)
    if hover == CENTER and n:
        center_bg = center_bg.lighter(125) if t.dark else center_bg.darker(104)
    p.setBrush(center_bg)
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
    unusable = 0 <= hover < n and not items[hover].ok
    p.setPen(t.accent if hover >= 0 and not unusable else t.muted)
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
        self.setMouseTracking(True)
        self.settings, self.theme = settings, theme
        self.R = WHEEL_SIZES["medium"]
        self.scale = 1.0
        self.click_mode = False
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(170)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.valueChanged.connect(self._animate)
        self.watch = QTimer(self)  # click mode: close on Esc or a click outside
        self.watch.timeout.connect(self._watch)
        self.reset()

    def reset(self):
        self.files: list[Path] = []
        self.slots: list[Slot] = []
        self.hover = CENTER
        self.title = ""
        self.empty_reason = ""

    def _animate(self, v):
        v = float(v)
        self.scale = 0.82 + 0.18 * ease_out_back(v)
        self.setWindowOpacity(min(1.0, v * 1.8))
        self.update()

    def _load(self, paths: list[Path]):
        s = self.settings
        self.files, self.slots = tools_for(paths, set(s.hidden_tools), s.favorites, s.show_unavailable)
        if not self.slots:
            self.title = paths[0].name
            if self.files:
                self.empty_reason = "Nothing available. Check Formats in Settings"
            elif paths[0].is_dir():
                self.empty_reason = "Folders can't be converted"
            else:
                self.empty_reason = f"Can't convert {paths[0].suffix.lower() or 'this file'}"
        elif len(self.files) == 1:
            self.title = self.files[0].name
        else:
            self.title = f"{len(self.files)} files"

    def popup_at_cursor(self, paths: list[Path] | None = None):
        """Drag mode when paths is None (files arrive with the drag); click mode otherwise."""
        if self.isVisible() and not paths:
            return
        self.hide()
        self.reset()
        self.click_mode = paths is not None
        if paths:
            self._load(paths)
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
        if self.click_mode:
            self._down_at_open = winutil.any_button_down()
            self.watch.start(30)

    def hideEvent(self, e):
        self.watch.stop()
        self.click_mode = False
        super().hideEvent(e)

    def _watch(self):
        if winutil.key_down(winutil.VK_ESCAPE):
            self.hide()
            return
        down = winutil.any_button_down()
        if self._down_at_open:  # wait for the click that opened the menu to finish
            self._down_at_open = down
            return
        if down and not self.geometry().contains(QCursor.pos()):
            self.hide()

    def hit(self, pos: QPointF) -> int:
        dx, dy = pos.x() - self.width() / 2, pos.y() - self.height() / 2
        dist = math.hypot(dx, dy)
        if dist < inner_radius(self.R, self.settings.wheel_style):
            return CENTER
        if dist > self.R + POP or not self.slots:
            return -1
        seg = 360 / len(self.slots)
        angle = math.degrees(math.atan2(dx, -dy)) % 360  # 0 = top, clockwise
        return int(((angle + seg / 2) % 360) // seg) % len(self.slots)

    def _usable(self, idx: int) -> bool:
        return 0 <= idx < len(self.slots) and self.slots[idx].ok

    # drag & drop
    def dragEnterEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if not paths:
            e.ignore()
            return
        self._load(paths)
        e.setDropAction(Qt.CopyAction)  # Shift normally means "move"; Satsuma always copies
        e.accept()
        self.update()

    def dragMoveEvent(self, e):
        self.hover = self.hit(e.position())
        if self._usable(self.hover):
            e.setDropAction(Qt.CopyAction)
            e.accept()
        else:
            e.ignore()  # middle, outside or a dimmed slice = no drop
        self.update()

    def dragLeaveEvent(self, e):
        self.hover = -1
        self.update()

    def dropEvent(self, e):
        idx = self.hit(e.position())
        if self._usable(idx):
            e.setDropAction(Qt.CopyAction)
            e.accept()
            self.chosen.emit(self.slots[idx].tool, list(self.files))
        else:
            e.ignore()
        self.hide()

    # click mode
    def mouseMoveEvent(self, e):
        if self.click_mode:
            idx = self.hit(e.position())
            if idx != self.hover:
                self.hover = idx
                self.update()

    def mouseReleaseEvent(self, e):
        if not self.click_mode:
            return
        idx = self.hit(e.position())
        if self._usable(idx):
            tool, files = self.slots[idx].tool, list(self.files)
            self.hide()
            self.chosen.emit(tool, files)
        elif idx in (CENTER, -1):
            self.hide()
        # clicking a dimmed slice keeps the wheel open; its reason is already shown

    def paintEvent(self, _):
        p = QPainter(self)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self.scale != 1.0:
            p.translate(c)
            p.scale(self.scale, self.scale)
            p.translate(-c)
        hint = "Click a format" if self.click_mode else "Drop on a format"
        if not self.title:
            line1, line2 = "Satsuma", hint
        elif not self.slots:
            line1, line2 = self.title, self.empty_reason
        elif 0 <= self.hover < len(self.slots):
            slot = self.slots[self.hover]
            line1, line2 = self.title, slot.reason or slot.tool.description
        elif self.hover == CENTER:
            line1, line2 = self.title, "Click here to cancel" if self.click_mode else "Let go here to cancel"
        else:
            line1, line2 = self.title, hint
        favs = set(self.settings.favorites)
        items = [Item(s.tool.label, s.ok, s.tool.key in favs) for s in self.slots]
        paint_wheel(p, c, self.R, items, self.hover, line1, line2, self.theme, self.settings.wheel_style)
        p.end()


class WheelPreview(QWidget):
    """A live, non-interactive wheel for the Look settings page."""
    DEMO = [Item("PNG"), Item("JPG", ok=False), Item("WEBP"), Item("PDF"), Item("Resize", favorite=True),
            Item("Cut out"), Item("Small")]
    TEXT = ["Convert to PNG", "Already JPG", "Convert to WebP", "Convert to PDF", "Make it bigger or smaller",
            "Remove the background", "Shrink for sharing"]

    def __init__(self, settings: Settings, theme: Theme):
        super().__init__()
        self.settings, self.theme = settings, theme
        self.hover = 0
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
        paint_wheel(p, c, R, self.DEMO, self.hover, "photo.jpg", self.TEXT[self.hover], self.theme,
                    self.settings.wheel_style)
        p.end()
