"""
Satsuma's conversion tools. No GUI code here, so this module can be tested on its own.

Every tool runs as run(item, ctx): item is a Path (or a list of Paths for batch tools),
ctx gives the output location, the user's choices (ctx.params), progress and cancellation.
Each tool can also say *why* it can't be used for some files (Tool.check); the wheel then
shows that slice dimmed with the reason, or hides it.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Callable

from .core import (ALIASES, ALL_EXT, ARCHIVE_EXT, AUDIO_EXT, DOCUMENT_EXT, EXCEL_EXT, IMAGE_EXT,  # noqa: F401
                   NO_WINDOW, SLIDES_EXT, TAR_SUFFIXES, VIDEO_EXT, WORD_EXT, Cancelled, Ctx, Options, Result,
                   Tool, _cached, app_dir, bundled, category, fallback_folder, human_size, norm, os_open,
                   safe_name, unique_path, writable)

log = logging.getLogger("satsuma")


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


def save_image(im, out: Path, ext: str, ctx: Ctx):
    from PIL import Image
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


def keep_format(src: Path) -> str:
    """Format to save an edited image in: the same as the original when Pillow can write it."""
    ext = norm(src.suffix)
    return ext if ext in (".png", ".jpg", ".webp", ".bmp", ".tiff", ".gif") else ".png"


def convert_image(src: Path, ctx: Ctx, ext: str) -> Result:
    im = load_image(src)
    out = ctx.out_path(src, ctx.stem(src) + ext)
    save_image(im, out, ext, ctx)
    return Result([out])


def images_to_pdf(files: list[Path], ctx: Ctx) -> Result:
    pages = []
    for i, f in enumerate(files):
        ctx.check()
        pages.append(flatten(load_image(f)))
        ctx.progress((i + 1) / (len(files) + 1))
    if ctx.options.rename_base:
        name = safe_name(ctx.options.rename_base)
    else:
        name = files[0].stem if len(files) == 1 else f"{files[0].stem} +{len(files) - 1}"
    out = ctx.out_path(files[0], name + ".pdf")
    pages[0].save(out, save_all=True, append_images=pages[1:], resolution=150)
    return Result([out])


def image_small(src: Path, ctx: Ctx) -> Result:
    """Shrink for sharing: max 1920 px, JPG quality 80."""
    from PIL import Image
    im = flatten(load_image(src))
    im.thumbnail((1920, 1920), Image.LANCZOS)
    out = ctx.out_path(src, f"{ctx.stem(src)} (small).jpg")
    im.save(out, quality=80, optimize=True, progressive=True)
    return Result([out])


RESIZE_PRESETS = {  # key: (label, kind, value)
    "75": ("75% size", "scale", 0.75),
    "50": ("Half size (50%)", "scale", 0.5),
    "25": ("Quarter size (25%)", "scale", 0.25),
    "fhd": ("Full HD (1920 px wide or tall)", "fit", 1920),
    "hd": ("HD (1280 px)", "fit", 1280),
    "web": ("Web (800 px)", "fit", 800),
    "square": ("Square 1080 × 1080 (profile picture)", "square", 1080),
}


def resize_image(src: Path, ctx: Ctx) -> Result:
    from PIL import Image, ImageOps
    p = ctx.params
    preset = p.get("preset", "50")
    im = load_image(src)
    w, h = im.size
    if preset == "custom":
        new_w = max(1, int(p.get("width", 1000)))
        size = (new_w, max(1, round(h * new_w / w)))
        tag = f"{size[0]}x{size[1]}"
        im = im.resize(size, Image.LANCZOS)
    else:
        _, kind, value = RESIZE_PRESETS[preset]
        if kind == "scale":
            size = (max(1, round(w * value)), max(1, round(h * value)))
            im = im.resize(size, Image.LANCZOS)
            tag = f"{int(value * 100)}%"
        elif kind == "fit":
            scale = min(1.0, value / max(w, h))  # never blow small images up
            size = (max(1, round(w * scale)), max(1, round(h * scale)))
            im = im.resize(size, Image.LANCZOS)
            tag = f"{size[0]}x{size[1]}"
        else:
            im = ImageOps.fit(rgb_or_rgba(im), (value, value), Image.LANCZOS)
            tag = "square"
    ext = keep_format(src)
    out = ctx.out_path(src, f"{ctx.stem(src)} ({tag}){ext}")
    save_image(im, out, ext, ctx)
    return Result([out])


ROTATIONS = {  # key: (button text, file name tag)
    "right": ("Rotate right 90°", "rotated"),
    "left": ("Rotate left 90°", "rotated"),
    "180": ("Turn upside down (180°)", "rotated"),
    "hflip": ("Flip left ↔ right (mirror)", "flipped"),
    "vflip": ("Flip top ↕ bottom", "flipped"),
}


def rotate_image(src: Path, ctx: Ctx) -> Result:
    from PIL import Image
    how = ctx.params.get("how", "right")
    ops = {"right": Image.Transpose.ROTATE_270, "left": Image.Transpose.ROTATE_90,
           "180": Image.Transpose.ROTATE_180, "hflip": Image.Transpose.FLIP_LEFT_RIGHT,
           "vflip": Image.Transpose.FLIP_TOP_BOTTOM}
    im = load_image(src).transpose(ops[how])
    ext = keep_format(src)
    out = ctx.out_path(src, f"{ctx.stem(src)} ({ROTATIONS[how][1]}){ext}")
    save_image(im, out, ext, ctx)
    return Result([out])


def strip_metadata(src: Path, ctx: Ctx) -> Result:
    from PIL import Image
    im = load_image(src)
    if im.mode == "P":
        im = im.convert("RGBA" if has_alpha(im) else "RGB")
    clean = Image.frombytes(im.mode, im.size, im.tobytes())  # pixels only: no EXIF, GPS, camera info
    ext = keep_format(src)
    if ext in (".jpg", ".bmp"):
        clean = flatten(clean)
    out = ctx.out_path(src, f"{ctx.stem(src)} (clean){ext}")
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


# --- background removal (U²-Net "silueta" model, runs on the PC) ---

_bg_session = None
_bg_lock = threading.Lock()


def bg_model() -> Path | None:
    return bundled("models", "silueta.onnx")


def remove_background(src: Path, ctx: Ctx) -> Result:
    global _bg_session
    import numpy as np
    from PIL import Image
    model = bg_model()
    if not model:
        raise RuntimeError("the background remover isn't installed in this copy")
    with _bg_lock:
        if _bg_session is None:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.log_severity_level = 3
            _bg_session = ort.InferenceSession(str(model), opts, providers=["CPUExecutionProvider"])
        sess = _bg_session
    ctx.progress(0.1)
    im = load_image(src)
    rgb = im.convert("RGB")
    x = np.asarray(rgb.resize((320, 320), Image.LANCZOS), dtype=np.float32)
    x = x / max(float(x.max()), 1e-6)
    x = (x - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array([0.229, 0.224, 0.225], dtype=np.float32)
    x = x.transpose(2, 0, 1)[None].astype(np.float32)
    ctx.check()
    pred = sess.run(None, {sess.get_inputs()[0].name: x})[0][0, 0]
    pred = (pred - pred.min()) / max(float(pred.max() - pred.min()), 1e-6)
    ctx.progress(0.8)
    mask = Image.fromarray((pred * 255).astype(np.uint8)).resize(rgb.size, Image.LANCZOS)
    if has_alpha(im):  # keep parts that were already transparent
        from PIL import ImageChops
        mask = ImageChops.multiply(mask, im.convert("RGBA").getchannel("A"))
    cut = rgb.copy()
    cut.putalpha(mask)
    out = ctx.out_path(src, f"{ctx.stem(src)} (no background).png")
    cut.save(out)
    return Result([out])


def check_bg_model(files: list[Path]) -> str | None:
    return None if bg_model() else "Not included in this copy"


# ----------------------------------------------------------------------------- audio / video (FFmpeg)

def find_tool(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    for cand in (app_dir() / "ffmpeg" / exe, app_dir() / "vendor" / "ffmpeg" / exe):
        if cand.exists():
            return str(cand)
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError("FFmpeg is missing. Reinstall Satsuma to fix audio and video conversion.")


def probe(src: Path) -> dict:
    """Duration and which kinds of streams a media file has (cached)."""
    def work():
        info = {"duration": 0.0, "audio": False, "video": False, "width": 0, "height": 0}
        try:
            out = subprocess.run(
                [find_tool("ffprobe"), "-v", "error", "-show_entries",
                 "format=duration:stream=codec_type,width,height:stream_side_data=rotation",
                 "-of", "json", str(src)],
                capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW,
            ).stdout
            data = json.loads(out or "{}")
            info["duration"] = float(data.get("format", {}).get("duration") or 0)
            streams = data.get("streams", [])
            kinds = {s.get("codec_type") for s in streams}
            info["audio"], info["video"] = "audio" in kinds, "video" in kinds
            for s in streams:
                if s.get("codec_type") == "video" and s.get("width"):
                    w, h = int(s["width"]), int(s.get("height") or 0)
                    rot = 0
                    for sd in s.get("side_data_list", []) or []:
                        rot = int(sd.get("rotation") or 0) or rot
                    info["width"], info["height"] = (h, w) if abs(rot) in (90, 270) else (w, h)
                    break
        except (ValueError, OSError, subprocess.SubprocessError, RuntimeError):
            info["audio"] = info["video"] = True  # unknown: don't block anything
        return info
    return _cached("probe", src, work)


def media_duration(src: Path) -> float:
    return probe(src)["duration"]


def run_ffmpeg(src: Path, out: Path, args: list[str], ctx: Ctx, pre: list[str] | None = None,
               duration: float | None = None):
    duration = media_duration(src) if duration is None else duration
    cmd = [find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           *(pre or []), "-i", str(src), *args, "-progress", "pipe:1", "-nostats", str(out)]
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
        real_file = str(out) != os.devnull
        if ctx.cancelled:
            if real_file:
                out.unlink(missing_ok=True)
            raise Cancelled()
        if proc.returncode:
            if real_file:
                out.unlink(missing_ok=True)
            err.seek(0)
            lines = [ln for ln in err.read().strip().splitlines() if ln.strip()]
            log.error("ffmpeg failed for %s: %s", src, "\n".join(lines[-10:]))
            text = "\n".join(lines)
            if "does not contain any stream" in text or "matches no streams" in text:
                raise RuntimeError("this file has no audio track" if "-vn" in args else "nothing to convert in this file")
            raise RuntimeError(lines[-1] if lines else "FFmpeg could not convert this file")


def ffmpeg_convert(src: Path, ctx: Ctx, ext: str, args: list[str], suffix: str = "") -> Result:
    out = ctx.out_path(src, f"{ctx.stem(src)}{suffix}{ext}")
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
_AAC = ["-c:a", "aac", "-b:a", "160k"]
VIDEO_ARGS = {
    ".mp4": _EVEN + _H264 + ["-crf", "20"] + _AAC + ["-movflags", "+faststart"],
    ".mov": _EVEN + _H264 + ["-crf", "20"] + _AAC,
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

VIDEO_ROTATE_FILTERS = {"right": "transpose=1", "left": "transpose=2", "180": "transpose=1,transpose=1",
                        "hflip": "hflip", "vflip": "vflip"}


def rotate_video(src: Path, ctx: Ctx) -> Result:
    how = ctx.params.get("how", "right")
    vf = VIDEO_ROTATE_FILTERS[how] + ",pad=ceil(iw/2)*2:ceil(ih/2)*2"
    args = ["-vf", vf, "-metadata:s:v", "rotate=0"] + _H264 + ["-crf", "20"] + _AAC + ["-movflags", "+faststart"]
    out = ctx.out_path(src, f"{ctx.stem(src)} ({ROTATIONS[how][1]}).mp4")
    run_ffmpeg(src, out, args, ctx)
    return Result([out])


def trim_media(src: Path, ctx: Ctx) -> Result:
    start = max(0.0, float(ctx.params.get("start", 0)))
    end = float(ctx.params.get("end", 0)) or media_duration(src)
    if end <= start:
        raise RuntimeError("the end must be after the start")
    pre = ["-ss", f"{start:.3f}", "-to", f"{end:.3f}"]
    if category(src) == "audio":
        ext = norm(src.suffix)
        if ext not in AUDIO_ARGS:
            ext = ".mp3"
        args = AUDIO_ARGS[ext]
    else:
        ext = ".mp4"
        args = _EVEN + _H264 + ["-crf", "20"] + _AAC + ["-movflags", "+faststart"]
    out = ctx.out_path(src, f"{ctx.stem(src)} (trimmed){ext}")
    run_ffmpeg(src, out, args, ctx, pre=pre, duration=end - start)
    return Result([out])


def video_frame(src: Path, seconds: float, width: int = 240) -> bytes | None:
    """A small PNG of one video frame, for the trim window."""
    try:
        proc = subprocess.run(
            [find_tool("ffmpeg"), "-v", "error", "-ss", f"{max(0.0, seconds):.2f}", "-i", str(src),
             "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "image2pipe", "-c:v", "png", "pipe:1"],
            capture_output=True, timeout=15, creationflags=NO_WINDOW)
        return proc.stdout or None
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return None


def check_has_audio(files: list[Path]) -> str | None:
    return None if probe(files[0])["audio"] else "This video has no sound"


def check_gif_length(files: list[Path]) -> str | None:
    return "Too long for a GIF (over 2 minutes)" if media_duration(files[0]) > 120 else None


# ----------------------------------------------------------------------------- PDF (pypdf + pdfium)

def _open_reader(src: Path):
    from pypdf import PdfReader
    reader = PdfReader(str(src))
    if reader.is_encrypted and not reader.decrypt(""):
        raise RuntimeError("this PDF is password-protected")
    return reader


def check_pdf_locked(files: list[Path]) -> str | None:
    def work():
        try:
            from pypdf import PdfReader
            r = PdfReader(str(files[0]))
            return "This PDF is password-protected" if r.is_encrypted and not r.decrypt("") else None
        except Exception:
            return None
    return _cached("pdflock", files[0], work)


def pdf_split(src: Path, ctx: Ctx) -> Result:
    from pypdf import PdfWriter
    reader = _open_reader(src)
    n = len(reader.pages)
    stem = ctx.stem(src)
    outdir = ctx.out_dir(src, f"{stem} pages")
    width = len(str(n))
    for i, page in enumerate(reader.pages):
        ctx.check()
        w = PdfWriter()
        w.add_page(page)
        with open(outdir / f"{stem} p{i + 1:0{width}}.pdf", "wb") as fh:
            w.write(fh)
        ctx.progress((i + 1) / n)
    return Result([outdir])


def pdf_to_png(src: Path, ctx: Ctx) -> Result:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(src))
    stem = ctx.stem(src)
    try:
        n = len(pdf)
        if n == 1:
            out = ctx.out_path(src, stem + ".png")
            targets = [out]
        else:
            out = ctx.out_dir(src, f"{stem} images")
            width = len(str(n))
            targets = [out / f"{stem} p{i + 1:0{width}}.png" for i in range(n)]
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
    out = ctx.out_path(src, ctx.stem(src) + ".txt")
    out.write_text("\n\n".join(parts), encoding="utf-8")
    return Result([out])


def pdf_merge(files: list[Path], ctx: Ctx) -> Result:
    from pypdf import PdfWriter
    w = PdfWriter()
    for i, f in enumerate(files):
        ctx.check()
        w.append(_open_reader(f))
        ctx.progress((i + 1) / (len(files) + 1))
    name = safe_name(ctx.options.rename_base) or f"{files[0].stem} merged"
    out = ctx.out_path(files[0], f"{name}.pdf")
    with open(out, "wb") as fh:
        w.write(fh)
    return Result([out])


def write_compressed_pdf(src: Path, out: Path, ctx: Ctx, quality: int = 65, max_dim: int = 1800,
                         progress_span: tuple[float, float] = (0.0, 0.9)):
    """Shrinks big pictures inside the PDF and squeezes the rest. Text stays sharp."""
    from PIL import Image
    from pypdf import PdfWriter
    reader = _open_reader(src)
    writer = PdfWriter(clone_from=reader)
    n = max(1, len(writer.pages))
    a, b = progress_span
    for i, page in enumerate(writer.pages):
        ctx.check()
        try:
            for img in page.images:
                try:
                    pil = img.image
                    if pil.width * pil.height < 160_000:  # leave small images (logos, icons) alone
                        continue
                    if max(pil.size) > max_dim:
                        pil.thumbnail((max_dim, max_dim), Image.LANCZOS)
                    if pil.mode not in ("RGB", "L"):
                        pil = flatten(pil)
                    img.replace(pil, quality=quality)
                except Exception:  # unusual image types: keep as they are
                    log.debug("kept one image in %s", src, exc_info=True)
        except Exception:
            log.debug("page %s images skipped", i, exc_info=True)
        page.compress_content_streams()
        ctx.progress(a + (b - a) * (i + 1) / n)
    writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
    with open(out, "wb") as fh:
        writer.write(fh)


def pdf_compress(src: Path, ctx: Ctx) -> Result:
    out = ctx.out_path(src, f"{ctx.stem(src)} (compressed).pdf")
    write_compressed_pdf(src, out, ctx)
    before, after = src.stat().st_size, out.stat().st_size
    if after >= before * 0.97:
        out.unlink(missing_ok=True)
        return Result(note=f"{src.name} is already as small as it gets")
    return Result([out], note=f"{human_size(before)} → {human_size(after)} "
                              f"({100 - round(after * 100 / before)}% smaller)")


# ----------------------------------------------------------------------------- Word / Excel / PowerPoint

def _office_installed(prog_id: str) -> bool:
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + r"\CLSID"):
            return True
    except OSError:
        return False


def find_libreoffice() -> str | None:
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        p = Path(base) / "LibreOffice" / "program" / "soffice.exe"
        if p.exists():
            return str(p)
    return shutil.which("soffice")


def office_app_for(src: Path) -> str | None:
    ext = norm(src.suffix)
    prog = "Word.Application" if ext in WORD_EXT else "Excel.Application" if ext in EXCEL_EXT \
        else "PowerPoint.Application"
    if ext not in (".odt", ".ods", ".odp") and _cached("office", Path(prog), lambda: _office_installed(prog)):
        return prog
    return None


OFFICE_SCRIPTS = {
    "Word.Application": """
