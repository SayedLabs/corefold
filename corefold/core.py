"""Building blocks shared by all of Corefold's tools (no GUI code here)."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger("corefold")

APP_NAME = "Corefold"
LEGACY_NAME = "Satsuma"  # the name before 1.7.0; only used to bring old settings along and tidy up
PUBLISHER = "Atomic Works"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
_background = True  # run long jobs at "below normal" priority so the PC stays quick


def set_background_priority(on: bool):
    global _background
    _background = on


def job_flags() -> int:
    """Process flags for programs Corefold runs to do a job (FFmpeg, 7-Zip, LibreOffice)."""
    return NO_WINDOW | (BELOW_NORMAL if _background else 0)


def lower_this_thread():
    """Called at the start of each job, so conversions never make the PC feel slow.
    (Pool threads are reused, so the priority is set every time, both ways.)"""
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetThreadPriority(k32.GetCurrentThread(), -1 if _background else 0)  # BELOW_NORMAL / NORMAL
        except Exception:
            pass


class Cancelled(Exception):
    pass


@dataclass
class Options:
    output_folder: Path | None = None  # None = save next to the original
    jpg_quality: int = 90
    rename_base: str = ""              # "" = keep original names
    rename_start: int = 1


@dataclass
class Result:
    outputs: list[Path] = field(default_factory=list)
    clipboard: str | None = None
    note: str = ""
    locked: list = field(default_factory=list)  # items that need a (different) password
    undo: list = field(default_factory=list)    # (new, old) names, for "Undo" after renaming


@dataclass
class Tool:
    key: str          # stable id used by settings, e.g. "image:jpg"
    label: str        # short text on the wheel
    description: str  # shown in the middle of the wheel and in the progress popup
    run: Callable | None  # run(item, ctx) -> Result   (None for tools that open a window)
    batch: bool = False                          # gets all files at once instead of one at a time
    check: Callable[[list[Path]], str | None] | None = None  # reason it can't be used, or None
    ask: str | None = None                       # dialog to show first ("resize", "trim", "fit", ...)
    renames: bool = True                         # honours "ask for new names"
    opens: str | None = None                     # opens a window instead of running ("browse")
    password: bool = False                       # asks for the password if the archive has one
    target: str | None = None                    # the format it makes (".jpg"); left out of that format's menu
    slow_check: bool = False                     # check needs to read the file (done in the background)
    static_check: bool = False                   # check doesn't depend on the files (e.g. "needs Office")


_downloads = Path.home() / "Downloads"


def set_downloads_folder(folder: Path):
    """The app tells us where Downloads really is (it may have been moved to another drive or OneDrive)."""
    global _downloads
    _downloads = folder


def fallback_folder() -> Path:
    """Where new files go when the original's folder can't be written to (a CD, a protected folder)."""
    return _downloads / "Corefold"


_writable_cache: dict[str, tuple[float, bool]] = {}


def writable(folder: Path) -> bool:
    """Can Corefold save in this folder? (Remembered for a minute, so converting 500 files doesn't
    create and delete 500 test files in the folder.)"""
    import time
    key = str(folder)
    hit = _writable_cache.get(key)
    if hit and time.monotonic() - hit[0] < 60:
        return hit[1]
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=folder):
            pass
        ok = True
    except OSError:
        ok = False
    _writable_cache[key] = (time.monotonic(), ok)
    return ok


_reserved: set[str] = set()   # names handed out but maybe not written yet (two jobs at once)
_reserve_lock = threading.Lock()


def unique_path(p: Path) -> Path:
    with _reserve_lock:
        cand, n = p, 2
        while cand.exists() or os.path.normcase(str(cand)) in _reserved:
            cand = p.with_name(f"{p.stem} ({n}){p.suffix}")
            n += 1
        if len(_reserved) > 5000:
            _reserved.clear()
        _reserved.add(os.path.normcase(str(cand)))
        return cand


WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(text: str) -> str:
    bad = '<>:"/\\|?*'
    out = "".join("_" if c in bad or ord(c) < 32 else c for c in text).strip(" .")
    out = out[:120].rstrip(" .")
    if out.split(".")[0].upper() in WINDOWS_RESERVED:  # Windows can't make a file called "CON" or "NUL"
        out = "_" + out
    return out


def quick_size(path: Path, budget_s: float = 0.25) -> tuple[int, bool]:
    """Size of a file or folder, giving up after a moment so huge folders never freeze the window.
    Returns (bytes, complete)."""
    import time
    try:
        if not path.is_dir():
            return path.stat().st_size, True
    except OSError:
        return 0, True
    total, deadline = 0, time.monotonic() + budget_s
    for root, _dirs, files in os.walk(path):
        for i, f in enumerate(files):
            try:
                total += os.stat(os.path.join(root, f)).st_size
            except OSError:
                pass
            # also checked inside a folder: one huge folder on a slow network drive could freeze it
            if i % 200 == 199 and time.monotonic() > deadline:
                return total, False
        if time.monotonic() > deadline:
            return total, False
    return total, True


def short_name(name: str, limit: int = 46) -> str:
    """A very long file name shortened in the middle ("my holiday vide…final.mp4"), so it can't
    stretch a window or popup off the screen."""
    if len(name) <= limit:
        return name
    keep = limit - 1
    return name[: keep - keep // 3] + "…" + name[-(keep // 3):]


def is_cloud_only(path: Path) -> bool:
    """A OneDrive (or other cloud) file that isn't downloaded to this PC yet. Opening it would
    download all of it, so previews and quick checks leave it alone."""
    try:
        attrs = getattr(os.stat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & (0x400000 | 0x40000))  # RECALL_ON_DATA_ACCESS / RECALL_ON_OPEN


def human_size(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class Ctx:
    def __init__(self, options: Options, progress: Callable[[float], None] | None = None,
                 cancel: threading.Event | None = None, params: dict | None = None,
                 index: int | None = None, total: int = 1):
        self.options = options
        self.params = params or {}
        self.index, self.total = index, total
        self._progress = progress or (lambda f: None)
        self.cancel_event = cancel or threading.Event()
        self.made: list[Path] = []  # new files/folders handed out (removed again if the job fails)

    def progress(self, frac: float):
        self._progress(max(0.0, min(1.0, frac)))

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check(self):
        if self.cancelled:
            raise Cancelled()

    def stem(self, src: Path) -> str:
        """Base name for a new file: the original name, or the user's new name (+ number)."""
        base = safe_name(self.options.rename_base)
        if not base:
            return src.name if src.is_dir() else src.stem  # a folder called "v1.2" keeps its whole name
        if self.index is None or self.total <= 1:
            return base
        return f"{base}-{self.options.rename_start + self.index}"

    def folder_for(self, src: Path) -> Path:
        folder = self.options.output_folder or src.parent
        return folder if writable(folder) else fallback_folder()

    def out_path(self, src: Path, name: str) -> Path:
        folder = self.folder_for(src)
        folder.mkdir(parents=True, exist_ok=True)
        p = unique_path(folder / name)
        self.made.append(p)
        return p

    def out_dir(self, src: Path, name: str) -> Path:
        p = self.out_path(src, name)
        p.mkdir()
        return p

    def discard(self):
        """Removes what this job started writing: a failed or cancelled conversion must never
        leave a broken, half-written file behind. (Names are always new, so nothing else is touched.)"""
        import shutil
        for p in self.made:
            try:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                elif p.exists():
                    p.unlink()
            except OSError:
                pass
        self.made.clear()


