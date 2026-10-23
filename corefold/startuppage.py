"""Corefold Startup's page: every program that starts with Windows, each with a switch."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QFileInfo, QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QButtonGroup, QFileIconProvider, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, \
    QPushButton, QScrollArea, QVBoxLayout, QWidget

from . import i18n, icons, startup, work
from .atomic.widgets import Group, IconButton, SettingRow, Switch
from .i18n import ltr, tr
from .theme import round_menu
from .timefmt import date_text
from .winutil import reveal_in_explorer

if TYPE_CHECKING:
    from .app import Controller

log = logging.getLogger("corefold")

FILTERS = [("", "All"), ("on", "Switched on"), ("off", "Switched off"), ("new", "New")]
# where an entry comes from (English, shown through tr())
WHERE = {"HKCU-Run": "Registry, this user", "HKLM-Run": "Registry, everyone",
         "HKLM-Run32": "Registry, everyone (32-bit)", "User-Folder": "Startup folder, this user",
         "Common-Folder": "Startup folder, everyone"}


def describe(e: startup.Entry) -> str:
    """The line under an entry's name: who made it, where it comes from, and anything to know."""
    bits = []
    if e.own:
        bits.append(tr("This is Corefold itself"))
    elif e.publisher:
        bits.append(ltr(e.publisher))
    bits.append(tr(WHERE.get(e.where, e.where)))
    if not e.on and e.off_since:
        bits.append(tr("Off since {date}", date=date_text(e.off_since, long=True)))
    return _para("  ·  ".join(bits))


def _para(text: str) -> str:
    """In Arabic, a line that starts with a Latin name still reads (and lines up) right to left."""
    return "\u200f" + text if text and i18n.is_rtl() else text


def matches(e: startup.Entry, text: str, show: str) -> bool:
    if show == "on" and not e.on or show == "off" and e.on or show == "new" and not e.new:
        return False
    text = text.strip().lower()
    return not text or any(text in s.lower() for s in (e.name, e.value, e.publisher, e.command))


class EntryRow(QWidget):
    """One program: its icon, name and a line about it, a "…" menu, and its switch."""

    def __init__(self, page: "StartupPage", entry: startup.Entry, icon: QIcon | None):
        super().__init__()
        self.page, self.entry = page, entry
        tile = QLabel()
        tile.setFixedSize(28, 28)
        tile.setAlignment(Qt.AlignCenter)
        if icon is not None and not icon.isNull():
            tile.setPixmap(icon.pixmap(QSize(24, 24), self.devicePixelRatioF()))
        else:
            tile.setPixmap(icons.pixmap("apps", 22, page.c.theme.muted, self.devicePixelRatioF()))
        self.switch = Switch()
        self.switch.setChecked(entry.on)
        self.switch.setAccessibleName(entry.name)
        self.switch.toggled.connect(self._toggled)
        more = IconButton(tr("More"), 30)
        more.setIcon(icons.icon("more_horizontal", page.c.theme.muted))
        more.clicked.connect(lambda: self._menu(more))
        controls = QWidget()
        h = QHBoxLayout(controls)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        h.addWidget(more)
        h.addWidget(self.switch)
        self.row = SettingRow(_para(ltr(entry.name)), describe(entry), controls, tile)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.row)
        pills = QHBoxLayout()
        pills.setContentsMargins(0, 4, 0, 0)
        pills.setSpacing(6)
        for shown, text, name in ((entry.new, tr("New"), "chip"),
                                  (entry.admin and not page.admin, tr("Asks for permission"), "badge"),
                                  (entry.missing, tr("Its program isn't there any more"), "badge")):
            if shown:
                pills.addWidget(_label(text, name))
        if pills.count():  # (under the name and the line about it)
            pills.addStretch()
            self.row.layout().itemAt(1).layout().addLayout(pills)
        self.setToolTip(ltr(entry.command))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self._menu(None, self.mapToGlobal(pos)))

    def _toggled(self, on: bool):
        self.page.switch(self, on)

    def set_checked_quietly(self, on: bool):
        self.switch.blockSignals(True)
        self.switch.setChecked(on)
        self.switch.blockSignals(False)
        self.switch.update()

    def refresh_text(self):
        self.row.description.setText(describe(self.entry))

    def _menu(self, button: QPushButton | None, pos=None):
        e = self.entry
        m = QMenu(self)
        round_menu(m)
        place = e.extra.get("file") or (e.exe if e.exe and os.path.exists(e.exe) else "")
        go = m.addAction(icons.icon("folder_open", self.page.c.theme.muted), tr("Show in folder"))
        go.setEnabled(bool(place))
        go.triggered.connect(lambda: reveal_in_explorer(Path(place)))
        copy = m.addAction(icons.icon("copy", self.page.c.theme.muted), tr("Copy what it runs"))
        copy.triggered.connect(lambda: QGuiApplication.clipboard().setText(e.command))
        if pos is None and button is not None:
            pos = button.mapToGlobal(button.rect().bottomLeft())
        m.exec(pos)


