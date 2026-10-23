"""Corefold Space: what's filling the disk, and freeing space safely.

Looking through a drive keeps only what the map needs (each folder's size and its biggest files), so
even a drive with millions of files fits in a few dozen MB, which are let go when the page closes.
Nothing here deletes anything by itself: the page asks first, files go to the Recycle Bin, and only
junk (temporary files, caches) is deleted for good."""
from __future__ import annotations

import ctypes
import hashlib
import heapq
import logging
import os
import shutil
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("corefold")

TOP_FILES = 48         # files remembered per folder (the rest are added up as "smaller files")
TOP_OVERALL = 100      # the biggest files of the whole drive
PROGRESS_EVERY = 2000  # entries between progress reports

# reparse points that lead somewhere else (junctions, links): not followed, so nothing counts twice
_JUNCTION, _SYMLINK = 0xA0000003, 0xA000000C
_REPARSE, _OFFLINE, _RECALL_OPEN, _RECALL_ACCESS = 0x400, 0x1000, 0x40000, 0x400000

KINDS = {  # kind: English name (shown through tr())
    "video": "Videos", "picture": "Pictures", "music": "Music", "document": "Documents", "archive": "Archives",
    "program": "Programs", "other": "Other",
}
_KIND_EXT = {
    "video": "mp4 mkv mov avi wmv webm m4v mpg mpeg ts mts m2ts flv 3gp vob",
    "picture": "jpg jpeg png gif bmp tif tiff webp heic heif avif raw cr2 cr3 nef arw dng psd svg ico jfif",
    "music": "mp3 wav flac m4a aac ogg opus wma aiff alac mid",
    "document": "pdf doc docx xls xlsx ppt pptx odt ods odp txt rtf csv md epub one pst ost msg eml",
    "archive": "zip 7z rar tar gz tgz bz2 xz iso cab zipx wim img vhd vhdx",
    "program": "exe msi dll sys msix appx cab pak dat bin so lib pyd ocx",
}
KIND_OF = {f".{ext}": kind for kind, exts in _KIND_EXT.items() for ext in exts.split()}


def kind_of(name: str) -> str:
    return KIND_OF.get(os.path.splitext(name)[1].lower(), "other")


class Folder:
    __slots__ = ("name", "parent", "size", "files", "folders", "children", "big", "rest", "rest_count", "denied")

    def __init__(self, name: str, parent: "Folder | None" = None):
        self.name = name
        self.parent = parent
        self.size = 0          # everything inside, sub-folders too
        self.files = 0         # files inside, sub-folders too
        self.folders = 0
        self.children: list[Folder] = []
        self.big: list[tuple[int, str]] = []  # its own biggest files (size, name), biggest first
        self.rest = 0          # its own files that aren't in big
        self.rest_count = 0
        self.denied = False    # Windows didn't let Corefold look inside

    @property
    def path(self) -> Path:
        parts, f = [], self
        while f is not None:
            parts.append(f.name)
            f = f.parent
        return Path(*reversed(parts))

    def entries(self) -> list[tuple[int, str, "Folder | None"]]:
        """What's directly inside, biggest first: (size, name, folder or None for a file)."""
        out = [(c.size, c.name, c) for c in self.children] + [(s, n, None) for s, n in self.big]
        out.sort(key=lambda e: e[0], reverse=True)
        return out


@dataclass
class Scan:
    root: Folder
    biggest: list = field(default_factory=list)   # (size, path) of the biggest files, biggest first
    kinds: dict = field(default_factory=dict)     # kind -> bytes
    files: int = 0
    folders: int = 0
    denied: int = 0                               # folders Windows didn't let Corefold into
    cloud: int = 0                                # files that are only in the cloud (not counted)
    seconds: float = 0.0
    cancelled: bool = False


