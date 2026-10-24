"""Running slow things (the disk, the internet) off the window's thread, with their answer back on it."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal


class _Signals(QObject):
    progress = Signal(object)
    done = Signal(object)


_busy: set[int] = set()
_lock = threading.Lock()
_ids = iter(range(1, 1 << 62))


def busy() -> bool:
    """A job that moves or changes the person's files is running (Corefold waits for it before it quits or
    is updated: stopped half-way, files would be left half-sorted or half-changed)."""
    with _lock:
        return bool(_busy)


def run(fn, on_progress, on_done, name: str = "corefold-work", busy: bool = False) -> threading.Thread:
    """Runs fn(progress) on its own thread. on_progress gets what progress() is given, and on_done
    what fn returns (or the exception it raised), both on the window's thread. busy: it changes files
    (see busy())."""
    sig = _Signals()
    sig.progress.connect(on_progress)
    sig.done.connect(on_done)
    job = next(_ids)
    if busy:
        with _lock:
            _busy.add(job)

    def go():
        try:
            result = fn(sig.progress.emit)
        except Exception as e:  # (shown, never lost)
            result = e
        finally:
            with _lock:
                _busy.discard(job)
        sig.done.emit(result)
    t = threading.Thread(target=go, daemon=True, name=name)
    t.sig = sig  # (kept alive while it runs)
    t.start()
    return t


def alive(widget) -> bool:
    """False once Qt has deleted the widget (an answer arriving after its window closed)."""
    try:
        from shiboken6 import isValid
        return isValid(widget)
    except ImportError:
        return True
