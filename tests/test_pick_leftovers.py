"""Choosing leftovers one by one: a leftover folder or registry key opens to show what's inside it, so some
of it can stay while the rest goes (with the same backup and undo as a whole leftover)."""
import os
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon

from corefold import settings as settings_mod
from corefold import uninstall as un

WINDOWS = sys.platform == "win32"


def _app_folder(tmp_path: Path) -> Path:
    acme = tmp_path / "Acme Studio"
    (acme / "bin").mkdir(parents=True)
    (acme / "bin" / "acme.dll").write_bytes(b"x" * 300)
    (acme / "bin" / "acme.exe").write_bytes(b"x" * 200)
    (acme / "my project.acme").write_bytes(b"precious")
    (acme / "cache.db").write_bytes(b"x" * 100)
    return acme


# ---- what's inside, and what may go

def test_parts_are_listed_folders_first_each_a_leftover_of_its_own(tmp_path):
    acme = _app_folder(tmp_path)
    whole = un.Leftover("folder", str(acme), "install", level=3, app="Acme Studio")
    parts = un.children_of(whole)
    assert [Path(p.path).name for p in parts] == ["bin", "cache.db", "my project.acme"]
    assert [p.kind for p in parts] == ["folder", "file", "file"]
    assert all(p.inside == str(acme) and p.reason == "install" and p.level == 3 and p.app == "Acme Studio"
               for p in parts)
    assert parts[1].size == 100
    deeper = un.children_of(parts[0])  # (a part of a part still names the whole leftover)
    assert [Path(p.path).name for p in deeper] == ["acme.dll", "acme.exe"]
    assert all(p.inside == str(acme) for p in deeper)
    assert all(un.item_allowed(dict(p.__dict__)) for p in parts + deeper)


@pytest.mark.skipif(not WINDOWS, reason="junctions are Windows' own")
def test_links_inside_are_not_listed_as_parts(tmp_path):
    import _winapi
    acme = _app_folder(tmp_path)
    elsewhere = tmp_path / "someone else"
    elsewhere.mkdir()
    _winapi.CreateJunction(str(elsewhere), str(acme / "linked"))
    parts = un.children_of(un.Leftover("folder", str(acme), "install"))
    assert "linked" not in [Path(p.path).name for p in parts]
    assert not un.item_allowed({"kind": "folder", "path": str(acme / "linked"), "inside": str(acme)})


def test_a_part_must_be_truly_inside_a_leftover_that_may_go(tmp_path):
    acme = _app_folder(tmp_path)

    def ok(kind, path, inside, value=""):
        return un.item_allowed({"kind": kind, "path": str(path), "inside": str(inside), "value": value})
    assert ok("file", acme / "cache.db", acme)
    assert ok("folder", acme / "bin", acme)  # ("bin" alone would never be offered: as a part, it may go)
    assert not ok("folder", acme, acme)  # (the whole folder is a whole leftover, never its own part)
    assert not ok("file", tmp_path / "next to it.txt", acme)
    assert not ok("file", f"{acme}{os.sep}..{os.sep}next to it.txt", acme)
    assert not ok("file", str(acme) + " Other" + os.sep + "x.txt", acme)  # (a name that only starts the same)
    for shared in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA"), str(Path.home())):
        if shared:
            assert not ok("folder", Path(shared, "Anything"), shared)  # (inside a place no leftover can be)
    assert not ok("task", "\\Acme\\Updater", acme)


def test_a_registry_part_must_be_inside_an_apps_own_key():
    def ok(kind, path, inside, value=""):
        return un.item_allowed({"kind": kind, "path": path, "inside": inside, "value": value})
    acme = "HKCU\\Software\\Acme"
    assert ok("key", acme + "\\Recent", acme)
    assert ok("value", acme, acme, "Theme") and ok("value", acme + "\\Recent", acme, "File1")
    assert not ok("value", acme, acme, "")       # (the default value goes with its key)
    assert not ok("key", acme, acme)             # (the key itself is a whole leftover)
    assert not ok("key", "HKCU\\Software\\AcmeOther\\x", acme)
    assert not ok("key", acme + "\\\\x", acme)    # (no empty names on the way)
    assert not ok("key", "HKCU\\Software\\Classes\\.txt\\x", "HKCU\\Software\\Classes\\.txt")
    assert not ok("key", "HKCU\\Software\\Microsoft\\Windows\\x", "HKCU\\Software\\Microsoft")
    assert not ok("value", "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run", "HKCU\\Software\\Microsoft", "x")


