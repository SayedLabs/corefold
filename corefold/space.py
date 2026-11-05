"""Rakiza Core Space: what's filling the disk, and freeing space safely.

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
from array import array
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("corefold")

TOP_FILES = 48         # files per folder with a box of their own on the map (the rest are "smaller files")
TOP_OVERALL = 100      # the biggest files of the whole drive
TOP_KIND = 60          # the biggest files of each kind (videos, pictures…)
TOP_OLD = 100          # the biggest files nobody has opened or changed for a year
TOP_TYPE = 10          # the biggest files of each file type (.mp4, .iso…)
KEEP_TYPES = 300       # file types kept after a look, biggest first (the rest are tiny)
OLD_AFTER_DAYS = 365
PROGRESS_EVERY = 2000  # entries between progress reports

# reparse points that lead somewhere else (junctions, links): not followed, so nothing counts twice
_JUNCTION, _SYMLINK = 0xA0000003, 0xA000000C
_REPARSE, _OFFLINE, _RECALL_OPEN, _RECALL_ACCESS = 0x400, 0x1000, 0x40000, 0x400000
# (not a Windows attribute: Rakiza Core's own mark on a file whose bytes are counted under another of its names)
_LINKED = 1 << 40

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


def type_of(name: str) -> str:
    """A file's type, as its extension in lower case (".mp4"); "" when it has none, or when what follows
    the last dot isn't one ("backup.2024-05-01", "Report v2.final copy")."""
    ext = os.path.splitext(name)[1].lower()
    return ext if 1 < len(ext) <= 12 and ext[1:].isalnum() else ""


_NO_SIZES = array("q")  # (shared by every folder without files of its own: never changed in place)
_NO_TIMES = array("f")  # (a float32 is to within a few minutes for a date: half the room of a float64)


class Folder:
    __slots__ = ("name", "parent", "size", "files", "folders", "children", "names", "sizes", "denied", "times",
                 "newest")

    def __init__(self, name: str, parent: "Folder | None" = None):
        self.name = name
        self.parent = parent
        self.size = 0          # everything inside, sub-folders too
        self.files = 0         # files inside, sub-folders too
        self.folders = 0
        self.children: list[Folder] = []
        # every file of its own, biggest first: the names (one string, a new line between them, so a
        # drive's million names take little room and a search runs through them quickly) and the sizes
        self.names = ""
        self.sizes = _NO_SIZES
        self.times = _NO_TIMES  # …and when each was last changed (in the same order; 0 when not known)
        self.denied = False    # Windows didn't let Rakiza Core look inside
        self.newest = 0.0      # when anything inside was last changed (sub-folders too)

    @property
    def path(self) -> Path:
        parts, f = [], self
        while f is not None:
            parts.append(f.name)
            f = f.parent
        return Path(*reversed(parts))

    def set_files(self, files: list[tuple]) -> None:
        """Its own files, as (size, name) or (size, name, when last changed)."""
        files.sort(key=lambda f: (f[0], f[1]), reverse=True)
        if not files:
            self.names, self.sizes, self.times = "", _NO_SIZES, _NO_TIMES
            return
        self.names = "\n".join(f[1].replace("\n", " ") for f in files)  # (a new line in a name: not on Windows)
        self.sizes = array("q", (f[0] for f in files))
        if len(files[0]) > 2:
            self.times = array("f", (f[2] for f in files))
            self.newest = max(self.newest, max(self.times))
        else:
            self.times = _NO_TIMES

    def own_times(self) -> dict[str, float]:
        """When its own biggest files were last changed: name -> time (the ones the list beside the map shows)."""
        if not self.times:
            return {}
        n = min(len(self.times), 300)
        return dict(zip(self.names.split("\n", n)[:n], self.times[:n]))

    def own_files_timed(self) -> list[tuple[int, str, float]]:
        """Its own files, (size, name, when last changed), biggest first."""
        if not self.sizes:
            return []
        times = self.times if len(self.times) == len(self.sizes) else [0.0] * len(self.sizes)
        return list(zip(self.sizes, self.names.split("\n"), times))

    def own_files(self) -> list[tuple[int, str]]:
        """Its own files, (size, name), biggest first."""
        if not self.sizes:
            return []
        return list(zip(self.sizes, self.names.split("\n")))

    def own_size(self) -> int:
        return sum(self.sizes)

    @property
    def big(self) -> list[tuple[int, str]]:
        """Its own biggest files (size, name), biggest first: the ones the map gives a box of their own."""
        if not self.sizes:
            return []
        return list(zip(self.sizes[:TOP_FILES], self.names.split("\n", TOP_FILES)[:TOP_FILES]))

    @property
    def rest(self) -> int:
        """Its own files that aren't in big, added up."""
        return sum(self.sizes[TOP_FILES:]) if len(self.sizes) > TOP_FILES else 0

    @property
    def rest_count(self) -> int:
        return max(0, len(self.sizes) - TOP_FILES)

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
    types: dict = field(default_factory=dict)     # file type (".mp4", "" for none) -> (bytes, files), biggest first
    biggest_type: dict = field(default_factory=dict)  # file type -> (size, path) of its biggest files, biggest first
    grew: list = field(default_factory=list)      # (bytes more, path) since the last look, from remember()
    since: float = 0.0                            # when that last look was
    files: int = 0
    folders: int = 0
    denied: int = 0                               # folders Windows didn't let Rakiza Core into
    cloud: int = 0                                # files that are only in the cloud (not counted)
    seconds: float = 0.0
    cancelled: bool = False
    fast: bool = False          # (read from the drive's own table: mftscan)
    linked: int = 0             # bytes of Windows' files that are also somewhere else (hard links): counted once
    watch: "Watch | None" = None                  # Windows telling what changes in it (while the map is kept)


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


def _link_dirs() -> tuple[str, ...]:
    """Folders whose files are mostly hard links to files elsewhere in Windows (the component store: each of
    its files is also in System32 or another of Windows' folders, under a second name). Explorer counts them
    twice, which makes Windows look far bigger than it is; Space counts them once (where else they are)."""
    if os.name != "nt":
        return ()
    win = os.environ.get("SystemRoot") or os.environ.get("windir")
    return (os.path.normcase(os.path.join(win, "WinSxS")),) if win else ()


_LINK_DIRS = _link_dirs()


def _in_link_dir(path: str) -> bool:
    if not _LINK_DIRS:
        return False
    p = os.path.normcase(path)
    return any(p == d or p.startswith(d + os.sep) for d in _LINK_DIRS)


