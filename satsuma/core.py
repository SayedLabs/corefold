"""Building blocks shared by all of Satsuma's tools (no GUI code here)."""
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

log = logging.getLogger("satsuma")

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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


def fallback_folder() -> Path:
    return Path.home() / "Downloads" / "Satsuma"


def writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=folder):
            pass
        return True
    except OSError:
        return False


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    n = 2
    while True:
        cand = p.with_name(f"{p.stem} ({n}){p.suffix}")
        if not cand.exists():
            return cand
        n += 1


def safe_name(text: str) -> str:
    bad = '<>:"/\\|?*'
    out = "".join("_" if c in bad or ord(c) < 32 else c for c in text).strip(" .")
    return out[:120]


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
            return src.stem if src.is_file() or src.suffix else src.name
        if self.index is None or self.total <= 1:
            return base
        return f"{base}-{self.options.rename_start + self.index}"

    def folder_for(self, src: Path) -> Path:
        folder = self.options.output_folder or src.parent
        return folder if writable(folder) else fallback_folder()

    def out_path(self, src: Path, name: str) -> Path:
        folder = self.folder_for(src)
        folder.mkdir(parents=True, exist_ok=True)
        return unique_path(folder / name)

    def out_dir(self, src: Path, name: str) -> Path:
        p = self.out_path(src, name)
        p.mkdir()
        return p


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
                            cwd=str(cwd) if cwd else None, env=env, creationflags=NO_WINDOW)
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
