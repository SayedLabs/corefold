"""Watches every window Windows shows while a test runs, and tells which ones only flashed: a window of
the app (or of a program it started) that shows up and is gone again a moment later, and any console
window at all (a black box from a program started without CREATE_NO_WINDOW). Those are the "small
windows that open and close quickly" people see.

    watch = FlashWatch(pid)      # the app's process (its own windows, and every program it starts)
    watch.start()
    ...                          # use the app
    for line in watch.problems(): print("FAIL  " + line)

Windows only. Used by newtest_flash.py (the app from source) and exetest.py (the built Corefold.exe)."""
from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes

EVENT_OBJECT_DESTROY, EVENT_OBJECT_SHOW, EVENT_OBJECT_HIDE = 0x8001, 0x8002, 0x8003
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
WINEVENT_OUTOFCONTEXT = 0
GA_ROOT = 2
GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CHILD = 0x40000000
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x2
TH32CS_SNAPPROCESS = 0x2
# console windows: the old console host, Windows Terminal's, and the hidden one behind a pseudo console
CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}
# not windows people see as "a window": tooltips, menus, the drop shadow under a menu, IME and
# Windows' own bits that every app gets
NOT_A_WINDOW = ("ToolTip", "tooltips_class32", "#32768", "DropShadow", "IME", "MSCTFIME", "SysShadow",
                "PseudoConsoleWindow", "OleMainThreadWndClass", "CicMarshalWnd", "Shell_TrayWnd")
FLASH_S = 0.6        # shown and gone again within this: it flashed
SMALLEST = 24        # (px) smaller than this, nothing anyone would see

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
WinEventProc = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                  wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, WinEventProc,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetLayeredWindowAttributes.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
                                              ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def processes() -> dict[int, tuple[int, str]]:
    """Every running process: pid -> (its parent's pid, its program's name)."""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    out = {}
    if snap in (0, -1, ctypes.c_void_p(-1).value):
        return out
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = kernel32.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(e))
        while ok:
            out[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile)
            ok = kernel32.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(e))
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(snap))
    return out


