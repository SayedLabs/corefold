"""Checks for fixes from the pre-launch look at the window around the tools: the sidebar, the Settings list,
the Shortcuts page, the tray menu, the problem report and bringing settings back (offscreen: no Windows needed)."""
import json
import zipfile
from datetime import datetime

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon

from corefold import settings as settings_mod
from corefold import settings_backup, tools


@pytest.fixture
def controller(qtbot, monkeypatch):
    from corefold import app as A
    from corefold import cloudlocal
    monkeypatch.setattr(cloudlocal, "find", lambda: [])
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = False
    s.tools_off = []
    s.save()
    c = A.Controller(A.QApplication.instance(), QIcon())
    c.apply_tools()
    yield c
    from shiboken6 import delete
    c.tray.hide()
    for timer in c.findChildren(QTimer):
        timer.stop()
    if c._window is not None:
        c._window.close()
        delete(c._window)
    delete(c)


def test_the_settings_list_shows_names_at_the_windows_first_size(controller):
    w = controller.window
    w.open_page("General")
    w._settings_narrow = None
    w._fit_settings_side(1040 - 236)  # (the page area of a window 1040 wide, beside the sidebar)
    assert not w._settings_narrow
    assert w.settings_side.width() >= 168
    assert not w.settings_side_title.isHidden()
    w._fit_settings_side(700)  # (the smallest windows: icons, each with its name as a tip)
    assert w._settings_narrow and w.settings_side.width() == 52
    assert w.settings_list.item(0).toolTip()


def test_the_shortcuts_page_leaves_out_the_keys_of_tools_resting_for_now(controller):
    if not tools.PAUSED_SECTIONS:
        pytest.skip("no section is resting")
    from corefold.shortcutspage import ShortcutsPage, state
    page = ShortcutsPage(controller.window)
    shown = [entry for (entry, *_), (_, box) in zip(page.rows, page.lines) if not box.isHidden()]
    assert shown and all(state(controller, e) != "resting" for e in shown)
    assert len(shown) < len(page.rows)
    assert not page.resting_note.isHidden()
    first = next(i for i, (_, box) in enumerate(page.lines) if not box.isHidden())
    assert page.lines[first][0].isHidden()  # (no line above the first key shown)


def test_the_tray_says_what_pause_stops(controller):
    assert controller.act_pause.text() == "Pause the Converter"


def test_the_problem_report_leaves_out_what_only_says_something_about_the_person(tmp_path):
    from corefold import report
    s = settings_mod.Settings()
    s.quick_links = [{"id": "x", "name": "My bank", "target": "https://bank.example/login"}]
    s.clip_ignore = "mybank.exe"
    s.watermark = {"text": "Sam Smith Photography", "logo": "", "position": "br", "size": "medium"}
    s.colors_history = ["#123456"]
    path = report.make(s, tmp_path, screens=[])
    with zipfile.ZipFile(path) as z:
        text = z.read("settings.json").decode("utf-8")
    for private in ("bank.example", "mybank.exe", "Sam Smith", "#123456"):
        assert private not in text
    assert json.loads(text)["language"] == s.language


def test_settings_brought_back_say_when_they_save_to_another_computer():
    from corefold import i18n
    from corefold.settingscard import folders_note
    i18n.set_language("en")

    def saved(values):
        return settings_backup.Saved(values, "1.15.0", datetime(2026, 10, 27), None)
    assert folders_note(saved({"output_folder": r"C:\Users\me\Downloads\Corefold"})) == ""
    note = folders_note(saved({"output_folder": r"\\server\share\in", "cap_folder": "//server/pics"}))
    assert r"\\server\share\in" in note and "//server/pics" in note
    assert "another computer" in note
