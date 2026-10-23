"""Corefold Space's empty folders window: finds folders with nothing in them (corefold/emptyfolders.py)
and sends the ticked ones to the Recycle Bin. Opened from Space's Duplicates section."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton, \
    QTreeWidget, QTreeWidgetItem, QVBoxLayout

from . import emptyfolders, work
from .i18n import ltr, tr
from .timefmt import date_text
from .winutil import reveal_in_explorer, set_title_bar_dark


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


def inside_text(item: emptyfolders.EmptyFolder) -> str:
    """What's in it: nothing, empty folders, or only files Windows leaves by itself."""
    names = ", ".join(item.leftovers)
    if item.inside == 0:
        return tr("Only {names}", names=ltr(names)) if names else tr("Nothing")
    if item.inside == 1:
        return tr("1 empty folder and {names}", names=ltr(names)) if names else tr("1 empty folder")
    count = f"{item.inside:,}"
    return tr("{count} empty folders and {names}", count=count, names=ltr(names)) if names \
        else tr("{count} empty folders", count=count)


class EmptyFoldersDialog(QDialog):
    """Its own window, so Space's page stays as it is; looking goes on while it's open."""

    def __init__(self, page):
        super().__init__(page, Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint |
                         Qt.WindowMaximizeButtonHint)
        self.page = page
        self.c = page.c
        self.cancel = threading.Event()
        self._cancels = [self.cancel]
        # (closed along with Space's page or the main window: looking stops too)
        self.destroyed.connect(lambda *_, cancels=self._cancels: [c.set() for c in cancels])
        self.job = None
        self.found: emptyfolders.Found | None = None
        self.roots: list[Path] = []
        self.setWindowTitle(tr("Empty folders"))
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(780, 580)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)
        lay.addWidget(_label(tr("Empty folders"), "h2"))
        lay.addWidget(_label(tr("Folders with no files in them, left behind by apps and old projects. They go to "
                                "the Recycle Bin, where you can put them back."), "muted", wrap=True))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.look_btn = _button(tr("Look in your folders"), "primary", lambda: self.start(None))
        self.pick_btn = _button(tr("Choose a folder…"), None, self._pick)
        row.addWidget(self.look_btn)
        row.addWidget(self.pick_btn)
        row.addStretch()
        lay.addLayout(row)
        self.where = _label(self._your_folders(), "muted", wrap=True)
        lay.addWidget(self.where)
        busy = QHBoxLayout()
        busy.setSpacing(10)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.stop_btn = _button(tr("Stop"), None, self.stop)
        busy.addWidget(self.bar, 1)
        busy.addWidget(self.stop_btn)
        lay.addLayout(busy)
        self.status = _label("", "muted", wrap=True)
        lay.addWidget(self.status)
        self.tree = QTreeWidget()
        self.tree.setObjectName("history")
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setHeaderLabels([tr("Folder"), tr("What's inside"), tr("Created")])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.itemChanged.connect(lambda *_: self._ticked_changed())
        self.tree.currentItemChanged.connect(lambda *_: self._ticked_changed())
        self.tree.itemDoubleClicked.connect(lambda item, _col: self._show(item))
        lay.addWidget(self.tree, 1)
        self.notes = _label("", "muted", wrap=True)
        lay.addWidget(self.notes)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.count = _label("", None, wrap=True)
        bottom.addWidget(self.count, 1)
        self.show_btn = _button(tr("Show in folder"), None, lambda: self._show(self.tree.currentItem()))
        self.remove_btn = _button(tr("Move to the Recycle Bin"), "primary", self.remove)
        bottom.addWidget(self.show_btn)
        bottom.addWidget(self.remove_btn)
        lay.addLayout(bottom)
        self._status("")
        self._idle()

    @staticmethod
    def show_for(page) -> "EmptyFoldersDialog":
        """Opens the window (or brings back the one already open) and starts looking in your folders."""
        dlg = getattr(page, "_empty_dialog", None)
        if dlg is None or not work.alive(dlg):
            dlg = EmptyFoldersDialog(page)
            page._empty_dialog = dlg
            dlg.show()
            dlg.start(None)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def showEvent(self, e):
        super().showEvent(e)
        set_title_bar_dark(self, self.c.theme.dark)

    def _your_folders(self) -> str:
        names = [tr(n) for n in emptyfolders.USER_FOLDERS]
        if any(p.name.lower().startswith("onedrive") for p in emptyfolders.user_folders()):
            names.append("OneDrive")
        return tr("Your folders: {names}", names=", ".join(names))

    def _status(self, text: str):
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _idle(self):
        self.bar.hide()
        self.stop_btn.hide()
        self.look_btn.setEnabled(True)
        self.pick_btn.setEnabled(True)
        self._ticked_changed()

    def _pick(self):
        folder = QFileDialog.getExistingDirectory(self, tr("Choose a folder"), str(Path.home()))
        if folder:
            self.start([Path(folder)])

    # ---- looking

    def start(self, roots: list[Path] | None, **options):
        """Looks in these folders (your own folders if None). options go to emptyfolders.find (tests)."""
        if self.job is not None:
            return
        if roots is None:
            roots = emptyfolders.user_folders()
            self.where.setText(self._your_folders())
        else:
            self.where.setText(ltr(str(roots[0])))
        self.roots = roots
        self.found = None
        self.tree.clear()
        self.notes.setText("")
        self.count.setText("")
        self._status(tr("Looking at folders…"))
        self.bar.show()
        self.stop_btn.show()
        self.look_btn.setEnabled(False)
        self.pick_btn.setEnabled(False)
        self.remove_btn.setEnabled(False)
        self.show_btn.setEnabled(False)
        cancel = self.cancel = threading.Event()
        self._cancels[:] = [cancel]
        self.job = work.run(lambda progress: emptyfolders.find(roots, cancel, lambda *a: progress(a), **options),
                            self._progress, self._found, name="corefold-empty")

    def _progress(self, args):
        if work.alive(self):
            self._status(tr("Looking at folders… {count}", count=f"{args[0]:,}"))

    def stop(self):
        self.cancel.set()

    def busy(self) -> bool:
        return self.job is not None

    def _found(self, found):
        if not work.alive(self):
            return
        self.job = None
        if isinstance(found, Exception):
            self._status(str(found))
            self._idle()
            return
        self.found = found
        self._fill()
        self._idle()

    def _fill(self):
        found = self.found
        self.tree.blockSignals(True)
        self.tree.clear()
        for item in found.items:
            it = QTreeWidgetItem([ltr(emptyfolders.shown_path(item)), inside_text(item),
                                  date_text(item.created, True)])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked)
            it.setToolTip(0, item.path)
            it.setData(0, Qt.UserRole, item)
            self.tree.addTopLevelItem(it)
        self.tree.blockSignals(False)
        self._status(tr("No empty folders found.") if not found.items else "")
        notes = []
        if found.cancelled:
            notes.append(tr("Stopped before the end. These are the empty folders found so far."))
        if found.capped:
            notes.append(tr("Stopped after {count} folders. Choose a smaller folder to look through the rest.",
                            count=f"{emptyfolders.MAX_FOLDERS:,}"))
        if found.refused:
            notes.append(tr("Corefold doesn't look for empty folders inside Windows, programs' folders or AppData."))
        if any(i.leftovers for i in found.items):
            notes.append(tr("Folders holding only files Windows adds by itself (desktop.ini, Thumbs.db) count as "
                            "empty."))
        if found.new_left == 1:
            notes.append(tr("1 empty folder made in the last day was left out: an app may be about to use it."))
        elif found.new_left:
            notes.append(tr("{count} empty folders made in the last day were left out: an app may be about to use "
                            "them.", count=f"{found.new_left:,}"))
        self.notes.setText("\n".join(notes))
        self._ticked_changed()

    # ---- the list

    def ticked(self) -> list[emptyfolders.EmptyFolder]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.checkState(0) == Qt.Checked:
                out.append(it.data(0, Qt.UserRole))
        return out

    def _ticked_changed(self):
        total = self.tree.topLevelItemCount()
        ticked = len(self.ticked()) if total else 0
        self.remove_btn.setEnabled(ticked > 0 and self.job is None)
        self.show_btn.setEnabled(self.tree.currentItem() is not None)
        self.tree.setVisible(total > 0)
        if not total:
            self.count.setText("")
        elif total == 1:
            self.count.setText(tr("1 empty folder found"))
        else:
            self.count.setText(tr("{count} empty folders found, {ticked} ticked", count=f"{total:,}",
                                  ticked=f"{ticked:,}"))

    def _show(self, it):
        if it is not None and it.data(0, Qt.UserRole) is not None:
            reveal_in_explorer(Path(it.data(0, Qt.UserRole).path))

    # ---- removing

    def remove(self):
        chosen = self.ticked()
        if not chosen or self.job is not None:
            return
        self.remove_btn.setEnabled(False)
        self.look_btn.setEnabled(False)
        self.pick_btn.setEnabled(False)
        self._status(tr("Sending them to the Recycle Bin…"))
        self.job = work.run(lambda progress: emptyfolders.remove(chosen), lambda _: None,
                            lambda gone: self._removed(chosen, gone), name="corefold-empty")

    def _removed(self, chosen, gone):
        if not work.alive(self):
            return
        self.job = None
        if isinstance(gone, Exception):
            self._status(str(gone))
            self._idle()
            return
        went = {id(g) for g in gone}
        self.found.items = [i for i in self.found.items if id(i) not in went]
        self._fill()
        self._idle()
        if gone:
            forget = getattr(self.page, "forget_maps", None)
            if forget is not None:
                forget()  # (the maps kept are out of date)
            title = tr("1 empty folder removed") if len(gone) == 1 else \
                tr("{count} empty folders removed", count=f"{len(gone):,}")
            self.c.say(title, tr("They're in the Recycle Bin, if you need them back."), done=True)
        left = len(chosen) - len(gone)
        if left:
            self._status(tr("{count} couldn't be removed: something was put in them, or they're in use.",
                                   count=f"{left:,}"))
        elif gone:
            self._status(tr("They're in the Recycle Bin, if you need them back."))

    def closeEvent(self, e):
        self.cancel.set()  # (looking stops; a removal already started finishes on its own)
        super().closeEvent(e)
