"""The Files tools never do harm: an archive can't write outside its folder or leave a link behind, a
password never goes on a command line or into a log, Undo never writes over a file, Unlock never closes
a part of Windows, and Cloud never offers an online sign-in it can't do."""
import io
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from corefold import archives, cloud, organizer, unlock
from corefold.core import Ctx, Options


def _ctx(**params) -> Ctx:
    import threading
    return Ctx(Options(), lambda f: None, threading.Event(), params)


def _outside_files(base: Path) -> set[str]:
    return {p.name for p in base.parent.iterdir()} if base.parent.exists() else set()


def test_a_tar_cant_write_outside_or_bring_links(tmp_path):
    src = tmp_path / "in" / "bad.tar"
    src.parent.mkdir()
    with tarfile.open(src, "w") as tf:
        for name, data in (("ok.txt", b"fine"), ("../escaped.txt", b"no"), ("/abs.txt", b"no")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("sys")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc"
        tf.addfile(link)
    dest = tmp_path / "out"
    try:
        archives.extract(src, _ctx(dest=str(dest)), "folder")
    except Exception:  # (refusing the whole archive is safe too)
        pass
    assert not (tmp_path / "escaped.txt").exists() and not (tmp_path / "in" / "escaped.txt").exists()
    assert not any(p.is_symlink() for p in dest.rglob("*"))
    assert not Path("/abs.txt").exists()


def test_a_zip_cant_write_outside(tmp_path, monkeypatch):
    monkeypatch.setattr(archives, "find_7z", lambda: None)  # (Python's own unzip, used without 7-Zip)
    src = tmp_path / "bad.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("ok.txt", "fine")
        zf.writestr("../../escaped.txt", "no")
    dest = tmp_path / "deep" / "out"
    archives.extract(src, _ctx(dest=str(dest)), "folder")
    assert not (tmp_path / "escaped.txt").exists() and not (tmp_path / "deep" / "escaped.txt").exists()
    assert any(p.name == "ok.txt" for p in dest.rglob("*"))


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no links here")
def test_links_that_came_out_of_an_archive_are_left_out(tmp_path):
    keep = tmp_path / "elsewhere"
    keep.mkdir()
    (keep / "precious.txt").write_text("stays")
    staging = tmp_path / "staging"
    (staging / "sub").mkdir(parents=True)
    (staging / "sub" / "real.txt").write_text("real")
    try:
        os.symlink(keep, staging / "sub" / "to-elsewhere", target_is_directory=True)
        os.symlink(keep / "precious.txt", staging / "file-link.txt")
    except OSError:
        pytest.skip("this PC doesn't let a test make links")
    assert archives._drop_links(staging) == 2
    assert (keep / "precious.txt").read_text() == "stays"  # (only the link went, never what it pointed at)
    assert (staging / "sub" / "real.txt").exists()
    assert not os.path.lexists(staging / "sub" / "to-elsewhere") and not os.path.lexists(staging / "file-link.txt")


def test_an_archive_password_never_goes_on_the_command_line_or_in_a_log():
    secret = "hunter2-Ünï"
    cmd, typed = archives._with_password(["7z", "x", "a.7z", archives._pw(secret)])
    assert not any(secret in str(a) for a in cmd)
    assert typed is not None and secret.encode("utf-8") in typed
    assert secret not in repr(archives._pw(secret)) and secret not in str([archives._pw(secret)])
    made, _ = archives._with_password(["7z", "a", "b.7z", archives._pw(secret, making=True)])
    assert not any(secret in str(a) for a in made)


def test_undo_never_writes_over_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(organizer, "_state_path", lambda: tmp_path / "organizer.json")
    folder = tmp_path / "Downloads"
    (folder / "Pictures").mkdir(parents=True)
    moved = folder / "Pictures" / "photo.jpg"
    moved.write_bytes(b"the sorted photo")
    st = moved.stat()
    organizer._save({"last": {"moves": [[str(folder / "photo.jpg"), str(moved), st.st_size, st.st_mtime]],
                              "made": [str(folder / "Pictures")]}})
    (folder / "photo.jpg").write_bytes(b"a new, unrelated photo")  # (put there after the sort)
    res = organizer.undo()
    assert (folder / "photo.jpg").read_bytes() == b"a new, unrelated photo"
    back = [p for p in folder.iterdir() if p.is_file() and p.name != "photo.jpg"]
    assert res.back + len(res.failed) + res.changed == 1
    if res.back:
        assert res.renamed == 1 and back and back[0].read_bytes() == b"the sorted photo"


@pytest.mark.parametrize("name", sorted(unlock.CRITICAL))
def test_unlock_never_closes_a_part_of_windows(name, monkeypatch):
    called = []

    class Asked:  # (any Windows call: noted, never made)
        def __getattr__(self, fn):
            return lambda *a: called.append(fn)
    monkeypatch.setattr(unlock, "_dll", lambda n: Asked())
    info = unlock.RM_PROCESS_INFO()
    info.Process.dwProcessId = 4242
    info.strAppName = ""
    info.ApplicationType = unlock.RM_MAIN_WINDOW
    monkeypatch.setattr(unlock, "_exe_of", lambda pid: os.sep.join(("C:", "Windows", "System32", name)))
    monkeypatch.setattr(unlock, "_can_close", lambda pid: True)
    prog = unlock.program_of(info, {})
    assert prog.critical and not prog.closable, name
    assert unlock.force_close(prog) is False
    assert unlock.close_politely([prog]) == {4242: "denied"}
    assert called == []  # (nothing was asked of Windows: no closing, no ending)


def test_restart_managers_critical_kind_is_never_closed(monkeypatch):
    monkeypatch.setattr(unlock, "_exe_of", lambda pid: os.sep.join(("C:", "Tools", "some-agent.exe")))
    monkeypatch.setattr(unlock, "_can_close", lambda pid: True)
    info = unlock.RM_PROCESS_INFO()
    info.Process.dwProcessId = 777
    info.ApplicationType = unlock.RM_CRITICAL
    assert not unlock.program_of(info, {}).closable


def test_cloud_offers_no_online_sign_in_it_cant_do(monkeypatch):
    monkeypatch.setattr(cloud, "GOOGLE_CLIENT_SECRET", "")  # (Google needs both its id and its secret)
    monkeypatch.setattr(cloud, "ONEDRIVE_CLIENT_ID", "")
    monkeypatch.setattr(cloud, "DROPBOX_APP_KEY", "")
    assert not any(cloud.available(k) for k in cloud.ONLINE)


def test_google_drive_sign_in_comes_built_in():
    from corefold import cloudkeys
    assert cloudkeys.GOOGLE_CLIENT_ID.endswith(".apps.googleusercontent.com")
    assert cloudkeys.GOOGLE_CLIENT_SECRET


def test_a_sound_never_holds_the_window_up(monkeypatch):
    import sys
    import threading
    import time
    from corefold import winutil
    started, release = threading.Event(), threading.Event()

    class SlowSound:  # (Windows waking a sleeping sound device first)
        SND_FILENAME = SND_ASYNC = SND_NODEFAULT = 0

        @staticmethod
        def PlaySound(*a):
            started.set()
            release.wait(5)
    monkeypatch.setitem(sys.modules, "winsound", SlowSound)
    t0 = time.perf_counter()
    winutil.play_sound(Path("done.wav"))
    assert time.perf_counter() - t0 < 0.2
    assert started.wait(2)  # (it is played, just not on the window's thread)
    release.set()


def _disk_full(*args, **kwargs):
    raise OSError(28, "No space left on device")


def _pictures(folder: Path, count: int) -> list[Path]:
    from PIL import Image
    folder.mkdir()
    made = []
    for i in range(count):
        p = folder / f"p{i}.png"
        Image.new("RGB", (40, 30), (i * 40, 90, 160)).save(p)
        made.append(p)
    return made


def test_a_full_disk_stops_a_folder_conversion_and_leaves_nothing_half_done(tmp_path, monkeypatch):
    from corefold import converters as cv, moretools
    pics = _pictures(tmp_path / "holiday", 5)
    before = {p: p.read_bytes() for p in pics}
    real_save, saved = moretools.save_image, []

    def save_then_fill(im, target, ext, ctx):
        if len(saved) == 2:
            Path(target).write_bytes(b"\xff\xd8 half a picture")  # (what a cut-off write leaves)
            _disk_full()
        real_save(im, target, ext, ctx)
        saved.append(target)

    monkeypatch.setattr(moretools, "save_image", save_then_fill)
    tool = next(t for t in cv.catalog()["folder"] if t.key == "folder:jpg")
    out = tmp_path / "out"
    res, errors = cv.run_job(tool, [tmp_path / "holiday"], Options(output_folder=out))
    assert errors and "the disk is full" in errors[0]  # (not "3 couldn't be read": they could)
    assert not res.outputs and not any(out.rglob("*"))  # (no half-done folder, no half a picture)
    assert {p: p.read_bytes() for p in pics} == before


def test_a_folder_conversion_tells_unreadable_from_unsaved(tmp_path, monkeypatch):
    from corefold import moretools
    pics = _pictures(tmp_path / "mixed", 3)
    pics[0].write_bytes(b"not a picture at all")
    real_save = moretools.save_image

    def refuse_one(im, target, ext, ctx):
        if Path(target).stem == "p1":
            Path(target).write_bytes(b"half")
            raise PermissionError(13, "Access is denied")
        real_save(im, target, ext, ctx)

    monkeypatch.setattr(moretools, "save_image", refuse_one)
    res = moretools.convert_folder(tmp_path / "mixed", _ctx(), "jpg")
    made = sorted(p.name for p in res.outputs[0].rglob("*"))
    assert made == ["p2.jpg"]  # (p1's half-written copy is gone)
    assert "1 couldn't be read" in res.note and "1 couldn't be saved" in res.note


def test_a_full_disk_never_says_a_pdf_has_no_pictures(tmp_path, monkeypatch):
    from PIL import Image
    from corefold import converters as cv
    pages = [Image.new("RGB", (60, 80), (200, 30 * i, 40)) for i in range(3)]
    pdf = tmp_path / "scan.pdf"
    pages[0].save(pdf, save_all=True, append_images=pages[1:])
    monkeypatch.setattr(Path, "write_bytes", _disk_full)  # (Pillow's pages are JPEGs: saved as they are)
    tool = next(t for t in cv.catalog()["pdf"] if t.key == "pdf:images")
    out = tmp_path / "out"
    res, errors = cv.run_job(tool, [pdf], Options(output_folder=out))
    assert errors and "the disk is full" in errors[0] and not res.note
    assert not any(out.rglob("*"))


@pytest.mark.parametrize("said", ["System ERROR:\nThere is not enough space on the disk.",
                                  "System ERROR:\nerrno=28 : No space left on device"])
def test_7zip_running_out_of_room_is_said_plainly(said):
    assert str(archives._7z_error(said)) == "the disk is full"


def test_a_tiff_that_cant_be_written_for_lack_of_room_says_the_disk_is_full(tmp_path, monkeypatch):
    import shutil
    from PIL import Image
    from corefold import converters as cv
    src = tmp_path / "photo.png"
    Image.new("RGB", (300, 200), (10, 120, 200)).save(src)

    def libtiff_cant_write(*args, **kwargs):
        raise OSError("encoder error -2 when writing image file")  # (all libtiff says on a full disk)

    monkeypatch.setattr(Image.Image, "save", libtiff_cant_write)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(16 << 20, 16 << 20, 4096))
    tool = next(t for t in cv.catalog()["image"] if t.key == "image:tiff")
    res, errors = cv.run_job(tool, [src], Options(output_folder=tmp_path / "out"))
    assert errors == ["photo.png: the disk is full"] and not res.outputs