def _list_dir(path: str) -> tuple[list, bool]:
    """One folder's entries as (name, is folder, size, attributes, last used, last changed); the slow part
    (asking the disk), done on helper threads. Folders that lead elsewhere (junctions, links) are left out.
    "Last used" is the later of the last change and the last opening (Windows doesn't always note openings).
    In Windows' component store, a file with more than one name gets the _LINKED mark (counted elsewhere)."""
    out = []
    links = _in_link_dir(path)
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if not _skip_dir(e):
                            out.append((e.name, True, 0, 0, 0.0, 0.0))
                        continue
                    st = e.stat(follow_symlinks=False)
                    if os.name != "nt" and not stat.S_ISREG(st.st_mode):
                        continue
                    attrs = getattr(st, "st_file_attributes", 0)
                    if links:
                        try:  # (the number of names needs the file itself asked: only done here)
                            if os.stat(e.path, follow_symlinks=False).st_nlink > 1:
                                attrs |= _LINKED
                        except OSError:
                            pass
                    out.append((e.name, False, st.st_size, attrs, max(st.st_mtime, st.st_atime), st.st_mtime))
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
    types: dict[str, list] = {}  # file type -> [bytes, files, its biggest files as a heap]
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
                own: list[tuple[int, str, float]] = []
                for name, is_dir, size, attrs, used, changed in entries:
                    if is_dir:
                        child = Folder(name, folder)
                        folder.children.append(child)
                        order.append(child)
                        waiting.append(child)
                        continue
                    if attrs & hidden_bits:
                        result.cloud += 1  # only in the cloud: it takes no space on this PC
                        continue
                    if attrs & _LINKED:
                        result.linked += size  # (its bytes are counted under its other name)
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
                    ext = type_of(name)
                    of_type = types.get(ext)
                    if of_type is None:
                        of_type = types[ext] = [0, 0, []]
                    of_type[0] += size
                    of_type[1] += 1
                    if len(of_type[2]) < TOP_TYPE:
                        heapq.heappush(of_type[2], (size, os.path.join(path, name)))
                    elif size > of_type[2][0][0]:
                        heapq.heapreplace(of_type[2], (size, os.path.join(path, name)))
                    own.append((size, name, changed))
                    if len(biggest) < TOP_OVERALL:
                        heapq.heappush(biggest, (size, os.path.join(path, name)))
                    elif size > biggest[0][0]:
                        heapq.heapreplace(biggest, (size, os.path.join(path, name)))
                folder.set_files(own)
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
            if f.newest > p.newest:
                p.newest = f.newest
    result.folders = top.folders
    result.biggest = sorted(biggest, reverse=True)
    result.kinds = kinds
    result.biggest_kind = {k: sorted(v, reverse=True) for k, v in by_kind.items()}
    result.old = sorted(old, reverse=True)
    kept = sorted(types.items(), key=lambda kv: kv[1][0], reverse=True)[:KEEP_TYPES]
    result.types = {ext: (v[0], v[1]) for ext, v in kept}
    result.biggest_type = {ext: sorted(v[2], reverse=True) for ext, v in kept}
    result.seconds = time.monotonic() - started
    return result


# ----------------------------------------------------------------------------- the map as a spreadsheet

def save_csv(result: Scan, out: Path, header: list[str], folder_word: str, file_word: str, size_text,
             min_size: int = 1 << 20) -> int:
    """Writes the map to a file a spreadsheet opens: every folder of at least min_size and the files it
    remembers of that size, in the map's order (each folder followed by what's in it, biggest first).
    header names the columns: path, what it is, bytes, size (size_text(bytes)), files, folders, share of
    the folder above. The number of rows written."""
    import csv
    rows = 0
    with open(out, "w", newline="", encoding="utf-8-sig") as f:  # (the mark lets Excel read Arabic)
        w = csv.writer(f)
        w.writerow(header)
        todo = [(result.root, 0)]
        while todo:
            folder, above = todo.pop()
            share = f"{folder.size * 100 / above:.1f}" if above else ""
            w.writerow([str(folder.path), folder_word, folder.size, size_text(folder.size), folder.files,
                        folder.folders, share])
            rows += 1
            base = folder.path
            for size, name in folder.big:
                if size < min_size:
                    break
                w.writerow([str(base / name), file_word, size, size_text(size), "", "",
                            f"{size * 100 / folder.size:.1f}" if folder.size else ""])
                rows += 1
            todo.extend((c, folder.size) for c in reversed(folder.children) if c.size >= min_size)
    return rows


# ----------------------------------------------------------------------------- finding files by name

FIND_LIMIT = 500  # results kept (the biggest first): more would only be scrolled past


def _words(query: str):
    """What a search looks for: every word in the name ("trip 2024"), or a pattern with * and ?
    ("*.iso", "IMG_??12*"). (matcher of one lower-case name, the plain words a folder's names must hold)"""
    import fnmatch
    import re
    q = " ".join(query.split()).lower()
    if not q:
        return None, []
    if "*" in q or "?" in q:
        rx = re.compile(fnmatch.translate(q), re.DOTALL)
        plain = [p for p in re.split(r"[*?]+", q) if p]
        return rx.match, plain
    words = q.split(" ")
    return (lambda name: all(w in name for w in words)), words


def find(result: Scan, query: str, limit: int = FIND_LIMIT, cancel: threading.Event | None = None
         ) -> tuple[list[tuple[int, str, "Folder | None"]], int]:
    """The files (and folders) whose name matches, biggest first: (size, path, the Folder for a folder),
    and how many match in all. Runs through every name the look found, not only the biggest ones."""
    matches, words = _words(query)
    if matches is None:
        return [], 0
    found: list = []  # a heap of (size, n, folder, index of the file or -1 for the folder itself)
    total = 0
    n = 0
    todo = [result.root]
    seen = 0
    while todo:
        f = todo.pop()
        todo.extend(f.children)
        seen += 1
        if cancel is not None and seen % 2000 == 0 and cancel.is_set():
            break
        if f is not result.root and matches(f.name.lower()):
            total += 1
            n += 1
            item = (f.size, n, f, -1)
            if len(found) < limit:
                heapq.heappush(found, item)
            elif f.size > found[0][0]:
                heapq.heapreplace(found, item)
        if not f.sizes:
            continue
        low = f.names.lower()
        if not all(w in low for w in words):
            continue
        for i, name in enumerate(low.split("\n")):
            if matches(name):
                total += 1
                n += 1
                size = f.sizes[i]
                if len(found) < limit:
                    heapq.heappush(found, (size, n, f, i))
                elif size > found[0][0]:
                    heapq.heapreplace(found, (size, n, f, i))
    out = []
    names: dict[int, list[str]] = {}
    for size, _n, f, i in sorted(found, key=lambda e: (-e[0], e[1])):
        if i < 0:
            out.append((size, str(f.path), f))
            continue
        split = names.get(id(f))
        if split is None:
            split = names[id(f)] = f.names.split("\n")
        out.append((size, os.path.join(str(f.path), split[i]), None))
    return out, total


# ----------------------------------------------------------------------------- following the disk as it changes

# what Windows tells about: files and folders added, removed or renamed, and files whose size changed
_NOTIFY = 0x1 | 0x2 | 0x8   # FILE_NOTIFY_CHANGE_FILE_NAME | DIR_NAME | SIZE
WATCH_BUFFER = 256 * 1024    # (what Windows keeps between two reads; beyond it, it says it lost count)
WATCH_MAX = 20000            # folders waiting to be read again; beyond it, the map is made again instead


