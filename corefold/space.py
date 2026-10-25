"""Corefold Space: what's filling the disk, and freeing space safely.

Looking through a drive keeps only what the map needs (each folder's size and its biggest files), so
even a drive with millions of files fits in a few dozen MB, which are let go when the page closes.
Nothing here deletes anything by itself: the page asks first, files go to the Recycle Bin, and only
junk (temporary files, caches) is deleted for good."""
from __future__ import annotations

import ctypes
import hashlib
import heapq
import json
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
TOP_KIND = 60          # the biggest files of each kind (videos, pictures…)
TOP_OLD = 100          # the biggest files nobody has opened or changed for a year
OLD_AFTER_DAYS = 365
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
    biggest_kind: dict = field(default_factory=dict)  # kind -> (size, path) of its biggest files, biggest first
    old: list = field(default_factory=list)       # (size, path, last used) of big files untouched for a year
    grew: list = field(default_factory=list)      # (bytes more, path) since the last look, from remember()
    since: float = 0.0                            # when that last look was
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


_GONE = -1  # (in a listing: not a regular file, left out)


def _list_dir(path: str) -> tuple[list, bool]:
    """One folder's entries as (name, is folder, size, attributes, last used); the slow part (asking the
    disk), done on helper threads. Folders that lead elsewhere (junctions, links) are left out. "Last used"
    is the later of the last change and the last opening (Windows doesn't always note openings)."""
    out = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if not _skip_dir(e):
                            out.append((e.name, True, 0, 0, 0.0))
                        continue
                    st = e.stat(follow_symlinks=False)
                    if os.name != "nt" and not stat.S_ISREG(st.st_mode):
                        continue
                    out.append((e.name, False, st.st_size, getattr(st, "st_file_attributes", 0),
                                max(st.st_mtime, st.st_atime)))
                except OSError:
                    continue
    except OSError:
        return out, True
    return out, False


def scan(root: Path, cancel: threading.Event | None = None, progress=None, workers: int = 16) -> Scan:
    """Looks through a folder or a whole drive. progress(files, bytes, folder) is called now and then.
    Several folders are read at once (disks answer faster that way, a lot faster when they're slow)."""
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
    started = time.monotonic()
    top = Folder(str(root))
    result = Scan(top)
    order = [top]
    waiting = [top]          # folders still to read
    biggest: list[tuple[int, str]] = []
    kinds = dict.fromkeys(KINDS, 0)
    by_kind: dict[str, list] = {k: [] for k in KINDS}
    old: list = []
    old_before = time.time() - OLD_AFTER_DAYS * 86400
    total = 0
    last_report = 0
    hidden_bits = _OFFLINE | _RECALL_ACCESS | _RECALL_OPEN
    pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="corefold-scan")
    try:
        busy: dict = {}
        while waiting or busy:
            if cancel is not None and cancel.is_set():
                result.cancelled = True
                for fut in busy:
                    fut.cancel()
                break
            while waiting and len(busy) < workers * 4:
                folder = waiting.pop()
                path = str(folder.path)
                busy[pool.submit(_list_dir, path)] = (folder, path)
            done, _ = wait(list(busy), timeout=0.5, return_when=FIRST_COMPLETED)
            for fut in done:
                folder, path = busy.pop(fut)
                try:
                    entries, denied = fut.result()
                except Exception:
                    entries, denied = [], True
                if denied:
                    folder.denied = True
                    result.denied += 1
                heap: list[tuple[int, str]] = []
                for name, is_dir, size, attrs, used in entries:
                    if is_dir:
                        child = Folder(name, folder)
                        folder.children.append(child)
                        order.append(child)
                        waiting.append(child)
                        continue
                    if attrs & hidden_bits:
                        result.cloud += 1  # only in the cloud: it takes no space on this PC
                        continue
                    folder.files += 1
                    folder.size += size
                    total += size
                    result.files += 1
                    kind = kind_of(name)
                    kinds[kind] += size
                    if 0 < used < old_before and size >= 1 << 20:
                        item = (size, os.path.join(path, name), used)
                        if len(old) < TOP_OLD:
                            heapq.heappush(old, item)
                        elif size > old[0][0]:
                            heapq.heapreplace(old, item)
                    of_kind = by_kind[kind]
                    if len(of_kind) < TOP_KIND:
                        heapq.heappush(of_kind, (size, os.path.join(path, name)))
                    elif size > of_kind[0][0]:
                        heapq.heapreplace(of_kind, (size, os.path.join(path, name)))
                    if len(heap) < TOP_FILES:
                        heapq.heappush(heap, (size, name))
                    elif size > heap[0][0]:
                        small = heapq.heapreplace(heap, (size, name))
                        folder.rest += small[0]
                        folder.rest_count += 1
                    else:
                        folder.rest += size
                        folder.rest_count += 1
                    if len(biggest) < TOP_OVERALL:
                        heapq.heappush(biggest, (size, os.path.join(path, name)))
                    elif size > biggest[0][0]:
                        heapq.heapreplace(biggest, (size, os.path.join(path, name)))
                heap.sort(reverse=True)
                folder.big = heap
                if progress is not None and result.files - last_report >= PROGRESS_EVERY:
                    last_report = result.files
                    progress(result.files, total, path)
    finally:
        # (on Stop, folders still being read are left to finish on their own: a huge one can take a while)
        pool.shutdown(wait=not result.cancelled, cancel_futures=True)
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
    result.biggest_kind = {k: sorted(v, reverse=True) for k, v in by_kind.items()}
    result.old = sorted(old, reverse=True)
    result.seconds = time.monotonic() - started
    return result


