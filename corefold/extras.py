"""
Bigger tools: Make it fit (a target file size), Copy text (OCR), Watermark, Video → frames,
Images → video and Scan to PDF.
"""
from __future__ import annotations

import io
import logging
import math
import os
import re
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path

from .converters import (AUDIO_ARGS, Ctx, Result, category, flatten, has_alpha, is_animated, keep_format,
                         load_image, media_duration, probe, rgb_or_rgba, run_ffmpeg, save_image,
                         save_animated, write_compressed_pdf, write_pages_pdf)
from .core import human_size, safe_name
from .i18n import tr

log = logging.getLogger("corefold")
MB = 1024 * 1024


def sub_ctx(ctx: Ctx, start: float, end: float) -> Ctx:
    """Same job, but its progress fills only part of the bar."""
    return Ctx(ctx.options, lambda f: ctx.progress(start + (end - start) * f), ctx.cancel_event, ctx.params,
               ctx.index, ctx.total)


def size_tag(mb: float) -> str:
    return tr("{size} MB", size=f"{mb:g}")


# ----------------------------------------------------------------------------- make it fit

FIT_PRESETS = [(8, ""), (10, "Discord"), (25, "Email"), (50, ""), (100, "")]  # size in MB, what it's for


def fit(src: Path, ctx: Ctx) -> Result:
    mb = float(ctx.params.get("mb", 10))
    if mb <= 0:
        raise RuntimeError(tr("pick a size bigger than 0"))
    target = int(mb * MB)
    if src.stat().st_size <= target:
        return Result(note=tr("{name} is already under {limit} ({size})", name=src.name, limit=size_tag(mb),
                              size=human_size(src.stat().st_size)))
    kind = category(src)
    if kind == "video":
        return _fit_video(src, ctx, mb, target)
    if kind == "image":
        return _fit_image(src, ctx, mb, target)
    if kind == "pdf":
        return _fit_pdf(src, ctx, mb, target)
    if kind == "audio":
        return _fit_audio(src, ctx, mb, target)
    raise RuntimeError(tr("this kind of file can't be shrunk"))


def _fit_video(src: Path, ctx: Ctx, mb: float, target: int) -> Result:
    info = probe(src)
    dur = info["duration"]
    if dur <= 0:
        raise RuntimeError(tr("couldn't read the length of this video"))
    total_bps = target * 8 * 0.95 / dur
    if not info["audio"]:
        abr = 0
    elif total_bps > 2_000_000:
        abr = 128_000
    elif total_bps > 600_000:
        abr = 96_000
    else:
        abr = 64_000
    vbr = total_bps - abr
    if vbr < 90_000:
        need = math.ceil(dur * 150_000 / 8 / MB)
        raise RuntimeError(tr("too long to fit in {limit}. Try {need} MB or more, or trim it first",
                              limit=size_tag(mb), need=need))
    height = 1080 if vbr >= 2_000_000 else 720 if vbr >= 1_000_000 else 480 if vbr >= 450_000 else 360
    vf = f"scale=-2:'min(ih,{height})',pad=ceil(iw/2)*2:ceil(ih/2)*2"
    if vbr < 600_000:
        vf += ",fps=24"
    out = ctx.out_path(src, f"{ctx.stem(src)} ({size_tag(mb)}).mp4")
    with tempfile.TemporaryDirectory(prefix="corefold-", ignore_cleanup_errors=True) as tmp:
        log_base = str(Path(tmp) / "pass")
        common = ["-map", "0:V:0", "-sn", "-dn", "-vf", vf, "-c:v", "libx264", "-preset", "medium",
                  "-pix_fmt", "yuv420p"]
        run_ffmpeg(src, Path(os.devnull), common + ["-b:v", str(int(vbr)), "-pass", "1", "-passlogfile", log_base,
                                                    "-an", "-f", "null"], sub_ctx(ctx, 0, 0.45), duration=dur)
        for attempt, factor in enumerate((1.0, 0.85, 0.7)):
            audio = ["-map", "0:a:0?", "-c:a", "aac", "-b:a", str(abr)] if abr else ["-an"]
            run_ffmpeg(src, out, common + ["-b:v", str(int(vbr * factor)), "-pass", "2", "-passlogfile", log_base]
                       + audio + ["-movflags", "+faststart"], sub_ctx(ctx, 0.45 + 0.18 * attempt, 1.0), duration=dur)
            if out.stat().st_size <= target:
                break
    return _fit_result(src, out, mb, target)


