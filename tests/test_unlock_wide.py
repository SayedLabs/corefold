"""A whole drive (that isn't a USB drive) or one of Windows' own big folders is never looked at: nearly every
program uses something in C:\\ or C:\\Windows, so "Close all" (or "Close all and eject" for a drive) would have
asked every program to close. A program's own folder or any file can still be checked."""
import os
import sys
import types
from pathlib import Path

import pytest
from PySide6.QtWidgets import QHBoxLayout

from corefold import theme, unlock


def test_drives_and_windows_folders_are_too_wide(tmp_path, monkeypatch):
    monkeypatch.setattr(unlock, "usb_drives", lambda: [unlock.Drive("E:\\")])
    assert unlock.too_wide("C:\\") and unlock.too_wide("D:")
    if sys.platform == "win32":
        assert not unlock.too_wide("E:\\")
        windows = os.environ["SystemRoot"]
        assert unlock.too_wide(windows) and unlock.too_wide(os.path.join(windows, "System32"))
        assert unlock.too_wide(os.environ["ProgramFiles"]) and unlock.too_wide(Path.home())
        assert unlock.too_wide(os.environ["LOCALAPPDATA"])
        assert not unlock.too_wide(os.path.join(windows, "notepad.exe"))  # (a file: only who has it open)
    folder = tmp_path / "project"
    folder.mkdir()
    assert not unlock.too_wide(folder) and not unlock.too_wide(tmp_path / "x.txt")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own drive")
def test_the_page_wont_look_at_the_windows_drive(qtbot, monkeypatch):
    from corefold import unlockpage
    looked = []
    monkeypatch.setattr(unlock, "find", lambda paths: looked.append(paths))
    page = unlockpage.UnlockPage(types.SimpleNamespace(theme=theme.build_theme("light", "core"),
                                                       settings=types.SimpleNamespace()), QHBoxLayout())
    page.check([Path(os.environ.get("SystemDrive", "C:") + "\\")])
    qtbot.wait(300)
    assert not looked and not page.busy and "whole drive" in page.problem.text()
    for b in (page.close_eject_btn, page.eject_btn, page.close_all_btn, page.then_btn, page.delete_btn):
        assert not b.isVisibleTo(page.result)
    page.deleteLater()


def test_programs_run_from_a_drive_never_include_windows_parts(monkeypatch):
    monkeypatch.setattr(unlock, "_exe_of", lambda pid: "E:\\tools\\" + {10: "app.exe", 11: "svchost.exe"}[pid])
    monkeypatch.setattr(unlock, "_started", lambda pid: 1)
    monkeypatch.setattr(unlock, "_can_close", lambda pid: True)
    found = {p.pid: p for p in unlock.running_from(["E:\\"], {10: "app.exe", 11: "svchost.exe"})}
    assert found[10].closable and not found[11].closable
