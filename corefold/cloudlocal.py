"""Corefold Cloud without signing in: the folders that OneDrive, Dropbox, Google Drive and iCloud's own apps keep
on this PC. Corefold only works with those folders here (it never goes online for them); the service's app sends
the changes up, as it does for anything saved there.

Finding them reads what each app writes about itself: OneDrive's accounts in the registry, Dropbox's info.json,
Google Drive's drive (a drive called Google Drive) and the iCloudDrive folder."""
from __future__ import annotations

import contextlib
import errno
import json
import logging
import os
import shutil
import stat
import string
import threading
import time
from pathlib import Path

from .cloud import FOLDER, Account, Client, CloudError, Entry

ADDED_FILE = FOLDER / "folders.json"   # cloud folders the person added by hand (only their places: nothing secret)
_added_lock = threading.Lock()
SPACE_S = 20.0                         # how long adding up a big cloud folder's files may take

log = logging.getLogger("corefold")

ONLINE_ONLY = 0x400000 | 0x40000      # RECALL_ON_DATA_ACCESS / RECALL_ON_OPEN: kept online, not on this PC yet
HIDDEN = 0x2 | 0x4                    # hidden / system
SKIP = {"desktop.ini", "thumbs.db", ".ds_store", "icon\r"}
PIECE = 1024 * 1024
FOUND_MAX, SEARCH_S = 300, 30.0


# ----------------------------------------------------------------------------- finding them

def _env(name: str) -> Path | None:
    v = os.environ.get(name, "").strip()
    return Path(v) if v else None


def _onedrive(add):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\OneDrive\Accounts") as k:
            for i in range(64):
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                try:
                    with winreg.OpenKey(k, sub) as a:
                        def value(name):
                            try:
                                return str(winreg.QueryValueEx(a, name)[0] or "")
                            except OSError:
                                return ""
                        folder = value("UserFolder")
                        if folder:
                            add("onedrive", folder, value("UserEmail"), value("DisplayName"))
                except OSError:
                    continue
    except (ImportError, OSError):
        pass
    for name in ("OneDriveConsumer", "OneDriveCommercial", "OneDrive"):
        p = _env(name)
        if p is not None:
            add("onedrive", p)


def _dropbox(add):
    for base in (_env("LOCALAPPDATA"), _env("APPDATA")):
        if base is None:
            continue
        try:
            info = json.loads((base / "Dropbox" / "info.json").read_text("utf-8"))
        except (OSError, ValueError):
            continue
        for kind in ("personal", "business"):
            d = info.get(kind) if isinstance(info, dict) else None
            if isinstance(d, dict) and d.get("path"):
                add("dropbox", d["path"], "", str(d.get("name") or ""))


def _drives() -> list[str]:
    """The drives that could be Google Drive's (fixed or network: not CDs or USB sticks)."""
    if os.name != "nt":
        return []
    import ctypes
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    out = []
    for i, letter in enumerate(string.ascii_uppercase):
        root = f"{letter}:\\"
        if mask & (1 << i) and k32.GetDriveTypeW(root) in (3, 4):
            out.append(root)
    return out


def _label(root: str) -> str:
    import ctypes
    buf = ctypes.create_unicode_buffer(261)
    if ctypes.windll.kernel32.GetVolumeInformationW(root, buf, 261, None, None, None, None, 0):
        return buf.value
    return ""


def _gdrive(add):
    base = _env("LOCALAPPDATA")
    if base is None or not (base / "Google" / "DriveFS").is_dir():
        return  # (Google Drive isn't installed: no drive is even looked at)
    for root in _drives():  # (the whole drive: "My Drive" has a name in each language, and Shared drives is there too)
        try:
            if _label(root) == "Google Drive":
                add("gdrive", Path(root))
        except OSError:
            continue
    add("gdrive", Path.home() / "My Drive")  # "Mirror files" keeps My Drive in your user folder


def _icloud(add):
    add("icloud", Path.home() / "iCloudDrive")


def find() -> list[Account]:
    """Every cloud folder kept on this PC by the service's own app."""
    found: list[Account] = []
    seen: set[str] = set()

    def add(service: str, folder, email: str = "", name: str = ""):
        p = Path(folder)
        try:
            if not p.is_dir():
                return
        except OSError:
            return
        key = os.path.normcase(os.path.abspath(str(p)))
        if key in seen:
            return
        seen.add(key)
        found.append(Account(service, email, name=name, extra={"local": str(p)}))
    for look in (_onedrive, _dropbox, _gdrive, _icloud):
        try:
            look(add)
        except Exception:  # (one odd app setup must never hide the others)
            log.exception("cloud folders: %s", look.__name__)
    for service, folder in _load_added():
        add(service, folder)
        if found and os.path.normcase(found[-1].local) == os.path.normcase(str(folder)):
            found[-1].extra["added"] = True
    return found


