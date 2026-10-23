"""Windows' own commands for the quick menu and the wheel (sleep, restart, shut down, sign out, empty the
Recycle Bin), and "Keep a window on top" (the window in front stays above all the others until it's
switched back).

Loaded only when one of them is used. Restarting, shutting down, signing out and emptying the Recycle
Bin are always asked about first, in the quick menu (see quickmenu.py): never on one key press."""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import threading

from PySide6.QtCore import QObject, QRect, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

log = logging.getLogger("corefold")

# what's asked before doing it: (the question, the button that does it, its icon). English, shown through tr()
CONFIRM: dict[str, tuple[str, str, str]] = {
    "restart": ("Restart the PC?", "Restart now", "arrow_clockwise"),
    "shutdown": ("Shut down the PC?", "Shut down now", "power"),
    "signout": ("Sign out of Windows?", "Sign out now", "sign_out"),
    "recycle": ("Empty the Recycle Bin?", "Empty it", "delete"),
}
SYSTEM = ("sleep", "hibernate", "restart", "shutdown", "signout", "recycle")

_hibernate: bool | None = None


def hibernate_allowed() -> bool:
    """Hibernate is only offered on a PC that has it switched on (asked once)."""
    global _hibernate
    if _hibernate is None:
        _hibernate = False
        if os.name == "nt":
            try:
                _hibernate = bool(ctypes.windll.powrprof.IsPwrHibernateAllowed())
            except Exception:
                pass
    return _hibernate


def _start(cmd: list[str]):
    try:
        subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True)
    except OSError:
        log.exception("couldn't start %s", cmd[0])


def run(action_id: str) -> bool:
    """Sleep, hibernate, restart, shut down or sign out (asked about already, where that's needed)."""
    if os.name != "nt":
        return False
    log.info("quick access: Windows command %s", action_id)
    try:
        if action_id in ("sleep", "hibernate"):
            return bool(ctypes.windll.powrprof.SetSuspendState(action_id == "hibernate", False, False))
        if action_id == "restart":
            _start(["shutdown.exe", "/r", "/t", "0"])
        elif action_id == "shutdown":
            _start(["shutdown.exe", "/s", "/t", "0"])
        elif action_id == "signout":
            return bool(ctypes.windll.user32.ExitWindowsEx(0, 0))  # EWX_LOGOFF
        else:
            return False
        return True
    except Exception:
        log.exception("quick access: %s failed", action_id)
        return False


# ---------------------------------------------------------------------------- the Recycle Bin

class _RecycleInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("i64Size", ctypes.c_int64), ("i64NumItems", ctypes.c_int64)]


def recycle_bin() -> tuple[int, int]:
    """(items, bytes) in the Recycle Bin of every drive."""
    if os.name != "nt":
        return 0, 0
    info = _RecycleInfo()
    info.cbSize = ctypes.sizeof(info)
    try:
        if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
            return 0, 0
    except Exception:
        return 0, 0
    return int(info.i64NumItems), int(info.i64Size)


class RecycleEmptier(QObject):
    """Empties the Recycle Bin on a thread of its own (a full one can take a while) and says when it's done."""
    done = Signal(int)   # bytes freed

    def start(self):
        _, size = recycle_bin()

        def work():
            try:
                # (no Windows question, sound or progress: the quick menu already asked)
                ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)
            except Exception:
                log.exception("couldn't empty the Recycle Bin")
            self.done.emit(max(0, size - recycle_bin()[1]))

        threading.Thread(target=work, name="corefold-recycle", daemon=True).start()


# ---------------------------------------------------------------------------- keeping a window on top

GWL_EXSTYLE, WS_EX_TOPMOST = -20, 0x8
# the desktop, the taskbar, Start and Alt+Tab: never "the window in front"
NOT_WINDOWS = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
               "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow", "TopLevelWindowForOverflowXamlIsland",
               "ForegroundStaging", "MultitaskingViewFrame"}


