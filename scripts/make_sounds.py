"""Synthesises Satsuma's "done" sounds into assets/sounds/*.wav. Run: python scripts/make_sounds.py"""
import math
import struct
import wave
from pathlib import Path

RATE = 44100
OUT = Path(__file__).resolve().parent.parent / "assets" / "sounds"


def write(name: str, samples: list[float], volume: float = 0.5):
    OUT.mkdir(parents=True, exist_ok=True)
    peak = max(1e-9, max(abs(s) for s in samples))
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(s / peak * volume * 32767)) for s in samples))


def fade_in(samples, ms=4):
    n = int(RATE * ms / 1000)
    return [s * min(1.0, i / n) for i, s in enumerate(samples)]


def pop():
    """Short pitch drop, like a cork."""
    n = int(RATE * 0.14)
    out, phase = [], 0.0
    for i in range(n):
        t = i / RATE
        freq = 900 * math.exp(-t * 18) + 220
        phase += 2 * math.pi * freq / RATE
        out.append(math.sin(phase) * math.exp(-t * 32))
    return fade_in(out)


def chime():
    """Two soft bell notes (E6 then B6)."""
    n = int(RATE * 0.9)
    out = [0.0] * n
    for start, f in ((0.0, 1318.5), (0.11, 1975.5)):
        s0 = int(start * RATE)
        for i in range(s0, n):
            t = (i - s0) / RATE
            env = math.exp(-t * 5.5)
            out[i] += env * (math.sin(2 * math.pi * f * t) + 0.25 * math.sin(2 * math.pi * f * 2.01 * t))
    return fade_in(out)


def bubble():
    """Rising blip, like a bubble."""
    n = int(RATE * 0.22)
    out, phase = [], 0.0
    for i in range(n):
        t = i / RATE
        freq = 380 + 1500 * (t / 0.22) ** 1.6
        phase += 2 * math.pi * freq / RATE
        env = math.sin(math.pi * min(1.0, t / 0.22)) ** 1.5
        out.append(math.sin(phase) * env)
    return fade_in(out)


if __name__ == "__main__":
    write("pop", pop(), 0.55)
    write("chime", chime(), 0.4)
    write("bubble", bubble(), 0.45)
    print("wrote", ", ".join(p.name for p in sorted(OUT.glob("*.wav"))))
