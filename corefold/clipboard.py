"""Corefold Clipboard: everything you copy, kept on this PC and easy to find again.

The history is a small SQLite file in Corefold's local folder, with copied pictures as PNG files
beside it. Nothing leaves the PC. Copies that ask not to be kept (password managers mark them, and
Windows' own clipboard history skips them too) are never written down.

This part has no windows (clipview.py has the popup and the page), so having the Clipboard on costs
little while idle: Windows tells Corefold when something is copied, and it writes one small row."""
from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPixmap

from .hotkey import SHORTCUT_NAMES, SHORTCUTS, Hotkey
from .settings import LOG_DIR

log = logging.getLogger("corefold")

FOLDER = LOG_DIR / "Clipboard"
KINDS = ("text", "link", "image", "files")
MAX_TEXT = 4 * 2**20       # characters: a bigger copy (a whole log file) isn't kept
MAX_HTML = 4 * 2**20       # (its formatting is dropped above this; the text is still kept)
MAX_PIXELS = 64_000_000    # a bigger picture isn't kept
THUMB = 160                # px: the longest side of a picture's small preview
SETTLE_MS = 120            # apps often put several things on the clipboard in a row: read the last
TRIM_EVERY_S = 60

# the shortcut that opens the clipboard (see hotkey.py)
HOTKEYS = {k: SHORTCUTS[k] for k in ("win+alt+v", "ctrl+shift+v", "ctrl+alt+v", "ctrl+`", "off")}
HOTKEY_NAMES = {k: SHORTCUT_NAMES[k] for k in HOTKEYS if k != "off"}

# copies marked like this ask clipboard tools not to keep them (password managers, banking apps)
PRIVATE_FORMATS = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")
HISTORY_FORMAT = "CanIncludeInClipboardHistory"   # a DWORD: 0 = don't keep
# password managers whose copies are never kept, even when they don't mark them
PASSWORD_APPS = {"keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe", "dashlane.exe", "lastpass.exe",
                 "enpass.exe", "roboform.exe", "robotaskbaricon.exe", "nordpass.exe", "keeperpasswordmanager.exe",
                 "proton pass.exe", "protonpass.exe", "pwsafe.exe", "passwordsafe.exe", "kee.exe", "sticky password.exe",
                 "stpass.exe", "zoho vault.exe", "authy desktop.exe", "keychain.exe"}

_URL = re.compile(r"^(?:https?://|www\.)[^\s<>\"]+$", re.I)
_SPACES = re.compile(r"\s+")
_ADDRESS = re.compile(r"^(?:[a-z]+://|[a-z]:\\|\\\\)\S*$", re.I)


def kind_of_text(text: str) -> str:
    t = text.strip()
    return "link" if len(t) < 4096 and _URL.match(t) else "text"


# quick changes to copied text before it's pasted (English names, shown through tr())
CHANGES = (("upper", "UPPERCASE"), ("lower", "lowercase"), ("title", "Capital Letters"),
           ("spaces", "Remove extra spaces"), ("one_line", "Join into one line"), ("json", "Tidy JSON"))


def change_text(text: str, how: str) -> str:
    """The text changed one of the CHANGES ways (as it was, if it can't be: JSON that isn't JSON)."""
    if how == "upper":
        return text.upper()
    if how == "lower":
        return text.lower()
    if how == "title":
        return " ".join(w[:1].upper() + w[1:].lower() for w in text.split(" "))
    if how == "spaces":
        lines = [_SPACES.sub(" ", line).strip() for line in text.splitlines()]
        out: list[str] = []
        for line in lines:  # (at most one empty line in a row)
            if line or (out and out[-1]):
                out.append(line)
        return "\n".join(out).strip()
    if how == "one_line":
        return _SPACES.sub(" ", " ".join(text.split())).strip()
    if how == "json":
        import json
        try:
            return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except ValueError:
            return text
    return text


def text_item(text: str) -> "Item":
    """A copy made here (edited or changed text), not in the history yet."""
    return Item(kind=kind_of_text(text), text=text, digest=_digest("text", text), size=len(text))


def ignored_apps(settings) -> set[str]:
    """The programs (like bank.exe) whose copies are never kept, as the person typed them in Settings."""
    names = set()
    for part in (settings.clip_ignore or "").replace(";", ",").split(","):
        part = part.strip().strip('"').lower()
        if part:
            names.add(part if part.endswith(".exe") else part + ".exe")
    return names


