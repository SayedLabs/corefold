"""Corefold Cloud's page: your clouds side by side. OneDrive, Dropbox, Google Drive and iCloud folders kept on this
PC by their own apps show up by themselves; OneDrive and Dropbox can also be signed in to online. Open, download,
upload (or drop files and folders in), make folders, rename, delete, search everything, and copy from one cloud to
another by dragging."""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QMimeData, QRect, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QDrag, QFont, QFontMetrics, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, \
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar, QPushButton, QStyle, \
    QStyledItemDelegate, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from . import UPDATE_REPO, cloud, icons, work
from .core import human_size, short_name
from .i18n import ltr, tr
from .theme import round_menu
from .timefmt import date_text

if TYPE_CHECKING:
    from .app import Controller

DRAG_MIME = "application/x-corefold-cloud"
# where each service's own app is downloaded from (for "Add a cloud" when it isn't on this PC)
GET_APP = {"onedrive": "https://www.microsoft.com/microsoft-365/onedrive/download",
           "dropbox": "https://www.dropbox.com/install",
           "gdrive": "https://www.google.com/drive/download/",
           "icloud": "https://apps.microsoft.com/detail/9pktq5699m62"}
ACCOUNT = Qt.UserRole + 1          # (the account a search result belongs to)
TEMP_PREFIX = "corefold-cloud-"


def _ui_error(e) -> str:
    """What went wrong, in the person's language."""
    text = str(e)
    known = {
        "cancelled": tr("Cancelled."),
        "no internet connection, or the service didn't answer": tr("No internet connection, or the service didn't "
                                                                    "answer."),
        "there's no space left in this account": tr("There's no space left in this account."),
        "there's no space left on this PC": tr("There's no space left on this PC."),
        "it isn't there any more": tr("It isn't there any more."),
        "the service asks to wait a moment, try again soon": tr("The service asks to wait a moment. Try again "
                                                                 "soon."),
        "the service is having trouble right now, try again soon": tr("The service is having trouble right now. "
                                                                       "Try again soon."),
        "something with that name is already there": tr("Something with that name is already there."),
        "that name isn't allowed": tr("That name isn't allowed. Names can't use these: < > : \" / \\ | ? *"),
        "this account doesn't allow that": tr("This account doesn't allow that."),
        "it's open in another app, or Windows didn't allow it": tr("It's open in another app, or Windows didn't "
                                                                    "allow it."),
        "signing in took too long; try again": tr("Signing in took too long. Try again."),
        "signing in was cancelled": tr("Signing in was cancelled."),
        "the answer from the sign-in page didn't match; try again": tr("The answer from the sign-in page didn't "
                                                                        "match. Try again."),
        "the service didn't let Corefold in": tr("The service didn't let Corefold in. Try again."),
        "another program is using the port Dropbox signs in through; close it and try again":
            tr("Another program is using the port Dropbox signs in through. Close it and try again."),
        "OneDrive isn't set up in this copy of Corefold yet": tr("Signing in to {name} isn't set up in this copy of "
                                                                 "Corefold yet.", name="OneDrive"),
        "Dropbox isn't set up in this copy of Corefold yet": tr("Signing in to {name} isn't set up in this copy of "
                                                                "Corefold yet.", name="Dropbox"),
    }
    if isinstance(e, cloud.SignInNeeded):
        return tr("Signed out of this account. Sign in again to see its files.")
    if text in known:
        return known[text]
    if isinstance(e, cloud.CloudError):
        return tr("The service said: {text}", text=ltr(text))
    return tr("Something went wrong: {text}", text=ltr(text))


def clean_old_temp(days: float = 2):
    """Files opened from an online account are downloaded to a temporary folder; old ones are cleared away."""
    old = time.time() - days * 86400
    try:
        with os.scandir(tempfile.gettempdir()) as it:
            for d in it:
                try:
                    if d.name.startswith(TEMP_PREFIX) and d.is_dir() and d.stat().st_mtime < old:
                        shutil.rmtree(d.path, ignore_errors=True)
                except OSError:
                    continue
    except OSError:
        pass


def service_name(acc: cloud.Account) -> str:
    """OneDrive, Dropbox…, or for a folder added by hand that isn't any service's, the folder's name."""
    return cloud.SERVICES.get(acc.service) or acc.who


def space_text(acc: cloud.Account) -> str:
    """"3.2 GB free of 15 GB", "Uses 12 GB" (a folder: how much is left is only known online), or ""."""
    if acc.total:
        return tr("{free} free of {total}", free=human_size(max(0, acc.total - acc.used)), total=human_size(acc.total))
    if acc.used or acc.extra.get("measured"):
        return tr("Uses {size}", size=human_size(acc.used))
    return ""


