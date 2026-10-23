"""Quick look's side of Windows: which files are selected in the File Explorer window in front (or on
the desktop), read through the Shell's own COM objects with plain ctypes (no extra library), and the
shortcut, which is only taken while File Explorer or the desktop is in front, so Ctrl + Space keeps
working everywhere else.

One small thread does all of it: it sleeps in Windows' message loop and wakes only when another
window comes to the front (SetWinEventHook) or the shortcut is pressed. Nothing polls."""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
import uuid

from PySide6.QtCore import QObject, Signal

log = logging.getLogger("corefold")

EXPLORER_CLASSES = {"CabinetWClass", "ExploreWClass"}
DESKTOP_CLASSES = {"Progman", "WorkerW"}
MAX_SIBLINGS = 5000      # the files of the folder read for ← and → (a huge folder stops there)
SIBLINGS_BUDGET_S = 1.5

CLSCTX_ALL = 0x17
SVGIO_SELECTION, SVGIO_ALLVIEW, SVGIO_FLAG_VIEWORDER = 0x1, 0x2, 0x80000000
SIGDN_FILESYSPATH = 0x80058000 - (1 << 32)  # (as a signed int)
SWC_DESKTOP, SWFO_NEEDDISPATCH = 8, 1
SVSI_SELECT, SVSI_DESELECTOTHERS, SVSI_ENSUREVISIBLE, SVSI_FOCUSED = 0x1, 0x4, 0x8, 0x10


def _guid(text: str):
    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]
    g = GUID()
    ctypes.memmove(ctypes.byref(g), uuid.UUID(text).bytes_le, 16)
    return g


CLSID_ShellWindows = _guid("9BA05972-F6A8-11CF-A442-00A0C90A8F39")
IID_IShellWindows = _guid("85CB6900-4D95-11CF-960C-0080C7F4EE85")
IID_IServiceProvider = _guid("6D5140C1-7436-11CE-8034-00AA006009FA")
SID_STopLevelBrowser = _guid("4C96BE40-915C-11CF-99D3-00AA004AE837")
IID_IShellBrowser = _guid("000214E2-0000-0000-C000-000000000046")
IID_IFolderView = _guid("CDE725B0-CCC9-4519-917E-325D72FAB4CE")
IID_IShellItemArray = _guid("B63EA76D-1F85-456F-A19C-48159EFA858B")


class VARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("val", ctypes.c_longlong)] + ([("extra", ctypes.c_void_p)] if ctypes.sizeof(ctypes.c_void_p) == 8
                                               else [])


class _Com:
    """A COM interface pointer, called through its table of functions."""

    def __init__(self, ptr: int):
        self.p = ptr

    def call(self, index: int, argtypes: tuple, *args) -> int:
        vtbl = ctypes.cast(ctypes.c_void_p(self.p), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])
        return fn(self.p, *args)

    def out(self, index: int, argtypes: tuple, *args) -> "_Com | None":
        """Calls a method whose last argument gives back an interface."""
        ptr = ctypes.c_void_p()
        hr = self.call(index, argtypes + (ctypes.POINTER(ctypes.c_void_p),), *args, ctypes.byref(ptr))
        return _Com(ptr.value) if hr >= 0 and ptr.value else None

    def qi(self, iid) -> "_Com | None":
        return self.out(0, (ctypes.c_void_p,), ctypes.byref(iid))

    def release(self):
        if self.p:
            self.call(2, ())
            self.p = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()


def _hold(objs: list, obj):
    if obj is not None:
        objs.append(obj)
    return obj


def _release(objs: list):
    for o in reversed(objs):
        try:
            o.release()
        except Exception:
            pass


def _shell_windows() -> _Com | None:
    ptr = ctypes.c_void_p()
    hr = ctypes.windll.ole32.CoCreateInstance(ctypes.byref(CLSID_ShellWindows), None, CLSCTX_ALL,
                                              ctypes.byref(IID_IShellWindows), ctypes.byref(ptr))
    return _Com(ptr.value) if hr >= 0 and ptr.value else None


def _browser_of(disp: _Com, objs: list) -> _Com | None:
    sp = _hold(objs, disp.qi(IID_IServiceProvider))
    if sp is None:
        return None
    return _hold(objs, sp.out(3, (ctypes.c_void_p, ctypes.c_void_p), ctypes.byref(SID_STopLevelBrowser),
                              ctypes.byref(IID_IShellBrowser)))


def _browser_hwnd(browser: _Com) -> int:
    h = ctypes.c_void_p()
    return h.value or 0 if browser.call(3, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(h)) >= 0 else 0


def _folder_view(browser: _Com, objs: list) -> _Com | None:
    view = _hold(objs, browser.out(15, ()))  # IShellBrowser::QueryActiveShellView
    return _hold(objs, view.qi(IID_IFolderView)) if view is not None else None