class Watch:
    """Windows tells Rakiza Core what changes inside a drive or folder (ReadDirectoryChangesW): one thread
    waits for it, using nothing while nothing changes, and notes the folders something changed in.
    take() hands them over (as paths inside the root, "" for the root itself)."""

    def __init__(self, root: Path):
        self.root = str(root)
        self._lock = threading.Lock()
        self._changed: set[str] = set()
        self._lost = False             # Windows lost count (too much at once): the map is made again
        self._thread: threading.Thread | None = None
        self._stop = None              # (Windows' event that wakes the thread up to stop)
        self.alive = False
        self.failed = False

    def start(self) -> bool:
        if os.name != "nt" or self._thread is not None:
            return False
        k32 = ctypes.windll.kernel32
        k32.CreateEventW.restype = ctypes.c_void_p
        self._stop = k32.CreateEventW(None, True, False, None)
        if not self._stop:
            return False
        self.alive = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="corefold-space-watch")
        self._thread.start()
        return True

    def stop(self) -> None:
        self.alive = False
        if self._stop:
            ctypes.windll.kernel32.SetEvent(ctypes.c_void_p(self._stop))

    def take(self) -> tuple[set[str], bool]:
        """The folders something changed in since last time, and whether Windows lost count."""
        with self._lock:
            changed, lost = self._changed, self._lost
            self._changed, self._lost = set(), False
        return changed, lost

    def note(self, folders, lost: bool = False) -> None:
        with self._lock:
            if lost or self._lost:
                self._lost = True
                self._changed.clear()
                return
            self._changed.update(folders)
            if len(self._changed) > WATCH_MAX:
                self._lost = True
                self._changed.clear()

    def _run(self):
        from ctypes import wintypes as wt
        k32 = ctypes.windll.kernel32

        class OVERLAPPED(ctypes.Structure):
            _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                        ("Offset", wt.DWORD), ("OffsetHigh", wt.DWORD), ("hEvent", ctypes.c_void_p)]
        k32.CreateFileW.restype = ctypes.c_void_p
        k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD,
                                    ctypes.c_void_p]
        k32.ReadDirectoryChangesW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, wt.BOOL, wt.DWORD,
                                              ctypes.POINTER(wt.DWORD), ctypes.POINTER(OVERLAPPED), ctypes.c_void_p]
        k32.GetOverlappedResult.argtypes = [ctypes.c_void_p, ctypes.POINTER(OVERLAPPED),
                                            ctypes.POINTER(wt.DWORD), wt.BOOL]
        k32.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.POINTER(OVERLAPPED)]
        k32.WaitForMultipleObjects.argtypes = [wt.DWORD, ctypes.POINTER(ctypes.c_void_p), wt.BOOL, wt.DWORD]
        k32.CreateEventW.restype = ctypes.c_void_p
        k32.ResetEvent.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        # (below the window's threads: what it reads in can wait a moment)
        k32.SetThreadPriority(k32.GetCurrentThread(), -1)
        handle = k32.CreateFileW(self.root, 0x0001, 0x7, None, 3, 0x02000000 | 0x40000000, None)
        if not handle or handle == ctypes.c_void_p(-1).value:
            log.info("can't follow changes in %s (%s)", self.root, ctypes.GetLastError())
            self.failed = True
            self.alive = False
            k32.CloseHandle(self._stop)
            self._stop = None
            return
        buf = ctypes.create_string_buffer(WATCH_BUFFER)
        ov = OVERLAPPED()
        ov.hEvent = k32.CreateEventW(None, True, False, None)
        waits = (ctypes.c_void_p * 2)(ov.hEvent, self._stop)
        got = wt.DWORD()
        try:
            while self.alive:
                k32.ResetEvent(ov.hEvent)
                if not k32.ReadDirectoryChangesW(handle, buf, WATCH_BUFFER, True, _NOTIFY, None,
                                                 ctypes.byref(ov), None):
                    log.info("stopped following changes in %s (%s)", self.root, ctypes.GetLastError())
                    self.failed = True
                    break
                which = k32.WaitForMultipleObjects(2, waits, False, 0xFFFFFFFF)
                if which != 0:  # (asked to stop, or something went wrong)
                    k32.CancelIoEx(handle, ctypes.byref(ov))
                    k32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(got), True)
                    break
                if not k32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(got), False):
                    self.note((), lost=True)  # (ERROR_NOTIFY_ENUM_DIR: more changed than Windows kept)
                    continue
                if got.value == 0:
                    self.note((), lost=True)
                    continue
                self.note(changed_folders(buf.raw[:got.value]))
        finally:
            self.alive = False
            k32.CloseHandle(handle)
            k32.CloseHandle(ov.hEvent)
            stop, self._stop = self._stop, None
            if stop:
                k32.CloseHandle(stop)


def changed_folders(data: bytes) -> set[str]:
    """The folders (paths inside the root) that Windows' list of changes (FILE_NOTIFY_INFORMATION
    records) says something changed in: the folder holding each file or folder named."""
    import struct
    out: set[str] = set()
    pos = 0
    while pos + 12 <= len(data):
        nxt, _action, length = struct.unpack_from("<III", data, pos)
        name = data[pos + 12:pos + 12 + length].decode("utf-16-le", "replace")
        out.add(os.path.dirname(name))
        if not nxt:
            break
        pos += nxt
    return out


def folder_at(result: Scan, inside: str) -> Folder:
    """The map's folder for a path inside its root (the nearest one the map has, for a new one)."""
    f = result.root
    for part in Path(inside).parts if inside else ():
        key = os.path.normcase(part)
        nxt = next((c for c in f.children if os.path.normcase(c.name) == key), None)
        if nxt is None:
            break
        f = nxt
    return f


def attached(result: Scan, folder: Folder) -> bool:
    """The folder is still on the map (not taken off since)."""
    f = folder
    while f.parent is not None:
        if not any(c is f for c in f.parent.children):
            return False
        f = f.parent
    return f is result.root


def read_again(jobs: list[tuple[str, set[str]]], cancel: threading.Event | None = None) -> list:
    """Reads these folders again from the disk (the slow part of following changes, done on a helper
    thread): (path, the names of the folders the map knows in it) -> (path, its entries, whether Windows
    said no, {name: a look through each new folder in it})."""
    out = []
    for path, known in jobs:
        if cancel is not None and cancel.is_set():
            break
        entries, denied = _list_dir(path)
        known_n = {os.path.normcase(k) for k in known}
        new = {}
        for name, is_dir, *_rest in entries:
            if is_dir and os.path.normcase(name) not in known_n:
                new[name] = scan(Path(path, name), cancel, workers=2)
        out.append((path, entries, denied, new))
    return out


def _grow(f: Folder | None, size: int, files: int, folders: int) -> None:
    """Adds to a folder and every folder around it (keeping each one's folders biggest first)."""
    while f is not None:
        f.size = max(0, f.size + size)
        f.files = max(0, f.files + files)
        f.folders = max(0, f.folders + folders)
        if f.parent is not None:
            f.parent.children.sort(key=lambda c: c.size, reverse=True)
        f = f.parent


def _newer(f: Folder | None, when: float) -> None:
    """Something in this folder changed at `when`: it and every folder around it are at least that new."""
    while f is not None and when >= f.newest:
        f.newest = when
        f = f.parent


