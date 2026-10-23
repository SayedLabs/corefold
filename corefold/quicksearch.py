"""Finding apps and files from the quick menu as the person types (like Windows' own Start search).

Apps come from the Start menu (its shortcuts, and the Store apps Windows lists in its Apps folder).
The list is made on a thread the first time the menu opens and made again at most every 10 minutes.

Files and folders come from Everything (voidtools), if it's running (asked over its own messages,
it's the fastest), or else from Windows' own search index of the person's folders; files they opened
lately (Windows' Recent items) are added too, and are all there is when neither is there. Nothing is
indexed by Corefold, nothing runs unless the menu is open and the person types, and nothing leaves
the PC.

Only loaded once the menu is used with "Find files and apps as I type" on."""
from __future__ import annotations

import asyncio
import logging
import ntpath
import os
import queue
import re
import struct
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger("corefold")

MAX_APPS = 5
MAX_FILES = 8
MIN_CHARS = 2
DEBOUNCE_MS = 150
APPS_FRESH_S = 600      # the list of apps is made again at most this often
WORKER_IDLE_S = 60      # the search thread ends after this long with nothing to do

IS_WIN = os.name == "nt"


@dataclass(frozen=True)
class App:
    name: str
    target: str      # a shortcut's path, or its name in the Apps folder (a Store app's id, "…Calculator_…!App")
    shell: bool = False   # True: opened through shell:AppsFolder (a Store app)

    @property
    def admin(self) -> bool:
        """Can be run as administrator (a desktop app's shortcut or program)."""
        return not self.shell and self.target.lower().endswith((".lnk", ".exe"))


@dataclass(frozen=True)
class Found:
    path: str
    name: str
    folder: bool = False


# ---------------------------------------------------------------------------- matching

_WORDS = re.compile(r"[\s\-_.()&,+/\\\[\]]+")


def app_score(query: str, name: str) -> int:
    """How well what's typed fits an app's name, 0 (not at all) to 100 (it's the name). Initials
    count too: "vsc" is Visual Studio Code."""
    q = query.strip().lower()
    n = name.strip().lower()
    if not q or not n:
        return 0
    if n == q:
        return 100
    if n.startswith(q):
        return 90
    words = [w for w in _WORDS.split(n) if w]
    if any(w.startswith(q) for w in words):
        return 75
    if len(q) >= 2 and " " not in q:
        # initials ("vsc"), also with camel case ("PowerShell ISE" -> psi)
        caps = [w for part in _WORDS.split(name.strip()) if part
                for w in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", part)]
        for initials in ("".join(w[0] for w in words), "".join(w[0] for w in caps).lower()):
            if len(initials) >= 2 and initials.startswith(q):
                return 70
    if q in n:
        return 55
    squeezed = q.replace(" ", "")
    if len(squeezed) >= 3 and squeezed in n.replace(" ", ""):
        return 50
    return 0


def level(score: int) -> int:
    """The same on the quick menu's scale (quickfind.score: 4 is the name, 1 is somewhere in it)."""
    return 4 if score >= 100 else 3 if score >= 90 else 2 if score >= 70 else 1 if score else 0


def file_score(query: str, name: str) -> int:
    """Files found by name come before ones only found by what's in them."""
    q = query.strip().lower()
    n = name.lower()
    stem = n.rsplit(".", 1)[0] if "." in n else n
    if n == q or stem == q:
        return 100
    if n.startswith(q):
        return 90
    if any(w.startswith(q) for w in _WORDS.split(n) if w):
        return 75
    if q in n:
        return 55
    words = q.split()
    if words and all(w in n for w in words):
        return 50
    return 10


def match_apps(apps: list[App], query: str, limit: int = MAX_APPS) -> list[tuple[int, App]]:
    found = [(app_score(query, a.name), i, a) for i, a in enumerate(apps)]
    found = [x for x in found if x[0]]
    found.sort(key=lambda x: (-x[0], len(x[2].name), x[1]))
    return [(s, a) for s, _, a in found[:limit]]


# ---------------------------------------------------------------------------- Windows' folders

_FOLDERS = {
    "programs": "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}",
    "common_programs": "{0139D44E-6AFE-49F2-8690-3DAFCAE6FFB8}",
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
    "recent": "{AE50C081-EBD2-438A-8655-8A092E34987A}",
}
_FALLBACK = {
    "programs": ("APPDATA", r"Microsoft\Windows\Start Menu\Programs"),
    "common_programs": ("PROGRAMDATA", r"Microsoft\Windows\Start Menu\Programs"),
    "recent": ("APPDATA", r"Microsoft\Windows\Recent"),
}
USER_FOLDERS = ("desktop", "documents", "downloads", "pictures", "music", "videos")


def _guid(text: str):
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16),
                    ("d4", ctypes.c_ubyte * 8)]
    g = GUID()
    ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
    return g