def _digest(kind: str, data) -> str:
    h = hashlib.sha1(kind.encode())
    h.update(b"\0")
    h.update(data.encode("utf-8", "surrogatepass") if isinstance(data, str) else data)
    return h.hexdigest()


@dataclass
class Item:
    kind: str = "text"      # text, link, image or files
    text: str = ""          # the text, the link, or the files (one path per line)
    html: str = ""          # the formatting it was copied with (pasted back unless "plain text")
    image: str = ""         # the picture's file name (in FOLDER / "images")
    width: int = 0
    height: int = 0
    digest: str = ""
    pinned: bool = False
    created: float = 0.0
    used: float = 0.0       # copied (or pasted from here) last
    app: str = ""           # the program it was copied from ("Google Chrome")
    size: int = 0           # characters, or the picture file's bytes
    id: int = 0
    partial: bool = False   # only the start of the text (from a list)

    @property
    def files(self) -> list[str]:
        return [line for line in self.text.splitlines() if line] if self.kind == "files" else []

    def title(self, limit: int = 300) -> str:
        """One line to show for it."""
        if self.kind == "files":
            names = [Path(f).name or f for f in self.files]
            return ", ".join(names)[:limit]
        if self.kind == "image":
            return ""
        return _SPACES.sub(" ", self.text[:limit * 4]).strip()[:limit]


_COLUMNS = ("id", "kind", "text", "html", "image", "width", "height", "digest", "pinned", "created", "used", "app",
            "size")
PREVIEW = 2000  # characters of a text a list holds (a copied 4 MB log file isn't held 300 times)
_LIGHT = tuple(f"substr(text, 1, {PREVIEW})" if c == "text" else "''" if c == "html" else c for c in _COLUMNS)


def _row(r) -> Item:
    d = dict(zip(_COLUMNS, r))
    d["pinned"] = bool(d["pinned"])
    return Item(**d)