def _view_for(hwnd: int, objs: list) -> _Com | None:
    """The folder view (IFolderView) of this File Explorer window, or of the desktop when hwnd is the desktop's."""
    u = ctypes.windll.user32
    u.GetAncestor.restype = ctypes.c_void_p
    u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    u.IsWindowVisible.argtypes = [ctypes.c_void_p]
    sw = _hold(objs, _shell_windows())
    if sw is None:
        return None
    if window_class(hwnd) in DESKTOP_CLASSES:
        loc, root = VARIANT(vt=3, val=0), VARIANT()  # VT_I4 CSIDL_DESKTOP
        found = ctypes.c_long()
        disp = _hold(objs, sw.out(15, (ctypes.POINTER(VARIANT), ctypes.POINTER(VARIANT), ctypes.c_int,
                                       ctypes.POINTER(ctypes.c_long), ctypes.c_int),
                                  ctypes.byref(loc), ctypes.byref(root), SWC_DESKTOP, ctypes.byref(found),
                                  SWFO_NEEDDISPATCH))
        browser = _browser_of(disp, objs) if disp is not None else None
        return _folder_view(browser, objs) if browser is not None else None
    count = ctypes.c_long()
    if sw.call(7, (ctypes.POINTER(ctypes.c_long),), ctypes.byref(count)) < 0:
        return None
    best = None
    for i in range(count.value):
        mine: list = []
        disp = _hold(mine, sw.out(8, (VARIANT,), VARIANT(vt=3, val=i)))
        browser = _browser_of(disp, mine) if disp is not None else None
        tab = _browser_hwnd(browser) if browser is not None else 0
        if not tab or (u.GetAncestor(tab, 2) or 0) != hwnd:  # GA_ROOT: the tab's own window
            _release(mine)
            continue
        visible = bool(u.IsWindowVisible(tab))  # (Windows 11's tabs: only the one shown is visible)
        if best is not None and not visible:
            _release(mine)
            continue
        if best is not None:
            _release(best)
        best = mine
        if visible:
            break
    if best is None:
        return None
    objs.extend(best)
    return _folder_view(best[-1], objs)


def _paths(view: _Com, flags: int, limit: int = MAX_SIBLINGS, budget_s: float = 0.0) -> list[str]:
    objs: list = []
    out: list[str] = []
    try:
        arr = _hold(objs, view.out(8, (ctypes.c_uint, ctypes.c_void_p), flags | SVGIO_FLAG_VIEWORDER,
                                   ctypes.byref(IID_IShellItemArray)))
        if arr is None:
            return []
        n = ctypes.c_ulong()
        if arr.call(7, (ctypes.POINTER(ctypes.c_ulong),), ctypes.byref(n)) < 0:
            return []
        deadline = time.monotonic() + budget_s if budget_s else 0
        free = ctypes.windll.ole32.CoTaskMemFree
        free.argtypes = [ctypes.c_void_p]
        for i in range(min(n.value, limit)):
            item = arr.out(8, (ctypes.c_ulong,), i)
            if item is None:
                continue
            with item:
                name = ctypes.c_void_p()
                if item.call(5, (ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)), SIGDN_FILESYSPATH,
                             ctypes.byref(name)) >= 0 and name.value:
                    out.append(ctypes.wstring_at(name.value))
                    free(name)
            if deadline and time.monotonic() > deadline:
                break
        return out
    finally:
        _release(objs)


def _com_ready():
    ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED (already done is fine)


def selection(hwnd: int) -> list[str]:
    """The files and folders selected in this File Explorer window (or on the desktop), in the order shown."""
    if os.name != "nt":
        return []
    _com_ready()
    objs: list = []
    try:
        view = _view_for(hwnd, objs)
        return _paths(view, SVGIO_SELECTION) if view is not None else []
    except Exception:
        log.debug("couldn't read the selection", exc_info=True)
        return []
    finally:
        _release(objs)


def all_items(hwnd: int) -> list[str]:
    """Everything in the folder shown, in the order shown (for going to the next file)."""
    if os.name != "nt":
        return []
    _com_ready()
    objs: list = []
    try:
        view = _view_for(hwnd, objs)
        return _paths(view, SVGIO_ALLVIEW, budget_s=SIBLINGS_BUDGET_S) if view is not None else []
    except Exception:
        log.debug("couldn't read the folder", exc_info=True)
        return []
    finally:
        _release(objs)


