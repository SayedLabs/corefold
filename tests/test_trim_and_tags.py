"""Trim: take a part out of the middle, and the fast trim that copies the parts as they are. Song details:
read and changed in the song itself, the sound copied bit for bit."""
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from corefold import audiotags as at
from corefold import converters as cv
from corefold.core import Ctx, Options

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") and not cv.app_dir().joinpath("vendor", "ffmpeg").exists(),
                                  reason="FFmpeg isn't here")


def _ctx(tmp_path: Path, **params) -> Ctx:
    options = Options(output_folder=tmp_path / "out")
    options.output_folder.mkdir(exist_ok=True)
    return Ctx(options, lambda f: None, threading.Event(), params)


def _make(args: list[str], out: Path) -> Path:
    subprocess.run([cv.find_tool("ffmpeg"), "-loglevel", "error", "-y", *args, str(out)], check=True)
    return out


def _video(tmp_path: Path, name="clip.mp4", seconds=6) -> Path:
    # (a key picture every second, so a fast cut lands near the time picked)
    return _make(["-f", "lavfi", "-i", f"testsrc=s=320x240:d={seconds}:r=25", "-f", "lavfi", "-i",
                  f"sine=d={seconds}", "-c:v", "libx264", "-g", "25", "-pix_fmt", "yuv420p", "-c:a", "aac",
                  "-shortest"], tmp_path / name)


def _song(tmp_path: Path, ext: str, **tags) -> Path:
    codec = {".mp3": "libmp3lame", ".m4a": "aac", ".flac": "flac", ".ogg": "libvorbis", ".opus": "libopus"}[ext]
    meta = [x for k, v in tags.items() for x in ("-metadata", f"{k}={v}")]
    return _make(["-f", "lavfi", "-i", "sine=d=2", "-c:a", codec, *meta], tmp_path / f"song{ext}")


def _length(path: Path) -> float:
    cv.clear_cache() if hasattr(cv, "clear_cache") else None
    out = subprocess.run([cv.find_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json",
                          str(path)], capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])


def _sound_hash(path: Path) -> str:
    """The sound's own packets (not the details around them)."""
    out = subprocess.run([cv.find_tool("ffmpeg"), "-v", "error", "-i", str(path), "-map", "0:a", "-c", "copy",
                          "-f", "hash", "-hash", "md5", "-"], capture_output=True, text=True).stdout
    return out.strip()


def test_parts_left_after_taking_one_out():
    assert cv._encode_args(["-map", "0:V:0", "-map", "0:a:0?", "-sn", "-dn", "-vf", "pad", "-c:v", "libx264"]) == \
        ["-c:v", "libx264"]


@needs_ffmpeg
def test_take_a_part_out_of_a_video(tmp_path):
    src = _video(tmp_path)
    res = cv.trim_media(src, _ctx(tmp_path, start=2.0, end=4.0, mode="cut"))
    out = res.outputs[0]
    assert out.suffix == ".mp4" and "(cut)" in out.name
    assert abs(_length(out) - 4.0) < 0.3


@needs_ffmpeg
def test_take_the_start_away_is_a_plain_trim(tmp_path):
    src = _video(tmp_path)
    out = cv.trim_media(src, _ctx(tmp_path, start=0.0, end=2.0, mode="cut")).outputs[0]
    assert abs(_length(out) - 4.0) < 0.3


@needs_ffmpeg
def test_taking_everything_out_is_refused(tmp_path):
    src = _video(tmp_path)
    with pytest.raises(RuntimeError):
        cv.trim_media(src, _ctx(tmp_path, start=0.0, end=cv.media_duration(src), mode="cut"))


@needs_ffmpeg
def test_fast_trim_keeps_the_kind_and_the_quality(tmp_path):
    src = _video(tmp_path, "film.mkv")
    out = cv.trim_media(src, _ctx(tmp_path, start=1.0, end=4.0, fast=True)).outputs[0]
    assert out.suffix == ".mkv"
    assert 2.5 < _length(out) < 4.4  # (up to one key picture early: one a second here)
    codecs = subprocess.run([cv.find_tool("ffprobe"), "-v", "error", "-show_entries", "stream=codec_name", "-of",
                             "csv=p=0", str(out)], capture_output=True, text=True).stdout.split()
    assert "h264" in codecs and "aac" in codecs  # (copied, not made into something else)


@needs_ffmpeg
def test_fast_cut_joins_the_two_parts(tmp_path):
    src = _video(tmp_path, seconds=8)
    out = cv.trim_media(src, _ctx(tmp_path, start=2.0, end=5.0, mode="cut", fast=True)).outputs[0]
    assert out.suffix == ".mp4"
    assert 4.5 < _length(out) < 7.4


