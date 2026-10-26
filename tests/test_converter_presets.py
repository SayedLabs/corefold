"""The Converter's presets and sound quality: presets kept in the settings are checked, show on the wheel of the
files they're for (a sound preset on videos too), and make a file with their quality and size and their name
after the original's. Sound quality changes MP3/M4A/OGG/Opus only."""
import shutil
import threading

import pytest
from PIL import Image

from corefold import converters, presets
from corefold.core import Ctx, Options
from corefold.settings import Settings


@pytest.fixture(autouse=True)
def no_presets():
    presets.use([])
    yield
    presets.use([])


def _p(**kw):
    return {"id": "a1", "name": "Mine", "kind": "video", "format": ".mp4", "quality": "smaller", "size": "720", **kw}


def test_only_sensible_presets_are_kept():
    kept = presets.clean([_p(), _p(id="a1"), _p(id="b2", format=".gif"), _p(id="../x"), _p(id="c3", name="  "),
                          _p(id="d4", quality="best", size="9999"), "nonsense",
                          _p(id="e5", kind="audio", format=".mp3", size="720")])
    assert [p["id"] for p in kept] == ["a1", "d4", "e5"]
    assert kept[1]["quality"] == "balanced" and kept[1]["size"] == "original"
    assert kept[2]["size"] == "original"  # (sound has no size)
    assert len(presets.clean([_p(id=f"x{i}") for i in range(30)])) == presets.MAX


def test_settings_load_them_and_the_wheel_shows_them():
    s = Settings.from_data({"converter_presets": [_p(), _p(id="b2", kind="audio", format=".m4a", name="Podcast")],
                            "audio_quality": "loud"})
    assert s.audio_quality == "balanced"
    cat = converters.catalog()
    video = [t.key for t in cat["video"]]
    assert "preset:a1" in video and "preset:b2" in video  # (a sound preset saves a video's sound)
    assert [t.key for t in cat["audio"] if t.key.startswith("preset:")] == ["preset:b2"]
    assert not any(t.key.startswith("preset:") for t in cat["image"])
    tool = next(t for t in cat["video"] if t.key == "preset:a1")
    assert tool.label == "Mine" and "MP4" in tool.description


def test_sound_quality():
    assert presets.audio_args(".mp3", "balanced") == converters.AUDIO_ARGS[".mp3"]
    high = presets.audio_args(".mp3", "high")
    assert "-q:a" not in high and high[-2:] == ["-b:a", "320k"]
    assert presets.audio_args(".m4a", "smaller")[-2:] == ["-b:a", "128k"]
    assert presets.audio_args(".wav", "smallest") == converters.AUDIO_ARGS[".wav"]
    assert Settings.from_data({"audio_quality": "high"}).options().audio_quality == "high"


def test_a_picture_preset(tmp_path):
    src = tmp_path / "photo.png"
    Image.new("RGB", (4000, 3000), "orange").save(src)
    p = presets.clean([_p(kind="image", format=".jpg", quality="smaller", size="1280", name="Email")])[0]
    ctx = Ctx(Options(jpg_quality=95), lambda f: None, threading.Event(), {})
    out = presets.run(src, ctx, p).outputs[0]
    assert out.name == "photo (Email).jpg"
    with Image.open(out) as im:
        assert im.size == (1280, 960) and im.format == "JPEG"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no FFmpeg here")
def test_a_sound_preset(tmp_path, monkeypatch):
    import subprocess
    src = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(src)],
                   check=True)
    monkeypatch.setattr(converters, "find_tool", lambda name: shutil.which(name))
    p = presets.clean([_p(kind="audio", format=".mp3", quality="high", name="Hi")])[0]
    ctx = Ctx(Options(), lambda f: None, threading.Event(), {})
    out = presets.run(src, ctx, p).outputs[0]
    assert out.name == "tone (Hi).mp3" and out.stat().st_size > 1000


def test_the_options_card_adds_a_ready_preset(qtbot):
    from types import SimpleNamespace

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QPushButton

    from corefold import theme
    from corefold.presetspage import PresetsCard
    told = []
    c = SimpleNamespace(theme=theme.build_theme("light", "core"), icon=QIcon(), settings=Settings(),
                        settings_changed=lambda menu=False: told.append(menu))
    card = PresetsCard(c)
    qtbot.addWidget(card)
    next(b for b in card.findChildren(QPushButton) if "WhatsApp" in b.text()).click()
    assert [p["name"] for p in c.settings.converter_presets] == ["WhatsApp"] and told == [True]
    assert any(t.label == "WhatsApp" for t in converters.catalog()["video"])  # (on the wheel straight away)
    qtbot.waitUntil(lambda: not any("WhatsApp" in b.text() for b in card.findChildren(QPushButton)
                                    if not b.isHidden() and b.objectName() == "ghost"))
    remove = [b for b in card.findChildren(QPushButton) if b.accessibleName() == "Remove" and not b.isHidden()]
    remove[0].click()
    assert c.settings.converter_presets == [] and not any(t.key.startswith("preset:")
                                                          for t in converters.catalog()["video"])