class AccountRows(QStyledItemDelegate):
    """An account in the list: its service, whose it is, and its space (with a bar when the total is known)."""

    def __init__(self, page: "CloudPage"):
        super().__init__(page)
        self.page = page

    def sizeHint(self, option, index):
        small = QFont(option.font)
        small.setPointSizeF(max(7.0, option.font.pointSizeF() - 1))
        bold = QFont(option.font)
        bold.setWeight(QFont.DemiBold)
        return QSize(100, 16 + QFontMetrics(bold).height() + 2 * QFontMetrics(small).height() + 3 + 10)

    def paint(self, p: QPainter, opt, index):
        acc = index.data(Qt.UserRole)
        if acc is None:
            return
        t = self.page.c.theme
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        rtl = opt.direction == Qt.RightToLeft
        r = opt.rect.adjusted(2, 2, -2, -2)
        if opt.state & QStyle.State_Selected:
            soft = QColor(t.accent)
            soft.setAlpha(46 if t.dark else 34)
            p.setPen(Qt.NoPen)
            p.setBrush(soft)
            p.drawRoundedRect(r, 8, 8)
        elif opt.state & QStyle.State_MouseOver:
            p.setPen(Qt.NoPen)
            p.setBrush(t.hover or t.field)
            p.drawRoundedRect(r, 8, 8)
        icon = index.data(Qt.DecorationRole)
        s = 20
        x = r.right() - 10 - s if rtl else r.left() + 10
        if icon is not None:
            icon.paint(p, QRect(x, r.top() + 10, s, s))
        text = r.adjusted(10, 8, -10, -8)
        text = text.adjusted(0, 0, -(s + 8), 0) if rtl else text.adjusted(s + 8, 0, 0, 0)
        align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignVCenter
        f = QFont(opt.font)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(t.text)
        line = QFontMetrics(f).height()
        p.drawText(QRect(text.left(), text.top(), text.width(), line), align,
                   QFontMetrics(f).elidedText(service_name(acc), Qt.ElideRight, text.width()))
        small = QFont(opt.font)
        small.setPointSizeF(max(7.0, opt.font.pointSizeF() - 1))
        p.setFont(small)
        p.setPen(t.muted)
        fm = QFontMetrics(small)
        under = index.data(Qt.UserRole + 2) or ""
        p.drawText(QRect(text.left(), text.top() + line + 1, text.width(), fm.height()), align,
                   fm.elidedText(under, Qt.ElideRight, text.width()))
        said = space_text(acc)
        top = text.top() + line + fm.height() + 3
        if said:
            p.setPen(t.muted)
            p.drawText(QRect(text.left(), top, text.width(), fm.height()), align,
                       fm.elidedText(said, Qt.ElideRight, text.width()))
        if acc.total:  # (how full it is)
            bar = QRectF(text.left(), top + fm.height() + 4, text.width(), 4)
            p.setPen(Qt.NoPen)
            p.setBrush(t.border)
            p.drawRoundedRect(bar, 2, 2)
            share = min(1.0, acc.used / acc.total)
            full = QRectF(bar)
            full.setWidth(max(4.0, bar.width() * share))
            if rtl:
                full.moveRight(bar.right())
            p.setBrush(t.danger if share > 0.9 and t.danger else t.accent)
            p.drawRoundedRect(full, 2, 2)
        p.restore()


class Files(QTreeWidget):
    """The files of one folder (or what a search found). Files and folders dropped in are uploaded; items dragged
    out go to another account, or to File Explorer for folders on this PC."""

    def __init__(self, page: "CloudPage"):
        super().__init__()
        self.page = page
        self.setObjectName("history")
        self.setHeaderLabels([tr("Name"), tr("Size"), tr("Changed")])
        self.headerItem().setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
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

    def picked(self) -> tuple[cloud.Account | None, list]:
        """The selected items of one account (the first one picked, when a search found things in several)."""
        acc, out = None, []
        for it in self.selectedItems():
            e, a = it.data(0, Qt.UserRole), it.data(0, ACCOUNT) or self.page.account
            if e is None or a is None:
                continue
            if acc is None:
                acc = a
            if a.key == acc.key:
                out.append(e)
        return acc, out

    def startDrag(self, actions):
        acc, picked = self.picked()
        if not picked:
            return
        md = QMimeData()
        md.setData(DRAG_MIME, b"1")
        if acc.local:  # (File Explorer can take them straight from the folder)
            md.setUrls([QUrl.fromLocalFile(e.id) for e in picked])
        self.page.dragging = (acc, picked)
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.exec(Qt.CopyAction)
        self.page.dragging = None

    def _takes(self, md) -> bool:
        page = self.page
        if page.account is None or page.results is not None:
            return False
        if md.hasFormat(DRAG_MIME):  # from another account: copied into this folder
            return page.dragging is not None and page.dragging[0].key != page.account.key
        return md.hasUrls()

    def dragEnterEvent(self, e):
        if self._takes(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, e):
        md, page = e.mimeData(), self.page
        if not self._takes(md):
            e.ignore()
            return
        if md.hasFormat(DRAG_MIME):
            source, entries = page.dragging
            page.copy_to(source, entries, page.account, page._here())
        else:
            paths = [Path(u.toLocalFile()) for u in md.urls() if u.isLocalFile()]
            page.upload([p for p in paths if p.exists()])
        e.acceptProposedAction()