def known_folder(name: str) -> str:
    """Where one of Windows' folders really is (Documents may be moved to OneDrive or another drive)."""
    if IS_WIN:
        try:
            import ctypes
            out = ctypes.c_void_p()
            shell32 = ctypes.windll.shell32
            shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                                     ctypes.POINTER(ctypes.c_void_p)]
            g = _guid(_FOLDERS[name])
            if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0 and out.value:
                try:
                    return ctypes.wstring_at(out.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(out)
        except Exception:
            log.debug("no known folder %s", name, exc_info=True)
    env, rest = _FALLBACK.get(name, ("USERPROFILE", name.capitalize()))
    base = os.environ.get(env) or str(Path.home())
    return os.path.join(base, rest)


# ---------------------------------------------------------------------------- the apps

_SKIP = re.compile(r"\b(uninstall|uninstaller|readme|read me|release notes|license|licence|documentation|"
                   r"help|manual|website|web site|on the web|changelog|what's new|faq)\b", re.I)
_SKIP_APP = re.compile(r"\b(uninstall|uninstaller)\b", re.I)  # (a Store app called "Get Help" is an app)
_SHORTCUTS = (".lnk", ".url", ".appref-ms")


def start_menu_apps() -> list[App]:
    """The Start menu's shortcuts (the person's own, then everyone's), without "Uninstall …" and the like."""
    out: list[App] = []
    seen: set[str] = set()
    for which in ("programs", "common_programs"):
        root = known_folder(which)
        for folder, dirs, files in os.walk(root):
            dirs.sort()
            for f in sorted(files):
                low = f.lower()
                if not low.endswith(_SHORTCUTS) or low == "desktop.ini":
                    continue
                name = f.rsplit(".", 1)[0].strip()
                if not name or _SKIP.search(name) or name.lower() in seen:
                    continue
                seen.add(name.lower())
                out.append(App(name, os.path.join(folder, f)))
    return out


def apps_folder() -> list[tuple[str, str]]:
    """(name, parsing name) of everything in Windows' Apps folder (shell:AppsFolder), Store apps
    included. Read through the shell's own folder interfaces (ctypes, no PowerShell)."""
    if not IS_WIN:
        return []
    import ctypes
    from ctypes import POINTER, byref, c_uint, c_ulong, c_void_p, c_wchar_p
    ole32, shell32, shlwapi = ctypes.windll.ole32, ctypes.windll.shell32, ctypes.windll.shlwapi

    class _U(ctypes.Union):
        _fields_ = [("pOleStr", c_void_p), ("uOffset", c_uint), ("cStr", ctypes.c_char * 260)]

    class STRRET(ctypes.Structure):
        _fields_ = [("uType", c_uint), ("u", _U)]

    def method(obj, index, *argtypes):
        vtbl = ctypes.cast(c_void_p(obj), POINTER(POINTER(c_void_p))).contents
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, *argtypes)(vtbl[index])
        return lambda *a: fn(obj, *a)

    shell32.SHParseDisplayName.argtypes = [c_wchar_p, c_void_p, POINTER(c_void_p), c_ulong, c_void_p]
    shell32.SHBindToObject.argtypes = [c_void_p, c_void_p, c_void_p, c_void_p, POINTER(c_void_p)]
    shlwapi.StrRetToStrW.argtypes = [POINTER(STRRET), c_void_p, POINTER(c_void_p)]
    ole32.CoTaskMemFree.argtypes = [c_void_p]
    inited = ole32.CoInitializeEx(None, 0x2) in (0, 1)  # COINIT_APARTMENTTHREADED (S_OK or S_FALSE)
    out: list[tuple[str, str]] = []
    pidl, folder, enum = c_void_p(), c_void_p(), c_void_p()
    try:
        if shell32.SHParseDisplayName("shell:AppsFolder", None, byref(pidl), 0, None) != 0 or not pidl.value:
            return out
        iid = _guid("{000214E6-0000-0000-C000-000000000046}")  # IShellFolder
        if shell32.SHBindToObject(None, pidl, None, byref(iid), byref(folder)) != 0 or not folder.value:
            return out
        enum_objects = method(folder.value, 4, c_void_p, c_ulong, POINTER(c_void_p))
        display_name = method(folder.value, 11, c_void_p, c_ulong, POINTER(STRRET))
        if enum_objects(None, 0x20 | 0x40, byref(enum)) != 0 or not enum.value:  # FOLDERS | NONFOLDERS
            return out
        next_item = method(enum.value, 3, c_ulong, POINTER(c_void_p), POINTER(c_ulong))

        def text(child, flags) -> str:
            sr = STRRET()
            if display_name(child, flags, byref(sr)) != 0:
                return ""
            s = c_void_p()
            if shlwapi.StrRetToStrW(byref(sr), child, byref(s)) != 0 or not s.value:
                return ""
            try:
                return ctypes.wstring_at(s.value)
            finally:
                ole32.CoTaskMemFree(s)
        for _ in range(3000):
            child, got = c_void_p(), c_ulong()
            if next_item(1, byref(child), byref(got)) != 0 or not got.value or not child.value:
                break
            try:
                name = text(child, 0)                 # SHGDN_NORMAL
                parsing = text(child, 0x8000 | 0x1)   # SHGDN_FORPARSING | SHGDN_INFOLDER
            finally:
                ole32.CoTaskMemFree(child)
            if name and parsing:
                out.append((name, parsing))
    except Exception:
        log.debug("couldn't read the Apps folder", exc_info=True)
    finally:
        for obj in (enum, folder):
            if obj.value:
                try:
                    method(obj.value, 2)()   # Release
                except Exception:
                    pass
        if pidl.value:
            ole32.CoTaskMemFree(pidl)
        if inited:
            ole32.CoUninitialize()
    return out