def test_saving_elsewhere_is_said(tmp_path, monkeypatch):
    """When the folder a file was meant for can't be saved in, it goes to Downloads › Corefold, and the
    message says so (looked for beside the original, it wouldn't be found)."""
    from types import SimpleNamespace

    from corefold import app, core
    monkeypatch.setattr(core, "_downloads", tmp_path / "Downloads")
    spare = core.fallback_folder()
    src = tmp_path / "CD" / "photo.heic"
    beside = SimpleNamespace(settings=SimpleNamespace(save_mode="beside", output_folder=str(spare)))
    said = app.Controller._saved_elsewhere(beside, [src], [spare / "photo.jpg"])
    assert said == "Downloads › Corefold"
    assert app.Controller._saved_elsewhere(beside, [src], [src.parent / "photo.jpg"]) == ""
    assert app.Controller._saved_elsewhere(beside, [spare / "a.png"], [spare / "a.jpg"]) == ""  # (it was there)
    chosen = SimpleNamespace(settings=SimpleNamespace(save_mode="folder", output_folder=str(spare)))
    assert app.Controller._saved_elsewhere(chosen, [src], [spare / "photo.jpg"]) == ""  # (the folder picked)
    other = SimpleNamespace(settings=SimpleNamespace(save_mode="folder", output_folder=str(tmp_path / "Mine")))
    assert app.Controller._saved_elsewhere(other, [src], [spare / "sub" / "photo.jpg"]) == "Downloads › Corefold"


