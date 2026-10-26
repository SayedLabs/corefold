"""
Corefold's conversion tools. No GUI code here, so this module can be tested on its own.

Every tool runs as run(item, ctx): item is a Path (or a list of Paths for batch tools),
ctx gives the output location, the user's choices (ctx.params), progress and cancellation.
Each tool can also say *why* it can't be used for some files (Tool.check); the wheel then
shows that slice dimmed with the reason, or hides it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from functools import lru_cache, partial
from pathlib import Path
from typing import Callable

from .i18n import tr
from .core import (ALIASES, ALL_EXT, ARCHIVE_EXT, AUDIO_EXT, DOCUMENT_EXT, EXCEL_EXT, IMAGE_EXT,  # noqa: F401
                   NO_WINDOW, SLIDES_EXT, job_flags, TAR_SUFFIXES, VIDEO_EXT, WORD_EXT, Cancelled, Ctx, Options, Result,
                   Tool, _cached, app_dir, bundled, is_cached, is_cloud_only, category, fallback_folder, human_size,
                   copy_dates, disk_full, find_program, lower_this_thread, norm, os_open, pass_on_zone_mark, remove_file, system_tool,
                   safe_name, short_name, unique_path, writable)
from .pictureformats import RAW_EXT

log = logging.getLogger("corefold")


# ----------------------------------------------------------------------------- images

_heif_registered = False


def setup_pillow():
    """Pillow settings for a desktop app (done once, the first time a picture is opened)."""
    global _heif_registered
    if _heif_registered:
        return
    from PIL import Image, ImageFile
    # Big panoramas and scans are fine on a desktop PC (Pillow's default refuses anything over
    # about 180 megapixels as a safety measure for web servers). Pictures over 600 megapixels are
    # still refused: one of those needs several GB of memory, and a booby-trapped tiny file can
    # claim to be that big.
    Image.MAX_IMAGE_PIXELS = 300_000_000
    # A photo that was only partly downloaded still opens (the missing part is grey), like in any
    # picture viewer, instead of failing.
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        from pi_heif import register_heif_opener  # decode-only HEIC support
        register_heif_opener()
    except ImportError:
        pass
    from .pictureformats import register  # camera RAW files and drawings (SVG)
    register()
    _heif_registered = True


def load_image(src: Path, fit: int = 0):
    """The picture, upright. With fit, a JPG may be read at a smaller size that's still at least fit pixels on its
    long side (many times faster when a phone photo is made small)."""
    from PIL import Image, ImageOps
    setup_pillow()
    with Image.open(src) as im:
        if fit and im.format == "JPEG" and max(im.size) > fit * 2:
            scale = fit / max(im.size)
            im.draft(im.mode, (max(1, int(im.width * scale)), max(1, int(im.height * scale))))
        try:
            upright = im.getexif().get(0x0112, 1) in (0, 1)  # no phone-camera rotation to apply
        except Exception:
            upright = False
        if upright and not getattr(im, "is_animated", False):
            im.load()  # no copy needed: half the memory for big photos
            out = im
        else:
            try:
                out = ImageOps.exif_transpose(im)  # applies phone-camera rotation; returns a copy (first frame)
            except Exception:  # broken photo details (EXIF) must not stop the picture itself from opening
                out = im.copy()
            out.load()
    if out.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I", "F"):
        # 16-bit and 32-bit pictures (scanners, cameras, medical): scale down to normal 8-bit greys.
        # Without this they come out almost completely white.
        info = dict(out.info)
        wide = out.convert("F")
        hi = wide.getextrema()[1] or 1
        top = 255 if hi <= 255 else 65535 if hi <= 65535 else hi
        out = wide.point(lambda v: v * (255 / top)).convert("L")
        out.info.update(info)
    return out


def _metadata(im, ext: str, final_mode: str) -> dict:
    """Colour profile and photo details (date taken, camera) to keep in the new file. Without the
    colour profile, iPhone photos (Display P3) look dull after converting.
    The profile is only kept when it still matches the picture: a print (CMYK) or greyscale
    profile on a colour picture would make the colours come out wrong."""
    keep = {}
    icc = im.info.get("icc_profile")
    if icc and ext in (".jpg", ".png", ".webp", ".tiff", ".avif") and len(icc) > 20:
        space = bytes(icc[16:20])
        fits = (space == b"RGB " and final_mode in ("RGB", "RGBA", "P")) or \
            (space == b"GRAY" and final_mode in ("L", "LA"))
        if fits:
            keep["icc_profile"] = icc
    exif = im.info.get("exif")
    if isinstance(exif, (bytes, bytearray)) and exif and len(exif) < 60_000 and ext in (".jpg", ".png", ".webp", ".avif"):
        keep["exif"] = bytes(exif)
    return keep


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
    return im if im.mode == "RGB" else im.convert("RGB")  # (convert copies even when it's RGB already: 600 MB for 200 MP)


def rgb_or_rgba(im):
    if im.mode in ("RGB", "RGBA"):
        return im
    return im.convert("RGBA" if has_alpha(im) else "RGB")


def save_image(im, out: Path, ext: str, ctx: Ctx, keep_metadata: bool = True):
    from PIL import Image
    original = im
    if ext in (".jpg", ".bmp"):
        im = flatten(im)
    else:
        im = rgb_or_rgba(im)
    meta = _metadata(original, ext, im.mode) if keep_metadata else {}
    if "icc_profile" in im.info and "icc_profile" not in meta:
        im.info = {k: v for k, v in im.info.items() if k != "icc_profile"}  # Pillow would copy it anyway
    big = im.width * im.height > BIG_PICTURE
    if ext == ".jpg":  # progressive takes three times as long on a big photo for a file only 5% smaller
        # (that pass keeps the whole picture's packed form in memory: 600 MB more for 200 MP, and many times longer, for a file about 7% smaller)
        im.save(out, quality=ctx.options.jpg_quality, optimize=im.width * im.height <= HUGE_PICTURE,
                progressive=not big, **meta)
    elif ext == ".webp":
        if max(im.size) > WEBP_MAX:
            raise RuntimeError(tr("too big for WebP (it can't be wider or taller than 16,383 pixels)"))
        im.save(out, quality=ctx.options.jpg_quality, method=4, **meta)
    elif ext == ".avif":  # about half the size of a JPG at the same quality
        im.save(out, quality=max(50, ctx.options.jpg_quality - 15), speed=6,
                **{k: v for k, v in meta.items() if k in ("exif", "icc_profile")})
    elif ext == ".png":  # a big photo packs twice as fast at level 3, for a file a little bigger
        im.save(out, compress_level=3 if big else 6, **meta)
    elif ext == ".tiff":  # (packed without losing anything: a TIFF as it comes is as big as the photo's pixels)
        try:
            im.save(out, compression="tiff_deflate", **meta)
        except OSError as e:
            # libtiff says only "encoder error -2" when it can't write: on a disk without room for the
            # picture's pixels, that's the disk being full
            if not disk_full(e) and str(e).startswith("encoder error") and \
                    shutil.disk_usage(out.parent).free < im.width * im.height * len(im.getbands()) + (1 << 20):
                raise OSError(28, "No space left on device") from e
            raise
    elif ext == ".ico":
        im = im.convert("RGBA")
        # an icon is at most 256 × 256: shrunk first, so a panorama doesn't need a square of its full width
        im.thumbnail((256, 256), Image.LANCZOS)
        side = max(im.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
        if side < 16:  # an icon smaller than 16 pixels would be saved with no picture in it at all
            square = square.resize((16, 16), Image.NEAREST)
        square.save(out, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    else:
        im.save(out)


BIG_PICTURE = 8_000_000  # pixels: a phone photo is 12 million
HUGE_PICTURE = 50_000_000  # a JPG this big is saved without the second pass that packs it a little smaller
WEBP_MAX = 16383  # the largest width or height a WebP picture can have


def avif_supported() -> bool:
    try:
        from PIL import features
        return bool(features.check("avif"))
    except Exception:
        return False


def check_avif(files: list[Path]) -> str | None:
    return None if avif_supported() else tr("This copy of Corefold can't write AVIF")


def keep_format(src: Path) -> str:
    """Format to save an edited image in: the same as the original when Pillow can write it."""
    ext = norm(src.suffix)
    if ext in RAW_EXT:  # (a camera's photo stays a photo)
        return ".jpg"
    return ext if ext in (".png", ".jpg", ".webp", ".bmp", ".tiff", ".gif") else ".png"


def convert_image(src: Path, ctx: Ctx, ext: str) -> Result:
    if is_animated(src) and ext in (".webp", ".png", ".gif"):  # (they can move: before, only the first frame was kept)
        out = ctx.out_path(src, ctx.stem(src) + ext)
        save_animated(src, out, lambda frame: frame)
        return Result([out])
    if is_multipage(src) and ext != ".ico":  # every page of a scan or fax, one file each (before, only page 1)
        return Result(_convert_pages(src, ctx, ext))
    im = load_image(src)
    out = ctx.out_path(src, ctx.stem(src) + ext)
    save_image(im, out, ext, ctx)
    return Result([out])


def _convert_pages(src: Path, ctx: Ctx, ext: str) -> list[Path]:
    from PIL import Image, ImageSequence
    setup_pillow()
    outs = []
    with Image.open(src) as im:
        n = getattr(im, "n_frames", 1)
        width = len(str(n))
        for i, page in enumerate(ImageSequence.Iterator(im)):
            ctx.check()
            out = ctx.out_path(src, f"{ctx.stem(src)} p{i + 1:0{width}}{ext}")
            save_image(page.copy(), out, ext, ctx)
            outs.append(out)
            ctx.progress((i + 1) / n)
    return outs


def write_pages_pdf(pages, out: Path, resolution: int):
    """Writes pictures as PDF pages one at a time, so 200 photos don't need 200 photos' worth of
    memory at once (each page is compressed before the next one is loaded)."""
    import io
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for im in pages:
        buf = io.BytesIO()
        im.save(buf, "PDF", resolution=resolution)
        im.close()
        buf.seek(0)
        writer.append(PdfReader(buf))
    tmp = out.with_name(out.name + ".part")
    try:
        with open(tmp, "wb") as fh:
            writer.write(fh)
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _all_pages(f: Path):
    from PIL import Image, ImageSequence
    setup_pillow()
    if norm(f.suffix) == ".tiff":
        try:
            with Image.open(f) as im:
                if getattr(im, "n_frames", 1) > 1:
                    for page in ImageSequence.Iterator(im):
                        yield flatten(page.copy())
                    return
        except OSError:
            pass
    yield flatten(load_image(f))


def _plain_jpegs(files: list[Path]) -> list[tuple[Path, int, int, int, bool]] | None:
    """(file, width, height, phone rotation, grey) when every file is an ordinary JPG photo, else None."""
    from PIL import Image
    out = []
    for f in files:
        try:
            with Image.open(f) as im:
                if im.format != "JPEG" or im.mode not in ("RGB", "L"):
                    return None
                turn = im.getexif().get(0x0112, 1) or 1
                if turn not in (1, 3, 6, 8):  # (mirrored ones go the long way)
                    return None
                out.append((f, im.width, im.height, turn, im.mode == "L"))
        except Exception:
            return None
    return out


def jpegs_to_pdf(photos: list[tuple[Path, int, int, int, bool]], out: Path, resolution: int, ctx: Ctx):
    """JPG photos go into the PDF as they are: nothing is decoded and compressed again, so it's many times
    faster and the photos keep their quality. Phone rotation is applied on the page. The PDF is written
    here directly (a PDF of pictures is a short list of parts), so each photo is one plain file copy."""
    tmp = out.with_name(out.name + ".part")
    offsets: list[int] = []
    n = len(photos)
    # parts: 1 the catalog, 2 the page list, then for each photo: its page, its drawing, the photo
    kids = " ".join(f"{3 + 3 * i} 0 R" for i in range(n))
    try:
        with open(tmp, "wb") as fh:
            def part(body: bytes, stream: bytes | None = None):
                offsets.append(fh.tell())
                fh.write(f"{len(offsets)} 0 obj\n".encode("ascii") + body)
                if stream is not None:
                    fh.write(b"\nstream\n" + stream + b"\nendstream")
                fh.write(b"\nendobj\n")
            fh.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
            part(b"<< /Type /Catalog /Pages 2 0 R >>")
            part(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode("ascii"))
            for i, (f, w, h, turn, gray) in enumerate(photos):
                ctx.check()
                data = Path(f).read_bytes()
                sw, sh = w * 72 / resolution, h * 72 / resolution
                pw, ph = (sh, sw) if turn in (6, 8) else (sw, sh)
                # where the photo goes on the page: a x b y c d (turned the way the phone held it)
                a, b, c, d, e, g = {1: (sw, 0, 0, sh, 0, 0), 3: (-sw, 0, 0, -sh, sw, sh),
                                    6: (0, -sw, sh, 0, 0, sw), 8: (0, sw, -sh, 0, sh, 0)}[turn]
                draw = f"q {a:.4f} {b:.4f} {c:.4f} {d:.4f} {e:.4f} {g:.4f} cm /Im0 Do Q".encode("ascii")
                me = 3 + 3 * i
                part(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.4f} {ph:.4f}] "
                     f"/Resources << /XObject << /Im0 {me + 2} 0 R >> >> /Contents {me + 1} 0 R >>".encode("ascii"))
                part(f"<< /Length {len(draw)} >>".encode("ascii"), draw)
                part(f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /BitsPerComponent 8 "
                     f"/ColorSpace /{'DeviceGray' if gray else 'DeviceRGB'} /Filter /DCTDecode "
                     f"/Length {len(data)} >>".encode("ascii"), data)
                ctx.progress((i + 1) / (n + 1))
            xref = fh.tell()
            fh.write(f"xref\n0 {len(offsets) + 1}\n0000000000 65535 f \n".encode("ascii"))
            fh.write("".join(f"{o:010d} 00000 n \n" for o in offsets).encode("ascii"))
            fh.write(f"trailer\n<< /Size {len(offsets) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
                     .encode("ascii"))
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def images_to_pdf(files: list[Path], ctx: Ctx) -> Result:
    if ctx.options.rename_base:
        name = safe_name(ctx.options.rename_base)
    else:
        name = files[0].stem if len(files) == 1 else f"{files[0].stem} +{len(files) - 1}"
    out = ctx.out_path(files[0], name + ".pdf")

    photos = _plain_jpegs(files)
    if photos:
        import time
        started = time.monotonic()
        jpegs_to_pdf(photos, out, 150, ctx)
        log.info("%d JPG photos put in a PDF as they are (%.1f s)", len(photos), time.monotonic() - started)
        return Result([out])

    def pages():
        for i, f in enumerate(files):
            ctx.check()
            yield from _all_pages(f)  # a multi-page TIFF (scans, faxes) gives all its pages
            ctx.progress((i + 1) / (len(files) + 1))
    write_pages_pdf(pages(), out, 150)
    return Result([out])


def image_small(src: Path, ctx: Ctx) -> Result:
    """Shrink for sharing: max 1920 px, JPG quality 80."""
    from PIL import Image
    loaded = load_image(src, fit=1920)
    im = flatten(loaded)
    im.thumbnail((1920, 1920), Image.LANCZOS)
    out = ctx.out_path(src, f"{ctx.stem(src)} (small).jpg")
    im.save(out, quality=80, optimize=True, progressive=True, **colour_profile(loaded, "RGB"))
    return Result([out])


def colour_profile(im, final_mode: str) -> dict:
    """Only the colour profile (no photo details), when it fits: without it iPhone photos look dull."""
    icc = _metadata(im, ".png", final_mode).get("icc_profile")
    return {"icc_profile": icc} if icc else {}


RESIZE_PRESETS = {  # key: (label, kind, value)
    "75": ("75% size", "scale", 0.75),
    "50": ("Half size (50%)", "scale", 0.5),
    "25": ("Quarter size (25%)", "scale", 0.25),
    "fhd": ("Full HD (1920 px wide or tall)", "fit", 1920),
    "hd": ("HD (1280 px)", "fit", 1280),
    "web": ("Web (800 px)", "fit", 800),
    "square": ("Square 1080 × 1080 (profile picture)", "square", 1080),
}


def is_animated(src: Path) -> bool:
    """An animated GIF or WebP (more than one frame)."""
    if norm(src.suffix) not in (".gif", ".webp"):
        return False

    def work():
        try:
            from PIL import Image
            setup_pillow()
            with Image.open(src) as im:
                return bool(getattr(im, "is_animated", False))
        except Exception:
            return False
    return _cached("animated", src, work)


def save_animated(src: Path, out: Path, change):
    """Applies change(frame) to every frame of an animated GIF/WebP and saves it still animated
    (before, resizing or rotating one turned it into a still picture)."""
    from PIL import Image, ImageSequence
    setup_pillow()
    frames, durations = [], []
    with Image.open(src) as im:
        loop = im.info.get("loop", 0)
        for frame in ImageSequence.Iterator(im):
            durations.append(frame.info.get("duration") or im.info.get("duration") or 100)
            frames.append(change(frame.convert("RGBA")))
    if out.suffix.lower() == ".gif":
        frames[0].save(out, "GIF", save_all=True, append_images=frames[1:], duration=durations, loop=loop,
                       disposal=2, optimize=False)
    elif out.suffix.lower() == ".png":  # (an animated PNG: browsers and chat apps play it)
        frames[0].save(out, "PNG", save_all=True, append_images=frames[1:], duration=durations, loop=loop,
                       disposal=1)
    else:
        frames[0].save(out, "WEBP", save_all=True, append_images=frames[1:], duration=durations, loop=loop,
                       quality=90)
    for f in frames:
        f.close()


def is_multipage(src: Path) -> bool:
    """A TIFF with more than one page (scans, faxes)."""
    if norm(src.suffix) != ".tiff":
        return False

    def work():
        try:
            from PIL import Image
            setup_pillow()
            with Image.open(src) as im:
                return getattr(im, "n_frames", 1) > 1
        except Exception:
            return False
    return _cached("pages", src, work)


def save_pages(src: Path, out: Path, change):
    """Applies change(page) to every page of a multi-page TIFF and saves them all (before, only the first
    page was kept). Pages keep their own colours; black-and-white scans stay small (Group 4)."""
    from PIL import Image, ImageSequence
    setup_pillow()
    pages = []
    with Image.open(src) as im:
        for frame in ImageSequence.Iterator(im):
            pages.append(change(frame.copy()))
    squeeze = "group4" if all(pg.mode == "1" for pg in pages) else "tiff_deflate"
    pages[0].save(out, "TIFF", save_all=True, append_images=pages[1:], compression=squeeze)
    for pg in pages:
        pg.close()


def resize_image(src: Path, ctx: Ctx) -> Result:
    from PIL import Image, ImageOps
    p = ctx.params
    preset = p.get("preset", "50")
    im = load_image(src)
    square = 0
    kind, value = "custom", 0

    def size_for(w: int, h: int) -> tuple[int, int]:
        if kind == "custom":
            new_w = max(1, int(p.get("width", 1000)))
            return new_w, max(1, round(h * new_w / w))
        if kind == "scale":
            return max(1, round(w * value)), max(1, round(h * value))
        scale = min(1.0, value / max(w, h))  # "fit": never blow small images up
        return max(1, round(w * scale)), max(1, round(h * scale))
    if preset != "custom":
        _, kind, value = RESIZE_PRESETS[preset]
    if kind == "square":
        square, tag = value, "square"
    elif kind == "scale":
        tag = f"{int(value * 100)}%"
    else:
        tag = "%dx%d" % size_for(*im.size)

    def change(frame):
        if square:
            return ImageOps.fit(rgb_or_rgba(frame), (square, square), Image.LANCZOS)
        return frame.resize(size_for(*frame.size), Image.LANCZOS)
    ext = keep_format(src)
    out = ctx.out_path(src, f"{ctx.stem(src)} ({tag}){ext}")
    if is_animated(src):
        save_animated(src, out, change)
    elif is_multipage(src):
        save_pages(src, out, change)
    else:
        save_image(change(im), out, ext, ctx)
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
    ext = keep_format(src)
    out = ctx.out_path(src, f"{ctx.stem(src)} ({ROTATIONS[how][1]}){ext}")
    if is_animated(src):
        save_animated(src, out, lambda frame: frame.transpose(ops[how]))
    elif is_multipage(src):
        save_pages(src, out, lambda page: page.transpose(ops[how]))
    else:
        save_image(load_image(src).transpose(ops[how]), out, ext, ctx)
    return Result([out])


def strip_metadata(src: Path, ctx: Ctx) -> Result:
    from PIL import Image
    ext = keep_format(src)
    if is_animated(src) or is_multipage(src):  # every frame or page is kept (before, only the first one)
        out = ctx.out_path(src, f"{ctx.stem(src)} (clean){ext}")
        pixels_only = lambda frame: Image.frombytes(frame.mode, frame.size, frame.tobytes())  # noqa: E731
        if is_animated(src):
            save_animated(src, out, pixels_only)
        else:
            save_pages(src, out, lambda page: pixels_only(page if page.mode != "P" else page.convert("RGBA")))
        return Result([out])
    im = load_image(src)
    if im.mode == "P":
        im = im.convert("RGBA" if has_alpha(im) else "RGB")
    clean = Image.frombytes(im.mode, im.size, im.tobytes())  # pixels only: no EXIF, GPS, camera info
    if ext in (".jpg", ".bmp"):
        clean = flatten(clean)
    # (the colour profile says nothing about the person, and without it colours change)
    keep = colour_profile(im, clean.mode) if ext in (".jpg", ".png", ".webp", ".tiff") else {}
    out = ctx.out_path(src, f"{ctx.stem(src)} (clean){ext}")
    clean.save(out, **({"quality": 95} if ext == ".jpg" else {}), **keep)
    return Result([out])


def read_qr(src: Path, ctx: Ctx) -> Result:
    import zxingcpp
    codes = zxingcpp.read_barcodes(load_image(src).convert("RGB"))
    if not codes:
        return Result(note=tr("No QR code or barcode found in {name}", name=src.name))
    text = "\n".join(c.text for c in codes)
    preview = text if len(text) <= 80 else text[:77] + "..."
    return Result(clipboard=text, note=tr("Copied to clipboard: {text}", text=preview))


# --- background removal (U²-Net "silueta" model, runs on the PC) ---
# The model runs in a small helper process (bgworker.py) that Corefold closes when it tidies up,
# so its few hundred MB go back to Windows instead of staying while Corefold waits in the tray.

_bg_session = None   # only used if the helper can't start (and when testing off Windows)
_bg_worker = None    # (process, connection) while the helper is running
_bg_lock = threading.Lock()


def bg_model() -> Path | None:
    return bundled("models", "silueta.onnx")


def _start_bg_worker(model: Path):
    import secrets
    from multiprocessing.connection import Client
    from .settings import app_command
    from .core import job_flags
    key = secrets.token_bytes(32)
    address = "\\\\.\\pipe\\corefold-bg-" + secrets.token_hex(12)
    env = dict(os.environ, COREFOLD_WORKER_KEY=key.hex())
    proc = subprocess.Popen(app_command("--bg-worker", address, f'"{model}"'), env=env, creationflags=job_flags(),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 60
    while True:
        if proc.poll() is not None:
            raise RuntimeError(f"helper ended ({proc.returncode})")
        try:
            conn = Client(address, "AF_PIPE", authkey=key)
            log.info("background remover helper started")
            return proc, conn
        except (FileNotFoundError, OSError):
            if time.monotonic() > deadline:
                proc.kill()
                raise RuntimeError("helper didn't answer") from None
            time.sleep(0.1)


def _stop_bg_worker():
    """Closes the helper (it ends by itself when the pipe closes; forced if it doesn't)."""
    global _bg_worker
    worker, _bg_worker = _bg_worker, None
    if worker is None:
        return
    proc, conn = worker
    try:
        conn.close()
    except OSError:
        pass

    def finish():
        try:
            proc.wait(5)
        except Exception:
            proc.kill()
    threading.Thread(target=finish, daemon=True).start()


def _bg_mask(small, model: Path):
    """The 320×320 mask for a 320×320 RGB picture."""
    global _bg_session, _bg_worker
    from PIL import Image
    from . import bgworker
    data = small.tobytes()
    with _bg_lock:
        if os.name == "nt" and _bg_session is None:
            try:
                if _bg_worker is None or _bg_worker[0].poll() is not None:
                    _stop_bg_worker()
                    _bg_worker = _start_bg_worker(model)
                conn = _bg_worker[1]
                conn.send_bytes(data)
                out = conn.recv_bytes(maxlength=bgworker.SIDE ** 2)
                return Image.frombytes("L", (bgworker.SIDE, bgworker.SIDE), out)
            except Exception:
                log.warning("background remover helper failed; running it here instead", exc_info=True)
                _stop_bg_worker()
        if _bg_session is None:
            _bg_session = bgworker.open_model(str(model))
        out = bgworker.mask_from_rgb(_bg_session, data)
        return Image.frombytes("L", (bgworker.SIDE, bgworker.SIDE), out)


def remove_background(src: Path, ctx: Ctx) -> Result:
    from PIL import Image
    from .bgworker import SIDE
    model = bg_model()
    if not model:
        raise RuntimeError(tr("the background remover isn't installed in this copy"))
    ctx.progress(0.1)
    im = load_image(src)
    rgb = im.convert("RGB")
    ctx.check()
    small = rgb.resize((SIDE, SIDE), Image.LANCZOS)
    mask = _bg_mask(small, model)
    ctx.progress(0.8)
    ctx.check()
    mask = mask.resize(rgb.size, Image.LANCZOS)
    if has_alpha(im):  # keep parts that were already transparent
        from PIL import ImageChops
        mask = ImageChops.multiply(mask, im.convert("RGBA").getchannel("A"))
    cut = rgb.copy()
    cut.putalpha(mask)
    out = ctx.out_path(src, f"{ctx.stem(src)} (no background).png")
    cut.save(out)
    return Result([out])


def release_memory():
    """Frees what Corefold keeps around to be quick (the background remover alone is ~200 MB)."""
    global _bg_session
    with _bg_lock:
        _bg_session = None
        _stop_bg_worker()
    from .core import clear_cache
    clear_cache()
    import gc
    gc.collect()


def check_still(files: list[Path]) -> str | None:
    """Fit size makes still pictures; it would quietly drop the animation of a GIF."""
    return tr("Animated pictures can't be made smaller yet") if any(is_animated(f) for f in files[:50]) else None


def check_bg_model(files: list[Path]) -> str | None:
    return None if bg_model() else tr("Not included in this copy")


# ----------------------------------------------------------------------------- audio / video (FFmpeg)

def find_tool(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    for cand in (app_dir() / "ffmpeg" / exe, app_dir() / "vendor" / "ffmpeg" / exe):
        if cand.exists():
            return str(cand)
    found = find_program(name)
    if found:
        return found
    raise RuntimeError(tr("FFmpeg is missing. Reinstall Corefold to fix audio and video conversion."))


def probe(src: Path) -> dict:
    """Duration and which kinds of streams a media file has (cached)."""
    def work():
        info = {"duration": 0.0, "audio": False, "video": False, "width": 0, "height": 0, "rotation": 0,
                "hdr": False}
        try:
            out = subprocess.run(
                [find_tool("ffprobe"), "-v", "error", "-show_entries",
                 "format=duration:stream=codec_type,width,height,color_transfer:stream_disposition=attached_pic"
                 ":stream_side_data=rotation",
                 "-of", "json", *LOCAL_ONLY, str(src)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
            ).stdout
            data = json.loads(out or "{}")
            info["duration"] = float(data.get("format", {}).get("duration") or 0)
            streams = data.get("streams", [])
            kinds = {s.get("codec_type") for s in streams}
            info["audio"], info["video"] = "audio" in kinds, "video" in kinds
            for s in streams:
                if (s.get("disposition") or {}).get("attached_pic"):
                    continue  # an album cover inside the file, not the video itself
                if s.get("codec_type") == "video" and s.get("width"):
                    w, h = int(s["width"]), int(s.get("height") or 0)
                    rot = 0
                    for sd in s.get("side_data_list", []) or []:
                        rot = int(sd.get("rotation") or 0) or rot
                    info["width"], info["height"] = (h, w) if abs(rot) in (90, 270) else (w, h)
                    info["rotation"] = rot
                    info["hdr"] = s.get("color_transfer") in HDR_TRANSFERS
                    break
        except (ValueError, OSError, subprocess.SubprocessError, RuntimeError):
            info["audio"] = info["video"] = True  # unknown: don't block anything
        return info
    return _cached("probe", src, work)


HDR_TRANSFERS = ("smpte2084", "arib-std-b67")  # HDR10 / Dolby Vision, and HLG (iPhones film in HLG)
# Turns HDR video into normal (SDR) colours. Without it, HDR phone videos come out grey and washed out.
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def with_tonemap(src: Path, args: list[str]) -> list[str]:
    """The same FFmpeg arguments, with HDR → normal colours added when the video is HDR and the
    picture is being re-encoded (not for sound only, and not when the video is just copied)."""
    if not src.exists() or "-vn" in args or ("-c" in args and "copy" in args) or "-c:v" in args and \
            args[args.index("-c:v") + 1] == "copy":
        return args
    try:
        if not probe(src).get("hdr"):
            return args
    except Exception:
        return args
    args = list(args)
    if "-filter_complex" in args:
        i = args.index("-filter_complex") + 1
        args[i] = args[i].replace("[0:V:0]", f"[0:V:0]{TONEMAP}[sdr0];[sdr0]", 1)
    elif "-vf" in args:
        i = args.index("-vf") + 1
        args[i] = TONEMAP + "," + args[i]
    else:
        args += ["-vf", TONEMAP]
    return args


def media_duration(src: Path) -> float:
    return probe(src)["duration"]


# A file only ever reads local files: a video that is really a playlist can't make FFmpeg fetch
# things from the internet (or tell a server that it was opened).
LOCAL_ONLY = ["-protocol_whitelist", "file,pipe"]


# ----------------------------------------------------------------------------- the graphics card

GPU_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf")  # NVIDIA, Intel, AMD
_gpu: dict = {}
_gpu_lock = threading.Lock()


def gpu_encoder() -> str:
    """The graphics card's H.264 encoder that really works on this PC (tried once with a tiny video), or ""."""
    with _gpu_lock:
        if "encoder" in _gpu:
            return _gpu["encoder"]
        found = ""
        try:
            exe = find_tool("ffmpeg")
            listed = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20,
                                    stdin=subprocess.DEVNULL, creationflags=NO_WINDOW).stdout
            for enc in GPU_ENCODERS:
                if f" {enc} " not in listed:
                    continue
                r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                    "color=c=black:s=256x256:d=0.2", "-frames:v", "3", *_gpu_video_args([], enc, 23),
                                    "-f", "null", "-"], capture_output=True, timeout=30, stdin=subprocess.DEVNULL,
                                   creationflags=NO_WINDOW)
                if r.returncode == 0:
                    found = enc
                    break
        except (OSError, RuntimeError, subprocess.SubprocessError):
            found = ""
        log.info("graphics card video encoder: %s", found or "none")
        _gpu["encoder"] = found
        return found


