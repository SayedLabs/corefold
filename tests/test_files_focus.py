"""The sections resting for now (tools.PAUSED_SECTIONS): nothing of their tools runs or opens, nothing of them
is deleted, and taking a section out of the list brings its tools back the way each person had them."""
import importlib
from pathlib import Path
from unittest import mock

import pytest
from PySide6.QtGui import QIcon

from corefold import settings as settings_mod
from corefold import shellmenu
from corefold import tools as all_tools

ROOT = Path(__file__).resolve().parent.parent
FILES = {"converter", "pdf", "archive", "organizer", "unlock", "cloud"}
RESTING = {"capture", "viewer", "player", "colors", "space", "uninstaller", "startup", "scan", "clipboard", "awake"}


def test_only_the_files_tools_are_available():
    assert {t.id for t in all_tools.available()} == FILES
    assert {t.id for t in all_tools.paused()} == RESTING
    for tool_id in ("rename", "shredder", "backup", "ruler", "notes"):  # (still being made: never switched on)
        assert all_tools.state(tool_id) == all_tools.PLANNED
    assert all_tools.state("no-such-tool") == all_tools.PLANNED
    assert {t.id for t in all_tools.ready()} == FILES | RESTING  # (still made: only resting)


def test_resting_tools_are_off_without_touching_the_settings():
    s = settings_mod.Settings()
    s.tools_off = ["pdf", "space"]
    before = list(s.tools_off)
    assert not any(all_tools.is_on(s, t) for t in RESTING)
    assert {t.id for t in all_tools.enabled(s)} == FILES - {"pdf"}
    assert s.tools_off == before


def test_resting_tools_are_never_offered_as_new_meanwhile():
    s = settings_mod.Settings()
    s.known_tools = sorted(FILES)
    assert all_tools.offer_new_tools(s) == []
    assert s.tools_off == [] and s.known_tools == sorted(FILES)


def test_a_section_brought_back_comes_back_as_each_person_had_it(monkeypatch):
    s = settings_mod.Settings()
    s.tools_off = ["capture"]  # (Capture was switched off, the Clipboard on)
    s.known_tools = sorted(FILES | RESTING)
    monkeypatch.setattr(all_tools, "PAUSED_SECTIONS", frozenset())
    assert all_tools.is_on(s, "clipboard") and not all_tools.is_on(s, "capture")
    assert all_tools.offer_new_tools(s) == []


def test_a_fresh_install_meets_resting_tools_switched_off_when_they_come_back(monkeypatch):
    s = settings_mod.Settings()
    s.known_tools = sorted(t.id for t in all_tools.available())  # (what the first start offers now)
    monkeypatch.setattr(all_tools, "PAUSED_SECTIONS", frozenset())
    offered = {t.id for t in all_tools.offer_new_tools(s)}
    assert offered == RESTING
    assert not any(all_tools.is_on(s, t) for t in RESTING)


def test_nothing_of_a_resting_tool_was_deleted(qtbot):
    """Every resting tool keeps its code (each module still imports) and its icons."""
    for name in ("capture", "capview", "viewer", "player", "colors", "colorspage", "space", "spacepage",
                 "uninstall", "appspage", "startup", "startuppage", "scan", "scanpage", "clipboard", "clipview",
                 "awake", "awakepage", "peek", "peekview", "quick", "quickmenu", "quickwheel", "quickpage"):
        assert (ROOT / "corefold" / f"{name}.py").is_file(), name
        importlib.import_module(f"corefold.{name}")
    from corefold import icons
    for tool in all_tools.paused():
        assert icons.product_tile(tool.product, 32, False, 1.0) is not None


def test_the_right_click_menu_has_nothing_of_resting_tools():
    s = settings_mod.Settings()
    verbs = shellmenu.plan(s, lambda *args: "corefold.exe " + " ".join(args))
    keys = {v.name for v in verbs}
    assert verbs  # (the Converter's own entries are still there)
    assert "CorefoldScan" not in keys and "CorefoldUninstall" not in keys


def test_their_shortcuts_say_coming_back_soon():
    from corefold import shortcutspage
    c = mock.MagicMock()
    c.settings = settings_mod.Settings()
    for entry in shortcutspage.GLOBAL:
        st = shortcutspage.state(c, entry)
        if entry.tool in RESTING or entry.section == "quick":
            assert st == "resting", entry.label


@pytest.fixture
def controller(qtbot, monkeypatch):
    from corefold import app as A
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = False
    s.quick_menu = s.quick_wheel = True  # (wanted, but their section rests)
    s.tools_off = []
    s.save()
    c = A.Controller(A.QApplication.instance(), QIcon())
    c.apply_tools()
    yield c
    # (nothing of it may go on into the other tests: its timers stop and it goes, with its window)
    from PySide6.QtCore import QTimer
    from shiboken6 import delete
    c.tray.hide()
    for timer in c.findChildren(QTimer):
        timer.stop()
    if c._window is not None:
        c._window.close()
        delete(c._window)
    delete(c)


def test_no_background_work_of_resting_tools(controller):
    c = controller
    assert (c.clip, c.capture, c.colors, c.quick, c.awake, c.peek) == (None,) * 6
    assert not c.act_clip.isVisible() and not c.act_shot.isVisible() and not c.act_color.isVisible()
    assert not c.act_quick.isVisible()


def test_resting_pages_open_their_section_whatever_asks(controller):
    w = controller.window
    for name, shown in (("Capture", "Media"), ("CaptureSettings", "Media"), ("Space", "Computer"),
                        ("Apps", "Computer"), ("Clipboard", "QuickTools"), ("Quick", "QuickTools"),
                        ("ViewerSettings", "Media")):
        w.open_page(name)
        assert w.current_page() == shown, name
    for name in ("Converter", "PDF", "Archives", "Organizer", "Unlock", "Cloud", "Updates", "About", "History",
                 "General"):
        w.open_page(name)
        assert w.current_page() == name, name
    tabs = {tab[0] for tab in w.settings_tab_list}
    assert "Quick" not in tabs and "CaptureSettings" not in tabs and "ClipboardSettings" not in tabs
    assert {"General", "Look", "Shortcuts", "ConvOptions", "ArchiveSettings"} <= tabs
    assert not w.quick_btn.isVisible()


def test_a_resting_tool_cant_be_switched_on_or_opened(controller, tmp_path):
    c = controller
    c.set_tool("capture", True)
    assert c.capture is None and "capture" not in c.settings.tools_off
    picture = tmp_path / "a.png"
    picture.write_bytes(b"x")
    with mock.patch("corefold.viewer.ViewerWindow") as viewer:
        c.open_viewer(picture)
        c.handle_message(f"view\n{picture}")
    viewer.assert_not_called()


def test_a_planned_shutdown_is_cancelled_once_and_said(controller, monkeypatch):
    import sys
    from datetime import datetime, timedelta

    from corefold import awake
    c = controller
    monkeypatch.setattr(sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(awake, "run_shutdown", lambda *a: calls.append(a) or (0, ""))
    c.settings.shutdown_at = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
    c.settings.shutdown_planned = datetime.now().isoformat(timespec="seconds")
    c._rest_paused_tools()
    assert calls == [("/a",)] and c.settings.shutdown_at == "" and c.settings.shutdown_planned == ""
    c._rest_paused_tools()
    assert calls == [("/a",)]
