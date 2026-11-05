"""The Converter's "Video codec" setting (H.265 and AV1 next to H.264), JPEG XL pictures and the smaller PNG with
not a dot changed."""
import shutil
import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from corefold import converters as cv
from corefold import presets
from corefold.core import Ctx, Options
from corefold.settings import VIDEO_CODECS, Settings


def _ctx(tmp_path: Path, **opts) -> Ctx:
    options = Options(output_folder=tmp_path / "out", **opts)
    options.output_folder.mkdir(exist_ok=True)
    return Ctx(options, lambda f: None, threading.Event(), {})


def test_with_codec_swaps_the_encoder_and_matches_the_quality():
    mp4 = cv.VIDEO_ARGS[".mp4"]
    h265 = cv.with_codec(mp4, "h265", ".mp4")
    assert "libx265" in h265 and "libx264" not in h265 and h265[h265.index("-crf") + 1] == "24"
    assert h265[h265.index("-tag:v") + 1] == "hvc1"
    av1 = cv.with_codec(mp4, "av1", ".mp4")
    assert "libsvtav1" in av1 and av1[av1.index("-crf") + 1] == "30"
    assert "libx265" in cv.with_codec(mp4, "av1", ".mov")  # (MOV can't hold AV1)
    assert cv.with_codec(h265, "h264", ".mp4") == mp4
    assert cv.with_codec(mp4, "h264", ".mp4") is mp4
    assert cv.with_codec(cv.VIDEO_ARGS[".webm"], "h265", ".webm") is cv.VIDEO_ARGS[".webm"]


def test_graphics_card_versions_of_h265_and_av1():
    h265 = cv.with_codec(cv.VIDEO_ARGS[".mp4"], "h265", ".mp4")
    nv = cv.with_gpu(h265, "hevc_nvenc")
    assert nv[nv.index("-c:v") + 1] == "hevc_nvenc" and "libx265" not in nv and "-x265-params" not in nv
    assert nv[nv.index("-cq") + 1] == "22"  # (the same look as x264's 20)
    qsv = cv.with_gpu(cv.with_codec(cv.VIDEO_ARGS[".mp4"], "av1", ".mp4"), "av1_qsv")
    assert qsv[qsv.index("-c:v") + 1] == "av1_qsv" and "libsvtav1" not in qsv


def test_the_setting_reaches_the_converter_and_never_copies():
    s = Settings()
    assert s.video_codec == "h264" and set(VIDEO_CODECS) == {"h264", "h265", "av1"}
    s.video_codec = "av1"
    assert s.options().video_codec == "av1"
    assert not cv.can_copy(Path("a.mkv"), ".mp4", Options(video_codec="h265"))


def test_presets_keep_their_codec():
    kept = presets.clean([{"id": "a1", "name": "Small", "kind": "video", "format": ".mp4", "codec": "h265"},
                          {"id": "b2", "name": "Bad", "kind": "video", "format": ".mp4", "codec": "nope"},
                          {"id": "c3", "name": "Song", "kind": "audio", "format": ".mp3", "codec": "av1"}])
    assert [p["codec"] for p in kept] == ["h265", "h264", "h264"]
    assert "H.265" in presets.describe(kept[0])


def _ffmpeg_has(name: str) -> bool:
    try:
        return cv.has_encoder(name)
    except Exception:
        return False


@pytest.mark.skipif(not shutil.which("ffmpeg") and not cv.app_dir().joinpath("vendor", "ffmpeg").exists(),
                    reason="no FFmpeg here")
@pytest.mark.parametrize("codec,name", [("h265", "hevc"), ("av1", "av1")])
def test_a_real_video_in_h265_and_av1(tmp_path, codec, name):
    if not _ffmpeg_has(cv.SOFT_ENCODERS[codec]):
        pytest.skip(f"this FFmpeg has no {cv.SOFT_ENCODERS[codec]}")
    import subprocess
    src = tmp_path / "clip.mp4"
    subprocess.run([cv.find_tool("ffmpeg"), "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:d=1",
                    "-c:v", "libx264", str(src)], check=True)
    res = cv.ffmpeg_convert(src, _ctx(tmp_path, video_codec=codec), ".mp4", cv.VIDEO_ARGS[".mp4"])
    assert cv.probe(res.outputs[0]).get("vcodec") == name


def test_jpeg_xl(tmp_path):
    if not _ffmpeg_has("libjxl"):
        pytest.skip("this FFmpeg has no libjxl")
    im = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((10, 10, 100, 70), fill=(200, 30, 30, 255))
    im.save(tmp_path / "pic.png")
    res = cv.convert_jxl(tmp_path / "pic.png", _ctx(tmp_path))
    data = res.outputs[0].read_bytes()
    assert res.outputs[0].suffix == ".jxl" and (data[:2] == b"\xff\x0a" or data[4:8] == b"JXL ")


def test_smaller_png_changes_not_a_dot(tmp_path):
    im = Image.new("RGBA", (500, 400), (255, 255, 255, 255))
    d = ImageDraw.Draw(im)
    for i in range(30):
        d.rectangle((i * 15, i * 12, i * 15 + 40, i * 12 + 25), fill=(i * 8, 90, 200, 255 if i % 3 else 100))
    im.save(tmp_path / "shot.png", compress_level=1)
    res = cv.optimize_image(tmp_path / "shot.png", _ctx(tmp_path))
    assert res.outputs and res.outputs[0].stat().st_size < (tmp_path / "shot.png").stat().st_size
    a = np.asarray(Image.open(tmp_path / "shot.png").convert("RGBA"))
    b = np.asarray(Image.open(res.outputs[0]).convert("RGBA"))
    assert (a == b).all()
    assert (tmp_path / "shot.png").exists()


def test_smaller_png_only_for_png(tmp_path):
    assert cv.check_optimizable([tmp_path / "a.png"]) is None
    assert cv.check_optimizable([tmp_path / "a.jpg"])
    tools = {t.key for t in cv.catalog()["image"]}
    assert {"image:jxl", "image:optimize"} <= tools