def _gpu_video_args(rest: list[str], enc: str, crf: int | None, bitrate: str = "") -> list[str]:
    """The encoder's own words for "this quality" (FFmpeg's x264 CRF, roughly matched)."""
    if enc == "h264_nvenc":
        q = ["-rc", "vbr", "-b:v", bitrate] if bitrate else ["-rc", "vbr", "-cq", str((crf or 20) + 2), "-b:v", "0"]
        return ["-c:v", enc, "-preset", "p5", *q, "-pix_fmt", "yuv420p", *rest]
    if enc == "h264_qsv":
        q = ["-b:v", bitrate] if bitrate else ["-global_quality", str((crf or 20) + 2)]
        return ["-c:v", enc, "-preset", "medium", *q, "-pix_fmt", "nv12", *rest]
    q = ["-b:v", bitrate] if bitrate else ["-rc", "cqp", "-qp_i", str(crf or 20), "-qp_p", str((crf or 20) + 2)]
    return ["-c:v", enc, "-quality", "balanced", *q, "-pix_fmt", "nv12", *rest]


def with_gpu(args: list[str], enc: str) -> list[str] | None:
    """The same FFmpeg arguments with the graphics card making the H.264 video, or None when they can't
    be swapped (no x264 in them, two passes, or settings only x264 has)."""
    if not enc or "libx264" not in args or "-pass" in args or "-x264-params" in args or "-tune" in args:
        return None
    out, crf, bitrate, i = [], None, "", 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if a == "-c:v" and nxt == "libx264":
            i += 2
            continue
        if a == "-crf":
            crf = int(float(nxt)) if nxt.replace(".", "", 1).isdigit() else 20
            i += 2
            continue
        if a in ("-preset", "-pix_fmt", "-profile:v", "-level"):
            i += 2
            continue
        if a == "-b:v":
            bitrate = nxt
            i += 2
            continue
        out.append(a)
        i += 1
    return _gpu_video_args(out, enc, crf, bitrate)