def _count(result: Scan, files, sign: int) -> None:
    """Adds files (size, name) to the kinds and types of the scan, or takes them off (sign -1)."""
    for size, name in files:
        kind = kind_of(name)
        result.kinds[kind] = max(0, result.kinds.get(kind, 0) + sign * size)
        ext = type_of(name)
        b, n = result.types.get(ext, (0, 0))
        b, n = max(0, b + sign * size), n + sign
        if n > 0:
            result.types[ext] = (b, n)
        else:
            result.types.pop(ext, None)
            result.biggest_type.pop(ext, None)


def _merge(result: Scan, sub: Scan) -> None:
    """Adds a look through a new folder to the scan's totals and lists."""
    for kind, size in sub.kinds.items():
        result.kinds[kind] = result.kinds.get(kind, 0) + size
    for ext, (b, n) in sub.types.items():
        b0, n0 = result.types.get(ext, (0, 0))
        result.types[ext] = (b0 + b, n0 + n)
        result.biggest_type.setdefault(ext, []).extend(sub.biggest_type.get(ext, []))
    result.biggest.extend(sub.biggest)
    result.old.extend(sub.old)
    for kind, items in sub.biggest_kind.items():
        result.biggest_kind.setdefault(kind, []).extend(items)
    result.denied += sub.denied
    result.cloud += sub.cloud


def _settle(result: Scan) -> None:
    """The scan's lists back in order (biggest first) and to their length."""
    result.biggest = sorted(result.biggest, reverse=True)[:TOP_OVERALL]
    result.old = sorted(result.old, reverse=True)[:TOP_OLD]
    result.biggest_kind = {k: sorted(v, reverse=True)[:TOP_KIND] for k, v in result.biggest_kind.items()}
    result.types = dict(sorted(result.types.items(), key=lambda kv: kv[1][0], reverse=True)[:KEEP_TYPES])
    result.biggest_type = {ext: sorted(result.biggest_type.get(ext, []), reverse=True)[:TOP_TYPE]
                           for ext in result.types}


def _take_out(result: Scan, gone) -> None:
    """Takes the files that gone(path) says are gone off the scan's lists."""
    result.biggest = [b for b in result.biggest if not gone(b[1])]
    result.old = [b for b in result.old if not gone(b[1])]
    result.biggest_kind = {k: [b for b in v if not gone(b[1])] for k, v in result.biggest_kind.items()}
    result.biggest_type = {k: [b for b in v if not gone(b[1])] for k, v in result.biggest_type.items()}


def apply_changes(result: Scan, read: list, folders: dict[str, Folder]) -> bool:
    """Brings the map up to date with folders read again (read_again()'s answer; folders: path -> the
    map's folder): their own files, the folders gone from them and the new ones. Exact: the sizes, the
    kinds, the file types and the lists all follow. Whether anything changed."""
    hidden_bits = _OFFLINE | _RECALL_ACCESS | _RECALL_OPEN
    old_before = time.time() - OLD_AFTER_DAYS * 86400
    touched: set[str] = set()       # folders whose own files changed (their old ones leave the lists)
    gone: list[str] = []            # folders taken off (everything in them leaves the lists)
    added: list = []                # (path, [(size, name, last used)]) of the folders' new own files
    grafts: list[Scan] = []
    for path, entries, denied, new in read:
        folder = folders.get(path)
        if folder is None or not attached(result, folder):
            continue
        if denied and not entries:
            continue  # (Windows said no this time, or it's gone: its parent is read again for that)
        own: list[tuple[int, str]] = []
        used_of: dict[str, float] = {}
        changed_of: dict[str, float] = {}
        dirs: set[str] = set()
        cloud = 0
        for name, is_dir, size, attrs, used, when in entries:
            if is_dir:
                dirs.add(os.path.normcase(name))
            elif attrs & hidden_bits:
                cloud += 1
            elif not attrs & _LINKED:
                own.append((size, name))
                used_of[name] = used
                changed_of[name] = when
        before = folder.own_files()
        lost = [c for c in folder.children if os.path.normcase(c.name) not in dirs]
        changed = sorted(before, reverse=True) != sorted(own, reverse=True) or lost or new
        if not changed:
            continue
        if sorted(before) != sorted(own):
            _count(result, before, -1)
            touched.add(path)
            folder.set_files([(s, n, changed_of.get(n, 0.0)) for s, n in own])
            _newer(folder, folder.newest)
            _count(result, own, +1)
            delta_size = sum(s for s, _ in own) - sum(s for s, _ in before)
            delta_files = len(own) - len(before)
            result.files += delta_files
            _grow(folder, delta_size, delta_files, 0)
            added.append((path, [(s, n, used_of.get(n, 0.0)) for s, n in own]))
        for child in lost:
            todo = [child]
            while todo:
                f = todo.pop()
                _count(result, f.own_files(), -1)
                todo.extend(f.children)
            folder.children = [c for c in folder.children if c is not child]
            gone.append(str(child.path))
            result.files -= child.files
            result.folders -= child.folders + 1
            _grow(folder, -child.size, -child.files, -(child.folders + 1))
        for name, sub in new.items():
            if sub.cancelled or any(os.path.normcase(c.name) == os.path.normcase(name) for c in folder.children):
                continue
            top = sub.root
            top.name, top.parent = name, folder
            folder.children.append(top)
            result.files += sub.files
            result.folders += sub.folders + 1
            result.linked += sub.linked
            _grow(folder, top.size, top.files, top.folders + 1)
            _newer(folder, top.newest)
            grafts.append(sub)
    if not touched and not gone and not grafts:
        return False
    prefixes = tuple(g + os.sep for g in gone)
    _take_out(result, lambda p: os.path.dirname(p) in touched or (prefixes and p.startswith(prefixes)))
    for sub in grafts:
        _merge(result, sub)
    for path, files in added:
        for size, name, used in files:
            full = os.path.join(path, name)
            result.biggest.append((size, full))
            result.biggest_kind.setdefault(kind_of(name), []).append((size, full))
            result.biggest_type.setdefault(type_of(name), []).append((size, full))
            if 0 < used < old_before and size >= 1 << 20:
                result.old.append((size, full, used))
    _settle(result)
    return True


def forget(result: Scan, path: Path, folder: Folder | None) -> None:
    """Takes a file or folder Rakiza Core just removed off the map, without reading the disk."""
    if folder is not None:
        parent = folder.parent
        if parent is None or not any(c is folder for c in parent.children):
            return
        todo = [folder]
        while todo:
            f = todo.pop()
            _count(result, f.own_files(), -1)
            todo.extend(f.children)
        parent.children = [c for c in parent.children if c is not folder]
        result.files -= folder.files
        result.folders -= folder.folders + 1
        _grow(parent, -folder.size, -folder.files, -(folder.folders + 1))
        prefix = str(folder.path) + os.sep
        _take_out(result, lambda p: p.startswith(prefix))
    else:
        try:
            inside = os.path.relpath(str(path.parent), str(result.root.path))
        except ValueError:  # (another drive)
            return
        parent = folder_at(result, "" if inside == "." else inside)
        if os.path.normcase(str(parent.path)) != os.path.normcase(str(path.parent)):
            return
        own = parent.own_files_timed()
        keep = [(s, n, t) for s, n, t in own if n != path.name]
        if len(keep) == len(own):
            return
        gone_files = [(s, n) for s, n, _t in own if n == path.name]
        _count(result, gone_files, -1)
        parent.set_files(keep)
        size = sum(s for s, _ in gone_files)
        result.files -= len(gone_files)
        _grow(parent, -size, -len(gone_files), 0)
        full = str(path)
        _take_out(result, lambda p: p == full)
    _settle(result)


