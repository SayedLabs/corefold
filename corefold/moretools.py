"""
More tools: PDF pages, pictures out of a PDF, PDF passwords, crop, stickers, mute, speed, volume,
converting every picture in a folder, and renaming files. No GUI code here.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path

from .converters import (AUDIO_ARGS, Ctx, Result, category, is_animated, keep_format, load_image, media_duration, norm,
                         pdf_lock_state, probe, rgb_or_rgba, run_ffmpeg, safe_name, save_animated, save_image, tr)

log = logging.getLogger("corefold")
MB = 1024 * 1024


# ----------------------------------------------------------------------------- PDF

def _reader(src: Path, password: str = ""):
    from pypdf import PdfReader
    from .archives import NeedPassword
    reader = PdfReader(str(src))
    if reader.is_encrypted and not reader.decrypt(password or ""):
        raise NeedPassword()  # the app asks for the password and tries again
    return reader


def check_can_lock(files: list[Path]) -> str | None:
    return tr("This PDF already has a password") if pdf_lock_state(files[0]) == "locked" else None


def check_can_unlock(files: list[Path]) -> str | None:
    return tr("This PDF has no password") if pdf_lock_state(files[0]) == "open" else None


def pdf_lock(src: Path, ctx: Ctx) -> Result:
    from pypdf import PdfWriter
    password = ctx.params.get("new_password", "")
    if not password:
        raise RuntimeError(tr("type a password first"))
    writer = PdfWriter(clone_from=_reader(src, ctx.params.get("password", "")))
    writer.encrypt(user_password=password, owner_password=password, algorithm="AES-256")
    out = ctx.out_path(src, f"{ctx.stem(src)} (locked).pdf")
    with open(out, "wb") as fh:
        writer.write(fh)
    return Result([out], note=tr("Protected with your password"))


def pdf_unlock(src: Path, ctx: Ctx) -> Result:
    from pypdf import PdfWriter
    reader = _reader(src, ctx.params.get("password", ""))
    writer = PdfWriter(clone_from=reader)
    out = ctx.out_path(src, f"{ctx.stem(src)} (unlocked).pdf")
    with open(out, "wb") as fh:
        writer.write(fh)
    return Result([out])


def pdf_images(src: Path, ctx: Ctx) -> Result:
    """Saves every picture inside the PDF, as it is (no quality lost)."""
    reader = _reader(src, ctx.params.get("password", ""))
    n = max(1, len(reader.pages))
    outdir = None
    count = 0
    for i, page in enumerate(reader.pages):
        ctx.check()
        try:
            images = list(page.images)
        except Exception:
            log.debug("couldn't read pictures on page %s", i + 1, exc_info=True)
            images = []
        for j, img in enumerate(images):
            try:
                name = Path(img.name or f"image{j + 1}.png")
                ext = name.suffix.lower() if name.suffix.lower() in (".jpg", ".jpeg", ".png", ".jp2", ".tif",
                                                                      ".tiff", ".bmp", ".gif") else ".png"
                if outdir is None:
                    outdir = ctx.out_dir(src, f"{ctx.stem(src)} pictures")
                target = outdir / f"page {i + 1:0{len(str(n))}} - {j + 1}{ext}"
                if ext == ".png" and not name.suffix.lower() == ".png":
                    img.image.save(target)
                else:
                    target.write_bytes(img.data)
                count += 1
            except Exception:
                log.debug("skipped a picture on page %s", i + 1, exc_info=True)
        ctx.progress((i + 1) / n)
    if not count:
        return Result(note=tr("No pictures in {name}", name=src.name))
    return Result([outdir], note=tr("Saved {count} pictures", count=count))


def pdf_save_pages(src: Path, out: Path, pages: list[tuple[int, int]], password: str = ""):
    """Writes a new PDF with these pages: (page number in the original, extra turn in degrees)."""
    from pypdf import PdfWriter
    reader = _reader(src, password)
    writer = PdfWriter()
    for index, turn in pages:
        page = writer.add_page(reader.pages[index])
        if turn % 360:
            page.rotate(turn % 360)
    tmp = out.with_name(out.name + ".part")
    try:
        with open(tmp, "wb") as fh:
            writer.write(fh)
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)


def pdf_pages(src: Path, ctx: Ctx) -> Result:
    """Saves the pages as they were arranged in the Pages window, as a new PDF."""
    pages = [(int(i), int(t)) for i, t in ctx.params.get("pages", [])]
    if not pages:
        raise RuntimeError(tr("there are no pages left"))
    out = ctx.out_path(src, f"{ctx.stem(src)} (edited).pdf")
    pdf_save_pages(src, out, pages, ctx.params.get("password", ""))
    return Result([out])


# ----------------------------------------------------------------------------- crop

CROP_RATIOS = {"free": ("Free", None), "1:1": ("Square", 1.0), "4:3": ("4:3", 4 / 3), "3:2": ("3:2", 3 / 2),
               "16:9": ("16:9 (wide)", 16 / 9), "9:16": ("9:16 (phone)", 9 / 16)}


def crop_box(width: int, height: int, box) -> tuple[int, int, int, int]:
    """The crop box in pixels, from (left, top, right, bottom) between 0 and 1."""
    x0, y0, x1, y1 = (max(0.0, min(1.0, float(v))) for v in box)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    left, top = round(x0 * width), round(y0 * height)
    right, bottom = max(left + 1, round(x1 * width)), max(top + 1, round(y1 * height))
    return left, top, min(width, right), min(height, bottom)


def crop_image(src: Path, ctx: Ctx) -> Result:
    box = ctx.params.get("box", (0, 0, 1, 1))
    ext = keep_format(src)
    out = ctx.out_path(src, f"{ctx.stem(src)} (cropped){ext}")
    if is_animated(src):
        from PIL import Image
        with Image.open(src) as first:
            size = first.size
        b = crop_box(*size, box)
        save_animated(src, out, lambda frame: frame.crop(b))
        return Result([out])
    im = load_image(src)
    save_image(im.crop(crop_box(im.width, im.height, box)), out, ext, ctx)
    return Result([out])


# ----------------------------------------------------------------------------- stickers

STICKERS_IMAGE = {"whatsapp": "WhatsApp sticker (512 × 512 WebP)", "telegram": "Telegram sticker (512 px PNG)"}
STICKERS_VIDEO = {"whatsapp": "WhatsApp animated sticker (WebP, up to 6 seconds)",
                  "telegram": "Telegram video sticker (WebM, up to 3 seconds)",
                  "gif": "GIF for chats and Discord (up to 10 seconds)"}


def image_sticker(src: Path, ctx: Ctx) -> Result:
    import io
    from PIL import Image
    preset = ctx.params.get("preset", "whatsapp")
    if is_animated(src):  # an animated GIF or WebP stays animated
        if norm(src.suffix) == ".gif":
            return video_sticker(src, ctx)
        # FFmpeg can't always read animated WebP: hand it a GIF copy made by Pillow
        import tempfile
        from .converters import save_animated
        with tempfile.TemporaryDirectory(prefix="corefold-") as tmp:
            gif = Path(tmp) / "frames.gif"
            save_animated(src, gif, lambda frame: frame)
            return video_sticker(src, ctx, source=gif)
    im = rgb_or_rgba(load_image(src)).convert("RGBA")
    im.thumbnail((512, 512), Image.LANCZOS)
    if preset == "telegram":  # one side exactly 512
        scale = 512 / max(im.size)
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
        out = ctx.out_path(src, f"{ctx.stem(src)} (sticker).png")
        im.save(out, optimize=True)
        return Result([out])
    canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    canvas.alpha_composite(im, ((512 - im.width) // 2, (512 - im.height) // 2))
    out = ctx.out_path(src, f"{ctx.stem(src)} (sticker).webp")
    data = b""
    for q in (90, 80, 65, 50, 35, 20):  # WhatsApp wants still stickers under 100 KB
        buf = io.BytesIO()
        canvas.save(buf, "WEBP", quality=q, method=6)
        data = buf.getvalue()
        if len(data) <= 100 * 1024:
            break
    out.write_bytes(data)
    return Result([out])


def video_sticker(src: Path, ctx: Ctx, source: Path | None = None) -> Result:
    """source: what FFmpeg reads, when that isn't src itself (the new file is still named after src)."""
    preset = ctx.params.get("preset", "whatsapp")
    source = source or src
    dur = media_duration(source) or 10
    if preset == "telegram":
        length, limit = min(3.0, dur), 256 * 1024
        out = ctx.out_path(src, f"{ctx.stem(src)} (sticker).webm")
        tries = [("600k", 30), ("400k", 30), ("250k", 24), ("150k", 20)]
        for i, (rate, fps) in enumerate(tries):
            args = ["-map", "0:V:0", "-t", f"{length:.2f}", "-an", "-vf",
                    f"scale=512:512:force_original_aspect_ratio=decrease,fps={fps}", "-c:v", "libvpx-vp9",
                    "-b:v", rate, "-pix_fmt", "yuva420p", "-deadline", "good", "-cpu-used", "4"]
            run_ffmpeg(source, out, args, _part(ctx, i, len(tries)), duration=length)
            if out.stat().st_size <= limit:
                break
        return Result([out])
    if preset == "gif":
        length = min(10.0, dur)
        out = ctx.out_path(src, f"{ctx.stem(src)} (chat).gif")
        for i, (width, fps) in enumerate(((480, 15), (360, 12), (280, 10))):
            args = ["-map", "0:V:0", "-t", f"{length:.2f}", "-an", "-vf",
                    f"fps={fps},scale='min({width},iw)':-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
                    "-loop", "0"]
            run_ffmpeg(source, out, args, _part(ctx, i, 3), duration=length)
            if out.stat().st_size <= 10 * MB:
                break
        return Result([out])
    # WhatsApp: 512 × 512 animated WebP, under 500 KB
    length = min(6.0, dur)
    out = ctx.out_path(src, f"{ctx.stem(src)} (sticker).webp")
    tries = [(70, 15), (50, 12), (35, 10), (20, 8)]
    for i, (q, fps) in enumerate(tries):
        args = ["-map", "0:V:0", "-t", f"{length:.2f}", "-an", "-vf",
                f"fps={fps},scale=512:512:force_original_aspect_ratio=decrease,"
                "pad=512:512:(ow-iw)/2:(oh-ih)/2:color=0x00000000,format=rgba",
                "-c:v", "libwebp_anim", "-lossless", "0", "-q:v", str(q), "-loop", "0"]
        run_ffmpeg(source, out, args, _part(ctx, i, len(tries)), duration=length)
        if out.stat().st_size <= 500 * 1024:
            break
    return Result([out])


