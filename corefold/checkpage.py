"""The Check page: drop a file and see whether it's the real one. Its fingerprints are worked out in the background
(checker.py), then compared with a code pasted from the download page, with another file, or, for a list of codes
(SHA256SUMS, file.sha256...), every file the list names is checked."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QFont, QGuiApplication
from PySide6.QtWidgets import QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, \
    QProgressBar, QPushButton, QScrollArea, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from . import checker, work
from .core import human_size
from .i18n import ltr, tr
from .panel import DropZone

if TYPE_CHECKING:
    from .app import Controller

log = logging.getLogger("corefold")

STATES = {checker.SAME: "Matches", checker.DIFFERENT: "Doesn't match", checker.MISSING: "Not found"}


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


class CheckPage(QWidget):
    """Corefold Check in the window."""

    def __init__(self, controller: "Controller", header):
        super().__init__()
        self.c = controller
        self.file: Path | None = None
        self.found: dict[str, str] = {}
        self.job = None
        self.cancel = threading.Event()
        self.verdict_ok: bool | None = None    # (how the verdict is coloured: matches, doesn't, or plain)
        self.compare_ok: bool | None = None

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
        self.drop = DropZone(None, None, title=tr("Drop a file to check it"),
                             sub=tr("See its fingerprint and compare it with the code on the download page, or drop "
                                    "a list of codes (SHA256SUMS) to check every file it names. Nothing leaves your "
                                    "PC."),
                             hot_title=tr("Let go"),
                             buttons=[(tr("Choose a file"), self._choose, "primary")])
        self.drop.dropped.connect(self.add)
        outer.addWidget(self.drop)
        outer.addWidget(self._file_card())
        outer.addWidget(self._list_card())
        outer.addStretch()
        self.file_card.hide()
        self.list_card.hide()

    # ---- one file

    def _file_card(self) -> QFrame:
        card = self.file_card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        top = QHBoxLayout()
        self.name = _label("", "h2")
        self.name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.name, 1)
        self.size_label = _label("", "subtle")
        top.addWidget(self.size_label)
        lay.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        lay.addWidget(self.bar)
        self.status = _label("", "subtle", wrap=True)
        lay.addWidget(self.status)

        lay.addWidget(_label(tr("Paste the code from the download page"), "section"))
        self.paste = QLineEdit()
        self.paste.setPlaceholderText(tr("For example 3a7bd3e2360a3d29eea436fcfb7e44c735d117c4…"))
        self.paste.setClearButtonEnabled(True)
        self.paste.textChanged.connect(self.update_verdict)
        lay.addWidget(self.paste)
        self.verdict = _label("", wrap=True)
        self.verdict.setTextFormat(Qt.RichText)
        lay.addWidget(self.verdict)

        lay.addWidget(_label(tr("Its fingerprints"), "section"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.codes: dict[str, QLabel] = {}
        for r, algo in enumerate(checker.ALGOS):
            grid.addWidget(_label(checker.NAMES[algo], "subtle"), r, 0)
            code = _label("…")
            code.setFont(mono)
            code.setTextInteractionFlags(Qt.TextSelectableByMouse)
            code.setWordWrap(True)
            code.setLayoutDirection(Qt.LeftToRight)
            code.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.codes[algo] = code
            grid.addWidget(code, r, 1)
            copy = _button(tr("Copy"), "ghost", lambda _=False, a=algo: self.copy(a))
            grid.addWidget(copy, r, 2)
        lay.addLayout(grid)

        row = QHBoxLayout()
        self.compare_btn = _button(tr("Compare with another file"), None, self._compare)
        row.addWidget(self.compare_btn)
        row.addStretch()
        row.addWidget(_button(tr("Check another file"), "ghost", self._choose))
        lay.addLayout(row)
        self.compared = _label("", wrap=True)
        lay.addWidget(self.compared)
        return card

    def add(self, paths: list):
        files = [Path(p) for p in paths if Path(p).is_file()]
        if not files:
            if paths:
                self.c.tell(tr("Check"), tr("Pick a file, not a folder"))
            return
        f = files[0]
        if checker.is_list(f):
            self.check_list(f)
        else:
            self.check_file(f)

    def _choose(self):
        start = self.file.parent if self.file else Path.home() / "Downloads"
        name, _ = QFileDialog.getOpenFileName(self, tr("Choose a file to check"), str(start if start.is_dir()
                                                                                       else Path.home()))
        if name:
            self.add([Path(name)])

    def _stop_job(self):
        self.cancel.set()
        self.cancel = threading.Event()
        self.job = None

    def check_file(self, f: Path):
        self._stop_job()
        self.list_card.hide()
        self.file_card.show()
        self.file = f
        self.found = {}
        self.name.setText(ltr(f.name))
        try:
            self.size_label.setText(human_size(f.stat().st_size))
        except OSError:
            self.size_label.setText("")
        for code in self.codes.values():
            code.setText("…")
        self.compared.setText("")
        self.compare_ok = None
        self.compare_btn.setEnabled(False)
        self.bar.setValue(0)
        self.bar.show()
        self.status.setText(tr("Reading the file…"))
        self.update_verdict()
        cancel = self.cancel

        def go(progress):
            return checker.fingerprints(f, progress, cancel)

        def step(p):
            if cancel is self.cancel and isinstance(p, float):
                self.bar.setValue(int(p * 1000))

        def done(answer):
            if cancel is not self.cancel or f != self.file:
                return
            self.job = None
            self.bar.hide()
            if isinstance(answer, checker.Cancelled):
                return
            if isinstance(answer, Exception):
                log.info("couldn't read %s", f, exc_info=answer)
                self.status.setText(tr("Couldn't read the file: {why}", why=str(answer)))
                return
            self.found = answer
            self.status.setText(tr("Done. Compare a code with these below, or paste it above."))
            for algo, code in self.codes.items():
                text = answer.get(algo, "")  # (SHA-512 on two lines: one long word would make the page too wide)
                code.setText("\n".join(text[i:i + 64] for i in range(0, len(text), 64)))
            self.compare_btn.setEnabled(True)
            self.update_verdict()

        self.job = work.run(go, step, done, name="corefold-check")

    def update_verdict(self, *_):
        text = self.paste.text().strip()
        self.verdict_ok = None
        if not text:
            self.verdict.setText("")
            return
        algo, ok = checker.matches(self.found, text)
        if algo is None:
            self.verdict.setText(tr("That isn't a code: a code is 32, 40, 64 or 128 letters and digits."))
        elif not self.found:
            self.verdict.setText(tr("Wait a moment: the file is still being read."))
        elif ok:
            self.verdict_ok = True
            self.verdict.setText("<b>" + tr("It matches ({kind}).", kind=checker.NAMES[algo]) + "</b> " +
                                 tr("This is the file the website means, and it wasn't damaged."))
        else:
            self.verdict_ok = False
            self.verdict.setText("<b>" + tr("It doesn't match ({kind}).", kind=checker.NAMES[algo]) + "</b> " +
                                 tr("This isn't the file the website means, or it was damaged on the way. Don't open "
                                    "it: download it again from the website itself."))
        self._colour()

    def copy(self, algo: str):
        code = self.found.get(algo)
        if code:
            QGuiApplication.clipboard().setText(code)
            self.c.tell(tr("Copied"), checker.NAMES[algo])

    def _compare(self):
        if self.file is None or not self.found:
            return
        name, _ = QFileDialog.getOpenFileName(self, tr("Choose the file to compare it with"), str(self.file.parent))
        if not name:
            return
        other = Path(name)
        first = self.file
        mine = self.found.get("sha256")
        self.compared.setText(tr("Comparing…"))
        self.compare_ok = None
        self._colour()

        def go(_progress):
            if other.stat().st_size != first.stat().st_size:
                return False
            return checker.fingerprints(other, None, None, ("sha256",))["sha256"] == mine

        def done(same):
            if first != self.file:
                return
            if isinstance(same, Exception):
                self.compared.setText(tr("Couldn't read the file: {why}", why=str(same)))
                return
            self.compare_ok = bool(same)
            self.compared.setText(tr("The two files are exactly the same.") if same else
                                  tr("The two files are different."))
            self._colour()

        work.run(go, lambda _p: None, done, name="corefold-check-compare")

    # ---- a list of codes

    def _list_card(self) -> QFrame:
        card = self.list_card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        self.list_name = _label("", "h2")
        lay.addWidget(self.list_name)
        self.list_bar = QProgressBar()
        self.list_bar.setRange(0, 1000)
        self.list_bar.setTextVisible(False)
        self.list_bar.setFixedHeight(6)
        lay.addWidget(self.list_bar)
        self.list_summary = _label("", "subtle", wrap=True)
        lay.addWidget(self.list_summary)
        self.list = QTreeWidget()
        self.list.setRootIsDecorated(False)
        self.list.setHeaderLabels([tr("File"), tr("Result")])
        self.list.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.list.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.list.setMinimumHeight(300)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(_button(tr("Check another file"), "ghost", self._choose))
        lay.addLayout(row)
        return card

    def check_list(self, f: Path):
        self._stop_job()
        self.file_card.hide()
        self.list_card.show()
        self.file = f
        self.entries: list[checker.Entry] = []
        self.list.clear()
        self.list_name.setText(ltr(f.name))
        self.list_bar.setValue(0)
        self.list_bar.show()
        self.list_summary.setText(tr("Checking the files it names…"))
        cancel = self.cancel

        def go(progress):
            return checker.check_list(f, lambda p, _name: progress(p), cancel)

        def step(p):
            if cancel is self.cancel and isinstance(p, float):
                self.list_bar.setValue(int(p * 1000))

        def done(answer):
            if cancel is not self.cancel or f != self.file:
                return
            self.job = None
            self.list_bar.hide()
            if isinstance(answer, checker.Cancelled):
                return
            if isinstance(answer, Exception):
                self.list_summary.setText(tr("Couldn't read the file: {why}", why=str(answer)))
                return
            self.entries = answer
            self._fill_list()

        self.job = work.run(go, step, done, name="corefold-check-list")

    def _fill_list(self):
        self.list.clear()
        entries = getattr(self, "entries", [])
        if not entries:
            self.list_summary.setText(tr("This file doesn't hold any codes."))
            return
        same = sum(e.state == checker.SAME for e in entries)
        bad = sum(e.state == checker.DIFFERENT for e in entries)
        missing = sum(e.state == checker.MISSING for e in entries)
        bits = [tr("{same} of {count} files match", same=f"{same:,}", count=f"{len(entries):,}")]
        if bad:
            bits.append(tr("{count} don't match: don't open them, download them again", count=f"{bad:,}"))
        if missing:
            bits.append(tr("{count} aren't in this folder", count=f"{missing:,}"))
        self.list_summary.setText(". ".join(bits) + ".")
        theme = self.c.theme
        for e in entries:
            item = QTreeWidgetItem([ltr(e.name), tr(STATES.get(e.state, ""))])
            colour = theme.accent if e.state == checker.SAME else \
                (theme.danger or theme.accent) if e.state == checker.DIFFERENT else theme.muted
            item.setForeground(1, QBrush(colour))
            self.list.addTopLevelItem(item)

    # ----

    def _colour(self):
        theme = self.c.theme
        for label, ok in ((self.verdict, self.verdict_ok), (self.compared, self.compare_ok)):
            if ok is None:
                label.setStyleSheet("")
            else:
                colour = theme.accent if ok else (theme.danger or theme.accent)
                label.setStyleSheet(f"color: {colour.name()};")

    def refresh(self):
        pass

    def apply_theme(self, _theme=None):
        self._colour()
        if self.list_card.isVisible():
            self._fill_list()

    def stop(self):
        self._stop_job()
