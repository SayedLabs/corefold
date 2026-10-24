"""Small Windows API helpers (keyboard/mouse state, window under cursor, title bar colour, ...)."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
from pathlib import Path

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

VK_LBUTTON, VK_RBUTTON, VK_SHIFT, VK_CONTROL, VK_MENU, VK_ESCAPE = 0x01, 0x02, 0x10, 0x11, 0x12, 0x1B
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

MUTEX_NAME = "CorefoldAppMutex"
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


def play_sound(path: Path | None):
    if not path:
        return
    try:
        import winsound
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass


def any_button_down() -> bool:
    return key_down(VK_LBUTTON) or key_down(VK_RBUTTON)


def set_app_id(app_id: str):
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def create_mutex() -> bool:
    """Lets the installer know Corefold is running. Returns False if another copy already holds it."""
    global _mutex
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wt.HANDLE
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    handle = k32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        # Let go of our handle, or the mutex would live on after the other copy quits.
        if handle:
            kernel32.CloseHandle(handle)
        return False
    _mutex = handle
    return True


def release_mutex():
    global _mutex
    if _mutex:
        kernel32.CloseHandle(_mutex)
        _mutex = None


def memory_use() -> int:
    """How much memory Corefold is using right now (the number Task Manager shows), in bytes."""
    return memory_info()[0]


def memory_info() -> tuple[int, int]:
    """(working set, private memory) in bytes; (0, 0) if Windows won't say."""
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]
    try:
        c = Counters()
        c.cb = ctypes.sizeof(c)
        get = ctypes.windll.psapi.GetProcessMemoryInfo
        get.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]
        if get(kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return int(c.WorkingSetSize), int(c.PrivateUsage)
    except Exception:
        pass
    return 0, 0


_seen_modules: set[str] = set()


def log_memory(where: str):
    """For measuring (only when COREFOLD_MEMLOG is set): memory now, and what was loaded since last time."""
    import os
    import sys
    if not os.environ.get("COREFOLD_MEMLOG"):
        return
    import logging
    ws, private = memory_info()
    mods = set(sys.modules)
    new = sorted({m.split(".")[0] for m in mods - _seen_modules}) if _seen_modules else []
    _seen_modules.update(mods)
    logging.getLogger("corefold").info("memory %s: %.1f MB working set, %.1f MB private, %d modules%s", where,
                                       ws / 2**20, private / 2**20, len(mods), (" new: " + " ".join(new)) if new else "")


def trim_memory():
    """Gives memory Corefold isn't using back to Windows (it comes back by itself when needed)."""
    try:
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wt.HANDLE
        k32.SetProcessWorkingSetSize.argtypes = [wt.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
        # free blocks left in the heaps (by the window, after it was freed) go back first
        k32.GetProcessHeaps.restype = wt.DWORD
        k32.GetProcessHeaps.argtypes = [wt.DWORD, ctypes.POINTER(wt.HANDLE)]
        k32.HeapCompact.restype = ctypes.c_size_t
        k32.HeapCompact.argtypes = [wt.HANDLE, wt.DWORD]
        heaps = (wt.HANDLE * 256)()
        for h in heaps[:min(k32.GetProcessHeaps(256, heaps), 256)]:
            k32.HeapCompact(h, 0)
        k32.SetProcessWorkingSetSize(k32.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1))
    except Exception:
        pass


def user_busy() -> bool:
    """True while a full-screen game, video or presentation is on screen. Popups nobody asked for
    (like "a new version is available") wait until then, so they never pull anyone out of a game."""
    try:
        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
        # 2 = busy (full screen), 3 = full-screen Direct3D game, 4 = presentation mode
        return state.value in (2, 3, 4)
    except Exception:
        return False


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint32)]


def idle_ms() -> int:
    """How long since the last key press or mouse move anywhere on the PC, in milliseconds (very long
    if Windows can't say)."""
    try:
        info = _LASTINPUTINFO(ctypes.sizeof(_LASTINPUTINFO), 0)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 1 << 30
        return int((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF)
    except Exception:
        return 1 << 30
