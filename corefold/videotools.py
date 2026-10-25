"""More video and sound tools: crop the picture, subtitles in and out, the sound in any format, and
joining several files into one. No GUI code here."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .converters import (_AAC, _H264, _PICK, AUDIO_ARGS, LOCAL_ONLY, Ctx, Result, find_tool, media_duration, norm,
                         probe, run_ffmpeg, tr)
from .core import NO_WINDOW, _cached, legacy_text

log = logging.getLogger("corefold")

SUB_EXT = (".srt", ".ass", ".ssa", ".vtt")
# how to crop the picture (English, shown through tr())
CROPS = {"bars": "Remove black bars", "square": "Square (1:1)", "vertical": "Vertical (9:16), for Reels and TikTok",
         "wide": "Wide (16:9)"}
# the sound out of a video, in these formats
SOUND_FORMATS = {".mp3": "MP3", ".m4a": "M4A", ".wav": "WAV", ".flac": "FLAC", ".opus": "Opus"}
_MP4_OUT = _H264 + ["-crf", "20"] + _AAC + ["-movflags", "+faststart"]


# ----------------------------------------------------------------------------- crop

def black_bars(src: Path) -> tuple[int, int, int, int] | None:
    """The picture without its black bars (w, h, x, y), found by FFmpeg looking at a few moments of
    the video. None if there are none."""
    dur = media_duration(src)
    found: dict[tuple[int, int, int, int], int] = {}
    for at in ([dur * f for f in (0.2, 0.5, 0.8)] if dur > 20 else [0.0]):
        try:
            out = subprocess.run([find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-ss", f"{at:.2f}", *LOCAL_ONLY,
                                  "-i", str(src), "-t", "4", "-vf", "cropdetect=limit=24:round=2:reset=0",
                                  "-an", "-sn", "-f", "null", "-"],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                                 stdin=subprocess.DEVNULL, creationflags=NO_WINDOW).stderr
        except (OSError, subprocess.SubprocessError):
            continue
        last = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", out)
        if last:
            box = tuple(int(v) for v in last[-1])
            found[box] = found.get(box, 0) + 1
    if not found:
        return None
    w, h, x, y = max(found, key=lambda b: (found[b], b[0] * b[1]))  # (the most common, then the biggest)
    info = probe(src)
    if w <= 0 or h <= 0 or (info["width"] and w >= info["width"] - 4 and h >= info["height"] - 4):
        return None
    return w, h, x, y


def crop_filter(how: str, width: int, height: int) -> str:
    """FFmpeg's crop for a shape, from the middle of the picture."""
    ratio = {"square": 1.0, "vertical": 9 / 16, "wide": 16 / 9}[how]
    if width / max(1, height) > ratio:  # too wide: cut the sides
        w, h = round(height * ratio), height
    else:                               # too tall: cut the top and bottom
        w, h = width, round(width / ratio)
    w, h = max(2, w - w % 2), max(2, h - h % 2)
    return f"crop={w}:{h}:{(width - w) // 2}:{(height - h) // 2}"


def crop_video(src: Path, ctx: Ctx) -> Result:
    how = ctx.params.get("how", "bars")
    if how == "bars":
        box = black_bars(src)
        if box is None:
            raise RuntimeError(tr("this video has no black bars to remove"))
        vf = "crop={}:{}:{}:{}".format(*box)
        name = tr("no bars")
    else:
        info = probe(src)
        if not info["width"] or not info["height"]:
            raise RuntimeError(tr("nothing to convert in this file"))
        vf = crop_filter(how, info["width"], info["height"])
        name = {"square": "1x1", "vertical": "9x16", "wide": "16x9"}[how]
    out = ctx.out_path(src, f"{ctx.stem(src)} ({name}).mp4")
    run_ffmpeg(src, out, _PICK + ["-vf", vf + ",setsar=1"] + _MP4_OUT, ctx)
    return Result([out])


# ----------------------------------------------------------------------------- subtitles

def subtitles_next_to(video: Path) -> Path | None:
    """A subtitle file with the video's name ("Film.srt", "Film.en.srt"), if there is one."""
    try:
        for p in sorted(video.parent.iterdir()):
            if p.suffix.lower() in SUB_EXT and (p.stem == video.stem or p.stem.startswith(video.stem + ".")):
                return p
    except OSError:
        pass
    return None


def subtitle_text(path: Path) -> str:
    """A subtitle file's text, whatever it was saved as (Arabic ones are often Windows-1256)."""
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:  # (cp1256 accepts any bytes, so the letters decide: see legacy_text)
        return legacy_text(raw)