# ----------------------------------------------------------------------------- what grew since last time

GREW_MIN = 50 << 20      # folders that grew less than this aren't mentioned
KEEP_FOLDERS = 30 << 20  # folders remembered for next time: the ones at least this big…
KEEP_DEPTH = 5           # …this deep at most
KEEP_ROOTS = 12


def _history_file() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or str(Path.home())) / "Corefold" / "space-history.json"


def folder_sizes(result: Scan) -> dict[str, int]:
    """The sizes of the bigger folders, to compare with next time."""
    out: dict[str, int] = {}
    todo = [(result.root, 0)]
    while todo:
        f, depth = todo.pop()
        out[str(f.path)] = f.size
        if depth < KEEP_DEPTH:
            todo += [(c, depth + 1) for c in f.children if c.size >= KEEP_FOLDERS]
    return out


def growth(before: dict[str, int], now: dict[str, int]) -> list[tuple[int, str]]:
    """The folders that grew the most: the ones it really happened in (a folder isn't listed when one
    folder inside it holds most of what it grew)."""
    grew = {p: size - before.get(p, 0) for p, size in now.items()
            if size - before.get(p, 0) >= GREW_MIN and (p in before or os.path.dirname(p) in before)}
    keep = []
    for p, delta in grew.items():
        inside = [d for q, d in grew.items() if q != p and os.path.dirname(q) == p]
        if inside and max(inside) >= delta * 0.7:
            continue
        keep.append((delta, p))
    keep.sort(reverse=True)
    return keep[:30]


