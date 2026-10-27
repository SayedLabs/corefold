"""Settings → Converter: Video quality and Video size. A video is made smaller (even sizes, same shape)
only when it's bigger than the size picked, never bigger, and the quality reaches x264, VP9 and the
graphics card alike."""

import pytest

from corefold import converters
from corefold.core import Options
from corefold.settings import VIDEO_CRF, VIDEO_QUALITIES, VIDEO_SIZES, Settings


@pytest.fixture
def video(monkeypatch, tmp_path):
    def make(w, h):
        monkeypatch.setattr(converters, "probe", lambda src: {"width": w, "height": h, "duration": 5})
        return tmp_path / "clip.mp4"
    return make


def test_smaller_never_bigger():
    assert converters.smaller_size(3840, 2160, 1080) == (1920, 1080)
    assert converters.smaller_size(1080, 1920, 720) == (720, 1280)  # (a phone video: its width is the short side)
    assert converters.smaller_size(1280, 720, 1080) is None
    assert converters.smaller_size(1000, 563, 480) == (852, 480)  # (even numbers)


def test_quality_and_size_reach_ffmpeg(video):
    src = video(3840, 2160)
    args = converters.tuned_video(src, ".mp4", converters.VIDEO_ARGS[".mp4"], Options(video_crf=24, video_max=1080))
    assert args[args.index("-crf") + 1] == "24"
    assert args[args.index("-vf") + 1] == f"{converters.DEINTERLACE},scale=1920:1080:flags=lanczos"
    gpu = converters.with_gpu(args, "h264_nvenc")
    assert "-cq" in gpu and gpu[gpu.index("-cq") + 1] == "26" and "-vf" in gpu
    webm = converters.tuned_video(src, ".webm", converters.VIDEO_ARGS[".webm"], Options(video_crf=18, video_max=720))
    assert webm[webm.index("-crf") + 1] == "28" and "scale=1280:720:flags=lanczos" in webm


def test_mkv_is_copied_unless_it_must_get_smaller(video):
    src = video(1920, 1080)
    same = converters.tuned_video(src, ".mkv", converters.VIDEO_ARGS[".mkv"], Options(video_max=1080))
    assert "copy" in same
    smaller = converters.tuned_video(src, ".mkv", converters.VIDEO_ARGS[".mkv"], Options(video_max=720))
    assert "copy" not in smaller and "libx264" in smaller and "scale=1280:720:flags=lanczos" in smaller


def test_defaults_change_nothing(video):
    src = video(3840, 2160)
    assert converters.tuned_video(src, ".mp4", converters.VIDEO_ARGS[".mp4"], Options()) == converters.VIDEO_ARGS[".mp4"]
    s = Settings()
    o = s.options()
    assert (o.video_crf, o.video_max) == (20, 0)
    assert set(VIDEO_CRF) == set(VIDEO_QUALITIES) and "original" in VIDEO_SIZES
    s.video_quality, s.video_size = "smallest", "720"
    o = s.options()
    assert (o.video_crf, o.video_max) == (28, 720)