def test_packing_deep_inside_folders_keeps_only_the_names(tmp_path, monkeypatch):
    """Past Windows' 260 letters 7-Zip can't be started inside the folder: it gets whole paths instead, and the
    archive still holds just the names, as it would anywhere else."""
    if not archives.find_7z():
        pytest.skip("needs 7-Zip")
    monkeypatch.setattr(archives, "_too_deep", lambda folder: True)
    (tmp_path / "a.txt").write_text("a", "utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("b", "utf-8")
    out = tmp_path / "out" / "x.zip"
    out.parent.mkdir()
    archives.pack_into([tmp_path / "a.txt", tmp_path / "sub"], out, Ctx(Options()))
    assert sorted(zipfile.ZipFile(out).namelist()) == ["a.txt", "sub/", "sub/b.txt"]


def test_a_path_too_long_for_windows_is_said_plainly():
    from corefold.converters import friendly_error
    e = NotADirectoryError(267, "The directory name is invalid")
    e.winerror = 267
    assert "WinError" not in friendly_error(e) and "too long" in friendly_error(e)


def test_two_files_with_one_name_in_an_archive_are_both_kept(tmp_path):
    """A ZIP can hold two files under one name (or names only the case tells apart, which are one name to
    Windows): unpacking keeps both, instead of the second silently writing over the first."""
    import warnings
    if not archives.find_7z():
        pytest.skip("needs 7-Zip")
    src = tmp_path / "twice.zip"
    with warnings.catch_warnings(), zipfile.ZipFile(src, "w") as zf:
        warnings.simplefilter("ignore")
        zf.writestr("a.txt", "first")
        zf.writestr("a.txt", "second")
        zf.writestr("A.TXT", "third")
    out = tmp_path / "out"
    out.mkdir()
    archives._unpack(src, out, Ctx(Options()), "")
    assert sorted(p.read_text("utf-8") for p in out.iterdir()) == ["first", "second", "third"]