# ----------------------------------------------------------------------------- what grew since last time

GREW_MIN = 50 << 20      # folders that grew less than this aren't mentioned
KEEP_FOLDERS = 30 << 20  # folders remembered for next time: the ones at least this big…
KEEP_DEPTH = 5           # …this deep at most
KEEP_ROOTS = 12


def _history_file() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or str(Path.home())) / "Rakiza Core" / "space-history.json"


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


def expected_size(root: Path, file: Path | None = None) -> int:
    """About how much a look through root will find (to show how far along it is): what the last look found
    there, else, for a whole drive, the room used on it. 0 when it can't be told."""
    root_path = Folder(str(root)).path
    file = file or _history_file()
    try:
        data = json.loads(file.read_text("utf-8"))
        last = data.get(os.path.normcase(str(root_path))) if isinstance(data, dict) else None
        size = last["sizes"].get(str(root_path)) if isinstance(last, dict) and isinstance(last.get("sizes"), dict) \
            else None
        if isinstance(size, int) and size > 0:
            return size
    except (OSError, ValueError, AttributeError):
        pass
    if os.path.normcase(str(root_path)).rstrip("\\/") == os.path.normcase(root_path.anchor).rstrip("\\/"):
        try:
            return shutil.disk_usage(str(root_path)).used
        except OSError:
            return 0
    return 0


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
    from .core import windows_folder
    keep = [_env_path("SystemRoot", r"C:\Windows"), windows_folder(), _env_path("ProgramFiles"), _env_path("ProgramFiles(x86)"),
            _env_path("ProgramData"), _env_path("ProgramW6432")]
    home = Path.home()
    users = Path(os.environ.get("SystemDrive", "C:") + os.sep + "Users")
    # (AppData itself, LocalLow and the per-user Programs folder hold every app's settings and programs)
    exact = [home, home.parent, _env_path("APPDATA"), _env_path("LOCALAPPDATA"), users, home / "AppData",
             home / "AppData" / "LocalLow", _env_path("LOCALAPPDATA", "", "Programs"), _env_path("PUBLIC"),
             users / "Public", users / "Default"]
    # (the person's own main folders: Windows and many programs expect them, and one in the Recycle Bin
    # takes everything in it along; what's inside them can still go)
    exact += [home / name for name in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos",
                                        "OneDrive", "Saved Games", "Contacts", "Favorites", "Links", "Searches",
                                        "3D Objects")]
    exact += [_env_path(n) for n in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")]
    for k in keep:
        if k is not None:
            kp = os.path.normcase(os.path.abspath(str(k)))
            if p == kp or p.startswith(kp + os.sep):
                return True
    for e in exact:
        if e is not None and p == os.path.normcase(os.path.abspath(str(e))):
            return True
    if own_windows_folder(path):  # (any account's, not only this one's)
        return True
    name = os.path.basename(p)
    return name in ("pagefile.sys", "hiberfil.sys", "swapfile.sys", "$recycle.bin", "system volume information")


def own_windows_folder(path: Path) -> bool:
    """An account's own folder that Windows keeps (Desktop, Documents, Downloads, Pictures, AppData...), of
    any account: in the Recycle Bin it would leave Windows without it. What's inside can go, not the folder."""
    from .uninstall import _profile_place
    return _profile_place(os.path.normcase(os.path.abspath(str(path))))


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


# ----------------------------------------------------------------------------- a file with what belongs to it

# files that only go with a file of the same name: subtitles, a video's notes, a picture's edits…
SIDECARS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup", ".nfo", ".xmp", ".aae", ".thm", ".lrc",
            ".cue", ".sfv", ".md5", ".sha1", ".sha256", ".par2"}
# what's left of the file being downloaded, saved or kept in case: "video.mp4.part", "report.docx.bak"
PARTIALS = {".part", ".crdownload", ".partial", ".download", ".opdownload", ".!ut", ".!qb", ".tmp", ".bak", ".old"}
# folder names too common to say a same-named folder elsewhere belongs to the same app
_COMMON_NAMES = {"temp", "tmp", "cache", "caches", "log", "logs", "data", "config", "settings", "packages",
                 "programs", "common files", "crashdumps", "default", "backup", "backups", "updates", "update",
                 "downloads", "files", "app", "apps", "user data", "profiles", "local", "roaming", "locallow"}

RELATED_WHY = {  # why something goes with it (English, shown through tr())
    "sidecar": "Goes with it (same name)",
    "partial": "Left from saving or downloading it",
    "lock": "Office's lock file for it",
    "shortcut": "A shortcut to it",
    "same_name": "Same name, another type",
    "app_data": "A folder of the same name in app data",
}


@dataclass
class Related:
    path: Path
    size: int
    why: str           # a key of RELATED_WHY
    sure: bool         # ticked to begin with (the ones that only might belong to it aren't)


def _app_data_places() -> list[Path]:
    places = [_env_path("APPDATA"), _env_path("LOCALAPPDATA"), _env_path("USERPROFILE", "", "AppData", "LocalLow"),
              _env_path("ProgramData"), _env_path("TEMP")]
    out, seen = [], set()
    for p in places:
        if p is None:
            continue
        k = os.path.normcase(os.path.abspath(str(p)))
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def _recent_folder() -> Path | None:
    return _env_path("APPDATA", "", "Microsoft", "Windows", "Recent")


def _shortcuts_to(target: Path, folder: bool, limit_s: float = 8.0) -> list[Path]:
    """Windows shortcuts (Desktop, Start menu, taskbar, Recent) that point at it (or inside it, for a folder)."""
    from .uninstall import lnk_target, shortcut_roots
    t = os.path.normcase(os.path.abspath(str(target)))
    roots = list(shortcut_roots())
    recent = _recent_folder()
    if recent is not None and recent.is_dir():
        roots.append(recent)
    out: list[Path] = []
    started = time.monotonic()
    for root in roots:
        stack = [str(root)]
        while stack and time.monotonic() - started < limit_s:
            here = stack.pop()
            try:
                with os.scandir(here) as it:
                    for e in it:
                        try:
                            if e.is_dir(follow_symlinks=False):
                                if not _skip_dir(e):
                                    stack.append(e.path)
                                continue
                        except OSError:
                            continue
                        if not e.name.lower().endswith(".lnk"):
                            continue
                        where = lnk_target(Path(e.path))
                        if not where:
                            continue
                        w = os.path.normcase(os.path.abspath(where))
                        if w == t or (folder and w.startswith(t + os.sep)):
                            out.append(Path(e.path))
            except OSError:
                continue
    return out