def remember(result: Scan, file: Path | None = None) -> None:
    """Notes what grew since the last look at the same drive or folder (result.grew), and keeps this
    look's folder sizes for next time. A look stopped before the end isn't kept."""
    file = file or _history_file()
    key = os.path.normcase(str(result.root.path))
    try:
        data = json.loads(file.read_text("utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    now = folder_sizes(result)
    last = data.get(key)
    if isinstance(last, dict) and isinstance(last.get("sizes"), dict):
        result.grew = growth(last["sizes"], now)
        result.since = float(last.get("time") or 0)
    if result.cancelled:
        return
    data[key] = {"time": time.time(), "sizes": now}
    if len(data) > KEEP_ROOTS:  # the drives and folders looked at longest ago are forgotten
        for old in sorted(data, key=lambda k: data[k].get("time", 0))[:len(data) - KEEP_ROOTS]:
            data.pop(old, None)
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), "utf-8")
        os.replace(tmp, file)
    except OSError:
        log.warning("couldn't keep the folder sizes for next time", exc_info=True)


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
            return max(max(short * short * a / (s * s), (s * s) / (short * short * a)) if a > 0 else float("inf")  # noqa: B023
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

def _env_path(name: str, default: str = "", *more: str) -> Path | None:
    v = os.environ.get(name, default)
    return Path(v, *more) if v else None


def protected(path: Path) -> bool:
    """Windows itself, installed programs and the top of a drive: never removed from the map (programs
    are removed with the Uninstaller, so nothing is left half-deleted)."""
    p = os.path.normcase(os.path.abspath(str(path)))
    if os.path.dirname(p) == p:  # a whole drive
        return True
    keep = [_env_path("SystemRoot", r"C:\Windows"), _env_path("ProgramFiles"), _env_path("ProgramFiles(x86)"),
            _env_path("ProgramData"), _env_path("ProgramW6432")]
    home = Path.home()
    users = Path(os.environ.get("SystemDrive", "C:") + os.sep + "Users")
    # (AppData itself, LocalLow and the per-user Programs folder hold every app's settings and programs)
    exact = [home, home.parent, _env_path("APPDATA"), _env_path("LOCALAPPDATA"), users, home / "AppData",
             home / "AppData" / "LocalLow", _env_path("LOCALAPPDATA", "", "Programs"), _env_path("PUBLIC"),
             users / "Public", users / "Default"]
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
    from .uninstall import RECYCLE_FLAGS, SHFILEOPSTRUCTW
    op = SHFILEOPSTRUCTW()
    op.wFunc = 3  # FO_DELETE
    op.pFrom = "\0".join(str(p) for p in paths) + "\0"
    # silent, no questions, to the Recycle Bin, no error windows, and ask before anything would be deleted for
    # good instead: a file too big for the Recycle Bin, or on a drive that has none (a USB stick, a network
    # folder), otherwise goes for good without a word
    op.fFlags = RECYCLE_FLAGS
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
    admin: bool = False        # Windows' own files: removed by an administrator copy of Corefold (Windows asks)


def _tree_size(path: Path, limit_s: float = 20.0) -> tuple[int, int]:
    return _tree_info(path, limit_s)[:2]


def _tree_info(path: Path, limit_s: float = 20.0) -> tuple[int, int, float, bool]:
    """(bytes, files, when anything inside last changed, whether all of it was looked at in time)."""
    size = count = 0
    newest = 0.0
    started = time.monotonic()
    stack = [str(path)]
    while stack:
        if time.monotonic() - started > limit_s:
            return size, count, newest, False
        here = stack.pop()
        try:
            with os.scandir(here) as it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)
                        newest = max(newest, st.st_mtime)
                        if e.is_dir(follow_symlinks=False):
                            if not _skip_dir(e):
                                stack.append(e.path)
                        else:
                            size += st.st_size
                            count += 1
                    except OSError:
                        pass
        except OSError:
            pass
    return size, count, newest, True


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
# apps built like browsers (Electron) keep the same caches: (name, exe, folder under APPDATA or LOCALAPPDATA)
APP_CACHES = [
    ("Discord", "discord.exe", "APPDATA", r"discord"),
    ("Microsoft Teams", "teams.exe", "APPDATA", r"Microsoft\Teams"),
    ("Microsoft Teams", "ms-teams.exe", "LOCALAPPDATA",
     r"Packages\MSTeams_8wekyb3d8bbwe\LocalCache\Microsoft\MSTeams\EBWebView\Default"),
    ("Slack", "slack.exe", "APPDATA", r"Slack"),
    ("Visual Studio Code", "code.exe", "APPDATA", r"Code"),
    ("WhatsApp", "whatsapp.exe", "LOCALAPPDATA",
     r"Packages\5319275A.WhatsAppDesktop_cv1g1gvanyjgm\LocalState\EBWebView\Default"),
    ("Spotify", "spotify.exe", "LOCALAPPDATA", r"Spotify\Browser"),
    ("Steam", "steamwebhelper.exe", "LOCALAPPDATA", r"Steam\htmlcache"),
]
# what the graphics card keeps for games (made again the next time a game starts, a little slower once)
GPU_CACHES = (r"D3DSCache", r"NVIDIA\DXCache", r"NVIDIA\GLCache", r"AMD\DxCache", r"AMD\GLCache",
              r"Intel\ShaderCache")
INSTALLER_EXT = {".exe", ".msi", ".msix", ".msixbundle", ".appx", ".appxbundle"}
OLD_DAYS = 30