def select(hwnd: int, path: str, only: bool = True) -> bool:
    """Selects a file in this File Explorer window (used by the tests)."""
    if os.name != "nt":
        return False
    _com_ready()
    s = ctypes.windll.shell32
    s.SHParseDisplayName.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                     ctypes.c_ulong, ctypes.c_void_p]
    s.ILFindLastID.restype = ctypes.c_void_p
    s.ILFindLastID.argtypes = [ctypes.c_void_p]
    objs: list = []
    pidl = ctypes.c_void_p()
    try:
        view = _view_for(hwnd, objs)
        shell_view = _hold(objs, view.qi(_guid("000214E3-0000-0000-C000-000000000046"))) if view else None
        if shell_view is None or s.SHParseDisplayName(path, None, ctypes.byref(pidl), 0, None) < 0:
            return False
        flags = SVSI_SELECT | SVSI_ENSUREVISIBLE | SVSI_FOCUSED | (SVSI_DESELECTOTHERS if only else 0)
        return shell_view.call(14, (ctypes.c_void_p, ctypes.c_uint), s.ILFindLastID(pidl), flags) >= 0
    finally:
        if pidl.value:
            ctypes.windll.ole32.CoTaskMemFree(pidl)
        _release(objs)


def window_class(hwnd: int) -> str:
    if not hwnd or os.name != "nt":
        return ""
    buf = ctypes.create_unicode_buffer(64)
    ctypes.windll.user32.GetClassNameW(ctypes.c_void_p(hwnd), buf, 64)
    return buf.value


def foreground() -> int:
    if os.name != "nt":
        return 0
    u = ctypes.windll.user32
    u.GetForegroundWindow.restype = ctypes.c_void_p
    return u.GetForegroundWindow() or 0


def is_files_window(hwnd: int) -> bool:
    return window_class(hwnd) in EXPLORER_CLASSES | DESKTOP_CLASSES


class Watcher(QObject):
    """The thread that holds the shortcut while File Explorer or the desktop is in front, and reads the
    selection when it's pressed."""
    picked = Signal(object, list)   # (the Explorer window, the selected paths)
    siblings = Signal(object, list)  # (the same window, everything in its folder, in order)

    def __init__(self):
        super().__init__()
        self.key = "off"
        self.ok = True
        self.registered = False
        self._thread: threading.Thread | None = None
        self._tid = 0

    def set(self, key: str):
        if key == self.key and (self._thread is not None or key == "off"):
            return
        self.stop()
        self.key = key
        self.ok = True
        from .hotkey import parse
        spec = parse(key)
        if not spec or os.name != "nt":
            return
        ready = threading.Event()
        t = threading.Thread(target=self._loop, args=(spec, ready), name="corefold-peek", daemon=True)
        t.start()
        ready.wait(3)
        self._thread = t

    def _loop(self, spec, ready: threading.Event):
        import ctypes.wintypes as wt
        from .hotkey import MOD_ALT, MOD_NOREPEAT, MOD_WIN, _mask_modifier_release
        u = ctypes.windll.user32
        u.SetWinEventHook.restype = ctypes.c_void_p
        u.UnhookWinEvent.argtypes = [ctypes.c_void_p]
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        _com_ready()

        def follow(*_):
            """Another window came to the front: take the shortcut only for File Explorer and the desktop."""
            want = is_files_window(foreground())
            if want and not self.registered:
                self.registered = bool(u.RegisterHotKey(None, 1, spec[0] | MOD_NOREPEAT, spec[1]))
                self.ok = self.registered
                if not self.ok:
                    log.info("the quick look shortcut %s is used by another app", self.key)
            elif not want and self.registered:
                u.UnregisterHotKey(None, 1)
                self.registered = False

        proto = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_long,
                                   ctypes.c_long, ctypes.c_ulong, ctypes.c_ulong)
        callback = proto(follow)
        hook = u.SetWinEventHook(3, 3, None, callback, 0, 0, 0)  # EVENT_SYSTEM_FOREGROUND, WINEVENT_OUTOFCONTEXT
        follow()
        ready.set()
        msg = wt.MSG()
        try:
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message != 0x0312:  # WM_HOTKEY
                    u.TranslateMessage(ctypes.byref(msg))
                    u.DispatchMessageW(ctypes.byref(msg))
                    continue
                if spec[0] & (MOD_ALT | MOD_WIN):
                    _mask_modifier_release(u)
                hwnd = foreground()
                if not is_files_window(hwnd):
                    continue
                paths = selection(hwnd)
                try:
                    self.picked.emit(hwnd, paths)
                except RuntimeError:
                    break
                if len(paths) == 1:  # ← and → go through the folder: read it once the window is open
                    try:
                        self.siblings.emit(hwnd, all_items(hwnd))
                    except RuntimeError:
                        break
        finally:
            if hook:
                u.UnhookWinEvent(hook)
            if self.registered:
                u.UnregisterHotKey(None, 1)
                self.registered = False
            del callback

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
