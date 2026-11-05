"""The words on the settings pages are learned soon after the window opens (for Ctrl + K). Formats has about
90 rows: they're made a group at a time, as when the page is opened, not all in one go (that stopped the
window for up to 0.4 s), and the page is read once they're all in."""
from unittest import mock

from tests.test_one_window import window  # noqa: F401  (the fixture)


def test_formats_rows_are_made_a_group_at_a_time_while_its_words_are_learned(window, qtbot):  # noqa: F811
    from corefold import window as W
    from corefold.converters import catalog
    w = window
    kept = {}
    with mock.patch.object(W.winutil, "idle_ms", return_value=60_000), \
            mock.patch.object(W, "_keep_setting_words", side_effect=kept.update), \
            mock.patch.object(W, "_setting_words", return_value={}):
        qtbot.waitUntil(w._formats_modules_loaded, timeout=10_000)  # (read on a thread first: see below)
        w._learning = ({}, ["Formats"])
        w._learn_next()
        assert w.built("Formats")
        assert w._formats_pending, "every row of Formats was made in one step"
        qtbot.waitUntil(lambda: w._learning is None, timeout=10_000)
    last = catalog()["files"][-1]
    assert any(last.label in text for text in kept["Formats"])  # (the last group was in when it was read)
    assert not w.built("Formats")  # (let go again)


def test_formats_modules_are_read_on_a_thread_before_the_page_is_made(window, qtbot, monkeypatch):  # noqa: F811
    """Reading the six modules the Formats list needs from a cold disk stopped the window for 0.9 s while the
    page was made out of sight: they're read on a thread first, and the page waits for them."""
    import sys
    import threading
    w = window
    names = [f"corefold.{m}" for m in ("archives", "audiotags", "extras", "moretools", "pdfstamp", "videotools")]
    gone = names[0]
    import corefold
    monkeypatch.delitem(sys.modules, gone)
    monkeypatch.delattr(corefold, "archives")
    seen = {}
    real = threading.Thread

    def spy(*a, **kw):
        t = real(*a, **kw)
        seen["name"] = kw.get("name")
        return t
    monkeypatch.setattr(threading, "Thread", spy)
    w._formats_loader = None
    w._formats_modules_loaded()
    qtbot.waitUntil(w._formats_modules_loaded, timeout=10_000)
    assert seen.get("name") == "formats-load"
    assert gone in sys.modules
