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


def right_click_menu():
    """Both right-click menu styles are written to this user's registry the way Explorer reads them,
    every entry starts Corefold with the right request, and removing the menu leaves nothing."""
    import shlex
    import winreg
    from corefold import ipc
    from corefold import settings as st
    from corefold import shellmenu as sm
    s = st.Settings()
    cmd = lambda *a: '"C:\\Program Files\\Corefold\\Corefold.exe" ' + " ".join(a)  # noqa: E731
    hkcu = winreg.HKEY_CURRENT_USER

    def value(path, name=""):
        with winreg.OpenKey(hkcu, path) as k:
            return winreg.QueryValueEx(k, name)[0]

    for style in ("windows", "wheel"):
        s.menu_style = style
        sm.write(sm.plan(s, cmd), cmd, "")
        base = sm.FILE_ROOT + r"\.jpg\shell\Corefold"
        if style == "windows":
            entries, i = [], 1
            while True:
                try:
                    entries.append((value(rf"{base}\shell\{i:02d}", "MUIVerb"), value(rf"{base}\shell\{i:02d}\command")))
                except OSError:
                    break
                i += 1
            check(len(entries) > 5, f"Windows menu for photos has {len(entries)} entries")
            png = next((c for t, c in entries if "image:png" in c), "")
            argv = shlex.split(png.replace('"%1"', '"C:\\Photos\\a b.jpg"'), posix=False)[1:]
            argv = [a.strip('"') for a in argv]
            check(ipc.message_for(argv) == b"run\nimage:png\nC:\\Photos\\a b.jpg", f"Convert to PNG starts the right job {argv}")
            check(any("--open" in c for _, c in entries), "\"All options…\" opens the window")
        else:
            c = value(base + r"\command")
            check("--convert" in c and '"%1"' in c, "the wheel entry opens the wheel")
        check(value(sm.FOLDER_ROOT + r"\shell\Corefold", "MUIVerb") != "", f"folders have an entry ({style})")
    sm.remove()
    try:
        value(sm.FILE_ROOT + r"\.jpg\shell\Corefold", "MUIVerb")
        check(False, "removing the menu leaves nothing")
    except OSError:
        check(True, "removing the menu leaves nothing")


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
    used = un.usage()
    check(isinstance(used, dict), f"when apps were last used can be read ({len(used)} entries)")
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


# what the windows for each tool would send (the same as a person choosing the usual options)
PARAMS = {"image:resize": {"preset": "50"}, "image:rotate": {"how": "left"}, "image:fit": {"mb": 0.01},
          "image:watermark": {"text": "(c) Test", "logo": "", "position": "br", "size": "medium", "opacity": 60},
          "audio:trim": {"start": 0.5, "end": 2.0}, "video:trim": {"start": 0.5, "end": 2.0},
          "audio:fit": {"mb": 0.05}, "video:fit": {"mb": 0.2}, "video:rotate": {"how": "right"},
          "video:frames": {"mode": "count", "count": 3}, "image:video": {"seconds": 0.5},
          "video:watermark": {"text": "Hi", "logo": "", "position": "c", "size": "small", "opacity": 50},
          "pdf:fit": {"mb": 1}, "pack:options": {"format": "7z", "level": "max", "password": "x y", "split_mb": 0},
          "image:crop": {"box": (0.1, 0.1, 0.6, 0.9)}, "image:sticker": {"preset": "whatsapp"},
          "video:sticker": {"preset": "whatsapp"}, "audio:speed": {"speed": 1.5}, "video:speed": {"speed": 0.5},
          "pdf:pages": {"pages": [(0, 90)]}, "pdf:lock": {"new_password": "secret"},
          "pdf:split": {"mode": "each"}, "files:rename": {"mode": "number", "base": "holiday", "start": 1},
          "archive:rename": {"mode": "add", "before": "x-"}, "video:volume": {"mode": "louder"},
          "audio:volume": {"mode": "louder"}, "video:crop": {"how": "square"},
          "pdf:numbers": {"place": "br", "style": "of", "skip": 1},
          "pdf:watermark": {"text": "Draft", "logo": "", "position": "tile", "size": "large", "opacity": 40},
          "video:subtitles": {"subtitles": str(TMP / "samples" / "words.srt"), "how": "track"}}


