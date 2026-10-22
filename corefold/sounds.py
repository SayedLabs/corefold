"""Corefold's sounds, played at the user's volume (Windows' simple player has no volume control,
so a quieter copy of the sound is made once and reused)."""
from __future__ import annotations

import array
import logging
import tempfile
import wave
from pathlib import Path

from .core import LEGACY_NAME, bundled

log = logging.getLogger("corefold")

DONE_SOUNDS = {"corefold": "Corefold", "peel": "Peel", "droplet": "Droplet", "glass": "Glass", "off": "No sound"}
OLD_NAMES = {"pop": "corefold", "chime": "glass", "bubble": "droplet",  # sounds from 1.3.0
             LEGACY_NAME.lower(): "corefold"}  # the signature sound had the old name before 1.7.0
EXTRA_GAIN = {"tick": 0.45}  # the wheel tick is meant to be barely there


def gain_for(volume: int) -> float:
    v = max(0, min(100, volume)) / 100
    return v ** 1.6  # feels even: 50% sounds about half as loud


def prepared(name: str, volume: int) -> Path | None:
    """A copy of the sound at the given volume (cached in the temp folder)."""
    src = bundled("assets", "sounds", f"{name}.wav")
    if not src or volume <= 0:
        return None
    gain = gain_for(volume) * EXTRA_GAIN.get(name, 1.0)
    out = Path(tempfile.gettempdir()) / "corefold-sounds" / f"{name}-{int(gain * 1000)}-{src.stat().st_mtime_ns}.wav"
    if out.exists():
        return out
    try:
        with wave.open(str(src), "rb") as w:
            params = w.getparams()
            frames = w.readframes(w.getnframes())
        if params.sampwidth != 2:
            return src
        samples = array.array("h", frames)
        for i, s in enumerate(samples):
            samples[i] = int(s * gain)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part")
        with wave.open(str(tmp), "wb") as w:
            w.setparams(params)
            w.writeframes(samples.tobytes())
        tmp.replace(out)
        return out
    except (OSError, wave.Error, EOFError):
        log.exception("could not prepare sound %s", name)
        return src