def run_ffmpeg(src: Path, out: Path, args: list[str], ctx: Ctx, pre: list[str] | None = None,
               duration: float | None = None, more_inputs: list[Path] | None = None, cwd: Path | None = None):
    """FFmpeg from src to out, with progress and Stop. more_inputs come after src (-i 1, 2 …).
    With the graphics card option on, H.264 videos are made by the graphics card; if that fails, the
    usual way follows by itself."""
    fast = with_gpu(args, gpu_encoder()) if getattr(ctx.options, "gpu", False) and "libx264" in args else None
    if fast is not None:
        try:
            return _run_ffmpeg(src, out, fast, ctx, pre, duration, more_inputs, cwd)
        except Cancelled:
            raise
        except RuntimeError:
            log.warning("the graphics card couldn't make %s; trying again the usual way", out.name)
    return _run_ffmpeg(src, out, args, ctx, pre, duration, more_inputs, cwd)


def _run_ffmpeg(src: Path, out: Path, args: list[str], ctx: Ctx, pre: list[str] | None = None,
                duration: float | None = None, more_inputs: list[Path] | None = None, cwd: Path | None = None):
    duration = media_duration(src) if duration is None else duration
    args = with_tonemap(src, args) if not more_inputs else args
    extra = [x for p in (more_inputs or []) for x in (*LOCAL_ONLY, "-i", str(p))]
    cmd = [find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           *(pre or []), *LOCAL_ONLY, "-i", str(src), *extra, *args, "-progress", "pipe:1", "-nostats", str(out)]
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL, text=True,
                                encoding="utf-8", errors="replace", creationflags=job_flags(),
                                cwd=str(cwd) if cwd else None)
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
                remove_file(out)
            raise Cancelled()
        if proc.returncode:
            if real_file:
                remove_file(out)
            err.seek(0)
            lines = [ln for ln in err.read().strip().splitlines() if ln.strip()]
            log.error("ffmpeg failed for %s: %s", src, "\n".join(lines[-10:]))
            text = "\n".join(lines)
            damaged = tr("the file is damaged or isn't really an audio or video file")
            if src.exists() and media_duration(src) <= 0:
                raise RuntimeError(damaged)
            if "does not contain any stream" in text or "matches no streams" in text:
                raise RuntimeError(tr("this file has no audio track") if "-vn" in args
                                   else tr("nothing to convert in this file"))
            if any(s in text for s in DAMAGED_MEDIA):
                raise RuntimeError(damaged)
            if any(s in text for s in DISK_FULL):  # (FFmpeg's English "Error closing file: No space left on device")
                raise RuntimeError(tr("the disk is full"))
            # FFmpeg's own wording, without its technical "[mp4 @ 0x1234]" prefix
            last = re.sub(r"^(\[[^\]]*\]\s*)+", "", lines[-1]).strip() if lines else ""
            raise RuntimeError(last or tr("FFmpeg could not convert this file"))