def every_tool():
    """Every option the wheel offers for a set of real files is run once, the way a click would."""
    import shutil
    import subprocess
    from PIL import Image
    from pypdf import PdfWriter
    from corefold import converters as cv
    from corefold.core import Options
    S = TMP / "samples"
    S.mkdir()
    Image.new("RGB", (800, 600), (30, 120, 200)).save(S / "photo.jpg", quality=95)
    Image.new("RGBA", (300, 200), (255, 0, 0, 128)).save(S / "logo.png")
    Image.new("RGB", (320, 240), (200, 120, 30)).save(S / "pic.webp")
    Image.new("RGB", (320, 240), (20, 220, 30)).save(S / "pic.bmp")
    Image.new("RGB", (320, 240), (20, 20, 230)).save(S / "pic.tiff")
    (S / "words.srt").write_text("1\n00:00:00,000 --> 00:00:01,500\nHello\n", "utf-8")
    frames = [Image.new("RGB", (120, 90), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    frames[0].save(S / "anim.gif", save_all=True, append_images=frames[1:], duration=200, loop=0)
    ff = cv.find_tool("ffmpeg")
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=3", str(S / "tone.wav")], check=True)
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=3", str(S / "tone.mp3")], check=True)
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:d=3", "-f", "lavfi", "-i",
                    "sine=d=3", "-shortest", "-pix_fmt", "yuv420p", str(S / "clip.mp4")], check=True)
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:d=2", "-pix_fmt", "yuv420p",
                    str(S / "silent.mov")], check=True)
    for n in ("a", "b"):
        w = PdfWriter()
        w.add_blank_page(200, 200)
        w.add_blank_page(200, 200)
        with open(S / f"{n}.pdf", "wb") as fh:
            w.write(fh)
    Image.new("RGB", (600, 800), "white").save(S / "scan.pdf")
    with zipfile.ZipFile(S / "pack.zip", "w") as z:
        z.writestr("folder/x.txt", "hello")
        z.writestr("y.txt", "world")
    F = TMP / "folder-of-pictures"
    F.mkdir()
    for i in range(3):
        Image.new("RGB", (100 + i, 80), (i * 50, 0, 0)).save(F / f"p{i}.png")
    singles = [S / n for n in ("photo.jpg", "logo.png", "pic.webp", "pic.bmp", "pic.tiff", "anim.gif", "tone.wav",
                               "tone.mp3", "clip.mp4", "silent.mov", "a.pdf", "scan.pdf", "pack.zip")] + [F]
    groups = [[S / "photo.jpg", S / "logo.png"], [S / "a.pdf", S / "b.pdf"], [S / "tone.wav", S / "clip.mp4"]]
    done = 0
    for files in [[f] for f in singles] + groups:
        cv.warm_up(files)
        pk = cv.pick(files)
        names = ", ".join(f.name for f in files)
        for slot in pk.slots:
            t = slot.tool
            if not slot.ok or t.run is None:
                continue
            work = TMP / "runs" / f"{len(list((TMP / 'runs').glob('*'))) if (TMP / 'runs').exists() else 0}"
            work.mkdir(parents=True)
            copies = []
            for f in files:  # a fresh copy each time: renaming tools change the files themselves
                c = work / f.name
                (shutil.copytree if f.is_dir() else shutil.copy2)(f, c)
                copies.append(c)
            out = work / "out"
            out.mkdir()
            try:
                res, errs = cv.run_job(t, copies, Options(output_folder=out), params=PARAMS.get(t.key, {}))
            except Exception:
                check(False, f"{t.key} on {names}: {traceback.format_exc().splitlines()[-1]}")
                continue
            made = [p for p in out.rglob("*") if p.is_file()] + [p for p in res.outputs if p.exists()]
            empty = [p for p in made if p.is_file() and p.stat().st_size == 0]
            good = not errs and not empty and (made or res.note or res.clipboard)
            check(good, f"{t.key} on {names}" + ("" if good else f": {errs} {res.note!r} empty={empty}"))
            done += 1
    check(done > 60, f"ran {done} options")