def _skip_dir(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        if os.name == "nt":
            st = entry.stat(follow_symlinks=False)
            if getattr(st, "st_file_attributes", 0) & _REPARSE and \
                    getattr(st, "st_reparse_tag", 0) in (_JUNCTION, _SYMLINK):
                return True
    except OSError:
        return True
    return False


def scan(root: Path, cancel: threading.Event | None = None, progress=None) -> Scan:
    """Looks through a folder or a whole drive. progress(files, bytes, folder) is called now and then."""
    started = time.monotonic()
    top = Folder(str(root))
    result = Scan(top)
    order = [top]
    stack = [top]
    biggest: list[tuple[int, str]] = []
    kinds = dict.fromkeys(KINDS, 0)
    seen = total = 0
    while stack:
        if cancel is not None and cancel.is_set():
            result.cancelled = True
            break
        folder = stack.pop()
        here = str(folder.path)
        heap: list[tuple[int, str]] = []
        try:
            with os.scandir(here) as it:
                for entry in it:
                    seen += 1
                    if seen % PROGRESS_EVERY == 0:
                        if progress is not None:
                            progress(result.files, total, here)
                        if cancel is not None and cancel.is_set():
                            break
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_dir:
                        if _skip_dir(entry):
                            continue
                        child = Folder(entry.name, folder)
                        folder.children.append(child)
                        order.append(child)
                        stack.append(child)
                        continue
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if not stat.S_ISREG(st.st_mode) and os.name != "nt":
                        continue
                    attrs = getattr(st, "st_file_attributes", 0)
                    if attrs & (_OFFLINE | _RECALL_ACCESS | _RECALL_OPEN):
                        result.cloud += 1  # only in the cloud: it takes no space on this PC
                        continue
                    size = st.st_size
                    folder.files += 1
                    folder.size += size
                    total += size
                    result.files += 1
                    kinds[kind_of(entry.name)] += size
                    if len(heap) < TOP_FILES:
                        heapq.heappush(heap, (size, entry.name))
                    elif size > heap[0][0]:
                        small = heapq.heapreplace(heap, (size, entry.name))
                        folder.rest += small[0]
                        folder.rest_count += 1
                    else:
                        folder.rest += size
                        folder.rest_count += 1
                    if len(biggest) < TOP_OVERALL:
                        heapq.heappush(biggest, (size, entry.path))
                    elif size > biggest[0][0]:
                        heapq.heapreplace(biggest, (size, entry.path))
        except OSError:
            folder.denied = True
            result.denied += 1
        heap.sort(reverse=True)
        folder.big = heap
    # add each folder's size to the folders around it (deepest first)
    for f in reversed(order):
        f.children.sort(key=lambda c: c.size, reverse=True)
        p = f.parent
        if p is not None:
            p.size += f.size
            p.files += f.files
            p.folders += f.folders + 1
    result.folders = top.folders
    result.biggest = sorted(biggest, reverse=True)
    result.kinds = kinds
    result.seconds = time.monotonic() - started
    return result


def squarify(sizes: list[float], x: float, y: float, w: float, h: float) -> list[tuple[float, float, float, float]]:
    """Squarified treemap: boxes for these sizes (biggest first) that fill the rectangle, each as
    close to a square as the others allow."""
    total = sum(sizes)
    if total <= 0 or w <= 0 or h <= 0 or not sizes:
        return [(x, y, 0.0, 0.0) for _ in sizes]
    scale = w * h / total
    areas = [s * scale for s in sizes]
    out: list[tuple[float, float, float, float]] = []
    i = 0
    while i < len(areas):
        short = min(w, h)
        row = [areas[i]]
        i += 1

        def worst(r):
            s = sum(r)
            if s <= 0:
                return float("inf")
            return max(max(short * short * a / (s * s), (s * s) / (short * short * a)) if a > 0 else float("inf")
                       for a in r)
        while i < len(areas) and worst(row + [areas[i]]) <= worst(row):
            row.append(areas[i])
            i += 1
        s = sum(row)
        if w >= h:  # a column on the left
            cw = s / h if h else 0
            cy = y
            for a in row:
                ch = a / cw if cw else 0
                out.append((x, cy, cw, ch))
                cy += ch
            x += cw
            w -= cw
        else:       # a row along the top
            rh = s / w if w else 0
            cx = x
            for a in row:
                rw = a / rh if rh else 0
                out.append((cx, y, rw, rh))
                cx += rw
            y += rh
            h -= rh
    return out


# ----------------------------------------------------------------------------- drives

@dataclass
class Drive:
    path: str          # "C:\\"
    label: str         # "Windows" (or "" if it has none)
    total: int
    free: int
    removable: bool = False

    @property
    def used(self) -> int:
        return max(0, self.total - self.free)


def drives() -> list[Drive]:
    """The PC's drives that hold files (not CD drives or network drives)."""
    out: list[Drive] = []
    if os.name != "nt":
        try:
            u = shutil.disk_usage("/")
            out.append(Drive("/", "", u.total, u.free))
        except OSError:
            pass
        return out
    k = ctypes.windll.kernel32
    mask = k.GetLogicalDrives()
    for i in range(26):
        if not mask & (1 << i):
            continue
        root = f"{chr(65 + i)}:\\"
        kind = k.GetDriveTypeW(root)
        if kind not in (2, 3):  # removable, fixed
            continue
        try:
            u = shutil.disk_usage(root)
        except OSError:
            continue
        name = ctypes.create_unicode_buffer(261)
        if not k.GetVolumeInformationW(root, name, 261, None, None, None, None, 0):
            name.value = ""
        out.append(Drive(root, name.value, u.total, u.free, kind == 2))
    return out


# ----------------------------------------------------------------------------- what can't be removed

def _env_path(name: str, default: str = "") -> Path | None:
    v = os.environ.get(name, default)
    return Path(v) if v else None


def protected(path: Path) -> bool:
    """Windows itself, installed programs and the top of a drive: never removed from the map (programs
    are removed with the Uninstaller, so nothing is left half-deleted)."""
    p = os.path.normcase(os.path.abspath(str(path)))
    if os.path.dirname(p) == p:  # a whole drive
        return True
    keep = [_env_path("SystemRoot", r"C:\Windows"), _env_path("ProgramFiles"), _env_path("ProgramFiles(x86)"),
            _env_path("ProgramData"), _env_path("ProgramW6432")]
    home = Path.home()
    exact = [home, home.parent, _env_path("APPDATA"), _env_path("LOCALAPPDATA"),
             Path(os.environ.get("SystemDrive", "C:") + os.sep + "Users")]
    for k in keep:
        if k is not None:
            kp = os.path.normcase(os.path.abspath(str(k)))
            if p == kp or p.startswith(kp + os.sep):
                return True
    for e in exact:
        if e is not None and p == os.path.normcase(os.path.abspath(str(e))):
            return True
    name = os.path.basename(p)
    return name in ("pagefile.sys", "hiberfil.sys", "swapfile.sys", "$recycle.bin", "system volume information")


def recycle(paths: list[Path]) -> int:
    """Sends these to the Recycle Bin in one go. Returns how many are gone."""
    paths = [p for p in paths if not protected(p)]
    if not paths:
        return 0
    if os.name != "nt":
        gone = 0
        for p in paths:
            try:
                shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
                gone += 1
            except OSError:
                pass
        return gone
    from .uninstall import SHFILEOPSTRUCTW
    op = SHFILEOPSTRUCTW()
    op.wFunc = 3  # FO_DELETE
    op.pFrom = "\0".join(str(p) for p in paths) + "\0"
    op.fFlags = 0x04 | 0x10 | 0x40 | 0x400  # silent, no questions, to the Recycle Bin, no error windows
    ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return sum(1 for p in paths if not os.path.lexists(p))


# ----------------------------------------------------------------------------- junk

@dataclass
class Junk:
    key: str
    title: str                 # English, shown through tr()
    detail: str                # English, shown through tr()
    paths: list = field(default_factory=list)   # what would be removed
    size: int = 0
    count: int = 0
    busy: str = ""             # a program that has to be closed first ("Google Chrome")
    to_bin: bool = False       # goes to the Recycle Bin (personal files), not deleted for good
    ticked: bool = True        # ticked to begin with
    name: str = ""             # a name in the title ("Google Chrome")


def _tree_size(path: Path, limit_s: float = 20.0) -> tuple[int, int]:
    size = count = 0
    started = time.monotonic()
    stack = [str(path)]
    while stack:
        if time.monotonic() - started > limit_s:
            break
        here = stack.pop()
        try:
            with os.scandir(here) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if not _skip_dir(e):
                                stack.append(e.path)
                        else:
                            size += e.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        pass
        except OSError:
            pass
    return size, count


def _size_of(p: Path) -> tuple[int, int]:
    try:
        if p.is_dir() and not p.is_symlink():
            return _tree_size(p)
        return p.stat().st_size, 1
    except OSError:
        return 0, 0


def running_programs() -> set[str]:
    """The .exe names (lowercase) of the programs running now."""
    if os.name != "nt":
        return set()
    try:
        from .clipboard import process_path
        psapi = ctypes.windll.psapi
        pids = (ctypes.c_ulong * 4096)()
        needed = ctypes.c_ulong()
        if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed)):
            return set()
        names = set()
        for pid in pids[:needed.value // ctypes.sizeof(ctypes.c_ulong)]:
            path = process_path(pid) if pid else ""
            if path:
                names.add(os.path.basename(path).lower())
        return names
    except Exception:
        return set()


# browsers: (name, exe, cache folders under LOCALAPPDATA ("*" is any profile))
BROWSERS = [
    ("Google Chrome", "chrome.exe", r"Google\Chrome\User Data"),
    ("Microsoft Edge", "msedge.exe", r"Microsoft\Edge\User Data"),
    ("Brave", "brave.exe", r"BraveSoftware\Brave-Browser\User Data"),
    ("Vivaldi", "vivaldi.exe", r"Vivaldi\User Data"),
    ("Opera", "opera.exe", r"Opera Software\Opera Stable"),
]
CHROMIUM_CACHES = ("Cache", "Code Cache", "GPUCache")
INSTALLER_EXT = {".exe", ".msi", ".msix", ".msixbundle", ".appx", ".appxbundle"}
OLD_DAYS = 30


def find_junk(downloads: Path | None = None, cancel: threading.Event | None = None) -> list[Junk]:
    """What can go: temporary files, the Recycle Bin, browsers' caches, crash reports and old installers."""
    now = time.time()
    found: list[Junk] = []
    running = running_programs()
    local = _env_path("LOCALAPPDATA")

    temp = Junk("temp", "Temporary files", "Left behind by programs and installers. Only ones older than a day.")
    tmp_dir = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "")
    if tmp_dir and tmp_dir.is_dir():
        try:
            entries = list(os.scandir(tmp_dir))
        except OSError:
            entries = []
        for e in entries:
            try:
                if now - e.stat(follow_symlinks=False).st_mtime < 86400:
                    continue
            except OSError:
                continue
            p = Path(e.path)
            size, count = _size_of(p)
            temp.paths.append(p)
            temp.size += size
            temp.count += count
    found.append(temp)

    bin_size, bin_count = recycle_bin_size()
    found.append(Junk("bin", "Recycle Bin", "Files you deleted earlier. Emptying it can't be undone.",
                      size=bin_size, count=bin_count))

    if local is not None:
        for name, exe, base in BROWSERS:
            if cancel is not None and cancel.is_set():
                break
            root = local.joinpath(*base.split("\\"))
            if not root.is_dir():
                continue
            j = Junk("browser:" + exe, "{name}'s cache", "Copies of websites kept to load them faster. They come "
                     "back as you browse; you stay signed in.", name=name)
            try:
                profiles = [root] + [p for p in root.iterdir() if p.is_dir()]
            except OSError:
                profiles = [root]
            for profile in profiles:
                for cache in CHROMIUM_CACHES:
                    p = profile / cache
                    if p.is_dir():
                        size, count = _tree_size(p)
                        j.paths.append(p)
                        j.size += size
                        j.count += count
            if j.paths:
                j.busy = name if exe in running else ""
                found.append(j)
        ff = local / "Mozilla" / "Firefox" / "Profiles"
        if ff.is_dir():
            j = Junk("browser:firefox.exe", "{name}'s cache", "Copies of websites kept to load them faster. They "
                     "come back as you browse; you stay signed in.", name="Firefox")
            try:
                for profile in ff.iterdir():
                    p = profile / "cache2"
                    if p.is_dir():
                        size, count = _tree_size(p)
                        j.paths.append(p)
                        j.size += size
                        j.count += count
            except OSError:
                pass
            if j.paths:
                j.busy = "Firefox" if "firefox.exe" in running else ""
                found.append(j)

        dumps = Junk("dumps", "Crash reports", "Saved when a program crashed. Only useful to the people who make it.")
        for p in (local / "CrashDumps", local / "Microsoft" / "Windows" / "WER" / "ReportArchive",
                  local / "Microsoft" / "Windows" / "WER" / "ReportQueue"):
            if p.is_dir():
                try:
                    for child in p.iterdir():
                        size, count = _size_of(child)
                        dumps.paths.append(child)
                        dumps.size += size
                        dumps.count += count
                except OSError:
                    pass
        if dumps.paths:
            found.append(dumps)

    if downloads is not None and downloads.is_dir():
        old = Junk("installers", "Old installers in Downloads", "Setup files downloaded more than a month ago. "
                   "They go to the Recycle Bin.", to_bin=True, ticked=False)
        try:
            for e in os.scandir(downloads):
                if not e.is_file() or os.path.splitext(e.name)[1].lower() not in INSTALLER_EXT:
                    continue
                st = e.stat()
                if now - st.st_mtime > OLD_DAYS * 86400:
                    old.paths.append(Path(e.path))
                    old.size += st.st_size
                    old.count += 1
        except OSError:
            pass
        if old.paths:
            found.append(old)
    return found