DISK_FULL = ("No space left on device", "There is not enough space on the disk")
DAMAGED_MEDIA = ("Invalid data found when processing input", "Error opening input", "End of file",
                 "moov atom not found", "Nothing was written into output file", "could not find codec parameters")


def ffmpeg_convert(src: Path, ctx: Ctx, ext: str, args: list[str], suffix: str = "") -> Result:
    out = ctx.out_path(src, f"{ctx.stem(src)}{suffix}{ext}")
    if ext == ".mkv" and probe(src).get("rotation"):
        # MKV can't store "this video is turned": a phone video filmed upright would play sideways
        # if just copied, so it's re-encoded the right way up instead.
        args = _EVEN + _H264 + ["-crf", "20"] + _AAC
    if ext in (".mp4", ".mov", ".webm", ".mkv") and not suffix:
        args = tuned_video(src, ext, args, ctx.options)
    quality = getattr(ctx.options, "audio_quality", "balanced")
    if ext in AUDIO_ARGS and args == AUDIO_ARGS[ext] and quality != "balanced":  # (Settings → Converter)
        from .presets import audio_args
        args = audio_args(ext, quality)
    run_ffmpeg(src, out, args, ctx)
    return Result([out])


def smaller_size(width: int, height: int, most: int) -> tuple[int, int] | None:
    """The new width and height (even numbers, the same shape) when the video's shorter side is over
    `most`, else None: a video is made smaller, never bigger."""
    short = min(width, height)
    if not most or not width or not height or short <= most:
        return None
    f = most / short
    return max(2, round(width * f / 2) * 2), max(2, round(height * f / 2) * 2)


