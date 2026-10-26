"""Unlock's USB drives: the list shows what's plugged in, "Eject" first looks at what's using the drive
(its files, and programs run from it) and only asks Windows to eject it when nothing is; a whole drive is
never offered to be deleted, moved or renamed."""
import sys
import types

import pytest
from PySide6.QtWidgets import QHBoxLayout

from corefold import theme, unlock


def test_what_counts_as_a_whole_drive():
    for yes in ("E:\\", "E:", "e:/"):
        assert unlock.is_drive(yes), yes
    for no in ("E:\\photos", "\\\\server\\share", "", "/", "EE:\\"):
        assert not unlock.is_drive(no), no


def test_a_disk_id_from_its_path():
    path = "\\\\?\\usbstor#disk&ven_kingston&prod_datatraveler_3.0#60a44c42&0#{53f56307-b6bf-11d0-94f2-00a0c91efb8b}"
    if sys.platform != "win32":
        assert unlock.instance_of(path) == "USBSTOR\\DISK&VEN_KINGSTON&PROD_DATATRAVELER_3.0\\60A44C42&0"


def test_a_program_run_from_the_drive_is_found(monkeypatch):
    monkeypatch.setattr(unlock, "files_of", lambda paths: ([], False))
    monkeypatch.setattr(unlock, "process_names", lambda: {})
    game = unlock.Program(pid=77, name="game", exe="E:\\game.exe")
    monkeypatch.setattr(unlock, "running_from", lambda roots, names=None: [game] if roots == ["E:\\"] else [])
    assert unlock.find(["E:\\"]).programs == [game]
    assert unlock.find(["F:\\notes.txt"]).programs == []


def _page(monkeypatch, tmp_path, holders):
    from corefold import unlockpage
    root = str(tmp_path)
    monkeypatch.setattr(unlock, "is_drive", lambda p: str(p) == root)
    monkeypatch.setattr(unlock, "drive_letters", lambda: 16)
    monkeypatch.setattr(unlock, "usb_drives", lambda: [unlock.Drive(root, "KINGSTON", 32 << 30, 20 << 30)])
    calls = {"found": 0, "ejected": []}

    def find(paths):
        calls["found"] += 1
        return unlock.Found(list(paths), [unlock.Program(pid=9, name="Photos")] if holders(calls["found"]) else [])
    monkeypatch.setattr(unlock, "find", find)
    monkeypatch.setattr(unlock, "close_politely", lambda programs, wait_s=0: {p.pid: "closed" for p in programs})
    monkeypatch.setattr(unlock, "eject", lambda r: calls["ejected"].append(r) or {"ok": True})
    page = unlockpage.UnlockPage(types.SimpleNamespace(theme=theme.build_theme("light", "core"),
                                                       settings=types.SimpleNamespace()), QHBoxLayout())
    return page, calls, root


def test_eject_says_what_is_using_it_and_does_nothing(qtbot, tmp_path, monkeypatch):
    page, calls, root = _page(monkeypatch, tmp_path, holders=lambda n: True)
    page._look_for_drives()
    qtbot.waitUntil(lambda: len(page.drive_rows) == 1)
    assert page.drives[0].label == "KINGSTON" and not page.drives_none.isVisibleTo(page.drives_card)
    page.eject_drive(root)
    qtbot.waitUntil(lambda: page.found is not None and not page.busy)
    assert calls["ejected"] == [] and "Photos" in page.problem.text()
    assert page.close_eject_btn.isVisibleTo(page.result)
    for b in (page.delete_btn, page.move_btn, page.rename_btn, page.then_btn, page.copy_btn, page.later_btn):
        assert not b.isVisibleTo(page.result)


def test_close_all_and_eject(qtbot, tmp_path, monkeypatch):
    page, calls, root = _page(monkeypatch, tmp_path, holders=lambda n: n == 1)
    page._look_for_drives()
    qtbot.waitUntil(lambda: len(page.drives) == 1)
    page.check([root])
    qtbot.waitUntil(lambda: page.found is not None and not page.busy)
    page.close_then("eject")
    qtbot.waitUntil(lambda: calls["ejected"] == [root], timeout=5000)
    qtbot.waitUntil(lambda: not page.busy)
    assert "KINGSTON" in page.status.text() and not page.eject_btn.isVisibleTo(page.result)


def test_windows_says_it_is_still_in_use(qtbot, tmp_path, monkeypatch):
    page, calls, root = _page(monkeypatch, tmp_path, holders=lambda n: False)
    monkeypatch.setattr(unlock, "eject", lambda r: {"ok": False, "why": "busy"})
    page.check([root])
    qtbot.waitUntil(lambda: page.found is not None and not page.busy)
    assert page.eject_btn.isVisibleTo(page.result)
    page.eject(root)
    qtbot.waitUntil(lambda: calls["found"] >= 2 and not page.busy, timeout=5000)
    assert "still in use" in page.problem.text()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own drives")
def test_on_windows_the_drives_can_be_asked_about():
    assert unlock.drive_letters()
    assert isinstance(unlock.usb_drives(), list)
    assert isinstance(unlock._describe("C:\\"), tuple)
    free = next(f"{c}:\\" for c in "QPONMLKJ" if not unlock.drive_letters() & (1 << (ord(c) - 65)))
    assert unlock.eject(free) == {"ok": False, "why": "gone"}
