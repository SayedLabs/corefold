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
