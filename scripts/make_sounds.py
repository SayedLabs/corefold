"""
Synthesises Satsuma's sounds into assets/sounds/*.wav. Run: python scripts/make_sounds.py

All sounds are soft, short and warm: smooth starts (no clicks), gentle low-passing (no harsh
highs), quiet by default. The app scales the volume when it plays them.
"""
import math
import random
import struct
import wave
from pathlib import Path

RATE = 44100
OUT = Path(__file__).resolve().parent.parent / "assets" / "sounds"


def silence(seconds: float) -> list[float]:
    return [0.0] * int(RATE * seconds)


def mix_into(buf: list[float], part: list[float], at: float, gain: float = 1.0):
    start = int(at * RATE)
    if start + len(part) > len(buf):
        buf.extend([0.0] * (start + len(part) - len(buf)))
    for i, v in enumerate(part):
        buf[start + i] += v * gain


def lowpass(samples: list[float], cutoff: float) -> list[float]:
    a = 1 - math.exp(-2 * math.pi * cutoff / RATE)
    out, y = [], 0.0
    for x in samples:
        y += a * (x - y)
        out.append(y)
    return out


def edges(samples: list[float], fade_in_ms: float = 3, fade_out_ms: float = 25) -> list[float]:
    n_in, n_out = int(RATE * fade_in_ms / 1000), int(RATE * fade_out_ms / 1000)
    n = len(samples)
    out = []
    for i, s in enumerate(samples):
        g = 1.0
        if i < n_in:
            g *= 0.5 - 0.5 * math.cos(math.pi * i / n_in)
        if i > n - n_out:
            g *= 0.5 - 0.5 * math.cos(math.pi * (n - i) / n_out)
        out.append(s * g)
    return out


def marimba(freq: float, length: float = 0.6, brightness: float = 1.0) -> list[float]:
    """Wooden bar: a warm fundamental plus a quickly fading 4th partial (the 'tok')."""
    n = int(RATE * length)
    out = []
    for i in range(n):
        t = i / RATE
        v = math.sin(2 * math.pi * freq * t) * math.exp(-t * 7.5)
        v += 0.35 * brightness * math.sin(2 * math.pi * freq * 3.98 * t) * math.exp(-t * 38)
        v += 0.12 * brightness * math.sin(2 * math.pi * freq * 9.9 * t) * math.exp(-t * 90)
        out.append(v)
    return out


def pluck(freq: float, length: float = 0.5, damping: float = 0.996) -> list[float]:
    """Karplus-Strong string: sounds like a soft plucked rubber band / peel snap."""
    rnd = random.Random(7)
    period = int(RATE / freq)
    ring = [rnd.uniform(-1, 1) for _ in range(period)]
    ring = lowpass(ring * 2, 2500)[-period:]  # softer attack
    out = []
    for i in range(int(RATE * length)):
        j = i % period
        v = ring[j]
        ring[j] = damping * 0.5 * (ring[j] + ring[(j + 1) % period])
        out.append(v)
    return out


def drop(f0: float = 520, f1: float = 1250, length: float = 0.16) -> list[float]:
    """Water droplet: a short rising 'bloop'."""
    out, phase = [], 0.0
    n = int(RATE * length)
    for i in range(n):
        t = i / RATE
        k = t / length
        freq = f0 + (f1 - f0) * (k ** 0.6)
        phase += 2 * math.pi * freq / RATE
        env = math.sin(math.pi * min(1.0, k * 1.15)) ** 2 * math.exp(-t * 9)
        out.append(math.sin(phase) * env)
    return out


def bell(freq: float, length: float = 1.1) -> list[float]:
    """Glassy bell: inharmonic partials with long, soft decay."""
    partials = [(1.0, 1.0, 3.0), (2.76, 0.35, 5.0), (5.40, 0.12, 9.0), (8.93, 0.05, 14.0)]
    n = int(RATE * length)
    out = []
    for i in range(n):
        t = i / RATE
        out.append(sum(a * math.sin(2 * math.pi * freq * r * t) * math.exp(-t * d) for r, a, d in partials))
    return out


def note(name: str) -> float:
    names = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}
    semis = names[name[0]] + (1 if "#" in name else 0) + 12 * (int(name[-1]) - 4)
    return 440 * 2 ** (semis / 12)


# ----------------------------------------------------------------------------- the sounds

def satsuma():
    """Signature sound: two juicy wooden notes going up (G5 → C6)."""
    buf = silence(0.75)
    mix_into(buf, marimba(note("G5"), 0.55, 0.8), 0.0, 0.8)
    mix_into(buf, marimba(note("C6"), 0.65, 0.8), 0.085, 1.0)
    return lowpass(buf, 5200)


def peel():
    """A soft snap, like peeling a satsuma."""
    buf = silence(0.5)
    mix_into(buf, pluck(note("E4"), 0.45, 0.994), 0.0, 0.9)
    mix_into(buf, pluck(note("B4"), 0.4, 0.993), 0.045, 0.55)
    return lowpass(buf, 3800)


def droplet():
    """Two little drops of juice."""
    buf = silence(0.4)
    mix_into(buf, drop(480, 1100, 0.14), 0.0, 0.8)
    mix_into(buf, drop(640, 1500, 0.12), 0.11, 0.6)
    return lowpass(buf, 4500)


def glass():
    """One clear, quiet bell."""
    return lowpass(bell(note("E6") / 2, 1.2), 6000)


def problem():
    """Something went wrong: two low, gentle notes going down (not a buzzer)."""
    buf = silence(0.8)
    mix_into(buf, marimba(note("E4"), 0.5, 0.5), 0.0, 0.9)
    mix_into(buf, marimba(note("C4"), 0.7, 0.5), 0.14, 1.0)
    return lowpass(buf, 2500)


def tick():
    """Tiny, very quiet tick when the wheel opens."""
    return lowpass(marimba(note("C7"), 0.08, 0.3), 6000)


SOUNDS = {"satsuma": satsuma, "peel": peel, "droplet": droplet, "glass": glass, "problem": problem, "tick": tick}
PEAK = 0.7  # -3 dBFS; the app turns it down further with the volume slider


def write(name: str, samples: list[float]):
    OUT.mkdir(parents=True, exist_ok=True)
    samples = edges(samples)
    peak = max(1e-9, max(abs(s) for s in samples))
    gain = PEAK / peak
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s * gain)) * 32767)) for s in samples))


if __name__ == "__main__":
    for old in OUT.glob("*.wav"):
        old.unlink()
    for name, fn in SOUNDS.items():
        write(name, fn())
    print("wrote", ", ".join(p.name for p in sorted(OUT.glob("*.wav"))))
