"""The calmer look: settings kept as they're changed, the few likely buttons first after a drop, status lines
in Arabic that keep their order, and a Home page without noughts on a new PC."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon

from corefold import i18n, panel
from corefold import settings as settings_mod


@pytest.fixture
def window(qtbot):
    from corefold import app as A
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = False
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


def test_a_changed_setting_is_kept_without_a_save_button(window, qtbot, monkeypatch):
    saved = []
    monkeypatch.setattr(window.c.settings, "save", lambda: saved.append(True))
    window.open_page("General")
    window.c.settings.volume = 12 if window.c.settings.volume != 12 else 13
    window.update_save_bar()
    assert not window.save_btn.isVisible() and not window.done_btn.isVisible()
    qtbot.waitUntil(lambda: bool(saved), timeout=2000)
    assert not window.is_dirty() and settings_mod.HELD is None


def test_settings_have_no_save_bar_and_say_saved_quietly(window, qtbot, monkeypatch):
    monkeypatch.setattr(window.c.settings, "save", lambda: None)
    window.show()
    window.open_page("General")
    for w in (window.saved_hint, window.unsaved, window.discard_btn, window.save_btn, window.done_btn):
        assert w.isHidden()  # (settings are kept as they change: nothing says "press Save")
    window.c.settings.volume = 12 if window.c.settings.volume != 12 else 13
    window.update_save_bar()
    qtbot.waitUntil(lambda: window.saved_note.text() == "Saved", timeout=2000)
    window.open_page("Home")
    assert window.saved_note.text() == ""


def test_a_chosen_option_stands_out_in_dark_too(window):
    from corefold.window import Segmented
    seg = Segmented([("a", "A"), ("b", "B")], "b")
    buttons = seg.group.buttons()
    assert [b.objectName() for b in buttons] == ["choice", "choice"] and buttons[1].isChecked()
    from corefold import theme
    dark = theme.build_theme("dark", settings_mod.DEFAULT_ACCENT)
    css, p = theme.stylesheet(dark), dark.tokens
    assert "QPushButton#choice:checked { background: " + p.accent_soft.name() in css
    assert abs(p.accent_soft.lightness() - p.field.lightness()) > 12  # (not the grey a dark button already is)


def test_reset_to_defaults_asks_first(window, monkeypatch):
    from corefold import window as W
    asked, done = [], []

    def no(box):
        asked.append(box.windowTitle())
        return 0
    monkeypatch.setattr(W.sheet, "run", no)
    b = W._reset_button(lambda: done.append(True))
    b.click()
    assert asked == ["Reset this page?"] and not done


def test_a_new_home_doesnt_show_noughts(window):
    window.refresh()
    assert window.hero_figures_box.isHidden() == (window.c.history.stats.files == 0)


def _slot(key: str, ok: bool = True):
    return SimpleNamespace(ok=ok, tool=SimpleNamespace(key=key))


def test_the_likely_buttons_come_first():
    slots = [_slot("image:jpg", False), _slot("image:png"), _slot("image:webp"), _slot("image:resize"),
             _slot("image:rotate"), _slot("image:fit"), _slot("image:small"), _slot("image:crop")]
    by_group = {"convert": slots[:3], "edit": [slots[3], slots[4], slots[7]], "shrink": slots[5:7]}
    keys = [sl.tool.key for sl in panel.suggested(slots, by_group, set())]
    assert keys == ["image:png", "image:fit", "image:resize"]  # (not the format it already is)
    keys = [sl.tool.key for sl in panel.suggested(slots, by_group, {"image:crop"})]
    assert keys[0] == "image:crop"  # (a favourite first)
    assert panel.suggested(slots[:5], {"convert": slots[:3]}, set()) == []  # (only a few buttons anyway)


def test_status_bits_keep_their_order_in_arabic():
    try:
        i18n.set_language("ar")
        line = i18n.join(["280 تطبيقًا · " + i18n.isolate("940.7 GB"), "15 لم تُستخدم"])
        assert line.startswith("\u200f\u2068") and "\u2069 · \u206815" in line
    finally:
        i18n.set_language("en")
    assert i18n.join(["280 apps", "", "15 not used"]) == "280 apps · 15 not used"