def related(path: Path, apps: list | None = None) -> list[Related]:
    """What goes with a file or folder, to remove along with it (only what's offered: the person ticks).
    Never anything Windows or a program needs (protected()), nothing inside it (that goes anyway), and,
    for a folder, nothing where an installed program lives (apps: the Uninstaller's list)."""
    from .uninstall import _inside, _safe_folder, _system_named
    path = Path(os.path.abspath(str(path)))
    me = os.path.normcase(str(path))
    is_folder = path.is_dir() and not path.is_symlink()
    found: dict[str, Related] = {}

    def offer(p: Path, why: str, sure: bool):
        k = os.path.normcase(os.path.abspath(str(p)))
        if k == me or k.startswith(me + os.sep) or me.startswith(k + os.sep) or k in found:
            return
        if protected(p) or not os.path.lexists(p):
            return
        size = _size_of(p)[0]
        found[k] = Related(p, size, why, sure)

    if not is_folder:
        name = path.name
        low = name.lower()
        stem = os.path.splitext(low)[0]
        try:
            with os.scandir(path.parent) as it:
                for e in it:
                    try:
                        if not e.is_file(follow_symlinks=False) or e.name == name:
                            continue
                    except OSError:
                        continue
                    other = e.name.lower()
                    ext = os.path.splitext(other)[1]
                    if other.startswith(low + ".") and ext in PARTIALS:
                        offer(Path(e.path), "partial", True)
                    elif other in ("~$" + low, "~$" + low[2:]) and len(low) > 2:
                        offer(Path(e.path), "lock", True)
                    elif ext in SIDECARS and (os.path.splitext(other)[0] == stem or
                                              other.startswith(stem + ".") or other.startswith(low + ".")):
                        offer(Path(e.path), "sidecar", True)
                    elif os.path.splitext(other)[0] == stem and stem:
                        offer(Path(e.path), "same_name", False)
        except OSError:
            pass
    for link in _shortcuts_to(path, is_folder):
        offer(link, "shortcut", True)
    if is_folder:
        key = path.name.lower()
        if len(key) >= 4 and key not in _COMMON_NAMES and not _system_named(path.name):
            for place in _app_data_places():
                cand = place / path.name
                try:
                    if not cand.is_dir() or cand.is_symlink():
                        continue
                except OSError:
                    continue
                c = str(cand)
                if not _safe_folder(c) or own_windows_folder(cand):
                    continue
                if any((a.location or "").strip() and (_inside(a.location, c) or _inside(c, a.location))
                       for a in apps or []):
                    continue  # (an installed program lives there: the Uninstaller's business)
                offer(cand, "app_data", False)
    return sorted(found.values(), key=lambda r: (not r.sure, -r.size))


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
    admin: bool = False        # Windows' own files: removed by an administrator copy of Rakiza Core (Windows asks)
    about: str = ""            # how much it frees, when only Windows can say beforehand (English, through tr())


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


def find_junk(downloads: Path | None = None, cancel: threading.Event | None = None, admin: bool = True) -> list[Junk]:
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

    if admin:  # (the clean-up by itself never touches Windows' own files: it doesn't look for them)
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


# Windows' own leftovers, which only an administrator can remove. The administrator copy of Rakiza Core
# is only told which of these keys to clean; the folders come from here, never from the request.
ADMIN_KEYS = ("winupdate", "winold", "delivery", "hibernate", "restorepoints", "compactos")
SETTING_KEYS = ("hibernate", "restorepoints", "compactos")  # Windows settings, not folders
DELIVERY_MIN = 50 << 20  # update copies kept for other PCs: offered from this size


def _windows_dir() -> Path:
    """Where Windows is, from Windows itself (not from the environment, which a program could change)."""
    from .core import windows_folder
    return windows_folder()


def admin_path(key: str) -> Path | None:
    windows = _windows_dir()
    if key == "winupdate":
        return windows / "SoftwareDistribution" / "Download"
    if key == "winold":
        return Path(windows.drive + "\\") / "Windows.old"
    if key == "delivery":  # (only the Delivery Optimization service and administrators can look inside)
        return windows / "ServiceProfiles" / "NetworkService" / "AppData" / "Local" / "Microsoft" / "Windows" / \
            "DeliveryOptimization" / "Cache"
    return None


def delivery_size() -> int:
    """How much Windows keeps of updates and Store apps to share with other PCs (Delivery Optimization),
    as Windows itself says (its folder is closed to everyone but administrators). 0 when it can't say."""
    import subprocess
    from .uninstall import powershell
    try:
        out = powershell("(Get-DeliveryOptimizationPerfSnap -ErrorAction Stop).CacheSizeBytes", timeout=30)
        return max(0, int(out.splitlines()[-1].strip())) if out else 0
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 0


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
    if not (cancel is not None and cancel.is_set()):
        size = delivery_size()
        if size >= DELIVERY_MIN:
            rows.append(Junk("delivery", "Update copies for other PCs", "Windows keeps updates and Store apps it "
                             "downloaded, to hand them to other PCs. Windows removes them its own way, and downloads "
                             "again any it needs. Windows asks for an administrator.",
                             [admin_path("delivery")], size, 0, ticked=False, admin=True))
    if not (cancel is not None and cancel.is_set()):
        try:
            rows += _setting_junk(cancel)
        except Exception:  # (Windows' settings rows are extras: the rest of the list is shown without them)
            log.info("Windows' settings rows couldn't be made", exc_info=True)
    return rows


HIBERNATE_MIN = 512 << 20


def hibernation_file() -> int:
    """How big Windows' hibernation file is (it keeps the memory there to wake up, and for Fast startup), 0 when
    there's none."""
    try:
        return (Path(_windows_dir().drive + "\\") / "hiberfil.sys").stat().st_size
    except OSError:
        return 0


def restore_points_size() -> int:
    """The room Windows' restore points take on the Windows drive, as Windows says (0 when it doesn't say to
    someone who isn't an administrator)."""
    import subprocess
    from .uninstall import powershell
    try:
        out = powershell("$v = (Get-CimInstance Win32_Volume -Filter \"DriveLetter='" + _windows_dir().drive +
                         "'\" -ErrorAction Stop).DeviceID; (Get-CimInstance Win32_ShadowStorage -ErrorAction Stop | "
                         "Where-Object { $_.Volume.DeviceID -eq $v } | Measure-Object UsedSpace -Sum).Sum",
                         timeout=30)
        return max(0, int(float(out.splitlines()[-1].strip()))) if out and out.strip() else 0
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 0


def restore_point_count() -> int:
    """How many restore points there are (from Windows' own list, which anyone may read)."""
    import subprocess
    from .uninstall import powershell
    try:
        out = powershell("@(Get-CimInstance -Namespace root/default -ClassName SystemRestore -ErrorAction Stop).Count",
                         timeout=30)
        return max(0, int(out.splitlines()[-1].strip())) if out else 0
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 0


