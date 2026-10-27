"""The tidy a few seconds after a job: with a window in use, only the younger objects are looked through
(going through every object stops the window for up to half a second)."""
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