$app = New-Object -ComObject Word.Application; $app.Visible = $false; $app.DisplayAlerts = 0
try { $d = $app.Documents.Open($env:SAT_IN, $false, $true); $d.ExportAsFixedFormat($env:SAT_OUT, 17); $d.Close(0) }
finally { $app.Quit() }""",
    "Excel.Application": """
$app = New-Object -ComObject Excel.Application; $app.Visible = $false; $app.DisplayAlerts = $false
try { $b = $app.Workbooks.Open($env:SAT_IN, 0, $true); $b.ExportAsFixedFormat(0, $env:SAT_OUT); $b.Close($false) }
finally { $app.Quit() }""",
    "PowerPoint.Application": """
$app = New-Object -ComObject PowerPoint.Application
try { $p = $app.Presentations.Open($env:SAT_IN, -1, 0, 0); $p.SaveAs($env:SAT_OUT, 32); $p.Close() }
finally { $app.Quit() }""",
}


def document_to_pdf(src: Path, ctx: Ctx) -> Result:
    out = ctx.out_path(src, ctx.stem(src) + ".pdf")
    prog = office_app_for(src)
    if prog:
        env = dict(os.environ, SAT_IN=str(src.resolve()), SAT_OUT=str(out.resolve()))
        ps = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        proc = subprocess.run([str(ps), "-NoProfile", "-NonInteractive", "-Command", OFFICE_SCRIPTS[prog]],
                              capture_output=True, text=True, env=env, timeout=600, creationflags=NO_WINDOW)
        if proc.returncode or not out.exists():
            log.error("office export failed: %s", proc.stderr[-2000:])
            raise RuntimeError("Office couldn't turn this file into a PDF")
        return Result([out])
    soffice = find_libreoffice()
    if not soffice:
        raise RuntimeError("needs Microsoft Office or LibreOffice")
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run([soffice, "--headless", "--norestore", "--convert-to", "pdf", "--outdir", tmp,
                               str(src)], capture_output=True, text=True, timeout=600, creationflags=NO_WINDOW)
        made = Path(tmp) / (src.stem + ".pdf")
        if proc.returncode or not made.exists():
            log.error("libreoffice export failed: %s", proc.stderr[-2000:])
            raise RuntimeError("LibreOffice couldn't turn this file into a PDF")
        shutil.move(str(made), out)
    return Result([out])


def check_office(files: list[Path]) -> str | None:
    if office_app_for(files[0]) or _cached("lo", Path("soffice"), find_libreoffice):
        return None
    return "Needs Microsoft Office or LibreOffice"


# ----------------------------------------------------------------------------- what goes on the wheel

CATEGORY_NAMES = {"image": "Images", "audio": "Audio", "video": "Video", "pdf": "PDFs",
                  "document": "Word, Excel & PowerPoint", "archive": "Archives (ZIP, RAR, 7z…)",
                  "files": "Make archives (any files and folders)"}
CATEGORY_ICONS = {"image": "🖼️", "audio": "🎵", "video": "🎬", "pdf": "📄", "document": "📝", "archive": "🗜️",
                  "files": "📦"}


def already(ext: str) -> Callable[[list[Path]], str | None]:
    name = FORMAT_NAMES.get(ext, ext[1:].upper())

    def check(files: list[Path]) -> str | None:
        return f"Already {name}" if all(norm(f.suffix) == ext for f in files) else None
    return check


def needs_several(what: str) -> Callable[[list[Path]], str | None]:
    def check(files: list[Path]) -> str | None:
        return None if len(files) > 1 else f"Drop 2 or more {what} to combine them"
    return check


def both(*checks):
    def check(files):
        for c in checks:
            reason = c(files) if c else None
            if reason:
                return reason
        return None
    return check


def catalog(multi: bool = False) -> dict[str, list[Tool]]:
    """Every tool Satsuma has, per kind of file, in default wheel order."""
    from . import archives as ar, extras as ex

    def fmt(kind, ext, fn, check=None, **kw):
        return Tool(f"{kind}:{ext[1:]}", ext[1:].upper(),
                    f"Convert to {FORMAT_NAMES.get(ext, ext[1:].upper())}",
                    partial(fn, ext=ext, **kw), check=both(already(ext), check))

    pack = [Tool("pack:zip", "ZIP", "Put in a ZIP file", partial(ar.pack, fmt="zip"), batch=True),
            Tool("pack:7z", "7z", "Put in a 7z file (smaller than ZIP)", partial(ar.pack, fmt="7z"), batch=True,
                 check=ar.check_needs_7z),
            Tool("pack:options", "Archive…", "Archive with a password, split or max compression", ar.pack,
                 batch=True, ask="pack")]

    image = [fmt("image", e, convert_image) for e in (".png", ".jpg", ".webp")]
    image += [Tool("image:pdf", "PDF", "Combine into one PDF" if multi else "Convert to PDF", images_to_pdf, batch=True),
              Tool("image:resize", "Resize", "Make it bigger or smaller", resize_image, ask="resize"),
              Tool("image:rotate", "Rotate", "Rotate or flip", rotate_image, ask="rotate"),
              Tool("image:fit", "Fit size", "Make it fit a size limit (Discord, email…)", ex.fit, ask="fit"),
              Tool("image:cutout", "Cut out", "Remove the background", remove_background, check=check_bg_model),
              Tool("image:ocr", "Copy text", "Copy the text in the picture", ex.read_text, check=ex.check_ocr,
                   renames=False),
              Tool("image:small", "Small", "Shrink for sharing", image_small),
              Tool("image:watermark", "Watermark", "Add your name or logo", ex.watermark_image, ask="watermark"),
              Tool("image:scan", "Scan", "Clean up photos of paper into a PDF", ex.scan_to_pdf, batch=True),
              Tool("image:video", "Video", "Turn the pictures into a video slideshow", ex.images_to_video, batch=True,
                   ask="slideshow", check=ex.check_several_images)]
    image += [fmt("image", e, convert_image) for e in (".ico", ".bmp")]
    image += [Tool("image:qr", "QR", "Read QR code", read_qr, renames=False),
              Tool("image:clean", "Clean", "Remove location & camera info", strip_metadata)]

    audio = [fmt("audio", e, ffmpeg_convert, args=a) for e, a in AUDIO_ARGS.items()]
    audio += [Tool("audio:trim", "Trim", "Cut the start or end", trim_media, ask="trim"),
              Tool("audio:fit", "Fit size", "Make it fit a size limit", ex.fit, ask="fit")]

    video = [fmt("video", e, ffmpeg_convert, check=check_gif_length if e == ".gif" else None, args=a)
             for e, a in VIDEO_ARGS.items()]
    video += [Tool("video:trim", "Trim", "Cut the start or end", trim_media, ask="trim"),
              Tool("video:fit", "Fit size", "Make it fit a size limit (Discord, email…)", ex.fit, ask="fit"),
              Tool("video:rotate", "Rotate", "Rotate or flip", rotate_video, ask="rotate"),
              Tool("video:small", "Small", "Shrink for sharing",
                   partial(ffmpeg_convert, ext=".mp4", args=VIDEO_SMALL_ARGS, suffix=" (small)")),
              Tool("video:watermark", "Watermark", "Add your name or logo", ex.watermark_video, ask="watermark"),
              Tool("video:frames", "Frames", "Save pictures from the video", ex.video_frames, ask="frames"),
              Tool("video:mp3", "MP3", "Save the sound as MP3",
                   partial(ffmpeg_convert, ext=".mp3", args=AUDIO_ARGS[".mp3"]), check=check_has_audio)]

    pdf = [Tool("pdf:merge", "Merge", "Combine into one PDF", pdf_merge, batch=True, check=needs_several("PDFs")),
           Tool("pdf:compress", "Compress", "Make the file smaller", pdf_compress, check=check_pdf_locked),
           Tool("pdf:fit", "Fit size", "Make it fit a size limit (email…)", ex.fit, ask="fit", check=check_pdf_locked),
           Tool("pdf:split", "Split", "One PDF per page", pdf_split, check=check_pdf_locked),
           Tool("pdf:png", "PNG", "Pages to images", pdf_to_png, check=check_pdf_locked),
           Tool("pdf:text", "Text", "Pull out the text", pdf_to_text, check=check_pdf_locked)]

    document = [Tool("document:pdf", "PDF", "Convert to PDF", document_to_pdf, check=check_office)]

    def is_ext(*exts):
        def check(files):
            return f"Already {exts[0][1:].upper()}" if all(f.suffix.lower() in exts for f in files) else None
        return check

    archive = [Tool("archive:extract", "Extract", "Extract here", partial(ar.extract, mode="smart"),
                    check=ar.check_readable, password=True, renames=False),
               Tool("archive:folder", "To folder", "Extract into its own folder", partial(ar.extract, mode="folder"),
                    check=ar.check_readable, password=True),
               Tool("archive:browse", "Browse", "See what's inside", None, opens="browse", check=ar.check_readable,
                    password=True),
               Tool("archive:test", "Test", "Check it isn't damaged", ar.test_archive, check=ar.check_readable,
                    password=True, renames=False),
               Tool("archive:zip", "ZIP", "Turn into a ZIP", partial(ar.convert_archive, fmt="zip"),
                    check=both(ar.check_readable, is_ext(".zip")), password=True),
               Tool("archive:7z", "7z", "Turn into a 7z (smaller)", partial(ar.convert_archive, fmt="7z"),
                    check=both(ar.check_needs_7z, is_ext(".7z")), password=True)]
    return {"image": image, "audio": audio, "video": video, "pdf": pdf, "document": document,
            "archive": archive, "files": pack}


@dataclass
class Slot:
    tool: Tool
    reason: str | None = None  # why it can't be used right now (shown dimmed), or None

    @property
    def ok(self) -> bool:
        return self.reason is None


@dataclass
class Pick:
    files: list[Path]
    slots: list[Slot]
    kind: str = ""          # "image", "video", ... or "files" for mixed files / folders
    hidden: int = 0         # tools the user turned off in Settings
    dimmed: int = 0         # tools that don't fit these files


def pick(paths: list[Path], hidden=frozenset(), favorites=(), show_unavailable: bool = True,
         order: dict | None = None, pack_everywhere: bool = True) -> Pick:
    """The files dropped and the wheel's slices for them."""
    from .archives import is_extra_part
    unique: dict[str, Path] = {}
    for p in paths:
        if p.exists():
            unique.setdefault(os.path.normcase(str(p.resolve())), p)
    paths = list(unique.values())
    if not paths:
        return Pick([], [])
    kinds = {category(p) for p in paths}
    kind = kinds.pop() if len(kinds) == 1 else "files"
    if kind in ("folder", "other"):
        kind = "files"
    files = [p for p in paths if not (kind == "archive" and is_extra_part(p))] or paths
    cat = catalog(len(files) > 1)
    tools = list(cat[kind])
    if pack_everywhere and kind not in ("files", "archive"):
        tools += cat["files"]
    result = Pick(files, [], kind)
    for t in tools:
        if t.key in hidden:
            result.hidden += 1
            continue
        try:
            reason = t.check(files) if t.check else None
        except Exception:
            log.debug("check failed for %s", t.key, exc_info=True)
            reason = None
        if reason:
            result.dimmed += 1
            if not show_unavailable:
                continue
        result.slots.append(Slot(t, reason))
    fav = {k: i for i, k in enumerate(favorites)}
    custom = {k: i for i, k in enumerate((order or {}).get(kind, []))}
    default = {s.tool.key: i for i, s in enumerate(result.slots)}
    result.slots.sort(key=lambda s: (fav.get(s.tool.key, len(fav)),
                                     custom.get(s.tool.key, len(custom) + default[s.tool.key])))
    return result


