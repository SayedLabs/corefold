"""Corefold Startup's page: every program that starts with Windows, each with a switch."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QFileInfo, QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QButtonGroup, QFileIconProvider, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, \
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget

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
# the list in three parts, so what's old is clearly old: (key, heading, a line about it)
SECTIONS = [("on", "Starting with Windows", ""),
            ("off", "Switched off",
             "Windows keeps these so they can be switched back on. Some may have been switched off a long time "
             "ago, by you, by Task Manager or by the program itself."),
            ("gone", "Left behind by removed programs",
             "Their program was removed from this PC, but its line stayed behind. They can't start anything and "
             "do no harm: removing them only tidies the list.")]


def section_of(e: startup.Entry) -> str:
    if e.missing and not e.own:
        return "gone"
    return "on" if e.on else "off"


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
                                  (entry.admin and not page.admin, tr("Asks for permission"), "badge")):
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
        m.deleteLater()


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
        self.after_load = ""
        self.new_ids: set[str] = set()   # marked New this time Corefold runs (kept when looking again)
        self.admin = startup.is_admin()
        self._icons = QFileIconProvider()
        self._icon_cache: dict[str, QIcon | None] = {}
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
        lay.addWidget(_label(tr("Everything here is read from this PC, just now. Nothing comes from the internet "
                                "or from another computer."), "subtle", wrap=True))
        self.problem = _label("", "warning", wrap=True)
        self.problem.hide()
        lay.addWidget(self.problem)
        holder = QWidget()
        holder.setObjectName("scrollContent")  # (see-through, like every page's scrolling part)
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        self.groups: dict[str, Group] = {}
        self.heads: dict[str, QWidget] = {}
        for key, title, about in SECTIONS:
            head = QWidget()
            hv = QVBoxLayout(head)
            hv.setContentsMargins(0, 6 if key == "on" else 14, 0, 0)
            hv.setSpacing(2)
            top = QHBoxLayout()
            top.setSpacing(8)
            top.addWidget(_label(tr(title), "section"), 1)
            if key == "gone":
                self.clean_btn = QPushButton(tr("Remove leftovers"))
                self.clean_btn.setIcon(icons.icon("delete", self.c.theme.muted))
                self.clean_btn.setCursor(Qt.PointingHandCursor)
                self.clean_btn.clicked.connect(self.remove_leftovers)
                top.addWidget(self.clean_btn, 0, Qt.AlignBottom)
            hv.addLayout(top)
            if about:
                hv.addWidget(_label(tr(about), "subtle", wrap=True))
            self.heads[key] = head
            self.groups[key] = Group()
            hl.addWidget(head)
            hl.addWidget(self.groups[key])
        self.group = self.groups["on"]
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
        self.refresh_btn.setEnabled(not self.busy)
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
            if self.after_load:  # (what removing the leftovers had to say)
                self._say(self.after_load)
            else:
                self.problem.hide()
            self.after_load = ""
            if same:  # (nothing changed: the rows stay, without a flicker)
                for row, e in zip(self.rows, result):
                    row.entry = e
                self._show_state()
                return
        self._fill()

    def _fill(self):
        scrolled = self.scroll.verticalScrollBar().value()
        for row in self.rows:
            row.hide()
            row.deleteLater()
        self.rows = []
        for e in self.entries:
            row = EntryRow(self, e, self._icon(e))
            row.section = section_of(e)  # (a row stays in its part until the list is read again)
            self.groups[row.section].add(row)
            self.rows.append(row)
        any_new = any(e.new for e in self.entries)
        self.filter_buttons["new"].setVisible(any_new)
        if not any_new and self.kind == "new":
            self._filter("")
        self.filter()
        QTimer.singleShot(0, lambda: work.alive(self) and self.scroll.verticalScrollBar().setValue(scrolled))

    def _icon(self, e: startup.Entry) -> QIcon | None:
        """The program's own icon (kept, so looking again doesn't read every file's icon again)."""
        for path in (e.exe, e.extra.get("file", "")):
            if not path:
                continue
            if path not in self._icon_cache:
                ic = self._icons.icon(QFileInfo(path)) if os.path.isfile(path) else None
                self._icon_cache[path] = ic if ic is not None and not ic.isNull() else None
            if self._icon_cache[path] is not None:
                return self._icon_cache[path]
        return None

    # ---- search and filters

    def _filter(self, key: str):
        self.kind = key
        self.filter_buttons[key].setChecked(True)
        self._regroup()
        self.filter()

    def _regroup(self):
        """Rows switched since the list was read move to their part now (not while being switched, so
        nothing jumps from under the mouse)."""
        if all(row.section == section_of(row.entry) for row in self.rows):
            return
        for row in self.rows:
            self.groups[row.section].lay.removeWidget(row)
            row.section = section_of(row.entry)
        for row in self.rows:  # (in the list's order: by name)
            self.groups[row.section].add(row)

    def filter(self):
        text = self.search.text()
        shown = set()
        for row in self.rows:
            seen = matches(row.entry, text, self.kind)
            row.setVisible(seen)
            if seen:
                shown.add(row.section)
        for key, group in self.groups.items():
            self.heads[key].setVisible(key in shown)
            group.setVisible(key in shown)
            group.update()
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
        self.clean_btn.setEnabled(False)
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
        self.clean_btn.setEnabled(not self.busy)
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

    # ---- leftovers of removed programs

    def remove_leftovers(self, ask: bool = True):
        """Removes the lines whose program is gone from this PC (after asking), then reads the list again."""
        gone = startup.leftovers(self.entries)
        if not gone or self.busy or self.loading:
            return
        if ask:
            box = QMessageBox(QMessageBox.Question, tr("Remove leftovers?"),
                              tr("Leftovers to remove: {count}", count=len(gone)) + "\n\n"
                              + tr("They're taken out of the startup list. Their programs are already gone, so "
                                   "nothing else changes. Corefold keeps a copy of each line first."),
                              parent=self)
            yes = box.addButton(tr("Remove"), QMessageBox.DestructiveRole)
            box.addButton(tr("Cancel"), QMessageBox.RejectRole)
            box.exec()
            chosen = box.clickedButton() is yes
            box.deleteLater()
            if not chosen:
                return
        self.busy += 1
        self.refresh_btn.setEnabled(False)
        self.clean_btn.setEnabled(False)
        self.problem.hide()
        job = work.run(lambda _p: startup.remove_leftovers(gone), lambda _: None,
                       lambda result, n=len(gone): self._removed(n, result), name="corefold-startup-clean")
        self.switching.append(job)

    def _removed(self, asked: int, result):
        self.busy -= 1
        self.switching = [j for j in self.switching if j.is_alive()]
        if not work.alive(self):
            return
        self.clean_btn.setEnabled(not self.busy)
        if isinstance(result, Exception):
            log.warning("couldn't remove the leftovers: %s", result)
            result = []
        if len(result) < asked:
            self.after_load = tr("Windows didn't allow removing some of them ({count}), so they're as they were.",
                                 count=asked - len(result))
        self.load()

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