def temp_folder() -> Path | None:
    """The temporary folder, only when it really is one. TEMP set to a drive, the profile or some other
    folder of the person's (or not set at all) would offer everything in it as junk, deleted for good."""
    value = os.environ.get("TEMP") or os.environ.get("TMP") or ""
    if not value:
        return None
    p = Path(os.path.abspath(value))
    def temp_named(name: str) -> bool:
        return "temp" in name.lower() or "tmp" in name.lower()
    named = temp_named(p.name) or (p.name.isdigit() and temp_named(p.parent.name))  # ("Temp\\2": a session's)
    home = os.path.normcase(os.path.abspath(str(Path.home())))
    here = os.path.normcase(str(p))
    if not named or protected(p) or home == here or home.startswith(here.rstrip("\\/") + os.sep) or not p.is_dir():
        return None
    return p


def find_junk(downloads: Path | None = None, cancel: threading.Event | None = None) -> list[Junk]:
    """What can go: temporary files, the Recycle Bin, browsers' caches, crash reports and old installers."""
    now = time.time()
    found: list[Junk] = []
    running = running_programs()
    local = _env_path("LOCALAPPDATA")

    temp = Junk("temp", "Temporary files", "Left behind by programs and installers. Only ones older than a day.")
    tmp_dir = temp_folder()
    if tmp_dir is not None:
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
            try:
                is_dir = e.is_dir(follow_symlinks=False)
                if protected(p) or e.is_symlink() or (is_dir and _skip_dir(e)):
                    continue  # (a link or junction: what it leads to isn't the temp folder's)
            except OSError:
                continue
            if is_dir:
                # a folder counts by what's newest inside it (a program still running keeps its working
                # folder for days, and changes the files in it, not the folder); all of it, or it stays
                size, count, newest, whole = _tree_info(p, 10.0)
                if not whole or now - newest < 86400:
                    continue
            else:
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

        found += _app_caches(running, cancel)

        thumbs = Junk("thumbs", "Picture previews", "The small previews File Explorer shows for pictures and "
                      "videos. Windows makes them again when you open a folder.", ticked=False)
        explorer = local / "Microsoft" / "Windows" / "Explorer"
        try:
            for e in os.scandir(explorer):
                if e.is_file() and e.name.lower().startswith(("thumbcache_", "iconcache_")) and \
                        e.name.lower().endswith(".db"):
                    thumbs.paths.append(Path(e.path))
                    thumbs.size += e.stat().st_size
                    thumbs.count += 1
        except OSError:
            pass
        if thumbs.paths:
            found.append(thumbs)

        gpu = Junk("gpu", "Graphics card caches", "Kept for games and apps that use the graphics card. They're made "
                   "again the next time, so a game may start a little slower once.", ticked=False)
        for rel in GPU_CACHES:
            p = local.joinpath(*rel.split("\\"))
            if p.is_dir():
                size, count = _tree_size(p)
                if size:
                    gpu.paths.append(p)
                    gpu.size += size
                    gpu.count += count
        if gpu.paths:
            found.append(gpu)

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

    found += _admin_junk(cancel)

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


# Windows' own leftovers, which only an administrator can remove. The administrator copy of Corefold
# is only told which of these keys to clean; the folders come from here, never from the request.
ADMIN_KEYS = ("winupdate", "winold")


def _windows_dir() -> Path:
    """Where Windows is, from Windows itself (not from the environment, which a program could change)."""
    if os.name == "nt":
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.kernel32.GetSystemWindowsDirectoryW(buf, 260):
            return Path(buf.value)
    return Path(os.environ.get("SystemRoot") or r"C:\Windows")


def admin_path(key: str) -> Path | None:
    windows = _windows_dir()
    if key == "winupdate":
        return windows / "SoftwareDistribution" / "Download"
    if key == "winold":
        return Path(windows.drive + "\\") / "Windows.old"
    return None


def _admin_junk(cancel: threading.Event | None = None) -> list[Junk]:
    if os.name != "nt":
        return []
    rows = []
    updates = admin_path("winupdate")
    if updates is not None and updates.is_dir():
        size, count = _tree_size(updates, 10.0)
        if size > 20 << 20:
            rows.append(Junk("winupdate", "Windows Update downloads", "Updates Windows has already installed. "
                             "Windows downloads again any it still needs. Windows asks for an administrator.",
                             [updates], size, count, ticked=False, admin=True))
    old = admin_path("winold")
    if old is not None and old.is_dir() and not (cancel is not None and cancel.is_set()):
        size, count = _tree_size(old, 20.0)
        rows.append(Junk("winold", "The previous Windows", "Kept after a big Windows update, to go back to it. Once "
                         "it's removed, going back isn't possible. Windows asks for an administrator.",
                         [old], size, count, ticked=False, admin=True))
    return rows


