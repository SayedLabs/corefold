"""
Satsuma's conversion tools. No GUI code here, so this module can be tested on its own.

Every tool takes (item, ctx): item is a Path (or a list of Paths for batch tools),
ctx gives the output location, progress reporting and cancellation.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from functools import partial
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
    run: Callable     # run(item, ctx) -> Result
    batch: bool = False          # gets all files at once instead of one at a time
    target_ext: str | None = None  # hidden when the file is already this format
    multi_only: bool = False     # only offered when several files are dropped


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


class Ctx:
    def __init__(self, options: Options, progress: Callable[[float], None] | None = None,
                 cancel: threading.Event | None = None):
        self.options = options
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


def norm(ext: str) -> str:
    ext = ext.lower()
    return ALIASES.get(ext, ext)


def category(p: Path) -> str:
    e = norm(p.suffix)
    if e in IMAGE_EXT:
        return "image"
    if e in AUDIO_EXT:
        return "audio"
    if e in VIDEO_EXT:
        return "video"
    if e == ".pdf":
        return "pdf"
    return "other"


# ----------------------------------------------------------------------------- images

_heif_registered = False


def load_image(src: Path):
    global _heif_registered
    from PIL import Image, ImageOps
    if not _heif_registered:
        try:
            from pi_heif import register_heif_opener  # decode-only HEIC support
            register_heif_opener()
        except ImportError:
            pass
        _heif_registered = True
    with Image.open(src) as im:
        out = ImageOps.exif_transpose(im)  # applies phone-camera rotation; returns a copy (first frame)
        out.load()
        return out


def has_alpha(im) -> bool:
    return im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info


def flatten(im):
    """Transparent areas become white; result is RGB (for JPG, BMP, PDF)."""
    from PIL import Image
    if has_alpha(im):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.getchannel("A"))
        return bg
    return im.convert("RGB")


def rgb_or_rgba(im):
    if im.mode in ("RGB", "RGBA"):
        return im
    return im.convert("RGBA" if has_alpha(im) else "RGB")


def convert_image(src: Path, ctx: Ctx, ext: str) -> Result:
    from PIL import Image
    im = load_image(src)
    out = ctx.out_path(src, src.stem + ext)
    if ext in (".jpg", ".bmp"):
        im = flatten(im)
    else:
        im = rgb_or_rgba(im)
    if ext == ".jpg":
        im.save(out, quality=ctx.options.jpg_quality, optimize=True, progressive=True)
    elif ext == ".webp":
        im.save(out, quality=ctx.options.jpg_quality, method=5)
    elif ext == ".ico":
        im = im.convert("RGBA")
        side = max(im.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
        square.save(out, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    else:
        im.save(out)
    return Result([out])


def images_to_pdf(files: list[Path], ctx: Ctx) -> Result:
    pages = []
    for i, f in enumerate(files):
        ctx.check()
        pages.append(flatten(load_image(f)))
        ctx.progress((i + 1) / (len(files) + 1))
    name = files[0].stem if len(files) == 1 else f"{files[0].stem} +{len(files) - 1}"
    out = ctx.out_path(files[0], name + ".pdf")
    pages[0].save(out, save_all=True, append_images=pages[1:], resolution=150)
    return Result([out])


def image_small(src: Path, ctx: Ctx) -> Result:
    """Shrink for sharing: max 1920 px, JPG quality 80."""
    from PIL import Image
    im = flatten(load_image(src))
    im.thumbnail((1920, 1920), Image.LANCZOS)
    out = ctx.out_path(src, f"{src.stem} (small).jpg")
    im.save(out, quality=80, optimize=True, progressive=True)
    return Result([out])


def strip_metadata(src: Path, ctx: Ctx) -> Result:
    from PIL import Image
    im = load_image(src)
    if im.mode == "P":
        im = im.convert("RGBA" if has_alpha(im) else "RGB")
    clean = Image.frombytes(im.mode, im.size, im.tobytes())  # pixels only: no EXIF, GPS, camera info
    ext = norm(src.suffix)
    if ext not in (".png", ".jpg", ".webp", ".bmp", ".tiff", ".gif"):
        ext = ".jpg"
    if ext in (".jpg", ".bmp"):
        clean = flatten(clean)
    out = ctx.out_path(src, f"{src.stem} (clean){ext}")
    clean.save(out, **({"quality": 95} if ext == ".jpg" else {}))
    return Result([out])


def read_qr(src: Path, ctx: Ctx) -> Result:
    import zxingcpp
    codes = zxingcpp.read_barcodes(load_image(src).convert("RGB"))
    if not codes:
        return Result(note=f"No QR code or barcode found in {src.name}")
    text = "\n".join(c.text for c in codes)
    preview = text if len(text) <= 80 else text[:77] + "..."
    return Result(clipboard=text, note=f"Copied to clipboard: {preview}")


# ----------------------------------------------------------------------------- audio / video (FFmpeg)

def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def find_tool(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    for cand in (app_dir() / "ffmpeg" / exe, app_dir() / "vendor" / "ffmpeg" / exe):
        if cand.exists():
            return str(cand)
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError("FFmpeg is missing. Reinstall Satsuma to fix audio and video conversion.")


def media_duration(src: Path) -> float:
    try:
        out = subprocess.run(
            [find_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(src)],
            capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW,
        ).stdout.strip()
        return float(out)
    except (ValueError, OSError, subprocess.SubprocessError, RuntimeError):
        return 0.0


def run_ffmpeg(src: Path, out: Path, args: list[str], ctx: Ctx):
    duration = media_duration(src)
    cmd = [find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-i", str(src), *args, "-progress", "pipe:1", "-nostats", str(out)]
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True, encoding="utf-8",
                                errors="replace", creationflags=NO_WINDOW)
        try:
            for line in proc.stdout:
                if ctx.cancelled:
                    proc.kill()
                    break
                key, _, value = line.strip().partition("=")
                if key == "out_time_us" and value.isdigit() and duration > 0:
                    ctx.progress(int(value) / 1_000_000 / duration)
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if ctx.cancelled:
            out.unlink(missing_ok=True)
            raise Cancelled()
        if proc.returncode:
            out.unlink(missing_ok=True)
            err.seek(0)
            lines = [ln for ln in err.read().strip().splitlines() if ln.strip()]
            log.error("ffmpeg failed for %s: %s", src, "\n".join(lines[-10:]))
            text = "\n".join(lines)
            if "does not contain any stream" in text or "matches no streams" in text:
                raise RuntimeError("this file has no audio track" if "-vn" in args else "nothing to convert in this file")
            raise RuntimeError(lines[-1] if lines else "FFmpeg could not convert this file")


def ffmpeg_convert(src: Path, ctx: Ctx, ext: str, args: list[str], suffix: str = "") -> Result:
    out = ctx.out_path(src, f"{src.stem}{suffix}{ext}")
    run_ffmpeg(src, out, args, ctx)
    return Result([out])


AUDIO_ARGS = {
    ".mp3": ["-vn", "-c:a", "libmp3lame", "-q:a", "2"],
    ".m4a": ["-vn", "-c:a", "aac", "-b:a", "192k"],
    ".wav": ["-vn", "-c:a", "pcm_s16le"],
    ".flac": ["-vn", "-c:a", "flac"],
    ".ogg": ["-vn", "-c:a", "libvorbis", "-q:a", "5"],
    ".opus": ["-vn", "-c:a", "libopus", "-b:a", "128k"],
    ".aiff": ["-vn", "-c:a", "pcm_s16be"],
}
_EVEN = ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"]  # H.264 needs even width/height
_H264 = ["-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p"]
VIDEO_ARGS = {
    ".mp4": _EVEN + _H264 + ["-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"],
    ".mov": _EVEN + _H264 + ["-crf", "20", "-c:a", "aac", "-b:a", "160k"],
    ".webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1", "-c:a", "libopus"],
    ".mkv": ["-map", "0", "-c", "copy"],
    ".gif": ["-an", "-vf", "fps=12,scale='min(640,iw)':-1:flags=lanczos,"
                           "split[a][b];[a]palettegen[p];[b][p]paletteuse", "-loop", "0"],
}
# Fits inside 1280x1280 (works for portrait and landscape), smaller file for sharing.
VIDEO_SMALL_ARGS = ["-vf", "scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:"
                           "force_divisible_by=2"] + _H264 + ["-crf", "28", "-c:a", "aac", "-b:a", "96k",
                                                              "-movflags", "+faststart"]

FORMAT_NAMES = {".jpg": "JPG", ".png": "PNG", ".webp": "WebP", ".ico": "icon (ICO)", ".bmp": "BMP",
                ".mp3": "MP3", ".m4a": "M4A", ".wav": "WAV", ".flac": "FLAC", ".ogg": "OGG",
                ".opus": "Opus", ".aiff": "AIFF", ".mp4": "MP4", ".mov": "MOV", ".webm": "WebM",
                ".mkv": "MKV", ".gif": "GIF"}


# ----------------------------------------------------------------------------- PDF (pypdf + pdfium)

def _open_reader(src: Path):
    from pypdf import PdfReader
    reader = PdfReader(str(src))
    if reader.is_encrypted and not reader.decrypt(""):
        raise RuntimeError("this PDF is password-protected")
    return reader


def pdf_split(src: Path, ctx: Ctx) -> Result:
    from pypdf import PdfWriter
    reader = _open_reader(src)
    n = len(reader.pages)
    outdir = ctx.out_dir(src, f"{src.stem} pages")
    width = len(str(n))
    for i, page in enumerate(reader.pages):
        ctx.check()
        w = PdfWriter()
        w.add_page(page)
        with open(outdir / f"{src.stem} p{i + 1:0{width}}.pdf", "wb") as fh:
            w.write(fh)
        ctx.progress((i + 1) / n)
    return Result([outdir])


def pdf_to_png(src: Path, ctx: Ctx) -> Result:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(src))
    try:
        n = len(pdf)
        if n == 1:
            out = ctx.out_path(src, src.stem + ".png")
            targets = [out]
        else:
            out = ctx.out_dir(src, f"{src.stem} images")
            width = len(str(n))
            targets = [out / f"{src.stem} p{i + 1:0{width}}.png" for i in range(n)]
        for i, target in enumerate(targets):
            ctx.check()
            page = pdf[i]
            page.render(scale=150 / 72).to_pil().save(target)
            page.close()
            ctx.progress((i + 1) / n)
    finally:
        pdf.close()
    return Result([out])


def pdf_to_text(src: Path, ctx: Ctx) -> Result:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(src))
    parts = []
    try:
        for i in range(len(pdf)):
            ctx.check()
            page = pdf[i]
            tp = page.get_textpage()
            parts.append(tp.get_text_bounded())
            tp.close()
            page.close()
    finally:
        pdf.close()
    out = ctx.out_path(src, src.stem + ".txt")
    out.write_text("\n\n".join(parts), encoding="utf-8")
    return Result([out])


def pdf_merge(files: list[Path], ctx: Ctx) -> Result:
    from pypdf import PdfWriter
    w = PdfWriter()
    for i, f in enumerate(files):
        ctx.check()
        w.append(_open_reader(f))
        ctx.progress((i + 1) / (len(files) + 1))
    out = ctx.out_path(files[0], f"{files[0].stem} merged.pdf")
    with open(out, "wb") as fh:
        w.write(fh)
    return Result([out])


# ----------------------------------------------------------------------------- what goes on the wheel

CATEGORY_NAMES = {"image": "Images", "audio": "Audio", "video": "Video", "pdf": "PDFs"}


def catalog(multi: bool = False) -> dict[str, list[Tool]]:
    """Every tool Satsuma has, per file type, in wheel order."""
    def fmt(kind, ext, fn, **kw):
        return Tool(f"{kind}:{ext[1:]}", ext[1:].upper(),
                    f"Convert to {FORMAT_NAMES.get(ext, ext[1:].upper())}",
                    partial(fn, ext=ext, **kw), target_ext=ext)

    image = [fmt("image", e, convert_image) for e in (".png", ".jpg", ".webp")]
    image.append(Tool("image:pdf", "PDF", "Combine into one PDF" if multi else "Convert to PDF",
                      images_to_pdf, batch=True))
    image += [fmt("image", e, convert_image) for e in (".ico", ".bmp")]
    image += [Tool("image:small", "Small", "Shrink for sharing", image_small),
              Tool("image:qr", "QR", "Read QR code", read_qr),
              Tool("image:clean", "Clean", "Remove location & camera info", strip_metadata)]

    audio = [fmt("audio", e, ffmpeg_convert, args=a) for e, a in AUDIO_ARGS.items()]

    video = [fmt("video", e, ffmpeg_convert, args=a) for e, a in VIDEO_ARGS.items()]
    video += [Tool("video:small", "Small", "Shrink for sharing",
                   partial(ffmpeg_convert, ext=".mp4", args=VIDEO_SMALL_ARGS, suffix=" (small)")),
              Tool("video:mp3", "MP3", "Save the audio as MP3",
                   partial(ffmpeg_convert, ext=".mp3", args=AUDIO_ARGS[".mp3"]))]

    pdf = [Tool("pdf:merge", "Merge", "Combine into one PDF", pdf_merge, batch=True, multi_only=True),
           Tool("pdf:split", "Split", "One PDF per page", pdf_split),
           Tool("pdf:png", "PNG", "Pages to images", pdf_to_png),
           Tool("pdf:text", "Text", "Pull out the text", pdf_to_text)]
    return {"image": image, "audio": audio, "video": video, "pdf": pdf}


def tools_for(paths: list[Path], hidden: set[str] | frozenset = frozenset()) -> tuple[list[Path], list[Tool]]:
    """Pick the files that match the first one's type and the tools that apply to them."""
    if not paths:
        return [], []
    kind = category(paths[0])
    files = [p for p in paths if category(p) == kind and p.is_file()]
    if not files or kind == "other":
        return files, []
    exts = {norm(f.suffix) for f in files}
    src_ext = exts.pop() if len(exts) == 1 else None
    multi = len(files) > 1
    tools = [t for t in catalog(multi)[kind]
             if t.key not in hidden
             and not (t.target_ext and t.target_ext == src_ext)
             and not (t.multi_only and not multi)]
    return files, tools