def recycle_bin_size() -> tuple[int, int]:
    if os.name != "nt":
        return 0, 0
    try:
        class SHQUERYRBINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("i64Size", ctypes.c_int64), ("i64NumItems", ctypes.c_int64)]
        info = SHQUERYRBINFO(cbSize=ctypes.sizeof(SHQUERYRBINFO))
        if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) == 0:
            return int(info.i64Size), int(info.i64NumItems)
    except Exception:
        pass
    return 0, 0


def _remove(p: Path) -> bool:
    try:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
            return not p.exists()
        p.unlink()
        return True
    except OSError:
        return False


def clean(junk: Junk, cancel: threading.Event | None = None) -> int:
    """Removes it. Returns the bytes freed (files in use are left alone)."""
    if junk.busy:
        return 0
    if junk.key == "bin":
        if os.name == "nt" and junk.size:
            before = recycle_bin_size()[0]
            ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)  # no questions, no window, no sound
            return max(0, before - recycle_bin_size()[0])
        return 0
    if junk.to_bin:
        before = sum(_size_of(p)[0] for p in junk.paths)
        recycle(junk.paths)
        return max(0, before - sum(_size_of(p)[0] for p in junk.paths if os.path.lexists(p)))
    freed = 0
    for p in junk.paths:
        if cancel is not None and cancel.is_set():
            break
        if junk.key.startswith("browser:"):  # the cache folder stays (empty), only what's inside goes
            try:
                children = list(p.iterdir())
            except OSError:
                continue
            for c in children:
                size = _size_of(c)[0]
                if _remove(c):
                    freed += size
            continue
        size = _size_of(p)[0]
        if _remove(p):
            freed += size
        elif p.is_dir():  # some files inside were in use: the rest went
            freed += max(0, size - _size_of(p)[0])
    return freed