class Accounts(QListWidget):
    """The accounts. Dropping files from another account here copies them to the top of this one."""

    def __init__(self, page: "CloudPage"):
        super().__init__()
        self.page = page
        self.setObjectName("history")
        self.setAcceptDrops(True)

    def _target(self, e) -> cloud.Account | None:
        it = self.itemAt(e.position().toPoint())
        target = it.data(Qt.UserRole) if it is not None else None
        drag = self.page.dragging
        if e.mimeData().hasFormat(DRAG_MIME) and target is not None and drag is not None and \
                drag[0].key != target.key:
            return target
        return None

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(DRAG_MIME):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._target(e) is not None:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        target = self._target(e)
        if target is None:
            e.ignore()
            return
        source, entries = self.page.dragging
        self.page.copy_to(source, entries, target, None)
        e.acceptProposedAction()


class CloudPage(QWidget):
    def __init__(self, controller: "Controller", header):
        super().__init__()
        self.c = controller
        self.found: list[cloud.Account] | None = None   # folders on this PC (None: still looking)
        self.accounts: list[cloud.Account] = []
        self.account: cloud.Account | None = None
        self.trail: list[cloud.Entry] = []   # the folders opened, from the top
        self.results: list | None = None      # what a search found, [(account, entry)], while it's shown
        self.shown: list = []                 # what the list shows, [(account, entry)]
        self.dragging = None
        self.job = None                       # the transfer (or change) going on; others wait in the queue
        self.cancel = threading.Event()
        self.queue: list = []
        self.view = 0                         # (an answer for a folder that's no longer wanted is dropped)
        self.view_cancel = threading.Event()
        self.looking = False
        self.finder = self.lister = None
        self.download_to = str(Path.home() / "Downloads")
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
        self.account_list.setItemDelegate(AccountRows(self))
        self.account_list.setMouseTracking(True)
        self.add_btn = QPushButton(tr("Add a cloud"))
        self.add_btn.setObjectName("primary")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_menu = QMenu(self.add_btn)
        round_menu(self.add_menu)
        self.add_menu.aboutToShow.connect(self._fill_add_menu)
        self.add_btn.setMenu(self.add_menu)
        self.add_btn.setMinimumWidth(self.add_btn.sizeHint().width() + 18)  # (room for the menu's arrow)
        side.addWidget(self.add_btn)
        self.space_job = None
        self.again_btn = self._button(tr("Look again"), self.look_for_folders)
        self.again_btn.setToolTip(tr("Look again for cloud folders on this PC"))
        side.addWidget(self.again_btn)
        if UPDATE_REPO:  # what Cloud reaches and keeps, before signing in
            owner, _, name = UPDATE_REPO.partition("/")
            privacy = self._button(tr("Privacy"), lambda: QDesktopServices.openUrl(
                QUrl(f"https://{owner}.github.io/{name}/privacy.html#cloud")))
            privacy.setObjectName("flat")
            side.addWidget(privacy, 0, Qt.AlignLeft)
        side.addStretch()  # (takes the list's place while there are no accounts: the buttons stay on top)
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
        self.search.textChanged.connect(lambda t: self.end_search() if not t and self.results is not None else None)
        self.search.setMinimumWidth(210)
        self.search.setMaximumWidth(280)
        self.explorer_btn = self._button(tr("Open in File Explorer"), self._open_in_explorer)
        self.explorer_btn.setObjectName("flat")
        top.addWidget(self.up_btn)
        top.addWidget(self.where, 1)
        top.addWidget(self.explorer_btn)
        top.addWidget(self.search)
        main.addLayout(top)
        acts = QHBoxLayout()
        acts.setSpacing(6)
        self.upload_btn = self._button(tr("Upload…"), lambda: None)
        upload = QMenu(self.upload_btn)
        round_menu(upload)
        upload.addAction(tr("Files…"), self._pick_upload)
        upload.addAction(tr("A folder…"), self._pick_upload_folder)
        self.upload_btn.setMenu(upload)
        self.upload_btn.setMinimumWidth(self.upload_btn.sizeHint().width() + 18)  # (room for the menu's arrow)
        self.folder_btn = self._button(tr("New folder"), self.new_folder)
        self.refresh_btn = self._button(tr("Refresh"), self.refresh)
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
        self.status.setWordWrap(True)
        self.sign_again_btn = self._button(tr("Sign in again"), self._sign_in_again)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setMaximumWidth(220)
        self.stop_btn = self._button(tr("Cancel"), self.stop)
        status.addWidget(self.status, 1)
        status.addWidget(self.sign_again_btn)
        status.addWidget(self.bar)
        status.addWidget(self.stop_btn)
        main.addLayout(status)
        body.addLayout(main, 1)
        lay.addLayout(body, 1)
        # keys: Enter opens, Delete deletes, F2 renames, Backspace goes up, F5 looks again, Ctrl+F searches
        for keys, fn in ((Qt.Key_Return, self._open_current), (Qt.Key_Enter, self._open_current),
                         (QKeySequence.StandardKey.Delete, lambda: self._with_picked(self.delete)),
                         (Qt.Key_F2, self._rename_current), (Qt.Key_Backspace, self.go_back)):
            QShortcut(QKeySequence(keys), self.files, fn, context=Qt.WidgetShortcut)
        QShortcut(QKeySequence(Qt.Key_F5), self, self.refresh, context=Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence.StandardKey.Find, self, self.search.setFocus, context=Qt.WidgetWithChildrenShortcut)
        self.sign_again_btn.hide()
        self._busy(False)
        self.load_accounts()
        self.look_for_folders()

    def _working(self) -> bool:
        return self.job is not None and self.job.is_alive()

    def _button(self, text, fn) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(fn)
        return b

    # ---- accounts

    def look_for_folders(self):
        """Finds the cloud folders the services' own apps keep on this PC (off the window's thread: it reads the
        registry and asks the drives their names)."""
        if self.looking:
            return
        self.looking = True
        self._buttons()

        def go(_progress):
            from . import cloudlocal
            found = cloudlocal.find()
            clean_old_temp()
            return found

        def done(result):
            self.looking = False
            if not work.alive(self):
                return
            first = self.found is None  # (the first time: the list starts at the top, with what was found)
            self.found = [] if isinstance(result, Exception) else result
            self.load_accounts(None if first or self.account is None else self.account.key)
            self.measure()
        self.finder = work.run(go, lambda _: None, done, name="corefold-cloud-find")

    def load_accounts(self, pick: str | None = None):
        self.accounts = list(self.found or []) + cloud.load_accounts()
        self._fill_accounts()
        if not self.accounts:
            self.account = None
            self.trail = []
            self.results = None
            self._show([])
            self.space.setText("")
            if self.found is None:
                self.empty.setText(tr("Looking for your clouds on this PC…"))
            elif any(cloud.available(k) for k in cloud.ONLINE):
                self.empty.setText(tr("No cloud folders on this PC yet. Install OneDrive, Dropbox, Google Drive or "
                                      "iCloud and its files show up here, or sign in to OneDrive or Dropbox: you "
                                      "sign in on their own page, so your password never reaches Corefold."))
            else:
                self.empty.setText(tr("No cloud folders on this PC yet. Install OneDrive, Dropbox, Google Drive or "
                                      "iCloud and sign in to it, and its files show up here. Is your cloud's folder "
                                      "somewhere else? Add it with Add a cloud."))
            self._buttons()
            return
        keys = [a.key for a in self.accounts]
        row = keys.index(pick) if pick in keys else 0
        self.account_list.setCurrentRow(row)
        if self.account is None or self.account.key != keys[row] or pick is None:
            self._account_picked(self.account_list.item(row), None)
        else:  # (the same account as before: what's shown stays, with the new details)
            self.account = self.accounts[row]

    def _fill_accounts(self):
        self.account_list.blockSignals(True)
        current = self.account.key if self.account else None
        self.account_list.clear()
        online, here = icons.icon("cloud", self.c.theme.muted), icons.icon("hard_drive", self.c.theme.muted)
        local = [a.service for a in self.accounts if a.local]
        for acc in self.accounts:
            # (a folder on this PC: its email when the app says it, else "On this PC", or its folder's name when
            # one service has several)
            under = acc.email or (acc.who if acc.local and local.count(acc.service) > 1 else tr("On this PC"))
            if acc.service not in cloud.SERVICES:  # (a folder added by hand: its name is the title, its place under it)
                under = acc.local
            it = QListWidgetItem(here if acc.local else online, f"{service_name(acc)}\n{ltr(short_name(under, 34))}")
            it.setData(Qt.UserRole, acc)
            it.setData(Qt.UserRole + 2, ltr(short_name(under, 40)))
            if acc.local:
                tip = (tr("On this PC: {folder}. The {service} app keeps it in sync.", folder=ltr(acc.local),
                          service=cloud.SERVICES[acc.service]) if acc.service in cloud.SERVICES else
                       tr("On this PC: {folder}.", folder=ltr(acc.local)))
                if not acc.total and cloud.available(acc.service):
                    tip += "\n" + tr("Sign in to {service} to see how much space is left.",
                                     service=cloud.SERVICES[acc.service])
            else:
                tip = acc.name
                if acc.total:
                    tip += "\n" + tr("{used} of {total} used", used=human_size(acc.used), total=human_size(acc.total))
            it.setToolTip(tip.strip())
            self.account_list.addItem(it)
            if acc.key == current:
                self.account_list.setCurrentItem(it)
        self.account_list.blockSignals(False)

    def _account_picked(self, item, _old):
        if item is None:
            return
        self.account = item.data(Qt.UserRole)
        self.trail = []
        self.results = None
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._space_text()
        self.open_here()

    def _space_text(self):
        acc = self.account
        if acc is None:
            self.space.setText("")
        else:
            self.space.setText(space_text(acc))

    def _switch_to(self, acc: cloud.Account):
        """Shows this account in the list (a search result from another account was opened)."""
        for i in range(self.account_list.count()):
            if self.account_list.item(i).data(Qt.UserRole).key == acc.key:
                self.account_list.blockSignals(True)
                self.account_list.setCurrentRow(i)
                self.account_list.blockSignals(False)
                break
        self.account = acc
        self._space_text()

    def _account_menu(self, pos):
        it = self.account_list.itemAt(pos)
        if it is None or it.data(Qt.UserRole) is None:
            return
        acc = it.data(Qt.UserRole)
        menu = QMenu(self)
        round_menu(menu)
        if acc.local:
            menu.addAction(tr("Open in File Explorer"), lambda: self._explore(Path(acc.local)))
            if acc.extra.get("added"):
                menu.addAction(tr("Remove from the list"), lambda: self.remove_folder(acc))
        else:
            menu.addAction(tr("Sign out"), lambda: self.sign_out(acc))
        menu.exec(self.account_list.viewport().mapToGlobal(pos))

    def _fill_add_menu(self):
        """Sign in online (when this copy can), add any folder, or get a cloud's app that isn't on this PC."""
        menu = self.add_menu
        menu.clear()
        for key in cloud.ONLINE:
            if cloud.available(key):
                menu.addAction(icons.icon("cloud", self.c.theme.muted), tr("Sign in to {service}",
                               service=cloud.SERVICES[key]), lambda k=key: self.sign_in(k))
        menu.addAction(icons.icon("folder", self.c.theme.muted), tr("A cloud folder on this PC…"), self.pick_folder)
        here = {a.service for a in self.found or []} | {a.service for a in self.accounts if not a.local}
        missing = [k for k in GET_APP if k not in here]
        if missing:
            menu.addSeparator()
            get = menu.addMenu(tr("Get a cloud's app"))
            round_menu(get)
            for key in missing:
                get.addAction(cloud.SERVICES[key], lambda k=key: QDesktopServices.openUrl(QUrl(GET_APP[k])))
            menu.addAction(tr("Look again"), self.look_for_folders)

    def pick_folder(self):
        """Adds a folder Corefold didn't find by itself (a cloud app's folder somewhere else, a synced folder…)."""
        start = str(Path(self.account.local)) if self.account is not None and self.account.local else str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, tr("Add a cloud folder"), start)
        if not folder:
            return
        from . import cloudlocal
        try:
            acc = cloudlocal.add_folder(folder)
        except (OSError, cloud.CloudError) as e:
            self.status.setText(_ui_error(e))
            return
        self.found = [a for a in self.found or [] if a.key != acc.key] + [acc]
        self.load_accounts(acc.key)
        self.status.setText(tr("Added {name}.", name=ltr(acc.title)))
        self.measure()

    def remove_folder(self, acc: cloud.Account):
        from . import cloudlocal
        try:
            cloudlocal.remove_folder(acc.local)
        except OSError as e:
            self.status.setText(_ui_error(e))
            return
        self.found = [a for a in self.found or [] if a.key != acc.key]
        if self.account is not None and self.account.key == acc.key:
            self.account = None
        self.load_accounts()

    def measure(self):
        """Each account's space, off the window's thread: online ones ask their service, a folder on this PC adds up
        its files (a whole drive, like Google Drive's, tells its own size)."""
        if self.space_job is not None and self.space_job.is_alive():
            return
        accounts = list(self.accounts)

        def go(progress):
            from . import cloudlocal
            for acc in accounts:
                try:
                    if acc.local:
                        used, total = cloudlocal.space(acc)
                    else:
                        c = cloud.client(acc)
                        c.fill_profile()
                        cloud.remember_account(acc)
                        used, total = acc.used, acc.total
                except Exception:  # (no internet, signed out…: the space just isn't shown)
                    continue
                progress((acc.key, used, total))
            return None

        def got(what):
            if not work.alive(self):
                return
            key, used, total = what
            for acc in self.accounts + list(self.found or []):
                if acc.key == key:
                    acc.used, acc.total = used, total
                    acc.extra["measured"] = True
            self.account_list.viewport().update()
            self._space_text()
        self.space_job = work.run(go, got, lambda _: None, name="corefold-cloud-space")

    def sign_in(self, service: str):
        if self._working():
            self.status.setText(tr("Wait for what's going on to finish first."))
            return
        self.cancel = threading.Event()
        cancel = self.cancel

        def go(progress):
            return cloud.sign_in(service, lambda url: progress(("open", url)), cancel)
        self._busy(True, tr("Waiting for you to sign in, in your browser…"), spin=True, can_stop=True)
        self.job = work.run(go, self._sign_in_progress, self._signed_in)

    def _sign_in_again(self):
        if self.account is not None and not self.account.local:
            self.sign_in(self.account.service)

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
        self.sign_again_btn.hide()
        self.status.setText(tr("Signed in as {name}.", name=ltr(result.email)))
        self.account = None
        self.load_accounts(result.key)
        win = self.window()  # (back from the browser)
        win.showNormal() if win.isMinimized() else None
        win.raise_()
        win.activateWindow()

    def sign_out(self, acc: cloud.Account):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("Sign out?"))
        box.setText(tr("Corefold forgets its key to {name}. Your files stay where they are.", name=ltr(acc.title)))
        yes = box.addButton(tr("Sign out"), QMessageBox.AcceptRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        threading.Thread(target=cloud.client(acc).sign_out, daemon=True, name="corefold-cloud-signout").start()
        cloud.forget_account(acc.key)
        if self.account is not None and self.account.key == acc.key:
            self.account = None
        self.load_accounts()

    # ---- folders

    def _here(self) -> cloud.Entry | None:
        return self.trail[-1] if self.trail else None

    def open_here(self, quiet: bool = False):
        acc = self.account
        if acc is None:
            return
        self.results = None
        folder = self._here()
        self.view += 1
        view = self.view
        self.view_cancel.set()
        name = service_name(acc)
        self.where.setText(ltr(short_name(name + (folder.path if folder else ""), 80)))
        self.sign_again_btn.hide()
        opening = tr("Opening…")
        if not self._working() and not quiet:
            self.status.setText(opening)
        self._buttons()

        def done(result):
            if not work.alive(self) or view != self.view:
                return
            if self.status.text() == opening:
                self.status.setText("")
            if isinstance(result, cloud.SignInNeeded):
                self.status.setText(_ui_error(result))
                self.sign_again_btn.show()
                self._show([])
                return
            if isinstance(result, Exception):
                self.status.setText(_ui_error(result))
                if folder is not None and str(result) == "it isn't there any more":
                    self.trail.pop()  # (deleted somewhere else: back to the folder it was in)
                    self.open_here()
                return
            self._show([(acc, e) for e in result])
        # (kept: its answer only arrives while it's held; a newer folder replaces it, and the old answer is dropped)
        self.lister = work.run(lambda progress: cloud.client(acc).list(folder), lambda _: None, done,
                               name="corefold-cloud-list")

    def refresh(self, quiet: bool = False):
        if self.results is not None:
            self.search_all()
        elif self.account is not None:
            self.open_here(quiet)
        else:
            self.look_for_folders()

    def _show(self, found: list):
        """found: [(account, entry)]."""
        self.shown = found
        self.files.clear()
        theme = self.c.theme
        folder_icon, file_icon = icons.icon("folder", theme.muted), icons.icon("document", theme.muted)
        online_icon = icons.icon("cloud", theme.muted)
        searching = self.results is not None
        for acc, e in sorted(found, key=lambda f: (not f[1].folder, f[1].name.lower())):
            name = ltr(e.name)
            if searching:
                where = service_name(acc) + " · " + (e.path.rsplit("/", 1)[0] or "/")
                name = f"{name}   ·   {ltr(short_name(where, 60))}"
            it = QTreeWidgetItem([name, "" if e.folder else human_size(e.size),
                                  date_text(e.modified, True) if e.modified else ""])
            it.setIcon(0, folder_icon if e.folder else online_icon if e.online else file_icon)
            if e.online:
                it.setToolTip(0, tr("Kept online only. Opening it downloads it first."))
            it.setData(0, Qt.UserRole, e)
            it.setData(0, ACCOUNT, acc)
            it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            self.files.addTopLevelItem(it)
        nothing = not found
        self.files.setVisible(not nothing)
        self.empty.setVisible(nothing)
        if nothing and searching:
            self.empty.setText(tr("Nothing found."))
        elif nothing and self.account is not None:
            self.empty.setText(tr("This folder is empty. Drop files here to upload them."))
        self._buttons()

    def _buttons(self):
        have = self.account is not None
        any_account = bool(self.accounts)
        for w in (self.account_list, self.up_btn, self.where, self.search, self.upload_btn,
                  self.folder_btn, self.refresh_btn, self.space):
            w.setVisible(any_account)  # nothing to browse yet: only the buttons and the note
        self.again_btn.setVisible(not any_account or self.found == [])
        self.again_btn.setEnabled(not self.looking)
        self.explorer_btn.setVisible(have and bool(self.account.local))
        browsing = have and self.results is None
        for b in (self.upload_btn, self.folder_btn):
            b.setEnabled(browsing)
        self.refresh_btn.setEnabled(have)
        self.up_btn.setEnabled(have and (bool(self.trail) or self.results is not None))

    def go_back(self) -> bool:
        """Back (Alt+Left, the mouse's back button, Backspace): out of a search, then up a folder."""
        if self.results is not None:
            self.end_search()
            return True
        if self.trail:
            self.up()
            return True
        return False

    def up(self):
        if self.results is not None:
            self.end_search()
        elif self.trail:
            self.trail.pop()
            self.open_here()

    def end_search(self):
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.results = None
        self.open_here()

    def _item_of(self, item) -> tuple[cloud.Account | None, cloud.Entry | None]:
        if item is None:
            return None, None
        return item.data(0, ACCOUNT) or self.account, item.data(0, Qt.UserRole)

    def _open(self, acc: cloud.Account, e: cloud.Entry):
        if e.folder:
            if self.results is not None or acc.key != (self.account.key if self.account else None):
                self._switch_to(acc)  # (a folder a search found: opened in its own account, from its top)
                self.search.blockSignals(True)
                self.search.clear()
                self.search.blockSignals(False)
                self.trail = []
            self.trail.append(e)
            self.open_here()
        else:
            self.open_file(acc, e)

    def _double_click(self, item, _col):
        acc, e = self._item_of(item)
        if e is not None:
            self._open(acc, e)

    def _open_current(self):
        acc, e = self._item_of(self.files.currentItem())
        if e is not None:
            self._open(acc, e)

    def _rename_current(self):
        acc, picked = self.files.picked()
        if len(picked) == 1:
            self.rename(acc, picked[0])

    def _with_picked(self, fn):
        acc, picked = self.files.picked()
        if picked:
            fn(acc, picked)

    def _file_menu(self, pos):
        acc, picked = self.files.picked()
        if not picked:
            return
        menu = QMenu(self)
        round_menu(menu)
        one = picked[0] if len(picked) == 1 else None
        if one is not None:
            menu.addAction(tr("Open"), lambda: self._open(acc, one))
        if acc.local and one is not None:
            menu.addAction(tr("Show in folder"), lambda: self._explore(Path(one.id), select=True))
        menu.addAction(tr("Download…"), lambda: self.download(acc, picked))
        others = [a for a in self.accounts if a.key != acc.key]
        if others:
            sub = menu.addMenu(tr("Copy to"))
            round_menu(sub)
            for a in others:
                sub.addAction(ltr(a.title), lambda a=a: self.copy_to(acc, picked, a, None))
        if one is not None:
            menu.addAction(tr("Rename…"), lambda: self.rename(acc, one))
        menu.addSeparator()
        menu.addAction(tr("Delete"), lambda: self.delete(acc, picked))
        menu.exec(self.files.viewport().mapToGlobal(pos))

    def _explore(self, path: Path, select: bool = False):
        if select and os.name == "nt":
            from .winutil import reveal_in_explorer
            reveal_in_explorer(path)
        else:
            from .core import os_open
            os_open(path)

    def _open_in_explorer(self):
        if self.account is not None and self.account.local:
            here = self._here()
            self._explore(Path(here.id) if here else Path(self.account.local))

    # ---- doing things (one at a time; more wait their turn)

    def _busy(self, on: bool, text: str = "", spin: bool = False, can_stop: bool = True):
        self.bar.setVisible(on)
        self.stop_btn.setVisible(on and can_stop)
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
            self.bar.setValue(int(1000 * done / total) if total else 1000)
            text = f"{ltr(short_name(name, 50))}   ·   {human_size(done)} / {human_size(total)}"
            if self.queue:
                text += "   ·   " + tr("{count} more waiting", count=len(self.queue))
            self.status.setText(text)

    def stop(self):
        """Cancel: what's going on stops, and what was waiting is dropped."""
        self.queue.clear()
        self.cancel.set()

    def _run(self, fn, refresh: bool = True):
        """fn(progress, cancel) does the work off the window's thread and returns what to say when it's done."""
        if self._working():
            self.queue.append((fn, refresh))
            self.status.setText(tr("Waiting for what's going on to finish…") + "   ·   " +
                                tr("{count} more waiting", count=len(self.queue)))
            return
        self.cancel = threading.Event()
        cancel = self.cancel
        self._busy(True, tr("Working…"))

        def finished(result):
            self.job = None
            if not work.alive(self):
                return
            self._busy(False)
            self.status.setText(_ui_error(result) if isinstance(result, Exception) else (result or ""))
            if isinstance(result, cloud.SignInNeeded):
                self.sign_again_btn.show()
            if refresh:
                self.refresh(quiet=True)  # (what was done stays in the status line)
            if self.queue:
                self._run(*self.queue.pop(0))
        self.job = work.run(lambda progress: fn(progress, cancel), self._progress, finished, name="corefold-cloud")

    def open_file(self, acc: cloud.Account, e: cloud.Entry):
        from .core import os_open
        if acc.local:  # (opened where it is: the service's app keeps the changes in sync)
            try:
                os_open(Path(e.id))
                self.status.setText(tr("Opened {name}.", name=ltr(e.name)))
            except OSError as err:
                self.status.setText(_ui_error(err))
            return

        def go(progress, cancel):
            folder = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
            target = folder / cloud.local_name(e.name)
            cloud.client(acc).download(e, target, lambda d, t: progress((e.name, d, t)), cancel)
            os_open(target)
            return tr("Opened {name}. Changes to it stay on this PC until you upload it again.", name=ltr(e.name))
        self._run(go, refresh=False)

    def download(self, acc: cloud.Account, entries: list):
        folder = QFileDialog.getExistingDirectory(self, tr("Download to"), self.download_to)
        if not folder:
            return
        self.download_to = folder

        def go(progress, cancel):
            c = cloud.client(acc)
            count = sum(cloud.download_tree(c, e, Path(folder), progress, cancel) for e in entries)
            return tr("Downloaded 1 file to {folder}.", folder=ltr(folder)) if count == 1 else \
                tr("Downloaded {count} files to {folder}.", count=count, folder=ltr(folder))
        self._run(go, refresh=False)

    def _pick_upload(self):
        names, _ = QFileDialog.getOpenFileNames(self, tr("Upload"), str(Path.home()))
        if names:
            self.upload([Path(n) for n in names])

    def _pick_upload_folder(self):
        name = QFileDialog.getExistingDirectory(self, tr("Upload a folder"), str(Path.home()))
        if name:
            self.upload([Path(name)])

    def upload(self, paths: list[Path]):
        acc, folder = self.account, self._here()
        if acc is None or not paths:
            return

        def go(progress, cancel):
            c = cloud.client(acc)
            count = sum(cloud.upload_tree(c, p, folder, progress, cancel) for p in paths)
            return tr("Uploaded 1 file.") if count == 1 else tr("Uploaded {count} files.", count=count)
        self._run(go)

    def new_folder(self):
        if self.account is None:
            return
        name, ok = QInputDialog.getText(self, tr("New folder"), tr("Name:"))
        name = name.strip()
        if not ok or not name:
            return
        acc, folder = self.account, self._here()

        def go(progress, cancel):
            cloud.client(acc).new_folder(name, folder)
            return tr("Made the folder {name}.", name=ltr(name))
        self._run(go)

    def rename(self, acc: cloud.Account, e: cloud.Entry):
        name, ok = QInputDialog.getText(self, tr("Rename"), tr("New name:"), text=e.name)
        name = name.strip()
        if not ok or not name or name == e.name:
            return

        def go(progress, cancel):
            cloud.client(acc).rename(e, name)
            return tr("Renamed.")
        self._run(go)

    def delete(self, acc: cloud.Account, entries: list):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("Delete?"))
        what = ltr(entries[0].name) if len(entries) == 1 else tr("{count} items", count=len(entries))
        service = cloud.SERVICES.get(acc.service, "")
        box.setText((tr("{what} goes to the Recycle Bin, and {service} keeps it in its deleted files for a while.",
                        what=what, service=service) if service else tr("{what} goes to the Recycle Bin.", what=what))
                    if acc.local else
                    tr("{what} goes to the deleted files of {service}, where you can put it back for a while.",
                       what=what, service=service))
        yes = box.addButton(tr("Delete"), QMessageBox.DestructiveRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return

        def go(progress, cancel):
            c = cloud.client(acc)
            for e in entries:
                if cancel.is_set():
                    raise cloud.CloudError("cancelled")
                c.delete(e)
            return tr("Deleted.")
        self._run(go)

    def copy_to(self, source: cloud.Account, entries: list, target: cloud.Account, into: cloud.Entry | None):
        """Copies files and folders from one account to another (into a folder there, or its top). Online ones go
        through a temporary folder on this PC; folders on this PC are copied straight from where they are."""
        if not entries or source.key == target.key:
            return

        def go(progress, cancel):
            src, dst = cloud.client(source), cloud.client(target)
            count = 0
            for e in entries:
                if source.local:
                    count += cloud.upload_tree(dst, Path(e.id), into, progress, cancel)
                    continue
                folder = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
                try:
                    cloud.download_tree(src, e, folder, progress, cancel)
                    for p in folder.iterdir():
                        count += cloud.upload_tree(dst, p, into, progress, cancel)
                finally:
                    shutil.rmtree(folder, ignore_errors=True)
            return tr("Copied 1 file to {name}.", name=ltr(target.title)) if count == 1 else \
                tr("Copied {count} files to {name}.", count=count, name=ltr(target.title))
        self._run(go, refresh=self.account is not None and self.account.key == target.key)

    def search_all(self):
        text = self.search.text().strip()
        if not text:
            self.end_search()
            return
        if not self.accounts:
            return
        accounts = list(self.accounts)
        self.view += 1
        view = self.view
        self.view_cancel.set()
        self.view_cancel = cancel = threading.Event()
        self.results = []
        self.where.setText(tr("Found: {text}", text=ltr(text)))
        self.files.clear()
        self.files.hide()
        self.empty.show()
        self.empty.setText(tr("Searching…"))
        self.sign_again_btn.hide()
        self._buttons()

        def go(progress):
            found = []
            for a in accounts:
                try:
                    found += [(a, e) for e in cloud.client(a).search(text, cancel)]
                except cloud.CloudError:
                    if cancel.is_set():
                        raise
                    continue
            return found

        def done(result):
            if not work.alive(self) or view != self.view:
                return
            if isinstance(result, Exception):
                self.results = None
                self.status.setText(_ui_error(result))
                return
            self.results = result
            self._show(result)
            if not self._working():
                self.status.setText(tr("{count} found", count=len(result)))
        self.lister = work.run(go, lambda _: None, done, name="corefold-cloud-search")

    def apply_theme(self, theme):
        self._fill_accounts()
        self._show(self.shown)
