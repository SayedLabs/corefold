"""Checks for the fixes of the security audit of 1.15.2: links in "The previous Windows", Windows' own
programs found from Windows itself, uninstallers named without a folder, winget ids, risky email
attachments and the person's own main folders in Space."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from corefold import appupdates, core, space
from corefold import uninstall as un


def _link(link: Path, target: Path):
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=True)
    else:
        os.symlink(target, link, target_is_directory=True)


# ---- The previous Windows: links inside go first, only the links

def test_links_inside_are_removed_not_followed(tmp_path):
    today = tmp_path / "today"
    today.mkdir()
    (today / "mine.txt").write_text("someone's own file")
    old = tmp_path / "Windows.old"
    (old / "Users" / "Old").mkdir(parents=True)
    (old / "Users" / "Old" / "letter.txt").write_text("old")
    _link(old / "Documents and Settings", today)
    _link(old / "Users" / "Old" / "Application Data", today)
    assert space._drop_links_inside(old) is True
    assert not os.path.lexists(old / "Documents and Settings")
    assert not os.path.lexists(old / "Users" / "Old" / "Application Data")
    assert (old / "Users" / "Old" / "letter.txt").exists()      # (the real things stay for rd)
    assert (today / "mine.txt").read_text() == "someone's own file"


def test_a_previous_windows_that_is_a_link_is_left_alone(tmp_path, monkeypatch):
    today = tmp_path / "today"
    today.mkdir()
    (today / "mine.txt").write_text("x")
    fake = tmp_path / "Windows.old"
    _link(fake, today)
    ran = []
    monkeypatch.setattr(space, "admin_path", lambda key: fake if key == "winold" else None)
    monkeypatch.setattr(space, "_previous_windows_by_windows", lambda *a: ran.append("cleanmgr") or False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    assert space.clean_as_admin(["winold"]) == 0
    assert ran == []
    assert (today / "mine.txt").exists()


# ---- Windows' own programs

def test_windows_programs_come_from_windows_not_the_environment(monkeypatch, tmp_path):
    if sys.platform != "win32":
        pytest.skip("Windows only")
    real = core.windows_folder()
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    assert core.windows_folder() == real
    assert Path(core.system_tool("reg.exe")).parent == real / "System32"


def test_uninstaller_named_without_a_folder_is_windows_own():
    app = un.App(id="x", name="Thing", key="{X}", uninstall="MsiExec.exe /I{12345678-1234-1234-1234-123456789012}")
    exe, args = un.uninstall_command(app)
    assert args.lower().startswith("/x {")
    if sys.platform == "win32":
        assert os.path.isabs(exe) and exe.lower().endswith(os.path.join("system32", "msiexec.exe"))


# ---- winget

def test_winget_id_never_an_option():
    table = ("Name   Id                Version  Available Source\n"
             "-----------------------------------------------------\n"
             "Bad    --override=x      1.0      2.0       winget\n"
             "Good   Vendor.Good       1.0      2.0       winget\n")
    found = appupdates.parse(table)
    assert [u.id for u in found] == ["Vendor.Good"]


# ---- email attachments

def test_more_attachments_ask_first():
    from corefold import mailview
    for ext in (".one", ".xll", ".settingcontent-ms", ".search-ms", ".appref-ms", ".rdp", ".msp", ".inf"):
        assert ext in mailview.RISKY


# ---- Space: the person's own main folders

def test_main_folders_are_kept_but_not_what_is_inside():
    home = Path.home()
    for name in ("Documents", "Desktop", "Downloads", "Pictures"):
        assert space.protected(home / name)
    assert not space.protected(home / "Downloads" / "big video.mp4")
