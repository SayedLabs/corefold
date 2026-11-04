"""Corefold Space: a file or folder to the Recycle Bin with what goes with it (its subtitles, the shortcuts
to it, what's left of downloading it, a same-named folder in app data…). Shown in a sheet inside the
window: what's found is listed with why it goes with it, and only what's ticked goes. Loaded only when
asked for."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, \
    QVBoxLayout

from . import space, work
from .core import human_size, short_name
from .i18n import ltr, tr
from .winutil import reveal_in_explorer

log = logging.getLogger("corefold")

PATH = Qt.UserRole + 1


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


def find_related(path: Path) -> list[space.Related]:
    """What goes with it (on a helper thread): the installed programs are read first, so nothing where
    one of them lives is ever offered."""
    apps = []
    if os.name == "nt":
        try:
            from . import uninstall as un
            apps = un.list_apps(include_store=False)
        except Exception:
            log.warning("couldn't list the installed programs", exc_info=True)
            return []  # (without knowing where programs live, nothing in app data is offered)
    return space.related(path, apps)


class RelatedDialog(QDialog):
    """Lists what goes with it: accepted, chosen is what's ticked besides what was picked."""

    def __init__(self, page, path: Path, size: int):
        super().__init__(page, Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.path = path
        self.size = size
        self.chosen: list[Path] = []
        self.found: list[space.Related] = []
        self.setWindowTitle(tr("With related files"))
        self.setMinimumSize(640, 440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)
        lay.addWidget(_label(tr("Move {name} to the Recycle Bin with what goes with it?",
                                name=ltr(short_name(path.name, 50))), "h2", wrap=True))
        lay.addWidget(_label(tr("What's ticked goes to the Recycle Bin with it, where you can put it back. What only "
                                "might belong to it isn't ticked. Nothing Windows or your programs need is ever "
                                "offered."), "muted", wrap=True))
        self.status = _label(tr("Looking for what goes with it…"), "muted", wrap=True)
        lay.addWidget(self.status)
        self.tree = QTreeWidget()
        self.tree.setObjectName("history")
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setHeaderLabels([tr("Name"), tr("Why"), tr("Size")])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.itemChanged.connect(lambda *_: self._total())
        self.tree.itemDoubleClicked.connect(lambda item, _c: self._reveal(item))
        lay.addWidget(self.tree, 1)
        # the file or folder itself, always going (it's what was asked for)
        me = QTreeWidgetItem([ltr(short_name(path.name, 70)), tr("What you picked"), human_size(size)])
        me.setFlags(Qt.ItemIsEnabled)
        me.setCheckState(0, Qt.Checked)
        me.setToolTip(0, str(path))
        me.setData(0, PATH, str(path))
        me.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
        self.tree.addTopLevelItem(me)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.count = _label("", None, wrap=True)
        bottom.addWidget(self.count, 1)
        bottom.addWidget(_button(tr("Cancel"), None, self.reject))
        self.go_btn = _button(tr("Move to the Recycle Bin"), "primary", self._go)
        self.go_btn.setEnabled(False)
        bottom.addWidget(self.go_btn)
        lay.addLayout(bottom)
        self._total()
        self.job = work.run(lambda _p: find_related(path), lambda _: None, self._found, name="corefold-related")

    def _found(self, found):
        if not work.alive(self):
            return
        if isinstance(found, Exception):
            log.warning("couldn't look for related files", exc_info=found)
            found = []
        self.found = found
        self.tree.blockSignals(True)
        for r in found:
            it = QTreeWidgetItem([ltr(short_name(r.path.name, 70)), tr(space.RELATED_WHY[r.why]), human_size(r.size)])
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            it.setCheckState(0, Qt.Checked if r.sure else Qt.Unchecked)
            it.setToolTip(0, str(r.path))
            if not r.sure:
                it.setToolTip(1, tr("Not ticked: it may not belong to it. Tick it if it does."))
            it.setData(0, PATH, str(r.path))
            it.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
            self.tree.addTopLevelItem(it)
        self.tree.blockSignals(False)
        self.status.setText(tr("Nothing else goes with it: only what you picked moves.") if not found else
                            tr("Double-click one to see it in its folder."))
        self.go_btn.setEnabled(True)
        self._total()

    def _ticked(self) -> list[tuple[Path, int]]:
        out = []
        for i in range(1, self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.checkState(0) == Qt.Checked:
                r = self.found[i - 1]
                out.append((r.path, r.size))
        return out

    def _total(self):
        ticked = self._ticked()
        size = self.size + sum(s for _, s in ticked)
        self.count.setText(tr("{count} items, {size}", count=len(ticked) + 1, size=human_size(size)))

    def _reveal(self, item):
        p = item.data(0, PATH)
        if p:
            reveal_in_explorer(Path(p))

    def _go(self):
        # (checked once more: nothing Windows or a program needs, even if it changed meanwhile)
        self.chosen = [p for p, _ in self._ticked() if not space.protected(p)]
        self.accept()

    def reject(self):
        self.chosen = []
        super().reject()