# ----------------------------------------------------------------------------- folders added by hand

def guess_service(folder) -> str:
    """Which cloud a folder belongs to, from its name or its drive's (any other folder is just "folder")."""
    p = Path(folder)
    names = [n.casefold() for n in (p.name, *(q.name for q in p.parents)) if n]
    try:
        if not p.name and _label(str(p)) == "Google Drive":
            return "gdrive"
    except OSError:
        pass
    for service, words in (("onedrive", ("onedrive",)), ("dropbox", ("dropbox",)),
                           ("gdrive", ("google drive", "my drive", "googledrive")),
                           ("icloud", ("iclouddrive", "icloud drive"))):
        if any(w in n for n in names for w in words):
            return service
    return "folder"


def _load_added() -> list[tuple[str, Path]]:
    with _added_lock:
        try:
            data = json.loads(ADDED_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
    out = []
    for d in data if isinstance(data, list) else []:
        if isinstance(d, dict) and isinstance(d.get("folder"), str) and d["folder"]:
            out.append((str(d.get("service") or "folder"), Path(d["folder"])))
    return out


def _save_added(items: list[tuple[str, Path]]):
    with _added_lock:
        FOLDER.mkdir(parents=True, exist_ok=True)
        tmp = ADDED_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps([{"service": s, "folder": str(f)} for s, f in items]), encoding="utf-8")
        os.replace(tmp, ADDED_FILE)


def add_folder(folder) -> Account:
    """Adds a folder (a cloud app's folder somewhere Corefold didn't look, or any synced folder) to Cloud."""
    p = Path(os.path.abspath(str(folder)))
    if not p.is_dir():
        raise CloudError("it isn't there any more")
    service = guess_service(p)
    items = [(s, f) for s, f in _load_added() if os.path.normcase(str(f)) != os.path.normcase(str(p))]
    _save_added(items + [(service, p)])
    return Account(service, "", extra={"local": str(p), "added": True})


def remove_folder(folder):
    """Takes an added folder off the list (nothing in it is touched)."""
    key = os.path.normcase(os.path.abspath(str(folder)))
    _save_added([(s, f) for s, f in _load_added() if os.path.normcase(os.path.abspath(str(f))) != key])


def space(acc: Account, cancel=None) -> tuple[int, int]:
    """(used, total) for a cloud folder on this PC. A whole drive (Google Drive's) tells both, as the account's
    space; a folder only tells what its files take (adding them up, also those kept online only), total 0."""
    root = Path(acc.local)
    if not root.name:
        try:
            u = shutil.disk_usage(str(root))
            return u.used, u.total
        except OSError:
            return 0, 0
    used = 0
    deadline = time.monotonic() + SPACE_S
    stack = [str(root)]
    while stack and time.monotonic() < deadline:
        if cancel is not None and cancel.is_set():
            break
        try:
            with os.scandir(stack.pop()) as it:
                for d in it:
                    try:
                        if d.is_symlink() or _skipped(d):
                            continue
                        if d.is_dir(follow_symlinks=False):
                            stack.append(d.path)
                        else:
                            used += d.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return used, 0


# ----------------------------------------------------------------------------- working with them

@contextlib.contextmanager
def _plain_errors():
    """What went wrong on the disk, in the words Corefold Cloud shows."""
    try:
        yield
    except CloudError:
        raise
    except FileNotFoundError as e:
        raise CloudError("it isn't there any more") from e
    except FileExistsError as e:
        raise CloudError("something with that name is already there") from e
    except PermissionError as e:
        raise CloudError("it's open in another app, or Windows didn't allow it") from e
    except OSError as e:
        if e.errno == errno.ENOSPC or getattr(e, "winerror", 0) in (39, 112):
            raise CloudError("there's no space left on this PC") from e
        raise CloudError(e.strerror or str(e)) from e


def check_name(name: str):
    from .core import WINDOWS_RESERVED
    if (not name or name in (".", "..") or name != name.rstrip(" .") or len(name) > 255
            or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name)
            or name.split(".")[0].upper() in WINDOWS_RESERVED):
        raise CloudError("that name isn't allowed")


def _skipped(d: os.DirEntry) -> bool:
    from .core import is_link
    name = d.name
    if name.startswith((".", "~$")) or name.lower() in SKIP or name.endswith(".corefold-part"):
        return True
    try:
        st = d.stat(follow_symlinks=False)  # (from the listing itself: no asking the disk again for each of 20,000)
    except OSError:
        return True
    if is_link(d.path, st):  # (a link or junction leads out of the cloud folder, or round in a circle)
        return True
    return bool(getattr(st, "st_file_attributes", 0) & HIDDEN)