@needs_ffmpeg
def test_cut_a_part_out_of_a_song(tmp_path):
    src = _song(tmp_path, ".mp3")
    out = cv.trim_media(src, _ctx(tmp_path, start=0.5, end=1.0, mode="cut")).outputs[0]
    assert out.suffix == ".mp3" and abs(_length(out) - 1.5) < 0.2
    fast = cv.trim_media(src, _ctx(tmp_path, start=0.5, end=1.0, mode="cut", fast=True)).outputs[0]
    assert abs(_length(fast) - 1.5) < 0.2


def test_only_songs_have_details():
    assert at.check_tags([Path("a.mp3"), Path("b.flac")]) is None
    assert at.check_tags([Path("a.wav")])


@needs_ffmpeg
@pytest.mark.parametrize("ext", [".mp3", ".m4a", ".flac", ".ogg", ".opus"])
def test_change_a_songs_details(tmp_path, ext):
    src = _song(tmp_path, ext, title="Old", artist="Someone", album="Album")
    before = _sound_hash(src)
    found = at.read_tags(src)
    assert found["title"] == "Old" and found["artist"] == "Someone"
    res = at.write_tags(src, _ctx(tmp_path, tags={"title": "أغنية جديدة", "date": "2024", "album": ""},
                                  numbers={}, cover=None))
    assert res.outputs == [src]
    after = at.read_tags(src)
    assert after["title"] == "أغنية جديدة" and after["date"] == "2024"
    assert after["artist"] == "Someone"  # (left as it was)
    assert after["album"] == ""          # (an emptied box takes it away)
    assert _sound_hash(src) == before    # (the sound itself didn't change)
    assert not list(tmp_path.glob(".*corefold-tags*"))


@needs_ffmpeg
def test_a_song_that_comes_out_shorter_is_left_as_it_was(tmp_path, monkeypatch):
    """The sound is only copied: a new copy that's shorter means writing went wrong, and the song stays."""
    src = _song(tmp_path, ".mp3", title="Old")
    before = src.read_bytes()
    real = at.probe

    def shorter(path):
        info = dict(real(path))
        if ".corefold-tags" in path.name:
            info["duration"] = info["duration"] / 2
        return info
    monkeypatch.setattr(at, "probe", shorter)
    with pytest.raises(RuntimeError, match="shorter"):
        at.write_tags(src, _ctx(tmp_path, tags={"title": "New"}, numbers={}, cover=None))
    assert src.read_bytes() == before
    assert not list(tmp_path.glob(".*corefold-tags*"))


@needs_ffmpeg
@pytest.mark.parametrize("ext", [".mp3", ".m4a", ".flac"])
def test_put_a_cover_in_and_take_it_out(tmp_path, ext):
    from PIL import Image
    src = _song(tmp_path, ext, title="Song")
    pic = tmp_path / "cover.webp"
    Image.new("RGB", (2000, 1500), (200, 30, 30)).save(pic)
    at.write_tags(src, _ctx(tmp_path, tags={}, numbers={}, cover=str(pic)))
    assert at.read_tags(src)["cover"] and at.read_tags(src)["title"] == "Song"
    at.write_tags(src, _ctx(tmp_path, tags={"artist": "X"}, numbers={}, cover=None))
    assert at.read_tags(src)["cover"]  # (changing other details keeps the cover)
    at.write_tags(src, _ctx(tmp_path, tags={}, numbers={}, cover="remove"))
    assert not at.read_tags(src)["cover"] and at.read_tags(src)["artist"] == "X"


@needs_ffmpeg
def test_number_an_album(tmp_path):
    songs = []
    for name in ("b", "a"):
        s = _song(tmp_path, ".mp3")
        songs.append(s.rename(tmp_path / f"{name}.mp3"))
    numbers = {str(tmp_path / "a.mp3"): 1, str(tmp_path / "b.mp3"): 2}
    for s in songs:
        at.write_tags(s, _ctx(tmp_path, tags={}, numbers=numbers, cover=None))
    assert at.read_tags(tmp_path / "a.mp3")["track"] == "1"
    assert at.read_tags(tmp_path / "b.mp3")["track"] == "2"


def test_details_tool_is_in_the_converter():
    tools = {t.key: t for t in cv.catalog()["audio"]}
    assert "audio:tags" in tools and tools["audio:tags"].ask == "tags" and not tools["audio:tags"].renames