def all_apps() -> list[App]:
    apps = start_menu_apps()
    seen = {a.name.lower() for a in apps}
    try:
        listed = apps_folder()
    except Exception:
        log.debug("no Apps folder", exc_info=True)
        listed = []
    for name, parsing in listed:
        if name.lower() in seen or _SKIP_APP.search(name):
            continue
        low = parsing.lower()
        if low.endswith(_SHORTCUTS) or low.startswith(("http:", "https:", "::")):
            continue  # (a shortcut the Start menu walk would have had, or a website)
        seen.add(name.lower())
        if os.path.isabs(parsing) and low.endswith(".exe"):
            apps.append(App(name, parsing))
        else:
            apps.append(App(name, parsing, shell=True))
    return apps


_apps: list[App] = []
_apps_at = 0.0
_apps_lock = threading.Lock()
_apps_building = False


def apps() -> list[App]:
    return _apps


def refresh_apps(done=None) -> bool:
    """Makes the list of apps on a thread, if it's missing or more than 10 minutes old. done() is
    called (on that thread) when it's ready. False if the list is fresh already."""
    global _apps_building
    with _apps_lock:
        if _apps_building or (_apps and time.monotonic() - _apps_at < APPS_FRESH_S):
            return False
        _apps_building = True

    def work():
        global _apps, _apps_at, _apps_building
        started = time.monotonic()
        try:
            found = all_apps()
        except Exception:
            log.exception("couldn't list the apps")
            found = []
        with _apps_lock:
            if found or not _apps:
                _apps = found
            _apps_at = time.monotonic()
            _apps_building = False
        log.info("quick menu: %d apps listed in %d ms", len(found), (time.monotonic() - started) * 1000)
        if done is not None:
            done()
    threading.Thread(target=work, name="corefold-apps", daemon=True).start()
    return True


def launch(app: App, admin: bool = False) -> bool:
    """Starts an app the way the Start menu would (as administrator if asked and it can be)."""
    if not IS_WIN:
        return False
    try:
        if app.shell:
            subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + app.target], close_fds=True)
        elif admin and app.admin:
            os.startfile(app.target, "runas")  # type: ignore[attr-defined]
        else:
            os.startfile(app.target)  # type: ignore[attr-defined]
        return True
    except OSError as e:
        if admin and getattr(e, "winerror", 0) == 1223:  # (they said no to Windows' question)
            return True
        log.info("couldn't start an app: %s", e)
        return False