class Local(Client):
    """A cloud folder on this PC, worked with like an online account."""

    def __init__(self, account: Account):
        super().__init__(account)
        self.root = Path(account.local)

    def token(self) -> str:
        return ""

    def fill_profile(self):
        pass

    def _entry(self, p: Path, st=None) -> Entry:
        return self._made(p.name, str(p), st or p.stat(), self._rel(p), str(p.parent))

    def _rel(self, p: Path) -> str:
        try:
            rel = "/" + p.relative_to(self.root).as_posix()
        except ValueError:
            rel = "/" + p.name
        return "/" if rel == "/." else rel

    @staticmethod
    def _made(name: str, path: str, st, rel: str, parent: str) -> Entry:
        folder = stat.S_ISDIR(st.st_mode)
        attrs = getattr(st, "st_file_attributes", 0)
        return Entry(name, folder, path, rel, 0 if folder else st.st_size, st.st_mtime, parent,
                     online=bool(attrs & ONLINE_ONLY))

    def _where(self, folder: Entry | None) -> Path:
        return Path(folder.id) if folder is not None else self.root

    def list(self, folder):
        out = []
        where = self._where(folder)
        # (worked out once for the folder, not for each of its 20,000 files: that took most of the time)
        base, parent = self._rel(where).rstrip("/"), str(where)
        with _plain_errors(), os.scandir(where) as it:
            for d in it:
                if _skipped(d):
                    continue
                try:
                    out.append(self._made(d.name, d.path, d.stat(), f"{base}/{d.name}", parent))
                except OSError:
                    continue
        return out

    @staticmethod
    def _copy(src: Path, dst: Path, progress, cancel):
        with _plain_errors():
            st = src.stat()
            size, done = st.st_size, 0
            # (copied under a passing name first: a copy cut short never looks like the whole file)
            part = dst.with_name(dst.name + ".corefold-part")
            try:
                with open(src, "rb") as f, open(part, "xb") as g:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise CloudError("cancelled")
                        piece = f.read(PIECE)
                        if not piece:
                            break
                        g.write(piece)
                        done += len(piece)
                        if progress is not None:
                            progress(done, size)
                if done != size:  # (it changed while it was copied: the cloud app was still syncing it)
                    from .cloud import CUT_OFF
                    raise CloudError(CUT_OFF)
                os.utime(part, (st.st_atime, st.st_mtime))
                os.rename(part, dst)  # (never over something that took the name meanwhile)
            except BaseException:
                with contextlib.suppress(OSError):
                    part.unlink()
                raise
        if progress is not None and not size:
            progress(0, 0)

    def download(self, entry, target, progress=None, cancel=None):
        self._copy(Path(entry.id), target, progress, cancel)

    def upload(self, src, folder, progress=None, cancel=None):
        from .core import unique_path
        dest = unique_path(self._where(folder) / src.name)
        self._copy(src, dest, progress, cancel)
        return self._entry(dest)

    def new_folder(self, name, folder):
        from .core import unique_path
        check_name(name)
        with _plain_errors():
            dest = unique_path(self._where(folder) / name)
            dest.mkdir()
            return self._entry(dest)

    def rename(self, entry, name):
        check_name(name)
        src = Path(entry.id)
        dest = src.with_name(name)
        with _plain_errors():
            if dest.exists() and os.path.normcase(str(dest)) != os.path.normcase(str(src)):
                raise CloudError("something with that name is already there")
            os.rename(src, dest)
            return self._entry(dest)

    def delete(self, entry):
        from .space import recycle
        p = Path(entry.id)
        with _plain_errors():
            if not os.path.lexists(p):
                raise CloudError("it isn't there any more")
            recycle([p])
            if os.path.lexists(p):
                raise CloudError("it's open in another app, or Windows didn't allow it")

    def search(self, text, cancel=None):
        want = text.casefold()
        out: list[Entry] = []
        deadline = time.monotonic() + SEARCH_S
        stack = [self.root]
        while stack and len(out) < FOUND_MAX and time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise CloudError("cancelled")
            try:
                with os.scandir(stack.pop()) as it:
                    for d in it:
                        if _skipped(d):
                            continue
                        try:
                            if d.is_dir(follow_symlinks=False):
                                stack.append(Path(d.path))
                            if want in d.name.casefold():
                                out.append(self._entry(Path(d.path), d.stat()))
                        except OSError:
                            continue
            except OSError:
                continue
        return out
