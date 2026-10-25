"""Corefold Cloud's page: your clouds side by side. OneDrive, Dropbox, Google Drive and iCloud folders kept on this
PC by their own apps show up by themselves; Google Drive, OneDrive and Dropbox can also be signed in to online, with
nothing of theirs installed, and as many accounts as you like (several Google accounts at once too). Open, download,
upload (or drop files and folders in), make folders, rename, delete, search everything, and copy from one cloud to
another by dragging."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QMimeData, QRect, QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QDrag, QFont, QFontMetrics, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, \
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar, QPushButton, QStyle, \
    QStyledItemDelegate, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from . import UPDATE_REPO, cloud, icons, work
from .atomic.widgets import IconButton
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
# each service's own website, for "Open in the browser"
WEBSITE = {"gdrive": "https://drive.google.com/", "onedrive": "https://onedrive.live.com/",
           "dropbox": "https://www.dropbox.com/home"}
ACCOUNT = Qt.UserRole + 1          # (the account a search result belongs to)
TEMP_PREFIX = "corefold-cloud-"
# files opened from an online account are changed here: not in Windows' temporary folder, which Storage Sense and
# Disk Cleanup empty by themselves (changes not uploaded yet would go with it); removing Corefold leaves it too
OPENED = cloud.LOG_DIR / "Opened cloud files"
PREVIEW_MAX = 300 * 1024 * 1024    # (an online file bigger than this isn't downloaded just to look at it)
KEPT = 30                          # transfers kept in the list


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
        cloud.CUT_OFF: tr("The download was cut off before the end, so it wasn't kept. Try again."),
        cloud.NOT_THE_SAME: tr("The downloaded file isn't the same as the one in the account, so it wasn't kept. "
                               "Try again."),
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
        cloud.TOO_BIG_TO_EXPORT: tr("Google can't turn something this big into a file. Download it from the Google "
                                    "Drive website."),
        cloud.WEB_ONLY: tr("This one only opens on the service's website."),
        cloud.NO_FILES_ALLOWED: tr("Corefold wasn't allowed to see your files. Sign in again and leave the box that "
                                   "lets it see, edit and delete your files ticked."),
        cloud.NOT_SENT_WHOLE: tr("The upload didn't arrive whole, so it was taken away again. Your file on this PC "
                                 "is untouched. Try again."),
    }
    for name in cloud.SERVICES.values():
        known[f"{name} isn't set up in this copy of Corefold yet"] = tr(
            "Signing in to {name} isn't set up in this copy of Corefold yet.", name=name)
    if isinstance(e, cloud.SignInNeeded):
        return tr("Signed out of this account. Sign in again to see its files.")
    if text in known:
        return known[text]
    if isinstance(e, cloud.CloudError):
        return tr("The service said: {text}", text=ltr(text))
    return tr("Something went wrong: {text}", text=ltr(text))


def _left_out(c: cloud.Client) -> str:
    """What a download or copy left out because it only opens on the service's website (Google Forms, Sites…)."""
    if not c.skipped:
        return ""
    return " " + tr("{count} left out: they only open on the website ({names}).", count=len(c.skipped),
                    names=ltr(", ".join(short_name(n, 30) for n in c.skipped[:3]) +
                              ("…" if len(c.skipped) > 3 else "")))


OPENED_NOTE = ".corefold-opened"  # (in each such folder: the file's size and date right after it came down)


def _untouched(folder: str) -> bool | None:
    """The file opened from an online account is still exactly as it came down (None: not known)."""
    try:
        with open(os.path.join(folder, OPENED_NOTE), encoding="utf-8") as fh:
            kept = json.load(fh)
        st = os.stat(os.path.join(folder, kept["name"]))
        if set(os.listdir(folder)) - {kept["name"], OPENED_NOTE}:  # (saved as something else next to it)
            return False
        return st.st_size == kept["size"] and st.st_mtime_ns == kept["mtime"]
    except FileNotFoundError:
        return False if os.path.exists(os.path.join(folder, OPENED_NOTE)) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def note_opened(target: Path):
    try:
        st = target.stat()
        (target.parent / OPENED_NOTE).write_text(
            json.dumps({"name": target.name, "size": st.st_size, "mtime": st.st_mtime_ns}), encoding="utf-8")
    except OSError:
        pass


def clean_old_temp(days: float = 2):
    """Files opened from an online account are downloaded to a temporary folder; old ones are cleared away.
    One that was changed is kept: "changes to it stay on this PC until you upload it again"."""
    old = time.time() - days * 86400
    for base in (tempfile.gettempdir(), str(OPENED)):
        _clean_old(base, old)


def _clean_old(base: str, old: float):
    try:
        with os.scandir(base) as it:
            for d in it:
                try:
                    if not (d.name.startswith(TEMP_PREFIX) and d.is_dir() and d.stat().st_mtime < old):
                        continue
                    untouched = _untouched(d.path)
                    if untouched is None:  # (opened before the note was kept: only when nothing changed in a month)
                        newest = max((e.stat().st_mtime for e in os.scandir(d.path)), default=0)
                        untouched = newest < time.time() - 30 * 86400
                    if untouched:
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


