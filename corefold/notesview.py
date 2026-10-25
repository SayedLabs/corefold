"""What changed in a version, on screen: the notes block (the Updates page's new version and its Version
history), and the "What's new" window shown once after an update. Every word is shown as plain text."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import UPDATE_REPO, i18n, icons, releasenotes
from .i18n import tr
from .releasenotes import Notes
from .timefmt import locale

# the repository whose release pages are linked (a copy built without one links to Corefold's own)
REPO = UPDATE_REPO or "SayedLabs/corefold"


def _text(words: str, name: str = "", wrap: bool = True) -> QLabel:
    lab = QLabel(words)
    lab.setTextFormat(Qt.PlainText)  # (release notes come from outside: never read as HTML)
    lab.setWordWrap(wrap)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    if name:
        lab.setObjectName(name)
    return lab


def when(notes: Notes) -> str:
    """"Released 24 Oct 2026", or "" when the day isn't known."""
    if not notes.date:
        return ""
    from PySide6.QtCore import QDate
    day = QDate.fromString(notes.date, "yyyy-MM-dd")
    return tr("Released {date}", date=locale().toString(day, "d MMMM yyyy")) if day.isValid() else ""


class NotesBlock(QWidget):
    """A version's notes: each heading, and the changes under it."""

    def __init__(self, notes: Notes, accent: str, parent: QWidget | None = None, limit: int = 0):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        shown = 0
        dpr = self.devicePixelRatioF()
        for part in notes.parts:
            if limit and shown >= limit:
                lay.addWidget(_text(tr("…and more on the release page."), "subtle"))
                break
            if part.heading:
                head = _text(part.heading, "cardTitle")
                head.setContentsMargins(0, 6 if shown else 0, 0, 0)
                lay.addWidget(head)
            for line in part.lines:
                if limit and shown >= limit:
                    break
                row = QHBoxLayout()
                row.setSpacing(10)
                dot = QLabel()
                dot.setPixmap(icons.pixmap("add", 14, accent, dpr))
                dot.setFixedWidth(16)
                row.addWidget(dot, 0, Qt.AlignTop)
                row.addWidget(_text(line), 1)
                lay.addLayout(row)
                shown += 1
        if notes.empty():
            lay.addWidget(_text(tr("The release page has the details."), "muted"))


def open_release_page(notes: Notes):
    url = releasenotes.release_page(REPO, notes)
    if url:
        QDesktopServices.openUrl(QUrl(url))


class VersionHistory(QWidget):
    """Every version's notes kept with Corefold (newest first), each one opened with a click; works without
    the internet. The installed version's are open to begin with. Its rows are made just after it's first
    shown, so the Updates page opens as quickly as before."""

    def __init__(self, current: str, accent: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.accent = accent
        self.current = current
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.rows: list[tuple[Notes, QPushButton, QWidget]] = []
        self._made = False

    def showEvent(self, event):
        super().showEvent(event)
        if not self._made:
            QTimer.singleShot(0, self, self._make)  # (with self: never after it was deleted)

    def _make(self):
        if self._made:
            return
        self._made = True
        lay = self.layout()
        # (one style for every row, set once: a style of its own on each row made the page slow to open)
        self.setStyleSheet('QPushButton[historyRow="true"] { text-align: %s; }' % ("right" if i18n.is_rtl() else "left"))
        for n, notes in enumerate(releasenotes.heads()):  # (each version's changes are read when it's opened)
            if n:
                line = QFrame()
                line.setObjectName("line")
                lay.addWidget(line)
            head = QHBoxLayout()
            head.setSpacing(8)
            toggle = QPushButton()
            toggle.setObjectName("flat")
            toggle.setCursor(Qt.PointingHandCursor)
            toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            toggle.setProperty("historyRow", True)
            head.addWidget(toggle, 1)
            page = QPushButton(tr("Release page"))
            page.setObjectName("flat")
            page.setCursor(Qt.PointingHandCursor)
            page.setToolTip(releasenotes.release_page(REPO, notes))
            page.clicked.connect(lambda _=False, nt=notes: open_release_page(nt))
            head.addWidget(page)
            lay.addLayout(head)
            body = QWidget()
            QVBoxLayout(body).setContentsMargins(12, 2, 12, 10)
            body.hide()
            lay.addWidget(body)
            self.rows.append((notes, toggle, body))
            toggle.clicked.connect(lambda _=False, i=len(self.rows) - 1: self.show_notes(i))
            body._open = False
            self._name(len(self.rows) - 1)
        for i, (notes, _, _) in enumerate(self.rows):
            if notes.version == self.current:
                self.show_notes(i, True)

    def _name(self, i: int):
        notes, toggle, body = self.rows[i]
        name = tr("Corefold {version}", version=notes.version)
        if notes.version == self.current:
            name += "  ·  " + tr("installed")
        date = when(notes)
        arrow = "▾" if body._open else ("◂" if i18n.is_rtl() else "▸")
        toggle.setText(f"{arrow}  {name}" + (f"  ·  {date}" if date else ""))
        toggle.setAccessibleName(name)

    def show_notes(self, i: int, shown: bool | None = None):
        notes, _, body = self.rows[i]
        shown = not body._open if shown is None else shown
        if shown and body.layout().count() == 0:  # (made the first time it's opened)
            full = releasenotes.kept(notes.version, i18n.language()) or notes
            body.layout().addWidget(NotesBlock(full, self.accent, body))
        body._open = shown
        body.setVisible(shown)
        self._name(i)

    def versions(self) -> list[str]:
        self._make()
        return [notes.version for notes, _, _ in self.rows]


class WhatsNew(QDialog):
    """Shown once, the first time Corefold is opened after an update: what's new in this version."""

    def __init__(self, notes: Notes, accent: str, parent: QWidget):
        super().__init__(parent, Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.notes = notes
        self.setWindowTitle(tr("What's new in Corefold {version}", version=notes.version))
        self.setMinimumSize(QSize(520, 420))
        self.resize(600, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(10)
        lay.addWidget(_text(tr("What's new in Corefold {version}", version=notes.version), "h1"))
        line = tr("Your settings and everything Corefold kept for you are as they were.")
        date = when(notes)
        lay.addWidget(_text(f"{date}  ·  {line}" if date else line, "muted"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        inner.setObjectName("scrollContent")
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 4, 8, 4)
        il.addWidget(NotesBlock(notes, accent, inner))
        il.addStretch()
        scroll.setWidget(inner)
        scroll.viewport().setAutoFillBackground(False)
        lay.addWidget(scroll, 1)
        row = QHBoxLayout()
        page = QPushButton(tr("Release page"))
        page.clicked.connect(lambda: open_release_page(notes))
        row.addWidget(page)
        row.addStretch()
        go = QPushButton(tr("Continue"))
        go.setObjectName("primary")
        go.setDefault(True)
        go.clicked.connect(self.accept)
        row.addWidget(go)
        for b in (page, go):
            b.setCursor(Qt.PointingHandCursor)
        lay.addLayout(row)
