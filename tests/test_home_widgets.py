"""The Home page's widgets: where they go, what's kept in the settings, and that a tool switched off or
resting for now has nothing of it on the Home page (they run offscreen: no Windows needed)."""
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon

from corefold import homelist
from corefold import settings as settings_mod
from corefold import tools as all_tools

import pytest


def test_each_widget_takes_the_first_free_place():
    # medium, medium | medium, small, small | wide
    assert homelist.pack(["medium", "medium", "medium", "small", "small", "wide"], 4) == [
        (0, 0, 2, 1), (0, 2, 2, 1), (1, 0, 2, 1), (1, 2, 1, 1), (1, 3, 1, 1), (2, 0, 4, 1)]
    # a small one fills the gap a big one left next to it
    assert homelist.pack(["large", "medium", "small", "small"], 4) == [
        (0, 0, 2, 2), (0, 2, 2, 1), (1, 2, 1, 1), (1, 3, 1, 1)]
    # in a narrow window a wide one is as wide as the two columns
    assert homelist.pack(["wide", "small", "medium"], 2) == [(0, 0, 2, 1), (1, 0, 1, 1), (2, 0, 2, 1)]


def test_what_is_saved_is_made_safe():
    saved = [{"id": "a", "kind": "pdf", "size": "huge"},            # (a size it doesn't come in)
             {"id": "a", "kind": "archive", "size": "small"},       # (the same id again)
             {"kind": "convert", "size": "small"},                  # (no id)
             "nonsense",
             {"id": "b", "kind": "from-a-newer-version", "size": "x", "more": 1},
             {"id": "c", "kind": "shortcut", "size": "small"},      # (a shortcut to nothing)
             {"id": "d", "kind": "shortcuts", "size": "wide", "items": [{"target": "C:/x"}, {"name": "no target"}]}]
    got = homelist.clean(saved)
    assert [e["id"] for e in got] == ["a", "b", "d"]
    assert got[0]["size"] == "medium"
    assert got[1] == saved[4]  # (kept as it is, for the version that knows it)
    assert got[2]["items"] == [{"name": "", "target": "C:/x"}]
    assert len(homelist.clean("broken")) == len(homelist.DEFAULT)
    assert len(homelist.clean([homelist.make("shortcut", target="x") for _ in range(50)])) == homelist.MAX_WIDGETS


def test_widgets_of_resting_or_switched_off_tools_are_left_out():
    s = settings_mod.Settings()
    saved = [homelist.make("screenshot"), homelist.make("pdf"), homelist.make("disk"),
             homelist.make("shortcut", target="x"), {"id": "z", "kind": "unknown", "size": "small"}]
    assert {e["kind"] for e in homelist.shown(s, saved, all_tools.is_on)} == {"pdf", "shortcut"}
    s.tools_off = ["pdf"]
    assert {e["kind"] for e in homelist.shown(s, saved, all_tools.is_on)} == {"shortcut"}
    offered = {k.id for k in homelist.offered(s, saved, all_tools.is_on)}
    assert not offered & {"screenshot", "disk", "clipboard", "color", "pdf"}  # (resting, or switched off)
    assert {"shortcut", "shortcuts", "convert"} <= offered  # (shortcuts can be there more than once)


def test_a_shortcut_cant_open_a_resting_tool():
    from corefold.homewidgets import target_ok
    s = settings_mod.Settings()
    assert not target_ok(s, "page:Capture") and not target_ok(s, "page:Space")
    assert target_ok(s, "page:PDF") and target_ok(s, "page:History") and target_ok(s, "C:/Users")
    s.tools_off = ["pdf"]
    assert not target_ok(s, "page:PDF")


def test_a_new_home_and_one_from_before_start_with_the_usual_widgets(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "CONFIG_DIR", tmp_path)
    (tmp_path / "settings.json").write_text('{"language": "en"}', "utf-8")  # (from a version without widgets)
    s = settings_mod.Settings.load()
    assert [(e["kind"], e["size"]) for e in s.home_widgets] == list(homelist.DEFAULT)
    s.home_widgets = s.home_widgets[:2]
    s.save()
    assert settings_mod.Settings.load().home_widgets == s.home_widgets
    assert "home_widgets" not in settings_mod.editable_keys()  # (kept right away, never waits for Save)