def _part(ctx: Ctx, i: int, n: int) -> Ctx:
    return Ctx(ctx.options, lambda f: ctx.progress((i + f) / n), ctx.cancel_event, ctx.params, ctx.index,
               ctx.total)


# ----------------------------------------------------------------------------- mute, speed, volume

def _same_container(src: Path) -> str:
    ext = norm(src.suffix)
    return ext if ext in (".mp4", ".mov", ".mkv", ".webm", ".m4v") else ".mp4"


def mute_video(src: Path, ctx: Ctx) -> Result:
    """Removes the sound. The picture is copied as it is, so it's quick and loses no quality."""
    ext = _same_container(src)
    out = ctx.out_path(src, f"{ctx.stem(src)} (no sound){ext}")
    copy = ["-map", "0:V:0", "-an", "-sn", "-dn", "-c:v", "copy"]
    try:
        run_ffmpeg(src, out, copy, ctx)
    except RuntimeError:  # an old kind of video that can't simply be copied into this file type
        run_ffmpeg(src, out, ["-map", "0:V:0", "-an", "-sn", "-dn", "-c:v", "libx264", "-crf", "20",
                              "-pix_fmt", "yuv420p", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"], ctx)
    return Result([out])


SPEEDS = [0.25, 0.5, 0.75, 1.25, 1.5, 2.0, 3.0, 4.0]


def atempo_chain(speed: float) -> str:
    """FFmpeg's atempo only goes from 0.5× to 2× in one step, so bigger changes are chained."""
    parts = []
    s = speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.4f}")
    return ",".join(parts)


def change_speed(src: Path, ctx: Ctx) -> Result:
    speed = float(ctx.params.get("speed", 2.0))
    if speed <= 0:
        raise RuntimeError(tr("pick a speed"))
    tag = f"{speed:g}x"
    dur = media_duration(src) / speed if media_duration(src) else None
    if category(src) == "audio":
        ext = norm(src.suffix) if norm(src.suffix) in AUDIO_ARGS else ".mp3"
        out = ctx.out_path(src, f"{ctx.stem(src)} ({tag}){ext}")
        run_ffmpeg(src, out, ["-vn", "-af", atempo_chain(speed)] + AUDIO_ARGS[ext][1:], ctx, duration=dur)
        return Result([out])
    out = ctx.out_path(src, f"{ctx.stem(src)} ({tag}).mp4")
    args = ["-map", "0:V:0", "-sn", "-dn", "-vf", f"setpts=PTS/{speed},pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if probe(src).get("audio"):
        args = args[:2] + ["-map", "0:a:0?"] + args[2:] + ["-af", atempo_chain(speed), "-c:a", "aac", "-b:a", "160k"]
    else:
        args += ["-an"]
    run_ffmpeg(src, out, args, ctx, duration=dur)
    return Result([out])


LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"  # the loudness most apps and streaming sites aim for


def fix_volume(src: Path, ctx: Ctx) -> Result:
    """Makes quiet (or too loud) recordings an even, normal loudness."""
    if category(src) == "audio":
        ext = norm(src.suffix) if norm(src.suffix) in AUDIO_ARGS else ".mp3"
        out = ctx.out_path(src, f"{ctx.stem(src)} (volume fixed){ext}")
        # loudnorm works at a very high sample rate inside; "-ar 48000" brings it back to a normal one
        args = ["-vn", "-af", LOUDNORM, "-ar", "48000"] + AUDIO_ARGS[ext][1:]
        run_ffmpeg(src, out, args, ctx)
        return Result([out])
    if not probe(src)["audio"]:
        raise RuntimeError(tr("this file has no audio track"))
    ext = _same_container(src)
    out = ctx.out_path(src, f"{ctx.stem(src)} (volume fixed){ext}")
    audio = ["-c:a", "libopus", "-b:a", "128k"] if ext == ".webm" else ["-c:a", "aac", "-b:a", "192k"]
    run_ffmpeg(src, out, ["-map", "0:V:0", "-map", "0:a:0", "-sn", "-dn", "-c:v", "copy", "-af", LOUDNORM,
                          "-ar", "48000"] + audio, ctx)
    return Result([out])


# ----------------------------------------------------------------------------- every picture in a folder

FOLDER_FORMATS = {"jpg": ".jpg", "png": ".png", "webp": ".webp", "small": ".jpg"}


def _pictures_in(folder: Path) -> list[Path]:
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            p = Path(root) / f
            if category(p) == "image":
                out.append(p)
    return sorted(out)


def convert_folder(src: Path, ctx: Ctx, fmt: str = "jpg") -> Result:
    """Converts every picture in a folder (and the folders inside it) into a new folder next to it,
    with the same layout. The originals stay as they are."""
    from PIL import Image
    if not src.is_dir():
        raise RuntimeError(tr("drop a folder for this"))
    pictures = _pictures_in(src)
    if not pictures:
        return Result(note=tr("No pictures in {name}", name=src.name))
    ext = FOLDER_FORMATS.get(fmt, ".jpg")
    tag = "small" if fmt == "small" else ext[1:].upper()
    outdir = ctx.out_dir(src, f"{src.name} ({tag})")
    failed = 0
    for i, pic in enumerate(pictures):
        ctx.check()
        rel = pic.relative_to(src).with_suffix(ext)
        target = outdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            im = load_image(pic)
            if fmt == "small":
                im = im.convert("RGB")
                im.thumbnail((1920, 1920), Image.LANCZOS)
                im.save(target, quality=80, optimize=True, progressive=True)
            else:
                save_image(im, target, ext, ctx)
        except Exception:
            failed += 1
            log.debug("couldn't convert %s", pic, exc_info=True)
        ctx.progress((i + 1) / len(pictures))
    done = len(pictures) - failed
    note = tr("Converted {count} pictures", count=done)
    if failed:
        note += " · " + tr("{count} couldn't be read", count=failed)
    return Result([outdir], note=note)


# ----------------------------------------------------------------------------- renaming

def _natural(f: Path):
    """Sorts "2.jpg" before "10.jpg", like Explorer does."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", f.name)]


def new_names(files: list[Path], params: dict) -> list[tuple[Path, Path]]:
    """What each file will be called: [(old, new)]. Nothing is renamed here."""
    mode = params.get("mode", "number")
    pairs = []
    if mode == "replace":
        find, repl = params.get("find", ""), params.get("replace", "")
        for f in files:
            stem = f.name if f.is_dir() else f.stem
            suffix = "" if f.is_dir() else f.suffix
            name = safe_name(stem.replace(find, repl) if find else stem) or stem
            pairs.append((f, f.with_name(name + suffix)))
        return pairs
    if mode == "add":
        before, after = params.get("before", ""), params.get("after", "")
        for f in files:
            stem = f.name if f.is_dir() else f.stem
            suffix = "" if f.is_dir() else f.suffix
            pairs.append((f, f.with_name(safe_name(before + stem + after) + suffix)))
        return pairs
    base = safe_name(params.get("base", "")) or "file"
    start = int(params.get("start", 1))
    files = sorted(files, key=_natural)
    digits = len(str(start + len(files) - 1))
    for i, f in enumerate(files):
        suffix = "" if f.is_dir() else f.suffix
        pairs.append((f, f.with_name(f"{base} {start + i:0{digits}d}{suffix}")))
    return pairs


def rename_conflicts(pairs: list[tuple[Path, Path]]) -> list[Path]:
    """New names that are already taken by another file (or used twice)."""
    olds = {os.path.normcase(str(o)) for o, _ in pairs}
    seen, bad = set(), []
    for old, new in pairs:
        key = os.path.normcase(str(new))
        if key in seen or (new.exists() and key not in olds):
            bad.append(new)
        seen.add(key)
    return bad


def rename_files(files: list[Path], ctx: Ctx) -> Result:
    pairs = [(o, n) for o, n in new_names(files, ctx.params) if o != n]
    if not pairs:
        return Result(note=tr("The names stay the same"))
    if rename_conflicts(pairs):
        raise RuntimeError(tr("some new names are already used by other files"))
    # in two steps through temporary names, so "a → b, b → c" can't trip over itself
    temp, done = [], []
    try:
        for old, new in pairs:
            t = old.with_name(f".corefold-rename-{uuid.uuid4().hex[:8]}")
            old.rename(t)
            temp.append((t, old, new))
        for i, (t, old, new) in enumerate(temp):
            t.rename(new)
            done.append((new, old))
            ctx.progress((i + 1) / len(temp))
    except OSError:
        # put everything back the way it was, then say what went wrong
        for new, old in done:
            try:
                new.rename(old)
            except OSError:
                log.exception("couldn't put back %s", old)
        for t, old, _ in temp:
            if t.exists():
                try:
                    t.rename(old)
                except OSError:
                    log.exception("couldn't put back %s", old)
        raise
    return Result([n for n, _ in done], note=tr("Renamed {count} files", count=len(done)), undo=done)


def undo_rename(done: list[tuple[Path, Path]]) -> int:
    """Gives files their old names back. Returns how many."""
    back = 0
    for new, old in reversed(done):
        try:
            if new.exists() and not old.exists():
                new.rename(old)
                back += 1
        except OSError:
            log.exception("couldn't undo the rename of %s", new)
    return back


def check_pictures_folder(files: list[Path]) -> str | None:
    return None if all(f.is_dir() for f in files) else tr("Drop a folder for this")

