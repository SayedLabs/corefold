"""Corefold Colors: pick any colour on the screen.

A shortcut freezes every screen (like Capture does) and a round magnifier follows the mouse, with the
pixel under it outlined and its HEX underneath. A click or Enter copies it as HEX, RGB or HSL; the
arrow keys move one pixel at a time; Esc or a right-click gives up. The picture is the screen's own
pixels (not scaled), so the colour is exact on every monitor and at every scale."""
from __future__ import annotations

import colorsys
import ctypes
import logging
import os

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .i18n import tr

log = logging.getLogger("corefold")

HISTORY_MAX = 24
ZOOMS = (7, 9, 11, 15, 21, 31)   # pixels across the magnifier (the mouse wheel goes through them)
LOUPE = 150                      # the magnifier's size, in the screen's own units


# ----------------------------------------------------------------------------- colours as text

def hex_of(color: QColor) -> str:
    return f"#{color.red():02X}{color.green():02X}{color.blue():02X}"


def rgb_text(hex_: str) -> str:
    c = QColor(hex_)
    return f"rgb({c.red()}, {c.green()}, {c.blue()})"


def hsl_text(hex_: str) -> str:
    """As CSS writes it: hue in degrees, saturation and lightness in percent (rounded)."""
    c = QColor(hex_)
    h, l, s = colorsys.rgb_to_hls(c.red() / 255, c.green() / 255, c.blue() / 255)
    return f"hsl({round(h * 360) % 360}, {round(s * 100)}%, {round(l * 100)}%)"


def as_text(hex_: str, fmt: str) -> str:
    if fmt == "rgb":
        return rgb_text(hex_)
    if fmt == "hsl":
        return hsl_text(hex_)
    return hex_.upper()


def remembered(history: list, hex_: str) -> list:
    """The history with this colour added on top (newest first, not twice in a row, at most 24)."""
    hex_ = hex_.upper()
    if history and history[0] == hex_:
        return list(history)
    return [hex_, *history][:HISTORY_MAX]


# ----------------------------------------------------------------------------- where the mouse is

def _cursor_native() -> tuple[int, int] | None:
    """The mouse in Windows' own pixels (exact at any scale), or None elsewhere."""
    if os.name != "nt":
        return None
    import ctypes.wintypes as wt
    pt = wt.POINT()
    if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
        return pt.x, pt.y
    return None


def _set_cursor_native(x: int, y: int) -> bool:
    if os.name != "nt":
        return False
    return bool(ctypes.windll.user32.SetCursorPos(int(x), int(y)))


# ----------------------------------------------------------------------------- the picker