def windows_is_compact() -> bool:
    """Windows' own files are packed already (Compact OS): some of its biggest files take less room on the disk
    than they hold."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.GetCompressedFileSizeW.restype = wintypes.DWORD
    k32.GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    system32 = _windows_dir() / "System32"
    for name in ("shell32.dll", "ntdll.dll", "mshtml.dll", "Windows.UI.Xaml.dll"):
        f = system32 / name
        try:
            size = f.stat().st_size
        except OSError:
            continue
        high = wintypes.DWORD(0)
        low = k32.GetCompressedFileSizeW(str(f), ctypes.byref(high))
        if low == 0xFFFFFFFF:
            continue
        if size > 1 << 20 and ((high.value << 32) | low) < size * 0.85:
            return True
    return False


def _setting_junk(cancel: threading.Event | None = None) -> list[Junk]:
    """Windows settings that free a lot of room (each unticked, each explained)."""
    rows = []
    size = hibernation_file()
    if size >= HIBERNATE_MIN:
        rows.append(Junk("hibernate", "Hibernation file", "Windows keeps your memory here to wake up from hibernation "
                         "and for Fast startup. Switching hibernation off removes it, and Windows starts a little "
                         "slower from off. It can be switched on again in Windows. Windows asks for an administrator.",
                         [], size, 1, ticked=False, admin=True))
    if cancel is not None and cancel.is_set():
        return rows
    count = restore_point_count()
    if count > 1:
        size = restore_points_size()
        rows.append(Junk("restorepoints", "Older restore points", "Windows' restore points, except the newest one. "
                         "Going back to an older one isn't possible afterwards. Windows asks for an administrator.",
                         [], size, count - 1, ticked=False, admin=True, about="" if size else "Windows says after"))
    if cancel is not None and cancel.is_set():
        return rows
    try:
        compact = windows_is_compact()
    except (OSError, AttributeError, ValueError):
        compact = True
    if not compact:
        rows.append(Junk("compactos", "Pack Windows tighter (Compact OS)", "Windows packs its own files, which usually "
                         "frees 2 to 4 GB. It takes 10 to 20 minutes and can make a very old PC a little slower. Windows "
                         "asks for an administrator.", [], 0, 0, ticked=False, admin=True, about="About 2 to 4 GB"))
    return rows


def _free_on_windows_drive() -> int:
    import shutil
    try:
        return shutil.disk_usage(_windows_dir().drive + "\\").free
    except OSError:
        return 0


def _clean_setting(key: str, system32: Path, run) -> int:
    """In the administrator copy: one of SETTING_KEYS. Returns the bytes freed."""
    before = _free_on_windows_drive()
    if key == "hibernate":
        run("powercfg", "/hibernate", "off", timeout=120)
    elif key == "restorepoints":
        # only the restore points of the Windows drive that Windows itself made to go back to (never another
        # program's copies), and never the newest
        from .uninstall import powershell
        try:
            powershell("$v = (Get-CimInstance Win32_Volume -Filter \"DriveLetter='" + _windows_dir().drive + "'\").DeviceID; "
                       "$all = @(Get-CimInstance Win32_ShadowCopy | Where-Object { $_.VolumeName -eq $v -and "
                       "$_.ClientAccessible } | Sort-Object InstallDate); "
                       "if ($all.Count -gt 1) { $all[0..($all.Count - 2)] | Remove-CimInstance }", timeout=900)
        except Exception:
            log.warning("couldn't remove the older restore points", exc_info=True)
    elif key == "compactos":
        run("compact", "/CompactOS:always", timeout=3600)
    return max(0, _free_on_windows_drive() - before)


def clean_as_admin(keys: list[str]) -> int:
    """In the administrator copy of Rakiza Core: removes Windows' own leftovers. Returns the bytes freed."""
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
    from .core import is_link
    from .uninstall import through_link
    for key in keys:
        if key in SETTING_KEYS:
            freed += _clean_setting(key, system32, run)
            continue
        path = admin_path(key) if key in ADMIN_KEYS else None
        if path is None or not path.is_dir():
            continue
        if is_link(path) or through_link(path):
            # (any account can make a "C:\Windows.old" that is a junction to Windows' own folders: taking it
            # over as administrator would take those over)
            log.warning("%s leads somewhere else: left alone", path)
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
        elif key == "delivery":
            # the Delivery Optimization service owns these files: it's asked to let them go, never bypassed
            from .uninstall import powershell
            try:
                powershell("Delete-DeliveryOptimizationCache -Force -ErrorAction Stop", timeout=600)
            except (OSError, subprocess.SubprocessError):
                log.warning("Delivery Optimization's clean-up failed", exc_info=True)
        elif key == "winold":
            # Windows' own Disk Cleanup removes it the way Windows means it to ("Previous Windows
            # installation(s)"); what it leaves (or everything, without Disk Cleanup) goes the hard way
            _previous_windows_by_windows(system32, run)
            if path.is_dir() and not is_link(path) and _tree_size(path, 60.0)[0] > 50 << 20:
                # Its old profiles hold links to the folders of the Windows in use ("Documents and Settings"
                # leads to C:\Users, "All Users" to C:\ProgramData), and takeown /R and icacls /T go through
                # them, even with /L: every file of every person on the PC would change hands. So the links
                # go first (only the links: what they lead to stays as it is); takeover and permissions only
                # when none is left. rd never follows a link.
                if _drop_links_inside(path, run):
                    own = ["takeown", "/F", str(path), "/R", "/A", "/D", "Y"]
                    if _takeown_skips_links(system32):
                        own.append("/SKIPSL")
                    run(*own, timeout=1800)
                    if _drop_links_inside(path, run):  # (any it couldn't see before it was taken over)
                        run("icacls", str(path), "/grant", "*S-1-5-32-544:F", "/T", "/C", "/L", "/Q", timeout=1800)
                else:
                    log.warning("links left in %s: only what rd removes goes", path)
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


def _remove_link(path: str) -> bool:
    """Removes a link or junction (only the link). False if Windows says no."""
    try:
        os.rmdir(path)  # (a junction or a folder link)
        return True
    except OSError:
        pass
    try:
        os.unlink(path)  # (a file link)
        return True
    except OSError:
        return False


LOCKED_LIMIT = 5000  # folders the administrator copy may take over one by one to look inside


def _drop_links_inside(top: Path, run=None, limit_s: float = 900.0) -> bool:
    """Removes every link and junction inside top (the link itself, never what it leads to). True when the
    whole of top was looked through and no link is left. A folder even an administrator can't look into is
    taken over first, that folder alone (run: starts a program of Windows' own, as in clean_as_admin)."""
    from .core import is_link
    started = time.monotonic()
    stack, locked, clean = [str(top)], 0, True
    while stack:
        if time.monotonic() - started > limit_s:
            return False
        here = stack.pop()
        try:
            with os.scandir(here) as it:
                entries = list(it)
        except PermissionError:
            if run is None or locked >= LOCKED_LIMIT:
                log.warning("couldn't look into %s", here)
                clean = False
                continue
            locked += 1
            run("takeown", "/F", here, "/A", timeout=60)  # (no /R: this folder only)
            run("icacls", here, "/grant", "*S-1-5-32-544:F", "/C", "/L", "/Q", timeout=60)
            try:
                with os.scandir(here) as it:
                    entries = list(it)
            except OSError:
                log.warning("couldn't look into %s, even taken over", here)
                clean = False
                continue
        except OSError:
            log.warning("couldn't look into %s", here, exc_info=True)
            clean = False
            continue
        for e in entries:
            try:
                st = e.stat(follow_symlinks=False)
                if is_link(e.path, st) or (getattr(st, "st_file_attributes", 0) & _REPARSE and
                                           getattr(st, "st_reparse_tag", 0) in (_JUNCTION, _SYMLINK)):
                    if not _remove_link(e.path) and run is not None:
                        # (one even an administrator may not remove: the link itself is taken over first,
                        # /L: never what it leads to)
                        run("icacls", e.path, "/setowner", "*S-1-5-32-544", "/C", "/L", "/Q", timeout=60)
                        run("icacls", e.path, "/grant", "*S-1-5-32-544:F", "/C", "/L", "/Q", timeout=60)
                    if os.path.lexists(e.path) and not _remove_link(e.path):
                        log.warning("couldn't remove the link %s", e.path)
                        clean = False
                    continue
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
            except OSError:
                log.warning("couldn't tell what %s is", e.path, exc_info=True)
                clean = False
    return clean


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
    if junk.key.startswith(("browser:", "app:")) and junk.key.split(":", 1)[1] in running_programs():
        return 0  # (started since it was found: its cache is in use now, and stays)
    now = time.time()
    freed = 0
    for p in junk.paths:
        if cancel is not None and cancel.is_set():
            break
        if junk.key == "temp" and not _still_old(p, now):
            continue  # (used again since it was found, or no longer a plain file or folder: it stays)
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