def friendly_error(e: Exception) -> str:
    name = type(e).__name__
    if name == "UnidentifiedImageError":
        return "not a readable image"
    if isinstance(e, PermissionError):
        return "permission denied (is the file open in another app?)"
    if isinstance(e, FileNotFoundError):
        return "file not found"
    return str(e) or name


def run_job(tool: Tool, files: list[Path], options: Options,
            progress: Callable[[float], None] | None = None,
            cancel: threading.Event | None = None) -> tuple[Result, list[str]]:
    """Run a tool over the files. Returns the combined result and a list of per-file errors."""
    progress = progress or (lambda f: None)
    cancel = cancel or threading.Event()
    total, errors = Result(), []
    items = [files] if tool.batch else list(files)
    n = len(items)
    for i, item in enumerate(items):
        if cancel.is_set():
            break
        ctx = Ctx(options, lambda f, i=i: progress((i + f) / n), cancel)
        try:
            r = tool.run(item, ctx)
            total.outputs += r.outputs
            total.clipboard = r.clipboard or total.clipboard
            total.note = r.note or total.note
        except Cancelled:
            break
        except Exception as e:  # keep going with the remaining files
            name = item.name if isinstance(item, Path) else f"{len(item)} files"
            log.exception("%s failed on %s", tool.label, name)
            errors.append(f"{name}: {friendly_error(e)}")
        progress((i + 1) / n)
    return total, errors
