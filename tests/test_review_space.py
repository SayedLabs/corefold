"""Checks for fixes in Corefold Space (clean-up, duplicates) and the Uninstaller's leftover logic."""
import os
import time
from pathlib import Path

import pytest

from corefold import space
from corefold import uninstall as un

OLD = time.time() - 5 * 86400


def _old(path: Path):
    os.utime(path, (OLD, OLD))


# ---- the temporary folder

def test_temp_folder_only_when_it_is_one(tmp_path, monkeypatch):
    real = tmp_path / "Temp"
    real.mkdir()
    monkeypatch.delenv("TMP", raising=False)
    monkeypatch.setenv("TEMP", str(real))
    assert space.temp_folder() == Path(os.path.abspath(real))
    other = tmp_path / "Projects"
    other.mkdir()
    (other / "2").mkdir()
    monkeypatch.setenv("TEMP", str(real / "2"))
    (real / "2").mkdir()
    assert space.temp_folder() is not None  # (a session's own, "Temp\\2")
    monkeypatch.setenv("TEMP", str(other / "2"))
    assert space.temp_folder() is None
    monkeypatch.setenv("TEMP", str(other))
    assert space.temp_folder() is None  # (not a temporary folder by its name)
    monkeypatch.setenv("TEMP", str(Path.home()))
    assert space.temp_folder() is None
    monkeypatch.delenv("TEMP")
    assert space.temp_folder() is None  # (not set: never the current folder)


def test_temp_folder_in_use_inside_is_left(tmp_path, monkeypatch):
    temp = tmp_path / "Temp"
    busy, idle = temp / "busy", temp / "idle"
    busy.mkdir(parents=True)
    idle.mkdir()
    (busy / "now.txt").write_text("x")      # changed just now, in an old folder
    (idle / "old.txt").write_text("x")
    _old(idle / "old.txt")
    _old(busy)
    _old(idle)
    monkeypatch.delenv("TMP", raising=False)
    monkeypatch.setenv("TEMP", str(temp))
    monkeypatch.setattr(space, "_admin_junk", lambda cancel=None: [])
    found = {j.key: j for j in space.find_junk(None)}
    names = {p.name for p in found["temp"].paths}
    assert "idle" in names and "busy" not in names


def test_protected_covers_appdata():
    assert space.protected(Path.home() / "AppData")
    assert space.protected(Path.home() / "AppData" / "LocalLow")


# ---- duplicates

def _pair(tmp_path, link: bool = False):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"z" * 4096)
    if link:
        os.link(a, b)
    else:
        b.write_bytes(b"z" * 4096)
    return a, b


def test_hard_links_are_not_duplicates(tmp_path):
    a, _b = _pair(tmp_path, link=True)
    assert space.find_duplicates([tmp_path], min_size=1) == []
    (tmp_path / "c.bin").write_bytes(a.read_bytes())
    groups = space.find_duplicates([tmp_path], min_size=1)
    assert len(groups) == 1 and len(groups[0]) == 2


def test_copies_to_remove_checks_the_kept_copy(tmp_path):
    a, b = _pair(tmp_path)

    def info(p):
        st = p.stat()
        return str(p), st.st_size, st.st_mtime
    groups = [([info(b)], [info(a)])]
    assert space.copies_to_remove(groups) == [str(b)]
    later = time.time() + 100
    os.utime(a, (later, later))  # the kept copy changed since
    assert space.copies_to_remove(groups) == []
    a.unlink()                  # the kept copy is gone
    assert space.copies_to_remove(groups) == []


def test_copies_to_remove_never_the_kept_file_by_another_name(tmp_path):
    a, b = _pair(tmp_path, link=True)
    st = a.stat()
    assert space.copies_to_remove([([(str(b), st.st_size, st.st_mtime)], [(str(a), st.st_size, st.st_mtime)])]) == []


# ---- the Uninstaller

def test_names_without_maker_are_not_used_as_whole_names():
    app = un.App(id="x", name="Epic Games Launcher", publisher="Epic Games, Inc.")
    assert "launcher" in app.names()
    assert "launcher" not in app.names(short=False)


def test_own_leaves_another_apps_folder_inside(tmp_path):
    loc = str(tmp_path / "Vendor")
    other = str(tmp_path / "Vendor" / "Other")
    assert un._own(str(tmp_path / "Vendor" / "tool.exe"), loc, [other])
    assert not un._own(str(tmp_path / "Vendor" / "Other" / "other.exe"), loc, [other])
    assert not un._own(str(tmp_path / "Vendor" / "tool.exe"), loc, [loc])  # (a folder two apps share)


def test_size_cut_short_is_said(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "f").write_bytes(b"x" * 10)
    assert un.folder_size_whole(tmp_path, limit_s=-1)[1] is False
    assert un.folder_size_whole(tmp_path) == (10, True)


def test_list_apps_keeps_two_installs_of_one_app(monkeypatch, tmp_path):
    entries = {
        ("HKLM", 64): {"A": {"DisplayName": "Foo", "DisplayVersion": "1", "UninstallString": "u.exe",
                             "InstallLocation": str(tmp_path / "pf" / "Foo")}},
        ("HKLM", 32): {"A": {"DisplayName": "Foo", "DisplayVersion": "1", "UninstallString": "u.exe",
                             "InstallLocation": str(tmp_path / "pf" / "Foo")}},
        ("HKCU", 0): {"B": {"DisplayName": "Foo", "DisplayVersion": "1", "UninstallString": "u.exe",
                            "InstallLocation": str(tmp_path / "user" / "Foo")}},
    }
    monkeypatch.setattr(un, "_subkeys", lambda hive, path, view: list(entries.get((hive, view), {})))
    monkeypatch.setattr(un, "_values", lambda hive, path, view: entries[(hive, view)][path.rsplit("\\", 1)[-1]])
    apps = un.list_apps(include_store=False)
    assert sorted(a.hive for a in apps) == ["HKCU", "HKLM"]  # (the same install in both views: once)


def test_folder_that_could_not_move_is_not_counted(tmp_path, monkeypatch):
    src = tmp_path / "Leftover"
    src.mkdir()
    (src / "file.txt").write_text("x" * 100)
    backup = tmp_path / "backup"
    backup.mkdir()

    def refuse(*_a, **_k):
        raise PermissionError("in use")
    monkeypatch.setattr(un, "_move", refuse)
    monkeypatch.setattr(un.os, "replace", refuse)
    item = un.Leftover("folder", str(src), "install", size=100)
    if not un.item_allowed(dict(un.asdict(item))):
        pytest.skip("the temporary folder is a place the Uninstaller never cleans")
    result = un.remove([item], backup)
    assert result["freed"] == 0 and not result["done"] and len(result["failed"]) == 1
    assert (src / "file.txt").exists()
    assert not any(f for _r, _d, f in os.walk(backup / "files")) if (backup / "files").exists() else True