def app_icon(app: App, size: int):
    """The app's own icon as a QPixmap (None if there isn't one)."""
    from PySide6.QtGui import QPixmap
    try:
        if not app.shell:
            return _file_icon(app.target, size)
        if not IS_WIN:
            return None
        image = _shell_icon("shell:AppsFolder\\" + app.target)
        return None if image is None or image.isNull() else QPixmap.fromImage(image)
    except Exception:
        return None


def _file_icon(path: str, size: int):
    from PySide6.QtCore import QFileInfo, QSize
    from PySide6.QtWidgets import QFileIconProvider
    global _provider
    if _provider is None:
        _provider = QFileIconProvider()
    icon = _provider.icon(QFileInfo(path))
    if icon.isNull():
        return None
    pm = icon.pixmap(QSize(size, size))
    return None if pm.isNull() else pm


_provider = None


def file_icon(path: str, size: int):
    try:
        return _file_icon(path, size)
    except Exception:
        return None


def _shell_icon(parsing: str):
    """A shell item's icon (a Store app's), as a QImage."""
    import ctypes
    from ctypes import POINTER, byref, c_uint, c_ulong, c_void_p, c_wchar_p, wintypes

    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [("hIcon", c_void_p), ("iIcon", ctypes.c_int), ("dwAttributes", wintypes.DWORD),
                    ("szDisplayName", ctypes.c_wchar * 260), ("szTypeName", ctypes.c_wchar * 80)]
    shell32, ole32, user32 = ctypes.windll.shell32, ctypes.windll.ole32, ctypes.windll.user32
    shell32.SHParseDisplayName.argtypes = [c_wchar_p, c_void_p, POINTER(c_void_p), c_ulong, c_void_p]
    shell32.SHGetFileInfoW.argtypes = [c_void_p, wintypes.DWORD, POINTER(SHFILEINFOW), c_uint, c_uint]
    shell32.SHGetFileInfoW.restype = ctypes.c_size_t
    user32.DestroyIcon.argtypes = [c_void_p]
    ole32.CoTaskMemFree.argtypes = [c_void_p]
    pidl = c_void_p()
    try:
        if shell32.SHParseDisplayName(parsing, None, byref(pidl), 0, None) != 0 or not pidl.value:
            return None
        info = SHFILEINFOW()
        if not shell32.SHGetFileInfoW(pidl, 0, byref(info), ctypes.sizeof(info), 0x8 | 0x100) or not info.hIcon:
            return None  # SHGFI_PIDL | SHGFI_ICON (large)
        try:
            return hicon_image(info.hIcon)
        finally:
            user32.DestroyIcon(info.hIcon)
    except Exception:
        log.debug("no icon for %s", parsing, exc_info=True)
        return None
    finally:
        if pidl.value:
            ole32.CoTaskMemFree(pidl)


def hicon_image(hicon):
    """An HICON as a QImage (read through GDI, so it doesn't depend on Qt having fromHICON)."""
    import ctypes
    from ctypes import byref, c_void_p, wintypes
    from PySide6.QtGui import QImage

    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                    ("hbmMask", c_void_p), ("hbmColor", c_void_p)]

    class BITMAP(ctypes.Structure):
        _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long), ("bmHeight", ctypes.c_long),
                    ("bmWidthBytes", ctypes.c_long), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                    ("bmBits", c_void_p)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetIconInfo.argtypes = [c_void_p, ctypes.POINTER(ICONINFO)]
    user32.GetDC.argtypes, user32.GetDC.restype = [c_void_p], c_void_p
    user32.ReleaseDC.argtypes = [c_void_p, c_void_p]
    gdi32.GetObjectW.argtypes = [c_void_p, ctypes.c_int, c_void_p]
    gdi32.GetDIBits.argtypes = [c_void_p, c_void_p, ctypes.c_uint, ctypes.c_uint, c_void_p, c_void_p, ctypes.c_uint]
    gdi32.DeleteObject.argtypes = [c_void_p]
    ii = ICONINFO()
    if not user32.GetIconInfo(hicon, byref(ii)):
        return None
    try:
        if not ii.hbmColor:
            return None
        bm = BITMAP()
        if not gdi32.GetObjectW(ii.hbmColor, ctypes.sizeof(bm), byref(bm)):
            return None
        w, h = bm.bmWidth, bm.bmHeight
        if not 0 < w <= 512 or not 0 < h <= 512:
            return None
        bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        dc = user32.GetDC(None)
        try:
            if gdi32.GetDIBits(dc, ii.hbmColor, 0, h, buf, byref(bi), 0) != h:
                return None
        finally:
            user32.ReleaseDC(None, dc)
        data = bytearray(buf.raw)
        if not any(data[3::4]):  # (an icon without its own see-through parts: all of it shows)
            data[3::4] = b"\xff" * (w * h)
        return QImage(bytes(data), w, h, w * 4, QImage.Format_ARGB32_Premultiplied).copy()
    finally:
        for bmp in (ii.hbmColor, ii.hbmMask):
            if bmp:
                gdi32.DeleteObject(bmp)