_WEBM_CRF = {18: 28, 20: 32, 24: 36, 28: 40}  # (VP9's scale for the same four qualities)


def tuned_video(src: Path, ext: str, args: list[str], options) -> list[str]:
    """The Converter's video settings (Settings → Converter): its quality, and a size it's made smaller to."""
    crf = int(getattr(options, "video_crf", 20) or 20)
    most = int(getattr(options, "video_max", 0) or 0)
    new = None
    if most:
        info = probe(src)
        new = smaller_size(info.get("width") or 0, info.get("height") or 0, most)
    args = list(args)
    if ext == ".mkv" and "copy" in args:
        if new is None:
            return args  # (copied as it is: nothing to make smaller)
        args = _PICK + _H264 + ["-crf", str(crf)] + _AAC  # (too big: made again, smaller)
    if "-crf" in args:
        i = args.index("-crf") + 1
        args[i] = str(_WEBM_CRF.get(crf, 32) if ext == ".webm" else crf)
    if new is not None:
        scale = f"scale={new[0]}:{new[1]}:flags=lanczos"
        if "-vf" in args:
            args[args.index("-vf") + 1] = scale  # (instead of the padding to even sizes: these are even)
        else:
            at = args.index("-sn") if "-sn" in args else len(args)
            args[at:at] = ["-vf", scale]
    return args


AUDIO_ARGS = {
    ".mp3": ["-vn", "-c:a", "libmp3lame", "-q:a", "2"],
    ".m4a": ["-vn", "-c:a", "aac", "-b:a", "192k"],
    ".wav": ["-vn", "-c:a", "pcm_s16le"],
    ".flac": ["-vn", "-c:a", "flac"],
    ".ogg": ["-vn", "-c:a", "libvorbis", "-q:a", "5"],
    ".opus": ["-vn", "-c:a", "libopus", "-b:a", "128k"],
    ".aiff": ["-vn", "-c:a", "pcm_s16be"],
}
# Which parts of the file to use: the main picture and the first sound track. Without this, FFmpeg
# can pick an album-cover picture inside the file as "the video" (the result is a still image),
# and films with picture subtitles fail to convert to MP4.
_PICK = ["-map", "0:V:0", "-map", "0:a:0?", "-sn", "-dn"]
_EVEN = _PICK + ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"]  # H.264 needs even width/height
_H264 = ["-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p"]
_AAC = ["-c:a", "aac", "-b:a", "160k"]
VIDEO_ARGS = {
    ".mp4": _EVEN + _H264 + ["-crf", "20"] + _AAC + ["-movflags", "+faststart"],
    ".mov": _EVEN + _H264 + ["-crf", "20"] + _AAC,
    # "-deadline good -cpu-used 4": several times faster than VP9's default, which can take ages
    ".webm": _PICK + ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1", "-deadline", "good",
              "-cpu-used", "4", "-pix_fmt", "yuv420p", "-c:a", "libopus"],
    # copy the picture and sound as they are (fast, no quality loss). Phone videos carry extra
    # data tracks that MKV can't hold, so only video and audio are taken.
    ".mkv": ["-map", "0:v", "-map", "0:a?", "-c", "copy", "-sn", "-dn"],
    ".gif": ["-map", "0:V:0", "-an", "-sn", "-dn", "-vf", "fps=12,scale='min(640,iw)':-1:flags=lanczos,"
                           "split[a][b];[a]palettegen[p];[b][p]paletteuse", "-loop", "0"],
}
# Fits inside 1280x1280 (works for portrait and landscape), smaller file for sharing.
VIDEO_SMALL_ARGS = _PICK + ["-vf", "scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:"
                           "force_divisible_by=2"] + _H264 + ["-crf", "28", "-c:a", "aac", "-b:a", "96k",
                                                              "-movflags", "+faststart"]

FORMAT_NAMES = {".jpg": "JPG", ".png": "PNG", ".webp": "WebP", ".ico": "icon (ICO)", ".bmp": "BMP", ".avif": "AVIF",
                ".mp3": "MP3", ".m4a": "M4A", ".wav": "WAV", ".flac": "FLAC", ".ogg": "OGG",
                ".opus": "Opus", ".aiff": "AIFF", ".mp4": "MP4", ".mov": "MOV", ".webm": "WebM",
                ".mkv": "MKV", ".gif": "GIF"}

VIDEO_ROTATE_FILTERS = {"right": "transpose=1", "left": "transpose=2", "180": "transpose=1,transpose=1",
                        "hflip": "hflip", "vflip": "vflip"}


def rotate_video(src: Path, ctx: Ctx) -> Result:
    how = ctx.params.get("how", "right")
    vf = VIDEO_ROTATE_FILTERS[how] + ",pad=ceil(iw/2)*2:ceil(ih/2)*2"
    args = _PICK + ["-vf", vf, "-metadata:s:v", "rotate=0"] + _H264 + ["-crf", "20"] + _AAC + ["-movflags", "+faststart"]
    out = ctx.out_path(src, f"{ctx.stem(src)} ({ROTATIONS[how][1]}).mp4")
    run_ffmpeg(src, out, args, ctx)
    return Result([out])


def trim_media(src: Path, ctx: Ctx) -> Result:
    start = max(0.0, float(ctx.params.get("start", 0)))
    end = float(ctx.params.get("end", 0)) or media_duration(src)
    if end <= start:
        raise RuntimeError(tr("the end must be after the start"))
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
        args = with_tonemap(src, ["-map", "0:V:0?", "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "image2pipe",
                                  "-c:v", "png"])
        proc = subprocess.run(
            [find_tool("ffmpeg"), "-v", "error", "-ss", f"{max(0.0, seconds):.2f}", *LOCAL_ONLY, "-i", str(src), *args,
             "pipe:1"],
            capture_output=True, timeout=15, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        return proc.stdout or None
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return None


def check_has_audio(files: list[Path]) -> str | None:
    return None if probe(files[0])["audio"] else tr("This video has no sound")


def check_gif_length(files: list[Path]) -> str | None:  # slow: reads the video
    return tr("Too long for a GIF (over 2 minutes)") if media_duration(files[0]) > 120 else None


# ----------------------------------------------------------------------------- PDF (pypdf + pdfium)

def _open_reader(src: Path):
    from pypdf import PdfReader
    reader = PdfReader(str(src))
    if reader.is_encrypted and not reader.decrypt(""):
        raise RuntimeError(tr("this PDF is password-protected"))
    return reader


def pdf_lock_state(src: Path) -> str:
    """"open" (no password), "restricted" (opens, but has an owner password) or "locked"."""
    def work():
        try:
            from pypdf import PdfReader
            r = PdfReader(str(src))
            if not r.is_encrypted:
                return "open"
            return "restricted" if r.decrypt("") else "locked"
        except Exception:
            return "open"
    return _cached("pdfstate", src, work)


def check_pdf_locked(files: list[Path]) -> str | None:
    return tr("This PDF is password-protected") if pdf_lock_state(files[0]) == "locked" else None


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
            from .core import render_scale
            page.render(scale=render_scale(page.get_width(), page.get_height(), 150 / 72)).to_pil().save(target)
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
    if not any(part.strip() for part in parts):  # only pictures of pages: an empty .txt would help nobody
        return Result(note=tr("{name} has no text to take out. It's probably a scan (pictures of pages).",
                              name=src.name))
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
                         progress_span: tuple[float, float] = (0.0, 0.9), reader=None):
    """Shrinks big pictures inside the PDF and squeezes the rest. Text stays sharp."""
    from PIL import Image
    from pypdf import PdfWriter
    reader = reader or _open_reader(src)
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
                    if pil.mode in ("RGBA", "LA", "PA", "P") or "transparency" in pil.info:
                        continue  # see-through pictures would get a white box around them
                    if pil.mode == "1":  # (black-and-white scans: their own packing is far smaller than a JPG)
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
        return Result(note=tr("{name} is already as small as it gets", name=src.name))
    return Result([out], note=tr("{before} → {after} ({percent}% smaller)", before=human_size(before),
                                 after=human_size(after), percent=100 - round(after * 100 / before)))