def _fit_image(src: Path, ctx: Ctx, mb: float, target: int) -> Result:
    from PIL import Image
    im = load_image(src)
    alpha = has_alpha(im)
    ext, fmt = (".webp", "WEBP") if alpha else (".jpg", "JPEG")
    im = rgb_or_rgba(im) if alpha else flatten(im)
    if alpha and max(im.size) > 16383:  # WebP's size limit; shrinking is the point here anyway
        im.thumbnail((16383, 16383))

    def encode(img, q) -> bytes:
        buf = io.BytesIO()
        img.save(buf, fmt, quality=q, **({"optimize": True, "progressive": True} if fmt == "JPEG" else {"method": 4}))
        return buf.getvalue()

    best = None
    for step in range(14):
        ctx.check()
        lo, hi, found = 30, 92, None
        while lo <= hi:  # highest quality that fits
            q = (lo + hi) // 2
            data = encode(im, q)
            if len(data) <= target:
                found, lo = data, q + 1
            else:
                hi = q - 1
        if found:
            best = found
            break
        im = im.resize((max(1, int(im.width * 0.8)), max(1, int(im.height * 0.8))), Image.LANCZOS)
        ctx.progress(min(0.9, (step + 1) / 10))
    if best is None:
        raise RuntimeError(tr("couldn't get this image under {limit}", limit=size_tag(mb)))
    out = ctx.out_path(src, f"{ctx.stem(src)} ({size_tag(mb)}){ext}")
    out.write_bytes(best)
    return _fit_result(src, out, mb, target)


def _fit_pdf(src: Path, ctx: Ctx, mb: float, target: int) -> Result:
    out = ctx.out_path(src, f"{ctx.stem(src)} ({size_tag(mb)}).pdf")
    levels = [(65, 1800), (50, 1500), (38, 1200), (28, 950), (20, 750), (14, 600)]
    last = None
    for i, (q, dim) in enumerate(levels):
        span = (i / len(levels), (i + 1) / len(levels))
        write_compressed_pdf(src, out, ctx, q, dim, span)
        size = out.stat().st_size
        if size <= target or (last is not None and size >= last * 0.98):
            break  # small enough, or squeezing harder doesn't help (a PDF with little or no pictures)
        last = size
    return _fit_result(src, out, mb, target)


def _fit_audio(src: Path, ctx: Ctx, mb: float, target: int) -> Result:
    dur = media_duration(src)
    if dur <= 0:
        raise RuntimeError(tr("couldn't read the length of this file"))
    kbps = int(target * 8 * 0.97 / dur / 1000)
    if kbps < 32:
        raise RuntimeError(tr("too long to fit in {limit}. Try {need} MB or more", limit=size_tag(mb),
                              need=math.ceil(dur * 32_000 / 8 / MB * 1.05)))
    kbps = min(320, kbps)
    out = ctx.out_path(src, f"{ctx.stem(src)} ({size_tag(mb)}).mp3")
    run_ffmpeg(src, out, ["-vn", "-c:a", "libmp3lame", "-b:a", f"{kbps}k"], ctx)
    return _fit_result(src, out, mb, target)


def _fit_result(src: Path, out: Path, mb: float, target: int) -> Result:
    size = out.stat().st_size
    if size > target:
        return Result([out], note=tr("Got it down to {size}, a little over {limit}", size=human_size(size),
                                     limit=size_tag(mb)))
    return Result([out], note=tr("{before} → {after}, fits in {limit}", before=human_size(src.stat().st_size),
                                 after=human_size(size), limit=size_tag(mb)))


def check_fit(files: list[Path]) -> str | None:
    return None


# ----------------------------------------------------------------------------- copy text (Windows OCR)

@lru_cache(maxsize=1)
def ocr_available() -> bool:
    """Checks the OCR package is there WITHOUT importing it: importing WinRT on the window's thread
    could change how that thread talks to Windows and break drag and drop."""
    def work():
        if os.name != "nt":
            return False
        import sys
        if sys.getwindowsversion().major < 10:
            return False
        if getattr(sys, "frozen", False):
            return True  # the installer always includes it
        import importlib.util
        try:
            spec = importlib.util.find_spec("winrt")
        except (ImportError, ValueError):
            return False
        if not spec or not spec.submodule_search_locations:
            return False
        return any((Path(loc) / "windows" / "media" / "ocr").exists() for loc in spec.submodule_search_locations)
    return work()