# ---------------------------------------------------------------------------- files: Everything

EVERYTHING_WINDOWS = ("EVERYTHING_TASKBAR_NOTIFICATION", "EVERYTHING_TASKBAR_NOTIFICATION_(1.5a)")
_QUERY2W = 18          # EVERYTHING_IPC_COPYDATA_QUERY2W
_REQ_NAME, _REQ_PATH = 0x1, 0x2
_SORT_NAME = 1
_REPLY_ID = 0x43460051  # our own number, so the reply is known as ours


def everything_window() -> int:
    if not IS_WIN:
        return 0
    try:
        import ctypes
        u = ctypes.windll.user32
        u.FindWindowW.restype = ctypes.c_void_p
        for cls in EVERYTHING_WINDOWS:
            h = u.FindWindowW(cls, None)
            if h:
                return int(h)
    except Exception:
        pass
    return 0


def parse_everything(data: bytes) -> list[Found]:
    """Everything's answer (EVERYTHING_IPC_LIST2 with names and paths) as found files."""
    out: list[Found] = []
    if len(data) < 20:
        return out
    _total, count, _offset, flags, _sort = struct.unpack_from("<5I", data, 0)
    for i in range(min(count, 500)):
        at = 20 + i * 8
        if at + 8 > len(data):
            break
        item_flags, pos = struct.unpack_from("<2I", data, at)
        name = path = ""
        for want in (_REQ_NAME, _REQ_PATH):
            if not flags & want:
                continue
            if pos + 4 > len(data):
                break
            (n,) = struct.unpack_from("<I", data, pos)
            text = data[pos + 4:pos + 4 + n * 2].decode("utf-16-le", "replace")
            pos += 4 + (n + 1) * 2
            if want == _REQ_NAME:
                name = text
            else:
                path = text
        if item_flags & 0x2 or not name:  # (a whole drive)
            continue
        out.append(Found(ntpath.join(path, name) if path else name, name, bool(item_flags & 0x1)))
    return out


