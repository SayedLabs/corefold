"""The Converter's presets: a conversion the person names and keeps (a format with its own quality and size,
like "WhatsApp: MP4, smaller file, up to 720p"). Each shows on the wheel and in the right-click menu of the
files it's for, next to the formats. No GUI code here.

They're kept in the settings (converter_presets); CURRENT is what the wheel and menu show now."""
from __future__ import annotations

import dataclasses
import os
from functools import partial
from pathlib import Path

from .core import Ctx, Result, Tool
from .i18n import tr

MAX = 12
KINDS = {"video": "Video", "audio": "Audio", "image": "Images"}  # (the Converter's own names for them)
FORMATS = {"video": (".mp4", ".mov", ".webm", ".mkv"), "audio": (".mp3", ".m4a", ".ogg", ".opus"),
           "image": (".jpg", ".webp", ".avif", ".png")}
QUALITIES = {"high": "High (bigger file)", "balanced": "Balanced", "smaller": "Smaller file",
             "smallest": "Smallest file"}
IMAGE_QUALITY = {"high": 92, "balanced": 82, "smaller": 70, "smallest": 55}
IMAGE_SIZES = {"original": "Same as the picture", "3840": "Up to 3840 pixels", "1920": "Up to 1920 pixels",
               "1280": "Up to 1280 pixels", "800": "Up to 800 pixels"}
# ready to add with one click: (name, kind, format, quality, size)
READY = [("WhatsApp", "video", ".mp4", "smaller", "720"),
         ("YouTube 1080p", "video", ".mp4", "high", "1080"),
         ("Photos for email", "image", ".jpg", "balanced", "1920")]
CURRENT: list[dict] = []


def sizes(kind: str) -> dict:
    from .settings import VIDEO_SIZES
    return VIDEO_SIZES if kind == "video" else IMAGE_SIZES if kind == "image" else {}


def clean(items) -> list[dict]:
    """Only presets that make sense (from settings.json, which anyone could have changed)."""
    out, ids = [], set()
    for p in items if isinstance(items, list) else []:
        if not isinstance(p, dict):
            continue
        kind, fmt = p.get("kind"), p.get("format")
        name = " ".join(str(p.get("name") or "").split())[:40]
        pid = str(p.get("id") or "")
        if kind not in FORMATS or fmt not in FORMATS[kind] or not name or not pid.isalnum() or pid in ids:
            continue
        quality = p.get("quality") if p.get("quality") in QUALITIES else "balanced"
        size = p.get("size") if p.get("size") in sizes(kind) else "original"
        out.append({"id": pid, "name": name, "kind": kind, "format": fmt, "quality": quality,
                    "size": size if kind != "audio" else "original"})
        ids.add(pid)
    return out[:MAX]


def use(items):
    """What the wheel and the menu show from now on."""
    CURRENT[:] = clean(items)


def new_id() -> str:
    return os.urandom(4).hex()


def describe(p: dict) -> str:
    """"MP4 · Smaller file · Up to HD (720p)"."""
    from .i18n import is_rtl, ltr
    bits = [ltr(p["format"][1:].upper()), tr(QUALITIES[p["quality"]])]
    if p["kind"] != "audio" and p["size"] != "original":
        bits.append(tr(sizes(p["kind"])[p["size"]]))
    return ("\u200f" if is_rtl() else "") + "  ·  ".join(f"\u2068{b}\u2069" if is_rtl() else b for b in bits)


def tools() -> dict[str, list[Tool]]:
    """Each preset as a tool, for the kinds of files it's for (a sound preset is on videos too: it saves their
    sound)."""
    out: dict[str, list[Tool]] = {}
    for p in CURRENT:
        from .converters import check_has_audio
        tool = Tool(f"preset:{p['id']}", p["name"], tr("{name}: {what}", name=p["name"], what=describe(p)),
                    partial(run, preset=dict(p)))
        out.setdefault(p["kind"], []).append(tool)
        if p["kind"] == "audio":
            out.setdefault("video", []).append(dataclasses.replace(tool, check=check_has_audio, slow_check=True))
    return out


def audio_args(ext: str, quality: str) -> list[str]:
    """FFmpeg's settings for a sound file this good (Balanced is what the Converter always made)."""
    from .converters import AUDIO_ARGS
    rates = {".mp3": {"high": ["-b:a", "320k"], "smaller": ["-b:a", "128k"], "smallest": ["-b:a", "96k"]},
             ".m4a": {"high": ["-b:a", "256k"], "smaller": ["-b:a", "128k"], "smallest": ["-b:a", "96k"]},
             ".ogg": {"high": ["-q:a", "8"], "smaller": ["-q:a", "3"], "smallest": ["-q:a", "1"]},
             ".opus": {"high": ["-b:a", "192k"], "smaller": ["-b:a", "96k"], "smallest": ["-b:a", "64k"]}}
    args = list(AUDIO_ARGS[ext])
    new = rates.get(ext, {}).get(quality)
    if new:
        for flag in ("-q:a", "-b:a"):
            if flag in args:
                i = args.index(flag)
                del args[i:i + 2]
        args += new
    return args


def run(src: Path, ctx: Ctx, preset: dict) -> Result:
    from . import converters as cv
    from .settings import VIDEO_CRF
    ext, kind, name = preset["format"], preset["kind"], preset["name"]
    out = ctx.out_path(src, f"{ctx.stem(src)} ({name}){ext}")
    if kind == "audio":
        cv.run_ffmpeg(src, out, audio_args(ext, preset["quality"]), ctx)
        return Result([out])
    size = preset["size"]
    if kind == "video":
        options = dataclasses.replace(ctx.options, video_crf=VIDEO_CRF.get(preset["quality"], 20),
                                      video_max=int(size) if size.isdigit() else 0)
        args = cv.VIDEO_ARGS[ext]
        if ext == ".mkv":  # (made again at this quality, not copied as it is)
            args = cv._PICK + cv._H264 + ["-crf", "20"] + cv._AAC
        cv.run_ffmpeg(src, out, cv.tuned_video(src, ext, args, options), ctx)
        return Result([out])
    ctx.options = dataclasses.replace(ctx.options, jpg_quality=IMAGE_QUALITY[preset["quality"]])
    most = int(size) if size.isdigit() else 0
    if ext == ".avif" and not cv.avif_supported():
        raise RuntimeError(tr("This copy of Corefold can't write AVIF"))
    im = cv.load_image(src, fit=most)
    if most and max(im.size) > most:
        from PIL import Image
        im.thumbnail((most, most), Image.LANCZOS)
    cv.save_image(im, out, ext, ctx)
    return Result([out])