class Loupe(QWidget):
    """The frozen picture of one monitor, with the magnifier while the mouse is on it."""

    def __init__(self, picker: "Picker", screen, shot):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.picker = picker
        self.screen_ = screen
        self.shot = shot
        self.ratio = shot.devicePixelRatio() or 1.0
        img = shot.toImage().convertToFormat(QImage.Format_RGB32)
        img.setDevicePixelRatio(1.0)
        self.img = img                      # the screen's own pixels: what's picked comes from here
        self.geo = screen.geometry()
        self.at: QPoint | None = None       # the pixel under the mouse (in img), while it's on this screen
        self.zoom = ZOOMS[2]
        self.pressed = False
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.BlankCursor)      # (the magnifier is the pointer)
        self.setMouseTracking(True)
        self.setGeometry(self.geo)

    # ---- where things are

    def native_rect(self) -> QRect:
        """This monitor in Windows' pixels (Qt keeps each screen's corner where Windows has it)."""
        return QRect(self.geo.x(), self.geo.y(), self.img.width(), self.img.height())

    def color(self) -> QColor | None:
        if self.at is None:
            return None
        return self.img.pixelColor(self.at)

    def _centre(self) -> QPointF:
        """The middle of the pixel being looked at, in this window's units."""
        return QPointF((self.at.x() + 0.5) / self.ratio, (self.at.y() + 0.5) / self.ratio)

    def _area(self) -> QRect:
        """What the magnifier and its label cover (to redraw only that)."""
        if self.at is None:
            return QRect()
        c = self._centre().toPoint()
        half = LOUPE // 2 + 6
        return QRect(c.x() - half - 70, c.y() - half - 50, 2 * half + 140, 2 * half + 100)

    def look_at(self, pixel: QPoint | None):
        if pixel is not None:
            pixel = QPoint(min(max(pixel.x(), 0), self.img.width() - 1), min(max(pixel.y(), 0), self.img.height() - 1))
        if pixel == self.at:
            return
        old = self._area()
        self.at = pixel
        self.update(old.united(self._area()))

    # ---- drawing

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(QPoint(0, 0), self.shot)
        if self.picker.hint_on is self:
            self._draw_hint(p)
        if self.at is None:
            return
        p.setRenderHint(QPainter.Antialiasing)
        centre = self._centre()
        n = self.zoom
        cell = LOUPE / n
        radius = LOUPE / 2
        circle = QPainterPath()
        circle.addEllipse(centre, radius, radius)
        p.save()
        p.setClipPath(circle)
        p.fillPath(circle, QColor(24, 24, 26))
        tile = self.img.copy(QRect(self.at.x() - n // 2, self.at.y() - n // 2, n, n))
        grid = QRectF(centre.x() - LOUPE / 2, centre.y() - LOUPE / 2, LOUPE, LOUPE)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.drawImage(grid, tile)
        if cell >= 6:  # the lines between pixels
            p.setRenderHint(QPainter.Antialiasing, False)
            p.setPen(QPen(QColor(128, 128, 128, 70), 1))
            for i in range(1, n):
                x = grid.left() + i * cell
                y = grid.top() + i * cell
                p.drawLine(QPointF(x, grid.top()), QPointF(x, grid.bottom()))
                p.drawLine(QPointF(grid.left(), y), QPointF(grid.right(), y))
            p.setRenderHint(QPainter.Antialiasing)
        # the pixel that's picked: outlined in black and white, so it shows on any colour
        mid = QRectF(centre.x() - cell / 2, centre.y() - cell / 2, cell, cell)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 200), 3))
        p.drawRect(mid)
        p.setPen(QPen(QColor(255, 255, 255), 1.5))
        p.drawRect(mid)
        p.restore()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 140), 5))
        p.drawEllipse(centre, radius + 2, radius + 2)
        p.setPen(QPen(QColor(255, 255, 255), 2))   # (a white edge, so a dark colour on a dark screen still shows)
        p.drawEllipse(centre, radius + 1.5, radius + 1.5)
        color = self.color()
        p.setPen(QPen(color if color is not None else QColor(255, 255, 255), 3))
        p.drawEllipse(centre, radius - 1, radius - 1)
        self._draw_label(p, centre, radius, color)

    def _draw_label(self, p: QPainter, centre: QPointF, radius: float, color: QColor | None):
        """The colour's HEX under the magnifier (above it at the bottom of the screen)."""
        if color is None:
            return
        text = hex_of(color)
        f = QFont(self.font())
        f.setPointSizeF(10.5)
        f.setBold(True)
        p.setFont(f)
        fm = p.fontMetrics()
        sw = 16
        w, h = fm.horizontalAdvance(text) + sw + 30, max(30, fm.height() + 12)
        top = centre.y() + radius + 10
        if top + h > self.height() - 4:
            top = centre.y() - radius - 10 - h
        box = QRectF(centre.x() - w / 2, top, w, h)
        if box.left() < 4:
            box.moveLeft(4)
        if box.right() > self.width() - 4:
            box.moveRight(self.width() - 4)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 22, 24, 235))
        p.drawRoundedRect(box, h / 2, h / 2)
        dot = QRectF(box.left() + 9, box.center().y() - sw / 2, sw, sw)
        p.setBrush(color)
        p.setPen(QPen(QColor(255, 255, 255, 160), 1))
        p.drawEllipse(dot)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(dot.right() + 6, box.top(), box.right() - dot.right() - 12, box.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, text)

    def _draw_hint(self, p: QPainter):
        hint = tr("Click or press Enter to pick. The arrow keys move one pixel, the mouse wheel zooms. Esc cancels.")
        f = QFont(self.font())
        f.setPointSizeF(10)
        p.setFont(f)
        tw = min(p.fontMetrics().horizontalAdvance(hint) + 28, self.width() - 32)
        box = QRect((self.width() - tw) // 2, 28, tw, 34)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 22, 24, 225))
        p.drawRoundedRect(box, 10, 10)
        p.setPen(QColor("#fff"))
        p.drawText(box.adjusted(10, 0, -10, 0), Qt.AlignCenter, p.fontMetrics().elidedText(hint, Qt.ElideRight,
                                                                                            tw - 20))

    # ---- the mouse and keys

    def showEvent(self, e):
        super().showEvent(e)
        self.picker.follow()

    def enterEvent(self, e):
        self.activateWindow()
        self.setFocus()
        self.picker.follow()

    def mouseMoveEvent(self, e):
        self.picker.follow()

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.picker.finish(None)
        elif e.button() == Qt.LeftButton:
            self.picker.follow()
            self.pressed = True

    def mouseReleaseEvent(self, e):
        # (picked when the button comes up, so the click doesn't land in the app underneath)
        if e.button() == Qt.LeftButton and self.pressed:
            self.picker.finish(self)

    def wheelEvent(self, e):
        steps = 1 if e.angleDelta().y() < 0 else -1 if e.angleDelta().y() > 0 else 0
        i = min(max(ZOOMS.index(self.zoom) + steps, 0), len(ZOOMS) - 1)
        for loupe in self.picker.loupes:
            loupe.zoom = ZOOMS[i]
        self.update(self._area())

    def keyPressEvent(self, e):
        self.picker.key(e.key(), bool(e.modifiers() & Qt.ShiftModifier))

    def cancel(self):
        """Esc, from outside (a test, or the app closing)."""
        self.picker.finish(None)


class Picker(QObject):
    """Freezes every screen and lets you pick one pixel on any of them. Calls on_picked(QColor, the
    point on screen in Qt's units) or on_picked(None, None)."""

    def __init__(self, on_picked):
        super().__init__()
        self.on_picked = on_picked
        self.loupes: list[Loupe] = []
        self.answered = False
        self.hint_on: Loupe | None = None

    def start(self):
        shots = [(s, s.grabWindow(0)) for s in QGuiApplication.screens()]
        for screen, shot in shots:
            if shot.isNull():
                continue
            self.loupes.append(Loupe(self, screen, shot))
        if not self.loupes:
            self.finish(None)
            return
        under = QGuiApplication.screenAt(QCursor.pos())
        self.hint_on = next((lp for lp in self.loupes if lp.screen_ is under), self.loupes[0])
        for loupe in self.loupes:
            loupe.show()
        self.hint_on.activateWindow()
        self.hint_on.raise_()
        self.hint_on.setFocus()
        self.follow()

    def active(self) -> Loupe | None:
        return next((lp for lp in self.loupes if lp.at is not None), None)

    def follow(self):
        """Puts the magnifier on the pixel under the mouse (on whichever monitor that is)."""
        if self.answered:
            return
        native = _cursor_native()
        pos = QCursor.pos() if native is None else None
        for loupe in self.loupes:
            if native is not None:
                r = loupe.native_rect()
                inside = r.contains(native[0], native[1])
                pixel = QPoint(native[0] - r.x(), native[1] - r.y())
            else:  # (not Windows: Qt's units, scaled to the picture's)
                inside = loupe.geo.contains(pos)
                pixel = QPoint(int((pos.x() - loupe.geo.x()) * loupe.ratio), int((pos.y() - loupe.geo.y()) * loupe.ratio))
            loupe.look_at(pixel if inside else None)

    def move(self, dx: int, dy: int):
        """One pixel (ten with Shift) with the arrow keys: the mouse goes there too."""
        loupe = self.active()
        if loupe is None:
            return
        target = QPoint(loupe.at.x() + dx, loupe.at.y() + dy)
        r = loupe.native_rect()
        if _set_cursor_native(r.x() + target.x(), r.y() + target.y()):
            self.follow()
        else:
            loupe.look_at(target)

    def key(self, key: int, shift: bool):
        step = 10 if shift else 1
        moves = {Qt.Key_Left: (-step, 0), Qt.Key_Right: (step, 0), Qt.Key_Up: (0, -step), Qt.Key_Down: (0, step)}
        if key == Qt.Key_Escape:
            self.finish(None)
        elif key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.finish(self.active())
        elif key in moves:
            self.move(*moves[key])

    def finish(self, loupe: Loupe | None):
        if self.answered:
            return
        color = where = None
        if loupe is not None:
            color = loupe.color()
            if color is not None:
                where = loupe.mapToGlobal(loupe._centre().toPoint())
        self.answered = True
        for lp in self.loupes:
            lp.hide()
            lp.close()
        self.loupes = []
        QTimer.singleShot(0, lambda: self.on_picked(color, where))


# ----------------------------------------------------------------------------- "copied", with the colour

class ColorToast(QWidget):
    """A small note by where you picked: the colour and what was copied."""

    def __init__(self, theme):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.theme = theme
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.color = QColor()
        self.text = ""
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def show_for(self, color: QColor, text: str, where: QPoint | None, ms: int):
        self.color, self.text = color, text
        f = QFont(self.font())
        f.setPointSizeF(10)
        self.setFont(f)
        fm = self.fontMetrics()
        title = tr("Colour copied")
        w = 16 + 34 + 12 + max(fm.horizontalAdvance(title), fm.horizontalAdvance(text) + 8) + 22
        h = max(58, 2 * fm.height() + 24)
        self.resize(w, h)
        screen = QGuiApplication.screenAt(where) if where is not None else None
        screen = screen or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        if where is None:
            where = QPoint(area.center().x(), area.bottom() - 120)
        x = where.x() - w // 2
        y = where.y() + LOUPE // 2 + 54 if where.y() + LOUPE // 2 + 54 + h < area.bottom() else \
            where.y() - LOUPE // 2 - 54 - h
        self.move(min(max(x, area.left() + 8), area.right() - w - 8), min(max(y, area.top() + 8), area.bottom() - h - 8))
        self.show()
        self.raise_()
        self.update()
        self.timer.start(ms)

    def mousePressEvent(self, e):
        self.hide()

    def paintEvent(self, e):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(t.border, 1))
        p.setBrush(t.panel)
        p.drawRoundedRect(r, 12, 12)
        rtl = self.layoutDirection() == Qt.RightToLeft
        dot = QRectF(r.right() - 16 - 34 if rtl else r.left() + 16, r.center().y() - 17, 34, 34)
        p.setBrush(self.color)
        p.setPen(QPen(t.line_strong or t.border, 1))
        p.drawEllipse(dot)
        words = QRectF(r.left() + 16, r.top(), r.width() - 16 - 34 - 12 - 16, r.height()) if rtl else \
            QRectF(dot.right() + 12, r.top(), r.right() - dot.right() - 24, r.height())
        fm = p.fontMetrics()
        half = words.height() / 2
        align = Qt.AlignRight if rtl else Qt.AlignLeft
        p.setPen(t.muted)
        p.drawText(QRectF(words.left(), words.top() + half - fm.height() - 1, words.width(), fm.height()),
                   align | Qt.AlignVCenter, tr("Colour copied"))
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        p.setPen(t.text)
        p.drawText(QRectF(words.left(), words.top() + half + 1, words.width(), fm.height() + 2),
                   align | Qt.AlignVCenter, self.text)


