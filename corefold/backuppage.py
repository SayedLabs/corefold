"""The Backup page: pick the folders to keep safe and the drive they go to; a backup then runs by itself once a day
whenever that drive is connected (or now, with a click). The work is backup.py; the app runs it (Controller
.run_drive_backup), so a backup by itself and one started here are the same."""
from __future__ import annotations

import datetime
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QProgressBar, \
    QPushButton, QScrollArea, QVBoxLayout, QWidget

from . import backup
from .atomic.widgets import Dropdown, FlowLayout, Switch
from .core import human_size, os_open
from .i18n import ltr, tr

if TYPE_CHECKING:
    from .app import Controller

log = logging.getLogger("corefold")

KEEP = ((30, "A month"), (90, "Three months"), (365, "A year"))
PROBLEMS = ("Add the folders to keep safe", "Choose where the backup goes", "The backup drive isn't connected",
            "The backup can't go inside a folder it keeps safe", "A folder to keep safe can't be inside the backup",
            "The backup drive is full", "Stopped")
COMMON = (("Documents", "Documents"), ("Pictures", "Pictures"), ("Desktop", "Desktop"), ("Music", "Music"),
          ("Videos", "Videos"))


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
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


def plan_of(settings) -> backup.Plan:
    plans = backup.load(getattr(settings, "drive_backups", ""))
    return plans[0] if plans else backup.Plan()


def save_plan(settings, plan: backup.Plan) -> None:
    settings.drive_backups = backup.dump([plan])
    settings.save()


