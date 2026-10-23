"""The quick wheel: the things a person picked, round the mouse, from any app.

Hold its shortcut, point at a slice and let go (like the weapon wheel in a game: only the direction
counts, so a flick is enough). Or tap the shortcut, or click the small round button if it's switched
on, and then click a slice or press its number. Esc, or a click in the middle or outside, closes it.

It's drawn exactly like the Converter's wheel (wheel.paint_wheel), in the style picked for it."""
from __future__ import annotations

import ctypes
import logging
import math
import os
import time

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget

from . import icons, quick, winutil
from .i18n import tr
from .settings import WHEEL_SIZES
from .wheel import CENTER, POP, Item, Look, ease_out_back, inner_radius, paint_wheel

TAP_S = 0.28        # let go sooner than this and the wheel stays open for clicking
WATCH_MS = 16
VK_ESCAPE = 0x1B
log = logging.getLogger("corefold")


class KeyTrap:
    """While the wheel is open, Esc and the number keys are for the wheel, not for the app under it
    (which keeps the keyboard, so what's picked happens there). Windows lets an app see keys first
    with a hook; it's only there while the wheel is open."""

    def __init__(self, on_key):
        self.on_key = on_key   # (virtual key) -> True if the wheel used it
        self._hook = None
        self._proc = None
        self._swallowed: set[int] = set()

    def start(self) -> bool:
        if self._hook or os.name != "nt":
            return bool(self._hook)
        import ctypes.wintypes as wt

        class KBD(ctypes.Structure):
            _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD),
                        ("dwExtraInfo", ctypes.c_void_p)]
        u = ctypes.windll.user32
        proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
        u.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wt.WPARAM, wt.LPARAM]
        u.CallNextHookEx.restype = ctypes.c_ssize_t
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, wt.DWORD]
        u.SetWindowsHookExW.restype = ctypes.c_void_p
        u.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]

        def proc(code, wparam, lparam):
            try:
                if code == 0:
                    vk = ctypes.cast(lparam, ctypes.POINTER(KBD)).contents.vkCode
                    if wparam in (0x0100, 0x0104):  # WM_KEYDOWN, WM_SYSKEYDOWN
                        if self.on_key(vk):
                            self._swallowed.add(vk)
                            return 1
                    elif vk in self._swallowed:  # ... and its key-up
                        self._swallowed.discard(vk)
                        return 1
            except Exception:
                pass
            return u.CallNextHookEx(None, code, wparam, lparam)
        self._proc = proc_type(proc)
        self._hook = u.SetWindowsHookExW(13, self._proc, None, 0)  # WH_KEYBOARD_LL
        if not self._hook:
            log.info("couldn't watch the keys for the quick wheel")
            self._proc = None
        return bool(self._hook)

    def stop(self):
        if self._hook:
            try:
                ctypes.windll.user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
        self._hook = None
        self._proc = None
        self._swallowed.clear()