def check_ocr(files: list[Path]) -> str | None:
    return None if ocr_available() else tr("Reading text needs Windows 10 or 11")


async def _windows_ocr(path: str) -> str:
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import FileAccessMode, StorageFile
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError(tr("Windows has no text-reading language installed (Settings → Time & language)"))
    file = await StorageFile.get_file_from_path_async(path)
    stream = await file.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await engine.recognize_async(bitmap)
    return "\n".join(line.text for line in result.lines)


def read_text(src: Path, ctx: Ctx) -> Result:
    from PIL import Image
    im = flatten(load_image(src))
    longest = max(im.size)
    if longest < 1200:  # small screenshots read better a bit bigger
        f = 1200 / longest
        im = im.resize((int(im.width * f), int(im.height * f)), Image.LANCZOS)
    try:
        from winrt.windows.media.ocr import OcrEngine
        limit = int(OcrEngine.max_image_dimension)
    except Exception:
        limit = 10000
    if max(im.size) > limit:
        im.thumbnail((limit, limit), Image.LANCZOS)
    ctx.progress(0.2)
    with tempfile.TemporaryDirectory(prefix="corefold-", ignore_cleanup_errors=True) as tmp:
        png = Path(tmp) / "page.png"
        im.save(png)
        import asyncio  # (only here: loading it takes a moment)
        text = asyncio.run(_windows_ocr(str(png.resolve())))
    text = text.strip()
    if not text:
        return Result(note=tr("No text found in {name}", name=src.name))
    lines = text.count("\n") + 1
    preview = re.sub(r"\s+", " ", text)
    preview = preview if len(preview) <= 70 else preview[:67] + "..."
    return Result(clipboard=text, note=tr("Copied the text ({lines} lines): {text}", lines=lines, text=preview))


# ----------------------------------------------------------------------------- watermark

FONT_CANDIDATES = [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
POSITIONS = {"br": "Bottom right", "bl": "Bottom left", "tr": "Top right", "tl": "Top left",
             "c": "Middle", "tile": "All over (tiled)"}
WM_SIZES = {"small": 0.035, "medium": 0.055, "large": 0.085}


def _font(size: int):
    from PIL import ImageFont
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size=size)


def needs_shaping(text: str) -> bool:
    """Arabic, Hebrew and Indian scripts need their letters joined/reordered, which Pillow can't do
    on Windows; Qt can."""
    import unicodedata
    return any(unicodedata.bidirectional(c) in ("R", "AL") or 0x0900 <= ord(c) < 0x1000 for c in text)


def _text_image_qt(text: str, px: int):
    """The watermark text drawn by Qt (correct Arabic joining and right-to-left order), or None."""
    try:
        from PIL import Image
        from PySide6.QtCore import QRect, Qt
        from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QImage, QPainter
        if QGuiApplication.instance() is None:
            return None
        font = QFont("Segoe UI")
        font.setPixelSize(px)
        font.setBold(True)
        fm = QFontMetrics(font)
        pad = max(2, int(px * 0.12))
        shadow = max(1, pad // 2)
        w = fm.horizontalAdvance(text) + 2 * pad + shadow
        h = fm.height() + 2 * pad + shadow
        img = QImage(w, h, QImage.Format.Format_RGBA8888)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(font)
        p.setPen(QColor(0, 0, 0, 120))
        p.drawText(QRect(pad + shadow, pad + shadow, w, h), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                   text)
        p.setPen(QColor(255, 255, 255, 255))
        p.drawText(QRect(pad, pad, w, h), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), text)
        p.end()
        data = bytes(img.constBits())[: img.bytesPerLine() * h]
        return Image.frombuffer("RGBA", (w, h), data, "raw", "RGBA", img.bytesPerLine(), 1).copy()
    except Exception:
        log.debug("Qt text drawing failed", exc_info=True)
        return None