class History:
    """The kept copies, newest first (pinned ones on top). Opened on first use."""

    def __init__(self, folder: Path = FOLDER):
        self.folder = folder
        self.images = folder / "images"
        self._db: sqlite3.Connection | None = None
        self._opening: threading.Thread | None = None

    def open_early(self):
        """Opens the history in the background, so the first copy or the first look at it doesn't wait
        (opening it can take a second while Windows' virus check looks at the files)."""
        if self._db is None and self._opening is None:
            self._opening = threading.Thread(target=self._open_quietly, daemon=True)
            self._opening.start()

    def _open_quietly(self):
        try:
            self._open()
        except Exception:
            log.debug("couldn't open the clipboard history early", exc_info=True)

    @property
    def db(self) -> sqlite3.Connection:
        if self._opening is not None:
            self._opening.join()
            self._opening = None
        if self._db is None:
            self._open()
        return self._db

    def _open(self):
        if self._db is None:
            self.images.mkdir(parents=True, exist_ok=True)
            # (used by one thread at a time: made on the opening one, then only on the app's)
            db = sqlite3.connect(str(self.folder / "history.db"), check_same_thread=False)
            try:  # each copy is kept without waiting for the disk (it used to hold the app up to a second)
                db.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute("PRAGMA cache_size=-512")  # (KB: it stays small while Corefold waits in the tray)
            db.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, text TEXT,"
                       " html TEXT, image TEXT, width INTEGER, height INTEGER, digest TEXT UNIQUE NOT NULL,"
                       " pinned INTEGER DEFAULT 0, created REAL, used REAL, app TEXT, size INTEGER)")
            db.execute("CREATE INDEX IF NOT EXISTS items_used ON items (pinned, used)")
            db.commit()
            self._db = db

    def close(self):
        if self._opening is not None:
            self._opening.join()
            self._opening = None
        if self._db is not None:
            try:
                self._db.close()
            except sqlite3.Error:
                pass
            self._db = None

    def add(self, item: Item) -> Item:
        """Keeps it, or moves the same copy kept before back to the top."""
        now = time.time()
        old = self.db.execute("SELECT id, pinned, created FROM items WHERE digest = ?", (item.digest,)).fetchone()
        if old:
            item.id, item.pinned, item.created = old[0], bool(old[1]), old[2]
            item.used = now
            self.db.execute("UPDATE items SET used = ?, app = COALESCE(NULLIF(?, ''), app),"
                            " html = COALESCE(NULLIF(?, ''), html) WHERE id = ?", (now, item.app, item.html, item.id))
        else:
            item.created = item.used = now
            cur = self.db.execute("INSERT INTO items (kind, text, html, image, width, height, digest, pinned, created,"
                                  " used, app, size) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                  (item.kind, item.text, item.html, item.image, item.width, item.height, item.digest,
                                   int(item.pinned), item.created, item.used, item.app, item.size))
            item.id = cur.lastrowid
        self.db.commit()
        return item

    def has(self, digest: str) -> bool:
        return self.db.execute("SELECT 1 FROM items WHERE digest = ?", (digest,)).fetchone() is not None

    def items(self, search: str = "", kind: str = "", limit: int = 400) -> list[Item]:
        """For lists: the newest first, with only the start of long texts (get() has the whole thing)."""
        where, args = [], []
        if kind == "pinned":
            where.append("pinned = 1")
        elif kind:
            where.append("kind = ?")
            args.append(kind)
        for word in search.split()[:8]:
            like = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(text LIKE ? ESCAPE '\\' OR app LIKE ? ESCAPE '\\')")
            args += [like, like]
        sql = "SELECT " + ", ".join(_LIGHT) + " FROM items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY pinned DESC, used DESC LIMIT ?"
        rows = [_row(r) for r in self.db.execute(sql, args + [limit])]
        for item in rows:
            item.partial = item.kind in ("text", "link") and len(item.text) < item.size
        return rows

    def get(self, item_id: int) -> Item | None:
        r = self.db.execute("SELECT " + ", ".join(_COLUMNS) + " FROM items WHERE id = ?", (item_id,)).fetchone()
        return _row(r) if r else None

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    def touch(self, item_id: int):
        self.db.execute("UPDATE items SET used = ? WHERE id = ?", (time.time(), item_id))
        self.db.commit()

    def pin(self, item_id: int, on: bool):
        self.db.execute("UPDATE items SET pinned = ? WHERE id = ?", (int(on), item_id))
        self.db.commit()

    def _forget(self, where: str, args=()) -> int:
        rows = self.db.execute("SELECT id, image FROM items WHERE " + where, args).fetchall()
        if not rows:
            return 0
        self.db.executemany("DELETE FROM items WHERE id = ?", [(r[0],) for r in rows])
        self.db.commit()
        for _, image in rows:
            if image:
                for name in (image, thumb_name(image)):
                    try:
                        (self.images / name).unlink()
                    except OSError:
                        pass
        return len(rows)

    def delete(self, item_id: int) -> int:
        return self._forget("id = ?", (item_id,))

    def clear(self, keep_pinned: bool = True) -> int:
        n = self._forget("pinned = 0" if keep_pinned else "1 = 1")
        if not keep_pinned:
            self.db.execute("VACUUM")
        return n

    def trim(self, max_items: int, max_days: int) -> int:
        """Forgets the oldest copies (never pinned ones) past the limits."""
        n = 0
        if max_days > 0:
            n += self._forget("pinned = 0 AND used < ?", (time.time() - max_days * 86400,))
        if max_items > 0:
            n += self._forget("pinned = 0 AND id NOT IN (SELECT id FROM items WHERE pinned = 0"
                              " ORDER BY used DESC LIMIT ?)", (max_items,))
        return n

    def image_path(self, item: Item) -> Path:
        return self.images / item.image

    def thumb_path(self, item: Item) -> Path:
        return self.images / thumb_name(item.image)


def thumb_name(image: str) -> str:
    return image.rsplit(".", 1)[0] + ".small.png"


# ----------------------------------------------------------------------------- reading the clipboard