# ----------------------------------------------------------------------------- duplicates

def _online_only(st) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & (_OFFLINE | _RECALL_ACCESS | _RECALL_OPEN))


def _hash(path: str, first_only: bool) -> bytes | None:
    h = hashlib.blake2b(digest_size=20)
    try:
        with open(path, "rb") as f:
            if first_only:
                h.update(f.read(64 * 1024))
            else:
                while chunk := f.read(1024 * 1024):
                    h.update(chunk)
    except OSError:
        return None
    return h.digest()


def find_duplicates(folders: list[Path], min_size: int = 100 * 1024, cancel: threading.Event | None = None,
                    progress=None) -> list[list[tuple[str, int, float]]]:
    """Files that are exactly the same, as groups of (path, size, modified), the most space wasted first.
    Files only in the cloud aren't read (that would download them)."""
    by_size: dict[int, list[tuple[str, float]]] = {}
    seen_dirs: set[str] = set()
    stack = [str(f) for f in folders if f.is_dir()]
    looked = 0
    while stack:
        if cancel is not None and cancel.is_set():
            return []
        here = stack.pop()
        key = os.path.normcase(os.path.abspath(here))
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        try:
            with os.scandir(here) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if not _skip_dir(e):
                                stack.append(e.path)
                            continue
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if st.st_size < min_size or _online_only(st) or not e.is_file(follow_symlinks=False):
                        continue
                    by_size.setdefault(st.st_size, []).append((e.path, st.st_mtime))
                    looked += 1
                    if progress is not None and looked % PROGRESS_EVERY == 0:
                        progress("look", looked, 0)
        except OSError:
            continue
    candidates = [(size, files) for size, files in by_size.items() if len(files) > 1]
    by_size.clear()
    groups: list[list[tuple[str, int, float]]] = []
    done = 0
    for size, files in sorted(candidates, key=lambda c: c[0] * (len(c[1]) - 1), reverse=True):
        if cancel is not None and cancel.is_set():
            return []
        quick: dict[bytes, list] = {}
        for path, mtime in files:
            d = _hash(path, True)
            if d is not None:
                quick.setdefault(d, []).append((path, mtime))
        for same in quick.values():
            if len(same) < 2:
                continue
            full: dict[bytes, list] = {}
            for path, mtime in same:
                if cancel is not None and cancel.is_set():
                    return []
                d = _hash(path, False) if size > 64 * 1024 else _hash(path, True)
                if d is not None:
                    full.setdefault(d, []).append((path, size, mtime))
            groups += [g for g in full.values() if len(g) > 1]
        done += 1
        if progress is not None:
            progress("compare", done, len(candidates))
    groups.sort(key=lambda g: g[0][1] * (len(g) - 1), reverse=True)
    return groups
