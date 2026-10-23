"""Corefold Cloud's page: your OneDrive and Dropbox accounts side by side. Open, download, upload
(or drop files in), make folders, rename, delete, search everything, and copy from one cloud to another."""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QMimeData, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QDrag
from PySide6.QtWidgets import QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, \
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar, QPushButton, QTreeWidget, \
    QTreeWidgetItem, QVBoxLayout, QWidget

from . import cloud, icons, work
from .core import human_size, short_name
from .i18n import ltr, tr
from .theme import round_menu
from .timefmt import date_text

if TYPE_CHECKING:
    from .app import Controller

DRAG_MIME = "application/x-corefold-cloud"


def _ui_error(e) -> str:
    text = str(e)
    known = {"cancelled": tr("Cancelled."),
             "no internet connection, or the service didn't answer": tr("No internet connection, or the service didn't "
                                                                         "answer."),
             "there's no space left in this account": tr("There's no space left in this account."),
             "it isn't there any more": tr("It isn't there any more."),
             "the service asks to wait a moment, try again soon": tr("The service asks to wait a moment. Try again "
                                                                     "soon.")}
    return known.get(text, text)


class Files(QTreeWidget):
    """The files of one folder. Files dropped in are uploaded; items dragged out can go to another account."""

    def __init__(self, page: "CloudPage"):
        super().__init__()
        self.page = page
        self.setObjectName("history")
        self.setHeaderLabels([tr("Name"), tr("Size"), tr("Changed")])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)

    def entries(self) -> list:
        return [it.data(0, Qt.UserRole) for it in self.selectedItems() if it.data(0, Qt.UserRole) is not None]

    def startDrag(self, actions):
        picked = self.entries()
        if not picked:
            return
        md = QMimeData()
        md.setData(DRAG_MIME, b"1")
        self.page.dragging = (self.page.account, picked)
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.exec(Qt.CopyAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and self.page.account is not None:
            e.acceptProposedAction()
        else:
            e.ignore()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        files = [p for p in paths if p.is_file()]
        if files:
            self.page.upload(files)
        e.acceptProposedAction()


class Accounts(QListWidget):
    """The accounts. Dropping files from another account here copies them to this one."""

    def __init__(self, page: "CloudPage"):
        super().__init__()
        self.page = page
        self.setObjectName("history")
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(DRAG_MIME):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        it = self.itemAt(e.position().toPoint())
        if e.mimeData().hasFormat(DRAG_MIME) and it is not None and it.data(Qt.UserRole) is not None:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        it = self.itemAt(e.position().toPoint())
        if it is None or self.page.dragging is None:
            return
        target = it.data(Qt.UserRole)
        source, entries = self.page.dragging
        self.page.dragging = None
        if target is not None and source is not None and target.key != source.key:
            self.page.copy_to(entries, target)
        e.acceptProposedAction()


class CloudPage(QWidget):
    def __init__(self, controller: "Controller", header):
        super().__init__()
        self.c = controller
        self.accounts: list[cloud.Account] = []
        self.account: cloud.Account | None = None
        self.trail: list[cloud.Entry] = []   # the folders opened, from the top
        self.dragging = None
        self.job = None
        self.cancel = threading.Event()
        self.queue: list = []                # transfers waiting
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(12)
        lay.addLayout(header)
        body = QHBoxLayout()
        body.setSpacing(12)
        side = QVBoxLayout()
        side.setSpacing(8)
        self.account_list = Accounts(self)
        self.account_list.setFixedWidth(250)
        self.account_list.currentItemChanged.connect(self._account_picked)
        self.account_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.account_list.customContextMenuRequested.connect(self._account_menu)
        side.addWidget(self.account_list, 1)
        add = QPushButton(tr("Add an account"))
        add.setObjectName("primary")
        add.setCursor(Qt.PointingHandCursor)
        menu = QMenu(add)
        round_menu(menu)
        for key, name in cloud.SERVICES.items():
            act = menu.addAction(name, lambda k=key: self.sign_in(k))
            act.setEnabled(cloud.available(key))
        add.setMenu(menu)
        side.addWidget(add)
        body.addLayout(side)
        main = QVBoxLayout()
        main.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.up_btn = self._button(tr("Up"), self.up)
        self.where = QLabel()
        self.where.setObjectName("h2")
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search all your clouds"))
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self.search_all)
        self.search.setMaximumWidth(260)
        top.addWidget(self.up_btn)
        top.addWidget(self.where, 1)
        top.addWidget(self.search)
        main.addLayout(top)
        acts = QHBoxLayout()
        acts.setSpacing(6)
        self.upload_btn = self._button(tr("Upload…"), self._pick_upload)
        self.folder_btn = self._button(tr("New folder"), self.new_folder)
        self.refresh_btn = self._button(tr("Refresh"), self.open_here)
        for b in (self.upload_btn, self.folder_btn, self.refresh_btn):
            acts.addWidget(b)
        acts.addStretch()
        self.space = QLabel()
        self.space.setObjectName("muted")
        acts.addWidget(self.space)
        main.addLayout(acts)
        self.files = Files(self)
        self.files.itemDoubleClicked.connect(self._double_click)
        self.files.setContextMenuPolicy(Qt.CustomContextMenu)
        self.files.customContextMenuRequested.connect(self._file_menu)
        main.addWidget(self.files, 1)
        self.empty = QLabel()
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        main.addWidget(self.empty, 1)
        status = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setMaximumWidth(220)
        self.stop_btn = self._button(tr("Cancel"), lambda: self.cancel.set())
        status.addWidget(self.status, 1)
        status.addWidget(self.bar)
        status.addWidget(self.stop_btn)
        main.addLayout(status)
        body.addLayout(main, 1)
        lay.addLayout(body, 1)
        self._busy(False)
        self.load_accounts()

    def _working(self) -> bool:
        return self.job is not None and self.job.is_alive()

    def _button(self, text, fn) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(fn)
        return b

    # ---- accounts

    def load_accounts(self, pick: str | None = None):
        self.accounts = cloud.load_accounts()
        self.account_list.blockSignals(True)
        self.account_list.clear()
        icon = icons.icon("cloud", self.c.theme.muted)
        for acc in self.accounts:
            it = QListWidgetItem(icon, f"{cloud.SERVICES.get(acc.service, acc.service)}\n{ltr(acc.email)}")
            it.setData(Qt.UserRole, acc)
            tip = acc.name
            if acc.total:
                tip += "\n" + tr("{used} of {total} used", used=human_size(acc.used), total=human_size(acc.total))
            it.setToolTip(tip.strip())
            self.account_list.addItem(it)
        self.account_list.blockSignals(False)
        if not self.accounts:
            self.account = None
            self.trail = []
            self._show([])
            ready = any(cloud.available(k) for k in cloud.SERVICES)
            self.empty.setText(tr("Add your OneDrive or Dropbox to see your files here. You sign in on their own page: "
                                  "your password never reaches Corefold.") if ready else
                               tr("Cloud accounts aren't set up in this copy of Corefold yet."))
            self._buttons()
            return
        keys = [a.key for a in self.accounts]
        row = keys.index(pick) if pick in keys else 0
        self.account_list.setCurrentRow(row)
        self._account_picked(self.account_list.item(row), None)

    def _account_picked(self, item, _old):
        if item is None:
            return
        self.account = item.data(Qt.UserRole)
        self.trail = []
        acc = self.account
        self.space.setText(tr("{used} of {total} used", used=human_size(acc.used), total=human_size(acc.total))
                           if acc.total else "")
        self.open_here()

    def _account_menu(self, pos):
        it = self.account_list.itemAt(pos)
        if it is None or it.data(Qt.UserRole) is None:
            return
        acc = it.data(Qt.UserRole)
        menu = QMenu(self)
        round_menu(menu)
        menu.addAction(tr("Sign out"), lambda: self.sign_out(acc))
        menu.exec(self.account_list.viewport().mapToGlobal(pos))

    def sign_in(self, service: str):
        if self._working():
            return
        self.cancel = threading.Event()
        cancel = self.cancel

        def go(progress):
            return cloud.sign_in(service, lambda url: progress(("open", url)), cancel)
        self._busy(True, tr("Waiting for you to sign in, in your browser…"))
        self.job = work.run(go, self._sign_in_progress, self._signed_in)

    def _sign_in_progress(self, what):
        if isinstance(what, tuple) and what[0] == "open":
            QDesktopServices.openUrl(QUrl(what[1]))

    def _signed_in(self, result):
        self.job = None
        if not work.alive(self):
            return
        self._busy(False)
        if isinstance(result, Exception):
            self.status.setText(_ui_error(result))
            return
        self.status.setText(tr("Signed in as {name}.", name=ltr(result.email)))
        self.load_accounts(result.key)

    def sign_out(self, acc: cloud.Account):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("Sign out?"))
        box.setText(tr("Corefold forgets its key to {name}. Your files stay where they are.", name=ltr(acc.title)))
        yes = box.addButton(tr("Sign out"), QMessageBox.AcceptRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            cloud.forget_account(acc.key)
            self.load_accounts()

    # ---- folders

    def _here(self) -> cloud.Entry | None:
        return self.trail[-1] if self.trail else None

    def open_here(self):
        acc = self.account
        if acc is None or self._working():
            return
        folder = self._here()
        self.where.setText(ltr(short_name(folder.path if folder else "/", 80)))
        self._busy(True, tr("Opening…"), spin=True)
        self.job = work.run(lambda progress: cloud.client(acc).list(folder), lambda _: None, self._listed)

    def _listed(self, result):
        self.job = None
        if not work.alive(self):
            return
        self._busy(False)
        if isinstance(result, cloud.SignInNeeded):
            self.status.setText(tr("Signed out of this account. Sign in again to see its files."))
            self._show([])
            return
        if isinstance(result, Exception):
            self.status.setText(_ui_error(result))
            return
        self._show(result)

    def _show(self, entries: list, with_account: bool = False):
        self.files.clear()
        folder_icon = icons.icon("folder", self.c.theme.muted)
        file_icon = icons.icon("document", self.c.theme.muted)
        for e in sorted(entries, key=lambda e: (not e.folder, e.name.lower())):
            name = ltr(e.name) if not with_account else f"{ltr(e.name)}   ·   {ltr(short_name(e.path, 50))}"
            it = QTreeWidgetItem([name, "" if e.folder else human_size(e.size),
                                  date_text(e.modified, True) if e.modified else ""])
            it.setIcon(0, folder_icon if e.folder else file_icon)
            it.setData(0, Qt.UserRole, e)
            it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            self.files.addTopLevelItem(it)
        nothing = not entries
        self.files.setVisible(not nothing)
        self.empty.setVisible(nothing)
        if nothing and self.account is not None:
            self.empty.setText(tr("This folder is empty. Drop files here to upload them."))
        self._buttons()

    def _buttons(self):
        have = self.account is not None
        for b in (self.upload_btn, self.folder_btn, self.refresh_btn):
            b.setEnabled(have and not self._working())
        self.up_btn.setEnabled(have and bool(self.trail))

    def up(self):
        if self.trail:
            self.trail.pop()
            self.open_here()

    def _double_click(self, item, _col):
        e = item.data(0, Qt.UserRole)
        if e is None:
            return
        if e.folder:
            self.trail.append(e)
            self.open_here()
        else:
            self.open_file(e)

    def _file_menu(self, pos):
        picked = self.files.entries()
        if not picked:
            return
        menu = QMenu(self)
        round_menu(menu)
        one = picked[0] if len(picked) == 1 else None
        if one is not None and not one.folder:
            menu.addAction(tr("Open"), lambda: self.open_file(one))
        if one is not None and one.folder:
            menu.addAction(tr("Open"), lambda: (self.trail.append(one), self.open_here()))
        files = [e for e in picked if not e.folder]
        if files:
            menu.addAction(tr("Download…"), lambda: self.download(files))
        others = [a for a in self.accounts if self.account is not None and a.key != self.account.key]
        if files and others:
            sub = menu.addMenu(tr("Copy to"))
            round_menu(sub)
            for a in others:
                sub.addAction(ltr(a.title), lambda a=a: self.copy_to(files, a))
        if one is not None:
            menu.addAction(tr("Rename…"), lambda: self.rename(one))
        menu.addSeparator()
        menu.addAction(tr("Delete"), lambda: self.delete(picked))
        menu.exec(self.files.viewport().mapToGlobal(pos))

    # ---- doing things (one at a time)

    def _busy(self, on: bool, text: str = "", spin: bool = False):
        self.bar.setVisible(on)
        self.stop_btn.setVisible(on and not spin)
        if spin:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(0)
        if text or on:
            self.status.setText(text)
        self._buttons()

    def _progress(self, what):
        if isinstance(what, tuple) and len(what) == 3:
            name, done, total = what
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * done / total) if total else 0)
            self.status.setText(f"{ltr(short_name(name, 50))}   ·   {human_size(done)} / {human_size(total)}")

    def _run(self, fn, done_text: str, refresh: bool = True):
        if self._working() or self.account is None:
            return False
        self.cancel = threading.Event()
        self._busy(True, tr("Working…"))

        def finished(result):
            self.job = None
            if not work.alive(self):
                return
            self._busy(False)
            if isinstance(result, Exception):
                self.status.setText(_ui_error(result))
            else:
                self.status.setText(done_text if result is None else result)
            if refresh:
                self.open_here()
        self.job = work.run(fn, self._progress, finished)
        return True

    def open_file(self, e: cloud.Entry):
        acc = self.account
        folder = Path(tempfile.mkdtemp(prefix="corefold-cloud-"))
        target = folder / e.name

        def go(progress):
            cloud.client(acc).download(e, target, lambda d, t: progress((e.name, d, t)), self.cancel)
            os.startfile(target) if os.name == "nt" else QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
            return tr("Opened {name}. Changes to it stay on this PC until you upload it again.", name=ltr(e.name))
        self._run(go, "", refresh=False)

    def download(self, entries: list):
        folder = QFileDialog.getExistingDirectory(self, tr("Download to"), str(Path.home() / "Downloads"))
        if not folder:
            return
        acc = self.account

        def go(progress):
            from .core import unique_path
            for e in entries:
                target = unique_path(Path(folder) / e.name)
                cloud.client(acc).download(e, target, lambda d, t, n=e.name: progress((n, d, t)), self.cancel)
            return tr("Downloaded {count} files to {folder}.", count=len(entries), folder=ltr(folder))
        self._run(go, "", refresh=False)

    def _pick_upload(self):
        names, _ = QFileDialog.getOpenFileNames(self, tr("Upload"), str(Path.home()))
        if names:
            self.upload([Path(n) for n in names])

    def upload(self, files: list[Path]):
        acc, folder = self.account, self._here()

        def go(progress):
            for f in files:
                cloud.client(acc).upload(f, folder, lambda d, t, n=f.name: progress((n, d, t)), self.cancel)
            return tr("Uploaded {count} files.", count=len(files))
        self._run(go, "")

    def new_folder(self):
        name, ok = QInputDialog.getText(self, tr("New folder"), tr("Name:"))
        name = name.strip()
        if not ok or not name:
            return
        acc, folder = self.account, self._here()
        self._run(lambda progress: cloud.client(acc).new_folder(name, folder) and None,
                  tr("Made the folder {name}.", name=ltr(name)))

    def rename(self, e: cloud.Entry):
        name, ok = QInputDialog.getText(self, tr("Rename"), tr("New name:"), text=e.name)
        name = name.strip()
        if not ok or not name or name == e.name:
            return
        acc = self.account
        self._run(lambda progress: cloud.client(acc).rename(e, name) and None, tr("Renamed."))

    def delete(self, entries: list):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("Delete?"))
        what = ltr(entries[0].name) if len(entries) == 1 else tr("{count} items", count=len(entries))
        box.setText(tr("{what} goes to the deleted files of {service}, where you can put it back for a while.",
                       what=what, service=cloud.SERVICES.get(self.account.service, "")))
        yes = box.addButton(tr("Delete"), QMessageBox.DestructiveRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        acc = self.account

        def go(progress):
            for e in entries:
                cloud.client(acc).delete(e)
            return None
        self._run(go, tr("Deleted."))

    def copy_to(self, entries: list, target: cloud.Account):
        """Copies files from this account to the top of another one (through a temporary file here)."""
        files = [e for e in entries if not e.folder]
        if not files:
            self.status.setText(tr("Only files can be copied to another cloud for now."))
            return
        source = self.account

        def go(progress):
            folder = Path(tempfile.mkdtemp(prefix="corefold-cloud-"))
            try:
                for e in files:
                    tmp = folder / e.name
                    cloud.client(source).download(e, tmp, lambda d, t, n=e.name: progress((n, d, 2 * t)), self.cancel)
                    cloud.client(target).upload(tmp, None, lambda d, t, n=e.name: progress((n, t + d, 2 * t)),
                                                self.cancel)
                    tmp.unlink(missing_ok=True)
            finally:
                import shutil
                shutil.rmtree(folder, ignore_errors=True)
            return tr("Copied {count} files to {name}.", count=len(files), name=ltr(target.title))
        self._run(go, "", refresh=False)

    def search_all(self):
        text = self.search.text().strip()
        if not text:
            self.open_here()
            return
        if self._working() or not self.accounts:
            return
        accounts = list(self.accounts)

        def go(progress):
            found = []
            for a in accounts:
                try:
                    found += [(a, e) for e in cloud.client(a).search(text)]
                except cloud.CloudError:
                    continue
            return found
        self._busy(True, tr("Searching…"), spin=True)

        def done(result):
            self.job = None
            if not work.alive(self):
                return
            self._busy(False)
            if isinstance(result, Exception):
                self.status.setText(_ui_error(result))
                return
            here = [e for a, e in result if self.account is not None and a.key == self.account.key]
            self._show(here, with_account=True)
            self.where.setText(tr("Found: {text}", text=ltr(text)))
            elsewhere = len(result) - len(here)
            self.status.setText(tr("{count} found", count=len(result)) +
                                ("   ·   " + tr("{count} in your other accounts", count=elsewhere) if elsewhere else ""))
        self.job = work.run(go, lambda _: None, done)

    def apply_theme(self, theme):
        self.load_accounts(self.account.key if self.account else None)