def clean_as_admin(keys: list[str]) -> int:
    """In the administrator copy of Corefold: removes Windows' own leftovers. Returns the bytes freed."""
    import subprocess
    flags = 0x08000000  # CREATE_NO_WINDOW
    system32 = _windows_dir() / "System32"
    freed = 0

    def run(name, *args, timeout=600):
        try:  # (Windows' own programs, by their full path)
            subprocess.run([str(system32 / (name + ".exe")), *args], capture_output=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, creationflags=flags)
        except (OSError, subprocess.SubprocessError):
            log.warning("%s failed", name, exc_info=True)
    for key in keys:
        path = admin_path(key) if key in ADMIN_KEYS else None
        if path is None or not path.is_dir():
            continue
        before = _tree_size(path, 60.0)[0]
        if key == "winupdate":
            # Windows Update has to stop while its downloads go; it starts again straight after
            run("net", "stop", "wuauserv", timeout=120)
            run("net", "stop", "bits", timeout=120)
            try:
                for child in path.iterdir():
                    _remove(child)
            finally:
                run("net", "start", "bits", timeout=120)
                run("net", "start", "wuauserv", timeout=120)
        elif key == "winold":
            # Windows' own Disk Cleanup removes it the way Windows means it to ("Previous Windows
            # installation(s)"); what it leaves (or everything, without Disk Cleanup) goes the hard way
            _previous_windows_by_windows(system32, run)
            if path.is_dir() and _tree_size(path, 60.0)[0] > 50 << 20:
                # take it over first, without following the links inside it to the folders of the Windows in use
                # (old profiles link to today's), then remove it (rd doesn't follow them either)
                own = ["takeown", "/F", str(path), "/R", "/A", "/D", "Y"]
                if _takeown_skips_links(system32):
                    own.append("/SKIPSL")
                run(*own, timeout=1800)
                run("icacls", str(path), "/grant", "*S-1-5-32-544:F", "/T", "/C", "/L", "/Q", timeout=1800)
                run("cmd", "/c", "rd", "/s", "/q", str(path), timeout=1800)
        after = _tree_size(path, 60.0)[0] if path.exists() else 0
        freed += max(0, before - after)
    return freed


VOLUME_CACHES = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\VolumeCaches"
SAGE_RUN = 6213  # Disk Cleanup's numbered set of what to clean, only ours


def _previous_windows_by_windows(system32: Path, run) -> bool:
    """Disk Cleanup's "Previous Windows installation(s)", and nothing else, run once."""
    cleanmgr = system32 / "cleanmgr.exe"
    if os.name != "nt" or not cleanmgr.is_file():
        return False
    import winreg
    name = f"StateFlags{SAGE_RUN:04d}"
    key = VOLUME_CACHES + "\\Previous Installations"
    access = winreg.KEY_SET_VALUE | getattr(winreg, "KEY_WOW64_64KEY", 0)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, access) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, 2)
    except OSError:
        return False
    try:
        run("cleanmgr", f"/sagerun:{SAGE_RUN}", timeout=3600)
    finally:
        try:  # (Disk Cleanup's settings are left as they were)
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, access) as k:
                winreg.DeleteValue(k, name)
        except OSError:
            pass
    return True


def _takeown_skips_links(system32: Path) -> bool:
    import subprocess
    try:
        r = subprocess.run([str(system32 / "takeown.exe"), "/?"], capture_output=True, timeout=30,
                           stdin=subprocess.DEVNULL, creationflags=0x08000000)
        return b"/SKIPSL" in r.stdout.upper()
    except (OSError, subprocess.SubprocessError):
        return False


