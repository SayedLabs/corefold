"""A window that shows what's inside an archive and lets you take out, open, add or remove files."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFileIconProvider, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import archives as ar
from .core import APP_NAME, Tool, human_size
from .i18n import tr
from .winutil import set_title_bar_dark

if TYPE_CHECKING:
    from .app import Controller

PATH_ROLE = Qt.UserRole
DIR_ROLE = Qt.UserRole + 1
SORT_ROLE = Qt.UserRole + 2


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

        title = QLabel(src.name)
        title.setObjectName("h2")
        self.info = QLabel()
        self.info.setObjectName("muted")
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search inside the archive"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)

        self.tree = QTreeWidget()
        self.tree.setObjectName("history")
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
        self.b_add = btn(tr("Add files…"), self._add)
        self.b_del = btn(tr("Remove"), self._delete, "danger")
        editable = ar.can_edit(src)
        self.b_add.setVisible(editable)
        self.b_del.setVisible(editable)
        row = QHBoxLayout()
        for b in (self.b_all, self.b_sel, self.b_open, self.b_test):
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
        lay.addLayout(row)
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
            lock = " 🔒" if e.encrypted else ""
            item = Row([name + lock, human_size(e.size), human_size(e.packed) if e.packed else "", e.modified[:16]])
            item.setData(1, SORT_ROLE, e.size)
            item.setData(2, SORT_ROLE, e.packed)
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
        self.info.setText(text)
        self._buttons()

    def reload(self):
        try:
            self.set_entries(ar.list_entries(self.src, self.password))
        except Exception as e:
            self.info.setText(tr("Couldn't read the archive: {error}", error=e))

    def _filter(self, text: str):
        text = text.strip().lower()

        def visit(item: QTreeWidgetItem) -> bool:
            match = not text or text in item.text(0).lower()
            child_match = False
            for i in range(item.childCount()):
                child_match = visit(item.child(i)) or child_match
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

    def _run(self, key: str, desc: str, fn, items=None, after=None):
        tool = Tool(key, desc, desc, fn, batch=True, renames=False)
        self.c.start_job(tool, items or [self.src], {"password": self.password}, after=after)

    def _pick_folder(self) -> str:
        return QFileDialog.getExistingDirectory(self, tr("Extract to"), str(self.src.parent))

    def _extract_all(self):
        dest = self._pick_folder()
        if dest:
            self._run("archive:extract", tr("Extract"), lambda items, ctx: ar.extract(
                self.src, _with(ctx, dest=dest), mode="folder"))

    def _extract_selected(self):
        members = self._selected_paths()
        dest = self._pick_folder() if members else ""
        if dest:
            self._run("archive:extract", tr("Extract"), lambda items, ctx: ar.extract(
                self.src, _with(ctx, dest=dest, members=members)))

    def _open(self, item):
        if not item or item.data(0, DIR_ROLE):
            return
        member = item.data(0, PATH_ROLE)
        self._run("archive:open", tr("Open"), lambda items, ctx: ar.extract_for_opening(self.src, member, ctx),
                  after="open")

    def _test(self):
        self._run("archive:test", tr("Test"), lambda items, ctx: ar.test_archive(self.src, ctx))

    def _add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Add to {name}", name=self.src.name), str(self.src.parent))
        if paths:
            self._run("archive:add", tr("Add to {name}", name=self.src.name),
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
            self._run("archive:delete", tr("Remove from {name}", name=self.src.name),
                      lambda items, ctx: ar.delete_entries(self.src, members, ctx), after="reload")

    # ---- drag files in to add them

    def dragEnterEvent(self, e):
        if ar.can_edit(self.src) and e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if p.exists() and p.resolve() != self.src.resolve()]
        if paths:
            self._run("archive:add", tr("Add to {name}", name=self.src.name),
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

