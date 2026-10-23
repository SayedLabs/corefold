"""Keyboard shortcuts that work in any app (Windows' RegisterHotKey), for the Clipboard, Capture and
Quick access. Fixed shortcuts are in SHORTCUTS; any other one is written like "ctrl+alt+space"
(see parse()), so a person can record their own."""
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


MODIFIERS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
MOD_NAMES = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
# key name -> virtual key (letters and digits are their own character)
KEYS = {"space": 0x20, "printscreen": VK_SNAPSHOT, "`": 0xC0, "pause": 0x13, "scrolllock": 0x91, "insert": 0x2D,
        "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
        ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC, "up": 0x26, "down": 0x28, "left": 0x25,
        "right": 0x27, **{f"f{n}": 0x6F + n for n in range(1, 25)}}
KEY_NAMES = {"space": "Space", "printscreen": "Print Screen", "pause": "Pause", "scrolllock": "Scroll Lock",
             "insert": "Insert", "home": "Home", "end": "End", "pageup": "Page Up", "pagedown": "Page Down",
             "up": "Up", "down": "Down", "left": "Left", "right": "Right"}
# keys that are fine on their own (nothing types them, and few apps use them)
ALONE_OK = {"pause", "scrolllock", *(f"f{n}" for n in range(13, 25))}
# shortcuts Windows or almost every app already uses: never taken, even if Windows would let us
RESERVED = {"alt+f4", "alt+tab", "alt+shift+tab", "alt+space", "alt+esc", "ctrl+alt+delete", "ctrl+shift+esc",
            "win+l", "win+d", "win+e", "win+r", "win+i", "win+a", "win+s", "win+v", "win+x", "win+tab", "win+space",
            "win+.", "win+;", "win+alt+space", "win+shift+s", "win+printscreen", "win+alt+r", "win+alt+g",
            "win+alt+printscreen", "win+alt+k", "win+alt+b", "win+alt+d", "win+ctrl+d", "win+ctrl+f4", "win+up",
            "win+down", "win+left", "win+right", "win+shift+left", "win+shift+right", "win+ctrl+left",
            "win+ctrl+right", "win+h", "win+k", "win+p", "win+u", "win+g", "win+m", "win+n", "win+w", "win+c",
            "win+t", "win+b", "win+o", "win+z", "win+j", "win+q", "win+f",
            *(f"ctrl+{k}" for k in "acvxyzsfnopwtqrhgbiu"), *(f"ctrl+shift+{k}" for k in "tnzvsp"),
            *(f"alt+{k}" for k in ("left", "right", "up", "down", "home", "enter")), "ctrl+space", "shift+space"}


def _split(combo: str) -> tuple[list[str], str]:
    parts = combo.lower().split("+")
    if combo.endswith("++"):  # (no shortcut uses "+" itself; "ctrl++" isn't valid)
        return [], ""
    return parts[:-1], parts[-1]


def normalize(combo: str) -> str:
    """The one way of writing a shortcut ("Alt+Ctrl+Z" -> "ctrl+alt+z"), or "" if it isn't one."""
    if combo in SHORTCUTS:
        return combo
    mods, key = _split(combo)
    if not key or any(m not in MODIFIERS for m in mods) or len(set(mods)) != len(mods):
        return ""
    if not (key in KEYS or (len(key) == 1 and (key.isascii() and key.isalnum()))):
        return ""
    return "+".join([m for m in MODIFIERS if m in mods] + [key])


def parse(combo: str) -> tuple[int, int] | None:
    """(modifiers, virtual key) for RegisterHotKey, or None for "off" or something that isn't a shortcut."""
    if combo in SHORTCUTS:
        return SHORTCUTS[combo]
    combo = normalize(combo)
    if not combo:
        return None
    mods, key = _split(combo)
    flags = 0
    for m in mods:
        flags |= MODIFIERS[m]
    return flags, KEYS[key] if key in KEYS else ord(key.upper())


def display(combo: str) -> str:
    """How a shortcut is written for people: "Ctrl + Alt + Space"."""
    if combo in SHORTCUT_NAMES:
        return SHORTCUT_NAMES[combo]
    combo = normalize(combo)
    if not combo:
        return ""
    mods, key = _split(combo)
    return " + ".join([MOD_NAMES[m] for m in mods] + [KEY_NAMES.get(key) or _key_char(key)])


def _key_char(key: str) -> str:
    """A symbol key as this keyboard writes it (the key left of 1 is ` in the US, ذ in Arabic, ² in
    French): the shortcut is the physical key, so it's named the way it's printed on it."""
    if len(key) == 1 and not key.isalnum() and os.name == "nt":
        try:
            ch = ctypes.windll.user32.MapVirtualKeyW(KEYS[key], 2) & 0xFFFF  # MAPVK_VK_TO_CHAR
            if ch and chr(ch).isprintable():
                return chr(ch).upper()
        except Exception:
            pass
    return key.upper()