# ----------------------------------------------------------------------------- Word / Excel / PowerPoint

@lru_cache(maxsize=None)
def _office_installed(prog_id: str) -> bool:
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + r"\CLSID"):
            return True
    except OSError:
        return False


@lru_cache(maxsize=None)
def find_libreoffice() -> str | None:
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        p = Path(base) / "LibreOffice" / "program" / "soffice.exe"
        if p.exists():
            return str(p)
    return find_program("soffice")


def office_app_for(src: Path) -> str | None:
    ext = norm(src.suffix)
    prog = "Word.Application" if ext in WORD_EXT else "Excel.Application" if ext in EXCEL_EXT \
        else "PowerPoint.Application"
    if ext not in (".odt", ".ods", ".odp") and _cached("office", Path(prog), lambda: _office_installed(prog)):
        return prog
    return None


# Office is only closed afterwards if nothing else is open in it: PowerPoint (and sometimes Word
# and Excel) share one copy with whatever the person is working on.
# Office opens the file read-only, without adding it to its recent files, and with macros switched
# off: a program that drives Office would otherwise run a document's macros without asking
# (AutomationSecurity 3 = "disable all macros"; the old value is put back afterwards).
OFFICE_SCRIPTS = {
    "Word.Application": """
$app = New-Object -ComObject Word.Application; $app.DisplayAlerts = 0
$was = $app.AutomationSecurity; $app.AutomationSecurity = 3
# a made-up password: a locked file then fails at once, instead of waiting on an invisible password box
try { $d = $app.Documents.Open($env:CF_IN, $false, $true, $false, "corefold-no-password", "corefold-no-password")
      $d.ExportAsFixedFormat($env:CF_OUT, 17); $d.Close(0) }
finally { $app.AutomationSecurity = $was; if ($app.Documents.Count -eq 0) { $app.Quit() } }""",
    "Excel.Application": """
$app = New-Object -ComObject Excel.Application; $app.DisplayAlerts = $false
$was = $app.AutomationSecurity; $app.AutomationSecurity = 3; $app.EnableEvents = $false
$m = [Reflection.Missing]::Value
try { $b = $app.Workbooks.Open($env:CF_IN, 0, $true, $m, "corefold-no-password", "corefold-no-password", $true, $m, $m,
                              $false, $false, $m, $false)
      $b.ExportAsFixedFormat(0, $env:CF_OUT); $b.Close($false) }
finally { $app.AutomationSecurity = $was; $app.EnableEvents = $true; if ($app.Workbooks.Count -eq 0) { $app.Quit() } }""",
    "PowerPoint.Application": """
$app = New-Object -ComObject PowerPoint.Application
$was = $app.AutomationSecurity; $app.AutomationSecurity = 3
try { $p = $app.Presentations.Open($env:CF_IN, -1, 0, 0); $p.SaveAs($env:CF_OUT, 32); $p.Close() }
finally { $app.AutomationSecurity = $was; if ($app.Presentations.Count -eq 0) { $app.Quit() } }""",
}


def document_to_pdf(src: Path, ctx: Ctx) -> Result:
    out = ctx.out_path(src, ctx.stem(src) + ".pdf")
    prog = office_app_for(src)
    if prog:
        # (Office writes into a folder of its own first: after a Cancel, what it still makes goes with it)
        tmp = Path(tempfile.mkdtemp(prefix="corefold-office-"))
        made = tmp / "document.pdf"
        env = dict(os.environ, CF_IN=str(src.resolve()), CF_OUT=str(made))
        ps = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        code, err = _run_office([str(ps), "-NoProfile", "-NonInteractive", "-Command", OFFICE_SCRIPTS[prog]],
                                env, tmp, ctx)
        try:
            if code or not made.exists():
                log.error("office export failed: %s", err[-2000:])
                raise RuntimeError(tr("Office couldn't turn this file into a PDF"))
            shutil.move(str(made), out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return Result([out])
    soffice = find_libreoffice()
    if not soffice:
        raise RuntimeError(tr("needs Microsoft Office or LibreOffice"))
    with tempfile.TemporaryDirectory(prefix="corefold-", ignore_cleanup_errors=True) as tmp:
        # its own profile: otherwise LibreOffice quietly does nothing while it's open on the PC
        profile = (Path(tmp) / "profile").as_uri()
        code, err = _run_stoppable([soffice, f"-env:UserInstallation={profile}", "--headless", "--norestore",
                                    "--convert-to", "pdf", "--outdir", tmp, str(src)], ctx, timeout=600)
        made = Path(tmp) / (src.stem + ".pdf")
        if code or not made.exists():
            log.error("libreoffice export failed: %s", err[-2000:])
            raise RuntimeError(tr("LibreOffice couldn't turn this file into a PDF"))
        shutil.move(str(made), out)
    return Result([out])


def _run_office(cmd: list[str], env: dict, tmp: Path, ctx: Ctx, timeout: float = 600) -> tuple[int, str]:
    """Runs the PowerShell that has Office make the PDF. Cancel stops the waiting at once, but not Office:
    Office runs on its own (Windows starts it, not this PowerShell), so ending the PowerShell would leave
    it hidden in the background with the file still open in it. The script is left to finish (Office
    closes the file and quits as usual), and its folder (tmp, with the PDF it made) is deleted then."""
    err = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err, stdin=subprocess.DEVNULL, env=env,
                            creationflags=job_flags())
    deadline = time.monotonic() + timeout
    while proc.poll() is None:
        if ctx.cancelled or time.monotonic() > deadline:
            threading.Thread(target=_after_office, args=(proc, err, tmp, deadline), daemon=True,
                             name="office-cancelled").start()
            if ctx.cancelled:
                raise Cancelled()
            raise RuntimeError(tr("took too long and was stopped"))
        time.sleep(0.2)
    err.seek(0)
    text = err.read().decode("utf-8", "replace")
    err.close()
    return proc.returncode, text