def _user32():
    u = ctypes.windll.user32
    vp = ctypes.c_void_p
    u.GetForegroundWindow.restype = vp
    u.GetAncestor.argtypes, u.GetAncestor.restype = [vp, ctypes.c_uint], vp
    u.IsWindow.argtypes = [vp]
    u.IsWindowVisible.argtypes = [vp]
    u.GetClassNameW.argtypes = [vp, ctypes.c_wchar_p, ctypes.c_int]
    u.GetWindowTextW.argtypes = [vp, ctypes.c_wchar_p, ctypes.c_int]
    u.GetWindowThreadProcessId.argtypes = [vp, ctypes.POINTER(ctypes.c_ulong)]
    u.GetWindowLongW.argtypes = [vp, ctypes.c_int]
    u.SetWindowPos.argtypes = [vp, vp, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u.GetWindowRect.argtypes = [vp, ctypes.c_void_p]
    return u


def foreground() -> int:
    if os.name != "nt":
        return 0
    try:
        return _user32().GetForegroundWindow() or 0
    except Exception:
        return 0


def usable(hwnd: int) -> int:
    """The app window hwnd belongs to, or 0 if it's Corefold's own, the desktop or the taskbar."""
    if os.name != "nt" or not hwnd:
        return 0
    try:
        u = _user32()
        root = u.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT
        if not u.IsWindow(root) or not u.IsWindowVisible(root):
            return 0
        buf = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(root, buf, 256)
        if buf.value in NOT_WINDOWS:
            return 0
        pid = ctypes.c_ulong()
        u.GetWindowThreadProcessId(root, ctypes.byref(pid))
        if pid.value == os.getpid():
            return 0
        return int(root)
    except Exception:
        return 0


def title(hwnd: int) -> str:
    try:
        buf = ctypes.create_unicode_buffer(512)
        _user32().GetWindowTextW(hwnd, buf, 512)
        text = buf.value.strip()
    except Exception:
        text = ""
    return text if len(text) <= 48 else text[:46].rstrip() + "…"


def is_on_top(hwnd: int) -> bool:
    try:
        return bool(_user32().GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)
    except Exception:
        return False


def set_on_top(hwnd: int, on: bool) -> bool:
    """True if Windows did it (it doesn't for an app running as administrator when Corefold isn't)."""
    try:
        u = _user32()
        after = ctypes.c_void_p(-1 if on else -2)  # HWND_TOPMOST, HWND_NOTOPMOST
        u.SetWindowPos(hwnd, after, 0, 0, 0, 0, 0x1 | 0x2 | 0x10)  # NOSIZE | NOMOVE | NOACTIVATE
    except Exception:
        log.exception("couldn't change a window's place")
        return False
    return is_on_top(hwnd) == on


class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def frame(hwnd: int) -> QRect:
    """Where the window is on the screen, in Qt's pixels (its visible frame, without the invisible
    border Windows adds for resizing)."""
    r = _Rect()
    try:
        ok = ctypes.windll.dwmapi.DwmGetWindowAttribute(ctypes.c_void_p(hwnd), 9, ctypes.byref(r),
                                                       ctypes.sizeof(r)) == 0  # DWMWA_EXTENDED_FRAME_BOUNDS
        if not ok:
            _user32().GetWindowRect(hwnd, ctypes.byref(r))
    except Exception:
        return QRect()
    # Windows' pixels to Qt's: each screen keeps its corner, and is scaled from there
    for screen in QGuiApplication.screens():
        g, dpr = screen.geometry(), screen.devicePixelRatio()
        native = QRect(g.left(), g.top(), round(g.width() * dpr), round(g.height() * dpr))
        if native.contains(r.left + (r.right - r.left) // 2, r.top + (r.bottom - r.top) // 2):
            return QRect(round(g.left() + (r.left - g.left()) / dpr), round(g.top() + (r.top - g.top()) / dpr),
                         round((r.right - r.left) / dpr), round((r.bottom - r.top) / dpr))
    return QRect(r.left, r.top, r.right - r.left, r.bottom - r.top)


class Flash(QWidget):
    """A coloured border round a window for a moment, so it's clear which one stays on top. Clicks go
    through it, and it's gone after a second."""
    PAD = 3

    def __init__(self, rect: QRect, color: QColor, radius: float = 8):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.color, self.radius = QColor(color), radius
        self.setGeometry(rect.adjusted(-self.PAD, -self.PAD, self.PAD, self.PAD))
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(1100)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.valueChanged.connect(self._step)
        self.anim.finished.connect(self._done)
        self.alpha = 1.0

    def flash(self):
        self.show()
        self.anim.start()

    def _step(self, v):
        v = float(v)
        self.alpha = 1.0 if v < 0.55 else max(0.0, 1 - (v - 0.55) / 0.45)  # (stays, then fades)
        self.update()

    def _done(self):
        self.hide()
        self.deleteLater()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self.color)
        c.setAlphaF(self.alpha)
        w = 4.0
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(w / 2, w / 2, -w / 2, -w / 2), self.radius, self.radius)
        p.setPen(QPen(c, w))
        p.drawPath(path)
        p.end()