class BackupPage(QWidget):
    """Corefold Backup in the window."""

    def __init__(self, controller: "Controller", header):
        super().__init__()
        self.c = controller
        self.plan = plan_of(self.c.settings)

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        inner = QWidget()
        inner.setObjectName("scrollContent")
        outer = QVBoxLayout(inner)
        outer.setContentsMargins(0, 0, 10, 0)
        outer.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(inner)
        box.addWidget(scroll)
        outer.addLayout(header)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self._folders_card(), 1)
        row.addWidget(self._drive_card(), 1)
        outer.addLayout(row)
        outer.addWidget(self._status_card())
        outer.addStretch()
        self.show_plan()

    # ---- what's kept safe

    def _folders_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        lay.addWidget(_label(tr("Folders to keep safe"), "h2"))
        self.folders = QListWidget()
        self.folders.setMinimumHeight(150)
        self.folders.itemSelectionChanged.connect(self._selection)
        lay.addWidget(self.folders)
        row = QWidget()
        quick = FlowLayout(row, spacing=6)   # (wraps to a second line in a narrow window or a long language)
        self.quick_buttons = []
        for folder, text in COMMON:
            b = _button("+ " + tr(text), "small", lambda _=False, f=folder: self.add_folder(Path.home() / f))
            self.quick_buttons.append((folder, b))
            quick.addWidget(b)
        lay.addWidget(row)
        buttons = QHBoxLayout()
        buttons.addWidget(_button(tr("Add a folder"), None, self._choose_folder))
        self.remove = _button(tr("Take out"), "ghost", self.remove_selected)
        buttons.addWidget(self.remove)
        buttons.addStretch()
        lay.addLayout(buttons)
        return card

    def _drive_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        lay.addWidget(_label(tr("Where it goes"), "h2"))
        self.dest = _label("", wrap=True)
        lay.addWidget(self.dest)
        self.dest_note = _label("", "subtle", wrap=True)
        lay.addWidget(self.dest_note)
        lay.addWidget(_button(tr("Choose a drive or folder"), None, self._choose_dest), 0, Qt.AlignLeft)
        self.auto = Switch(tr("Back up by itself once a day, whenever the drive is connected"))
        self.auto.toggled.connect(self._auto_changed)
        lay.addWidget(self.auto)
        line = QHBoxLayout()
        line.addWidget(_label(tr("Keep older versions for"), "subtle"))
        self.keep = Dropdown()
        self.keep.setAccessibleName(tr("Keep older versions for"))
        for days, text in KEEP:
            self.keep.addItem(tr(text), days)
        self.keep.currentIndexChanged.connect(self._keep_changed)
        line.addWidget(self.keep)
        line.addStretch()
        lay.addLayout(line)
        lay.addStretch()
        return card

    def _status_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        self.status = _label("", "h2", wrap=True)
        lay.addWidget(self.status)
        self.detail = _label("", "subtle", wrap=True)
        lay.addWidget(self.detail)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.hide()
        lay.addWidget(self.bar)
        buttons = QHBoxLayout()
        self.go = _button(tr("Back up now"), "primary", self.back_up)
        buttons.addWidget(self.go)
        self.stop = _button(tr("Stop"), None, self._stop)
        self.stop.hide()
        buttons.addWidget(self.stop)
        buttons.addStretch()
        self.open_backup = _button(tr("Open the backup"), "ghost", self._open_backup)
        buttons.addWidget(self.open_backup)
        self.open_older = _button(tr("Older versions"), "ghost", self._open_older)
        buttons.addWidget(self.open_older)
        lay.addLayout(buttons)
        lay.addWidget(_label(tr("The backup is an ordinary folder: open it in File Explorer and copy back what you "
                                "need. A file as it was before it changed or was deleted is in Older versions, by "
                                "date."), "subtle", wrap=True))
        return card

    # ---- showing the plan

    def show_plan(self):
        p = self.plan
        self.folders.clear()
        for s in p.sources:
            item = QListWidgetItem(ltr(s))
            item.setData(Qt.UserRole, s)
            item.setToolTip(s)
            self.folders.addItem(item)
        have = {os.path.normcase(s) for s in p.sources}
        for folder, b in self.quick_buttons:
            path = Path.home() / folder
            b.setVisible(path.is_dir() and os.path.normcase(str(path)) not in have)
        self._selection()
        self.dest.setText(ltr(p.dest) if p.dest else tr("No drive chosen yet"))
        notes = []
        if p.dest and Path(p.dest).is_dir():
            try:
                notes.append(tr("{size} free there", size=human_size(backup.shutil.disk_usage(p.dest).free)))
            except OSError:
                pass
            if backup.same_drive(p.sources, p.dest):
                notes.append(tr("This is the drive the folders are on: if it breaks, the backup goes with it. A USB "
                                "drive or another disk is safer."))
        self.dest_note.setText(" ".join(notes))
        self.auto.blockSignals(True)
        self.auto.setChecked(p.auto)
        self.auto.blockSignals(False)
        self.keep.blockSignals(True)
        i = self.keep.findData(p.keep_days)
        self.keep.setCurrentIndex(i if i >= 0 else 1)
        self.keep.blockSignals(False)
        self.show_status()

    def show_status(self):
        p = self.plan
        running = self.c.drive_backup_running()
        self.bar.setVisible(running)
        self.stop.setVisible(running)
        why = backup.problem(p.sources, p.dest)
        self.go.setEnabled(not running and not why)
        has_backup = bool(p.dest) and backup.home(p).is_dir()
        self.open_backup.setEnabled(has_backup)
        self.open_older.setEnabled(has_backup and bool(backup.versions(p)))
        if running:
            self.status.setText(tr("Backing up…"))
            return
        if p.last:
            when = datetime.datetime.fromtimestamp(p.last)
            day = tr("today") if when.date() == datetime.date.today() else when.strftime("%Y-%m-%d")
            self.status.setText(tr("Last backup: {day} at {time}", day=day, time=when.strftime("%H:%M")))
            bits = [tr("{count} files copied ({size})", count=f"{p.last_files:,}", size=human_size(p.last_bytes))
                    if p.last_files else tr("Nothing had changed")]
            if p.last_failed:
                bits.append(tr("{count} couldn't be copied (in use, or Windows said no)", count=f"{p.last_failed:,}"))
            self.detail.setText(". ".join(bits) + ".")
        else:
            self.status.setText(tr("Not backed up yet"))
            self.detail.setText("")
        if p.last_problem and p.last_problem in PROBLEMS:
            self.detail.setText((self.detail.text() + " " if self.detail.text() else "") +
                                tr("The last backup didn't finish: {why}.", why=tr(p.last_problem)))
        elif why and p.sources and p.dest:
            self.detail.setText(tr(why) + ".")

    def _selection(self):
        self.remove.setEnabled(bool(self.folders.selectedItems()))

    # ---- changes

    def _save(self):
        save_plan(self.c.settings, self.plan)
        self.c.keep_drive_backup()
        self.show_plan()

    def add_folder(self, folder: Path):
        s = str(folder)
        if not folder.is_dir() or any(os.path.normcase(s) == os.path.normcase(x) for x in self.plan.sources):
            return
        self.plan.sources.append(s)
        self._save()

    def remove_selected(self):
        gone = {item.data(Qt.UserRole) for item in self.folders.selectedItems()}
        self.plan.sources = [s for s in self.plan.sources if s not in gone]
        self._save()

    def set_dest(self, folder: Path):
        self.plan.dest = str(folder)
        why = backup.problem(self.plan.sources, self.plan.dest)
        if why in ("The backup can't go inside a folder it keeps safe",
                   "A folder to keep safe can't be inside the backup"):
            self.c.tell(tr("Backup"), tr(why))
        self._save()

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, tr("Choose a folder to keep safe"), str(Path.home()))
        if folder:
            self.add_folder(Path(folder))

    def _choose_dest(self):
        folder = QFileDialog.getExistingDirectory(self, tr("Choose where the backup goes"),
                                                  self.plan.dest or str(Path.home()))
        if folder:
            self.set_dest(Path(folder))

    def _auto_changed(self, on: bool):
        self.plan.auto = bool(on)
        self._save()

    def _keep_changed(self, _i: int):
        self.plan.keep_days = int(self.keep.currentData() or backup.KEEP_DAYS)
        self._save()

    # ---- running

    def back_up(self):
        self.c.run_drive_backup(now=True)
        self.show_status()

    def _stop(self):
        self.c.stop_drive_backup()

    def backup_progress(self, files: int, size: int, name: str):
        self.detail.setText(tr("{count} files looked at, {size} copied", count=f"{files:,}", size=human_size(size))
                            + " · " + ltr(name))

    def backup_done(self):
        self.plan = plan_of(self.c.settings)
        self.show_plan()

    def _open_backup(self):
        self._open(backup.home(self.plan))

    def _open_older(self):
        self._open(backup.home(self.plan) / backup.OLDER)

    def _open(self, folder: Path):
        if folder.is_dir():
            try:
                os_open(folder)
            except OSError:
                log.info("couldn't open %s", folder, exc_info=True)

    def refresh(self):
        self.plan = plan_of(self.c.settings)
        self.show_plan()

    def apply_theme(self, _theme=None):
        pass