def tools_for(paths: list[Path], hidden=frozenset(), favorites=(), show_unavailable: bool = True,
              order: dict | None = None, pack_everywhere: bool = True) -> tuple[list[Path], list[Slot]]:
    p = pick(paths, hidden, favorites, show_unavailable, order, pack_everywhere)
    return p.files, p.slots


PASSWORD_ERROR = "wrong or missing password"


def friendly_error(e: Exception) -> str:
    name = type(e).__name__
    if name == "NeedPassword":
        return PASSWORD_ERROR
    if name == "UnidentifiedImageError":
        return "not a readable image"
    if isinstance(e, PermissionError):
        return "permission denied (is the file open in another app?)"
    if isinstance(e, FileNotFoundError):
        return "file not found"
    if isinstance(e, subprocess.TimeoutExpired):
        return "took too long and was stopped"
    return str(e) or name


def run_job(tool: Tool, files: list[Path], options: Options,
            progress: Callable[[float], None] | None = None,
            cancel: threading.Event | None = None,
            params: dict | None = None) -> tuple[Result, list[str]]:
    """Run a tool over the files. Returns the combined result and a list of per-file errors."""
    progress = progress or (lambda f: None)
    cancel = cancel or threading.Event()
    total, errors = Result(), []
    items = [files] if tool.batch else list(files)
    n = len(items)
    for i, item in enumerate(items):
        if cancel.is_set():
            break
        ctx = Ctx(options, lambda f, i=i: progress((i + f) / n), cancel, params,
                  index=None if tool.batch else i, total=n)
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