def _key(e: startup.Entry) -> tuple:
    return e.id, e.on, e.command, e.name, e.new, e.missing


def _label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


class StartupPage(QWidget):
    """Corefold Startup in the window: the list, a search box, and All / On / Off / New."""

    def __init__(self, controller: "Controller", header):
        super().__init__()
        self.c = controller
        self.entries: list[startup.Entry] = []
        self.rows: list[EntryRow] = []
        self.kind = ""
        self.loading = False
        self.busy = 0
        self.job = None
        self.switching: list = []
        self.new_ids: set[str] = set()   # marked New this time Corefold runs (kept when looking again)
        self.admin = startup.is_admin()
        self._icons = QFileIconProvider()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(12)
        lay.addLayout(header)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("startupSearch")
        self.search.setPlaceholderText(tr("Search the programs that start with Windows"))
        self.search.setClearButtonEnabled(True)
        self.timer = QTimer(self, singleShot=True, interval=100)
        self.timer.timeout.connect(self.filter)
        self.search.textChanged.connect(lambda _: self.timer.start())
        bar.addWidget(self.search, 1)
        self.filters = QButtonGroup(self)
        self.filters.setExclusive(True)
        self.filter_buttons: dict[str, QPushButton] = {}
        for key, text in FILTERS:
            b = QPushButton(tr(text))
            b.setObjectName("subtab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(key == "")
            b.clicked.connect(lambda _=False, k=key: self._filter(k))
            self.filters.addButton(b)
            self.filter_buttons[key] = b
            bar.addWidget(b)
        self.filter_buttons["new"].hide()  # (only when something is new)
        self.refresh_btn = IconButton(tr("Look again"))
        self.refresh_btn.setIcon(icons.icon("arrow_clockwise", self.c.theme.muted))
        self.refresh_btn.clicked.connect(self.load)
        bar.addWidget(self.refresh_btn)
        lay.addLayout(bar)
        self.problem = _label("", "warning", wrap=True)
        self.problem.hide()
        lay.addWidget(self.problem)
        self.group = Group()
        holder = QWidget()
        holder.setObjectName("scrollContent")  # (see-through, like every page's scrolling part)
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        hl.addWidget(self.group)
        hl.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(holder)
        self.scroll.viewport().setAutoFillBackground(False)
        lay.addWidget(self.scroll, 1)
        self.empty = _label("", "muted", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(80)
        lay.addWidget(self.empty)
        foot = QHBoxLayout()
        self.count = _label("", "muted", wrap=True)
        foot.addWidget(self.count, 1)
        lay.addLayout(foot)
        lay.addWidget(_label(tr("Switching a program off doesn't delete anything: Windows just skips it when it "
                                "starts, the same as in Task Manager. Switch it back on any time."), "subtle",
                             wrap=True))
        self._show_state()

    # ---- reading the list (off the window's thread: it reads every program's file)

    def ensure_loaded(self):
        """Opening the page: read the list again (something may have added itself since)."""
        self.load()

    def load(self):
        if self.loading or self.busy:
            return
        self.loading = True
        self.refresh_btn.setEnabled(False)
        self._show_state()

        def read(_progress):
            found = startup.list_entries()
            startup.mark_new(found)
            return found
        self.job = work.run(read, lambda _: None, self._loaded, name="corefold-startup")

    def _loaded(self, result):
        if not work.alive(self):
            return
        self.loading = False
        self.refresh_btn.setEnabled(True)
        if isinstance(result, Exception):
            log.warning("couldn't read what starts with Windows: %s", result)
            self.entries = []
            self._say(tr("Corefold couldn't read what starts with Windows."))
        else:
            for e in result:
                if e.id in self.new_ids:
                    e.new = True
            self.new_ids |= {e.id for e in result if e.new}
            same = [_key(e) for e in result] == [_key(e) for e in self.entries] and self.rows
            self.entries = result
            self.problem.hide()
            if same:  # (nothing changed: the rows stay, without a flicker)
                for row, e in zip(self.rows, result):
                    row.entry = e
                self._show_state()
                return
        self._fill()

    def _fill(self):
        for row in self.rows:
            row.hide()
            row.deleteLater()
        self.rows = []
        for e in self.entries:
            row = EntryRow(self, e, self._icon(e))
            self.group.add(row)
            self.rows.append(row)
        any_new = any(e.new for e in self.entries)
        self.filter_buttons["new"].setVisible(any_new)
        if not any_new and self.kind == "new":
            self._filter("")
        self.filter()

    def _icon(self, e: startup.Entry) -> QIcon | None:
        for path in (e.exe, e.extra.get("file", "")):
            if path and os.path.isfile(path):
                ic = self._icons.icon(QFileInfo(path))
                if not ic.isNull():
                    return ic
        return None

    # ---- search and filters

    def _filter(self, key: str):
        self.kind = key
        self.filter_buttons[key].setChecked(True)
        self.filter()

    def filter(self):
        text = self.search.text()
        for row in self.rows:
            row.setVisible(matches(row.entry, text, self.kind))
        self.group.update()
        self._show_state()

    def _show_state(self):
        shown = sum(1 for r in self.rows if not r.isHidden())
        if self.loading and not self.rows:
            self.empty.setText(tr("Looking at what starts with Windows…"))
        elif not self.entries:
            self.empty.setText(tr("Nothing starts with Windows."))
        elif not shown:
            self.empty.setText(tr("Nothing matches."))
        self.empty.setVisible((self.loading and not self.rows) or not shown)
        self.scroll.setVisible(shown > 0)
        on = sum(1 for e in self.entries if e.on)
        off = len(self.entries) - on
        self.count.setText(tr("Starting with Windows: {count}", count=on) +
                           ("  ·  " + tr("Switched off: {count}", count=off) if off else ""))
        self.count.setVisible(bool(self.entries))

    # ---- switching on and off

    def switch(self, row: EntryRow, on: bool):
        """A switch was flipped: change it (off the window's thread, as Windows may ask for permission
        first), and put the switch back if it didn't work."""
        e = row.entry
        row.switch.setEnabled(False)
        self.busy += 1
        self.refresh_btn.setEnabled(False)
        # (the job is kept until its answer is in: an answer whose job was thrown away never arrives)
        job = work.run(lambda _p: startup.set_on(e, on), lambda _: None,
                       lambda result, r=row, want=on: self._switched(r, want, result), name="corefold-startup-switch")
        self.switching.append(job)

    def _switched(self, row: EntryRow, want: bool, result):
        self.busy -= 1
        self.switching = [j for j in self.switching if j.is_alive()]
        if not work.alive(self):
            return
        self.refresh_btn.setEnabled(not self.busy and not self.loading)
        if not work.alive(row):
            return
        row.switch.setEnabled(True)
        if result is True:
            self.problem.hide()
            row.refresh_text()
            if row.entry.own:
                self._sync_own_setting()
        else:
            if isinstance(result, Exception):
                log.warning("couldn't switch %s: %s", row.entry.value, result)
            row.set_checked_quietly(not want)
            self._say(tr("Windows didn't allow that change, so {name} is as it was.", name=ltr(row.entry.name)))
        self._show_state()

    def _sync_own_setting(self):
        """Corefold's own line switched: Settings → General shows the same."""
        w = self.window()
        box = getattr(w, "startup", None)
        if box is not None and getattr(w, "pending_startup", None) is None:
            from .settings import startup_enabled
            box.blockSignals(True)
            box.setChecked(startup_enabled())
            box.blockSignals(False)

    def _say(self, text: str):
        self.problem.setText(text)
        self.problem.show()