def test_registry_editor_names():
    assert un.registry_place("HKCU\\Software\\Acme") == "HKEY_CURRENT_USER\\Software\\Acme"
    assert un.registry_place("HKLM\\Software\\Acme", 32) == "HKEY_LOCAL_MACHINE\\SOFTWARE\\WOW6432Node\\Acme"
    assert un.registry_place("HKLM\\Software\\Acme", 64) == "HKEY_LOCAL_MACHINE\\Software\\Acme"


def test_only_the_parts_chosen_go_and_come_back(tmp_path):
    acme = _app_folder(tmp_path)
    parts = un.children_of(un.Leftover("folder", str(acme), "install"))
    keep = [p for p in parts if Path(p.path).name == "my project.acme"]
    chosen = [p for p in parts if p not in keep]
    backup = tmp_path / "backup"
    backup.mkdir()
    res = un.remove(chosen, backup, ["Acme Studio"])
    assert not res["failed"] and len(res["done"]) == 2
    assert (acme / "my project.acme").read_bytes() == b"precious"
    assert not (acme / "bin").exists() and not (acme / "cache.db").exists()
    back = un.restore(backup)
    assert not back["failed"] and back["done"] == 2
    assert (acme / "bin" / "acme.exe").exists() and (acme / "cache.db").exists()


@pytest.mark.skipif(not WINDOWS, reason="the administrator copy's checks are Windows' own")
def test_the_administrator_copy_takes_a_part_the_same_way(tmp_path):
    acme = _app_folder(tmp_path)
    part = un.Leftover("file", str(acme / "cache.db"), "install", inside=str(acme))
    record = dict(part.__dict__)
    assert un.items_from([record])[0].inside == str(acme)  # (the plan it reads keeps what the part is in)
    backup = tmp_path / "backup"
    backup.mkdir()
    res = un.remove(un.items_from([record]), backup, ["Acme Studio"], admin=True)
    assert not res["failed"] and not (acme / "cache.db").exists() and (acme / "bin").exists()
    assert not un.restore(backup, admin=True)["failed"] and (acme / "cache.db").exists()


# ---- the list in the sheet

@pytest.fixture
def controller(qtbot):
    from corefold import app as A
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = False
    s.save()
    c = A.Controller(A.QApplication.instance(), QIcon())
    yield c
    from PySide6.QtCore import QTimer
    from shiboken6 import delete
    c.tray.hide()
    for timer in c.findChildren(QTimer):
        timer.stop()
    delete(c)


def _sheet(controller, qtbot, found):
    from corefold.appspage import UninstallDialog
    app = un.App(id="acme", name="Acme Studio")
    dlg = UninstallDialog(controller, [app], [app], False, QIcon())
    qtbot.addWidget(dlg)
    dlg._state("found", ([app], found, []))
    assert dlg.stack.currentIndex() == 2
    return dlg


def _row(dlg, path):
    from corefold.appspage import APP_ROLE
    for g in range(dlg.left_tree.topLevelItemCount()):
        group = dlg.left_tree.topLevelItem(g)
        for c in range(group.childCount()):
            row = group.child(c)
            if getattr(row.data(0, APP_ROLE), "path", None) == str(path):
                return row
    raise AssertionError(path)


