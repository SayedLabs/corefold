"""Every Converter tool that needs no question, saving onto a disk that is all but full.

Whatever a tool does when the disk fills up, it must: leave the originals exactly as they were, leave no
half-written file or folder behind (one cut short must never look like a whole one), and say so in plain
words ("the disk is full", not FFmpeg's or 7-Zip's English). A tool that fits in the little room left must
make whole files.

The disk has to be a small one made for this (the Bug hunt makes a 16 MB one, R:), because the test fills
it up: it refuses a disk bigger than 64 MB. On Linux a small tmpfs does (mount -t tmpfs -o size=4M tmpfs X).

Prints one line per tool, one line per problem and "fulldisktest: N problem(s)". Needs FFmpeg and 7-Zip.
Run: python scripts/fulldisktest.py R:\\ [KB left free, 200]"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # (Arabic file names, into a file Windows would write in cp1252)
TMP = Path(tempfile.mkdtemp(prefix="corefold-fulldisk-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
if os.name != "nt":  # (a quick try on Linux: Windows' parts are pretend ones)
    import ctypes
    from unittest import mock
    ctypes.windll = mock.MagicMock()
    _winreg = mock.MagicMock()
    _winreg.EnumKey.side_effect = OSError
    _winreg.EnumValue.side_effect = OSError
    sys.modules.setdefault("winreg", _winreg)
    sys.modules.setdefault("winsound", mock.MagicMock())
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from corefold import converters as cv  # noqa: E402
from corefold.core import Options  # noqa: E402

LIMIT = 64 * 1024 * 1024
problems: list[str] = []


def problem(what: str):
    problems.append(what)
    print("PROBLEM:", what, flush=True)


def make_files(src: Path) -> list[list[Path]]:
    noise = (np.random.default_rng(1).random((1500, 2000, 3)) * 255).astype("uint8")
    Image.fromarray(noise).save(src / "صورة photo.jpg", quality=95)
    Image.fromarray(noise[:600, :800]).save(src / "pic.png")
    ffmpeg = cv.find_tool("ffmpeg")
    subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25", "-f",
                    "lavfi", "-i", "sine", "-t", "4", "-pix_fmt", "yuv420p", str(src / "clip.mp4")], check=True)
    subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine", "-t", "20",
                    str(src / "song.wav")], check=True)
    pages = [Image.fromarray(noise[i * 100:i * 100 + 800, :600]) for i in range(5)]
    pages[0].save(src / "doc.pdf", save_all=True, append_images=pages[1:])
    folder = src / "folder"
    folder.mkdir()
    for i in range(5):
        Image.fromarray(noise[:400, :400]).save(folder / f"p{i}.jpg")
    (src / "big.bin").write_bytes(os.urandom(3_000_000))
    with zipfile.ZipFile(src / "pack.zip", "w") as z:
        z.write(src / "big.bin", "big.bin")
    return [[src / "صورة photo.jpg"], [src / "pic.png"], [src / "clip.mp4"], [src / "song.wav"], [src / "doc.pdf"],
            [folder], [src / "pack.zip"], [src / "big.bin"]]


def fingerprints(src: Path) -> dict:
    return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in src.rglob("*") if p.is_file()}


def plain(error: str) -> bool:
    """Does the message say, in the app's own words, that there's no room? (Not FFmpeg's or 7-Zip's.)"""
    return "the disk is full" in error or ("needs" in error and "free" in error)  # (or Archive's check before it starts)


def whole(f: Path) -> bool:
    """Is this made file complete? (Pictures must open to the last pixel.)"""
    if f.stat().st_size == 0:
        return False
    if f.suffix.lower() in (".jpg", ".png", ".webp", ".bmp", ".ico", ".tiff"):
        try:
            with Image.open(f) as im:
                im.load()
        except Exception:
            return False
    return True


def fill(disk: Path, leave_kb: int):
    """Empties the disk, then fills it up to leave_kb free."""
    for p in disk.iterdir():
        if p.name.lower() in ("system volume information", "$recycle.bin"):
            continue
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    free = shutil.disk_usage(disk).free
    with open(disk / "filler", "wb") as f:
        left = max(0, free - leave_kb * 1024)
        while left > 0:
            n = min(left, 1 << 20)
            f.write(b"\0" * n)
            left -= n


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    disk = Path(sys.argv[1])
    leave_kb = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    if shutil.disk_usage(disk).total > LIMIT:
        print(f"{disk} is bigger than 64 MB: this test fills the disk up, so it only runs on a small one made for it")
        return 2
    src = TMP / "originals"
    src.mkdir()
    inputs = make_files(src)
    before = fingerprints(src)
    options = Options(output_folder=disk)
    tried = 0
    for files in inputs:
        for slot in cv.tools_for(files)[1]:
            tool = slot.tool
            if slot.reason or tool.ask or tool.opens or tool.run is None:
                continue
            fill(disk, leave_kb)
            result, errors = cv.run_job(tool, files, options)
            tried += 1
            left = sorted(p.name for p in disk.rglob("*") if p.name != "filler"
                          and "system volume information" not in str(p).lower())
            line = f"{files[0].name} · {tool.key}: made {len(result.outputs)}, errors {errors[:1]}, on disk {left[:4]}"
            print(line, flush=True)
            if any(not plain(e) for e in errors):
                problem("not said in plain words: " + line)
            if (errors or not result.outputs) and left:
                problem("left something behind: " + line)
            for out in result.outputs:
                for f in [out] if Path(out).is_file() else [p for p in Path(out).rglob("*") if p.is_file()]:
                    if not whole(f):
                        problem(f"{f.name} is cut short or empty but counted as made: " + line)
            if not errors and not result.outputs and not result.clipboard and not result.note:
                problem("nothing made and nothing said: " + line)
            if fingerprints(src) != before:
                problem("an original changed: " + line)
                before = fingerprints(src)
    fill(disk, 0)
    (disk / "filler").unlink(missing_ok=True)  # (the disk is left empty)
    print(f"{tried} tools tried on a full disk")
    print(f"fulldisktest: {len(problems)} problem(s)")
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