def _natural(text: str) -> list:
    """Sorts "file 2" before "file 10"."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"(\d+)", text.casefold()) if p]


FIRST_ROWS = 1500  # a folder with more than this shows these at once, and the rest a moment later
MORE_ROWS = 1500


def _sort_key(e, col: int):
    """What a file is sorted by (folders first is done apart: it holds whichever way round the rest goes)."""
    if col == 1:
        return (0 if e.folder else e.size, _natural(e.name))
    if col == 2:
        return (e.modified or 0, _natural(e.name))
    return _natural(e.name)


def _sorted(found: list, col: int, down: bool) -> list:
    """found ([(account, entry)]) in the order the list shows, sorted in Python with each key made once.
    (Qt's own sort asked Python about each comparison: 20,000 files froze the window for seconds.)"""
    out = sorted(found, key=lambda ae: _sort_key(ae[1], col), reverse=down)
    out.sort(key=lambda ae: not ae[1].folder)
    out.sort(key=lambda ae: ae[1].kind != "virtual")  # (Shared with me, Shared drives: always on top)
    return out


class Row(QTreeWidgetItem):
    """A file or folder in the list. Sorts by the column clicked; folders always come first."""


class Crumbs(QWidget):
    """Where you are: the cloud, then each folder opened, as buttons; a click goes back there."""

    def __init__(self, page: "CloudPage"):
        super().__init__()
        self.page = page
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.parts: list[str] = []

    def text(self) -> str:
        return " › ".join(self.parts)

    def _clear(self):
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()

    def show_text(self, text: str):
        """Just words (what a search found)."""
        self._clear()
        self.parts = [text]
        label = QLabel(text)
        label.setObjectName("h2")
        self.lay.addWidget(label)
        self.lay.addStretch()

    def show_trail(self, top: str, names: list[str]):
        self._clear()
        self.parts = [top, *names]
        from .i18n import is_rtl
        arrow = "‹" if is_rtl() else "›"
        steps = list(enumerate(self.parts))
        if len(steps) > 5:  # (a deep folder: the cloud, "…", and the last three)
            steps = steps[:1] + [(-1, "…")] + steps[-3:]
        for n, (i, name) in enumerate(steps):
            if n:
                sep = QLabel(arrow)
                sep.setObjectName("muted")
                sep.setContentsMargins(2, 0, 2, 0)
                self.lay.addWidget(sep)
            last = i == len(self.parts) - 1
            b = QPushButton(ltr(short_name(name, 40)))
            b.setObjectName("crumbHere" if last else "crumb")
            b.setToolTip(ltr(name) if len(name) > 40 else "")
            if i < 0:
                b.setEnabled(False)
            elif not last:
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, i=i: self.page.go_to(i))
            self.lay.addWidget(b)
        self.lay.addStretch()