def test_opening_a_folder_lets_some_of_it_stay(controller, qtbot, tmp_path):
    from corefold.appspage import APP_ROLE
    acme = _app_folder(tmp_path)
    whole = un.Leftover("folder", str(acme), "install", level=3, size=608)
    data = un.Leftover("folder", str(tmp_path / "Acme Data"), "data", level=2)
    dlg = _sheet(controller, qtbot, [whole, data])
    row = _row(dlg, acme)
    assert row.text(1) == "Sure" and _row(dlg, data.path).text(1) == "Likely"
    assert row.checkState(0) == Qt.Checked and dlg._chosen() == [whole]   # (a sure one is ticked)
    assert row.childCount() == 0                                          # (nothing listed until it's opened)
    row.setExpanded(True)
    names = [row.child(c).text(0) for c in range(row.childCount())]
    assert names == ["bin", "cache.db", "my project.acme"]
    assert all(row.child(c).checkState(0) == Qt.Checked for c in range(row.childCount()))
    assert dlg._chosen() == [whole]                                       # (all of it ticked: it goes whole)
    row.child(2).setCheckState(0, Qt.Unchecked)                           # keep the project
    assert row.checkState(0) == Qt.PartiallyChecked
    chosen = dlg._chosen()
    assert [Path(i.path).name for i in chosen] == ["bin", "cache.db"] and all(i.inside == str(acme) for i in chosen)
    assert dlg.remove_btn.isEnabled()
    bin_row = row.child(0)
    bin_row.setExpanded(True)
    bin_row.child(0).setCheckState(0, Qt.Unchecked)                       # and one file in a sub-folder
    assert [Path(i.path).name for i in dlg._chosen()] == ["acme.exe", "cache.db"]
    qtbot.waitUntil(lambda: bin_row.text(2) != "", timeout=5000)           # (sub-folder sizes come in later)
    dlg._tick_all(False)
    assert dlg._chosen() == [] and not dlg.remove_btn.isEnabled()
    dlg._tick_all(True)
    assert dlg._chosen() == [whole, data]
    dlg._tick_suggested()
    assert dlg._chosen() == [whole]
    assert isinstance(row.child(0).data(0, APP_ROLE), un.Leftover)


def test_a_long_folder_lists_the_rest_as_one_row(controller, qtbot, tmp_path, monkeypatch):
    from corefold import appspage
    monkeypatch.setattr(appspage, "PARTS_SHOWN", 2)
    folder = tmp_path / "Acme Logs"
    folder.mkdir()
    for n in range(5):
        (folder / f"log{n}.txt").write_text("x")
    whole = un.Leftover("folder", str(folder), "data", level=3)
    dlg = _sheet(controller, qtbot, [whole])
    row = _row(dlg, folder)
    row.setExpanded(True)
    assert row.childCount() == 3 and row.child(2).text(0) == "3 more items"
    row.child(0).setCheckState(0, Qt.Unchecked)
    assert [Path(i.path).name for i in dlg._chosen()] == ["log1.txt", "log2.txt", "log3.txt", "log4.txt"]
    row.child(2).setCheckState(0, Qt.Unchecked)
    assert [Path(i.path).name for i in dlg._chosen()] == ["log1.txt"]


def test_an_empty_folder_shows_nothing_to_open(controller, qtbot, tmp_path):
    empty = tmp_path / "Acme Empty"
    empty.mkdir()
    dlg = _sheet(controller, qtbot, [un.Leftover("folder", str(empty), "data", level=3)])
    row = _row(dlg, empty)
    row.setExpanded(True)
    assert row.childCount() == 0 and dlg._chosen()[0].path == str(empty)


@pytest.mark.skipif(not WINDOWS, reason="Windows' registry")
def test_opening_a_registry_key_lets_some_values_stay(controller, qtbot):
    import winreg
    base = "Software\\CorefoldPickTest"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + "\\Recent") as k:
        winreg.SetValueEx(k, "File1", 0, winreg.REG_SZ, "a")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
        winreg.SetValueEx(k, "Theme", 0, winreg.REG_SZ, "dark")
        winreg.SetValueEx(k, "Licence", 0, winreg.REG_SZ, "keep me")
    try:
        whole = un.Leftover("key", "HKCU\\" + base, "registry", level=3)
        dlg = _sheet(controller, qtbot, [whole])
        row = _row(dlg, whole.path)
        row.setExpanded(True)
        assert [row.child(c).text(0) for c in range(row.childCount())] == ["Recent", "Licence", "Theme"]
        row.child(1).setCheckState(0, Qt.Unchecked)
        chosen = dlg._chosen()
        assert [(i.kind, i.value) for i in chosen] == [("key", ""), ("value", "Theme")]
        assert all(un.item_allowed(dict(i.__dict__)) for i in chosen)
        backup = Path(os.environ["LOCALAPPDATA"]) / "pick-backup"
        backup.mkdir(parents=True, exist_ok=True)
        res = un.remove(chosen, backup, ["Acme"])
        assert not res["failed"]
        assert not un.key_exists("HKCU", base + "\\Recent", 0)
        assert un._values("HKCU", base, 0) == {"Licence": "keep me"}
        assert not un.restore(backup)["failed"]
        assert un._values("HKCU", base, 0) == {"Licence": "keep me", "Theme": "dark"}
        assert un.key_exists("HKCU", base + "\\Recent", 0)
    finally:
        for k in (base + "\\Recent", base):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, k)
            except OSError:
                pass