def make_overlay(w: int, h: int, params: dict):
    """A transparent picture the size of the image/video, with the text and/or logo on it."""
    from PIL import Image, ImageDraw
    text = (params.get("text") or "").strip()
    logo_path = params.get("logo") or ""
    opacity = max(5, min(100, int(params.get("opacity", 60)))) / 100
    rel = WM_SIZES.get(params.get("size", "medium"), 0.055)
    pos = params.get("position", "br")
    short = min(w, h)
    margin = int(short * 0.03)

    # the mark itself: logo above text
    parts = []
    if logo_path and Path(logo_path).exists():
        logo = load_image(Path(logo_path)).convert("RGBA")
        lw = max(8, int(w * rel * 3.2))
        logo = logo.resize((lw, max(1, int(logo.height * lw / logo.width))), Image.LANCZOS)
        parts.append(logo)
    if text:
        px = max(10, int(short * rel))
        txt = _text_image_qt(text, px) if needs_shaping(text) else None
        if txt is None:
            font = _font(px)
            probe_img = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
            l, t, r, b = probe_img.textbbox((0, 0), text, font=font)
            pad = max(2, int(font.size * 0.12))
            tw, th = r - l + 2 * pad, b - t + 2 * pad
            txt = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
            d = ImageDraw.Draw(txt)
            shadow = max(1, pad // 2)
            d.text((pad - l + shadow, pad - t + shadow), text, font=font, fill=(0, 0, 0, 120))
            d.text((pad - l, pad - t), text, font=font, fill=(255, 255, 255, 255))
        parts.append(txt)
    if not parts:
        raise RuntimeError(tr("type some text or pick a logo for the watermark"))
    mw = max(p.width for p in parts)
    mh = sum(p.height for p in parts) + (len(parts) - 1) * margin // 2
    mark = Image.new("RGBA", (mw, mh), (0, 0, 0, 0))
    y = 0
    for p in parts:
        mark.alpha_composite(p, ((mw - p.width) // 2, y))
        y += p.height + margin // 2
    alpha = mark.getchannel("A").point(lambda v: int(v * opacity))
    mark.putalpha(alpha)

    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if pos == "tile":
        tile = mark.rotate(28, expand=True, resample=Image.BICUBIC)
        sx, sy = tile.width + int(w * 0.08), tile.height + int(h * 0.12)
        for row, yy in enumerate(range(-tile.height // 2, h, sy)):
            off = (sx // 2) if row % 2 else 0
            for xx in range(-tile.width // 2 + off, w, sx):
                canvas.paste(tile, (xx, yy), tile)
        return canvas
    x = {"bl": margin, "tl": margin, "br": w - mw - margin, "tr": w - mw - margin, "c": (w - mw) // 2}[pos]
    y = {"tl": margin, "tr": margin, "bl": h - mh - margin, "br": h - mh - margin, "c": (h - mh) // 2}[pos]
    canvas.paste(mark, (max(0, x), max(0, y)), mark)
    return canvas


def watermark_image(src: Path, ctx: Ctx) -> Result:
    from PIL import Image
    im = rgb_or_rgba(load_image(src)).convert("RGBA")
    overlay = make_overlay(im.width, im.height, ctx.params)
    ext = keep_format(src)
    out = ctx.out_path(src, f"{ctx.stem(src)} (watermarked){ext}")
    if is_animated(src):
        save_animated(src, out, lambda frame: Image.alpha_composite(frame, overlay))
        return Result([out])
    im.alpha_composite(overlay)
    save_image(im if ext in (".png", ".webp", ".tiff", ".gif") else im.convert("RGB"), out, ext, ctx)
    return Result([out])


def watermark_video(src: Path, ctx: Ctx) -> Result:
    info = probe(src)
    w, h = info["width"], info["height"]
    if not (w and h):
        raise RuntimeError(tr("couldn't read the size of this video"))
    out = ctx.out_path(src, f"{ctx.stem(src)} (watermarked).mp4")
    with tempfile.TemporaryDirectory(prefix="corefold-", ignore_cleanup_errors=True) as tmp:
        png = Path(tmp) / "mark.png"
        make_overlay(w, h, ctx.params).save(png)
        args = ["-i", str(png), "-filter_complex",
                "[0:V:0][1:v]overlay=0:0:format=auto,pad=ceil(iw/2)*2:ceil(ih/2)*2[v]", "-map", "[v]", "-map", "0:a:0?",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]
        run_ffmpeg(src, out, args, ctx)
    return Result([out])


# ----------------------------------------------------------------------------- video → frames, images → video

def video_frames(src: Path, ctx: Ctx) -> Result:
    mode = ctx.params.get("mode", "every")
    dur = media_duration(src)
    if mode == "all":
        vf = ["-map", "0:V:0"]
    elif mode == "count":
        n = max(1, int(ctx.params.get("count", 10)))
        vf = ["-map", "0:V:0", "-vf", f"fps={n / max(dur, 0.1):.6f}"]
    else:
        secs = max(0.05, float(ctx.params.get("seconds", 1)))
        vf = ["-map", "0:V:0", "-vf", f"fps=1/{secs:g}"]
    outdir = ctx.out_dir(src, f"{ctx.stem(src)} frames")
    try:
        run_ffmpeg(src, outdir / "frame %05d.png", vf, ctx)
    except BaseException:
        shutil.rmtree(outdir, ignore_errors=True)
        raise
    n = len(list(outdir.glob("*.png")))
    return Result([outdir], note=tr("Saved {count} pictures", count=n))


def natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def images_to_video(files: list[Path], ctx: Ctx) -> Result:
    from PIL import Image
    files = sorted(files, key=natural_key)
    secs = max(0.04, float(ctx.params.get("seconds", 2)))
    first = load_image(files[0])
    scale = min(1.0, 1920 / max(first.size))
    w, h = max(2, int(first.width * scale) // 2 * 2), max(2, int(first.height * scale) // 2 * 2)
    name = safe_name(ctx.options.rename_base) or f"{files[0].stem} slideshow"
    out = ctx.out_path(files[0], f"{name}.mp4")
    with tempfile.TemporaryDirectory(prefix="corefold-", ignore_cleanup_errors=True) as tmp:
        for i, f in enumerate(files):
            ctx.check()
            im = flatten(load_image(f))
            im.thumbnail((w, h), Image.LANCZOS)
            frame = Image.new("RGB", (w, h), "black")
            frame.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
            frame.save(Path(tmp) / f"{i:05d}.png")
            ctx.progress(0.4 * (i + 1) / len(files))
        pattern = Path(tmp) / "%05d.png"
        pre = ["-framerate", f"1/{secs:g}" if secs >= 1 / 30 else "30"]
        args = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
                "-movflags", "+faststart"]
        run_ffmpeg(pattern, out, args, sub_ctx(ctx, 0.4, 1.0), pre=pre, duration=secs * len(files))
    return Result([out])


def check_several_images(files: list[Path]) -> str | None:
    return None if len(files) > 1 else tr("Drop 2 or more images to make a video")


# ----------------------------------------------------------------------------- scan to PDF

def scan_to_pdf(files: list[Path], ctx: Ctx) -> Result:
    """Makes photos of paper look like scans (clean white page, dark text) and puts them in one PDF."""
    name = safe_name(ctx.options.rename_base) or f"{files[0].stem} scan"
    out = ctx.out_path(files[0], f"{name}.pdf")

    def pages():
        for i, f in enumerate(sorted(files, key=natural_key)):
            ctx.check()
            yield _scan_page(f)
            ctx.progress(0.9 * (i + 1) / len(files))
    write_pages_pdf(pages(), out, 200)
    return Result([out])


def _scan_page(f: Path):
    import numpy as np
    from PIL import Image, ImageFilter, ImageOps
    g = ImageOps.grayscale(flatten(load_image(f)))
    if max(g.size) > 2600:
        g.thumbnail((2600, 2600), Image.LANCZOS)
    # even out shadows: divide by a blurred copy of the page (its "lighting")
    light = g.filter(ImageFilter.GaussianBlur(max(8, max(g.size) // 40)))
    arr = np.asarray(g, dtype=np.float32) / np.maximum(np.asarray(light, dtype=np.float32), 1.0) * 255 * 0.95
    flat = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    flat = ImageOps.autocontrast(flat, cutoff=(2, 1))
    return flat.filter(ImageFilter.UnsharpMask(radius=1.5, percent=80, threshold=2))  # grey: smaller PDF


def audio_args(ext: str) -> list[str]:
    return AUDIO_ARGS.get(ext, AUDIO_ARGS[".mp3"])
