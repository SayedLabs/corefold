"""The Uninstaller's "Folders of apps that are gone": program folders nothing installed uses any more."""
import _winapi
import os
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon

from corefold import settings as settings_mod
from corefold import uninstall as un

OLD = time.time() - 200 * 86400


def _old_tree(path: Path):
    for dirpath, dirs, files in os.walk(path, topdown=False):
        for name in files + dirs:
            os.utime(os.path.join(dirpath, name), (OLD, OLD))
    os.utime(path, (OLD, OLD))


def _folder(root: Path, name: str, files=("app.exe",)) -> Path:
    f = root / name
    f.mkdir()
    for n in files:
        (f / n).write_bytes(b"x" * 1000)
    _old_tree(f)
    return f


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "Program Files"
    r.mkdir()
    monkeypatch.setattr(un, "orphan_roots", lambda: [r])
    monkeypatch.setattr(un, "programs_in", lambda folders: [])
    return r


def _found(apps=(), refs=(), entries=()):
    return [Path(i.path).name for i in un.orphan_folders(list(apps), refs=list(refs), entries=list(entries))]


def test_a_folder_nothing_uses_is_offered_unticked(root):
    gone = _folder(root, "Old Tool", ("tool.exe", "data.bin"))
    found = un.orphan_folders([], refs=[], entries=[])
    assert [i.path for i in found] == [str(gone)]
    item = found[0]
    assert item.kind == "folder" and item.reason == "orphan" and not item.safe and item.level == 1
    assert item.size == 2000
    assert not un.ticked_at(item, "deep")                     # (never ticked, at any depth)
    assert un.item_allowed(dict(kind="folder", path=item.path))


def test_folders_something_points_at_are_left(root):
    _folder(root, "Svc")
    _folder(root, "Pathy")
    _folder(root, "Gone")
    refs = [str(root / "Svc" / "app.exe"), str(root / "Pathy") + "\\"]
    assert _found(refs=refs) == ["Gone"]


def test_folders_named_after_an_installed_app_or_its_maker_are_left(root):
    _folder(root, "Acme Studio")
    _folder(root, "Contoso")
    _folder(root, "Foo Runtime")
    _folder(root, "Gone")
    apps = [un.App(id="1", name="Acme Studio 2024"), un.App(id="2", name="Widget", publisher="Contoso Ltd.")]
    entries = [{"DisplayName": "Foo Runtime (x64)", "SystemComponent": 1}]   # (hidden from the list of apps)
    assert _found(apps, entries=entries) == ["Gone"]


def test_windows_own_and_shared_runtime_folders_are_never_offered(root):
    for name in ("DIFX", "VulkanRT", "Microsoft Office", "Windows Mail", "Common Files", "Uninstall Information",
                 "dotnet", "Gone"):
        _folder(root, name)
    assert _found() == ["Gone"]


def test_recently_changed_folders_and_drivers_are_left(root):
    _folder(root, "Gone")
    _folder(root, "Driver", ("filter.sys", "tool.exe"))
    busy = _folder(root, "Busy", ("app.exe", "today.log"))
    os.utime(busy / "today.log", None)
    assert _found() == ["Gone"]


def test_links_are_never_followed_or_offered(root, tmp_path):
    if sys.platform != "win32":
        pytest.skip("junctions are Windows only")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "mine.txt").write_text("someone's own file")
    _winapi.CreateJunction(str(elsewhere), str(root / "Linked"))
    inside = _folder(root, "Holds a link", ())
    _winapi.CreateJunction(str(elsewhere), str(inside / "link"))
    _old_tree(inside)
    _folder(root, "Gone")
    assert _found() == ["Gone"]


def test_a_folder_a_running_program_is_in_is_left(root, monkeypatch):
    running = _folder(root, "Running")
    _folder(root, "Gone")
    monkeypatch.setattr(un, "programs_in", lambda folders: [(123, str(running / "app.exe"))])
    assert _found() == ["Gone"]


# ---- the sheet on the Apps page

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


def test_the_sheet_offers_them_unticked(controller, qtbot, monkeypatch, tmp_path):
    from corefold.appspage import APP_ROLE, OrphansDialog
    item = un.Leftover("folder", str(tmp_path / "Old Tool"), "orphan", safe=False, size=5 << 20, level=1)
    asked = []
    monkeypatch.setattr(un, "orphan_folders", lambda apps, cancel=None: asked.append(apps) or [item])
    store = un.App(id="s", name="Store app", store="Pkg_1.0_x64__abc")
    dlg = OrphansDialog(controller, [un.App(id="1", name="Acme"), store], QIcon())
    qtbot.addWidget(dlg)
    assert dlg.depth.isHidden()
    dlg.start()
    qtbot.waitUntil(lambda: dlg.stack.currentIndex() == 2, timeout=5000)
    assert [a.name for a in asked[0]] == ["Acme"]              # (Store apps own no Program Files folder)
    group = dlg.left_tree.topLevelItem(0)
    row = group.child(0)
    assert row.data(0, APP_ROLE) is item and row.checkState(0) == Qt.Unchecked
    assert not dlg.remove_btn.isEnabled()
    assert dlg.left_title.text() == "1 program folders no installed app uses"
    row.setCheckState(0, Qt.Checked)
    assert dlg.remove_btn.isEnabled() and dlg._chosen() == [item]


def test_nothing_found_says_so(controller, qtbot, monkeypatch):
    from corefold.appspage import OrphansDialog
    monkeypatch.setattr(un, "orphan_folders", lambda apps, cancel=None: [])
    dlg = OrphansDialog(controller, [], QIcon())
    qtbot.addWidget(dlg)
    dlg.start()
    qtbot.waitUntil(lambda: dlg.stack.currentIndex() == 3, timeout=5000)
    assert dlg.done_title.text() == "All clean"
    assert dlg.undo_btn.isHidden() or not dlg.undo_btn.isVisible()


def test_it_opens_as_a_sheet_in_the_window(controller, qtbot, monkeypatch):
    from corefold import appspage
    shown = []
    monkeypatch.setattr(appspage.sheet, "run", lambda dlg: shown.append(dlg) or 0)
    page = appspage.AppsPage(controller)
    qtbot.addWidget(page)
    page.show_orphans()
    assert len(shown) == 1 and isinstance(shown[0], appspage.OrphansDialog)