def from_mime(md, keep_images: bool = True) -> tuple[Item, QImage | None] | None:
    """What was copied, as an Item (with its picture, which is saved later), or None if there's
    nothing to keep."""
    if md is None:
        return None
    if md.hasUrls():
        paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile() and u.toLocalFile()]
        if paths:
            text = "\n".join(os.path.normpath(p) for p in paths)
            return Item(kind="files", text=text, digest=_digest("files", text), size=len(paths)), None
    picture_first = keep_images and md.hasImage()
    if md.hasText():
        text = md.text()
        if picture_first and _ADDRESS.match(text.strip()):  # "Copy image" in a browser: the picture, not its address
            text = ""
        if text.strip():
            if len(text) > MAX_TEXT:
                return None
            html = md.html() if md.hasHtml() else ""
            if len(html) > MAX_HTML:
                html = ""
            kind = kind_of_text(text)
            if kind == "link":
                text = text.strip()
            return Item(kind=kind, text=text, html=html, digest=_digest("text", text), size=len(text)), None
    if keep_images and md.hasImage():
        img = md.imageData()
        if isinstance(img, QPixmap):
            img = img.toImage()
        if not isinstance(img, QImage) or img.isNull() or img.width() * img.height() > MAX_PIXELS:
            return None
        return Item(kind="image", width=img.width(), height=img.height()), img
    return None


def is_private(md) -> bool:
    """True for a copy that asks not to be kept (a password, usually)."""
    if md is None:
        return False
    formats = md.formats()
    if any(name in f for f in formats for name in PRIVATE_FORMATS):
        return True
    fmt = next((f for f in formats if HISTORY_FORMAT in f), None)
    if os.name == "nt":
        try:
            u = ctypes.windll.user32
            u.RegisterClipboardFormatW.restype = ctypes.c_uint
            for name in PRIVATE_FORMATS:
                if u.IsClipboardFormatAvailable(u.RegisterClipboardFormatW(name)):
                    return True
            if fmt is None and u.IsClipboardFormatAvailable(u.RegisterClipboardFormatW(HISTORY_FORMAT)):
                fmt = f'application/x-qt-windows-mime;value="{HISTORY_FORMAT}"'
        except Exception:
            pass
    if fmt is not None:
        data = bytes(md.data(fmt))
        return not (len(data) >= 4 and int.from_bytes(data[:4], "little") != 0)  # unreadable: don't keep
    return False


# ----------------------------------------------------------------------------- Windows: who copied, pasting

def sequence_number() -> int | None:
    """Windows counts every change to the clipboard (used to tell Corefold's own copies apart)."""
    if os.name != "nt":
        return None
    try:
        return int(ctypes.windll.user32.GetClipboardSequenceNumber())
    except Exception:
        return None


def clipboard_app() -> str:
    """The program that put the current thing on the clipboard (its .exe path), if Windows knows."""
    if os.name != "nt":
        return ""
    try:
        import ctypes.wintypes as wt
        u = ctypes.windll.user32
        u.GetClipboardOwner.restype = wt.HWND
        hwnd = u.GetClipboardOwner()
        if not hwnd:
            return ""
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
        return process_path(pid.value)
    except Exception:
        return ""


def process_path(pid: int) -> str:
    import ctypes.wintypes as wt
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = wt.HANDLE
    h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if k.QueryFullProcessImageNameW(wt.HANDLE(h), 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        k.CloseHandle(wt.HANDLE(h))


_APP_NAMES: dict[str, str] = {}


def app_name(path: str) -> str:
    """A program's own name ("Google Chrome" for chrome.exe), or its file name."""
    if not path:
        return ""
    if path in _APP_NAMES:
        return _APP_NAMES[path]
    name = ""
    if os.name == "nt":
        try:
            v = ctypes.windll.version
            size = v.GetFileVersionInfoSizeW(path, None)
            if size:
                data = ctypes.create_string_buffer(size)
                if v.GetFileVersionInfoW(path, 0, size, data):
                    ptr, length = ctypes.c_void_p(), ctypes.c_uint()
                    if v.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)):
                        lang, cp = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort * 2)).contents
                        key = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\FileDescription"
                        if v.VerQueryValueW(data, key, ctypes.byref(ptr), ctypes.byref(length)) and length.value:
                            name = ctypes.wstring_at(ptr, length.value).strip("\0 ").strip()
        except Exception:
            name = ""
    if not name or len(name) > 40:
        name = Path(path).stem.replace("_", " ")
        name = name[:1].upper() + name[1:]
    if len(_APP_NAMES) > 200:
        _APP_NAMES.clear()
    _APP_NAMES[path] = name
    return name


def foreground_window() -> int:
    if os.name != "nt":
        return 0
    try:
        ctypes.windll.user32.GetForegroundWindow.restype = ctypes.c_void_p
        return ctypes.windll.user32.GetForegroundWindow() or 0
    except Exception:
        return 0


