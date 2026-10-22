"""A window that shows what's inside an archive and lets you take out, open, add or remove files."""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QFileSystemWatcher, QMimeData, Qt, QTimer, QUrl
from PySide6.QtGui import QDrag, QGuiApplication, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QFileIconProvider, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from . import archives as ar
from .core import APP_NAME, Tool, human_size, short_name
from .i18n import tr
from .winutil import set_title_bar_dark

if TYPE_CHECKING:
    from .app import Controller

PATH_ROLE = Qt.UserRole
DIR_ROLE = Qt.UserRole + 1
SORT_ROLE = Qt.UserRole + 2
DRAG_LIMIT = 1 << 30  # bigger than this (unpacked): use Extract, which shows progress and can be cancelled


class Row(QTreeWidgetItem):
    """Sorts folders first, and sizes as numbers (not as text)."""

    def __lt__(self, other):
        tree = self.treeWidget()
        col = tree.sortColumn() if tree else 0
        a_dir, b_dir = bool(self.data(0, DIR_ROLE)), bool(other.data(0, DIR_ROLE))
        if a_dir != b_dir:
            ascending = not tree or tree.header().sortIndicatorOrder() == Qt.AscendingOrder
            return a_dir if ascending else b_dir
        if col in (1, 2):
            return (self.data(col, SORT_ROLE) or 0) < (other.data(col, SORT_ROLE) or 0)
        return self.text(col).lower() < other.text(col).lower()


class ArchiveTree(QTreeWidget):
    """The archive's contents; selected files can be dragged out into any folder."""

    def __init__(self, browser: "ArchiveBrowser"):
        super().__init__()
        self.browser = browser

    def startDrag(self, actions):
        self.browser._drag_out()