def archive_tool():
    """Corefold Archive with the real 7-Zip: save a changed file back, rescue a damaged ZIP and a
    damaged 7z, unpack archives inside archives, take files out for dragging, open-with registration."""
    import io
    import winreg
    w = TMP / "archive-tool"
    w.mkdir()
    # save a changed file back, at its place inside (ZIP and 7z)
    for fmt in ("zip", "7z"):
        arc = w / f"edit.{fmt}"
        ar.pack_into([src / "Reports", src / "notes.txt"], arc, ctx(), fmt)
        opened = ar.extract_for_opening(arc, "Reports/report-1.txt", ctx()).outputs[0]
        opened.write_text("changed in Notepad")
        ar.update_member(arc, "Reports/report-1.txt", opened, ctx())
        names = sorted(e.path for e in ar.list_entries(arc) if not e.is_dir)
        check(names == ["Reports/report-0.txt", "Reports/report-1.txt", "Reports/report-2.txt", "notes.txt"],
              f"{fmt}: saved back in the same place, nothing added {names}")
        back = ar.extract_for_opening(arc, "Reports/report-1.txt", ctx()).outputs[0]
        check(back.read_text() == "changed in Notepad", f"{fmt}: the new version is inside")
    # rescue: a ZIP with its table of contents cut off, and one with a damaged file
    good = w / "photos.zip"
    with zipfile.ZipFile(good, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a.txt", "hello " * 2000)
        z.writestr("dir/b.txt", "world " * 2000)
    data = good.read_bytes()
    cut = w / "cut.zip"
    cut.write_bytes(data[:data.rfind(b"PK\x01\x02")])
    r = ar.repair_archive(cut, ctx())
    with zipfile.ZipFile(r.outputs[0]) as z:
        files = sorted(n for n in z.namelist() if not n.endswith("/"))
        check(files == ["a.txt", "dir/b.txt"], f"cut-off ZIP rescued {files} ({r.note})")
    bad = bytearray(data)
    j = bytes(bad).find(b"dir/b.txt") + len("dir/b.txt") + 3
    bad[j:j + 6] = b"\xff" * 6
    (w / "bad.zip").write_bytes(bytes(bad))
    r = ar.repair_archive(w / "bad.zip", ctx())
    with zipfile.ZipFile(r.outputs[0]) as z:
        check("a.txt" in z.namelist() and "dir/b.txt" not in z.namelist(), f"damaged file left out {z.namelist()} "
              f"({r.note})")
    seven = w / "box.7z"
    ar.pack_into([src / "Reports"], seven, ctx(), "7z")
    raw = bytearray(seven.read_bytes())
    raw[len(raw) - 40:len(raw) - 30] = b"\x00" * 10
    (w / "box-bad.7z").write_bytes(bytes(raw))
    try:
        r = ar.repair_archive(w / "box-bad.7z", ctx())
        check(r.outputs[0].exists(), f"damaged 7z: {r.note}")
    except RuntimeError as e:
        check("rescued" in str(e), f"damaged 7z: nothing could be read, said so ({e})")
    # archives inside archives
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("deep.txt", "deep")
    outer = w / "outer.zip"
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr("inner.zip", inner.getvalue())
    seven_in = w / "in7"
    seven_in.mkdir()
    (seven_in / "x.txt").write_text("x")
    ar.pack_into([seven_in / "x.txt"], w / "in7.7z", ctx(), "7z")
    ar.add_files(outer, [w / "in7.7z"], ctx())
    dest = w / "dest"
    dest.mkdir()
    r = ar.extract(outer, ctx(dest=str(dest), nested=True), mode="folder")
    out = r.outputs[0]
    check((out / "inner" / "deep.txt").exists() and (out / "in7" / "x.txt").exists() and
          not (out / "inner.zip").exists(), f"archives inside unpacked ({r.note})")
    # taking files out for dragging
    t = ar.extract_for_drag(archive, ["Reports/report-2.txt"], "")
    check((t / "Reports" / "report-2.txt").exists(), "files taken out for dragging")
    # a folder picked in a ZIP that has no separate folder entries (as Python and many apps write them)
    t2 = ar.extract_for_drag(good, ["dir", "dir/b.txt"], "")
    check((t2 / "dir" / "b.txt").exists(), "a folder taken out for dragging (ZIP without folder entries)")
    ar.remove_drag_folder(t2)
    check(not t2.exists(), "the copies made for dragging are removed afterwards")
    try:
        mark = open(str(t / "Reports" / "report-2.txt") + ":Zone.Identifier", "rb").read()
    except OSError:
        mark = b""
    check(b"ZoneId=3" in mark, "dragged-out files keep the internet warning")
    # double-click to open: registered for this user, and taken out again
    from corefold import assoc
    cmd = lambda *a: " ".join(['"C:\\Corefold\\Corefold.exe"', *a])  # noqa: E731
    try:
        assoc.register(cmd, "", "Archive")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.zip\OpenWithProgids") as k:
            winreg.QueryValueEx(k, assoc.PROGID)
        check(assoc.is_current(cmd), "archives: Corefold is under Open with")
    finally:
        assoc.unregister()
    check(not assoc._registered(), "…and taken out again")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.zip\OpenWithProgids") as k:
            winreg.QueryValueEx(k, assoc.PROGID)
        left = True
    except OSError:
        left = False
    check(not left, "…with nothing left behind")
    # switching a setting on and off quickly: one change at a time, and the last choice stays
    import logging
    errors = []

    class Catch(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                errors.append(record.getMessage())
    catch = Catch()
    logging.getLogger("corefold").addHandler(catch)
    try:
        threads = [assoc.sync_in_background({"media": (i % 2 == 0, "Player")}, cmd, "") for i in range(12)]
        threads.append(assoc.sync_in_background({"media": (True, "Player")}, cmd, ""))
        for t in threads:
            t.join(60)
        check(not errors, f"quick on/off of opening media files: no registry errors ({errors[:2]})")
        check(assoc.is_current(cmd, "media"), "…and the last choice (on) is the one that stays")
    finally:
        logging.getLogger("corefold").removeHandler(catch)
        assoc.unregister()
    check(not assoc._registered("media"), "…and taken out again")


if __name__ == "__main__" and len(sys.argv) > 1:  # run just some: python scripts/selftest.py every_tool
    for name in sys.argv[1:]:
        step(name, globals()[name])
    print(f"selftest: {len(problems)} problem(s)")
    sys.exit(1 if problems else 0)

for name, fn in (("archive tool", archive_tool), ("right-click menu", right_click_menu), ("every tool", every_tool), ("uninstaller", uninstaller), ("list", listing), ("extract", extract_all), ("extract one", extract_some), ("open", open_one),
                 ("test", test_it), ("pack", pack_and_edit), ("video", video)):
    step(name, fn)
print(f"selftest: {len(problems)} problem(s)")
sys.exit(1 if problems else 0)