# ----------------------------------------------------------------------------- file types

ALIASES = {".jpeg": ".jpg", ".jfif": ".jpg", ".tif": ".tiff", ".aif": ".aiff", ".heif": ".heic"}
IMAGE_EXT = {".png", ".jpg", ".webp", ".bmp", ".gif", ".tiff", ".heic", ".avif", ".ico"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aiff", ".wma", ".aac"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".m4v", ".flv", ".mpg", ".mpeg", ".3gp", ".ts"}
WORD_EXT = {".doc", ".docx", ".rtf", ".odt"}
EXCEL_EXT = {".xls", ".xlsx", ".ods"}
SLIDES_EXT = {".ppt", ".pptx", ".odp"}
DOCUMENT_EXT = WORD_EXT | EXCEL_EXT | SLIDES_EXT
TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tbz", ".tar.xz", ".txz")
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".tbz2", ".tbz", ".xz", ".txz", ".iso",
               ".cab", ".wim", ".lzh", ".lha", ".arj", ".z", ".lzma", ".zipx", ".001", ".cpio", ".rpm", ".deb"}
ALL_EXT = IMAGE_EXT | AUDIO_EXT | VIDEO_EXT | DOCUMENT_EXT | ARCHIVE_EXT | {".pdf"} | set(ALIASES)


def norm(ext: str) -> str:
    ext = ext.lower()
    return ALIASES.get(ext, ext)


def category(p: Path) -> str:
    if p.is_dir():
        return "folder"
    e = norm(p.suffix)
    if e in IMAGE_EXT:
        return "image"
    if e in AUDIO_EXT:
        return "audio"
    if e in VIDEO_EXT:
        return "video"
    if e == ".pdf":
        return "pdf"
    if e in DOCUMENT_EXT:
        return "document"
    if e in ARCHIVE_EXT or re.search(r"\.part0*1\.rar$", p.name, re.I):
        return "archive"
    return "other"


# ----------------------------------------------------------------------------- helpers

def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundled(*parts: str) -> Path | None:
    """A file shipped with the app (or in vendor/ when running from source)."""
    roots = [Path(getattr(sys, "_MEIPASS", app_dir())), app_dir(), app_dir() / "vendor"]
    for root in roots:
        p = root.joinpath(*parts)
        if p.exists():
            return p
    return None


_cache: dict = {}


_MISSING = object()


def is_cached(kind: str, path: Path) -> bool:
    try:
        st = path.stat()
    except OSError:
        return True  # nothing to read anyway
    return (kind, str(path), st.st_mtime_ns, st.st_size) in _cache


def clear_cache():
    _cache.clear()


def _cached(kind: str, path: Path, fn):
    """Remember slow facts about a file (duration, has audio, ...) until it changes."""
    try:
        st = path.stat()
        key = (kind, str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return fn()
    if key not in _cache:
        if len(_cache) > 500:
            _cache.clear()
        _cache[key] = fn()
    return _cache[key]


def run_with_progress(cmd: list[str], ctx: Ctx, parse: Callable[[str], float | None],
                      cwd: Path | None = None, env: dict | None = None) -> tuple[int, str]:
    """Runs a program, feeding its output to `parse` for progress; stops it on cancel.
    Returns (exit code, all output)."""
    out_chunks: list[str] = []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            cwd=str(cwd) if cwd else None, env=env, creationflags=job_flags())
    buf = ""
    try:
        while True:
            if ctx.cancelled:
                proc.kill()
                break
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(256)
            if not chunk:
                break
            text = chunk.decode("utf-8", "replace")
            out_chunks.append(text)
            buf = (buf + text)[-400:]
            frac = parse(buf)
            if frac is not None:
                ctx.progress(frac)
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    if ctx.cancelled:
        raise Cancelled()
    return proc.returncode, "".join(out_chunks)


def os_open(path: Path):
    """Open a file with its default app."""
    if os.name == "nt":
        os.startfile(str(path))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])