class ArchiveBrowser(QWidget):
    def __init__(self, controller: "Controller", src: Path, password: str, entries: list[ar.Entry], icon: QIcon):
        super().__init__()
        self.c, self.src, self.password = controller, src, password
        self.setObjectName("window")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"{src.name} - {APP_NAME}")
        self.setWindowIcon(icon)
        self.resize(820, 560)
        self.icons = QFileIconProvider()
        self._lock_icon = None  # made the first time a locked file shows up

        title = QLabel(short_name(src.name, 70))
        title.setToolTip(src.name)
        title.setObjectName("h2")
        self.info = QLabel()
        self.info.setObjectName("muted")
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search inside the archive"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)

        self.tree = ArchiveTree(self)
        self.tree.setObjectName("history")
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragOnly)
        self.tree.setHeaderLabels([tr("Name"), tr("Size"), tr("Packed"), tr("Modified")])
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.itemDoubleClicked.connect(lambda item, _col: self._open(item))
        self.tree.itemSelectionChanged.connect(self._buttons)

        def btn(text, fn, name=None):
            b = QPushButton(text)
            if name:
                b.setObjectName(name)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            return b

        self.b_all = btn(tr("Extract all…"), self._extract_all, "primary")
        self.b_sel = btn(tr("Extract selected…"), self._extract_selected)
        self.b_open = btn(tr("Open"), lambda: self._open(self.tree.currentItem()))
        self.b_test = btn(tr("Test"), self._test)
        self.b_repair = btn(tr("Rescue files…"), self._repair)
        self.b_repair.setToolTip(tr("For a damaged archive: makes a new ZIP with every file that can still be read. "
                                    "The archive itself isn't changed."))
        self.nested = QCheckBox(tr("Also unpack archives inside"))
        self.nested.setToolTip(tr("Archives found inside are unpacked too, each into its own folder."))
        self.b_add = btn(tr("Add files…"), self._add)
        self.b_del = btn(tr("Remove"), self._delete, "danger")
        editable = ar.can_edit(src)
        self.b_add.setVisible(editable)
        self.b_del.setVisible(editable)
        row = QHBoxLayout()
        for b in (self.b_all, self.b_sel, self.b_open, self.b_test, self.b_repair):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(self.b_add)
        row.addWidget(self.b_del)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(title)
        lay.addWidget(self.info)
        lay.addWidget(self.search)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.nested)
        lay.addWidget(self._changes_bar())
        lay.addLayout(row)

        # files opened from the archive and changed: offered to be saved back into it
        self.watched: dict[str, tuple[str, tuple]] = {}  # temp file -> (path inside, how it was)
        self.changed: dict[str, Path] = {}               # path inside -> changed file, waiting for an answer
        self.watcher: QFileSystemWatcher | None = None
        self.check_timer = QTimer(self)
        self.check_timer.setSingleShot(True)
        self.check_timer.timeout.connect(self._check_changes)
        self.set_entries(entries)

    # ---- contents

    def set_entries(self, entries: list[ar.Entry]):
        self.entries = entries
        self.tree.clear()
        self.tree.setSortingEnabled(False)
        folders: dict[str, QTreeWidgetItem] = {}

        def folder(path: str) -> QTreeWidgetItem | None:
            if not path:
                return None
            if path in folders:
                return folders[path]
            parent_path, _, name = path.rpartition("/")
            parent = folder(parent_path)
            item = Row([name, "", "", ""])
            item.setIcon(0, self.icons.icon(QFileIconProvider.Folder))
            item.setData(0, PATH_ROLE, path)
            item.setData(0, DIR_ROLE, True)
            (parent.addChild(item) if parent else self.tree.addTopLevelItem(item))
            folders[path] = item
            return item

        files = size = 0
        for e in entries:
            path = e.path.strip("/")
            if not path:
                continue
            if e.is_dir:
                item = folder(path)
                if e.modified:
                    item.setText(3, e.modified[:16])
                continue
            parent_path, _, name = path.rpartition("/")
            parent = folder(parent_path)
            item = Row([name, human_size(e.size), human_size(e.packed) if e.packed else "", e.modified[:16]])
            item.setData(1, SORT_ROLE, e.size)
            item.setData(2, SORT_ROLE, e.packed)
            if e.encrypted:  # locked with a password
                if self._lock_icon is None:
                    from . import icons as line_icons
                    self._lock_icon = line_icons.icon("lock_closed", "#7e8c89")
                item.setIcon(0, self._lock_icon)
            else:
                item.setIcon(0, self.icons.icon(QFileIconProvider.File))
            item.setData(0, PATH_ROLE, path)
            item.setData(0, DIR_ROLE, False)
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
            (parent.addChild(item) if parent else self.tree.addTopLevelItem(item))
            files += 1
            size += e.size
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        if len(folders) < 30:
            self.tree.expandAll()
        try:
            packed = self.src.stat().st_size
        except OSError:
            packed = 0
        text = tr("Files: {count} · {size} unpacked · {packed} archive", count=files, size=human_size(size),
                  packed=human_size(packed))
        if size and packed < size:
            text += " · " + tr("{percent}% smaller packed", percent=100 - round(packed * 100 / size))
        self.info.setText(text + " · " + tr("Drag files out to any folder"))
        self.nested.setVisible(ar.holds_archives(entries))
        self._buttons()

    def reload(self):
        try:
            self.set_entries(ar.list_entries(self.src, self.password))
        except Exception as e:
            self.info.setText(tr("Couldn't read the archive: {error}", error=e))

    def _filter(self, text: str):
        text = text.strip().lower()

        def visit(item: QTreeWidgetItem, inside_match: bool = False) -> bool:
            # a folder that matches shows everything in it
            match = not text or inside_match or text in item.text(0).lower()
            child_match = False
            for i in range(item.childCount()):
                child_match = visit(item.child(i), match and bool(text)) or child_match
            show = match or child_match
            item.setHidden(not show)
            if text and child_match:
                item.setExpanded(True)
            return show
        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    def _selected_paths(self) -> list[str]:
        """Selected files, plus everything inside selected folders."""
        paths: list[str] = []
        for item in self.tree.selectedItems():
            path = item.data(0, PATH_ROLE)
            if item.data(0, DIR_ROLE):
                prefix = path + "/"
                paths += [e.path.strip("/") for e in self.entries if e.path.strip("/").startswith(prefix)]
                paths.append(path)
            else:
                paths.append(path)
        return sorted(set(paths))

    def _buttons(self):
        sel = self.tree.selectedItems()
        self.b_sel.setEnabled(bool(sel))
        self.b_del.setEnabled(bool(sel))
        current = self.tree.currentItem()
        self.b_open.setEnabled(bool(current and not current.data(0, DIR_ROLE)))

    # ---- actions (run in the background with the usual progress popup)

    def _run(self, key: str, desc: str, fn, items=None, after=None, params: dict | None = None):
        tool = Tool(key, desc, desc, fn, batch=True, renames=False, password=True)
        self.c.start_job(tool, items or [self.src], {"password": self.password, **(params or {})}, after=after)

    def _pick_folder(self) -> str:
        return QFileDialog.getExistingDirectory(self, tr("Extract to"), str(self.src.parent))

    def _extract_all(self):
        dest = self._pick_folder()
        if dest:
            nested = self.nested.isVisible() and self.nested.isChecked()
            self._run("archive:extract", tr("Extract"), lambda items, ctx: ar.extract(
                self.src, _with(ctx, dest=dest, nested=nested), mode="folder"))

    def _extract_selected(self):
        members = self._selected_paths()
        dest = self._pick_folder() if members else ""
        if dest:
            nested = self.nested.isVisible() and self.nested.isChecked()
            self._run("archive:extract", tr("Extract"), lambda items, ctx: ar.extract(
                self.src, _with(ctx, dest=dest, members=members, nested=nested)))

    def _open(self, item):
        if not item or item.data(0, DIR_ROLE):
            return
        member = item.data(0, PATH_ROLE)
        self._run("archive:open", tr("Open"), lambda items, ctx: ar.extract_for_opening(self.src, member, ctx),
                  after="open", params={"member": member})

    def _test(self):
        self._run("archive:test", tr("Test"), lambda items, ctx: ar.test_archive(self.src, ctx))

    def _repair(self):
        self.c.repair_archive(self.src, self.password)

    # ---- dragging files out

    def _top_selected(self) -> list[str]:
        """Selected items that aren't inside another selected folder."""
        chosen = [i.data(0, PATH_ROLE) for i in self.tree.selectedItems()]
        return [p for p in chosen if not any(p != q and p.startswith(q + "/") for q in chosen)]

    def _drag_out(self):
        members = self._selected_paths()
        tops = self._top_selected()
        if not members or not tops:
            return
        wanted = set(members)
        total = sum(e.size for e in self.entries if e.path.strip("/") in wanted)
        if total > DRAG_LIMIT:
            QMessageBox.information(self, tr("Extract"), tr("That's a lot to drag ({size}). Use Extract instead: it "
                                                             "shows the progress.", size=human_size(total)))
            return
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            folder = ar.extract_for_drag(self.src, members, self.password)
        except Exception as e:
            QGuiApplication.restoreOverrideCursor()
            QMessageBox.warning(self, tr("Couldn't take the files out"), str(e) or tr("the archive is damaged or "
                                                                                      "incomplete"))
            return
        QGuiApplication.restoreOverrideCursor()
        urls = []
        for p in tops:
            path = folder / p.replace("/", os.sep)
            try:
                if path.exists() and path.resolve().is_relative_to(folder.resolve()):
                    urls.append(QUrl.fromLocalFile(str(path)))
            except OSError:
                pass
        if not urls:
            return
        mime = QMimeData()
        mime.setUrls(urls)
        drag = QDrag(self.tree)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)  # Explorer copies them; the temporary copies are tidied away later

    # ---- a file opened from the archive was changed: put it back?

    def _changes_bar(self) -> QFrame:
        self.bar = QFrame()
        self.bar.setObjectName("card")
        bl = QHBoxLayout(self.bar)
        bl.setContentsMargins(12, 8, 12, 8)
        self.bar_text = QLabel()
        self.bar_text.setWordWrap(True)
        self.bar_save = QPushButton(tr("Save into the archive"))
        self.bar_save.setObjectName("primary")
        self.bar_save.clicked.connect(self._save_back)
        self.bar_skip = QPushButton(tr("Not now"))
        self.bar_skip.clicked.connect(self._skip_changes)
        for b in (self.bar_save, self.bar_skip):
            b.setCursor(Qt.PointingHandCursor)
        bl.addWidget(self.bar_text, 1)
        bl.addWidget(self.bar_save)
        bl.addWidget(self.bar_skip)
        self.bar.hide()
        return self.bar

    @staticmethod
    def _stamp(path: Path) -> tuple:
        try:
            st = path.stat()
            return st.st_mtime_ns, st.st_size
        except OSError:
            return ()

    def opened(self, member: str, path: Path):
        """A file from the archive was opened in its app: notice when it's saved there."""
        self.watched[str(path)] = (member, self._stamp(path))
        if self.watcher is None:
            self.watcher = QFileSystemWatcher(self)
            self.watcher.fileChanged.connect(lambda _p: self.check_timer.start(800))
            self.watcher.directoryChanged.connect(lambda _p: self.check_timer.start(800))
        # the folder too: many apps save by writing a new file and swapping it in
        self.watcher.addPath(str(path))
        self.watcher.addPath(str(path.parent))

    def _check_changes(self):
        for name, (member, before) in list(self.watched.items()):
            path = Path(name)
            now = self._stamp(path)
            if not now or now == before:
                continue
            self.watched[name] = (member, now)
            if self.watcher is not None and name not in self.watcher.files():
                self.watcher.addPath(name)
            self.changed[member] = path
        self._show_bar()

    def _show_bar(self):
        if not self.changed:
            self.bar.hide()
            return
        names = ", ".join(short_name(m.rpartition("/")[2], 40) for m in self.changed)
        if ar.can_edit(self.src):
            self.bar_text.setText(tr("You changed {name}. Save the new version into the archive?", name=names))
            self.bar_save.show()
        else:
            self.bar_text.setText(tr("You changed {name}, but this kind of archive can't be changed. Save the file "
                                     "somewhere else from its app to keep your changes.", name=names))
            self.bar_save.hide()
        self.bar_skip.setText(tr("Not now") if ar.can_edit(self.src) else tr("OK"))
        self.bar.show()

    def _save_back(self):
        changed, self.changed = self.changed, {}
        self.bar.hide()
        if not changed:
            return

        def save_all(items, ctx):  # one after the other: they all change the same archive
            result = None
            for member, path in changed.items():
                ctx.check()
                result = ar.update_member(self.src, member, path, ctx)
            return result
        self._run("archive:update", tr("Save into {name}", name=short_name(self.src.name, 30)), save_all,
                  after="reload")

    def _skip_changes(self):
        self.changed = {}
        self.bar.hide()

    def _add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Add to {name}", name=self.src.name), str(self.src.parent))
        if paths:
            self._run("archive:add", tr("Add to {name}", name=short_name(self.src.name, 30)),
                      lambda items, ctx: ar.add_files(self.src, [Path(p) for p in paths], ctx), after="reload")

    def _delete(self):
        members = self._selected_paths()
        if not members:
            return
        box = QMessageBox(QMessageBox.Question, tr("Remove from archive"),
                          tr("Remove these items from {name}? ({count})", name=self.src.name, count=len(members))
                          + "\n" + tr("This changes the archive and can't be undone."), parent=self)
        remove = box.addButton(tr("Remove"), QMessageBox.DestructiveRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        chosen = box.clickedButton() is remove
        box.deleteLater()
        if chosen:
            self._run("archive:delete", tr("Remove from {name}", name=short_name(self.src.name, 30)),
                      lambda items, ctx: ar.delete_entries(self.src, members, ctx), after="reload")

    # ---- drag files in to add them

    def dragEnterEvent(self, e):
        if ar.can_edit(self.src) and e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        if e.source() is not None:  # files dragged out of this window and let go over it again
            return
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if p.exists() and p.resolve() != self.src.resolve()]
        if paths:
            self._run("archive:add", tr("Add to {name}", name=short_name(self.src.name, 30)),
                      lambda items, ctx: ar.add_files(self.src, paths, ctx),
                      after="reload")

    def showEvent(self, e):
        super().showEvent(e)
        self.setAcceptDrops(ar.can_edit(self.src))
        set_title_bar_dark(self, self.c.theme.dark)


def _with(ctx, **params):
    """Same job context with extra settings."""
    from .core import Ctx
    return Ctx(ctx.options, ctx._progress, ctx.cancel_event, dict(ctx.params, **params), ctx.index, ctx.total)

