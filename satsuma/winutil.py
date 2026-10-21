"""Small Windows API helpers (keyboard/mouse state, window under cursor, title bar colour, ...)."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
from pathlib import Path

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

VK_LBUTTON, VK_RBUTTON, VK_SHIFT, VK_CONTROL, VK_MENU = 0x01, 0x02, 0x10, 0x11, 0x12
SM_SWAPBUTTON, GA_ROOT = 23, 2
SHORTCUT_KEYS = {"shift": (VK_SHIFT,), "ctrl+shift": (VK_CONTROL, VK_SHIFT), "alt": (VK_MENU,)}

user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, ctypes.c_uint]
user32.GetAncestor.restype = wt.HWND
user32.GetClassNameW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.CloseHandle.argtypes = [wt.HANDLE]

MUTEX_NAME = "SatsumaAppMutex"
_mutex = None


def key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def shortcut_held(shortcut: str) -> bool:
    return all(key_down(k) for k in SHORTCUT_KEYS.get(shortcut, SHORTCUT_KEYS["shift"]))


def primary_button() -> int:
    return VK_RBUTTON if user32.GetSystemMetrics(SM_SWAPBUTTON) else VK_LBUTTON


def window_class_under_cursor() -> str:
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    hwnd = user32.WindowFromPoint(pt)
    if not hwnd:
        return ""
    root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(root, buf, 256)
    return buf.value


def set_title_bar_dark(widget, dark: bool):
    try:
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(int(widget.winId()), 20, ctypes.byref(value), 4)
    except Exception:
        pass


def registry_prefers_dark() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return True


def reveal_in_explorer(path: Path):
    if path.exists():
        subprocess.Popen(f'explorer /select,"{path}"')


def set_app_id(app_id: str):
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def create_mutex():
    """Lets the installer know Satsuma is running."""
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)


def release_mutex():
    global _mutex
    if _mutex:
        kernel32.CloseHandle(_mutex)
        _mutex = None