def everything_query(text: str, limit: int = 40, timeout: float = 1.0, stale=lambda: False) -> list[Found] | None:
    """Asks Everything (if it's running) for files whose names fit what's typed. None: it isn't there
    or didn't answer."""
    target = everything_window()
    if not target:
        return None
    import ctypes
    from ctypes import POINTER, byref, c_size_t, c_ssize_t, c_uint, c_void_p, c_wchar_p, wintypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32

    class COPYDATASTRUCT(ctypes.Structure):
        _fields_ = [("dwData", c_size_t), ("cbData", wintypes.DWORD), ("lpData", c_void_p)]
    WNDPROC = ctypes.WINFUNCTYPE(c_ssize_t, c_void_p, c_uint, c_size_t, c_ssize_t)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", c_uint), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", c_void_p), ("hIcon", c_void_p),
                    ("hCursor", c_void_p), ("hbrBackground", c_void_p), ("lpszMenuName", c_wchar_p),
                    ("lpszClassName", c_wchar_p)]

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", c_void_p), ("message", c_uint), ("wParam", c_size_t), ("lParam", c_ssize_t),
                    ("time", wintypes.DWORD), ("pt", wintypes.POINT), ("private", wintypes.DWORD)]
    u.DefWindowProcW.argtypes = [c_void_p, c_uint, c_size_t, c_ssize_t]
    u.DefWindowProcW.restype = c_ssize_t
    u.CreateWindowExW.argtypes = [wintypes.DWORD, c_wchar_p, c_wchar_p, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, c_void_p, c_void_p, c_void_p, c_void_p]
    u.CreateWindowExW.restype = c_void_p
    u.DestroyWindow.argtypes = [c_void_p]
    u.SendMessageTimeoutW.argtypes = [c_void_p, c_uint, c_size_t, c_void_p, c_uint, c_uint, POINTER(c_size_t)]
    u.SendMessageTimeoutW.restype = c_ssize_t
    u.PeekMessageW.argtypes = [POINTER(MSG), c_void_p, c_uint, c_uint, c_uint]
    u.DispatchMessageW.argtypes = [POINTER(MSG)]
    u.TranslateMessage.argtypes = [POINTER(MSG)]
    u.RegisterClassW.argtypes = [POINTER(WNDCLASSW)]
    u.UnregisterClassW.argtypes = [c_wchar_p, c_void_p]
    u.MsgWaitForMultipleObjects.argtypes = [wintypes.DWORD, c_void_p, wintypes.BOOL, wintypes.DWORD, wintypes.DWORD]
    k.GetModuleHandleW.restype = c_void_p
    got: list[bytes] = []

    def proc(hwnd, msg, wparam, lparam):
        if msg == 0x004A and lparam:  # WM_COPYDATA
            cds = ctypes.cast(c_void_p(lparam), POINTER(COPYDATASTRUCT)).contents
            if cds.dwData == _REPLY_ID and cds.lpData:
                got.append(ctypes.string_at(cds.lpData, cds.cbData))
                return 1
        return u.DefWindowProcW(hwnd, msg, wparam, lparam)
    wndproc = WNDPROC(proc)
    inst = k.GetModuleHandleW(None)
    cls_name = f"CorefoldEverythingReply{threading.get_ident()}"
    wc = WNDCLASSW(0, wndproc, 0, 0, inst, None, None, None, None, cls_name)
    if not u.RegisterClassW(byref(wc)):
        return None
    hwnd = u.CreateWindowExW(0, cls_name, None, 0, 0, 0, 0, 0, c_void_p(-3), None, inst, None)  # HWND_MESSAGE
    try:
        if not hwnd:
            return None
        search = text.strip() + "\0"
        body = struct.pack("<7I", int(hwnd) & 0xFFFFFFFF, _REPLY_ID, 0, 0, max(1, limit), _REQ_NAME | _REQ_PATH,
                           _SORT_NAME) + search.encode("utf-16-le")
        buf = ctypes.create_string_buffer(body, len(body))
        cds = COPYDATASTRUCT(_QUERY2W, len(body), ctypes.cast(buf, c_void_p))
        result = c_size_t()
        if not u.SendMessageTimeoutW(c_void_p(target), 0x004A, int(hwnd), byref(cds), 0x2, 800, byref(result)):
            return None  # SMTO_ABORTIFHUNG
        if not result.value:
            return None
        end = time.monotonic() + timeout
        msg = MSG()
        while not got and time.monotonic() < end and not stale():
            u.MsgWaitForMultipleObjects(0, None, False, 20, 0x04FF)  # QS_ALLINPUT
            while u.PeekMessageW(byref(msg), None, 0, 0, 0x1):  # PM_REMOVE
                u.TranslateMessage(byref(msg))
                u.DispatchMessageW(byref(msg))
        return parse_everything(got[0]) if got else None
    finally:
        if hwnd:
            u.DestroyWindow(c_void_p(hwnd))
        u.UnregisterClassW(cls_name, inst)
        del wndproc


# ---------------------------------------------------------------------------- files: Windows' search index

def aqs(text: str) -> str:
    """What's typed as a search for Windows' index: each word on its own, and words with signs in
    them in quotes (in Windows' search, "-" before a word would mean "not")."""
    words = []
    for w in text.replace('"', " ").split():
        words.append(w if re.fullmatch(r"\w+", w) else f'"{w}"')
    return " ".join(words)


_indexed: dict[str, bool] = {}
_indexed_at = 0.0


