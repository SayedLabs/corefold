"""Computer is back with Space and the Uninstaller (Startup and Scan rest a while longer): the clean-up checks
what it found again right before it removes it, and an account's own Windows folders never go to the
Recycle Bin from the map (what's inside them can)."""
import os
import time
from pathlib import Path

from corefold import space

OLD = time.time() - 5 * 86400


def _old(path: Path):
    os.utime(path, (OLD, OLD))


def _profile(*parts: str) -> Path:
    return Path(Path.home().anchor, "Users", *parts)


def test_an_accounts_own_folders_stay_but_what_is_inside_can_go():
    for parts in (("sam",), ("sam", "Desktop"), ("sam", "Documents"), ("sam", "Downloads"), ("sam", "OneDrive"),
                  ("sam", "AppData", "Roaming"), ("sam", "AppData", "Local", "Programs")):
        assert space.protected(_profile(*parts)), parts
        assert space.windows_folder(_profile(*parts)), parts
    for parts in (("sam", "Documents", "Old projects"), ("sam", "Downloads", "setup.exe"), ("sam", "big.iso"),
                  ("sam", "Videos", "Holiday")):
        assert not space.protected(_profile(*parts)), parts
        assert not space.windows_folder(_profile(*parts)), parts


def test_the_onedrive_folder_itself_stays(tmp_path, monkeypatch):
    cloud = tmp_path / "OneDrive - Work"
    cloud.mkdir()
    monkeypatch.setenv("OneDriveCommercial", str(cloud))
    assert space.protected(cloud)
    assert not space.protected(cloud / "Reports")


def test_temporary_files_used_again_since_they_were_found_stay(tmp_path):
    old_file, new_file = tmp_path / "old.tmp", tmp_path / "new.tmp"
    old_dir, busy_dir = tmp_path / "olddir", tmp_path / "busydir"
    for f in (old_file, new_file):
        f.write_bytes(b"x" * 10)
    old_dir.mkdir()
    busy_dir.mkdir()
    (old_dir / "a.txt").write_bytes(b"x")
    (busy_dir / "a.txt").write_bytes(b"x")
    _old(old_file)
    _old(old_dir / "a.txt")
    _old(old_dir)
    _old(busy_dir)  # (an old folder, but a file in it changed just now: a program is still using it)
    junk = space.Junk("temp", "Temporary files", "", paths=[old_file, new_file, old_dir, busy_dir], size=40)
    space.clean(junk)
    assert not old_file.exists() and not old_dir.exists()
    assert new_file.exists() and busy_dir.exists() and (busy_dir / "a.txt").exists()


def test_a_cache_of_a_browser_started_since_stays(tmp_path, monkeypatch):
    cache = tmp_path / "Cache"
    cache.mkdir()
    (cache / "data_1").write_bytes(b"x" * 100)
    junk = space.Junk("browser:chrome.exe", "{name}'s cache", "", paths=[cache], size=100, name="Google Chrome")
    monkeypatch.setattr(space, "running_programs", lambda: {"chrome.exe"})
    assert space.clean(junk) == 0
    assert (cache / "data_1").exists()
    monkeypatch.setattr(space, "running_programs", lambda: set())
    assert space.clean(junk) == 100
    assert cache.is_dir() and not any(cache.iterdir())  # (the folder stays, empty)