@pytest.fixture
def window(qtbot, monkeypatch):
    from corefold import app as A
    from corefold import cloudlocal, homewidgets
    # (what the widgets look up is the PC's own: here, a quick stand-in. With the stand-ins for Windows'
    # registry that these tests run with off Windows, looking through it would slow every test after this one)
    monkeypatch.setattr(cloudlocal, "find", lambda: [])
    homewidgets.forget_looks()
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = False
    s.tools_off = []
    s.home_widgets = homelist.default() + [homelist.make("screenshot"), homelist.make("disk"),
                                           homelist.make("shortcut", target="page:Capture")]
    s.save()
    c = A.Controller(A.QApplication.instance(), QIcon())
    c.apply_tools()
    c.window.open_page("Home")
    yield c.window
    from shiboken6 import delete
    c.tray.hide()
    for timer in c.findChildren(QTimer):
        timer.stop()
    if c._window is not None:
        c._window.close()
        delete(c._window)
    delete(c)


def test_the_home_page_makes_only_the_widgets_of_tools_in_use(window):
    board = window.home_board
    kinds = [f.kind.id for f in board.frames]
    assert kinds == [k for k, _ in homelist.DEFAULT]  # (Capture and Space rest: none of theirs is made)
    assert len(window.c.settings.home_widgets) == len(homelist.DEFAULT) + 3  # (but they're kept)
    assert not any(t.isActive() for t in board.findChildren(QTimer))  # (nothing keeps running)


def test_widgets_are_added_moved_resized_and_removed(window, qtbot):
    board = window.home_board
    s = window.c.settings
    new = board.add("shortcuts", "wide", items=[{"target": "page:PDF", "name": ""}])
    assert board.frames[-1].entry["id"] == new["id"] and s.home_widgets[-1]["id"] == new["id"]
    first = board.frames[0].entry["id"]
    board.move_by(new["id"], -1)
    ids = [f.entry["id"] for f in board.frames]
    assert ids.index(new["id"]) == len(ids) - 2
    board.move(new["id"], first)
    assert board.frames[0].entry["id"] == new["id"]
    board.resize_widget(new["id"], "medium")
    assert board.frames[0].size_name == "medium"
    assert next(e for e in s.home_widgets if e["id"] == new["id"])["size"] == "medium"
    board.remove(new["id"])
    assert new["id"] not in [e["id"] for e in s.home_widgets]
    board.start_again()
    assert [e["kind"] for e in s.home_widgets] == [k for k, _ in homelist.DEFAULT]


def test_changing_the_home_page_and_leaving_it(window, qtbot):
    board = window.home_board
    window.show()
    board.set_editing(True)
    assert all(f.cover.isVisible() for f in board.frames) and board.add_tile.isVisible()
    assert window.home_edit.text() == "Done"
    assert window.go_back() and not board.editing  # (Esc or Back: done)
    board.set_editing(True)
    window.open_page("PDF")
    assert not board.editing
    window.apply_theme(window.c.theme)  # (the widgets are made again in the new colours)
    assert board.frames


def test_the_gallery_offers_no_widget_of_a_resting_tool(window, qtbot):
    from corefold.homewidgets import Gallery
    board = window.home_board
    board.c.settings.home_widgets = []
    board.fill()
    g = Gallery(board)
    qtbot.addWidget(g)
    kinds = {g.list.item(i).data(0x0100) for i in range(g.list.count())} - {None}
    assert {"convert", "pdf", "archive", "organizer", "unlock", "cloud", "shortcut", "shortcuts"} <= kinds
    assert not kinds & {"screenshot", "disk", "clipboard", "color"}
    for size in ("small", "medium", "large", "wide"):  # (each size of each kind can be seen)
        for i in range(g.list.count()):
            item = g.list.item(i)
            k = homelist.KINDS.get(item.data(0x0100))
            if k is not None and size in k.sizes:
                g.list.setCurrentItem(item)
                g._show(size)
                assert g.preview is not None and g.preview.kind is k
