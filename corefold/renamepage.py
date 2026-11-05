"""The Rename page: drop files (or a folder), set a few rules, and see every new name before anything changes.
The renaming is one job (moretools.rename_files): all or nothing, and undone from the popup or History."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QKeySequence, QShortcut
from PySide6.QtWidgets import QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, \
    QPushButton, QScrollArea, QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from . import i18n, renamer, work
from .atomic.widgets import Dropdown, Switch
from .core import Tool, short_name
from .i18n import ltr, tr
from .panel import DropZone

if TYPE_CHECKING:
    from .app import Controller

log = logging.getLogger("corefold")

MAX_FILES = 20000      # (more than this at once is a job for a script, not a list on a page)
SHOWN = 2000           # rows drawn in the list; the rest are renamed all the same

NAMES = (("keep", "Keep the name"), ("new", "A new name"), ("song", "The song's artist and title"))
CASES = (("keep", "As they are"), ("lower", "all small letters"), ("upper", "ALL CAPITALS"),
         ("title", "Each Word Capital"), ("sentence", "First letter capital"))
DATES = (("none", "No date"), ("taken", "The day the photo was taken"), ("changed", "The day it was last changed"))
NUMBERS = (("none", "No numbers"), ("before", "Before the name"), ("after", "After the name"))
PLACES = (("before", "Before the name"), ("after", "After the name"))
ORDERS = (("name", "By name"), ("date", "By date"), ("size", "By size"), ("added", "As added"))
EXTS = (("keep", "As it is"), ("lower", "small letters"), ("upper", "CAPITALS"))

RENAME = None


def rename_tool() -> Tool:
    global RENAME
    if RENAME is None:
        from . import moretools as mt
        RENAME = Tool("rename:rules", tr("Rename"), tr("Rename the files"), mt.rename_files, batch=True,
                      renames=False)
    return RENAME


def _drop(items) -> Dropdown:
    d = Dropdown()
    for key, text in items:
        d.addItem(tr(text), key)
    return d


def _label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
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


class RenamePage(QWidget):
    """Corefold Rename in the window."""

    def __init__(self, controller: "Controller", header):
        super().__init__()
        self.c = controller
        self.files: list[Path] = []
        self.facts: dict[Path, dict] = {}
        self.rows: list[renamer.Row] = []
        self.reading = None           # the job reading photo dates or song details
        self.busy = False             # renaming
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)  # (the list follows the typing, without working it out at each key)
        self._timer.timeout.connect(self.update_list)

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
        self.drop = DropZone(None, None, title=tr("Drop files or a folder here"),
                             sub=tr("Set the rules and see every new name before anything changes. You can undo it "
                                    "all afterwards."),
                             hot_title=tr("Let go"),
                             buttons=[(tr("Choose files"), self._choose_files, "primary"),
                                      (tr("Choose a folder"), self._choose_folder)])
        self.drop.dropped.connect(self.add)
        outer.addWidget(self.drop)

        self.body = QWidget()
        body = QHBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        body.addWidget(self._rules_card(), 0)
        body.addWidget(self._list_card(), 1)
        outer.addWidget(self.body)
        outer.addStretch()
        self.body.hide()
        QShortcut(QKeySequence.Delete, self.list, activated=self.remove_selected)
        self._load_rules()

    # ---- the rules

    def _rules_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(360)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        lay.addWidget(_label(tr("Rules"), "h2"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        r = 0

        def row(text: str, *widgets):
            nonlocal r
            grid.addWidget(_label(text, "subtle"), r, 0, Qt.AlignVCenter)
            if len(widgets) == 1:
                grid.addWidget(widgets[0], r, 1)
            else:
                h = QHBoxLayout()
                h.setSpacing(6)
                for w in widgets:
                    h.addWidget(w, 1)
                grid.addLayout(h, r, 1)
            r += 1

        self.name = _drop(NAMES)
        row(tr("Name"), self.name)
        self.base = QLineEdit()
        self.base.setPlaceholderText(tr("e.g. Holiday"))
        self.base_label = _label(tr("New name"), "subtle")
        grid.addWidget(self.base_label, r, 0)
        grid.addWidget(self.base, r, 1)
        r += 1
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText(tr("Text to find"))
        row(tr("Find"), self.find_edit)
        self.replace = QLineEdit()
        self.replace.setPlaceholderText(tr("Nothing: it's taken out"))
        row(tr("Replace with"), self.replace)
        self.match_case = Switch(tr("Capitals must match"))
        self.pattern = Switch(tr("Find is a pattern (regular expression)"))
        self.pattern_note = _label("", "warning", wrap=True)
        self.pattern_note.hide()
        for w in (self.match_case, self.pattern, self.pattern_note):
            grid.addWidget(w, r, 0, 1, 2)
            r += 1
        self.before = QLineEdit()
        self.after = QLineEdit()
        row(tr("Add before"), self.before)
        row(tr("Add after"), self.after)
        self.case = _drop(CASES)
        row(tr("Capitals"), self.case)
        self.date = _drop(DATES)
        self.date_at = _drop(PLACES)
        row(tr("Date"), self.date)
        self.date_at_label = _label("", "subtle")
        grid.addWidget(self.date_at_label, r, 0)
        grid.addWidget(self.date_at, r, 1)
        r += 1
        self.number = _drop(NUMBERS)
        row(tr("Numbers"), self.number)
        self.start = QSpinBox()
        self.start.setRange(0, 999999)
        self.start.setValue(1)
        self.step = QSpinBox()
        self.step.setRange(1, 1000)
        self.step.setPrefix("+")
        self.start_label = _label(tr("Start at"), "subtle")
        grid.addWidget(self.start_label, r, 0)
        h = QHBoxLayout()
        h.setSpacing(6)
        h.addWidget(self.start, 1)
        h.addWidget(self.step, 1)
        grid.addLayout(h, r, 1)
        r += 1
        self.order = _drop(ORDERS)
        self.order_label = _label(tr("Order"), "subtle")
        grid.addWidget(self.order_label, r, 0)
        grid.addWidget(self.order, r, 1)
        r += 1
        self.ext = _drop(EXTS)
        row(tr("Extension (.jpg)"), self.ext)
        lay.addLayout(grid)
        lay.addStretch()
        lay.addWidget(_button(tr("Start again"), "flat", self.reset_rules), 0, Qt.AlignLeft)
        for w in (self.base, self.find_edit, self.replace, self.before, self.after):
            w.textChanged.connect(self._changed)
        for w in (self.name, self.case, self.date, self.date_at, self.number, self.order, self.ext):
            w.currentIndexChanged.connect(self._changed)
        for w in (self.start, self.step):
            w.valueChanged.connect(self._changed)
        for w in (self.match_case, self.pattern):
            w.toggled.connect(self._changed)
        return card

    def params(self) -> dict:
        return {"mode": "rules", "name": self.name.currentData(), "base": self.base.text(),
                "find": self.find_edit.text(), "replace": self.replace.text(), "match_case": self.match_case.isChecked(),
                "pattern": self.pattern.isChecked(), "case": self.case.currentData(), "before": self.before.text(),
                "after": self.after.text(), "date": self.date.currentData(), "date_at": self.date_at.currentData(),
                "number": self.number.currentData(), "start": self.start.value(), "step": self.step.value(),
                "digits": 0, "ext": self.ext.currentData(), "order": self.order.currentData()}

    def _set_rules(self, p: dict):
        p = {**renamer.DEFAULTS, **p}
        widgets = [self.name, self.case, self.date, self.date_at, self.number, self.order, self.ext, self.base,
                   self.find_edit, self.replace, self.before, self.after, self.start, self.step, self.match_case,
                   self.pattern]
        for w in widgets:
            w.blockSignals(True)
        try:
            for box, key in ((self.name, "name"), (self.case, "case"), (self.date, "date"), (self.date_at, "date_at"),
                             (self.number, "number"), (self.order, "order"), (self.ext, "ext")):
                box.setCurrentIndex(max(0, box.findData(p[key])))
            for edit, key in ((self.base, "base"), (self.find_edit, "find"), (self.replace, "replace"),
                              (self.before, "before"), (self.after, "after")):
                edit.setText(str(p[key]))
            self.start.setValue(int(p["start"]))
            self.step.setValue(max(1, int(p["step"])))
            self.match_case.setChecked(bool(p["match_case"]))
            self.pattern.setChecked(bool(p["pattern"]))
        except (TypeError, ValueError):
            log.debug("Rename's saved rules couldn't be read", exc_info=True)
        finally:
            for w in widgets:
                w.blockSignals(False)
        self._show_rules()

    def _load_rules(self):
        try:
            saved = json.loads(self.c.settings.rename_rules or "{}")
        except ValueError:
            saved = {}
        self._set_rules(saved if isinstance(saved, dict) else {})

    def reset_rules(self):
        self._set_rules({})
        self._changed()

    def _show_rules(self):
        """Only the choices that mean something with the ones made."""
        p = self.params()
        self.base.setVisible(p["name"] == "new")
        self.base_label.setVisible(p["name"] == "new")
        self.date_at.setVisible(p["date"] != "none")
        self.date_at_label.setVisible(p["date"] != "none")
        numbers = p["number"] != "none"
        for w in (self.start, self.step, self.start_label, self.order, self.order_label):
            w.setVisible(numbers)
        problem = renamer.pattern_problem(p)
        self.pattern_note.setText(tr("This pattern can't be read: {why}", why=ltr(problem)) if problem else "")
        self.pattern_note.setVisible(bool(problem))

    def _changed(self, *_):
        self._show_rules()
        p = {k: v for k, v in self.params().items() if k != "mode"}
        self.c.settings.rename_rules = json.dumps(p, ensure_ascii=False)
        self.c.settings_changed()
        self._read_facts()
        self._timer.start()

    # ---- the list

    def _list_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        top = QHBoxLayout()
        self.count = _label("", "h2")
        top.addWidget(self.count, 1)
        top.addWidget(_button(tr("Add files…"), None, self._choose_files))
        top.addWidget(_button(tr("Clear"), "flat", self.clear))
        lay.addLayout(top)
        self.summary = _label("", "subtle", wrap=True)
        lay.addWidget(self.summary)
        self.list = QTreeWidget()
        self.list.setObjectName("renameList")
        self.list.setColumnCount(2)
        self.list.setHeaderLabels([tr("Name now"), tr("New name")])
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)
        self.list.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.list.setAlternatingRowColors(False)
        head = self.list.header()
        head.setSectionResizeMode(0, QHeaderView.Stretch)
        head.setSectionResizeMode(1, QHeaderView.Stretch)
        self.list.setMinimumHeight(380)
        lay.addWidget(self.list, 1)
        self.problem = _label("", "warning", wrap=True)
        self.problem.hide()
        lay.addWidget(self.problem)
        bottom = QHBoxLayout()
        self.remove_btn = _button(tr("Take out of the list"), "flat", self.remove_selected)
        bottom.addWidget(self.remove_btn)
        bottom.addStretch()
        self.go = _button(tr("Rename"), "primary", self.rename)
        bottom.addWidget(self.go)
        lay.addLayout(bottom)
        lay.addWidget(_label(tr("The files themselves get the new names (no copies). Undo puts every name back."),
                             "subtle", wrap=True))
        return card

    def add(self, paths: list):
        """Files are added; a folder adds the files right inside it."""
        new = []
        have = {str(f) for f in self.files}
        for p in paths:
            p = Path(p)
            items = renamer.files_in(p) if p.is_dir() else ([p] if p.exists() else [])
            for f in items:
                if str(f) not in have:
                    have.add(str(f))
                    new.append(f)
        room = MAX_FILES - len(self.files)
        if len(new) > room:
            self.c.tell(tr("Rename"), tr("Up to {count} files at a time.", count=f"{MAX_FILES:,}"))
            new = new[:max(0, room)]
        if not new:
            return
        self.files += new
        self.body.show()
        self._read_facts()
        self.update_list()

    def clear(self):
        self.files = []
        self.rows = []
        self.list.clear()
        self.body.hide()

    def remove_selected(self):
        gone = {item.data(0, Qt.UserRole) for item in self.list.selectedItems()}
        if not gone:
            return
        self.files = [f for f in self.files if str(f) not in gone]
        if not self.files:
            self.clear()
        else:
            self.update_list()

    def _choose_files(self):
        names, _ = QFileDialog.getOpenFileNames(self, tr("Choose files to rename"), str(self._start_folder()))
        if names:
            self.add([Path(n) for n in names])

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, tr("Choose a folder"), str(self._start_folder()))
        if folder:
            self.add([Path(folder)])

    def _start_folder(self) -> Path:
        return self.files[-1].parent if self.files else Path.home()

    def _read_facts(self):
        """Photo dates and song details the rules need, read once per file in the background."""
        need = renamer.needs(self.params())
        todo = [f for f in self.files if need - set(self._kinds(f))]
        if not todo or self.reading is not None:
            return
        self.summary.setText(tr("Reading the files…"))

        def go(progress):
            out = {}
            for i, f in enumerate(todo):
                out[f] = renamer.read_facts(f, need)
                if i % 50 == 49:
                    progress(None)
            return out, need

        def done(answer):
            if not work.alive(self):  # (the page was closed while it worked)
                return
            self.reading = None
            if isinstance(answer, Exception):
                log.warning("Rename couldn't read the files: %s", answer)
                return
            found, kinds = answer
            for f, facts in found.items():
                facts["_kinds"] = sorted(set(self.facts.get(f, {}).get("_kinds", [])) | kinds)
                self.facts[f] = {**self.facts.get(f, {}), **facts}
            self.update_list()
            self._read_facts()  # (the rules may have changed meanwhile)

        self.reading = work.run(go, lambda _p: None, done, name="corefold-rename-read")

    def _kinds(self, f: Path) -> list:
        return self.facts.get(f, {}).get("_kinds", [])

    def update_list(self):
        if not self.files:
            return
        self.files = [f for f in self.files if f.exists()]  # (renamed, moved or undone since)
        if not self.files:
            self.clear()
            return
        p = self.params()
        self.rows = renamer.plan(self.files, p, self.facts)
        theme = self.c.theme
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        changed = problems = 0
        first_why = ""
        items = []
        for row in self.rows:
            state = row.state
            if state == renamer.OK:
                changed += 1
            elif state not in (renamer.SAME,):
                problems += 1
            if len(items) >= SHOWN:
                continue
            item = QTreeWidgetItem([ltr(row.old.name), ltr(row.new.name) if state != renamer.EMPTY else "—"])
            item.setData(0, Qt.UserRole, str(row.old))
            item.setToolTip(0, str(row.old))
            if state == renamer.OK:
                item.setForeground(1, QBrush(theme.accent))
            elif state == renamer.SAME:
                item.setForeground(1, QBrush(theme.muted))
            else:
                why = {renamer.TAKEN: tr("Another file already has this name"),
                       renamer.TWICE: tr("Two files would get this name")}.get(state, tr(row.why))
                first_why = first_why or why
                item.setForeground(1, QBrush(theme.danger or theme.accent))
                item.setToolTip(1, why)
            items.append(item)
        self.list.addTopLevelItems(items)
        self.list.setUpdatesEnabled(True)
        total = len(self.rows)
        self.count.setText(tr("{count} files", count=f"{total:,}") if total != 1 else short_name(self.rows[0].old.name))
        more = tr("The list shows the first {count}.", count=f"{SHOWN:,}") if total > SHOWN else ""
        if self.reading is None:
            self.summary.setText(" ".join(t for t in (tr("{count} get a new name", count=f"{changed:,}"), more) if t))
        if problems:
            self.problem.setText(tr("{count} new names can't be used: {why}", count=f"{problems:,}", why=first_why))
        self.problem.setVisible(bool(problems))
        self.go.setEnabled(bool(changed) and not problems and not self.busy)
        self.go.setText(tr("Rename {count} files", count=f"{changed:,}") if changed > 1 else tr("Rename"))

    def rename(self):
        if self.busy or not self.rows:
            return
        pairs = renamer.pairs(self.rows)
        if not pairs:
            return
        self.busy = True
        self.go.setEnabled(False)
        news = {str(o): n for o, n in pairs}

        def done(ok: bool):
            if not work.alive(self):  # (the page was closed while it worked)
                return
            self.busy = False
            if ok:  # (the list goes on with the new names, so another rule can follow)
                self.files = [news.get(str(f), f) for f in self.files]
                self.facts = {news.get(str(f), f): v for f, v in self.facts.items()}
            self.update_list()

        # (every file goes to the job, not just the ones that change: the numbers count them all, in the same order)
        self.c.start_job(rename_tool(), [r.old for r in self.rows], self.params(), then=done)

    def refresh(self):
        if self.files:
            self.update_list()

    def apply_theme(self, _theme=None):
        if self.files:
            self.update_list()

    def is_rtl(self) -> bool:
        return i18n.is_rtl()