def _text(fn, hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    fn(hwnd, buf, 256)
    return buf.value


class Shown:
    """One window, from the moment Windows showed it."""

    def __init__(self, hwnd, pid, exe, cls, title, rect, alpha, mine):
        self.hwnd, self.pid, self.exe, self.cls, self.title = hwnd, pid, exe, cls, title
        self.rect, self.alpha, self.mine = rect, alpha, mine
        self.at = time.monotonic()
        self.gone_at: float | None = None
        self.moved: tuple | None = None  # (seconds after it showed, how far) the first big move

    def describe(self) -> str:
        left, top, right, bottom = self.rect
        who = "Corefold" if self.mine == "app" else f"{self.exe} (pid {self.pid})"
        name = f' "{self.title}"' if self.title else ""
        return f"{who}: {self.cls}{name} {right - left}x{bottom - top} at {left},{top}"


class FlashWatch:
    def __init__(self, pid: int | None = None):
        self.pid = pid or os.getpid()
        self.events: list[Shown] = []
        self.open: dict[int, Shown] = {}
        self.lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._proc = WinEventProc(self._event)  # (kept: Windows calls it for as long as the hook lives)
        self._tree: dict[int, tuple[int, str]] = {}
        self._tree_at = 0.0
        self.label = ""  # what the test is doing right now (written next to each window)

    # ---- which process a window belongs to

    def _whose(self, pid: int) -> tuple[str | None, str]:
        """("app", name) for the app itself, ("child", name) for a program it started (or one started
        by those), (None, name) for anything else."""
        if pid == self.pid:
            return "app", ""
        for again in (False, True):
            if again or time.monotonic() - self._tree_at > 0.5:
                self._tree = processes()
                self._tree_at = time.monotonic()
            seen, p = set(), pid
            while p in self._tree and p not in seen:
                seen.add(p)
                parent = self._tree[p][0]
                if parent == self.pid:
                    return "child", self._tree[pid][1]
                p = parent
            if pid in self._tree:
                return None, self._tree[pid][1]
        return None, ""

    # ---- the hook

    def _event(self, _hook, event, hwnd, id_object, id_child, _thread, _time):
        if id_object != 0 or id_child != 0 or not hwnd:
            return
        try:
            if event == EVENT_OBJECT_SHOW:
                self._shown(hwnd)
            elif event in (EVENT_OBJECT_HIDE, EVENT_OBJECT_DESTROY):
                with self.lock:
                    w = self.open.pop(hwnd, None)
                if w is not None:
                    w.gone_at = time.monotonic()
            elif event == EVENT_OBJECT_LOCATIONCHANGE:
                w = self.open.get(hwnd)
                if w is not None and w.moved is None and time.monotonic() - w.at < 1.0:
                    r = wintypes.RECT()
                    if user32.GetWindowRect(hwnd, ctypes.byref(r)):
                        far = abs(r.left - w.rect[0]) + abs(r.top - w.rect[1])
                        if far > 40:
                            w.moved = (time.monotonic() - w.at, far, (r.left, r.top, r.right, r.bottom))
        except Exception:  # (never let a test helper take the app down from inside Windows' callback)
            pass

    def _shown(self, hwnd):
        if user32.GetAncestor(hwnd, GA_ROOT) != hwnd or user32.GetWindowLongW(hwnd, GWL_STYLE) & WS_CHILD:
            return
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        cls = _text(user32.GetClassNameW, hwnd)
        mine, exe = self._whose(pid.value)
        if mine is None and cls not in CONSOLE_CLASSES:
            return
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        alpha = 255
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_LAYERED:
            a, flags = ctypes.c_ubyte(255), wintypes.DWORD()
            if user32.GetLayeredWindowAttributes(hwnd, None, ctypes.byref(a), ctypes.byref(flags)) and \
                    flags.value & LWA_ALPHA:
                alpha = a.value
        w = Shown(hwnd, pid.value, exe, cls, _text(user32.GetWindowTextW, hwnd),
                  (r.left, r.top, r.right, r.bottom), alpha, mine or "other")
        w.label = self.label
        with self.lock:
            self.events.append(w)
            self.open[hwnd] = w

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        hook = user32.SetWinEventHook(EVENT_OBJECT_DESTROY, EVENT_OBJECT_LOCATIONCHANGE, None, self._proc,
                                      0, 0, WINEVENT_OUTOFCONTEXT)
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if hook:
            user32.UnhookWinEvent(hook)

    def start(self):
        self._thread = threading.Thread(target=self._run, name="flashwatch", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        return self

    def stop(self):
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
        if self._thread is not None:
            self._thread.join(3)

    # ---- what it saw

    @staticmethod
    def _seen_by_people(w: Shown) -> bool:
        left, top, right, bottom = w.rect
        if right - left < SMALLEST or bottom - top < SMALLEST:
            return False
        if right < -1000 or bottom < -1000 or left < -20000 or top < -20000:  # (kept off the screen)
            return False
        if w.alpha == 0:  # (shown see-through: drawn first, then faded in)
            return False
        return not any(part in w.cls for part in NOT_A_WINDOW)

    def problems(self, since: float = 0.0) -> list[str]:
        out = []
        with self.lock:
            events = [w for w in self.events if w.at >= since]
        for w in events:
            where = f" (while: {w.label})" if getattr(w, "label", "") else ""
            if w.cls in CONSOLE_CLASSES:
                out.append(f"a console window opened: {w.describe()}{where}")
                continue
            if not self._seen_by_people(w):
                continue
            if w.mine == "child":
                out.append(f"a program Corefold started showed a window: {w.describe()}{where}")
            elif w.gone_at is not None and w.gone_at - w.at < FLASH_S:
                out.append(f"a window flashed for {(w.gone_at - w.at) * 1000:.0f} ms: {w.describe()}{where}")
            elif w.moved is not None and w.moved[0] < 0.3:
                out.append(f"a window jumped {w.moved[1]} px {w.moved[0] * 1000:.0f} ms after it showed: "
                           f"{w.describe()} -> {w.moved[2][0]},{w.moved[2][1]}{where}")
        return out

    def own_windows(self, since: float, besides: set[int]) -> list[str]:
        """Every window of the app itself shown since then other than those (hwnds), however small or quick:
        while everything is meant to show inside Corefold's window, there should be none at all. (Menus,
        tips and the picture under a dragged thing count too, unlike in problems().)"""
        with self.lock:
            events = [w for w in self.events if w.at >= since and w.mine == "app" and w.hwnd not in besides]
        system = ("IME", "MSCTFIME", "CicMarshalWnd", "OleMainThreadWndClass", "PseudoConsoleWindow")
        out = []
        for w in events:
            if any(part in w.cls for part in system):
                continue
            life = f"{(w.gone_at - w.at) * 1000:.0f} ms" if w.gone_at is not None else "still open"
            where = f" (while: {w.label})" if getattr(w, "label", "") else ""
            out.append(f"a window of its own ({life}): {w.describe()}{where}")
        return out

    def notes(self) -> list[str]:
        """Every window that was shown, with how long it stayed (for the log)."""
        with self.lock:
            events = list(self.events)
        out = []
        for w in events:
            life = f"{(w.gone_at - w.at) * 1000:.0f} ms" if w.gone_at is not None else "still open"
            alpha = f", alpha {w.alpha}" if w.alpha != 255 else ""
            where = f" (while: {w.label})" if getattr(w, "label", "") else ""
            out.append(f"{w.describe()}: {life}{alpha}{where}")
        return out