def focus_window(hwnd: int) -> bool:
    if os.name != "nt" or not hwnd:
        return False
    try:
        u = ctypes.windll.user32
        u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        u.IsWindow.argtypes = [ctypes.c_void_p]
        return bool(u.IsWindow(hwnd) and u.SetForegroundWindow(hwnd))
    except Exception:
        return False


VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, VK_V = 0x10, 0x11, 0x12, 0x5B, 0x5C, 0x56
HELD_KEYS = (VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN, VK_CONTROL)


def keys_held() -> bool:
    """True while the person still holds Shift, Alt, Ctrl or the Windows key (pasting waits for them)."""
    if os.name != "nt":
        return False
    u = ctypes.windll.user32
    return any(u.GetAsyncKeyState(vk) & 0x8000 for vk in HELD_KEYS)


def press_paste():
    """Presses Ctrl+V in the window in front."""
    if os.name != "nt":
        return
    u = ctypes.windll.user32
    for vk in HELD_KEYS:  # (a key still held would turn Ctrl+V into something else)
        if u.GetAsyncKeyState(vk) & 0x8000:
            u.keybd_event(vk, 0, 2, 0)
    u.keybd_event(VK_CONTROL, 0, 0, 0)
    u.keybd_event(VK_V, 0, 0, 0)
    u.keybd_event(VK_V, 0, 2, 0)
    u.keybd_event(VK_CONTROL, 0, 2, 0)


# ----------------------------------------------------------------------------- saving pictures

class _Saved(QObject):
    done = Signal(object)


class _SavePicture(QRunnable):
    """Works out a copied picture's fingerprint and saves it (and a small preview) as PNG, away
    from the window's thread (a 4K screenshot takes a moment to compress)."""

    def __init__(self, item: Item, image: QImage, folder: Path, signals: _Saved):
        super().__init__()
        self.item, self.image, self.folder, self.signals = item, image, folder, signals

    def run(self):
        try:
            img = self.image
            if img.format() not in (QImage.Format.Format_ARGB32, QImage.Format.Format_RGB32):
                img = img.convertToFormat(QImage.Format.Format_ARGB32)
            digest = _digest("image", bytes(img.constBits()))
            item = self.item
            item.digest, item.image = digest, digest + ".png"
            target = self.folder / item.image
            if not target.exists():
                self.folder.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(".tmp")
                if not img.save(str(tmp), "PNG"):
                    raise OSError("couldn't save the picture")
                os.replace(tmp, target)
                small = img.scaled(THUMB, THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation) \
                    if max(img.width(), img.height()) > THUMB else img
                small.save(str(self.folder / thumb_name(item.image)), "PNG")
            item.size = target.stat().st_size
            self.signals.done.emit(item)
        except Exception:
            log.exception("couldn't keep a copied picture")


# ----------------------------------------------------------------------------- the clipboard tool

