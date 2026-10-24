"""Every Converter tool on awkward files: Arabic and emoji names, upper-case and doubled endings, a very long
name, an empty file, files that only pretend to be pictures or videos, damaged and locked PDFs and archives,
tiny and huge pictures, 16-bit, CMYK and palette pictures, animated GIF/WebP, a multi-page TIFF, a video with
an odd size and one with no sound.

A tool may refuse a file, but it has to say why in plain words; it must never hang, crash, leave an empty
or unreadable file, or quietly drop the frames of an animation or the pages of a scan. Also checks how a
right-clicked drive reaches Corefold (Windows hands it over as C:" ).

Prints one line per problem and "edgetest: N problem(s)". Needs FFmpeg and 7-Zip on PATH.
Run: python scripts/edgetest.py
Everything happens in a temporary folder."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="corefold-edgetest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
if os.name != "nt":  # (a quick try on Linux: Windows' parts are pretend ones)
    import ctypes
    from unittest import mock
    ctypes.windll = mock.MagicMock()
    for name in ("winreg", "winsound"):
        sys.modules.setdefault(name, mock.MagicMock())

import logging  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402

from corefold import converters as cv  # noqa: E402
from corefold.converters import find_tool  # noqa: E402
from corefold.core import Options  # noqa: E402

logging.disable(logging.CRITICAL)
problems = []


def problem(what):
    print("FAIL  " + what, flush=True)
    problems.append(what)


def ffmpeg(*args):
    subprocess.run([find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *args], check=True,
                   capture_output=True)


def make(d: Path) -> dict:
    d.mkdir(parents=True, exist_ok=True)
    f = {}
    im = Image.new("RGB", (1200, 800), "skyblue")
    ImageDraw.Draw(im).text((100, 100), "Hello 123", fill="black")
    exif = im.getexif()
    exif[0x0112] = 6  # a phone photo held upright
    f["jpg"] = d / "صورة العيد 2026.jpg"
    im.save(f["jpg"], exif=exif, quality=85)
    f["jpeg"] = d / "v1.2.final.JPEG"
    im.save(f["jpeg"], quality=85)
    clear = Image.new("RGBA", (500, 300), (255, 0, 0, 0))
    ImageDraw.Draw(clear).ellipse((50, 50, 250, 250), fill=(0, 200, 0, 255))
    f["png"] = d / "🎉 party.PNG"
    clear.save(f["png"])
    frames = [Image.new("RGB", (120, 90), c) for c in ("red", "green", "blue")]
    f["gif"] = d / "anim.gif"
    frames[0].save(f["gif"], save_all=True, append_images=frames[1:], duration=100, loop=0)
    f["webp_anim"] = d / "anim.webp"
    frames[0].save(f["webp_anim"], save_all=True, append_images=frames[1:], duration=100, loop=0)
    f["webp"] = d / "still.webp"
    im.save(f["webp"])
    f["tiff"] = d / "scan pages.tif"
    frames[0].save(f["tiff"], save_all=True, append_images=frames[1:])
    f["png16"] = d / "grey16.png"
    Image.new("I;16", (300, 200), 40000).save(f["png16"])
    f["cmyk"] = d / "cmyk.jpg"
    Image.new("CMYK", (300, 200), (0, 100, 200, 0)).save(f["cmyk"])
    f["bmp"] = d / "palette.bmp"
    im.convert("P").save(f["bmp"])
    f["ico"] = d / "tiny.ico"
    clear.save(f["ico"])
    f["empty_jpg"] = d / "empty.jpg"
    f["empty_jpg"].write_bytes(b"")
    f["fake_png"] = d / "fake.png"
    f["fake_png"].write_bytes(b"not a picture at all" * 10)
    f["long"] = d / ("long " + "x" * 180 + ".jpg")
    im.save(f["long"])
    f["one_px"] = d / "1x1.png"
    Image.new("RGB", (1, 1)).save(f["one_px"])
    f["wide"] = d / "wide.png"
    Image.new("RGB", (17000, 10)).save(f["wide"])

    f["mp3"] = d / "تسجيل صوت.mp3"
    ffmpeg("-f", "lavfi", "-i", "sine=f=440:d=3", str(f["mp3"]))
    f["wav"] = d / "song.WAV"
    ffmpeg("-f", "lavfi", "-i", "sine=f=440:d=2", str(f["wav"]))
    f["mp4"] = d / "clip 1.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc=s=320x240:d=3", "-f", "lavfi", "-i", "sine=d=3", "-shortest",
           "-pix_fmt", "yuv420p", str(f["mp4"]))
    f["mp4_odd"] = d / "odd size, no sound.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc=s=321x241:d=2", "-c:v", "mpeg4", str(f["mp4_odd"]))
    f["mkv"] = d / "clip.mkv"
    ffmpeg("-i", str(f["mp4"]), "-c", "copy", str(f["mkv"]))
    f["broken_mp4"] = d / "broken.mp4"
    f["broken_mp4"].write_bytes(f["mp4"].read_bytes()[:3000])

    pages = [Image.new("RGB", (600, 800), c) for c in ("white", "yellow", "pink")]
    for pg in pages:
        ImageDraw.Draw(pg).text((50, 50), "Page text here", fill="black")
    f["pdf"] = d / "تقرير.pdf"
    pages[0].save(f["pdf"], save_all=True, append_images=pages[1:])
    f["pdf2"] = d / "second.PDF"
    pages[1].save(f["pdf2"])
    f["broken_pdf"] = d / "broken.pdf"
    f["broken_pdf"].write_bytes(b"%PDF-1.4\n garbage")
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter(clone_from=PdfReader(str(f["pdf2"])))
    w.encrypt("secret")
    f["pdf_locked"] = d / "locked.pdf"
    w.write(str(f["pdf_locked"]))

    f["zip"] = d / "أرشيف.zip"
    with zipfile.ZipFile(f["zip"], "w") as z:
        z.writestr("folder/ملف.txt", "hello")
        z.writestr("b.txt", "x" * 1000)
    f["zip_evil"] = d / "evil.zip"
    with zipfile.ZipFile(f["zip_evil"], "w") as z:
        z.writestr("../../escape.txt", "bad")
        z.writestr("ok.txt", "ok")
    from corefold.archives import find_7z
    f["7z_pw"] = d / "pw.7z"
    subprocess.run([find_7z(), "a", "-psecret", "-mhe=on", str(f["7z_pw"]), str(f["wav"])], check=True,
                   capture_output=True)
    f["zip_broken"] = d / "broken.zip"
    f["zip_broken"].write_bytes(f["zip"].read_bytes()[:40])
    f["folder"] = d / "Photos v2.0"
    f["folder"].mkdir(exist_ok=True)
    for i in range(3):
        im.save(f["folder"] / f"p{i}.jpg")
    return f


BAD_INPUT = {"empty_jpg", "fake_png", "broken_mp4", "broken_pdf", "zip_broken", "pdf_locked", "7z_pw"}
# a tool that needs something typed or picked first says so (the app's dialog always gives it)
NEEDS_INPUT = ("type some text or pick a logo", "pick a subtitle file", "no black bars", "draw your signature",
               "type a password", "no pages left", "too big for WebP")
PARAMS = {"watermark": {"text": "محمد © 2026"}}
RAW = ("Traceback", "Errno", "0x", "7-Zip reported an error", "FFmpeg could not convert", "object has no attribute",
       "NoneType")


def frames(p: Path) -> int:
    with Image.open(p) as im:
        return getattr(im, "n_frames", 1)


def check_output(out: Path, tool: str, src_key: str):
    if out.is_dir():
        if not any(out.rglob("*")):
            problem(f"{tool} on {src_key}: made an empty folder")
        return
    if not out.exists() or out.stat().st_size == 0:
        problem(f"{tool} on {src_key}: no file or an empty file ({out.name})")
        return
    ext = out.suffix.lower()
    try:
        if ext in (".jpg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".ico", ".avif"):
            with Image.open(out) as im:
                im.load()
        elif ext == ".pdf":
            from pypdf import PdfReader
            r = PdfReader(str(out))
            if not r.is_encrypted and not len(r.pages):
                problem(f"{tool} on {src_key}: a PDF with no pages")
        elif ext in (".mp4", ".mov", ".mkv", ".webm", ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aiff"):
            if cv.probe(out)["duration"] <= 0:
                problem(f"{tool} on {src_key}: {out.name} doesn't play")
    except Exception as e:
        problem(f"{tool} on {src_key}: {out.name} can't be opened ({type(e).__name__})")
        return
    # animations and scans keep every frame / page when the result is the same kind of picture
    if src_key in ("gif", "webp_anim", "tiff") and ext in (".gif", ".webp", ".tiff") and \
            tool.split(":")[1] in ("resize", "rotate", "crop", "watermark", "clean"):
        if frames(out) < 3:
            problem(f"{tool} on {src_key}: only {frames(out)} of 3 frames/pages kept")


def run_all(files: dict, out_root: Path):
    groups = [[k] for k in files] + [["jpg", "png"], ["pdf", "pdf2"], ["mp3", "wav"], ["mp4", "mkv"], ["jpg", "mp4"]]
    ran = 0
    for keys in groups:
        paths = [files[k] for k in keys]
        pk = cv.pick(paths)
        for slot in pk.slots:
            t = slot.tool
            if t.run is None or not slot.ok or t.key == "files:rename":  # (rename changes the originals)
                continue
            out = out_root / t.key.replace(":", "_")
            out.mkdir(parents=True, exist_ok=True)
            label = f"{t.key} on {'+'.join(keys)}"
            box: dict = {}

            def go():
                try:
                    box["r"], box["errors"] = cv.run_job(t, pk.files, Options(output_folder=out),
                                                         params=PARAMS.get(t.key.split(":")[1], {}))
                except Exception:
                    box["crash"] = traceback.format_exc().splitlines()[-1]
            th = threading.Thread(target=go, daemon=True)
            started = time.monotonic()
            th.start()
            th.join(180)
            ran += 1
            if th.is_alive():
                problem(f"{label}: still going after 3 minutes")
                continue
            if "crash" in box:
                problem(f"{label}: crashed: {box['crash']}")
                continue
            r, errors = box["r"], box["errors"]
            for err in errors:
                expected = any(k in BAD_INPUT for k in keys) or any(s in err for s in NEEDS_INPUT)
                if not expected:
                    problem(f"{label}: {err}")
                elif any(s in err for s in RAW):
                    problem(f"{label}: not in plain words: {err}")
            for o in r.outputs:
                check_output(o, t.key, "+".join(keys))
            took = time.monotonic() - started
            if took > 60:
                problem(f"{label}: took {took:.0f} s")
    return ran


def check_drive_argument():
    """Explorer starts Corefold with "C:\\" for a right-clicked drive; Windows' own reading of that command
    line gives C:" , which Corefold has to turn back into C:\\ ."""
    if os.name != "nt":
        return
    from corefold import ipc
    code = "import sys, json; print(json.dumps(sys.argv[1:]))"
    line = f'"{sys.executable}" -c "{code}" --scan "C:\\"'
    got = json.loads(subprocess.run(line, capture_output=True, text=True).stdout or "[]")
    msg = ipc.message_for(got)
    if msg != b"scan\nC:\\":
        problem(f"a right-clicked drive arrives as {got!r} and is passed on as {msg!r}")


def main():
    files = make(TMP / "in")
    ran = run_all(files, TMP / "out")
    if any((place / "escape.txt").exists() for place in (TMP, TMP / "out", TMP / "in")):
        problem("a ZIP with ../ in a name put a file outside the folder it was unpacked to")
    check_drive_argument()
    print(f"{ran} tool runs")
    print(f"edgetest: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
