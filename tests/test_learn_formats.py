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
        W._load_catalog()
        w._learning = ({}, ["Formats"])
        w._learn_next()
        assert w.built("Formats")
        assert w._formats_pending, "every row of Formats was made in one step"
        qtbot.waitUntil(lambda: w._learning is None, timeout=10_000)
    last = catalog()["files"][-1]
    assert any(last.label in text for text in kept["Formats"])  # (the last group was in when it was read)
    assert not w.built("Formats")  # (let go again)


def test_formats_tools_load_on_a_thread_of_their_own_first(window, qtbot):  # noqa: F811
    """Loading the modules of Formats' tools stopped the window 0.3 s (on a slow drive): it's done on a thread."""
    import threading

    from corefold import window as W
    w = window
    loaded_on = []
    with mock.patch.object(W.winutil, "idle_ms", return_value=60_000), \
            mock.patch.object(W, "_keep_setting_words"), mock.patch.object(W, "_setting_words", return_value={}), \
            mock.patch.object(W, "_catalog_tried", []), \
            mock.patch.object(W, "_load_catalog", side_effect=lambda: (loaded_on.append(threading.current_thread()),
                                                                       W._catalog_tried.append(True))):
        w._catalog_loading = False
        w._learning = ({}, ["Formats"])
        w._learn_next()
        assert not w.built("Formats")  # (not on the window's thread, not yet)
        qtbot.waitUntil(lambda: w._learning is None, timeout=10_000)
    assert loaded_on and loaded_on[0] is not threading.main_thread()
