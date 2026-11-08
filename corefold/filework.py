"""What the Files workspace is working on: the list of files (and which are selected), the tool picked, and the
operations started from Rakiza Core's jobs. Kept by the controller, not the page, so the list and the operations
stay while the page is made again (a new look, another language) or the window is closed and opened.

The operations come from the jobs Rakiza Core already runs (app.Controller.start_job / job_done, which also feed the
progress popup and the History): nothing runs twice and nothing here keeps a history of its own."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

# an item's state in the list
READY, WORKING, DONE, PROBLEM, CANCELLED = "ready", "working", "done", "problem", "cancelled"
# an operation's state
WAITING, RUNNING, FINISHED, FAILED, STOPPED = "waiting", "running", "finished", "failed", "stopped"
MAX_OPS = 30


@dataclass
class Op:
    job_id: int
    title: str                   # what's being done ("Compress")
    what: str                    # on what ("report.pdf", "3 items")
    files: list
    tool: object = None          # (the job's converters.Tool, so it can be tried again)
    params: dict = field(default_factory=dict)
    rename: dict = field(default_factory=dict)
    state: str = WAITING
    progress: float = 0.0
    detail: str = ""
    outputs: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    when: float = field(default_factory=time.time)   # started, then finished

    @property
    def over(self) -> bool:
        return self.state in (FINISHED, FAILED, STOPPED)

    @property
    def can_retry(self) -> bool:
        return self.state in (FAILED, STOPPED) and self.tool is not None and getattr(self.tool, "run", None) is not None


class WorkState(QObject):
    files_changed = Signal()      # the list or the selection
    statuses_changed = Signal()
    ops_changed = Signal()
    sized = Signal(str, int, bool)  # a folder's size was counted (path, bytes, complete)
    job_started = Signal(int)     # a job was started (the side panel stops waiting for a dialog)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[Path] = []
        self.selected: list[str] = []
        self.status: dict[str, tuple[str, str]] = {}   # item -> (state, words shown on hover)
        self.sizes: dict[str, tuple[int, bool]] = {}
        self.kinds: dict[str, str] = {}
        self.tool: str | None = None                   # the tool picked in the side panel
        self.ops: list[Op] = []
        self.library = ""                             # the library's tab: "", "now", "soon" or "manage"
        self.family: str | None = None                 # the library tile picked (its tools beside the files)
        self.save_to: str | None = None                # while a tool starts from the side panel: where it saves
        self._counting: set[str] = set()
        self.sized.connect(self._sized)  # (from the counting threads: queued onto the window's thread)

    # ---- the list

    def add(self, paths: list[Path], select: bool = True) -> list[Path]:
        """Adds what isn't in the list yet (the same file twice is shown once) and selects what was added."""
        from .filetools import kind_of
        known = {str(p) for p in self.items}
        fresh = []
        for p in paths:
            p = Path(p)
            try:
                there = p.exists()
            except OSError:
                there = False
            if there and str(p) not in known:
                known.add(str(p))
                fresh.append(p)
                self.kinds[str(p)] = kind_of(p)
                self.status.setdefault(str(p), (READY, ""))
        if not fresh:
            if select and paths:  # (already there: they're selected, so what's dropped is what's worked on)
                self.selected = [str(Path(p)) for p in paths if str(Path(p)) in known]
                self.files_changed.emit()
            return []
        self.items += fresh
        if select:
            self.selected = [str(p) for p in fresh]
        self._count(fresh)
        self.files_changed.emit()
        return fresh

    def remove(self, names: list[str]):
        """Takes items off the list (nothing on the PC is touched)."""
        gone = set(names)
        self.items = [p for p in self.items if str(p) not in gone]
        self.selected = [n for n in self.selected if n not in gone]
        for n in gone:
            self.status.pop(n, None)
            self.sizes.pop(n, None)
            self.kinds.pop(n, None)
        self.files_changed.emit()

    def clear(self):
        self.remove([str(p) for p in self.items])

    def select(self, names: list[str]):
        names = [n for n in names if any(str(p) == n for p in self.items)]
        if names != self.selected:
            self.selected = names
            self.files_changed.emit()

    def chosen(self) -> list[Path]:
        """What a tool works on: the selected items, or every item when none is selected."""
        if not self.selected:
            return list(self.items)
        picked = set(self.selected)
        return [p for p in self.items if str(p) in picked]

    def size_of(self, p: Path) -> tuple[int, bool] | None:
        return self.sizes.get(str(p))

    def _count(self, paths: list[Path]):
        """Files' sizes now (one look each); folders' in the background, so a huge one never holds the window."""
        from .core import quick_size
        folders = []
        for p in paths:
            if self.kinds.get(str(p)) == "folder":
                folders.append(p)
                continue
            try:
                self.sizes[str(p)] = (p.stat().st_size, True)
            except OSError:
                self.sizes[str(p)] = (0, False)
        for p in folders:
            if str(p) in self._counting:
                continue
            self._counting.add(str(p))

            def work(p=p):
                size, done = quick_size(p, 2.0)
                try:
                    self.sized.emit(str(p), size, done)
                except RuntimeError:  # (Rakiza Core is closing)
                    pass
            threading.Thread(target=work, daemon=True, name="corefold-files-size").start()

    def _sized(self, name: str, size: int, done: bool):
        self._counting.discard(name)
        if any(str(p) == name for p in self.items):
            self.sizes[name] = (size, done)
            self.statuses_changed.emit()

    def set_status(self, files: list, state: str, words: str = ""):
        changed = False
        for f in files:
            n = str(f)
            if any(str(p) == n for p in self.items):
                self.status[n] = (state, words)
                changed = True
        if changed:
            self.statuses_changed.emit()

    # ---- the operations (fed by the controller's jobs)

    def op(self, job_id: int) -> Op | None:
        return next((o for o in self.ops if o.job_id == job_id), None)

    def job_added(self, job_id: int, tool, files: list, params: dict, rename: dict, what: str):
        title = getattr(tool, "description", "") or getattr(tool, "label", "")
        keep = {k: v for k, v in (params or {}).items() if k != "password"}  # (a password is asked for again)
        self.ops.insert(0, Op(job_id, title, what, list(files), tool, keep, dict(rename or {})))
        del self.ops[MAX_OPS:]
        self.set_status(files, WORKING)
        self.ops_changed.emit()
        self.job_started.emit(job_id)

    def job_running(self, job_id: int):
        o = self.op(job_id)
        if o is not None and o.state == WAITING:
            o.state = RUNNING
            self.ops_changed.emit()

    def job_progress(self, job_id: int, frac: float):
        o = self.op(job_id)
        if o is not None:
            o.state = RUNNING if not o.over else o.state
            o.progress = frac
            self.ops_changed.emit()

    def job_item(self, job_id: int, text: str):
        o = self.op(job_id)
        if o is not None:
            o.detail = text
            self.ops_changed.emit()

    def job_retried(self, job_id: int):
        """A job that stopped to ask for a password and was started again (a new job takes its place)."""
        self.ops = [o for o in self.ops if o.job_id != job_id]
        self.ops_changed.emit()

    def job_finished(self, job_id: int, outputs: list, errors: list, cancelled: bool, note: str = ""):
        from .i18n import tr
        o = self.op(job_id)
        if o is None:
            return
        o.outputs, o.errors = list(outputs), list(errors)
        o.progress = 1.0
        o.when = time.time()
        if cancelled:
            o.state, o.detail = STOPPED, tr("Cancelled")
            self.set_status(o.files, CANCELLED, o.detail)
        elif errors and not outputs:
            o.state, o.detail = FAILED, errors[0]
            self.set_status(o.files, PROBLEM, "\n".join(errors[:3]))
        elif errors:
            o.state = FINISHED
            o.detail = tr("Done, but {count} couldn't be done", count=len(errors))
            self.set_status(o.files, PROBLEM, "\n".join(errors[:3]))
        else:
            o.state = FINISHED
            o.detail = note or (tr("Saved {count} items", count=len(outputs)) if len(outputs) > 1 else
                                tr("Saved {name}", name=Path(outputs[0]).name) if outputs else tr("Done"))
            self.set_status(o.files, DONE, o.detail if len(o.files) == 1 else tr("Done"))
        self.ops_changed.emit()

    def forget(self, job_id: int):
        self.ops = [o for o in self.ops if o.job_id != job_id or not o.over]
        self.ops_changed.emit()

    def forget_finished(self):
        self.ops = [o for o in self.ops if not o.over]
        self.ops_changed.emit()

    def running(self) -> int:
        return sum(1 for o in self.ops if not o.over)
