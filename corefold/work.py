"""Running slow things (the disk, the internet) off the window's thread, with their answer back on it."""
from __future__ import annotations

import queue
import threading

from PySide6.QtCore import QObject, Signal


class _Signals(QObject):
    progress = Signal(object)
    done = Signal(object)


_busy: set[int] = set()
_lock = threading.Lock()
_ids = iter(range(1, 1 << 62))


def busy() -> bool:
    """A job that moves or changes the person's files is running (Rakiza Core waits for it before it quits or
    is updated: stopped half-way, files would be left half-sorted or half-changed)."""
    with _lock:
        return bool(_busy)


class Job:
    """A job handed to run(): alive until its answer has gone to the window (like the thread it runs on)."""

    def __init__(self, name: str, box: list):
        self.name = name
        self.box = box  # (its signals, kept alive while it runs)
        self._over = threading.Event()

    def is_alive(self) -> bool:
        return not self._over.is_set()

    def join(self, timeout: float | None = None):
        self._over.wait(timeout)


_starts: "queue.SimpleQueue[threading.Thread]" = queue.SimpleQueue()
_starter: threading.Thread | None = None


def _start_them():
    # Starting a thread waits for Windows, which lets one thread start or end at a time: right after a job
    # that moved 30,000 files ended, a start held the window up to 3 s. This thread does the waiting instead.
    while True:
        _starts.get().start()


def run(fn, on_progress, on_done, name: str = "corefold-work", busy: bool = False) -> Job:
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
    # Only the window's thread holds the signals, and it lets them go once the answer is in and the job's
    # thread is done with them: when the last reference went on the job's thread, Qt could still be handing
    # the window an event for them, and it read what was already freed (a crash, now and then, as a job ended).
    box = [sig]
    handle = Job(name, box)
    sig.done.connect(lambda _result: _retire(handle))  # (after on_done)
    del sig

    def go():
        s = box[0]
        try:
            result = fn(s.progress.emit)
        except Exception as e:  # (shown, never lost)
            result = e
        finally:
            with _lock:
                _busy.discard(job)
        try:
            s.done.emit(result)
        finally:
            del s
            result = None  # (an error holds the job's frames, and through them its signals)
            handle._over.set()
    _start(threading.Thread(target=go, daemon=True, name=name))
    return handle


def _retire(handle: Job):
    """On the window's thread: the job's signals go once its thread no longer holds them (never in the middle
    of handing out their own answer: a moment later)."""
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0 if handle._over.is_set() else 20,
                      handle.box.clear if handle._over.is_set() else (lambda: _retire(handle)))


def background(fn, name: str = "corefold-work"):
    """Runs fn() on a thread of its own, started the same way (nothing comes back to the window)."""
    _start(threading.Thread(target=fn, daemon=True, name=name))


def _start(thread: threading.Thread):
    global _starter
    _starts.put(thread)
    with _lock:
        if _starter is None:  # (the first one starts the starter, once)
            _starter = threading.Thread(target=_start_them, daemon=True, name="corefold-starter")
            _starter.start()


def alive(widget) -> bool:
    """False once Qt has deleted the widget (an answer arriving after its window closed)."""
    try:
        from shiboken6 import isValid
        return isValid(widget)
    except ImportError:
        return True