def add_subtitles(src: Path, ctx: Ctx) -> Result:
    """Puts a subtitle file into the video: drawn into the picture ("burn", plays everywhere, phones and
    TVs too) or as a track players can turn on and off."""
    sub = Path(ctx.params.get("subtitles") or "") if ctx.params.get("subtitles") else subtitles_next_to(src)
    if sub is None or not sub.is_file():
        raise RuntimeError(tr("pick a subtitle file (.srt) to put in"))
    burn = ctx.params.get("how", "burn") == "burn"
    folder = Path(tempfile.mkdtemp(prefix="corefold-subs-"))
    try:
        # (FFmpeg's subtitle drawing can't take most Windows paths: it reads a copy by a plain name)
        local = folder / ("subs" + sub.suffix.lower())
        local.write_text(subtitle_text(sub), "utf-8")
        if burn:
            style = "force_style='FontName=Segoe UI,FontSize=22,Outline=2,Shadow=0'"
            args = _PICK + ["-vf", f"subtitles={local.name}:charenc=UTF-8:{style},pad=ceil(iw/2)*2:ceil(ih/2)*2"] \
                + _MP4_OUT
            out = ctx.out_path(src, f"{ctx.stem(src)} (subtitles).mp4")
            run_ffmpeg(src, out, args, ctx, cwd=folder)
        else:
            out = ctx.out_path(src, f"{ctx.stem(src)} (subtitles).mkv")
            args = ["-map", "0:v", "-map", "0:a?", "-map", "1:0", "-c", "copy", "-c:s", "srt" if local.suffix in
                    (".srt", ".vtt") else "ass", "-disposition:s:0", "default"]
            run_ffmpeg(src, out, args, ctx, more_inputs=[local])
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return Result([out])


def subtitle_tracks(src: Path) -> list[str]:
    """The codecs of the subtitles inside a video ("subrip", "ass", "hdmv_pgs_subtitle" …). Remembered until
    the file changes: the wheel asks every time it opens over a video."""
    def work():
        try:
            out = subprocess.run([find_tool("ffprobe"), "-v", "error", "-select_streams", "s", "-show_entries",
                                  "stream=codec_name", "-of", "csv=p=0", *LOCAL_ONLY, str(src)],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                                 stdin=subprocess.DEVNULL, creationflags=NO_WINDOW).stdout
        except (OSError, subprocess.SubprocessError, RuntimeError):
            return []
        return [line.strip() for line in out.splitlines() if line.strip()]
    return list(_cached("subs", src, work))


PICTURE_SUBS = ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub")


def check_has_subtitles(files: list[Path]) -> str | None:
    tracks = subtitle_tracks(files[0])
    if not tracks:
        return tr("This video has no subtitles inside")
    if all(t in PICTURE_SUBS for t in tracks):
        return tr("Its subtitles are pictures, not text")
    return None


def save_subtitles(src: Path, ctx: Ctx) -> Result:
    """Every text subtitle inside the video as its own .srt file."""
    tracks = subtitle_tracks(src)
    outs = []
    n = sum(1 for t in tracks if t not in PICTURE_SUBS)
    for i, codec in enumerate(tracks):
        if codec in PICTURE_SUBS:
            continue
        ctx.check()
        tag = "" if n == 1 else f".{len(outs) + 1}"
        out = ctx.out_path(src, f"{ctx.stem(src)}{tag}.srt")
        run_ffmpeg(src, out, ["-map", f"0:s:{i}", "-c:s", "srt"], ctx)
        outs.append(out)
    if not outs:
        raise RuntimeError(tr("Its subtitles are pictures, not text"))
    return Result(outs)


# ----------------------------------------------------------------------------- sound

def video_sound(src: Path, ctx: Ctx) -> Result:
    ext = ctx.params.get("format", ".mp3")
    if ext not in SOUND_FORMATS:
        ext = ".mp3"
    out = ctx.out_path(src, f"{ctx.stem(src)}{ext}")
    run_ffmpeg(src, out, ["-map", "0:a:0"] + AUDIO_ARGS[ext], ctx)
    return Result([out])


# ----------------------------------------------------------------------------- joining

def check_join(files: list[Path]) -> str | None:
    if len(files) < 2:
        return tr("Drop 2 or more files to join them")
    from .converters import category
    kinds = {category(f) for f in files}
    if len(kinds) > 1:
        return tr("Pick only videos, or only sound files")
    return None


def join_media(files: list[Path], ctx: Ctx) -> Result:
    """Several videos (or sound files) one after the other in one file, in the order they were picked.
    Videos of different sizes are fitted into the first one's size with black edges."""
    from .converters import category
    files = list(files)
    video = category(files[0]) == "video"
    infos = [probe(f) for f in files]
    total = sum(i["duration"] for i in infos)
    parts = []
    if video:
        w, h = infos[0]["width"] or 1280, infos[0]["height"] or 720
        w, h = w - w % 2, h - h % 2
        for n, info in enumerate(infos):
            parts.append(f"[{n}:V:0]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                         f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p[v{n}]")
            if info["audio"]:
                parts.append(f"[{n}:a:0]aresample=48000,aformat=channel_layouts=stereo[a{n}]")
            else:  # (a video without sound gets silence, so the sound stays in step)
                parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={max(0.1, info['duration']):.3f}[a{n}]")
        chain = "".join(f"[v{n}][a{n}]" for n in range(len(files)))
        parts.append(f"{chain}concat=n={len(files)}:v=1:a=1[v][a]")
        args = ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]"] + _MP4_OUT
        ext = ".mp4"
    else:
        ext = norm(files[0].suffix)
        if ext not in AUDIO_ARGS:
            ext = ".mp3"
        for n in range(len(files)):
            parts.append(f"[{n}:a:0]aresample=48000,aformat=channel_layouts=stereo[a{n}]")
        parts.append("".join(f"[a{n}]" for n in range(len(files))) + f"concat=n={len(files)}:v=0:a=1[a]")
        args = ["-filter_complex", ";".join(parts), "-map", "[a]"] + AUDIO_ARGS[ext]
    out = ctx.out_path(files[0], f"{ctx.stem(files[0])} (joined){ext}")
    run_ffmpeg(files[0], out, args, ctx, duration=total, more_inputs=files[1:])
    return Result([out])
