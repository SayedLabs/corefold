"""Corefold File Organizer's page: pick Downloads, the Desktop or any folder, see where every file will go,
then sort it with one click (and undo it with another)."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, QSize, QStandardPaths, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton, QScrollArea,
                               QSizePolicy, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import i18n, icons, organizer, work
from .atomic.tokens import mix
from .atomic.widgets import IconButton
from .core import human_size, short_name
from .i18n import ltr, tr
from .timefmt import time_ago
from .winutil import reveal_in_explorer

if TYPE_CHECKING:
    from .app import Controller

log = logging.getLogger("corefold")

# the folders on the cards: (key, English name shown through tr(), glyph)
PLACES = [("downloads", "Downloads", "arrow_download"), ("desktop", "Desktop", "desktop"),
          ("other", "Another folder…", "folder_search")]
KIND_GLYPHS = {"pictures": "image", "videos": "video", "music": "music_note_2", "documents": "document_text",
               "archives": "folder_zip", "installers": "box", "fonts": "text_t"}
FILE_ROLE = Qt.UserRole + 1


def known_folder(key: str) -> Path | None:
    """Downloads or the Desktop, wherever they really are (moved to another drive, or into OneDrive)."""
    if key == "downloads":
        from .app import downloads_folder
        return downloads_folder()
    if key == "desktop":
        loc = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
        return Path(loc) if loc else Path.home() / "Desktop"
    return None


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def to_sort_text(n: int) -> str:
    if not n:
        return tr("Nothing to sort")
    return tr("1 file to sort") if n == 1 else tr("{count} files to sort", count=n)


def _para(text: str) -> str:
    """In Arabic, a line that starts with a Latin name still reads right to left."""
    return "\u200f" + text if text and i18n.is_rtl() else text


class FolderCard(QPushButton):
    """A folder to sort: its icon, its name, and how many files are waiting. The picked one is outlined."""

    def __init__(self, theme, key: str, title: str, glyph: str):
        super().__init__()
        self.theme, self.key, self.title, self.glyph = theme, key, title, glyph
        self.line = ""
        self.setCheckable(True)
        self.setObjectName("folderCard")
        self.setAccessibleName(title)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(74)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_Hover)
        self.setFocusPolicy(Qt.StrongFocus)

    def set_line(self, text: str):
        self.line = text
        self.setToolTip(f"{self.title}\n{text}" if text else self.title)
        self.update()

    def sizeHint(self):
        return QSize(220, self.minimumHeight())

    def minimumSizeHint(self):
        return QSize(150, self.minimumHeight())

    def paintEvent(self, e):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rtl = self.layoutDirection() == Qt.RightToLeft
        hover = self.underMouse()
        on = self.isChecked()
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        edge = t.accent if on or self.hasFocus() else (t.line_strong if hover else t.border) or t.border
        p.setPen(QPen(edge, 2 if on else 1))
        p.setBrush(mix(t.panel, t.accent, 0.08) if on else (t.hover if hover else t.panel))
        p.drawRoundedRect(r, 12, 12)
        pad = 14
        tile = QRectF(r.right() - pad - 40 if rtl else r.left() + pad, r.center().y() - 20, 40, 40)
        p.setPen(Qt.NoPen)
        p.setBrush(mix(t.panel, t.accent, 0.2 if t.dark else 0.13))
        p.drawRoundedRect(tile, 10, 10)
        icons.paint(p, tile.adjusted(10, 10, -10, -10), self.glyph, t.accent)
        x0 = tile.right() + 12 if not rtl else r.left() + pad
        x1 = r.right() - pad if not rtl else tile.left() - 12
        align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignVCenter
        f = self.font()
        f.setBold(True)
        p.setFont(f)
        p.setPen(t.text)
        fm = p.fontMetrics()
        text_h = fm.height() * 2 + 2 if self.line else fm.height()
        top = r.center().y() - text_h / 2
        p.drawText(QRectF(x0, top, x1 - x0, fm.height()), align,
                   fm.elidedText(self.title, Qt.ElideRight, int(x1 - x0)))
        if self.line:
            f.setBold(False)
            p.setFont(f)
            p.setPen(t.muted)
            fm = p.fontMetrics()
            p.drawText(QRectF(x0, top + fm.height() + 2, x1 - x0, fm.height()), align,
                       fm.elidedText(self.line, Qt.ElideRight, int(x1 - x0)))
        p.end()


class _KindItem(QTreeWidgetItem):
    """A kind's line in the list. Its own box being ticked comes through setData (a click, the keyboard),
    while Qt only redraws it when one of its files changes, so the two can be told apart."""
    on_ticked = None

    def setData(self, column, role, value):
        super().setData(column, role, value)
        if column == 0 and role == Qt.CheckStateRole and self.on_ticked is not None:
            self.on_ticked(self)


class OrganizerPage(QWidget):
    """Corefold File Organizer in the window."""

    def __init__(self, controller: "Controller", header):
        super().__init__()
        self.c = controller
        self.key = "downloads"
        self.folder: Path | None = None
        self.other: Path | None = None
        self.plan: organizer.Plan | None = None
        self.job = None
        self.count_job = None
        self.busy = False
        self.loading = False
        self._filling = False
        self.left_out: set[str] = set()   # files the person unticked (left out again next time)
        self.setAcceptDrops(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QWidget()
        body.setObjectName("scrollContent")  # (see-through, like every page's scrolling part)
        scroll = QScrollArea()  # (in a small window the whole page scrolls, instead of squeezing the list away)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(12)
        lay.addLayout(header)

        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.cards: dict[str, FolderCard] = {}
        for key, name, glyph in PLACES:
            card = FolderCard(self.c.theme, key, tr(name), glyph)
            card.clicked.connect(lambda _=False, k=key: self.pick(k))
            cards.addWidget(card)
            self.cards[key] = card
        lay.addLayout(cards)

        where = QHBoxLayout()
        where.setSpacing(8)
        self.where = _label("", "muted")
        self.where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.where.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        where.addWidget(self.where, 1)
        self.open_btn = IconButton(tr("Open the folder"))
        self.open_btn.setIcon(icons.icon("folder_open", self.c.theme.muted))
        self.open_btn.clicked.connect(self._open_folder)
        where.addWidget(self.open_btn)
        self.refresh_btn = IconButton(tr("Look again"))
        self.refresh_btn.setIcon(icons.icon("arrow_clockwise", self.c.theme.muted))
        self.refresh_btn.clicked.connect(self.load)
        where.addWidget(self.refresh_btn)
        lay.addLayout(where)

        self.note = QFrame()   # what the last sort or undo did
        self.note.setObjectName("card")
        nl = QHBoxLayout(self.note)
        nl.setContentsMargins(14, 10, 14, 10)
        nl.setSpacing(10)
        tick = QLabel()
        tick.setPixmap(icons.pixmap("sparkle", 18, self.c.theme.accent, self.devicePixelRatioF()))
        nl.addWidget(tick, 0, Qt.AlignTop)
        self.note_text = _label("", None, wrap=True)
        nl.addWidget(self.note_text, 1)
        self.note.hide()
        lay.addWidget(self.note)
        self.problem = _label("", "warning", wrap=True)
        self.problem.hide()
        lay.addWidget(self.problem)
        self.summary = _label("", None, wrap=True)
        lay.addWidget(self.summary)

        self.tree = QTreeWidget()
        self.tree.setObjectName("history")
        self.tree.setProperty("ticks", True)  # (tick boxes drawn like every other check box in Corefold)
        self.tree.setHeaderLabels([tr("Name"), tr("Size")])
        self.tree.setUniformRowHeights(True)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.itemChanged.connect(self._ticked)
        self.tree.itemDoubleClicked.connect(self._show_file)
        self.tree.setMinimumHeight(160)
        lay.addWidget(self.tree, 1)
        self.empty = _label("", "muted", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(160)
        lay.addWidget(self.empty, 1)
        self.empty.hide()

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.last = _label("", "muted", wrap=True)
        bar.addWidget(self.last, 1)
        self.undo_btn = QPushButton(tr("Undo the last sort"))
        self.undo_btn.setObjectName("ghost")
        self.undo_btn.setIcon(icons.icon("arrow_rotate_counterclockwise", self.c.theme.text))
        self.undo_btn.setCursor(Qt.PointingHandCursor)
        self.undo_btn.clicked.connect(self.undo)
        bar.addWidget(self.undo_btn)
        self.sort_btn = QPushButton(tr("Sort"))
        self.sort_btn.setObjectName("primary")
        self.sort_btn.setCursor(Qt.PointingHandCursor)
        self.sort_btn.clicked.connect(self.sort)
        bar.addWidget(self.sort_btn)
        foot = QVBoxLayout()  # (under the scrolling part: Sort and Undo are always in sight)
        foot.setContentsMargins(0, 10, 10, 0)
        foot.setSpacing(8)
        foot.addLayout(bar)
        # (as Hazel, File Juggler and DropIt do: the folder stays tidy without opening Corefold)
        self.auto = QCheckBox(tr("Keep this folder tidy by itself: once a day, files that have been here a day "
                                 "go into their folders (you can undo it)"))
        self.auto.toggled.connect(self._auto_changed)
        foot.addWidget(self.auto)
        foot.addWidget(_label(tr("Files are only moved into folders right next to them: nothing is deleted, "
                                 "copied or replaced. Folders, shortcuts and files that are open stay where they "
                                 "are."), "subtle", wrap=True))
        outer.addLayout(foot)
        self.cards["downloads"].setChecked(True)
        self._show_last()
        self._show_state()

    # ---- picking a folder

    def ensure_loaded(self):
        """Opening the page: look again (files come and go)."""
        self._count_cards()
        self.load()

    def pick(self, key: str):
        if self.busy:
            self.cards[key].setChecked(key == self.key)
            return
        if key == "other":
            start = str(self.other or Path.home())
            chosen = QFileDialog.getExistingDirectory(self, tr("Pick a folder to sort"), start)
            if not chosen:
                for k, card in self.cards.items():
                    card.setChecked(k == self.key)
                return
            self.other = Path(chosen)
            self.cards["other"].set_line(ltr(short_name(str(self.other), 40)))
        self.key = key
        for k, card in self.cards.items():
            card.setChecked(k == key)
        self.note.hide()
        self.load()

    def set_folder(self, folder: Path):
        """Sort this folder (dropped on the page, or picked)."""
        self.other = Path(folder)
        self.cards["other"].set_line(ltr(short_name(str(self.other), 40)))
        self.key = "other"
        for k, card in self.cards.items():
            card.setChecked(k == "other")
        self.note.hide()
        self.load()

    def _show_auto(self):
        kept = self.c.settings.organize_auto_folder
        self.auto.blockSignals(True)
        self.auto.setChecked(bool(kept) and self.folder is not None and
                             os.path.normcase(kept) == os.path.normcase(str(self.folder)))
        self.auto.blockSignals(False)

    def _auto_changed(self, on: bool):
        """One folder is kept tidy: ticking it here moves that job to this folder."""
        s = self.c.settings
        if on and self.folder is not None:
            if organizer.refused(self.folder, known=self.key != "other"):
                self._show_auto()
                return
            s.organize_auto_folder = str(self.folder)
            s.organize_auto_last = 0.0  # (the first tidy comes with the next look, within the hour)
        elif not on:
            s.organize_auto_folder = ""
        s.save()
        self.c.keep_tidy()

    def current_folder(self) -> Path | None:
        return self.other if self.key == "other" else known_folder(self.key)

    def dragEnterEvent(self, e):
        urls = e.mimeData().urls() if e.mimeData().hasUrls() else []
        if len(urls) == 1 and urls[0].isLocalFile() and os.path.isdir(urls[0].toLocalFile()) and not self.busy:
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.set_folder(Path(e.mimeData().urls()[0].toLocalFile()))
        e.acceptProposedAction()

    def _count_cards(self):
        """How many files wait in Downloads and on the Desktop (on the cards)."""
        if self.count_job is not None and self.count_job.is_alive():
            return
        places = {k: known_folder(k) for k in ("downloads", "desktop")}

        def count(_progress):
            return {k: organizer.loose_count(p, known=True) if p else 0 for k, p in places.items()}
        self.count_job = work.run(count, lambda _: None, self._counted, name="corefold-organizer-count")

    def _counted(self, result):
        if not work.alive(self) or isinstance(result, Exception):
            return
        for k, n in result.items():
            self.cards[k].set_line(to_sort_text(n))

    # ---- the plan

    def load(self):
        if self.busy or self.loading:
            return
        folder = self.current_folder()
        if folder is None:
            return
        self.folder = folder
        self._show_auto()
        self.loading = True
        self._show_state()
        known = self.key != "other"
        self.job = work.run(lambda _p: organizer.plan(folder, known=known), lambda _: None,
                            lambda result, f=folder: self._planned(f, result), name="corefold-organizer-plan")

    def _planned(self, folder: Path, result):
        if not work.alive(self):
            return
        self.loading = False
        if folder != self.current_folder():  # (another folder was picked meanwhile)
            self.load()
            return
        if isinstance(result, Exception):
            log.warning("couldn't look through %s: %s", folder, result)
            self.plan = None
            self._say(tr("Corefold couldn't look through {folder}.", folder=ltr(str(folder))))
        else:
            self.plan = result
            if result.refused:
                self._say(tr(organizer.REFUSED[result.refused]))
            else:
                self.problem.hide()
            if self.key in ("downloads", "desktop") and not result.refused:
                n = len([i for i in result.items if i.kind])
                self.cards[self.key].set_line(to_sort_text(n))
        self._fill()

    def _fill(self):
        self._filling = True
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        p = self.plan
        t = self.c.theme
        if p is not None and not p.refused:
            for kind, items in p.by_kind().items():
                head = _KindItem(self.tree)
                head.on_ticked = self._kind_ticked
                head.setData(0, Qt.UserRole, kind)
                head.setIcon(0, icons.icon(KIND_GLYPHS.get(kind, "folder"), t.accent))
                head.setFlags(head.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                head.setToolTip(0, str(p.folders[kind]))
                for item in items:
                    if os.path.normcase(str(item.path)) in self.left_out:
                        item.on = False
                    child = QTreeWidgetItem(head)
                    child.setData(0, FILE_ROLE, item)
                    child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                    child.setText(0, _para(self._file_text(item)))
                    child.setText(1, human_size(item.size))
                    child.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                    child.setToolTip(0, f"{item.path}\n→ {item.target}")
                    child.setCheckState(0, Qt.Checked if item.on else Qt.Unchecked)
                self._head_text(head)
            staying = p.staying()
            if staying:
                head = QTreeWidgetItem(self.tree)
                head.setIcon(0, icons.icon("pin", t.muted))
                head.setText(0, tr("Left where they are: {count}", count=len(staying)))
                head.setForeground(0, t.muted)
                for item in staying:
                    child = QTreeWidgetItem(head)
                    child.setText(0, _para(f"{ltr(item.path.name)}  ·  {tr(organizer.STAYS[item.why])}"))
                    child.setText(1, human_size(item.size))
                    child.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                    child.setForeground(0, t.muted)
                    child.setToolTip(0, str(item.path))
                    child.setData(0, FILE_ROLE, item)
        if self.tree.topLevelItemCount() <= 3:
            self.tree.expandAll()
        self.tree.setUpdatesEnabled(True)
        self._filling = False
        self._show_state()

    def _file_text(self, item: organizer.Item) -> str:
        if item.renamed:  # (a file with its name is already in that folder)
            return tr("{name} → {new} (one with that name is already there)", name=ltr(item.path.name),
                      new=ltr(item.target.name))
        return ltr(item.path.name)

    def _head_text(self, head: QTreeWidgetItem):
        kind = head.data(0, Qt.UserRole)
        n = head.childCount()
        on = sum(1 for i in range(n) if head.child(i).checkState(0) == Qt.Checked)
        size = sum(head.child(i).data(0, FILE_ROLE).size for i in range(n))
        name = self.plan.folders[kind].name if self.plan else organizer.folder_name(kind)
        if on == n:
            words = tr("{folder}: 1 file", folder=ltr(name)) if n == 1 else \
                tr("{folder}: {count} files", folder=ltr(name), count=n)
        elif not on:
            words = tr("{folder}: left out", folder=ltr(name))
        else:
            words = tr("{folder}: {count} of {total} files", folder=ltr(name), count=on, total=n)
        if self.plan and kind in self.plan.new_folders:
            words += "  ·  " + tr("new folder")
        head.setText(0, _para(words))
        head.setText(1, human_size(size))
        head.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)

    def _ticked(self, it: QTreeWidgetItem, column: int):
        if self._filling or column != 0:
            return
        item = it.data(0, FILE_ROLE)
        if isinstance(item, organizer.Item) and item.kind:
            item.on = it.checkState(0) == Qt.Checked
            key = os.path.normcase(str(item.path))
            if item.on:
                self.left_out.discard(key)
            else:
                self.left_out.add(key)
            parent = it.parent()
            if parent is not None:
                self._filling = True
                self._head_text(parent)
                self._filling = False
        elif it.data(0, Qt.UserRole):  # a whole kind (Qt ticks its files too)
            self._filling = True
            self._head_text(it)
            self._filling = False
        self._show_state()

    def _kind_ticked(self, it: QTreeWidgetItem):
        """The kind's own box was ticked or unticked (not Qt updating it for its files): kept for next time.
        (Qt also unticks a kind when its last file is unticked, and leaving one file out this time mustn't
        leave the whole kind out of every tidy.)"""
        if self._filling or it.checkState(0) == Qt.PartiallyChecked:
            return
        off = organizer.kinds_off()
        kind = it.data(0, Qt.UserRole)
        if it.checkState(0) == Qt.Unchecked:
            off.add(kind)
        else:
            off.discard(kind)
        organizer.set_off(off)

    def _show_file(self, it: QTreeWidgetItem, _column: int):
        item = it.data(0, FILE_ROLE)
        if isinstance(item, organizer.Item) and item.path.exists():
            reveal_in_explorer(item.path)

    def _open_folder(self):
        if self.folder is not None and self.folder.is_dir():
            reveal_in_explorer(self.folder)

    # ---- what the page says

    def _show_state(self):
        p = self.plan
        folder = self.current_folder()
        self.where.setText(_para(ltr(str(folder))) if folder else "")
        self.where.setToolTip(str(folder) if folder else "")
        moving = p.moving() if p is not None else []
        staying = p.staying() if p is not None else []
        kinds = {i.kind for i in moving}
        nothing = p is None or bool(p.refused) or not p.items
        if self.loading and (p is None or p.folder != folder):
            self.empty.setText(tr("Looking through the folder…"))
            nothing = True
        elif p is not None and p.refused:
            self.empty.setText(tr("Nothing here will be sorted."))
        elif p is not None and not p.items:
            self.empty.setText(tr("Nothing to sort: there are no loose files here."))
        elif p is None:
            self.empty.setText("")
        self.tree.setVisible(not nothing)
        self.empty.setVisible(nothing)
        if p is not None and not p.refused and p.items:
            if moving:
                if len(moving) == 1:
                    text = tr("1 file will go into its folder.")
                elif len(kinds) == 1:
                    text = tr("{count} files will go into 1 folder.", count=len(moving))
                else:
                    text = tr("{count} files will go into {folders} folders.", count=len(moving), folders=len(kinds))
            else:
                text = tr("Nothing will be moved.")
            if staying:
                text += "  ·  " + tr("Left where they are: {count}", count=len(staying))
            self.summary.setText(text)
            self.summary.show()
        else:
            self.summary.hide()
        self.sort_btn.setText((tr("Sort 1 file") if len(moving) == 1 else tr("Sort {count} files", count=len(moving)))
                              if moving else tr("Sort"))
        self.sort_btn.setEnabled(bool(moving) and not self.busy and not self.loading)
        self.refresh_btn.setEnabled(not self.busy and not self.loading)
        self.open_btn.setEnabled(folder is not None)
        self.undo_btn.setEnabled(organizer.last_sort() is not None and not self.busy)
        for card in self.cards.values():
            card.setEnabled(not self.busy)

    def _show_last(self):
        last = organizer.last_sort()
        if last is None:
            self.last.setText("")
            self.undo_btn.setToolTip("")
            return
        folder = Path(last["folder"])
        n, name, when = len(last["moves"]), ltr(folder.name or str(folder)), time_ago(last.get("time", 0))
        text = tr("Last sort: 1 file in {folder}, {when}", folder=name, when=when) if n == 1 else \
            tr("Last sort: {count} files in {folder}, {when}", count=n, folder=name, when=when)
        self.last.setText(_para(text))
        self.undo_btn.setToolTip(tr("Puts every file back where it was before."))

    def _say(self, text: str):
        self.problem.setText(text)
        self.problem.show()

    def _tell(self, text: str):
        self.note_text.setText(text)
        self.note.show()

    # ---- sorting and undoing (off the window's thread)

    def _tidying(self) -> bool:
        """The daily tidy (or its Undo) is running: a sort or Undo now would wait, then work on what it changed."""
        if getattr(self.c, "_tidy_job", None) is None and getattr(self.c, "_untidy_job", None) is None:
            return False
        self._say(tr("Wait for what's going on to finish first."))
        return True

    def sort(self):
        p = self.plan
        if p is None or self.busy or not p.moving() or self._tidying():
            return
        self.busy = True
        self.note.hide()
        self.problem.hide()
        self.sort_btn.setText(tr("Sorting…"))
        self._show_state()
        self.job = work.run(lambda progress: organizer.sort(p, progress), lambda _: None, self._sorted,
                            name="corefold-organizer-sort", busy=True)

    def _sorted(self, result):
        if not work.alive(self):
            return
        self.busy = False
        if isinstance(result, Exception):
            log.warning("sorting failed: %s", result)
            self._say(tr("Something went wrong while sorting. Files that were moved can be put back with Undo."))
        else:
            if not result.moved:
                text = tr("Nothing was moved.")
            elif result.moved == 1:
                text = tr("Sorted 1 file into its folder.")
            elif result.folders == 1:
                text = tr("Sorted {count} files into 1 folder.", count=result.moved)
            else:
                text = tr("Sorted {count} files into {folders} folders.", count=result.moved, folders=result.folders)
            in_use = sum(1 for _, why in result.failed if why == "in_use")
            other = len(result.failed) - in_use
            if in_use:
                text += "\n" + tr("Left because a program has them open: {count}", count=in_use)
            if other:
                text += "\n" + tr("Couldn't be moved, so they stayed: {count}", count=other)
            self._tell(text)
            if hasattr(self.c, "play_done_sound"):
                self.c.play_done_sound()
        self._show_last()
        self._count_cards()
        self.load()

    def undo(self):
        if self.busy or organizer.last_sort() is None or self._tidying():
            return
        self.busy = True
        self.note.hide()
        self.problem.hide()
        self._show_state()
        self.job = work.run(lambda progress: organizer.undo(progress), lambda _: None, self._undone,
                            name="corefold-organizer-undo", busy=True)

    def _undone(self, result):
        if not work.alive(self):
            return
        self.busy = False
        if isinstance(result, Exception):
            log.warning("undo failed: %s", result)
            self._say(tr("Something went wrong while putting the files back."))
        else:
            text = tr("Put 1 file back where it was.") if result.back == 1 else \
                tr("Put {count} files back where they were.", count=result.back)
            if result.renamed:
                text += "\n" + tr("Given a new name, as a file with their old name is there now: {count}",
                                   count=result.renamed)
            if result.gone:
                text += "\n" + tr("Moved or deleted since, so not put back: {count}", count=result.gone)
            if result.changed:
                text += "\n" + tr("A different file is in its place now, so it was left alone: {count}",
                                   count=result.changed)
            if result.failed:
                text += "\n" + tr("Open in a program: {count}. Close it and press Undo again.",
                                   count=len(result.failed))
            self._tell(text)
        self._show_last()
        self._count_cards()
        self.load()