async def _index_query(text: str, folders: list[str], limit: int) -> list[Found] | None:
    global _indexed_at
    from winrt.windows.storage import StorageFolder, StorageItemTypes
    from winrt.windows.storage.search import FolderDepth, IndexedState, IndexerOption, QueryOptions
    if time.monotonic() - _indexed_at > APPS_FRESH_S:
        _indexed.clear()
        _indexed_at = time.monotonic()

    async def one(path: str):
        folder = await StorageFolder.get_folder_from_path_async(path)
        if path not in _indexed:
            state = await folder.get_indexed_state_async()
            _indexed[path] = state in (IndexedState.FULLY_INDEXED, IndexedState.PARTIALLY_INDEXED)
        if not _indexed[path]:
            return None
        options = QueryOptions()
        options.folder_depth = FolderDepth.DEEP
        options.indexer_option = IndexerOption.ONLY_USE_INDEXER
        options.user_search_filter = aqs(text)
        try:
            items = await folder.create_item_query_with_options(options).get_items_async(0, limit)
        except Exception:  # (a folder that can't list folders and files together)
            items = await folder.create_file_query_with_options(options).get_files_async(0, limit)
        found = []
        for it in items:
            try:
                found.append(Found(it.path, it.name, it.is_of_type(StorageItemTypes.FOLDER)))
            except Exception:
                continue
        return found
    answers = await asyncio.gather(*(one(f) for f in folders), return_exceptions=True)
    lists = [a for a in answers if isinstance(a, list)]
    for a in answers:
        if isinstance(a, BaseException):
            log.debug("index search: %s", a)
    if not lists:
        return None  # (not indexed, or no index at all)
    return [f for lst in lists for f in lst]


def index_query(text: str, limit: int = MAX_FILES) -> list[Found] | None:
    """Files in the person's folders that Windows' search index has for what's typed. None: there's
    no index (or no way to ask it)."""
    if not IS_WIN:
        return None
    try:
        import winrt.windows.storage.search  # noqa: F401
    except Exception:
        return None
    folders = []
    for name in USER_FOLDERS:
        p = known_folder(name)
        if p and os.path.isdir(p) and p not in folders:
            folders.append(p)
    if not folders:
        return None
    try:
        return asyncio.run(_index_query(text, folders, limit))
    except Exception:
        log.debug("index search failed", exc_info=True)
        return None


def _init_thread():
    """Windows' runtime on the search thread (once)."""
    if not IS_WIN:
        return
    try:
        from winrt import runtime
        runtime.init_apartment(runtime.ApartmentType.MULTI_THREADED)
    except Exception:
        pass


# ---------------------------------------------------------------------------- files: Recent items

def lnk_target(path: str) -> str:
    """Where a Windows shortcut (.lnk) points, read from the file itself (its LinkInfo part). "" if
    it doesn't say (a shortcut to something that isn't a file or folder)."""
    try:
        with open(path, "rb") as f:
            data = f.read(1 << 16)
    except OSError:
        return ""
    try:
        if len(data) < 0x4C or data[:4] != b"\x4c\x00\x00\x00":
            return ""
        (flags,) = struct.unpack_from("<I", data, 0x14)
        pos = 0x4C
        if flags & 0x1:  # HasLinkTargetIDList
            (n,) = struct.unpack_from("<H", data, pos)
            pos += 2 + n
        if not flags & 0x2:  # HasLinkInfo
            return ""
        li = pos
        _size, header, li_flags, _vol, base_off, _net, suffix_off = struct.unpack_from("<7I", data, li)
        if not li_flags & 0x1:  # VolumeIDAndLocalBasePath
            return ""

        def wide(at: int) -> str:
            end = at
            while end + 1 < len(data) and data[end:end + 2] != b"\0\0":
                end += 2
            return data[at:end].decode("utf-16-le", "replace")

        def narrow(at: int) -> str:
            end = data.find(b"\0", at)
            raw = data[at:end if end >= 0 else len(data)]
            try:
                return raw.decode("mbcs")
            except (LookupError, UnicodeDecodeError):
                return raw.decode("latin-1")
        if header >= 0x24:
            base_u, suffix_u = struct.unpack_from("<2I", data, li + 28)
            base = wide(li + base_u)
            suffix = wide(li + suffix_u) if suffix_u else ""
        else:
            base = narrow(li + base_off)
            suffix = narrow(li + suffix_off) if suffix_off else ""
        if suffix and not base.endswith("\\"):
            base += "\\"
        return base + suffix
    except struct.error:
        return ""


def recent_query(text: str, limit: int = MAX_FILES) -> list[Found]:
    """Files and folders opened lately (Windows' Recent items) whose names fit what's typed."""
    folder = known_folder("recent")
    q = text.strip().lower()
    hits = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                low = e.name.lower()
                if not low.endswith(".lnk"):
                    continue
                name = e.name[:-4]
                s = file_score(q, name)
                if s <= 10:
                    continue
                try:
                    when = e.stat().st_mtime
                except OSError:
                    when = 0
                hits.append((s, when, e.path))
    except OSError:
        return []
    hits.sort(key=lambda x: (-x[0], -x[1]))
    out: list[Found] = []
    for _, _, lnk in hits[:limit * 3]:
        target = lnk_target(lnk)
        if not target or not os.path.exists(target):
            continue
        out.append(Found(target, os.path.basename(target.rstrip("\\")) or target, os.path.isdir(target)))
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------- the search, all of it