PROBLEMS = {  # English, shown through tr()
    "bad": "That key can't be used for a shortcut.",
    "reserved": "Windows or most apps already use this shortcut.",
    "alone": "Add Ctrl, Alt or Win, so the key still types normally.",
    "shift": "Shift on its own types capitals: add Ctrl, Alt or Win.",
}


def problem(combo: str) -> str:
    """Why a recorded shortcut can't be used ("" if it can). English, shown through tr()."""
    combo = normalize(combo)
    if not combo or combo in SHORTCUTS:
        return PROBLEMS["bad"] if not combo else ""
    mods, key = _split(combo)
    if combo in RESERVED:
        return PROBLEMS["reserved"]
    if not mods and key not in ALONE_OK:
        return PROBLEMS["alone"]
    if mods == ["shift"]:
        return PROBLEMS["shift"]
    return ""


def types_on_some_keyboards(combo: str) -> bool:
    """Ctrl + Alt + a letter, digit or symbol is AltGr on many keyboards (German, French, Polish…): it
    types a character there, which the shortcut would take away."""
    mods, key = _split(normalize(combo))
    return set(mods) == {"ctrl", "alt"} and key not in KEYS or (set(mods) == {"ctrl", "alt"} and len(key) == 1)


def from_key_event(key: int, modifiers, native_vk: int = 0) -> str:
    """The shortcut being pressed in a Qt key event ("" while only modifiers are down)."""
    from PySide6.QtCore import Qt
    if key in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta, Qt.Key_AltGr, Qt.Key_Super_L,
               Qt.Key_Super_R, 0, Qt.Key_unknown):
        return ""
    by_vk = {vk: name for name, vk in KEYS.items()}
    name = ""
    if native_vk:  # Windows: the physical key, whatever the keyboard layout types with it
        if 0x30 <= native_vk <= 0x39 or 0x41 <= native_vk <= 0x5A:
            name = chr(native_vk).lower()
        else:
            name = by_vk.get(native_vk, "")
    if not name:
        qt_keys = {Qt.Key_Space: "space", Qt.Key_Print: "printscreen", Qt.Key_QuoteLeft: "`", Qt.Key_Pause: "pause",
                   Qt.Key_ScrollLock: "scrolllock", Qt.Key_Insert: "insert", Qt.Key_Home: "home", Qt.Key_End: "end",
                   Qt.Key_PageUp: "pageup", Qt.Key_PageDown: "pagedown", Qt.Key_Minus: "-", Qt.Key_Equal: "=",
                   Qt.Key_BracketLeft: "[", Qt.Key_BracketRight: "]", Qt.Key_Semicolon: ";",
                   Qt.Key_Apostrophe: "'", Qt.Key_Comma: ",", Qt.Key_Period: ".", Qt.Key_Slash: "/",
                   Qt.Key_Backslash: "\\", Qt.Key_Up: "up", Qt.Key_Down: "down", Qt.Key_Left: "left",
                   Qt.Key_Right: "right"}
        k = int(key)
        if Qt.Key_A <= k <= Qt.Key_Z or Qt.Key_0 <= k <= Qt.Key_9:
            name = chr(k).lower()
        elif Qt.Key_F1 <= k <= Qt.Key_F24:
            name = f"f{k - int(Qt.Key_F1) + 1}"
        else:
            name = qt_keys.get(key, "")
    if not name:
        return "?"
    m = modifiers
    mods = [n for n, flag in (("ctrl", Qt.ControlModifier), ("alt", Qt.AltModifier), ("shift", Qt.ShiftModifier),
                              ("win", Qt.MetaModifier)) if m & flag]
    return "+".join(mods + [name])


def _mask_modifier_release(u):
    """Windows never shows the app the key of a shortcut it caught, so when Alt (or Win) is let go
    the app thinks it was pressed on its own, and opens its menu bar (or the Start menu). An unused
    key, pressed and let go in between, stops that."""
    try:
        u.keybd_event(0xE8, 0, 0, 0)       # (0xE8 isn't any key)
        u.keybd_event(0xE8, 0, 0x0002, 0)  # KEYEVENTF_KEYUP
    except Exception:
        pass


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
        spec = parse(key)
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
                    if spec[0] & (MOD_ALT | MOD_WIN):
                        _mask_modifier_release(u)
                    self.pressed.emit()
            u.UnregisterHotKey(None, 1)

        t = threading.Thread(target=loop, name="corefold-key", daemon=True)
        t.start()
        ready.wait(3)
        self.ok = bool(result.get("ok"))
        if self.ok:
            self._thread, self._tid = t, result.get("tid", 0)
        else:
            log.info("the shortcut %s is used by another app", key)
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
