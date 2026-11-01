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
