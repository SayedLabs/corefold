"""Keyboard shortcuts that work in any app (Windows' RegisterHotKey), for the Clipboard and Capture."""
from __future__ import annotations

import ctypes
import logging
import os
import threading

from PySide6.QtCore import QObject, Signal

log = logging.getLogger("corefold")

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
VK_SNAPSHOT = 0x2C
# key -> (modifiers, virtual key); "off" is no shortcut
SHORTCUTS = {
    "win+alt+v": (MOD_WIN | MOD_ALT, 0x56), "ctrl+shift+v": (MOD_CONTROL | MOD_SHIFT, 0x56),
    "ctrl+alt+v": (MOD_CONTROL | MOD_ALT, 0x56), "ctrl+`": (MOD_CONTROL, 0xC0),
    "ctrl+printscreen": (MOD_CONTROL, VK_SNAPSHOT), "printscreen": (0, VK_SNAPSHOT),
    "win+shift+a": (MOD_WIN | MOD_SHIFT, 0x41), "ctrl+shift+printscreen": (MOD_CONTROL | MOD_SHIFT, VK_SNAPSHOT),
    "win+shift+r": (MOD_WIN | MOD_SHIFT, 0x52), "off": None,
}
SHORTCUT_NAMES = {
    "win+alt+v": "Win + Alt + V", "ctrl+shift+v": "Ctrl + Shift + V", "ctrl+alt+v": "Ctrl + Alt + V",
    "ctrl+`": "Ctrl + `", "ctrl+printscreen": "Ctrl + Print Screen", "printscreen": "Print Screen",
    "win+shift+a": "Win + Shift + A", "ctrl+shift+printscreen": "Ctrl + Shift + Print Screen",
    "win+shift+r": "Win + Shift + R",
}


class Hotkey(QObject):
    """The shortcut that opens the clipboard, from any app. Windows tells a tiny thread of its own
    (asleep until the keys are pressed), so it works even while a Corefold window is busy."""
    pressed = Signal()

    def __init__(self):
        super().__init__()
        self._thread: threading.Thread | None = None
        self._tid = 0
        self.key = "off"
        self.ok = True

    def set(self, key: str) -> bool:
        """Starts listening for this shortcut. False if another app already uses it."""
        if key == self.key and (self._thread is not None or key == "off"):
            return self.ok
        self.stop()
        self.key = key
        spec = SHORTCUTS.get(key)
        if not spec or os.name != "nt":
            self.ok = True
            return True
        ready, result = threading.Event(), {}

        def loop():
            import ctypes.wintypes as wt
            u = ctypes.windll.user32
            result["tid"] = ctypes.windll.kernel32.GetCurrentThreadId()
            result["ok"] = bool(u.RegisterHotKey(None, 1, spec[0] | MOD_NOREPEAT, spec[1]))
            ready.set()
            if not result["ok"]:
                return
            msg = wt.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:  # WM_HOTKEY
                    self.pressed.emit()
            u.UnregisterHotKey(None, 1)

        t = threading.Thread(target=loop, name="corefold-clipboard-key", daemon=True)
        t.start()
        ready.wait(3)
        self.ok = bool(result.get("ok"))
        if self.ok:
            self._thread, self._tid = t, result.get("tid", 0)
        else:
            log.info("the clipboard shortcut %s is used by another app", key)
        return self.ok

    def stop(self):
        t = self._thread
        self._thread = None
        if t is not None and t.is_alive():
            try:
                ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)  # WM_QUIT
            except Exception:
                pass
            t.join(1)
        self.key = "off"