class LauncherWheel(QWidget):
    def __init__(self, qa: "quick.QuickAccess"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.qa = qa
        self.theme = qa.c.theme
        self.entries: list[quick.Entry] = []
        self.hover = -1
        self.R = WHEEL_SIZES["medium"]
        self.scale = 1.0
        self.hold_vk = 0       # the shortcut's key, while it's held
        self.opened_at = 0.0
        self._down_at_open = False
        self._keys_down: set[str] = set()
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(160)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.valueChanged.connect(self._animate)
        self.watch = QTimer(self)
        self.watch.timeout.connect(self._watch)
        self.trap = KeyTrap(self._trapped)
        self._pending_key = 0

    @property
    def settings(self):
        return self.qa.c.settings

    @property
    def holding(self) -> bool:
        return bool(self.hold_vk)

    def popup(self, hold_vk: int = 0, center: QPoint | None = None):
        s = self.settings
        self.entries = quick.entries(s, s.quick_wheel_items)[:quick.MAX_WHEEL]
        if not self.entries:  # nothing picked (or all of it removed): at least a way to pick some
            self.entries = [quick.Entry("settings-quick", tr("Choose what's on the wheel"), "settings")]
        self.hover = -1
        self.hold_vk = hold_vk
        self.R = WHEEL_SIZES.get(s.wheel_size, WHEEL_SIZES["medium"])
        side = 2 * (self.R + POP) + 14
        self.setFixedSize(side, side)
        pos = center or QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        if center is None and s.wheel_position == "center":
            pos = g.center()
        x = min(max(pos.x() - side // 2, g.left()), g.right() - side)
        y = min(max(pos.y() - side // 2, g.top()), g.bottom() - side)
        self.move(x, y)
        if s.animations:
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
        self.opened_at = time.monotonic()
        self._down_at_open = winutil.any_button_down()
        self._keys_down = set()
        self._key_pressed()  # keys already down when it opened don't count
        self._pending_key = 0
        self.trap.start()
        self.watch.start(WATCH_MS)

    def _animate(self, v):
        v = float(v)
        self.scale = 0.82 + 0.18 * ease_out_back(v)
        self.setWindowOpacity(min(1.0, v * 1.8))
        self.update()

    def hideEvent(self, e):
        self.watch.stop()
        self.trap.stop()
        self.hold_vk = 0
        self.qa.c.popup_closed()
        super().hideEvent(e)

    def _trapped(self, vk: int) -> bool:
        """A key went down while the wheel is open (from the hook: decide fast, act a moment later)."""
        if vk == VK_ESCAPE:
            self._pending_key = vk
            return True
        if self.holding:
            return False
        number = vk - 0x30 if 0x31 <= vk <= 0x39 else vk - 0x60 if 0x61 <= vk <= 0x69 else 0
        if number and self._numbers_on() and number <= len(self.entries):
            self._pending_key = vk
            return True
        return False

    # ---- pointing

    def hit(self, pos: QPointF, anywhere: bool = False) -> int:
        """The slice at pos (in the wheel's own pixels). anywhere=True: only the direction counts."""
        dx, dy = pos.x() - self.width() / 2, pos.y() - self.height() / 2
        dist = math.hypot(dx, dy) / max(self.scale, 0.01)
        if dist < inner_radius(self.R, self.settings.wheel_style):
            return CENTER
        n = len(self.entries)
        if not n or (dist > self.R + POP and not anywhere):
            return -1
        seg = 360 / n
        angle = math.degrees(math.atan2(dx, -dy)) % 360  # 0 = top, clockwise
        return int(((angle + seg / 2) % 360) // seg) % n

    def _set_hover(self, idx: int):
        if idx != self.hover:
            self.hover = idx
            self.update()

    def _numbers_on(self) -> bool:
        return not self.holding and getattr(self.settings, "wheel_numbers", True)

    def _key_pressed(self) -> str:
        now = set()
        for d in range(10):
            if winutil.key_down(0x30 + d) or winutil.key_down(0x60 + d):
                now.add(str(d))
        new = now - self._keys_down
        self._keys_down = now
        return min(new) if new else ""

    def _watch(self):
        key, self._pending_key = self._pending_key, 0
        if key == VK_ESCAPE or (not self.trap._hook and winutil.key_down(winutil.VK_ESCAPE)):
            self.hide()
            return
        if key:
            self.choose((key - 0x31) if key < 0x60 else (key - 0x61))
            return
        local = self.mapFromGlobal(QCursor.pos())
        if self.holding:
            self._set_hover(self.hit(QPointF(local), anywhere=True))
            if not winutil.key_down(self.hold_vk):  # let go
                self.hold_vk = 0
                if time.monotonic() - self.opened_at < TAP_S:
                    self._set_hover(-1)  # a tap: the wheel stays open for a click
                    self.update()
                elif self.hover >= 0:
                    self.choose(self.hover)
                else:
                    self.hide()
            return
        if self._numbers_on() and not self.trap._hook:  # (no hook: the keys are only watched)
            key = self._key_pressed()
            if key and key != "0" and int(key) <= len(self.entries):
                self.choose(int(key) - 1)
                return
        down = winutil.any_button_down()
        if self._down_at_open:  # wait for the click that opened it to finish
            self._down_at_open = down
            return
        if down and not self.geometry().contains(QCursor.pos()):
            self.hide()

    def choose(self, idx: int):
        if not 0 <= idx < len(self.entries):
            return
        action = self.entries[idx].id
        self.hide()
        if action == "settings-quick":
            self.qa.c.show_window("Quick")
        else:
            self.qa.run(action)

    def mouseMoveEvent(self, e):
        if not self.holding:
            self._set_hover(self.hit(e.position()))

    def mouseReleaseEvent(self, e):
        if self.holding or e.button() != Qt.LeftButton:
            return
        idx = self.hit(e.position())
        if idx >= 0:
            self.choose(idx)
        else:
            self.hide()

    # ---- drawing

    def paintEvent(self, _):
        p = QPainter(self)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self.scale != 1.0:
            p.translate(c)
            p.scale(self.scale, self.scale)
            p.translate(-c)
        numbers = self._numbers_on()
        items = []
        for i, e in enumerate(self.entries):
            items.append(Item(e.label, icon=e.icon, number=str(i + 1) if numbers and i < 9 else ""))
        line1 = tr("Quick wheel")
        if 0 <= self.hover < len(self.entries):
            line2 = self.entries[self.hover].label
        elif self.hover == CENTER:
            line2 = tr("Let go here to cancel") if self.holding else tr("Click here to cancel")
        elif self.holding:
            line2 = tr("Point at one and let go")
        else:
            line2 = tr("Click one or press its number") if numbers else tr("Click one")
        paint_wheel(p, c, self.R, items, self.hover, line1, line2, self.theme, Look.of(self.settings))
        p.end()


class FloatingButton(QWidget):
    """A small round button that stays on top of everything and opens the quick wheel. Drag it
    anywhere; right-click it to hide it."""
    SIZE = 46

    def __init__(self, qa: "quick.QuickAccess"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self.SIZE + 8, self.SIZE + 8)
        self.setToolTip(tr("Quick wheel"))
        self.setCursor(Qt.PointingHandCursor)
        self.qa = qa
        self.theme = qa.c.theme
        self._press: QPoint | None = None
        self._start = QPoint()
        self._dragged = False
        self._hover = False

    def show_saved(self):
        pos = self.qa.c.settings.quick_button_pos
        screen = QGuiApplication.primaryScreen().availableGeometry()
        if len(pos) == 2 and any(s.availableGeometry().contains(QPoint(pos[0] + 20, pos[1] + 20))
                                 for s in QGuiApplication.screens()):
            self.move(pos[0], pos[1])
        else:  # to begin with: on the right edge, a little above the middle
            self.move(screen.right() - self.width() - 6, screen.top() + screen.height() * 2 // 5)
        self.show()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._start = self.pos()
            self._dragged = False

    def mouseMoveEvent(self, e):
        if self._press is None:
            return
        delta = e.globalPosition().toPoint() - self._press
        if self._dragged or delta.manhattanLength() > 6:
            self._dragged = True
            self.move(self._keep_on_screen(self._start + delta))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.RightButton:
            self._menu(e.globalPosition().toPoint())
            return
        if self._press is None:
            return
        self._press = None
        if self._dragged:
            s = self.qa.c.settings
            s.quick_button_pos = [self.x(), self.y()]
            self.qa.c.settings_changed()
            return
        self.qa.open_wheel_at(self.geometry().center())

    def _keep_on_screen(self, p: QPoint) -> QPoint:
        screen = QGuiApplication.screenAt(p + QPoint(self.width() // 2, self.height() // 2)) \
            or QGuiApplication.primaryScreen()
        g: QRect = screen.availableGeometry()
        return QPoint(min(max(p.x(), g.left()), g.right() - self.width()),
                      min(max(p.y(), g.top()), g.bottom() - self.height()))

    def _menu(self, at: QPoint):
        m = QMenu(self)
        m.addAction(tr("Quick access settings"), lambda: self.qa.c.show_window("Quick"))
        m.addAction(tr("Hide this button"), self._hide_button)
        m.exec(at)

    def _hide_button(self):
        self.qa.c.settings.quick_button = False
        self.qa.c.settings_changed()

    def paintEvent(self, _):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(4, 4, -4, -4)
        p.setOpacity(1.0 if self._hover else 0.86)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(r.adjusted(-2, 0, 2, 3))
        tok = getattr(t, "tokens", None)
        p.setBrush(tok.raised if tok is not None else t.panel)
        p.setPen(QPen(t.accent if self._hover else t.line_strong, 1.5))
        p.drawEllipse(r)
        inner = r.adjusted(13, 13, -13, -13)
        icons.paint(p, inner, "target", t.accent)
        p.end()
