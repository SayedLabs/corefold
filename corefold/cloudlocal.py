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
import stat
import string
import time
from pathlib import Path

from .cloud import Account, Client, CloudError, Entry

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
    return found


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
    name = d.name
    if name.startswith((".", "~$")) or name.lower() in SKIP or name.endswith(".corefold-part"):
        return True
    try:
        return bool(getattr(d.stat(follow_symlinks=False), "st_file_attributes", 0) & HIDDEN)
    except OSError:
        return True


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
        st = st or p.stat()
        folder = stat.S_ISDIR(st.st_mode)
        try:
            rel = "/" + p.relative_to(self.root).as_posix()
        except ValueError:
            rel = "/" + p.name
        attrs = getattr(st, "st_file_attributes", 0)
        return Entry(p.name, folder, str(p), "/" if rel == "/." else rel, 0 if folder else st.st_size, st.st_mtime,
                     str(p.parent), online=bool(attrs & ONLINE_ONLY))

    def _where(self, folder: Entry | None) -> Path:
        return Path(folder.id) if folder is not None else self.root

    def list(self, folder):
        out = []
        with _plain_errors(), os.scandir(self._where(folder)) as it:
            for d in it:
                if _skipped(d):
                    continue
                try:
                    out.append(self._entry(Path(d.path), d.stat()))
                except OSError:
                    continue
        return out

    @staticmethod
    def _copy(src: Path, dst: Path, progress, cancel):
        with _plain_errors():
            st = src.stat()
            size, done = st.st_size, 0
            try:
                with open(src, "rb") as f, open(dst, "xb") as g:
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
                os.utime(dst, (st.st_atime, st.st_mtime))
            except BaseException:
                with contextlib.suppress(OSError):
                    dst.unlink()
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
