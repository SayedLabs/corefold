"""The tidy a few seconds after a job, and Python's own garbage collection on the window's thread: with a window in
use, every object is looked through rarely (it stops the window for up to half a second)."""
from types import SimpleNamespace
from unittest import mock


def _light_tidy(active_window) -> list:
    from corefold import app as A
    me = SimpleNamespace(cancels=[], _light_waits=0, light_tidy_timer=mock.MagicMock())
    with mock.patch.object(A.QApplication, "activeWindow", return_value=active_window), \
            mock.patch.object(A.winutil, "idle_ms", return_value=60_000), \
            mock.patch.object(A.winutil, "trim_memory") as trim, \
            mock.patch.object(A.gc, "collect") as collect:
        A.Controller.light_tidy(me)
    assert trim.called
    return [c.args for c in collect.call_args_list]


def test_with_a_window_in_use_only_younger_objects_are_looked_through():
    assert _light_tidy(object()) == [(1,)]


def test_with_no_window_in_use_every_object_is_looked_through():
    assert _light_tidy(None) == [(2,)]


def _collect_garbage(active_window, since_full: float, counts=(0, 0, 11)) -> list:
    import time

    from corefold import app as A
    A._last_full[0] = time.monotonic() - since_full
    with mock.patch.object(A.QApplication, "activeWindow", return_value=active_window), \
            mock.patch.object(A.gc, "get_count", return_value=counts), \
            mock.patch.object(A.gc, "collect") as collect:
        A.collect_garbage()
    return [c.args for c in collect.call_args_list]


def test_with_a_window_in_use_every_object_is_looked_through_at_most_once_a_minute():
    assert _collect_garbage(object(), since_full=5, counts=(0, 11, 11)) == [(1,)]
    assert _collect_garbage(object(), since_full=5, counts=(0, 3, 11)) == []
    assert _collect_garbage(object(), since_full=61) == [(2,)]
    assert _collect_garbage(None, since_full=5) == [(2,)]


def test_a_full_look_elsewhere_counts_as_the_last_one():
    import gc
    import time

    from corefold import app as A
    A._last_full[0] = 0.0
    gc.collect(2)
    assert time.monotonic() - A._last_full[0] < 5
