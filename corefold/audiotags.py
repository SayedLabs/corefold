"""A song's details (title, artist, album, year, track, genre and album cover), read and changed in the file
itself, the way music players show them. The sound is copied as it is (nothing made again), so not a bit of
it changes. No GUI code here."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from .converters import LOCAL_ONLY, Ctx, Result, find_tool, norm, probe, run_ffmpeg
from .core import NO_WINDOW
from .i18n import tr

log = logging.getLogger("corefold")

# the kinds of sound file whose details can be changed, and the ones that can hold an album cover
TAG_EXT = {".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus"}
COVER_EXT = {".mp3", ".m4a", ".m4b", ".flac"}
# Ogg files (Vorbis, Opus) keep their details with the sound track, the others in the file as a whole
_TRACK_TAGS = {".ogg", ".opus"}
# what can be changed (FFmpeg's names), in the order shown (the window has their labels)
FIELDS = (("title", "Title"), ("artist", "Artist"), ("album", "Album"), ("album_artist", "Album artist"),
          ("date", "Year"), ("track", "Track"), ("genre", "Genre"), ("comment", "Comment"))
# other spellings files use for the same details (read only: they're written under FFmpeg's names)
_ALIASES = {"albumartist": "album_artist", "album artist": "album_artist", "year": "date",
            "tracknumber": "track", "description": "comment"}
COVER_MAX = 1400  # a picture bigger than this (either side) is made smaller for the cover


def check_tags(files: list[Path]) -> str | None:
    if all(norm(f.suffix) in TAG_EXT for f in files):
        return None
    return tr("Only MP3, M4A, FLAC, OGG and Opus files have song details")


def read_tags(src: Path) -> dict:
    """The song's details (FFmpeg's names, "" for none) and "cover": whether it has an album cover."""
    found = {key: "" for key, _ in FIELDS}
    try:
        out = subprocess.run(
            [find_tool("ffprobe"), "-v", "error", "-show_entries", "format_tags:stream=codec_type:stream_tags"
             ":stream_disposition=attached_pic", "-of", "json", *LOCAL_ONLY, str(src)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            stdin=subprocess.DEVNULL, creationflags=NO_WINDOW).stdout
        data = json.loads(out or "{}")
    except (ValueError, OSError, subprocess.SubprocessError, RuntimeError):
        data = {}
    streams = data.get("streams") or []
    sound = next((s for s in streams if s.get("codec_type") == "audio"), {})
    # (the whole file's details win over the sound track's, as players read them)
    for tags in ((sound.get("tags") or {}), (data.get("format") or {}).get("tags") or {}):
        for key, value in tags.items():
            key = key.lower()
            key = _ALIASES.get(key, key)
            if key in found and isinstance(value, str) and value.strip():
                found[key] = value.strip()
    found["cover"] = any((s.get("disposition") or {}).get("attached_pic") for s in streams)
    return found


def _cover_file(picture: Path, folder: Path) -> Path:
    """The picture as a JPEG or PNG every player reads (any other kind, or a huge one, is made into a JPEG)."""
    from PIL import Image

    from .converters import setup_pillow
    setup_pillow()
    with Image.open(picture) as im:
        if norm(picture.suffix) in (".jpg", ".png") and max(im.size) <= COVER_MAX:
            return picture
        im = im.convert("RGB")
        im.thumbnail((COVER_MAX, COVER_MAX))
        out = folder / "cover.jpg"
        im.save(out, "JPEG", quality=90)
        return out


def write_tags(src: Path, ctx: Ctx) -> Result:
    """Changes the song's details in the file itself. params: "tags" {name: new text ("" takes it away)} with
    only the details to change, "numbers" {file path: its track number} (numbering several songs),
    "cover": a picture's path, or "remove" to take the cover away (left out: the cover stays as it is)."""
    ext = norm(src.suffix)
    if ext not in TAG_EXT:
        raise RuntimeError(check_tags([src]) or "")
    p = ctx.params
    tags = {k: str(v).strip() for k, v in (p.get("tags") or {}).items() if k in dict(FIELDS)}
    number = (p.get("numbers") or {}).get(str(src))
    if number:
        tags["track"] = str(number)
    cover = p.get("cover")
    if cover and ext not in COVER_EXT:
        cover = None  # (Ogg files can't hold one: their other details still change)
    if not tags and not cover:
        return Result([src], note=tr("Nothing to change"))
    found = probe(src).get("cover", -1)
    has_cover = found is not None and found >= 0
    with tempfile.TemporaryDirectory(prefix="corefold-tags-") as tmp:
        args = ["-map", "0:a"]
        more = []
        if cover and cover != "remove":
            more = [_cover_file(Path(cover), Path(tmp))]
            args += ["-map", "1:0", "-disposition:v:0", "attached_pic"]
        elif cover != "remove" and has_cover:
            args += ["-map", "0:v"]  # (the album cover that's there stays)
        args += ["-c", "copy", "-map_metadata", "0"]
        if ext in _TRACK_TAGS:
            args += ["-map_metadata:s:a", "0:s:a"]
            args += [x for k, v in tags.items() for x in ("-metadata:s:a:0", f"{k}={v}")]
        else:
            args += [x for k, v in tags.items() for x in ("-metadata", f"{k}={v}")]
        if ext == ".mp3":
            args += ["-id3v2_version", "3"]  # (the version Windows Explorer and every player read)
        if ext in (".m4a", ".m4b"):
            args += ["-f", "ipod" if ext == ".m4a" else "mp4", "-movflags", "+faststart"]
        # made next to the song (so the swap is instant and can't half-happen), then put in its place
        new = src.with_name(f".{src.stem}.corefold-tags{src.suffix}")
        try:
            run_ffmpeg(src, new, args, ctx, more_inputs=more or None)
            before = probe(src).get("duration") or 0.0
            if before > 1 and (probe(new).get("duration") or 0.0) < before * 0.98:
                # (the sound is only copied, so the song is exactly as long as before: if it isn't, something went
                # wrong while writing, and the song itself stays)
                raise RuntimeError(tr("{name} came out shorter than before, so it was left as it was",
                                      name=src.name))
            stat = src.stat()
            os.replace(new, src)
        except PermissionError:
            raise RuntimeError(tr("{name} is in use: close the program playing it, then try again",
                                  name=src.name)) from None
        finally:
            if new.exists():
                try:
                    new.unlink()
                except OSError:
                    pass
    try:  # (the song keeps its date, so folders sorted by date stay in the same order)
        os.utime(src, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    except OSError:
        pass
    from . import core
    for key in [k for k in list(core._cache) if k[1] in (str(src), str(new))]:  # (what was read about it before is old now)
        core._cache.pop(key, None)
    return Result([src])
