"""Corefold Unlock's page: drop a file or folder (or pick "What's using this?" in its right-click menu),
see which programs have it open, close them, then delete it or move it."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QFileInfo, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFileDialog, QFileIconProvider, QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, \
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget

from . import i18n, icons, unlock, work
from .atomic.widgets import Group, SettingRow
from .i18n import ltr, tr
from .panel import DropZone
from .theme import round_menu

if TYPE_CHECKING:
    from .app import Controller

log = logging.getLogger("corefold")


def _label(text: str = "", name: str | None = None, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def _button(text: str, name: str | None = None, on_click=None) -> QPushButton:
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    if on_click:
        b.clicked.connect(on_click)
    return b


def _para(text: str) -> str:
    """In Arabic, a line that starts with a Latin name still reads (and lines up) right to left."""
    return "\u200f" + text if text and i18n.is_rtl() else text


def note_for(p: unlock.Program, state: str, admin: bool) -> str:
    """What to know about this program, or what to do next."""
    if p.own:
        return tr("This is Corefold itself: close the Corefold window that has it open.")
    if p.critical:
        return tr("Part of Windows. Closing it would stop your PC working, so Corefold leaves it alone. "
                  "Restarting the PC frees the file.")
    if p.service:
        return tr("A Windows service. Restarting the PC frees the file.")
    if p.blocked:
        if admin:
            return tr("Windows doesn't let other programs close it. Restarting the PC frees the file.")
        return tr("It runs as administrator, so Corefold can only close it when Corefold runs as administrator "
                  "too.")
    if p.explorer:
        if state == "stuck":
            return tr("Its folder windows are closed, but it still has the file open. Restarting Explorer frees "
                      "it: the taskbar disappears for a moment.")
        return tr("File Explorer: its folder windows close first. If that's not enough, Explorer will restart.")
    if state == "stuck":
        return tr("Still open. It may be asking whether to save your work: look at its window. Or force it to "
                  "close, but anything not saved in it is lost.")
    if state == "no_window":
        return tr("It has no window to close, so it can only be forced to close. Anything not saved in it is "
                  "lost.")
    return ""


class ProgramRow(QWidget):
    """One program: its icon, name, PID and program file, what to know, and Close (or Force close)."""

    def __init__(self, page: "UnlockPage", prog: unlock.Program, state: str, icon: QIcon | None):
        super().__init__()
        self.page, self.prog, self.state = page, prog, state
        dpr = self.devicePixelRatioF()
        tile = QLabel()
        tile.setFixedSize(28, 28)
        tile.setAlignment(Qt.AlignCenter)
        if icon is not None and not icon.isNull():
            tile.setPixmap(icon.pixmap(QSize(24, 24), dpr))
        else:
            tile.setPixmap(icons.pixmap("apps", 22, page.c.theme.muted, dpr))
        bits = [tr("PID {pid}", pid=prog.pid)]
        if prog.exe:
            bits.append(ltr(prog.exe))
        controls = QWidget()
        h = QHBoxLayout(controls)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.close_btn = self.force_btn = None
        if prog.closable:
            if state in ("stuck", "no_window"):
                text = tr("Restart Explorer") if prog.explorer else tr("Force close")
                self.force_btn = _button(text, "danger", lambda: page.force(self.prog))
                h.addWidget(self.force_btn)
            else:
                self.close_btn = _button(tr("Closing…") if state == "closing" else tr("Close"), None,
                                         lambda: page.close([self.prog]))
                self.close_btn.setEnabled(state != "closing")
                h.addWidget(self.close_btn)
        self.row = SettingRow(_para(ltr(prog.name)), _para("  ·  ".join(bits)), controls if h.count() else None,
                              tile)
        self.row.description.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.row)
        note = note_for(prog, state, page.admin)
        if note:  # (under the name, the whole width of the row)
            self.note = _label(note, "warning" if state in ("stuck", "no_window") else "subtle")
            under = QHBoxLayout()
            under.setContentsMargins(0, 0, 0, 0)
            under.addSpacing(16 + 28 + 14)  # (the row's margin, its icon and the space after it)
            under.addWidget(self.note, 1)
            under.addSpacing(16)
            lay.addLayout(under)
            lay.addSpacing(12)
        self.setToolTip(ltr(prog.exe) if prog.exe else "")


class UnlockPage(QWidget):
    """Corefold Unlock in the window."""

    def __init__(self, controller: "Controller", header):
        super().__init__()
        self.c = controller
        self.paths: list[Path] = []
        self.found: unlock.Found | None = None
        self.states: dict[int, str] = {}   # pid: "closing", "stuck" (asked, still open) or "no_window"
        self.rows: list[ProgramRow] = []
        self.busy = False
        self.queued: list[Path] = []       # asked for while busy (another "What's using this?")
        self.jobs: list = []               # (kept until their answer is in)
        self.delete_failed = False         # deleting it didn't work: deleting it as the PC restarts is offered
        self.pending: tuple | None = None  # what to do once the programs let go ("Close and delete" …)
        self.admin = unlock.is_admin()
        self._icons = QFileIconProvider()
        self._icon_cache: dict[str, QIcon | None] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        inner = QWidget()
        inner.setObjectName("scrollContent")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(12)
        lay.addLayout(header)
        self.drop = DropZone(None, None, title=tr("Drop a file or folder here"),
                             sub=tr("See which programs are using it, close them, then move or delete it."),
                             hot_title=tr("Let go"),
                             buttons=[(tr("Choose a file"), self._choose_file, "primary"),
                                      (tr("Choose a folder"), self._choose_folder)])
        self.drop.dropped.connect(self.check)
        lay.addWidget(self.drop)

        self.result = QFrame()
        self.result.setObjectName("card")
        rl = QVBoxLayout(self.result)
        rl.setContentsMargins(18, 16, 18, 16)
        rl.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.path_icon = QLabel()
        self.path_icon.setFixedSize(34, 34)
        self.path_icon.setAlignment(Qt.AlignCenter)
        top.addWidget(self.path_icon, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(2)
        self.path_name = _label("", "cardTitle")
        self.path_text = _label("", "muted")
        self.path_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        words.addWidget(self.path_name)
        words.addWidget(self.path_text)
        top.addLayout(words, 1)
        rl.addLayout(top)
        self.status = _label("", "h2")
        rl.addWidget(self.status)
        self.capped = _label("", "subtle")
        rl.addWidget(self.capped)
        self.problem = _label("", "warning")
        rl.addWidget(self.problem)
        self.capped.hide()
        self.problem.hide()
        self.group = Group()
        rl.addWidget(self.group)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.close_all_btn = _button(tr("Close all"), "primary", self.close_all)
        self.delete_btn = _button(tr("Delete it"), "danger", self.delete)
        self.again_btn = _button(tr("Check again"), None, lambda: self.check(self.paths))
        self.move_btn = _button(tr("Move to…"), None, self.move)
        self.rename_btn = _button(tr("Rename…"), None, self.rename)
        self.later_btn = _button(tr("Delete when the PC restarts"), None, self.delete_at_restart)
        self.later_btn.setToolTip(tr("Windows deletes it as the PC starts, before any program can open it."))
        # one click for the whole thing (as IObit Unlocker does): close what has it open, then do it
        self.then_btn = _button(tr("Close and…"), None)
        then_menu = QMenu(self.then_btn)
        round_menu(then_menu)
        then_menu.addAction(tr("Close and delete it"), lambda: self.close_then("delete"))
        then_menu.addAction(tr("Close and move it…"), lambda: self.close_then("move"))
        self.then_rename = then_menu.addAction(tr("Close and rename it…"), lambda: self.close_then("rename"))
        self.then_btn.setMenu(then_menu)
        self.copy_btn = _button(tr("Copy to…"), None, self.copy_to)
        self.copy_btn.setToolTip(tr("Most programs let a file be read while they have it open, so it can be "
                                    "copied without closing them."))
        self.later_more_btn = _button(tr("When the PC restarts…"), None)
        later_menu = QMenu(self.later_more_btn)
        round_menu(later_menu)
        later_menu.addAction(tr("Move it there then…"), lambda: self.move_at_restart("move"))
        self.later_rename = later_menu.addAction(tr("Rename it then…"), lambda: self.move_at_restart("rename"))
        self.later_more_btn.setMenu(later_menu)
        self.later_more_btn.setToolTip(tr("Windows moves or renames it as the PC starts, before any program can "
                                          "open it. Only on the same drive."))
        buttons.addWidget(self.close_all_btn)
        buttons.addWidget(self.then_btn)
        buttons.addWidget(self.delete_btn)
        buttons.addWidget(self.move_btn)
        buttons.addWidget(self.rename_btn)
        buttons.addWidget(self.copy_btn)
        buttons.addWidget(self.later_btn)
        buttons.addWidget(self.later_more_btn)
        buttons.addStretch()
        buttons.addWidget(self.again_btn)
        rl.addLayout(buttons)
        self.bin_note = _label(tr("It goes to the Recycle Bin, where you can put it back."), "subtle")
        rl.addWidget(self.bin_note)
        lay.addWidget(self.result)
        self.result.hide()

        tips = QFrame()
        tips.setObjectName("card")
        tl = QVBoxLayout(tips)
        tl.setContentsMargins(18, 16, 18, 16)
        tl.setSpacing(8)
        tl.addWidget(_label(tr("Good to know"), "h2"))
        for text in (tr("Right-click any file or folder in File Explorer and pick \"What's using this?\" (on "
                        "Windows 11, under \"Show more options\")."),
                     tr("Closing a program asks it to close itself, the same as clicking its X, so it can ask you "
                        "to save your work. Only if it's still open after that can you force it to close."),
                     tr("Corefold never closes the parts of Windows your PC needs to keep working.")):
            tl.addWidget(_label(text, "muted"))
        lay.addWidget(tips)
        lay.addStretch()

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(inner)
        self.scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(self.scroll)

    # ---- picking what to look at

    def _choose_file(self):
        name, _ = QFileDialog.getOpenFileName(self, tr("Choose a file"))
        if name:
            self.check([Path(name)])

    def _choose_folder(self):
        name = QFileDialog.getExistingDirectory(self, tr("Choose a folder"))
        if name:
            self.check([Path(name)])

    # ---- looking (off the window's thread: a folder can have thousands of files)

    def check(self, paths: list[Path], keep_states: bool = False):
        """Looks for the programs using these files or folders."""
        paths = [Path(p) for p in paths if p]
        if not paths:
            return
        if self.busy:
            self.queued = paths
            return
        if paths != self.paths:
            keep_states = False
            self.delete_failed = False
            self.pending = None
        self.paths = paths
        if not keep_states:
            self.states = {}
        self._show_path()
        self.problem.hide()
        self._set_busy(True, tr("Looking for programs that are using it…"))
        self._run(lambda _p: unlock.find(paths), self._found)

    def _run(self, fn, done):
        job = work.run(fn, lambda _: None, done, name="corefold-unlock", busy=True)
        self.jobs.append(job)

    def _found(self, result):
        self.jobs = [j for j in self.jobs if j.is_alive()]
        if not work.alive(self):
            return
        self._set_busy(False)
        if isinstance(result, Exception):
            log.warning("couldn't find what's using %s: %s", self.paths, result)
            self.found = None
            self._say(tr("Windows couldn't say what's using it. Try again in a moment."))
            self._fill()
            return
        self.found = result
        live = {p.pid for p in result.programs}
        self.states = {pid: s for pid, s in self.states.items() if pid in live}
        self._fill()
        if self.pending and self._then():
            return
        self._next()

    def _next(self):
        """Another "What's using this?" came while busy: its turn now."""
        if self.queued and not self.busy:
            paths, self.queued = self.queued, []
            self.check(paths)

    def _show_path(self):
        dpr = self.devicePixelRatioF()
        first = self.paths[0]
        if len(self.paths) == 1:
            self.path_name.setText(_para(ltr(first.name or str(first))))
            self.path_text.setText(_para(ltr(str(first))))
        else:
            self.path_name.setText(tr("{count} files and folders", count=len(self.paths)))
            shown = [ltr(str(p)) for p in self.paths[:3]]
            if len(self.paths) > 3:
                shown.append("…")
            self.path_text.setText("\n".join(shown))
        ic = self._icons.icon(QFileInfo(str(first))) if first.exists() else QIcon()
        if ic.isNull():
            self.path_icon.setPixmap(icons.pixmap("folder" if first.is_dir() else "document", 28,
                                                  self.c.theme.muted, dpr))
        else:
            self.path_icon.setPixmap(ic.pixmap(QSize(32, 32), dpr))
        self.result.show()

    def _fill(self):
        for row in self.rows:
            row.hide()
            row.deleteLater()
        self.rows = []
        found = self.found
        programs = found.programs if found else []
        for p in programs:
            row = ProgramRow(self, p, self.states.get(p.pid, ""), self._icon(p.exe))
            self.group.add(row)
            self.rows.append(row)
        self.group.setVisible(bool(programs))
        self.group.update()
        if found is None:
            self.status.setText("")
        elif programs:
            self.status.setText(tr("Programs using it: {count}", count=len(programs)))
        elif self.paths and not any(os.path.lexists(p) for p in self.paths):
            self.status.setText(tr("It isn't there any more."))
        else:
            self.status.setText(tr("Nothing is using it right now. You can move or delete it."))
        self.status.setVisible(bool(self.status.text()))
        self.capped.setText(tr("There are more than {count} files in it, so only the first {count} were checked.",
                               count=unlock.MAX_FILES) if found and found.capped else "")
        self.capped.setVisible(bool(found and found.capped))
        idle = [p for p in programs if p.closable and not self.states.get(p.pid)]
        self.close_all_btn.setVisible(len(idle) > 1)
        exists = any(os.path.lexists(p) for p in self.paths)
        self.then_btn.setVisible(bool(idle) and exists)
        self.then_rename.setVisible(len(self.paths) == 1)
        self.copy_btn.setVisible(found is not None and exists)
        can_delete = found is not None and not programs and exists
        self.delete_btn.setVisible(can_delete)
        self.move_btn.setVisible(can_delete)
        self.rename_btn.setVisible(can_delete and len(self.paths) == 1)
        self.bin_note.setVisible(can_delete)
        # (what won't let go, even of a program that won't close: Windows deletes it as the PC starts)
        stuck = any(self.states.get(p.pid) in ("stuck", "no_window") or not p.closable for p in programs)
        self.later_btn.setVisible(found is not None and exists and (self.delete_failed or stuck))
        self.later_more_btn.setVisible(self.later_btn.isVisibleTo(self.result))
        self.later_rename.setVisible(len(self.paths) == 1)
        self.again_btn.setVisible(bool(self.paths))
        if self.busy:
            self._set_busy(True)

    def _icon(self, exe: str) -> QIcon | None:
        """The program's own icon (kept, so checking again doesn't read the file again)."""
        if not exe:
            return None
        if exe not in self._icon_cache:
            ic = self._icons.icon(QFileInfo(exe)) if os.path.isfile(exe) else None
            self._icon_cache[exe] = ic if ic is not None and not ic.isNull() else None
        return self._icon_cache[exe]

    def _set_busy(self, on: bool, text: str = ""):
        self.busy = on
        for b in (self.close_all_btn, self.delete_btn, self.move_btn, self.rename_btn, self.again_btn,
                  self.later_btn, self.then_btn, self.copy_btn, self.later_more_btn):
            b.setEnabled(not on)
        for row in self.rows:
            for b in (row.close_btn, row.force_btn):
                if b is not None:
                    b.setEnabled(not on)
        if on and text:
            self.status.setText(text)
            self.status.show()

    def _say(self, text: str):
        self.problem.setText(text)
        self.problem.setVisible(bool(text))

    # ---- closing

    def close_all(self):
        found = self.found
        if found:
            self.close([p for p in found.programs if p.closable and not self.states.get(p.pid)])

    def close(self, programs: list):
        """Asks them to close (like clicking their X), waits a few seconds, then looks again."""
        if not programs or self.busy:
            return
        for p in programs:
            self.states[p.pid] = "closing"
        self._fill()
        self._set_busy(True, tr("Asking it to close…"))
        paths = list(self.paths)

        def go(_p):
            answers = unlock.close_politely(programs)
            return answers, unlock.find(paths)
        self._run(go, self._closed)

    def _closed(self, result):
        if not work.alive(self):
            return
        if isinstance(result, Exception):
            self.states = {pid: s for pid, s in self.states.items() if s != "closing"}
            self._found(result)
            return
        answers, found = result
        for pid, answer in answers.items():
            if answer == "running":
                self.states[pid] = "stuck"
            elif answer == "no_window":
                self.states[pid] = "no_window"
            elif answer == "closed":
                self.states[pid] = "stuck"  # (only matters if it still has the file open: Explorer)
            else:
                self.states.pop(pid, None)
        self._found(found)

    def force(self, prog: unlock.Program):
        """Ends it straight away, once the person agreed that unsaved work in it is lost."""
        if self.busy:
            return
        if not prog.explorer and not self._confirm_force(prog):
            return
        self.states[prog.pid] = "closing"
        self._set_busy(True, tr("Closing it…"))
        paths = list(self.paths)

        def go(_p):
            gone = unlock.force_close(prog)
            return gone, unlock.find(paths)
        self._run(go, lambda result, p=prog: self._forced(p, result))

    def _confirm_force(self, prog: unlock.Program) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("Force close"))
        box.setText(tr("Force {name} to close?", name=ltr(prog.name)))
        box.setInformativeText(tr("Anything you haven't saved in it is lost."))
        ok = box.addButton(tr("Force close"), QMessageBox.DestructiveRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        chosen = box.clickedButton() is ok
        box.deleteLater()
        return chosen

    def _forced(self, prog: unlock.Program, result):
        if not work.alive(self):
            return
        if isinstance(result, Exception):
            self.states[prog.pid] = "no_window"
            self._found(result)
            return
        gone, found = result
        self.states[prog.pid] = "no_window"
        self._found(found)
        if not gone and any(p.pid == prog.pid for p in found.programs):
            self._say(tr("Windows didn't let Corefold close {name}.", name=ltr(prog.name)))

    # ---- deleting

    def delete(self):
        """To the Recycle Bin (the same way Corefold Space does it)."""
        if self.busy or not self.paths:
            return
        paths = list(self.paths)
        self._set_busy(True, tr("Sending it to the Recycle Bin…"))

        def go(_p):
            from .space import recycle
            return recycle(paths)
        self._run(go, self._deleted)

    def _deleted(self, result):
        self.jobs = [j for j in self.jobs if j.is_alive()]
        if not work.alive(self):
            return
        self._set_busy(False)
        left = [p for p in self.paths if os.path.lexists(p)]
        if isinstance(result, Exception) or left:
            if isinstance(result, Exception):
                log.warning("couldn't delete %s: %s", self.paths, result)
            self.delete_failed = True
            self._fill()
            self._say(tr("Windows didn't let Corefold delete it. Something may have opened it again: check again, "
                         "or have it deleted when the PC restarts."))
            self._next()
            return
        self._fill()
        self.status.setText(tr("Sent to the Recycle Bin. You can put it back from there."))
        self.status.show()
        self.again_btn.hide()
        self._next()

    # ---- moving and renaming, now that nothing has it open (as IObit Unlocker does)

    def _pick_folder(self, title: str) -> str:
        return QFileDialog.getExistingDirectory(self, title, str(self.paths[0].parent))

    def _move_problem(self, dest_dir: Path) -> str:
        taken = [p.name for p in self.paths if (dest_dir / p.name).exists() or (dest_dir / p.name) == p]
        return tr("There's already something called {name} there.", name=ltr(taken[0])) if taken else ""

    def _ask_name(self) -> str | None:
        """The new name typed (checked), or None."""
        old = self.paths[0]
        new_name, ok = QInputDialog.getText(self, tr("Rename…"), tr("New name:"), text=old.name)
        if not ok:
            return None
        new_name = new_name.strip().rstrip(". ")
        if not new_name or new_name == old.name:
            return None
        problem = self._name_problem(new_name)
        if problem:
            self._say(problem)
            return None
        return new_name

    def _name_problem(self, new_name: str) -> str:
        if any(ch in new_name for ch in '\\/:*?"<>|'):
            return tr("A name can't have any of these in it: {chars}", chars='\\ / : * ? " < > |')
        old = self.paths[0]
        target = old.with_name(new_name)
        if target.exists() and os.path.normcase(str(target)) != os.path.normcase(str(old)):
            return tr("There's already something called {name} there.", name=ltr(new_name))
        return ""

    def close_then(self, action: str):
        """Close the programs that have it open, then delete, move or rename it in the same go. What's
        asked (where to, the new name) is asked first; if a program won't let go, the page says what's left."""
        if self.busy or not self.paths or not self.found:
            return
        arg = None
        if action == "move":
            dest = self._pick_folder(tr("Move to…"))
            if not dest:
                return
            problem = self._move_problem(Path(dest))
            if problem:
                self._say(problem)
                return
            arg = dest
        elif action == "rename":
            if len(self.paths) != 1:
                return
            arg = self._ask_name()
            if arg is None:
                return
        self._say("")
        self.pending = (action, arg)
        self.close([p for p in self.found.programs if p.closable and not self.states.get(p.pid)])

    def _then(self) -> bool:
        """After closing: the delete, move or rename asked for, if nothing has it open now. True if started."""
        action, arg = self.pending
        found = self.found
        if found is None:
            self.pending = None
            return False
        if found.programs:
            names = ", ".join(ltr(p.name) for p in found.programs[:3])
            self._say(tr("{names} still has it open. Force it to close, or have Windows do it when the PC restarts.",
                         names=names))
            return False
        self.pending = None
        if action == "delete":
            self.delete()
        elif action == "move":
            self.move(arg)
        else:
            self.rename(arg)
        return True

    def move(self, dest: str | None = None):
        if self.busy or not self.paths:
            return
        if dest is None:
            dest = self._pick_folder(tr("Move to…"))
        if not dest:
            return
        dest_dir = Path(dest)
        problem = self._move_problem(dest_dir)
        if problem:
            self._say(problem)
            return
        paths = list(self.paths)
        self._set_busy(True, tr("Moving it…"))

        def go(_p):
            import shutil
            moved = []
            for p in paths:
                target = dest_dir / p.name
                shutil.move(str(p), str(target))
                moved.append(target)
            return moved
        self._run(go, lambda result: self._moved(result, tr("Moved to {folder}.", folder=ltr(str(dest_dir)))))

    def rename(self, new_name: str | None = None):
        if self.busy or len(self.paths) != 1:
            return
        old = self.paths[0]
        if new_name is None:
            new_name = self._ask_name()
            if new_name is None:
                return
        new_name = new_name.strip().rstrip(". ")
        if not new_name or new_name == old.name:
            return
        problem = self._name_problem(new_name)
        if problem:
            self._say(problem)
            return
        target = old.with_name(new_name)
        self._set_busy(True, tr("Renaming it…"))

        def go(_p):
            os.rename(old, target)
            return [target]
        self._run(go, lambda result: self._moved(result, tr("Renamed to {name}.", name=ltr(new_name))))

    def _moved(self, result, said: str):
        self.jobs = [j for j in self.jobs if j.is_alive()]
        if not work.alive(self):
            return
        self._set_busy(False)
        if isinstance(result, Exception):
            log.warning("couldn't move or rename %s: %s", self.paths, result)
            self._fill()
            self._say(tr("Windows didn't let Corefold do it. Something may have opened it again: check again."))
            self._next()
            return
        self.paths = list(result)
        self._show_path()
        self._say("")
        self._fill()
        self.status.setText(said)
        self.status.show()
        self._next()

    # ---- copying (reading is allowed even while most programs have it open)

    def copy_to(self, dest: str | None = None):
        if self.busy or not self.paths:
            return
        if dest is None:
            dest = QFileDialog.getExistingDirectory(self, tr("Copy to…"), str(self.paths[0].parent))
        if not dest:
            return
        dest_dir = Path(dest)
        problem = self._move_problem(dest_dir)
        if problem:
            self._say(problem)
            return
        paths = list(self.paths)
        self._set_busy(True, tr("Copying it…"))

        def go(_p):
            import shutil
            made = []
            for p in paths:
                target = dest_dir / p.name
                if p.is_dir() and not p.is_symlink():
                    shutil.copytree(p, target, symlinks=True)
                else:
                    shutil.copy2(p, target, follow_symlinks=False)
                made.append(target)
            return made
        self._run(go, lambda result: self._copied(result, dest_dir))

    def _copied(self, result, dest_dir: Path):
        self.jobs = [j for j in self.jobs if j.is_alive()]
        if not work.alive(self):
            return
        self._set_busy(False)
        self._fill()
        if isinstance(result, Exception):
            log.warning("couldn't copy %s: %s", self.paths, result)
            locked = isinstance(result, OSError) and getattr(result, "winerror", 0) in (32, 33)
            self._say(tr("A program has it open and won't let it be read. Close it first, then copy it.") if locked
                      else tr("Windows didn't let Corefold copy it: {error}", error=result))
        else:
            self._say("")
            self.status.setText(tr("Copied to {folder}.", folder=ltr(str(dest_dir))))
            self.status.show()
        self._next()

    # ---- when the PC restarts

    def move_at_restart(self, action: str):
        """Windows moves or renames it as the PC starts (as administrator, so Windows asks first)."""
        if self.busy or not self.paths:
            return
        if action == "rename":
            if len(self.paths) != 1:
                return
            name = self._ask_name()
            if name is None:
                return
            moves = [(self.paths[0], self.paths[0].with_name(name))]
        else:
            dest = self._pick_folder(tr("Move it there then…"))
            if not dest:
                return
            problem = self._move_problem(Path(dest))
            if problem:
                self._say(problem)
                return
            moves = [(p, Path(dest) / p.name) for p in self.paths]
        problems = {unlock.move_at_restart_problem(str(a), str(b)) for a, b in moves} - {""}
        if "drive" in problems:
            self._say(tr("Windows can only do this on the same drive. Pick a folder on the same drive."))
            return
        if problems:
            self._say(tr("Corefold doesn't move Windows' or programs' own folders this way."))
            return
        self._set_busy(True, tr("Waiting for Windows' permission…"))
        self._run(lambda _p: unlock.ask_move_at_restart(moves), self._move_scheduled)

    def _move_scheduled(self, result):
        self.jobs = [j for j in self.jobs if j.is_alive()]
        if not work.alive(self):
            return
        self._set_busy(False)
        self._fill()
        try:
            if result is None:
                self._say(tr("Nothing was changed: Windows' permission wasn't given."))
            elif isinstance(result, dict) and result.get("scheduled") and not result.get("error"):
                self._say("")
                self.later_btn.hide()
                self.later_more_btn.hide()
                self.status.setText(tr("It will be moved the next time the PC restarts."))
                self.status.show()
            else:
                log.warning("couldn't ask for %s to be moved at restart: %s", self.paths, result)
                self._say(tr("Windows didn't take it. Try again, or restart the PC and move it then."))
        finally:
            self._next()

    def delete_at_restart(self):
        """Windows deletes it as the PC starts (as administrator, so Windows asks first). For good: it
        doesn't go to the Recycle Bin."""
        if self.busy or not self.paths:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("Delete it when the PC restarts?"))
        box.setText(tr("Windows deletes it for good as the PC starts, before any program can open it. It doesn't "
                       "go to the Recycle Bin. Windows asks for permission first."))
        yes = box.addButton(tr("Delete when the PC restarts"), QMessageBox.DestructiveRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        paths = list(self.paths)
        self._set_busy(True, tr("Waiting for Windows' permission…"))
        self._run(lambda _p: unlock.ask_delete_at_restart(paths), self._scheduled)

    def _scheduled(self, result):
        self.jobs = [j for j in self.jobs if j.is_alive()]
        if not work.alive(self):
            return
        self._set_busy(False)
        self._fill()
        try:
            self._said_scheduled(result)
        finally:
            self._next()

    def _said_scheduled(self, result):
        if result is None:
            self._say(tr("Nothing was changed: Windows' permission wasn't given."))
            return
        if isinstance(result, Exception) or not isinstance(result, dict) or result.get("error") \
                or not result.get("scheduled"):
            if isinstance(result, dict) and result.get("error") == "too many":
                self._say(tr("There are too many files in it to delete as the PC restarts."))
            elif isinstance(result, dict) and result.get("refused"):
                self._say(tr("Corefold doesn't delete Windows' or programs' own folders this way."))
            else:
                log.warning("couldn't ask for %s to be deleted at restart: %s", self.paths, result)
                self._say(tr("Windows didn't take it. Try again, or restart the PC and delete it then."))
            return
        self._say("")
        self.later_btn.hide()
        self.delete_btn.hide()
        self.bin_note.hide()
        self.status.setText(tr("It will be deleted the next time the PC restarts."))
        self.status.show()

    # ---- the rest of the window

    def apply_theme(self, _theme=None):
        if self.paths:
            self._show_path()
            self._fill()
