"""Quick checks of the parts that talk to 7-Zip and FFmpeg, on a real Windows PC.

Used by .github/workflows/screenshots.yml (it prints one line per check and "selftest: N problem(s)").
Run: python scripts/selftest.py
Everything happens in a temporary folder.
"""
import os
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="corefold-selftest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")

from corefold import archives as ar  # noqa: E402
from corefold.core import Ctx, Options, zone_mark  # noqa: E402

problems = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)


def ctx(**params):
    return Ctx(Options(), params=params)


def step(name, fn):
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")


print("7-Zip:", ar.find_7z())
MARK = b"[ZoneTransfer]\r\nZoneId=3\r\n"

src = TMP / "in"
(src / "Reports").mkdir(parents=True)
for i in range(3):
    (src / "Reports" / f"report-{i}.txt").write_text("x" * 1000 * (i + 1))
(src / "notes.txt").write_text("hello")
archive = TMP / "documents.zip"
with zipfile.ZipFile(archive, "w") as z:
    for p in src.rglob("*"):
        if p.is_file():
            z.write(p, p.relative_to(src).as_posix())
    z.writestr("../escape.txt", "should never land outside")
with open(str(archive) + ":Zone.Identifier", "wb") as fh:
    fh.write(MARK)


def listing():
    names = {e.path for e in ar.list_entries(archive)}
    check("Reports/report-1.txt" in names and "notes.txt" in names, "list the archive")


def extract_all():
    out = TMP / "out1"
    r = ar.extract(archive, ctx(dest=str(out)), mode="folder")
    folder = r.outputs[0]
    check((folder / "Reports" / "report-2.txt").is_file(), "extract everything")
    check(not (TMP / "escape.txt").exists() and not (out / "escape.txt").exists(), "a ../ name stays inside")
    check(zone_mark(folder / "notes.txt") is not None, "extracted files keep the download mark")


def extract_some():
    out = TMP / "out2"
    ar.extract(archive, ctx(dest=str(out), members=["Reports/report-0.txt"]))
    check((out / "Reports" / "report-0.txt").is_file() and not (out / "notes.txt").exists(), "extract one file")


def open_one():
    r = ar.extract_for_opening(archive, "notes.txt", ctx())
    check(r.outputs[0].read_text() == "hello", "take one file out to open it")
    try:
        ar.extract_for_opening(archive, "../../Windows/notepad.exe", ctx())
        check(False, "a name outside the folder is refused")
    except Exception:
        check(True, "a name outside the folder is refused")


def test_it():
    r = ar.test_archive(archive, ctx())
    check("documents.zip" in r.note, "test the archive")


def pack_and_edit():
    out = TMP / "packed.7z"
    outs = ar.pack_into([src / "Reports", src / "notes.txt"], out, ctx(), "7z", password="pa ss")
    check(outs and outs[0].is_file(), "make a 7z with a password")
    try:
        ar.list_entries(out)
        check(False, "a locked 7z asks for the password")
    except ar.NeedPassword:
        check(True, "a locked 7z asks for the password")
    names = {e.path for e in ar.list_entries(out, "pa ss")}
    check("notes.txt" in names, "open it with the password")
    z = TMP / "edit.zip"
    ar.pack_into([src / "notes.txt", src / "Reports"], z, ctx(), "zip")
    ar.delete_entries(z, ["notes.txt"], ctx())
    names = {e.path for e in ar.list_entries(z)}
    check("notes.txt" not in names and "Reports/report-0.txt" in names, "remove one file from a ZIP")
    star = src / "a[1].txt"
    star.write_text("brackets")
    ar.add_files(z, [star], ctx())
    names = {e.path for e in ar.list_entries(z)}
    check("a[1].txt" in names, "add a file with [ ] in its name")


def video():
    from corefold import converters as cv
    import subprocess
    ff = cv.find_tool("ffmpeg")
    clip = TMP / "clip.mp4"
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64", "-y", str(clip)], check=True)
    check(cv.probe(clip)["duration"] > 0.5, "read a video's length")
    out = TMP / "clip.gif"
    cv.run_ffmpeg(clip, out, ["-vf", "fps=5"], ctx())
    check(out.is_file() and out.stat().st_size > 0, "convert a video")
    fake = TMP / "playlist.mp4"
    fake.write_text("#EXTM3U\n#EXTINF:1,\nhttp://127.0.0.1:9/x.ts\n#EXT-X-ENDLIST\n")
    try:
        cv.run_ffmpeg(fake, TMP / "fake.gif", [], ctx())
        check(not (TMP / "fake.gif").exists(), "a playlist pretending to be a video isn't followed")
    except Exception:
        check(True, "a playlist pretending to be a video isn't followed")


for name, fn in (("list", listing), ("extract", extract_all), ("extract one", extract_some), ("open", open_one),
                 ("test", test_it), ("pack", pack_and_edit), ("video", video)):
    step(name, fn)
print(f"selftest: {len(problems)} problem(s)")
sys.exit(1 if problems else 0)
