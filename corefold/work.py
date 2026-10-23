"""Running slow things (the disk, the internet) off the window's thread, with their answer back on it."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal


class _Signals(QObject):
    progress = Signal(object)
    done = Signal(object)


def run(fn, on_progress, on_done, name: str = "corefold-work") -> threading.Thread:
    """Runs fn(progress) on its own thread. on_progress gets what progress() is given, and on_done
    what fn returns (or the exception it raised), both on the window's thread."""
    sig = _Signals()
    sig.progress.connect(on_progress)
    sig.done.connect(on_done)

    def go():
        try:
            result = fn(sig.progress.emit)
        except Exception as e:  # (shown, never lost)
            result = e
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
