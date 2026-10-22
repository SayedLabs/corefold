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


def uninstaller():
    """A pretend app in this user's part of the registry: its leftovers are found and removed, and
    nothing else is touched (not even through a junction), then everything is put back."""
    import subprocess
    import winreg
    from corefold import uninstall as un
    name = "CfTestApp"
    local, roam = Path(os.environ["LOCALAPPDATA"]), Path(os.environ["APPDATA"])
    inst = local / "Programs" / name
    inst.mkdir(parents=True)
    (inst / "app.exe").write_text("x")
    (roam / name).mkdir(parents=True)
    (roam / name / "cfg.ini").write_text("x")
    precious = TMP / "precious"
    precious.mkdir()
    (precious / "family.jpg").write_text("keep me")
    junction = local / (name + "Cache")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(precious)], check=True, capture_output=True)
    check(un.is_link(junction), "a junction is seen as a link")
    key = un.UNINSTALL + "\\" + name
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, name)
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, str(inst))
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, f'"{inst / "uninstall.exe"}"')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Software\\" + name) as k:
        winreg.SetValueEx(k, "Theme", 0, winreg.REG_SZ, "dark")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, un.RUN_KEYS[0]) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, f'"{inst / "app.exe"}" --tray')
    apps = un.list_apps(include_store=False)
    app = next((a for a in apps if a.name == name), None)
    check(app is not None and app.broken, "the pretend app is listed (its uninstaller is missing)")
    left = un.find_leftovers(app, [a for a in apps if a is not app], forced=True)
    paths = {i.path for i in left}
    check(str(inst) in paths and str(roam / name) in paths, "its folders are found")
    check(str(junction) not in paths, "a junction is never offered")
    check(any(i.kind == "key" and i.path.endswith("Software\\" + name) for i in left), "its registry key is found")
    check(any(i.kind == "value" and i.value == name for i in left), "its startup entry is found")
    check(all(un.item_allowed(dict(i.__dict__)) for i in left), "everything offered passes the safety checks")
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    for bad in ({"kind": "folder", "path": windir + r"\System32\drivers"},
                {"kind": "file", "path": windir + r"\System32\kernel32.dll"},
                {"kind": "folder", "path": str(Path.home() / "Documents")},
                {"kind": "folder", "path": str(junction)},
                {"kind": "key", "path": "HKLM\\SYSTEM\\CurrentControlSet\\Services"},
                {"kind": "key", "path": "HKCU\\Software\\Microsoft\\Windows"}):
        check(not un.item_allowed(bad), f"refused: {bad['path']}")
    folder = un.new_backup([name])
    res = un.remove([i for i in left if i.safe], folder, [name])
    check(not res["failed"], f"removed without problems {res['failed']}")
    check(not inst.exists() and not (roam / name).exists(), "its folders are gone (into the backup)")
    check((precious / "family.jpg").read_text() == "keep me", "the junction's target is untouched")
    check(not un.key_exists("HKCU", "Software\\" + name, 0), "its registry key is gone")
    r = un.restore(folder)
    check(not r["failed"] and (inst / "app.exe").exists() and (roam / name / "cfg.ini").exists(), "undo puts the files back")
    check(un.key_exists("HKCU", "Software\\" + name, 0), "undo puts the registry key back")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, un.RUN_KEYS[0]) as k:
        check(winreg.QueryValueEx(k, name)[0].startswith('"'), "undo puts the startup entry back")
    # tidy up the pretend app
    for sub in (key, "Software\\" + name):
        subprocess.run(["reg", "delete", "HKCU\\" + sub, "/f"], capture_output=True)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, un.RUN_KEYS[0], 0, winreg.KEY_SET_VALUE) as k:
        winreg.DeleteValue(k, name)
    os.rmdir(junction)


for name, fn in (("uninstaller", uninstaller), ("list", listing), ("extract", extract_all), ("extract one", extract_some), ("open", open_one),
                 ("test", test_it), ("pack", pack_and_edit), ("video", video)):
    step(name, fn)
print(f"selftest: {len(problems)} problem(s)")
sys.exit(1 if problems else 0)