class Transfer:
    """One thing Cloud does (an upload, a download, a copy…), for the list of transfers."""

    def __init__(self, what: str, fn, refresh: bool):
        self.what, self.fn, self.refresh = what, fn, refresh
        self.state = "waiting"   # waiting, working, done, failed, cancelled
        self.note = ""
        self.done = self.total = 0
        self.item: QListWidgetItem | None = None


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
        self.sort_state = (0, False)  # (column, the other way round): sorted by Corefold, see _sorted
        self.header().setSortIndicator(0, Qt.AscendingOrder)
        self.header().setSortIndicatorShown(True)
        self.header().setSectionsClickable(True)
        self.header().sectionClicked.connect(self._sort_by)
        self.headerItem().setToolTip(0, tr("Click a column's name to sort by it"))
        # Select all in a folder of thousands: the rows still to come are selected as they come (making all
        # 20,000 at once held the window up for a second), until the selection is changed by hand
        self.all_picked = False
        self._picking = False
        self.itemSelectionChanged.connect(self._selection_changed)

    def _selection_changed(self):
        if not self._picking:
            self.all_picked = False

    def pick_rows(self, first: int, last: int):
        """Selects rows first..last (ones just added, while everything is selected) in one go."""
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        model = self.model()
        self._picking = True
        try:
            self.selectionModel().select(QItemSelection(model.index(first, 0), model.index(last, 0)),
                                         QItemSelectionModel.Select | QItemSelectionModel.Rows)
        finally:
            self._picking = False

    def _sort_by(self, col: int):
        old, down = self.sort_state
        down = not down if col == old else False
        self.sort_state = (col, down)
        self.header().setSortIndicator(col, Qt.DescendingOrder if down else Qt.AscendingOrder)
        self.page.resort()

    def sortByColumn(self, col: int, order):
        """(the same sort as a click on the heading)"""
        down = order == Qt.DescendingOrder
        self.sort_state = (col, down)
        self.header().setSortIndicator(col, order)
        self.page.resort()

    def selectAll(self):
        self._picking = True
        try:
            super().selectAll()
        finally:
            self._picking = False
        self.all_picked = bool(self.page._later)  # (every file, not just the rows already shown)

    def picked(self) -> tuple[cloud.Account | None, list]:
        """The selected items of one account (the first one picked, when a search found things in several)."""
        if self.all_picked:  # (something is done with all of them: the rows still to come are made now)
            self.page.finish_rows()
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
        here = page._here()
        if here is not None and here.kind == "virtual":  # (Shared with me…: nothing can be put there)
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
        self._later: list = []                # (the rest of a folder of thousands, shown a moment later)
        self._round = 0
        self.dragging = None
        self.job = None                       # the transfer (or change) going on; others wait in the queue
        self.current: Transfer | None = None
        self.peeker = None                    # the Quick look window, once a file was previewed
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
        self.account_list.setFixedWidth(230)
        self.account_list.currentItemChanged.connect(self._account_picked)
        self.account_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.account_list.customContextMenuRequested.connect(self._account_menu)
        side.addWidget(self.account_list, 1)
        self.account_list.setItemDelegate(AccountRows(self))
        self.account_list.setMouseTracking(True)
        self.add_btn = QPushButton(tr("Add a cloud"), self)  # (shown before it's laid out)
        self.add_btn.setObjectName("primary")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_menu = QMenu(self.add_btn)
        round_menu(self.add_menu)
        self.add_menu.aboutToShow.connect(self._fill_add_menu)
        self.add_btn.setMenu(self.add_menu)
        self.add_btn.setMinimumWidth(self.add_btn.sizeHint().width() + 18)  # (room for the menu's arrow)
        side.addWidget(self.add_btn)
        self.privacy_btn = None
        self.space_job = None
        self.again_btn = self._button(tr("Look again"), self.look_for_folders)
        self.again_btn.setToolTip(tr("Look again for cloud folders on this PC"))
        side.addWidget(self.again_btn)
        if UPDATE_REPO:  # what Cloud reaches and keeps, before signing in
            privacy = self._button(tr("Privacy"), self._privacy)
            privacy.setObjectName("flat")
            side.addWidget(privacy, 0, Qt.AlignLeft)
            self.privacy_btn = privacy
        side.addStretch()  # (takes the list's place while there are no accounts: the buttons stay on top)
        body.addLayout(side)
        main = QVBoxLayout()
        main.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.up_btn = self._button(tr("Up"), self.up)
        self.where = Crumbs(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search all your clouds"))
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self.search_all)
        self.search.textChanged.connect(lambda t: self.end_search() if not t and self.results is not None else None)
        self.search.setMinimumWidth(160)
        self.search.setMaximumWidth(280)
        self.explorer_btn = IconButton(tr("Open in File Explorer"))  # (its words left the folder's name no room)
        self.explorer_btn.setIcon(self._ic("folder_open"))
        self.explorer_btn.clicked.connect(self._open_in_explorer)
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
        self.preview_btn = self._button(tr("Preview"), self.preview_current)
        self.preview_btn.setToolTip(tr("See what's in the picked file without opening it (Space)"))
        for b in (self.upload_btn, self.folder_btn, self.refresh_btn, self.preview_btn):
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
        main.addWidget(self._welcome(), 1)
        self.transfers: list[Transfer] = []
        self.transfer_list = QListWidget()
        self.transfer_list.setObjectName("history")
        self.transfer_list.setMaximumHeight(150)
        self.transfer_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.transfer_list.customContextMenuRequested.connect(self._transfer_menu)
        self.transfer_list.hide()
        main.addWidget(self.transfer_list)
        status = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.sign_again_btn = self._button(tr("Sign in again"), self._sign_in_again)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setMaximumWidth(220)
        self.stop_btn = self._button(tr("Cancel"), self.stop)
        self.transfers_btn = self._button(tr("Transfers"), self._toggle_transfers)
        self.transfers_btn.setObjectName("flat")
        self.transfers_btn.setToolTip(tr("What Cloud uploaded, downloaded and copied, and what's waiting"))
        self.transfers_btn.hide()
        status.addWidget(self.status, 1)
        status.addWidget(self.transfers_btn)
        status.addWidget(self.sign_again_btn)
        status.addWidget(self.bar)
        status.addWidget(self.stop_btn)
        main.addLayout(status)
        body.addLayout(main, 1)
        lay.addLayout(body, 1)
        # keys: Enter opens, Delete deletes, F2 renames, Backspace goes up, F5 looks again, Ctrl+F searches
        for keys, fn in ((Qt.Key_Return, self._open_current), (Qt.Key_Enter, self._open_current),
                         (QKeySequence.StandardKey.Delete, lambda: self._with_picked(self.delete)),
                         (Qt.Key_F2, self._rename_current), (Qt.Key_Backspace, self.go_back),
                         (Qt.Key_Space, self.preview_current)):
            QShortcut(QKeySequence(keys), self.files, fn, context=Qt.WidgetShortcut)
        QShortcut(QKeySequence(Qt.Key_F5), self, self.refresh, context=Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence.StandardKey.Find, self, self.search.setFocus, context=Qt.WidgetWithChildrenShortcut)
        self.sign_again_btn.hide()
        self._busy(False)
        self.load_accounts()
        self.look_for_folders()

    def _working(self) -> bool:
        return self.job is not None and self.job.is_alive()

    def _ic(self, name: str, danger: bool = False):
        """An icon in the theme's colours, drawn once (menus and lists open without drawing them again)."""
        cache = self.__dict__.setdefault("_icons", {})
        key = (name, danger)
        if key not in cache:
            t = self.c.theme
            cache[key] = icons.icon(name, t.danger if danger and t.danger else t.muted)
        return cache[key]

    def _button(self, text, fn) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(fn)
        return b

    def _welcome(self) -> QWidget:
        """No cloud yet: what Cloud does and how to add one, in the middle of the page (like the other tools'
        start), instead of a lone line of small print."""
        box = QWidget()
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(2)
        middle = QWidget()
        middle.setMaximumWidth(560)
        col = QVBoxLayout(middle)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        tile = QLabel()
        tile.setPixmap(icons.product_tile("cloud", 64, self.c.theme.dark, self.devicePixelRatioF()))
        tile.setAlignment(Qt.AlignCenter)
        col.addWidget(tile)
        col.addSpacing(6)
        self.welcome_title = QLabel()
        self.welcome_title.setObjectName("h2")
        self.welcome_title.setAlignment(Qt.AlignCenter)
        self.welcome_title.setWordWrap(True)
        col.addWidget(self.welcome_title)
        self.welcome_text = QLabel()
        self.welcome_text.setObjectName("muted")
        self.welcome_text.setAlignment(Qt.AlignCenter)
        self.welcome_text.setWordWrap(True)
        col.addWidget(self.welcome_text)
        col.addSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()
        self.welcome_add = QPushButton(tr("Add a cloud"))
        self.welcome_add.setObjectName("primary")
        self.welcome_add.setCursor(Qt.PointingHandCursor)
        self.welcome_add.setMenu(self.add_menu)
        self.welcome_add.setMinimumWidth(self.welcome_add.sizeHint().width() + 18)  # (room for the menu's arrow)
        row.addWidget(self.welcome_add)
        self.welcome_again = self._button(tr("Look again"), self.look_for_folders)
        self.welcome_again.setToolTip(tr("Look again for cloud folders on this PC"))
        row.addWidget(self.welcome_again)
        row.addStretch()
        col.addLayout(row)
        if UPDATE_REPO:
            privacy = self._button(tr("Privacy"), self._privacy)
            privacy.setObjectName("flat")
            col.addWidget(privacy, 0, Qt.AlignHCenter)
        across = QHBoxLayout()
        across.addStretch(1)
        across.addWidget(middle, 8)  # (wide enough on the smallest window for the text to fit)
        across.addStretch(1)
        outer.addLayout(across)
        outer.addStretch(3)
        self.welcome = box
        return box

    @staticmethod
    def _privacy():
        """What Cloud reaches and keeps, before signing in."""
        owner, _, name = UPDATE_REPO.partition("/")
        QDesktopServices.openUrl(QUrl(f"https://{owner}.github.io/{name}/privacy.html#cloud"))

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
            self.empty.hide()
            if self.found is None:
                self.welcome_title.setText(tr("Looking for your clouds on this PC…"))
                self.welcome_text.setText("")
            elif any(cloud.available(k) for k in cloud.ONLINE):
                names = " · ".join(cloud.SERVICES[k] for k in cloud.ONLINE if cloud.available(k))
                self.welcome_title.setText(tr("Add your first cloud"))
                self.welcome_text.setText(tr("Sign in to {names} right here, with nothing to install and as many accounts as "
                                             "you like. Your password stays on the service's own page. Cloud folders on "
                                             "this PC show up by themselves too.", names=names))
            else:
                self.welcome_title.setText(tr("No clouds on this PC yet"))
                self.welcome_text.setText(tr("Install OneDrive, Dropbox, Google Drive or iCloud and sign in to it, and "
                                             "its files show up here. Is your cloud's folder somewhere else? Add it "
                                             "with Add a cloud."))
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
        online, here = self._ic("cloud"), self._ic("hard_drive")
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
        self.account_menu(it.data(Qt.UserRole)).exec(self.account_list.viewport().mapToGlobal(pos))

    def account_menu(self, acc: cloud.Account) -> QMenu:
        """What can be done with an account (its right-click menu)."""
        menu = QMenu(self)
        round_menu(menu)
        if acc.local:
            menu.addAction(self._ic("folder_open"), tr("Open in File Explorer"), lambda: self._explore(Path(acc.local)))
            signed = {a.service for a in self.accounts if not a.local}
            if cloud.available(acc.service) and acc.service not in signed:
                menu.addAction(self._ic("cloud"), tr("Sign in to {service} online too",
                                                     service=cloud.SERVICES[acc.service]),
                               lambda: self.sign_in(acc.service))
            if acc.extra.get("added"):
                menu.addSeparator()
                menu.addAction(self._ic("dismiss"), tr("Remove from the list"), lambda: self.remove_folder(acc))
        else:
            site = WEBSITE.get(acc.service)
            if site:
                menu.addAction(self._ic("globe"), tr("Open {service} in the browser",
                                                     service=cloud.SERVICES[acc.service]),
                               lambda: QDesktopServices.openUrl(QUrl(site)))
            menu.addAction(self._ic("arrow_sync"), tr("Refresh"), lambda: self._refresh_account(acc))
            menu.addAction(self._ic("lock_closed_key"), tr("Sign in again"), lambda: self.sign_in(acc.service))
            menu.addSeparator()
            menu.addAction(self._ic("sign_out", danger=True), tr("Sign out"), lambda: self.sign_out(acc))
        return menu

    def _refresh_account(self, acc: cloud.Account):
        if self.account is not None and self.account.key == acc.key:
            self.refresh()
        self.measure()

    def _fill_add_menu(self):
        """Sign in online (nothing to install: as many accounts as you like, several of one service too), add any
        folder, or get a cloud's app for one this copy can't sign in to."""
        menu = self.add_menu
        menu.clear()
        signed = [a.service for a in self.accounts if not a.local]
        for key in cloud.ONLINE:
            if cloud.available(key):
                name = cloud.SERVICES[key]
                text = tr("Add another {service} account", service=name) if key in signed else \
                    tr("Sign in to {service}", service=name)
                act = menu.addAction(self._ic("cloud"), text, lambda k=key: self.sign_in(k))
                act.setToolTip(tr("On {service}'s own page, in your browser. Nothing to install.", service=name))
        if not menu.isEmpty():
            menu.addSeparator()
        menu.addAction(self._ic("folder"), tr("A cloud folder on this PC…"), self.pick_folder)
        menu.addAction(self._ic("arrow_clockwise"), tr("Look again"), self.look_for_folders)
        # (an app is only offered for a cloud this copy can't sign in to, and that isn't on this PC already)
        here = {a.service for a in self.found or []} | set(signed)
        missing = [k for k in GET_APP if k not in here and not cloud.available(k)]
        if missing:
            menu.addSeparator()
            get = menu.addMenu(self._ic("arrow_download"), tr("Get a cloud's app"))
            round_menu(get)
            for key in missing:
                get.addAction(cloud.SERVICES[key], lambda k=key: QDesktopServices.openUrl(QUrl(GET_APP[k])))
        menu.setToolTipsVisible(True)

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
                        cloud.update_account(acc)  # (one signed out meanwhile isn't brought back)
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
        else:
            self.sign_again_btn.hide()
            self.status.setText(tr("Signed in as {name}.", name=ltr(result.email)))
            self.account = None
            self.load_accounts(result.key)
            win = self.window()  # (back from the browser)
            win.showNormal() if win.isMinimized() else None
            win.raise_()
            win.activateWindow()
        if self.queue:  # (what was asked for while signing in waited its turn)
            self._run(self.queue.pop(0))

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
        self.where.show_trail(service_name(acc), [e.name for e in self.trail])
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
        col, down = self.files.sort_state
        found = _sorted(found, col, down)
        self.shown = found
        self._round += 1
        self._later = list(found[FIRST_ROWS:])
        self.files.all_picked = False
        self.files.clear()
        self._add_rows(found[:FIRST_ROWS])
        if self._later:
            QTimer.singleShot(0, lambda r=self._round: self._more_rows(r))
        nothing = not found
        self.files.setVisible(not nothing)
        self.empty.setVisible(nothing)
        if nothing and self.results is not None:
            self.empty.setText(tr("Nothing found."))
        elif nothing and self.account is not None:
            self.empty.setText(tr("This folder is empty. Drop files here to upload them."))
        self._buttons()

    def _add_rows(self, part: list):
        folder_icon, file_icon, online_icon = self._ic("folder"), self._ic("document"), self._ic("cloud")
        web_icon, shared_icon = self._ic("globe"), self._ic("link")
        searching = self.results is not None
        right = Qt.AlignRight | Qt.AlignVCenter
        rows = []
        for acc, e in part:
            name = ltr(e.name)
            if searching:
                where = service_name(acc) + " · " + (e.path.rsplit("/", 1)[0] or "/")
                name = f"{name}   ·   {ltr(short_name(where, 60))}"
            it = Row([name, "" if e.folder or e.kind else human_size(e.size),
                      date_text(e.modified, True) if e.modified else ""])
            it.setIcon(0, shared_icon if e.kind in ("virtual", "drive") else folder_icon if e.folder else
                       web_icon if e.kind else online_icon if e.online else file_icon)
            if e.online:
                it.setToolTip(0, tr("Kept online only. Opening it downloads it first."))
            elif e.kind == "web":
                it.setToolTip(0, tr("Opens on the website. It has no file to download."))
            elif e.kind:
                it.setToolTip(0, tr("A Google file: it opens in the browser, and downloads as {kind}.",
                                    kind=e.kind.rsplit(":", 1)[-1].lstrip(".").upper()))
            it.setData(0, Qt.UserRole, e)
            it.setData(0, ACCOUNT, acc)
            it.setTextAlignment(1, right)
            rows.append(it)
        self.files.addTopLevelItems(rows)

    def _more_rows(self, round_: int, count: int = MORE_ROWS):
        if not work.alive(self) or round_ != self._round:  # (another folder is shown now)
            return
        part, self._later = self._later[:count], self._later[count:]
        first = self.files.topLevelItemCount()
        self._add_rows(part)
        if self.files.all_picked and part:
            self.files.pick_rows(first, first + len(part) - 1)
        if not self._later:
            self.files.all_picked = False
        if self._later:
            QTimer.singleShot(0, lambda: self._more_rows(round_))

    def finish_rows(self):
        if self._later:
            self._more_rows(self._round, len(self._later))

    def resort(self):
        """A column heading was clicked: the same files, in the new order (what was selected stays selected)."""
        def key(it):
            e, acc = it.data(0, Qt.UserRole), it.data(0, ACCOUNT)
            return (getattr(acc, "key", ""), getattr(e, "id", None))
        chosen = {key(it) for it in self.files.selectedItems()}
        self._show(self.shown)
        if chosen:
            self.finish_rows()
            for i in range(self.files.topLevelItemCount()):
                it = self.files.topLevelItem(i)
                if key(it) in chosen:
                    it.setSelected(True)

    def _buttons(self):
        have = self.account is not None
        any_account = bool(self.accounts)
        for w in (self.account_list, self.up_btn, self.where, self.search, self.upload_btn,
                  self.folder_btn, self.refresh_btn, self.space):
            w.setVisible(any_account)  # nothing to browse yet: only the buttons and the note
        self.welcome.setVisible(not any_account)  # (nothing yet: the big welcome in the middle has the buttons)
        self.add_btn.setVisible(any_account)
        if self.privacy_btn is not None:
            self.privacy_btn.setVisible(any_account)
        self.again_btn.setVisible(any_account and self.found == [])
        self.again_btn.setEnabled(not self.looking)
        self.welcome_add.setEnabled(not self.looking or self.found is not None)
        self.welcome_again.setEnabled(not self.looking)
        self.welcome_text.setVisible(bool(self.welcome_text.text()))
        self.explorer_btn.setVisible(have and bool(self.account.local))
        here = self._here()
        browsing = have and self.results is None and not (here is not None and here.kind == "virtual")
        for b in (self.upload_btn, self.folder_btn):
            b.setEnabled(browsing)
        self.refresh_btn.setEnabled(have)
        self.preview_btn.setVisible(any_account)
        self.preview_btn.setEnabled(have and bool(self.shown))
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

    def go_to(self, depth: int):
        """Back to a folder on the way here (0: the cloud's top)."""
        if self.results is not None:
            self.end_search()
        if 0 <= depth < len(self.trail):
            del self.trail[depth:]
            self.open_here()

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
        elif e.kind and e.web:  # (a Google Doc, Form…: it's opened where it's changed, so nothing is left behind)
            QDesktopServices.openUrl(QUrl(e.web))
            self.status.setText(tr("Opened {name} in the browser.", name=ltr(e.name)))
        elif e.kind == "web":
            self.status.setText(_ui_error(cloud.CloudError(cloud.WEB_ONLY)))
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
        if len(picked) == 1 and picked[0].kind not in ("virtual", "drive"):
            self.rename(acc, picked[0])

    def _with_picked(self, fn):
        acc, picked = self.files.picked()
        if picked:
            fn(acc, picked)

    def _file_menu(self, pos):
        acc, picked = self.files.picked()
        if picked:
            self.file_menu(acc, picked).exec(self.files.viewport().mapToGlobal(pos))

    def file_menu(self, acc: cloud.Account, picked: list) -> QMenu:
        """What can be done with the picked files and folders (their right-click menu)."""
        menu = QMenu(self)
        round_menu(menu)
        one = picked[0] if len(picked) == 1 else None
        fixed = any(e.kind in ("virtual", "drive") for e in picked)  # (Shared with me…: not a real folder)
        if one is not None:
            menu.addAction(self._ic("folder_open" if one.folder else "document"), tr("Open") + "\tEnter",
                           lambda: self._open(acc, one))
            if not one.folder and not one.kind:
                menu.addAction(self._ic("search"), tr("Preview") + "\tSpace", lambda: self.preview(acc, one))
            if one.web and not acc.local and one.kind != "web":
                menu.addAction(self._ic("globe"), tr("Open on the website"),
                               lambda: QDesktopServices.openUrl(QUrl(one.web)))
        if acc.local and one is not None:
            menu.addAction(self._ic("folder_search"), tr("Show in folder"),
                           lambda: self._explore(Path(one.id), select=True))
        menu.addSeparator()
        menu.addAction(self._ic("arrow_download"), tr("Download…"), lambda: self.download(acc, picked))
        others = [a for a in self.accounts if a.key != acc.key]
        if others:
            sub = menu.addMenu(self._ic("copy"), tr("Copy to"))
            round_menu(sub)
            for a in others:
                sub.addAction(self._ic("hard_drive" if a.local else "cloud"), ltr(a.title),
                              lambda a=a: self.copy_to(acc, picked, a, None))
            if not fixed:
                sub = menu.addMenu(self._ic("cut"), tr("Move to"))
                round_menu(sub)
                for a in others:
                    sub.addAction(self._ic("hard_drive" if a.local else "cloud"), ltr(a.title),
                                  lambda a=a: self.copy_to(acc, picked, a, None, move=True))
        if not fixed:
            if one is not None:
                menu.addAction(self._ic("rename"), tr("Rename…") + "\tF2", lambda: self.rename(acc, one))
            menu.addSeparator()
            menu.addAction(self._ic("delete", danger=True), tr("Delete") + "\tDel", lambda: self.delete(acc, picked))
        return menu

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

    # ---- a look inside a file (the Viewer's Quick look)

    def preview_current(self):
        acc, e = self._item_of(self.files.currentItem())
        if e is not None and not e.folder and not e.kind:
            self.preview(acc, e)

    def preview(self, acc: cloud.Account, e: cloud.Entry):
        """Shows what's in a file without opening it. An online file is downloaded to a temporary folder first."""
        if acc.local:
            here = [Path(x.id) for a, x in self.shown if not x.folder and a.key == acc.key]
            self._peek(Path(e.id), here)
            return
        if e.size > PREVIEW_MAX:
            self.status.setText(tr("{name} is too big to preview. Download it to see it.", name=ltr(e.name)))
            return

        def go(progress, cancel):
            folder = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
            target = folder / cloud.file_name(e)
            cloud.client(acc).download(e, target, lambda d, t: progress((e.name, d, t)), cancel)
            return target
        self._run(go, refresh=False, what=tr("Preview {name}", name=ltr(e.name)), then=lambda p: self._peek(p, [p]))

    def _peek(self, path: Path, siblings: list[Path]):
        from .peekview import PeekWindow
        if getattr(self, "peeker", None) is None:
            class Host:  # (what the Quick look window needs from its tool)
                c = self.c
                window = None
            self.peeker = PeekWindow(Host())
        self.peeker.show_files([path], 0)
        if len(siblings) > 1:
            self.peeker.set_folder(0, siblings)  # (← → go to the other files here)

    # ---- the list of transfers

    def _toggle_transfers(self):
        self.transfer_list.setVisible(not self.transfer_list.isVisible())

    def _transfer_text(self, t: Transfer) -> str:
        if t.state == "working" and t.total:
            return f"{t.what}   ·   {human_size(t.done)} / {human_size(t.total)}"
        state = {"waiting": tr("Waiting"), "working": tr("Working…"), "done": t.note or tr("Done"),
                 "failed": t.note, "cancelled": tr("Cancelled")}[t.state]
        return f"{t.what}   ·   {state}" if state else t.what

    def _show_transfer(self, t: Transfer):
        if t.item is None:
            t.item = QListWidgetItem()
            self.transfer_list.insertItem(0, t.item)
        t.item.setText(self._transfer_text(t))
        theme = self.c.theme
        name = {"waiting": "timer", "working": "arrow_sync", "done": "shield_checkmark", "failed": "warning",
                "cancelled": "dismiss"}[t.state]
        color = theme.danger if t.state == "failed" else theme.accent if t.state == "working" else theme.muted
        t.item.setIcon(icons.icon(name, color))
        t.item.setData(Qt.UserRole, t)
        self._show_transfer_count()

    def _transfer_menu(self, pos):
        it = self.transfer_list.itemAt(pos)
        t = it.data(Qt.UserRole) if it is not None else None
        menu = QMenu(self)
        round_menu(menu)
        if t is not None and t.state == "waiting":
            menu.addAction(tr("Don't do this one"), lambda: self._drop_waiting(t))
        if t is not None and t.state == "working":
            menu.addAction(tr("Cancel"), self.cancel.set)
        if any(x.state in ("done", "failed", "cancelled") for x in self.transfers):
            menu.addAction(tr("Clear the finished ones"), self._clear_finished)
        if not menu.isEmpty():
            menu.exec(self.transfer_list.viewport().mapToGlobal(pos))

    def _drop_waiting(self, t: Transfer):
        if t in self.queue:
            self.queue.remove(t)
            t.state = "cancelled"
            self._show_transfer(t)

    def _clear_finished(self):
        for t in [x for x in self.transfers if x.state in ("done", "failed", "cancelled")]:
            self.transfers.remove(t)
            if t.item is not None:
                self.transfer_list.takeItem(self.transfer_list.row(t.item))
        self._show_transfer_count()

    def _show_transfer_count(self):
        waiting = sum(x.state in ("waiting", "working") for x in self.transfers)
        self.transfers_btn.setText(tr("Transfers") + (f" ({waiting})" if waiting else ""))
        self.transfers_btn.setVisible(bool(self.transfers))
        if not self.transfers:
            self.transfer_list.hide()

    def _keep(self, t: Transfer):
        self.transfers.append(t)
        while len(self.transfers) > KEPT:
            old = next((x for x in self.transfers if x.state in ("done", "failed", "cancelled")), None)
            if old is None:
                break
            self.transfers.remove(old)
            if old.item is not None:
                self.transfer_list.takeItem(self.transfer_list.row(old.item))
        self._show_transfer(t)

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
            t = self.current
            if t is not None:
                t.done, t.total = done, total
                self._show_transfer(t)

    def stop(self):
        """Cancel: what's going on stops, and what was waiting is dropped."""
        for t in self.queue:
            t.state = "cancelled"
            self._show_transfer(t)
        self.queue.clear()
        self.cancel.set()

    def _run(self, fn, refresh: bool = True, what: str = "", then=None):
        """fn(progress, cancel) does the work off the window's thread and returns what to say when it's done
        (or, with then, what then(result) gets). what names it in the list of transfers."""
        t = fn if isinstance(fn, Transfer) else Transfer(what or tr("Working…"), fn, refresh)
        if then is not None:
            t.then = then
        if t not in self.transfers:
            self._keep(t)
        if self._working():
            self.queue.append(t)
            self.status.setText(tr("Waiting for what's going on to finish…") + "   ·   " +
                                tr("{count} more waiting", count=len(self.queue)))
            return
        self.cancel = threading.Event()
        cancel = self.cancel
        self.current = t
        t.state = "working"
        self._show_transfer(t)
        self._busy(True, t.what)

        def finished(result):
            self.job = None
            self.current = None
            if not work.alive(self):
                return
            self._busy(False)
            after = getattr(t, "then", None)
            if isinstance(result, Exception):
                t.state = "cancelled" if cancel.is_set() else "failed"
                t.note = _ui_error(result)
                self.status.setText(t.note)
            elif after is not None:
                t.state = "done"
                self.status.setText("")
                try:
                    after(result)
                except Exception as e:  # (a file the Quick look can't show…)
                    self.status.setText(_ui_error(e))
            else:
                t.state, t.note = "done", result or ""
                self.status.setText(t.note)
            self._show_transfer(t)
            if isinstance(result, cloud.SignInNeeded):
                self.sign_again_btn.show()
            if t.refresh:
                self.refresh(quiet=True)  # (what was done stays in the status line)
            if self.queue:
                self._run(self.queue.pop(0))
        self.job = work.run(lambda progress: t.fn(progress, cancel), self._progress, finished,
                            name="corefold-cloud")

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
            OPENED.mkdir(parents=True, exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=OPENED))
            target = folder / cloud.file_name(e)
            cloud.client(acc).download(e, target, lambda d, t: progress((e.name, d, t)), cancel)
            note_opened(target)
            os_open(target)
            return tr("Opened {name}. Changes to it stay on this PC until you upload it again.", name=ltr(e.name))
        self._run(go, refresh=False, what=tr("Open {name}", name=ltr(e.name)))

    def download(self, acc: cloud.Account, entries: list):
        folder = QFileDialog.getExistingDirectory(self, tr("Download to"), self.download_to)
        if not folder:
            return
        self.download_to = folder

        def go(progress, cancel):
            c = cloud.client(acc)
            count = sum(cloud.download_tree(c, e, Path(folder), progress, cancel) for e in entries)
            said = tr("Downloaded 1 file to {folder}.", folder=ltr(folder)) if count == 1 else \
                tr("Downloaded {count} files to {folder}.", count=count, folder=ltr(folder))
            return said + _left_out(c)
        self._run(go, refresh=False, what=tr("Download {what}", what=self._what(entries)))

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
        what = ltr(paths[0].name) if len(paths) == 1 else tr("{count} items", count=len(paths))
        self._run(go, what=tr("Upload {what}", what=what))

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
        self._run(go, what=tr("New folder {name}", name=ltr(name)))

    def rename(self, acc: cloud.Account, e: cloud.Entry):
        name, ok = QInputDialog.getText(self, tr("Rename"), tr("New name:"), text=e.name)
        name = name.strip()
        if not ok or not name or name == e.name:
            return

        def go(progress, cancel):
            cloud.client(acc).rename(e, name)
            return tr("Renamed.")
        self._run(go, what=tr("Rename {name}", name=ltr(e.name)))

    def delete(self, acc: cloud.Account, entries: list):
        entries = [e for e in entries if e.kind not in ("virtual", "drive")]  # (Shared with me… can't be deleted)
        if not entries:
            return
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
        self._run(go, what=tr("Delete {what}", what=what))

    def copy_to(self, source: cloud.Account, entries: list, target: cloud.Account, into: cloud.Entry | None,
                move: bool = False):
        """Copies files and folders from one account to another (into a folder there, or its top), one file at a
        time through this PC; folders on this PC are copied straight from where they are. move: each one that got
        there whole is then deleted from where it was (into that account's deleted files, or the Recycle Bin)."""
        if not entries or source.key == target.key:
            return
        if move:
            entries = [e for e in entries if e.kind not in ("virtual", "drive")]
            if not entries:
                return

        def go(progress, cancel):
            src, dst = cloud.client(source), cloud.client(target)
            count = moved = 0
            for e in entries:
                left_out = len(src.skipped)
                if source.local:
                    count += cloud.upload_tree(dst, Path(e.id), into, progress, cancel)
                else:
                    count += cloud.copy_tree(src, e, dst, into, progress, cancel)
                if move and len(src.skipped) == left_out:  # (all of it got there: only then is it taken away)
                    if cancel.is_set():
                        raise cloud.CloudError("cancelled")
                    src.delete(e)
                    moved += 1
            if move:
                said = tr("Moved 1 file to {name}.", name=ltr(target.title)) if count == 1 else \
                    tr("Moved {count} files to {name}.", count=count, name=ltr(target.title))
                if moved < len(entries):
                    said += " " + tr("What couldn't be copied whole stays where it was.")
            else:
                said = tr("Copied 1 file to {name}.", name=ltr(target.title)) if count == 1 else \
                    tr("Copied {count} files to {name}.", count=count, name=ltr(target.title))
            return said + _left_out(src)
        here = self.account is not None and self.account.key in (target.key, source.key if move else "")
        self._run(go, refresh=here,
                  what=(tr("Move {what} to {name}", what=self._what(entries), name=ltr(target.title)) if move else
                        tr("Copy {what} to {name}", what=self._what(entries), name=ltr(target.title))))

    @staticmethod
    def _what(entries: list) -> str:
        return ltr(entries[0].name) if len(entries) == 1 else tr("{count} items", count=len(entries))

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
        self.where.show_text(tr("Found: {text}", text=ltr(text)))
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
        self.__dict__.pop("_icons", None)
        self._fill_accounts()
        self._show(self.shown)