def _app_caches(running: set[str], cancel: threading.Event | None = None) -> list[Junk]:
    """The caches of Discord, Teams, Slack, WhatsApp and the like (one row per app). Only their caches:
    messages, settings and downloaded files stay."""
    rows: dict[str, Junk] = {}
    for name, exe, env, rel in APP_CACHES:
        if cancel is not None and cancel.is_set():
            break
        base = _env_path(env)
        if base is None:
            continue
        root = base.joinpath(*rel.split("\\"))
        if not root.is_dir():
            continue
        j = rows.get(name) or Junk("app:" + exe, "{name}'s cache", "Copies of pictures and pages the app keeps to "
                                   "load faster. Your messages, settings and downloads stay.", name=name)
        for cache in CHROMIUM_CACHES + ("Service Worker\\CacheStorage",):
            p = root.joinpath(*cache.split("\\"))
            if p.is_dir():
                size, count = _tree_size(p)
                j.paths.append(p)
                j.size += size
                j.count += count
        if j.paths:
            if exe in running:
                j.busy = name
            rows[name] = j
    return [j for j in rows.values() if j.size]


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
    if junk.busy or junk.admin:  # (Windows' own leftovers: clean_as_admin, in the administrator copy)
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
        if junk.key.startswith(("browser:", "app:", "gpu")):  # the folder stays (empty), only what's inside goes
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


WHOLE_UP_TO = 8 * 2**20  # (bytes)


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


def _file_id(path: str) -> tuple[int, int] | None:
    """Which file this is on its drive (the same for every hard link to it), or None if Windows won't say."""
    try:
        st = os.stat(path)  # (os.scandir leaves these out on Windows)
    except OSError:
        return None
    return (st.st_dev, st.st_ino) if st.st_ino else None


def _distinct(files: list, pool=None) -> list:
    """(path, modified) pairs, with only one name kept for a file that has several (hard links)."""
    ids = list(pool.map(lambda f: _file_id(f[0]), files)) if pool is not None else [_file_id(f[0]) for f in files]
    seen, out = set(), []
    for f, fid in zip(files, ids):
        if fid is not None and fid in seen:
            continue
        seen.add(fid)
        out.append(f)
    return out


def still_same(path: str, size: int, mtime: float) -> bool:
    """The file is there and unchanged since the duplicates were found (same size, same time)."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return st.st_size == size and abs(st.st_mtime - mtime) < 2


def copies_to_remove(groups: list[tuple[list, list]]) -> list[str]:
    """What may really go: for each file, (the ticked copies, the copies kept), each as (path, size,
    modified). A copy goes only when one kept copy is still there, unchanged and a file of its own (not
    another name of the one going), and the copy itself hasn't changed since; otherwise none of that file
    goes (it was moved, deleted or edited meanwhile)."""
    out = []
    for ticked, kept in groups:
        if not ticked or not all(still_same(*t) for t in ticked):
            continue
        going = {_file_id(p) for p, _s, _m in ticked} - {None}
        if any(still_same(*k) and (_file_id(k[0]) is None or _file_id(k[0]) not in going) for k in kept):
            out += [p for p, _s, _m in ticked]
    return out


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
    # Files up to WHOLE_UP_TO are read once, whole; bigger ones first by their start, then whole only when the
    # starts are the same. Several are read at once: opening a file is the slow part (the virus scanner looks
    # at each one), not reading it.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="corefold-dupes") as pool:
        for size, files in sorted(candidates, key=lambda c: c[0] * (len(c[1]) - 1), reverse=True):
            if cancel is not None and cancel.is_set():
                return []
            # (two names of one file, a hard link, aren't two copies: removing one frees nothing)
            files = _distinct(files, pool)
            if len(files) < 2:
                done += 1
                continue
            whole = size <= WHOLE_UP_TO
            first: dict[bytes, list] = {}
            for (path, mtime), d in zip(files, pool.map(lambda f: _hash(f[0], not whole), files)):  # noqa: B023
                if d is not None:
                    first.setdefault(d, []).append((path, mtime))
            for same in first.values():
                if len(same) < 2:
                    continue
                if whole:
                    groups.append([(path, size, mtime) for path, mtime in same])
                    continue
                if cancel is not None and cancel.is_set():
                    return []
                full: dict[bytes, list] = {}
                for (path, mtime), d in zip(same, pool.map(lambda f: _hash(f[0], False), same)):
                    if d is not None:
                        full.setdefault(d, []).append((path, size, mtime))
                groups += [g for g in full.values() if len(g) > 1]
            done += 1
            if progress is not None:
                progress("compare", done, len(candidates))
    groups.sort(key=lambda g: g[0][1] * (len(g) - 1), reverse=True)
    return groups