class Clipboard(QObject):
    """Corefold Clipboard while it's on: listens for copies and keeps them, and opens the popup
    with its shortcut."""
    changed = Signal()   # the history changed (something was copied, pinned or deleted)

    def __init__(self, settings, folder: Path = FOLDER):
        super().__init__()
        self.settings = settings
        self.history = History(folder)
        self.history.open_early()
        self.popup = None
        self.on_hotkey = None        # set by the app: opens the popup
        self._own: int | None = None  # the clipboard's change number after Corefold copied something itself
        self._own_digest = ""
        self._last_trim = 0.0
        self.cb = QGuiApplication.clipboard()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._read)
        self.cb.dataChanged.connect(self._clipboard_changed)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.saved = _Saved()
        self.saved.done.connect(self._picture_saved)
        self.hotkey = Hotkey()
        self.hotkey.pressed.connect(self._hotkey_pressed, Qt.ConnectionType.QueuedConnection)
        self.hotkey.set(settings.clip_shortcut)

    def stop(self):
        try:
            self.cb.dataChanged.disconnect(self._clipboard_changed)
        except (RuntimeError, TypeError):
            pass
        self.timer.stop()
        self.hotkey.stop()
        self.pool.waitForDone(3000)
        if self.popup is not None:
            self.popup.close()
            self.popup.deleteLater()
            self.popup = None
        self.history.close()

    def apply_settings(self) -> bool:
        """After the Clipboard's settings changed. False if the shortcut couldn't be used."""
        ok = self.hotkey.set(self.settings.clip_shortcut)
        self.trim(force=True)
        return ok

    def _hotkey_pressed(self):
        if self.on_hotkey:
            self.on_hotkey()

    # ---- keeping copies

    def _clipboard_changed(self):
        self.timer.start(SETTLE_MS)

    def _read(self):
        if self.settings.clip_paused:
            return
        seq = sequence_number()
        if self._own is not None and seq is not None and seq == self._own:
            return  # Corefold's own copy (pasting from the history): already kept
        try:
            md = self.cb.mimeData()
            if md is None or is_private(md):
                return
            app_path = clipboard_app()
            exe = os.path.basename(app_path).lower()
            if exe in PASSWORD_APPS or (exe and exe in ignored_apps(self.settings)):
                return
            found = from_mime(md, self.settings.clip_images)
        except Exception:
            log.exception("couldn't read what was copied")
            return
        if found is None:
            return
        item, image = found
        if seq is None and item.digest and item.digest == self._own_digest:  # (no change numbers: off Windows)
            self._own_digest = ""
            return
        item.app = app_name(app_path)
        if image is not None:
            self.pool.start(_SavePicture(item, image, self.history.images, self.saved))
            return
        self._keep(item)

    def _picture_saved(self, item: Item):
        self._keep(item)

    def _keep(self, item: Item):
        try:
            self.history.add(item)
        except sqlite3.Error:
            log.exception("couldn't keep a copy in the clipboard history")
            return
        self.trim()
        self.changed.emit()

    def trim(self, force: bool = False):
        now = time.time()
        if not force and now - self._last_trim < TRIM_EVERY_S:
            return
        self._last_trim = now
        try:
            if self.history.trim(self.settings.clip_max, self.settings.clip_days):
                self.changed.emit()
        except sqlite3.Error:
            log.exception("couldn't tidy the clipboard history")

    # ---- using them

    def mime_for(self, item: Item, plain: bool = False):
        from PySide6.QtCore import QMimeData, QUrl
        md = QMimeData()
        if item.kind == "files":
            md.setUrls([QUrl.fromLocalFile(f) for f in item.files])
            md.setText("\n".join(item.files))
        elif item.kind == "image":
            img = QImage(str(self.history.image_path(item)))
            if img.isNull():
                return None
            md.setImageData(img)
        else:
            md.setText(item.text)
            if item.html and not plain:
                md.setHtml(item.html)
        return md

    def copy(self, item: Item, plain: bool = False) -> bool:
        """Puts it back on the clipboard (and at the top of the history)."""
        if item.id and (item.partial or item.kind in ("text", "link")):
            try:
                item = self.history.get(item.id) or item
            except sqlite3.Error:
                pass
        md = self.mime_for(item, plain)
        if md is None:
            return False
        self._own_digest = item.digest
        self.cb.setMimeData(md)
        self._own = sequence_number()
        try:
            self.history.touch(item.id)
        except sqlite3.Error:
            pass
        self.changed.emit()
        return True

    def pin(self, item: Item, on: bool):
        self.history.pin(item.id, on)
        self.changed.emit()

    def save_text(self, text: str, instead_of: Item | None = None) -> Item:
        """A text typed in to keep at hand (an address, a reply used often): kept pinned, at the top.
        instead_of is the saved text it replaces, when one was changed."""
        item = self.history.add(text_item(text))
        self.history.pin(item.id, True)
        item.pinned = True
        if instead_of is not None and instead_of.id and instead_of.id != item.id:
            self.history.delete(instead_of.id)
        self.changed.emit()
        return item

    def delete(self, item: Item):
        self.history.delete(item.id)
        self.changed.emit()

    def clear(self) -> int:
        n = self.history.clear(keep_pinned=True)
        self.changed.emit()
        return n

    def paste(self, item: Item, hwnd: int, plain: bool = False) -> bool:
        """Copies it and pastes it into the window that was in front (when that's turned on)."""
        if not self.copy(item, plain):
            return False
        if not (self.settings.clip_paste and hwnd):
            return True
        tries = [0]

        def go():
            if keys_held() and tries[0] < 40:  # wait (up to a second) for the shortcut's keys to be let go
                tries[0] += 1
                QTimer.singleShot(25, go)
                return
            press_paste()

        focus_window(hwnd)
        QTimer.singleShot(60, go)
        return True
