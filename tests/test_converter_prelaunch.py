"""Found in the Converter's check before the first launch: a video that's already H.264 is copied into an MP4
instead of being made again (only when nothing would be lost or left unplayable), a preset's name can't break
the new file's name, a PDF's own buttons aren't listed under Archive, and Trim's times read like a clock."""
import pytest

from corefold import converters, presets
from corefold.core import Ctx, Options

H264 = {"duration": 60, "vcodec": "h264", "pix_fmt": "yuv420p", "acodecs": ["aac"], "width": 1920, "height": 1080,
        "hdr": False, "subs": []}


@pytest.fixture
def video(monkeypatch, tmp_path):
    def make(name="clip.mkv", **info):
        monkeypatch.setattr(converters, "probe", lambda src: {**H264, **info})
        return tmp_path / name
    return make


def test_an_ordinary_h264_video_is_copied(video):
    assert converters.can_copy(video("obs.mkv"), ".mp4", Options())
    assert converters.can_copy(video("phone.mov"), ".mp4", Options())
    assert converters.can_copy(video("clip.flv", acodecs=["mp3"]), ".mov", Options())
    assert converters.can_copy(video("silent.mkv", acodecs=[]), ".mp4", Options())


@pytest.mark.parametrize("name, ext, info, options", [
    ("a.mkv", ".mp4", {"vcodec": "hevc"}, Options()),                 # (made H.264, which plays everywhere)
    ("a.mkv", ".mp4", {"pix_fmt": "yuv420p10le"}, Options()),         # (10-bit H.264 plays on few TVs and phones)
    ("a.mkv", ".mp4", {"hdr": True}, Options()),                      # (HDR is turned into normal colours)
    ("a.mkv", ".mp4", {"acodecs": ["aac", "opus"]}, Options()),       # (every sound track must fit as it is)
    ("a.avi", ".mp4", {}, Options()),                                 # (an AVI's times can be off)
    ("a.mkv", ".webm", {}, Options()),                                # (WebM can't hold H.264)
    ("a.mkv", ".mkv", {}, Options()),
    ("a.mp4", ".mp4", {}, Options()),
    ("a.mkv", ".mp4", {}, Options(video_crf=28)),                     # (Settings ask for smaller files)
    ("a.mkv", ".mp4", {}, Options(video_max=720)),                    # (and smaller than 1080p)
])
def test_made_again_when_copying_would_lose_or_break_something(video, name, ext, info, options):
    assert not converters.can_copy(video(name, **info), ext, options)


def test_a_size_limit_the_video_is_already_under_still_copies(video):
    assert converters.can_copy(video(width=1280, height=720), ".mp4", Options(video_max=1080))


def test_copy_failure_falls_back_to_making_it_again(video, monkeypatch, tmp_path):
    src = video()
    src.write_bytes(b"x")
    tried = []

    def fake(s, out, args, ctx, **kw):
        tried.append("copy" if "copy" in args else "again")
        if "copy" in args:
            raise RuntimeError("nope")
        out.write_bytes(b"made")
    monkeypatch.setattr(converters, "run_ffmpeg", fake)
    r = converters.ffmpeg_convert(src, Ctx(Options(output_folder=tmp_path)), ".mp4", converters.VIDEO_ARGS[".mp4"])
    assert tried == ["copy", "again"] and r.outputs[0].read_bytes() == b"made"


def test_a_preset_name_cant_break_the_file_name(monkeypatch, tmp_path):
    src = tmp_path / "song.mp3"
    src.write_bytes(b"x")
    made = []
    monkeypatch.setattr(converters, "run_ffmpeg", lambda s, out, args, ctx, **kw: made.append(out))
    monkeypatch.setattr(converters, "probe", lambda s: {"cover": -1})
    preset = {"name": "Phone: small/v2", "kind": "audio", "format": ".m4a", "quality": "smaller"}
    out = presets.run(src, Ctx(Options()), preset).outputs[0]
    assert out.parent == tmp_path and out.name == "song (Phone_ small_v2).m4a" and made == [out]


def test_pdf_buttons_arent_under_archive():
    from corefold.wheel import GROUPS
    assert {GROUPS[a] for a in ("merge", "split", "lock", "unlock")} == {"edit"}
    assert GROUPS["sound"] == "convert" and GROUPS["zip"] == "archive"


def test_trim_times_read_like_a_clock(qtbot):
    from corefold.dialogs import TimeBox, parse_time
    assert parse_time("1:02:03.5") == 3723.5 and parse_time("2:03,5") == 123.5 and parse_time("90") == 90
    assert parse_time("1:60") is None and parse_time("x") is None and parse_time("1::2") is None
    box = TimeBox()
    qtbot.addWidget(box)
    box.setRange(0, 7200)
    box.setValue(4530.2)
    assert box.text() == "1:15:30.2"
    box.lineEdit().setText("12:00")
    box.interpretText()
    assert box.value() == 720