def find_files(text: str, limit: int = MAX_FILES, stale=lambda: False) -> tuple[str, list[Found]]:
    """(where they came from: "everything", "index" or "recent", the files) for what's typed, the
    ones whose names fit best first."""
    source, found = "recent", None
    if not stale():
        found = everything_query(text, stale=stale)
        if found is not None:
            source = "everything"
    if found is None and not stale():
        found = index_query(text, limit)
        if found is not None:
            source = "index"
    found = list(found or [])
    if not stale():
        found += recent_query(text, limit)
    seen: set[str] = set()
    ranked = []
    for i, f in enumerate(found):
        key = os.path.normcase(os.path.normpath(f.path))
        if key in seen:
            continue
        seen.add(key)
        ranked.append((file_score(text, f.name), i, f))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return source, [f for _, _, f in ranked[:limit]]


class _Worker:
    """One thread that does the searches, newest first (an older one still waiting is skipped). It
    ends by itself after a minute with nothing to do."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()

    def submit(self, job):
        with self.lock:
            self.q.put(job)
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, name="corefold-find", daemon=True)
                self.thread.start()

    def _run(self):
        _init_thread()
        while True:
            try:
                job = self.q.get(timeout=WORKER_IDLE_S)
            except queue.Empty:
                with self.lock:
                    if self.q.empty():
                        self.thread = None
                        return
                continue
            while True:  # (only the newest)
                try:
                    job = self.q.get_nowait()
                except queue.Empty:
                    break
            try:
                job()
            except Exception:
                log.exception("quick menu: a search failed")


_worker = _Worker()


class Finder(QObject):
    """The quick menu's side of it: search(text) waits for a short pause in the typing, then looks on
    the search thread. found is emitted (on the UI thread) with what was typed and what was found;
    anything that comes back for older text, or after cancel(), is dropped."""
    found = Signal(str, object)    # text, (its number, list[Found]): from the search thread
    updated = Signal()             # new files for what's typed now (on the UI thread)
    apps_ready = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.gen = 0
        self.text = ""
        self.files: list[Found] = []
        self.files_for = ""
        self.source = ""
        self.took_ms = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(DEBOUNCE_MS)
        self.timer.timeout.connect(self._go)
        self.found.connect(self._got)

    def warm(self):
        """The list of apps, made the first time the menu opens (and again after 10 minutes)."""
        refresh_apps(self._apps_done)

    def _apps_done(self):
        try:
            self.apps_ready.emit()
        except RuntimeError:  # (the menu was put away meanwhile)
            pass

    def apps(self, query: str) -> list[tuple[int, App]]:
        return match_apps(apps(), query) if len(query.strip()) >= MIN_CHARS else []

    def search(self, text: str):
        text = text.strip()
        if text == self.text:
            return  # (already looked for, or being looked for)
        self.gen += 1
        self.text = text
        if len(text) < MIN_CHARS:
            self.timer.stop()
            self.files, self.files_for = [], ""
            return
        self.timer.start()

    def cancel(self):
        self.gen += 1
        self.timer.stop()
        self.text = ""
        self.files, self.files_for = [], ""

    def _go(self):
        gen, text = self.gen, self.text
        started = time.monotonic()

        def stale():
            return self.gen != gen

        def job():
            if stale():
                return
            source, files = find_files(text, stale=stale)
            if stale():
                return
            self.source = source
            self.took_ms = round((time.monotonic() - started) * 1000)
            try:
                self.found.emit(text, (gen, files))
            except RuntimeError:
                pass
        _worker.submit(job)

    def _got(self, text: str, payload):
        gen, files = payload
        if gen != self.gen or text != self.text:
            return  # (typed on, or the menu closed)
        self.files, self.files_for = list(files), text
        self.updated.emit()
        log.info("quick menu: %d files from %s in %d ms", len(files), self.source, self.took_ms)


def open_found(f: Found, reveal: bool = False) -> bool:
    """Opens a found file or folder, or shows it in File Explorer (selected)."""
    if not os.path.exists(f.path):
        return False
    if reveal:
        if IS_WIN:
            subprocess.Popen(f'explorer /select,"{f.path}"')
        return True
    from .quick import open_target
    return open_target(f.path)