def _after_office(proc, err, tmp: Path, deadline: float):
    """After a Cancel: waits for Office to finish (a while longer at most), then deletes what it made."""
    try:
        proc.wait(max(60.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        _kill_tree(proc)  # (stuck for good: only then is the PowerShell ended)
    finally:
        err.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _run_stoppable(cmd: list[str], ctx: Ctx, timeout: float) -> tuple[int, str]:
    """Runs a program that gives no progress, but can still be stopped with Cancel (the whole
    process tree, since LibreOffice starts a second program)."""
    import time
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err, stdin=subprocess.DEVNULL,
                                creationflags=job_flags())
        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                if ctx.cancelled or time.monotonic() > deadline:
                    _kill_tree(proc)
                    if ctx.cancelled:
                        raise Cancelled()
                    raise RuntimeError(tr("took too long and was stopped"))
                time.sleep(0.2)
        finally:
            if proc.poll() is None:
                _kill_tree(proc)
        err.seek(0)
        return proc.returncode, err.read().decode("utf-8", "replace")


def _kill_tree(proc):
    if os.name == "nt":
        subprocess.run([system_tool("taskkill.exe"), "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                       stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    else:
        proc.kill()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        pass


def check_office(files: list[Path]) -> str | None:
    if office_app_for(files[0]) or _cached("lo", Path("soffice"), find_libreoffice):
        return None
    return tr("Needs Microsoft Office or LibreOffice")


# ----------------------------------------------------------------------------- what goes on the wheel

CATEGORY_NAMES = {"image": "Images", "audio": "Audio", "video": "Video", "pdf": "PDFs",
                  "document": "Word, Excel & PowerPoint", "archive": "Archives (ZIP, RAR, 7z…)",
                  "folder": "Folders", "files": "Any files and folders (archive, rename)"}
CATEGORY_ICONS = {"image": "image", "audio": "music_note_2", "video": "video", "pdf": "document_pdf",  # icons.py
                  "document": "document_text", "archive": "archive", "folder": "folder", "files": "box"}


def already(ext: str) -> Callable[[list[Path]], str | None]:
    name = FORMAT_NAMES.get(ext, ext[1:].upper())

    def check(files: list[Path]) -> str | None:
        return tr("Already {format}", format=tr(name)) if all(norm(f.suffix) == ext for f in files) else None
    return check


def needs_several(what: str) -> Callable[[list[Path]], str | None]:
    def check(files: list[Path]) -> str | None:
        return None if len(files) > 1 else what
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
    """Every tool Corefold has, per kind of file, in default wheel order."""
    from . import archives as ar, extras as ex, moretools as mt, pdfstamp as ps, videotools as vt

    def fmt(kind, ext, fn, check=None, **kw):
        return Tool(f"{kind}:{ext[1:]}", ext[1:].upper(),
                    tr("Convert to {format}", format=tr(FORMAT_NAMES.get(ext, ext[1:].upper()))),
                    partial(fn, ext=ext, **kw), check=both(already(ext), check), target=ext,
                    slow_check=check is not None)

    pack = [Tool("pack:zip", "ZIP", tr("Put in a ZIP file"), partial(ar.pack, fmt="zip"), batch=True),
            Tool("pack:7z", "7z", tr("Put in a 7z file (smaller than ZIP)"), partial(ar.pack, fmt="7z"), batch=True,
                 check=ar.check_needs_7z, static_check=True),
            Tool("pack:options", tr("Archive…"), tr("Archive with a password, split or max compression"), ar.pack,
                 batch=True, ask="pack"),
            Tool("files:rename", tr("Rename"), tr("Rename the files (numbers, replace text…)"), mt.rename_files,
                 batch=True, ask="rename_files", renames=False),
            Tool("files:hash", "SHA-256", tr("Copy the file's fingerprint, to check a download"), mt.file_hash,
                 batch=True, renames=False, check=mt.check_files_only)]

    folder = [Tool(f"folder:{k}", label, desc, partial(mt.convert_folder, fmt=k), check=mt.check_pictures_folder,
                   renames=False)
              for k, label, desc in (("jpg", "JPG", tr("Convert the pictures inside to JPG")),
                                     ("png", "PNG", tr("Convert the pictures inside to PNG")),
                                     ("webp", "WebP", tr("Convert the pictures inside to WebP")),
                                     ("small", tr("Small"), tr("Shrink the pictures inside for sharing")))]

    image = [fmt("image", e, convert_image) for e in (".png", ".jpg", ".webp")]
    image += [Tool("image:pdf", "PDF", tr("Combine into one PDF") if multi else tr("Convert to PDF"), images_to_pdf,
                   batch=True),
              Tool("image:resize", tr("Resize"), tr("Make it bigger or smaller"), resize_image, ask="resize"),
              Tool("image:rotate", tr("Rotate"), tr("Rotate or flip"), rotate_image, ask="rotate"),
              Tool("image:crop", tr("Crop"), tr("Cut the edges off"), mt.crop_image, ask="crop"),
              Tool("image:fit", tr("Fit size"), tr("Make it fit a size limit (Discord, email…)"), ex.fit, ask="fit",
                   check=check_still),
              Tool("image:cutout", tr("Cut out"), tr("Remove the background"), remove_background,
                   check=check_bg_model, static_check=True),
              Tool("image:ocr", tr("Copy text"), tr("Copy the text in the picture"), ex.read_text, check=ex.check_ocr,
                   renames=False, static_check=True),
              Tool("image:small", tr("Small"), tr("Shrink for sharing"), image_small),
              Tool("image:watermark", tr("Watermark"), tr("Add your name or logo"), ex.watermark_image,
                   ask="watermark"),
              Tool("image:scan", tr("Scan"), tr("Clean up photos of paper into a PDF"), ex.scan_to_pdf, batch=True),
              Tool("image:video", tr("Video"), tr("Turn the pictures into a video slideshow"), ex.images_to_video,
                   batch=True, ask="slideshow", check=ex.check_several_images)]
    image += [fmt("image", e, convert_image) for e in (".ico", ".bmp", ".tiff", ".gif")]
    image += [fmt("image", ".avif", convert_image, check=check_avif)]
    image += [Tool("image:qr", "QR", tr("Read QR code"), read_qr, renames=False),
              Tool("image:clean", tr("Clean"), tr("Remove location & camera info"), strip_metadata),
              Tool("image:sticker", tr("Sticker"), tr("Make a sticker (WhatsApp, Telegram)"), mt.image_sticker,
                   ask="sticker")]

    audio = [fmt("audio", e, ffmpeg_convert, args=a) for e, a in AUDIO_ARGS.items()]
    audio += [Tool("audio:trim", tr("Trim"), tr("Cut the start or end"), trim_media, ask="trim"),
              Tool("audio:fit", tr("Fit size"), tr("Make it fit a size limit"), ex.fit, ask="fit"),
              Tool("audio:volume", tr("Volume"), tr("Fix the volume (too quiet or too loud)"), mt.fix_volume),
              Tool("audio:speed", tr("Speed"), tr("Speed up or slow down"), mt.change_speed, ask="speed"),
              Tool("audio:join", tr("Join"), tr("Join them into one file, one after another"), vt.join_media,
                   batch=True, check=vt.check_join)]

    video = [fmt("video", e, ffmpeg_convert, check=check_gif_length if e == ".gif" else None, args=a)
             for e, a in VIDEO_ARGS.items()]
    video += [Tool("video:trim", tr("Trim"), tr("Cut the start or end"), trim_media, ask="trim"),
              Tool("video:fit", tr("Fit size"), tr("Make it fit a size limit (Discord, email…)"), ex.fit, ask="fit"),
              Tool("video:rotate", tr("Rotate"), tr("Rotate or flip"), rotate_video, ask="rotate"),
              Tool("video:small", tr("Small"), tr("Shrink for sharing"),
                   partial(ffmpeg_convert, ext=".mp4", args=VIDEO_SMALL_ARGS, suffix=" (small)")),
              Tool("video:watermark", tr("Watermark"), tr("Add your name or logo"), ex.watermark_video,
                   ask="watermark"),
              Tool("video:frames", tr("Frames"), tr("Save pictures from the video"), ex.video_frames, ask="frames"),
              Tool("video:mp3", "MP3", tr("Save the sound as MP3"),
                   partial(ffmpeg_convert, ext=".mp3", args=AUDIO_ARGS[".mp3"]), check=check_has_audio,
                   slow_check=True),
              Tool("video:mute", tr("Mute"), tr("Remove the sound"), mt.mute_video, check=check_has_audio,
                   slow_check=True),
              Tool("video:volume", tr("Volume"), tr("Fix the volume (too quiet or too loud)"), mt.fix_volume,
                   check=check_has_audio, slow_check=True),
              Tool("video:speed", tr("Speed"), tr("Speed up or slow down"), mt.change_speed, ask="speed"),
              Tool("video:crop", tr("Crop"), tr("Remove black bars, or make it square or vertical"), vt.crop_video,
                   ask="video_crop"),
              Tool("video:subtitles", tr("Subtitles"), tr("Put a subtitle file into the video"), vt.add_subtitles,
                   ask="subtitles"),
              Tool("video:srt", "SRT", tr("Save the subtitles inside as .srt"), vt.save_subtitles,
                   check=vt.check_has_subtitles, slow_check=True),
              Tool("video:sound", tr("Sound…"), tr("Save the sound (M4A, WAV, FLAC…)"), vt.video_sound, ask="sound",
                   check=check_has_audio, slow_check=True),
              Tool("video:join", tr("Join"), tr("Join them into one video, one after another"), vt.join_media,
                   batch=True, check=vt.check_join),
              Tool("video:sticker", tr("Sticker"), tr("Make a sticker or chat GIF (WhatsApp, Telegram, Discord)"),
                   mt.video_sticker, ask="sticker")]

    pdf = [Tool("pdf:merge", tr("Merge"), tr("Combine into one PDF"), pdf_merge, batch=True,
                check=needs_several(tr("Drop 2 or more PDFs to combine them"))),
           Tool("pdf:compress", tr("Compress"), tr("Make the file smaller"), pdf_compress, check=check_pdf_locked,
                slow_check=True),
           Tool("pdf:fit", tr("Fit size"), tr("Make it fit a size limit (email…)"), ex.fit, ask="fit",
                check=check_pdf_locked, slow_check=True),
           Tool("pdf:split", tr("Split"), tr("One PDF per page"), pdf_split, check=check_pdf_locked, slow_check=True),
           Tool("pdf:png", "PNG", tr("Pages to images"), pdf_to_png, check=check_pdf_locked, slow_check=True),
           Tool("pdf:text", tr("Text"), tr("Pull out the text"), pdf_to_text, check=check_pdf_locked, slow_check=True),
           Tool("pdf:pages", tr("Pages"), tr("Turn, delete or reorder pages"), mt.pdf_pages, ask="pages",
                check=check_pdf_locked, slow_check=True),
           Tool("pdf:images", tr("Pictures"), tr("Save the pictures inside"), mt.pdf_images, check=check_pdf_locked,
                slow_check=True),
           Tool("pdf:numbers", tr("Page numbers"), tr("Number every page"), ps.page_numbers, ask="page_numbers",
                check=check_pdf_locked, slow_check=True),
           Tool("pdf:watermark", tr("Watermark"), tr("Add text or a logo to every page"), ps.watermark_pdf,
                ask="watermark", check=check_pdf_locked, slow_check=True),
           Tool("pdf:sign", tr("Sign"), tr("Put your signature on it"), ps.sign_pdf, ask="sign",
                check=check_pdf_locked, slow_check=True),
           Tool("pdf:lock", tr("Lock"), tr("Protect with a password"), mt.pdf_lock, ask="lock",
                check=mt.check_can_lock, slow_check=True),
           Tool("pdf:unlock", tr("Unlock"), tr("Remove the password"), mt.pdf_unlock, password=True,
                check=mt.check_can_unlock, slow_check=True)]

    document = [Tool("document:pdf", "PDF", tr("Convert to PDF"), document_to_pdf, check=check_office,
                     static_check=True)]

    def is_ext(*exts):
        def check(files):
            return tr("Already {format}", format=exts[0][1:].upper()) if all(f.suffix.lower() in exts for f in files) \
                else None
        return check

    archive = [Tool("archive:extract", tr("Extract"), tr("Extract here"), partial(ar.extract, mode="smart"),
                    check=ar.check_readable, password=True, renames=False),
               Tool("archive:folder", tr("To folder"), tr("Extract into its own folder"),
                    partial(ar.extract, mode="folder"),
                    check=ar.check_readable, password=True),
               Tool("archive:to", tr("Extract to…"), tr("Extract into a folder you pick"),
                    partial(ar.extract, mode="smart"), check=ar.check_readable, password=True, renames=False,
                    ask="folder"),
               Tool("archive:browse", tr("Browse"), tr("See what's inside"), None, opens="browse",
                    check=ar.check_readable,
                    password=True),
               Tool("archive:test", tr("Test"), tr("Check it isn't damaged"), ar.test_archive,
                    check=ar.check_readable,
                    password=True, renames=False),
               Tool("archive:zip", "ZIP", tr("Turn into a ZIP"), partial(ar.convert_archive, fmt="zip"),
                    check=both(ar.check_readable, is_ext(".zip")), password=True, target=".zip"),
               Tool("archive:7z", "7z", tr("Turn into a 7z (smaller)"), partial(ar.convert_archive, fmt="7z"),
                    check=both(ar.check_needs_7z, is_ext(".7z")), password=True, target=".7z")]
    archive += [t for t in pack if t.key == "files:rename"]  # archives can be renamed too
    from .presets import tools as preset_tools
    for kind, mine in preset_tools().items():  # (the person's own presets, after the formats)
        {"image": image, "audio": audio, "video": video}[kind].extend(mine)
    return {"image": image, "audio": audio, "video": video, "pdf": pdf, "document": document,
            "archive": archive, "folder": folder, "files": pack}


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
    pending: bool = False   # some checks still need to read the file (see warm_up)


def _slow_ready(f: Path) -> bool:
    """What the slow checks need for this file is known already (so they can run on the window's thread)."""
    kind = category(f)
    if kind == "pdf":
        return is_cached("pdfstate", f)
    # (a video's subtitle check runs FFprobe too: it must never run on the window's thread)
    return is_cached("probe", f) and (kind != "video" or is_cached("subs", f))


def warm_up(files: list[Path]):
    """Reads what the slow checks need (in a background thread), so the next pick() is instant."""
    for f in files[:1]:
        if is_cloud_only(f):
            continue  # don't download a whole video from OneDrive just to look at it
        try:
            if category(f) in ("video", "audio"):
                probe(f)
                if category(f) == "video":
                    from .videotools import subtitle_tracks
                    subtitle_tracks(f)
            elif category(f) == "pdf":
                check_pdf_locked([f])
        except Exception:
            log.debug("warm-up failed", exc_info=True)


def pick(paths: list[Path], hidden=frozenset(), favorites=(), show_unavailable: bool = True,
         order: dict | None = None, pack_everywhere: bool = True, quick: bool = False) -> Pick:
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
    if kind == "other":
        kind = "files"
    files = [p for p in paths if not (kind == "archive" and is_extra_part(p))] or paths
    cat = catalog(len(files) > 1)
    tools = list(cat[kind])
    if kind == "folder" or (pack_everywhere and kind not in ("files", "archive")):
        tools += cat["files"]  # folders always get "ZIP" and "Rename" too
    result = Pick(files, [], kind)
    for t in tools:
        if t.key in hidden:
            result.hidden += 1
            continue
        if t.slow_check and is_cloud_only(files[0]):
            result.slots.append(Slot(t, None))  # not on this PC yet: assume it works rather than download it
            continue
        if quick and t.slow_check and not _slow_ready(files[0]):
            result.pending = True  # assume it works for now; warm_up() + a second pick() will tell
            result.slots.append(Slot(t, None))
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


def password_error() -> str:
    """The password message in the current language (the app looks for it to ask again)."""
    return tr(PASSWORD_ERROR)


def friendly_error(e: Exception) -> str:
    name = type(e).__name__
    if name == "NeedPassword":
        return password_error()
    if name in ("BadZipFile", "ReadError", "CompressionError", "HeaderError"):
        return tr("the archive is damaged or isn't a real archive")
    if name == "UnidentifiedImageError":
        return tr("not a readable image")
    if name == "DecompressionBombError" or isinstance(e, MemoryError):
        return tr("this picture is too big to open")
    if name in ("PdfReadError", "PdfStreamError", "EmptyFileError", "PdfiumError", "FileTruncatedError"):
        return tr("the PDF is damaged or isn't a real PDF")
    if disk_full(e):
        return tr("the disk is full")
    if isinstance(e, PermissionError):
        return tr("permission denied (is the file open in another app?)")
    if isinstance(e, FileNotFoundError):
        return tr("file not found")
    if isinstance(e, subprocess.TimeoutExpired):
        return tr("took too long and was stopped")
    return str(e) or name


# picture tools that only use Pillow: several photos are done at once (one per processor core, up to 4), at
# the job's low priority, so 20 phone photos take a fraction of the time and the PC still feels normal
SERIAL_IMAGE_TOOLS = {"image:cutout", "image:ocr", "image:video", "image:sticker", "image:pdf", "image:scan"}


def _parallel(tool: Tool, items: list) -> int:
    if tool.batch or len(items) < 2 or not tool.key.startswith("image:") or tool.key in SERIAL_IMAGE_TOOLS:
        return 1
    workers = min(4, max(1, (os.cpu_count() or 2) - 1), len(items))
    try:
        if max(p.stat().st_size for p in items) > 40 * 2**20:  # very big pictures: fewer at once (memory)
            workers = min(workers, 2)
    except OSError:
        pass
    if workers > 1:  # (a small file can still be a huge picture: 3 of 250 MP each at once is 3 GB of memory)
        most = _most_pixels(items)
        if most > 150_000_000:
            workers = 1
        elif most > 50_000_000:
            workers = min(workers, 2)
    return workers


def _most_pixels(items: list, look_at: int = 400) -> int:
    """The most pixels of the bigger files' pictures (from their headers: nothing is decoded)."""
    setup_pillow()
    from PIL import Image
    try:
        big = sorted(items, key=lambda p: p.stat().st_size, reverse=True)[:look_at]
    except OSError:
        return 0
    most = 0
    for p in big:
        try:
            if p.stat().st_size < 2 << 20 and most:
                break
            with Image.open(p) as im:
                most = max(most, im.width * im.height)
        except Exception:
            continue
    return most


def run_job(tool: Tool, files: list[Path], options: Options,
            progress: Callable[[float], None] | None = None,
            cancel: threading.Event | None = None,
            params: dict | None = None,
            on_item: Callable[[int, int, str], None] | None = None) -> tuple[Result, list[str]]:
    """Run a tool over the files. Returns the combined result and a list of per-file errors.
    on_item(number, total, name) is told which file is being worked on (for "3 of 12")."""
    progress = progress or (lambda f: None)
    cancel = cancel or threading.Event()
    total, errors = Result(), []
    items = [files] if tool.batch else list(files)
    n = len(items)
    workers = _parallel(tool, items)
    parts = [0.0] * n  # how far each file is (several are worked on at once)
    so_far = [0.0]     # their sum (kept, not added up again each time: 100,000 files made that billions of steps)
    lock = threading.Lock()
    started = [0]

    def part(i, f):
        with lock:
            so_far[0] += f - parts[i]
            parts[i] = f
            done = so_far[0]
        progress(min(done / n, 1.0))

    def one(i, item):
        """(result, None), (None, error text) or (None, None) when it was cancelled."""
        if cancel.is_set():
            return None, None
        if workers > 1:
            lower_this_thread()
        ctx = Ctx(options, lambda f, i=i: part(i, f), cancel, params, index=None if tool.batch else i, total=n)
        if on_item and n > 1:
            with lock:
                started[0] += 1
                number = started[0]
            on_item(number, n, item.name)
        try:
            r = tool.run(item, ctx)
            try:
                pass_on_zone_mark([item] if isinstance(item, Path) else item, r.outputs)
            except Exception:
                log.debug("couldn't pass on the download mark", exc_info=True)
            if options.keep_dates and tool.target and isinstance(item, Path):  # (after the mark: writing it is a change)
                copy_dates(item, r.outputs)
            return r, None
        except Cancelled:
            ctx.discard()
            return None, None
        except Exception as e:  # keep going with the remaining files
            ctx.discard()
            if isinstance(item, Path):
                name = short_name(item.name)
            else:
                name = short_name(item[0].name) if len(item) == 1 else tr("{count} files", count=len(item))
            if type(e).__name__ == "NeedPassword":
                with lock:
                    total.locked.append(item)  # the app asks for the password and tries just these again
                log.info("%s needs a password: %s", tool.label, name)
            else:
                log.exception("%s failed on %s", tool.label, name)
            return None, f"{name}: {friendly_error(e)}"
        finally:
            part(i, 1.0)

    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="corefold-job") as pool:
            outcomes = list(pool.map(one, range(n), items))
    else:
        outcomes = []
        for i, item in enumerate(items):
            if cancel.is_set():
                break
            r, err = one(i, item)
            outcomes.append((r, err))
            if r is None and err is None:  # cancelled
                break
    for item, (r, err) in zip(items, outcomes):  # in the order the files were given
        if r is not None:
            total.outputs += r.outputs
            total.clipboard = r.clipboard or total.clipboard
            total.note = r.note or total.note
            total.undo += r.undo
            if r.outputs and isinstance(item, Path):
                total.sources.append(item)
        elif err is not None:
            errors.append(err)
    return total, errors