# ----------------------------------------------------------------------------- the tool

class ColorsTool(QObject):
    """Corefold Colors while it's in use: its shortcut, picking, and the colours picked lately."""
    changed = Signal()   # the colours picked lately changed

    def __init__(self, controller):
        super().__init__()
        from .hotkey import Hotkey
        self.c = controller
        self.picker: Picker | None = None
        self.toast: ColorToast | None = None
        self._hidden = None
        self.key = Hotkey()
        self.key.pressed.connect(self.pick, Qt.ConnectionType.QueuedConnection)
        self.apply_settings()

    @property
    def settings(self):
        return self.c.settings

    def apply_settings(self) -> bool:
        return self.key.set(self.settings.colors_shortcut)

    def stop(self):
        self.key.stop()
        if self.picker is not None:
            self.picker.on_picked = lambda *_: None  # (the tool is going: nothing to come back to)
            self.picker.finish(None)
            self.picker = None
        if self.toast is not None:
            self.toast.close()
            self.toast.deleteLater()
            self.toast = None

    def picking(self) -> bool:
        return self.picker is not None

    # ---- picking

    def pick(self):
        """Corefold's window steps aside, every screen freezes, and a click picks a colour."""
        if self.picker is not None:
            return
        if self.toast is not None:
            self.toast.hide()
        self.picker = Picker(self._picked)
        wait = 0
        w = self.c.window_if_open()
        if w is not None and w.isVisible() and not w.isMinimized():
            self._hidden = w
            w.hide()
            wait = 250  # (until Windows has taken it off the screen)
        QTimer.singleShot(wait or 10, self.picker.start)

    def _picked(self, color: QColor | None, where: QPoint | None):
        self.picker = None
        w, self._hidden = self._hidden, None
        if w is not None:
            w.show()
        if color is None:
            return
        hex_ = hex_of(color)
        text = self.copy(hex_)
        self.remember(hex_)
        if self.toast is None:
            self.toast = ColorToast(self.c.theme)
        self.toast.theme = self.c.theme
        self.toast.setLayoutDirection(QGuiApplication.layoutDirection())
        self.toast.show_for(color, text, where, int(2600 * getattr(self.c.toast, "time_scale", 1.0)))

    def copy(self, hex_: str, fmt: str | None = None) -> str:
        """Puts the colour on the clipboard as text, in the chosen format. Returns the text."""
        text = as_text(hex_, fmt or self.settings.colors_format)
        QGuiApplication.clipboard().setText(text)
        return text

    # ---- the colours picked lately

    def remember(self, hex_: str):
        self.settings.colors_history = remembered(self.settings.colors_history, hex_)
        self._changed()

    def forget(self, hex_: str):
        self.settings.colors_history = [h for h in self.settings.colors_history if h != hex_]
        self._changed()

    def clear(self):
        self.settings.colors_history = []
        self._changed()

    def _changed(self):
        self.c.settings_changed()
        self.changed.emit()
