"""The Converter keeps what's inside a file: every sound track and the subtitles a film carries (as far
as the new kind of file can hold them), a song's album cover, and a picture's see-through parts in Small.
When an unusual track can't be kept, the file is still made, without it."""

import pytest
from PIL import Image, ImageDraw

from corefold import converters
from corefold.core import Ctx, Options


@pytest.fixture
def media(monkeypatch, tmp_path):
    def make(**info):
        monkeypatch.setattr(converters, "probe", lambda src: {"duration": 5, **info})
        return tmp_path / "film.mkv"
    return make


def test_every_sound_track_and_the_text_subtitles(media):
    src = media(subs=["subrip", "hdmv_pgs_subtitle", "ass"])
    args = converters.all_tracks(src, ".mp4", converters.VIDEO_ARGS[".mp4"])
    assert "0:a?" in args and "0:a:0?" not in args and "-sn" not in args
    # (pictures can't go into an MP4: only the two text ones, turned into MP4's own kind)
    assert args[-8:] == ["-map", "0:s:0", "-map", "0:s:2", "-c:s:0", "mov_text", "-c:s:1", "mov_text"]
    webm = converters.all_tracks(src, ".webm", converters.VIDEO_ARGS[".webm"])
    assert webm.count("webvtt") == 2


def test_mkv_holds_every_subtitle(media):
    src = media(subs=["mov_text", "hdmv_pgs_subtitle", "dvb_teletext"])
    args = converters.all_tracks(src, ".mkv", converters.VIDEO_ARGS[".mkv"])
    assert ["-map", "0:s:0", "-map", "0:s:1"] == args[-8:-4]
    assert args[-4:] == ["-c:s:0", "srt", "-c:s:1", "copy"]  # (MP4's kind becomes SRT; the rest is copied)


def test_nothing_to_add_leaves_the_arguments(media):
    src = media(subs=[])
    mkv = converters.VIDEO_ARGS[".mkv"]
    assert converters.all_tracks(src, ".mkv", mkv) is mkv


def test_album_cover(media):
    src = media(cover=0)
    args = converters.with_cover(src, ".mp3", converters.AUDIO_ARGS[".mp3"])
    assert "-vn" not in args and ["-map", "0:v:0"] == args[2:4] and "attached_pic" in args
    assert args[-2:] == ["-id3v2_version", "3"]
    ogg = converters.AUDIO_ARGS[".ogg"]
    assert converters.with_cover(src, ".ogg", ogg) is ogg  # (OGG can't hold one)


def test_no_cover_from_a_video(media):
    src = media(cover=-1, video=True)
    mp3 = converters.AUDIO_ARGS[".mp3"]
    assert converters.with_cover(src, ".mp3", mp3) is mp3


def test_made_again_without_the_extra_tracks(media, monkeypatch, tmp_path):
    src = media(subs=["subrip"])
    src.write_bytes(b"x")
    tried = []

    def fake(s, out, args, ctx, *a, **k):
        tried.append(args)
        if "-map" in args and "0:s:0" in args:
            raise RuntimeError("Subtitle encoding failed")
        out.write_bytes(b"made")
    monkeypatch.setattr(converters, "run_ffmpeg", fake)
    ctx = Ctx(Options())
    result = converters.ffmpeg_convert(src, ctx, ".mp4", converters.VIDEO_ARGS[".mp4"])
    assert len(tried) == 2 and result.outputs[0].read_bytes() == b"made"


def test_small_keeps_see_through_parts(tmp_path):
    logo = Image.new("RGBA", (2400, 1200), (0, 0, 0, 0))
    ImageDraw.Draw(logo).ellipse((300, 300, 900, 900), fill=(200, 30, 30, 255))
    logo.save(tmp_path / "logo.png")
    Image.new("RGBA", (2400, 1200), (10, 120, 200, 255)).save(tmp_path / "solid.png")
    ctx = Ctx(Options())
    out = converters.image_small(tmp_path / "logo.png", ctx).outputs[0]
    assert out.suffix == ".png"
    with Image.open(out) as im:
        assert im.size == (1920, 960) and im.getpixel((0, 0))[3] == 0
    assert converters.image_small(tmp_path / "solid.png", ctx).outputs[0].suffix == ".jpg"  # (solid: still a JPG)