def _still_old(p: Path, now: float) -> bool:
    """A temporary file or folder found earlier is still one to remove: there, not a link or junction, and
    nothing in it changed in the last day (the page may have stayed open a long time since)."""
    try:
        st = os.lstat(p)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & _REPARSE:
        return False
    if now - st.st_mtime < 86400:
        return False
    if stat.S_ISDIR(st.st_mode):
        _size, _count, newest, whole = _tree_info(p, 10.0)
        return whole and now - newest >= 86400
    return True


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


# ----------------------------------------------------------------------------- similar photos

PHOTO_EXT = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff", ".gif", ".avif"}
SIMILAR_BITS = 6          # prints at most this many bits apart (of 64) are the same picture, near enough
SIMILAR_MIN = 30 * 1024   # (smaller pictures are icons and stickers, not photos)
SIMILAR_GROUPS = 400      # groups kept (the most room first)


def photo_print(path: str) -> tuple[int, int, bytes] | None:
    """A picture's print (a difference hash: 64 bits, each saying whether a spot of the picture, made tiny and
    grey, is brighter than the spot beside it; resized, recompressed or slightly edited copies have nearly the
    same print), how many pixels it has, and a small JPEG of it to show. None when it can't be read, or when it's
    one flat colour (all flat pictures have the same print)."""
    try:
        from PIL import Image, ImageOps
        if os.path.splitext(path)[1].lower() in (".heic", ".heif"):
            try:
                from pi_heif import register_heif_opener
                register_heif_opener()
            except Exception:
                return None
        with Image.open(path) as im:
            w, h = im.size
            if w * h > 300_000_000 or w < 16 or h < 16:   # (a booby-trapped or tiny picture: not opened)
                return None
            im.draft("RGB", (192, 192))                    # (a JPEG is read small straight away: much quicker)
            im = ImageOps.exif_transpose(im)
            small = im.convert("L").resize((9, 8), Image.Resampling.BILINEAR).tobytes()
            bits = 0
            for r in range(8):
                row = small[r * 9:r * 9 + 9]
                for c in range(8):
                    bits = (bits << 1) | (row[c] > row[c + 1])
            if bin(bits).count("1") < 3:
                return None
            thumb = im.convert("RGB")
            thumb.thumbnail((96, 96))
            import io
            buf = io.BytesIO()
            thumb.save(buf, "JPEG", quality=78)
            return bits, w * h, buf.getvalue()
    except Exception:
        return None


def find_similar_photos(folders: list[Path], cancel: threading.Event | None = None, progress=None
                        ) -> list[list[tuple[str, int, float, int, bytes]]]:
    """Photos that look the same (copies, resized or recompressed ones, burst shots a moment apart), as groups of
    (path, size, modified, pixels, small JPEG), oldest first in each group, the groups taking most room first.
    Nothing is changed; files only in the cloud aren't read (that would download them)."""
    photos: list[tuple[str, int, float]] = []
    seen_dirs: set[str] = set()
    stack = [str(f) for f in folders if f.is_dir()]
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
                        if os.path.splitext(e.name)[1].lower() not in PHOTO_EXT:
                            continue
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if st.st_size >= SIMILAR_MIN and not _online_only(st) and e.is_file(follow_symlinks=False):
                        photos.append((e.path, st.st_size, st.st_mtime))
                        if progress is not None and len(photos) % 500 == 0:
                            progress("look", len(photos), 0)
        except OSError:
            continue
    from concurrent.futures import ThreadPoolExecutor
    prints: list = []
    with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 4)), thread_name_prefix="corefold-photos") as pool:
        for n, got in enumerate(pool.map(lambda f: photo_print(f[0]), photos)):
            if cancel is not None and cancel.is_set():
                pool.shutdown(wait=False, cancel_futures=True)
                return []
            prints.append(got)
            if progress is not None and n % 50 == 0:
                progress("compare", n, len(photos))
    known = [(i, p) for i, p in enumerate(prints) if p is not None]
    # (two prints at most SIMILAR_BITS apart share at least one of their 8 bytes: only those are compared)
    parent = list(range(len(photos)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for byte in range(8):
        buckets: dict[int, list[int]] = {}
        for i, (bits, _px, _t) in known:
            buckets.setdefault((bits >> (8 * byte)) & 0xFF, []).append(i)
        for members in buckets.values():
            if len(members) < 2:
                continue
            for x in range(len(members)):
                a = members[x]
                pa = prints[a][0]
                for b in members[x + 1:]:
                    if root(a) != root(b) and bin(pa ^ prints[b][0]).count("1") <= SIMILAR_BITS:
                        parent[root(a)] = root(b)
    groups: dict[int, list] = {}
    for i, _p in known:
        groups.setdefault(root(i), []).append(i)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        files = _distinct([(photos[i][0], i) for i in members])   # (two names of one file aren't two photos)
        if len(files) < 2:
            continue
        rows = [(photos[i][0], photos[i][1], photos[i][2], prints[i][1], prints[i][2]) for _path, i in files]
        rows.sort(key=lambda r: r[2])
        out.append(rows)
    out.sort(key=lambda g: sum(r[1] for r in g) - max(r[1] for r in g), reverse=True)
    return out[:SIMILAR_GROUPS]


# ----------------------------------------------------------------------------- cleaning up by itself

AUTO_EVERY = {"off": 0, "week": 7 * 86400, "month": 30 * 86400}


def auto_cleanable(j: Junk) -> bool:
    """What the clean-up by itself may remove without asking: only temporary files, crash reports and the caches
    of browsers and apps that aren't open. Never the Recycle Bin, old installers, Windows' own files or previews."""
    return (j.key in ("temp", "dumps") or j.key.startswith(("browser:", "app:"))) and not j.busy and not j.admin \
        and not j.to_bin and j.size > 0


def auto_clean(cancel: threading.Event | None = None) -> int:
    """The clean-up by itself (Settings: every week or month, while Rakiza Core waits in the tray). Returns bytes
    freed."""
    freed = 0
    for j in find_junk(None, cancel, admin=False):
        if cancel is not None and cancel.is_set():
            break
        if auto_cleanable(j):
            freed += clean(j, cancel)
    return freed
