"""File Organizer rests for now (tools.PAUSED_TOOLS) until it's been checked again: nobody can use it by
mistake. No tab, card or switch in Files, no Home widget, its page opens Files instead, and the folder it
was keeping tidy isn't sorted in the background. Its code, settings and Undo are all kept."""
import pytest
from PySide6.QtGui import QIcon

from corefold import homelist
from corefold import settings as settings_mod
from corefold import tools as all_tools


@pytest.fixture
def resting(monkeypatch):
    monkeypatch.setattr(all_tools, "PAUSED_TOOLS", frozenset({"organizer"}))


@pytest.fixture
def window(qtbot, resting, tmp_path):
    from corefold import app as A
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = False
    s.tools_off = []
    s.last_page = "Home"
    s.organize_auto_folder = str(tmp_path)  # (kept tidy before it rested)
    s.organize_auto_last = 0.0
    s.save()
    c = A.Controller(A.QApplication.instance(), QIcon())
    c.apply_tools()
    w = c.window
    w.resize(1040, 700)
    w.show()
    qtbot.waitExposed(w)
    yield w
    from PySide6.QtCore import QTimer
    from shiboken6 import delete
    c.tray.hide()
    for timer in c.findChildren(QTimer):
        timer.stop()
    if c._window is not None:
        c._window.close()
        delete(c._window)
    delete(c)
    s = settings_mod.Settings.load()
    s.organize_auto_folder = ""
    s.save()


def test_a_built_corefold_rests_the_organizer(monkeypatch):
    import importlib
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)  # (a built Corefold.exe ignores the checks' switch)
    try:
        assert importlib.reload(all_tools).PAUSED_TOOLS == {"organizer"}
    finally:
        monkeypatch.delattr(sys, "frozen")
        importlib.reload(all_tools)


def test_the_organizer_is_never_on(resting):
    s = settings_mod.Settings()
    s.tools_off = []
    assert all_tools.state("organizer") == all_tools.PAUSED
    assert not all_tools.is_on(s, "organizer")
    assert "organizer" not in {t.id for t in all_tools.enabled(s)}
    assert "organizer" not in {t.id for t in all_tools.in_section(s, all_tools.SECTION_BY_PAGE["Files"])}
    assert "organizer" not in {k.id for k in homelist.offered(s, [], all_tools.is_on)}
    assert not homelist.shown(s, [{"id": "organizer", "size": "medium"}], all_tools.is_on)
    assert "organizer" not in {t.id for t in all_tools.new_tools(s)}
    assert "organizer" not in s.tools_off  # (kept as this person had it, for when it's back)


def test_nothing_of_the_organizer_shows_or_runs(window):
    w = window
    c = w.c
    assert "Organizer" not in w.one_head.tabs
    assert all(t.id != "organizer" for t in w.tools_on)
    w.open_page("Organizer")
    assert w.current_page() == "Files" and w.organizer_page is None  # (its page isn't even made)
    c.keep_tidy()
    assert getattr(c, "organize_timer", None) is None or not c.organize_timer.isActive()
    c.tidy_folder(now=True)
    assert getattr(c, "_tidy_job", None) is None
