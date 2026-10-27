"""Handing memory back to Windows without stopping the window: the slow part runs off the window's thread, and with a
window in use only the younger objects are looked through."""
import ctypes
import threading
from unittest import mock

from corefold import winutil


def _trim(background: bool) -> tuple[threading.Thread | None, threading.Thread | None]:
    k32 = mock.MagicMock()
    k32.GetProcessHeaps.return_value = 2
    where = {}
    k32.HeapCompact.side_effect = lambda *a: where.setdefault("compact", threading.current_thread())
    done = threading.Event()

    def hand_back(*a):
        where["hand_back"] = threading.current_thread()
        done.set()
    k32.SetProcessWorkingSetSize.side_effect = hand_back
    with mock.patch.object(ctypes, "windll", mock.MagicMock(kernel32=k32), create=True):
        winutil.trim_memory(background=background)
        assert done.wait(5)
    return where.get("compact"), where.get("hand_back")


def test_pages_are_handed_back_on_a_thread_of_their_own():
    compact, hand_back = _trim(background=True)
    assert compact is threading.current_thread()  # (a heap made for one thread is compacted on that thread)
    assert hand_back is not threading.current_thread()


def test_by_default_it_all_happens_here():
    compact, hand_back = _trim(background=False)
    assert compact is threading.current_thread() and hand_back is threading.current_thread()


def _light_tidy(active_window) -> list:
    from types import SimpleNamespace

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
