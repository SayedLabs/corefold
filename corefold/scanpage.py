"""Corefold Scan's page: Microsoft Defender's state, the three kinds of scan, and what a scan found."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, \
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from . import i18n, icons, scan, work
from .atomic import tokens
from .atomic.widgets import Group, IconButton, ResponsiveGrid, SettingRow
from .i18n import ltr, tr
from .theme import round_menu
from .timefmt import time_ago
from .winutil import reveal_in_explorer

if TYPE_CHECKING:
    from .app import Controller

log = logging.getLogger("corefold")

# where an odd place is, in words (English, shown through tr())
PLACES = {"temp": "a temporary folder", "downloads": "Downloads", "desktop": "the desktop",
          "public": "the shared Public folder", "appdata": "AppData", "programdata": "ProgramData",
          "recycle": "the Recycle Bin"}
STATUS = {"quarantined": "In quarantine", "removed": "Removed", "blocked": "Blocked", "allowed": "Allowed by you",
          "failed": "Defender couldn't remove it", "deleted": "Deleted", "": "Still there"}
SEVERITY = {"severe": "Severe", "high": "High", "moderate": "Moderate", "low": "Low"}
SKIPPED = {"path": "Microsoft Defender is told to skip this folder or file, so it's never scanned.",
           "proc": "Microsoft Defender is told to skip whatever this program opens.",
           "ext": "Microsoft Defender is told to skip every file of this kind."}
GONE = ("quarantined", "removed", "blocked", "deleted")   # what Defender did that leaves the threat unable to do anything
KINDS = {scan.QUICK: "Quick scan", scan.FULL: "Full scan", scan.CUSTOM: "Scan of your choice"}
SECURITY_LINK = "windowsdefender://threat"


def _para(text: str) -> str:
    """In Arabic, a line that starts with a Latin name still reads (and lines up) right to left."""
    return "\u200f" + text if text and i18n.is_rtl() else text


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def _button(text: str, name: str = "", on_click=None) -> QPushButton:
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    if on_click is not None:
        b.clicked.connect(on_click)
    return b


def elapsed_text(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def finding_text(f: dict) -> str:
    """What a finding means, in a sentence or two."""
    kind, d = f["kind"], f.get("detail", {})
    place = tr(PLACES.get(d.get("place", ""), "")) if d.get("place") in PLACES else ""
    if kind == "disguised":
        return tr("Its name makes it look like a {kind} file, but it's a program that runs when it's opened.",
                  kind=d.get("looks", "").lstrip(".").upper())
    if kind == "flipped":
        return tr("Its name has a hidden character that turns part of it around, so it reads differently from "
                  "what it is. Its name without that character: {name}", name=ltr(d.get("shown", "")))
    if kind == "broken":
        return tr("It was signed, then changed after it was signed. Real programs aren't changed after they're "
                  "signed.")
    if kind == "untrusted":
        if d.get("signer"):
            return tr("It's signed by someone Windows doesn't trust: {name}", name=ltr(d["signer"]))
        return tr("It's signed by someone Windows doesn't trust.")
    if kind == "startup":
        if d.get("sig") == "unsigned":
            return tr("Starts with Windows from {place}, and nobody signed it.", place=place)
        return tr("Starts with Windows from {place}, and its signature isn't valid.", place=place)
    if kind == "startup-script":
        return tr("A script that starts with Windows ({name}). Scripts are an easy way for malware to start, "
                  "but some programs use them too.", name=ltr(d.get("name", "")))
    if kind == "startup-hidden":
        return tr("Something starts with Windows with a hidden command or a web address ({name}).",
                  name=ltr(d.get("name", "")))
    if kind == "running":
        if d.get("sig") == "unsigned":
            return tr("Running right now from {place}, and nobody signed it.", place=place)
        return tr("Running right now from {place}, and its signature isn't valid.", place=place)
    if kind == "hosts":
        if d.get("blocked"):
            return tr("Your hosts file blocks {site}, so it can't be reached from this PC.",
                      site=ltr(d.get("site", "")))
        return tr("Your hosts file sends {site} to {address} instead of the real site.", site=ltr(d.get("site", "")),
                  address=ltr(d.get("to", "")))
    if kind == "replaced":
        if d.get("what") == "debugger":
            return tr("Whenever {name} starts, Windows runs this first: {command}", name=ltr(d.get("name", "")),
                      command=ltr(d.get("command", "")))
        return tr("Windows runs this when you sign in, instead of or next to its own: {command}",
                  command=ltr(d.get("command", "")))
    if kind == "service":
        if d.get("sig") == "unsigned":
            return tr("A service that runs from {place}, and nobody signed it.", place=place)
        return tr("A service that runs from {place}, and its signature isn't valid.", place=place)
    if kind == "browser":
        what = d.get("type")
        if what == "extension":
            return tr("An add-on is forced into {name}, so it can't be removed there.", name=ltr(d.get("name", "")))
        if what == "search":
            return tr("{name}'s search engine is fixed by a policy.", name=ltr(d.get("name", "")))
        if what == "proxy":
            return tr("{name} is told to send what you browse through another computer.",
                      name=ltr(d.get("name", "")))
        return tr("{name}'s home page is fixed by a policy.", name=ltr(d.get("name", "")))
    if kind == "proxy":
        return tr("Windows sends what you browse through another computer. Fine if your work set it up.")
    if kind == "defender-off":
        return tr("A policy switches off part of Microsoft Defender. Malware does this so it isn't found.")
    if kind == "skipped":
        text = tr(SKIPPED.get(d.get("what", "path"), SKIPPED["path"]))
        if f.get("level") == "warn":
            text += " " + tr("That leaves a lot unchecked.")
        return text
    return ""


def finding_title(f: dict) -> str:
    kind, path = f["kind"], f["path"]
    if kind == "hosts":
        return tr("The hosts file")
    if kind == "replaced":
        return ltr(d_name(f))
    if kind == "service":
        return ltr(f.get("detail", {}).get("name", "") or os.path.basename(path))
    if kind in ("browser", "proxy"):
        return ltr(f.get("detail", {}).get("value", "") if kind == "proxy" else path)
    if kind == "defender-off":
        return "Microsoft Defender"
    if kind == "skipped":
        return ltr(path)
    if kind == "flipped":
        return ltr(scan.shown_name(os.path.basename(path)))
    return ltr(os.path.basename(path) or path)


def d_name(f: dict) -> str:
    return f.get("detail", {}).get("name", "") or f["path"]


class ScanRunner(QObject):
    """The scan that's running, kept by the app rather than the page: the window can be closed (and even
    freed) while Defender works, and the page shows the scan again when it's opened."""
    finding = Signal(object)     # a Finding, as Corefold's checks make it
    stage = Signal(str)          # "defender", then "checks"
    began = Signal()
    finished = Signal(object)    # the ScanResult

    def __init__(self, controller: "Controller"):
        super().__init__()
        self.c = controller
        self.job = None
        self.cancel: threading.Event | None = None
        self.kind = ""
        self.paths: list[str] = []
        self.started = 0.0
        self.live: list = []
        self.stage_now = ""
        self.result: scan.ScanResult | None = None
        self.defender: scan.Defender | None = None
        self.finding.connect(lambda f: self.live.append(f))
        self.stage.connect(self._stage)

    @property
    def running(self) -> bool:
        return self.job is not None

    def start(self, kind: str, paths: list[str]) -> bool:
        if self.job is not None:
            return False
        self.kind, self.paths = kind, list(paths)
        self.cancel = threading.Event()
        self.started = time.time()
        self.live.clear()
        self.stage_now = ""
        self.result = None
        defender, cancel = self.defender, self.cancel

        def go(_progress):
            d = defender if defender is not None else scan.defender_status()
            return scan.scan(kind, self.paths, cancel, on_finding=self.finding.emit, defender=d,
                             on_stage=self.stage.emit)
        self.job = work.run(go, lambda _: None, self._done, name="corefold-scan")
        self.began.emit()
        return True

    def stop(self, wait_s: float = 0):
        if self.cancel is not None:
            self.cancel.set()
        if wait_s and self.job is not None:
            self.job.join(wait_s)

    def _stage(self, stage: str):
        self.stage_now = stage

    def _done(self, result):
        self.job = None
        if isinstance(result, Exception):
            log.warning("the scan failed: %s", result)
            result = scan.ScanResult(self.kind, self.paths, self.started, time.time(), defender_problem="failed")
        self.result = result
        self.finished.emit(result)
        self._tell(result)

    def _tell(self, r: scan.ScanResult):
        """A note when the scan finishes while the window is away (or on another page)."""
        if r.stopped:
            return
        w = self.c.window_if_open()
        if w is not None and w.isVisible() and w.isActiveWindow() and not w.isMinimized() \
                and w.current_page() == "Scan":
            return
        _icon, title, line = verdict_words(r, self.defender)
        try:
            self.c.toast.notify(tr("The scan is done"), title + "\n" + line, tr("See"),
                                lambda: self.c.show_window("Scan"), linger_ms=12000)
        except Exception:
            log.debug("couldn't say the scan is done", exc_info=True)


def runner(controller: "Controller") -> ScanRunner:
    """The app's one ScanRunner (made the first time it's needed)."""
    r = getattr(controller, "scan_runner", None)
    if r is None:
        r = ScanRunner(controller)
        controller.scan_runner = r
    return r


def verdict_words(r: scan.ScanResult, d: scan.Defender | None) -> tuple[str, str, str]:
    """(icon, title, line) for a finished scan."""
    warn = sum(1 for f in r.findings if f.get("level") == "warn")
    found = len(r.threats)
    if r.stopped:
        title, icon = tr("The scan was stopped"), "info"
    elif r.defender_problem == "not-here":
        title, icon = tr("Microsoft Defender isn't on this PC, so only Corefold's own checks were made"), "info"
    elif r.defender_problem == "off":
        if d is not None and d.others:
            title = tr("{name} protects this PC, so only Corefold's own checks were made. Scan with {name} too.",
                       name=ltr(d.others[0]))
        else:
            title = tr("Microsoft Defender is switched off, so only Corefold's own checks were made")
        icon = "shield_dismiss"
    elif r.defender_problem == "failed":
        title, icon = tr("Microsoft Defender couldn't finish the scan"), "shield_error"
    elif r.defender_problem == "cleaning":
        title, icon = tr("Microsoft Defender couldn't deal with everything it found. Open Windows Security to "
                         "see what to do."), "shield_error"
    elif found:
        title, icon = tr("Threats Microsoft Defender found: {count}", count=found), "shield_error"
    else:
        title, icon = tr("Microsoft Defender found no threats"), "shield"
    if warn:
        line = tr("Corefold's own checks: {count} worth a look", count=warn)
    elif r.looked_at:
        line = tr("Corefold's own checks found nothing unusual in {count} files", count=r.looked_at)
    else:
        line = tr("Corefold's own checks found nothing unusual")
    return icon, title, line


def _tile(icon: str, color, dpr: float) -> QLabel:
    tile = QLabel()
    tile.setFixedSize(28, 28)
    tile.setAlignment(Qt.AlignCenter)
    tile.setPixmap(icons.pixmap(icon, 22, color, dpr))
    return tile


def _pills(row: SettingRow, labels: list[QLabel]):
    """Small labels under a row's name and line."""
    pills = QHBoxLayout()
    pills.setContentsMargins(0, 4, 0, 0)
    pills.setSpacing(6)
    for lab in labels:
        pills.addWidget(lab)
    pills.addStretch()
    row.layout().itemAt(1).layout().addLayout(pills)


class FindingRow(QWidget):
    """Something Corefold's checks noticed: its file, what it means, how much it matters, and a "…" menu."""

    def __init__(self, page: "ScanPage", f: dict):
        super().__init__()
        self.page, self.f = page, f
        t = tokens.current()
        warn = f.get("level") == "warn"
        tile = _tile("warning" if warn else "info", t.warning if warn else t.muted, self.devicePixelRatioF())
        more = IconButton(tr("More"), 30)
        more.setIcon(icons.icon("more_horizontal", t.muted))
        more.clicked.connect(lambda: self._menu(more.mapToGlobal(more.rect().bottomLeft())))
        d = f.get("detail", {})
        lines = [finding_text(f)]
        if f["kind"] in ("startup-hidden", "service"):
            lines.append(ltr(d.get("command", "")))
        elif f["kind"] == "browser":
            lines.append(ltr(d.get("value", "")))
        elif f["kind"] == "defender-off":
            lines.append(ltr(d.get("what", "")))
        elif f["kind"] not in ("skipped", "replaced", "proxy"):
            lines.append(ltr(os.path.dirname(f["path"])))
        if d.get("from"):
            lines.append(tr("Downloaded from {site}", site=ltr(d["from"])))
        self.row = SettingRow(_para(finding_title(f)), _para("\n".join(x for x in lines if x)), more, tile)
        self.row.description.setTextFormat(Qt.PlainText)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.row)
        _pills(self.row, [_label(tr("Worth a look") if warn else tr("Good to know"), "chip" if warn else "badge")])
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self._menu(self.mapToGlobal(pos)))

    def _menu(self, pos):
        f, t = self.f, tokens.current()
        path = f["path"]
        m = QMenu(self)
        round_menu(m)
        is_file = f["kind"] != "skipped" and os.path.exists(path)
        go = m.addAction(icons.icon("folder_open", t.muted), tr("Show in folder"))
        go.setEnabled(is_file)
        go.triggered.connect(lambda: reveal_in_explorer(Path(path)))
        again = m.addAction(icons.icon("shield", t.muted), tr("Scan it with Microsoft Defender"))
        again.setEnabled(is_file and f["kind"] != "hosts" and self.page.defender_ok() and not self.page.runner.running)
        again.triggered.connect(lambda: self.page.scan_paths([path]))
        if f["kind"] == "skipped":
            m.addAction(icons.icon("shield", t.muted), tr("Open Windows Security"), self.page.open_security)
        copy = m.addAction(icons.icon("copy", t.muted), tr("Copy the path"))
        copy.triggered.connect(lambda: QGuiApplication.clipboard().setText(f.get("detail", {}).get("command") or path))
        m.addSeparator()
        hide = m.addAction(icons.icon("dismiss", t.muted), tr("Don't show this again"))
        hide.triggered.connect(lambda: self.page.ignore(self))
        m.exec(pos)
        m.deleteLater()


class ThreatRow(QWidget):
    """A threat Microsoft Defender found: its name, its files, and what Defender did with it."""

    def __init__(self, page: "ScanPage", threat: dict):
        super().__init__()
        self.page, self.threat = page, threat
        t = tokens.current()
        gone = threat.get("status") in GONE
        tile = _tile("shield" if gone else "shield_error", t.success if gone else t.danger, self.devicePixelRatioF())
        files = threat.get("files") or []
        more = IconButton(tr("More"), 30)
        more.setIcon(icons.icon("more_horizontal", t.muted))
        more.clicked.connect(lambda: self._menu(more.mapToGlobal(more.rect().bottomLeft())))
        text = "\n".join(ltr(x) for x in files[:4]) + ("\n…" if len(files) > 4 else "")
        self.row = SettingRow(_para(ltr(threat.get("name", ""))), _para(text), more, tile)
        self.row.description.setTextFormat(Qt.PlainText)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.row)
        labels = [_label(tr(STATUS.get(threat.get("status", ""), STATUS[""])), "chip")]
        if threat.get("severity") in SEVERITY:
            labels.append(_label(tr(SEVERITY[threat["severity"]]), "badge"))
        _pills(self.row, labels)

    def _menu(self, pos):
        t = tokens.current()
        files = [x for x in self.threat.get("files") or [] if os.path.exists(x)]
        m = QMenu(self)
        round_menu(m)
        go = m.addAction(icons.icon("folder_open", t.muted), tr("Show in folder"))
        go.setEnabled(bool(files))
        go.triggered.connect(lambda: reveal_in_explorer(Path(files[0])))
        m.addAction(icons.icon("shield", t.muted), tr("Open Windows Security"), self.page.open_security)
        if files and self.threat.get("status") not in GONE:
            m.addAction(icons.icon("delete", t.danger), tr("Delete it…"), lambda: self.page.delete_threat(self))
        copy = m.addAction(icons.icon("copy", t.muted), tr("Copy the threat's name"))
        copy.triggered.connect(lambda: QGuiApplication.clipboard().setText(self.threat.get("name", "")))
        m.exec(pos)
        m.deleteLater()


class OptionCard(QFrame):
    """One kind of scan: its icon, name, a line about it, and its button(s)."""

    def __init__(self, icon: str, title: str, text: str, buttons: list[QPushButton]):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(icons.pixmap(icon, 22, tokens.current().accent, self.devicePixelRatioF()))
        top.addWidget(ic)
        top.addWidget(_label(title, "h2"), 1)
        lay.addLayout(top)
        lay.addWidget(_label(text, "muted", wrap=True))
        lay.addStretch()
        row = QHBoxLayout()
        row.setSpacing(8)
        for b in buttons:
            row.addWidget(b)
        row.addStretch()
        lay.addSpacing(6)
        lay.addLayout(row)


def _card(layout_cls=QHBoxLayout) -> tuple[QFrame, QHBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    lay = layout_cls(card)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(14)
    return card, lay


class ScanPage(QWidget):
    """Corefold Scan in the window: Defender's state, the three kinds of scan, and what the last one found."""

    def __init__(self, controller: "Controller", header):
        super().__init__()
        self.c = controller
        self.status_job = None
        self.runner = runner(controller)
        self.defender = self.runner.defender
        self.result: scan.ScanResult | None = None
        self.threat_rows: list[ThreatRow] = []
        self.finding_rows: list[FindingRow] = []
        self.setAcceptDrops(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 10, 0)
        outer.setSpacing(12)
        outer.addLayout(header)
        holder = QWidget()
        holder.setObjectName("scrollContent")
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(12)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(holder)
        self.scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(self.scroll, 1)

        # ---- Microsoft Defender's state
        self.status, sl = _card()
        self.status_icon = QLabel()
        self.status_icon.setFixedSize(32, 32)
        sl.addWidget(self.status_icon, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(2)
        self.status_title = _label(tr("Checking Microsoft Defender…"), "h2", wrap=True)
        self.status_line = _label("", "muted", wrap=True)
        self.status_line.hide()
        words.addWidget(self.status_title)
        words.addWidget(self.status_line)
        sl.addLayout(words, 1)
        self.security_btn = _button(tr("Open Windows Security"), "flat", self.open_security)
        sl.addWidget(self.security_btn, 0, Qt.AlignVCenter)
        lay.addWidget(self.status)
        self.status_warning = _label("", "warning", wrap=True)
        self.status_warning.hide()
        lay.addWidget(self.status_warning)

        # ---- the three kinds of scan
        self.quick_btn = _button(tr("Quick scan"), "primary", lambda: self.start(scan.QUICK))
        self.full_btn = _button(tr("Full scan"), "", lambda: self.start(scan.FULL))
        self.file_btn = _button(tr("A file…"), "", self.pick_file)
        self.folder_btn = _button(tr("A folder or drive…"), "", self.pick_folder)
        self.options = ResponsiveGrid(240, 12, 3)
        self.options.add(OptionCard("top_speed", tr("Quick scan"),
                                    tr("The places malware hides most: what's running, what starts with Windows, "
                                       "and your Downloads. Usually a few minutes."), [self.quick_btn]))
        self.options.add(OptionCard("hard_drive", tr("Full scan"),
                                    tr("Every file on every drive. It can take an hour or more, and you can keep "
                                       "using your PC."), [self.full_btn]))
        self.options.add(OptionCard("folder_search", tr("Choose what to scan"),
                                    tr("A file, a folder or a drive. You can also drop files here, or right-click "
                                       "them and pick \"Scan with Corefold\"."), [self.file_btn, self.folder_btn]))
        self.options.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        lay.addWidget(self.options)

        # ---- while a scan runs
        self.running, rl = _card(QVBoxLayout)
        rl.setContentsMargins(18, 16, 18, 16)
        rl.setSpacing(8)
        top = QHBoxLayout()
        self.run_title = _label("", "h2", wrap=True)
        top.addWidget(self.run_title, 1)
        self.run_clock = _label("", "muted")
        top.addWidget(self.run_clock)
        rl.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)   # (Defender doesn't say how far along it is)
        self.bar.setTextVisible(False)
        rl.addWidget(self.bar)
        self.run_line = _label("", "muted", wrap=True)
        rl.addWidget(self.run_line)
        bottom = QHBoxLayout()
        bottom.addWidget(_label(tr("You can close this window: Corefold tells you when the scan is done."), "subtle",
                                wrap=True), 1)
        self.stop_btn = _button(tr("Stop"), "", self.stop)
        bottom.addWidget(self.stop_btn)
        rl.addLayout(bottom)
        self.running.hide()
        lay.addWidget(self.running)
        self.clock = QTimer(self, interval=1000)
        self.clock.timeout.connect(self._tick)

        # ---- what the last scan found
        self.verdict, vl = _card()
        self.verdict_icon = QLabel()
        self.verdict_icon.setFixedSize(32, 32)
        vl.addWidget(self.verdict_icon, 0, Qt.AlignTop)
        vw = QVBoxLayout()
        vw.setSpacing(3)
        self.verdict_title = _label("", "h2", wrap=True)
        self.verdict_line = _label("", "muted", wrap=True)
        self.verdict_note = _label("", "subtle", wrap=True)
        for w in (self.verdict_title, self.verdict_line, self.verdict_note):
            vw.addWidget(w)
        vl.addLayout(vw, 1)
        self.verdict.hide()
        lay.addWidget(self.verdict)
        self.threats_head = _label(tr("What Microsoft Defender found"), "section")
        self.threats = Group()
        self.findings_head = _label(tr("Worth a look, from Corefold's own checks"), "section")
        self.findings_note = _label(tr("These are tricks malware often uses, but harmless programs sometimes do "
                                       "them too. Corefold doesn't change or delete anything here."), "subtle",
                                    wrap=True)
        self.findings = Group()
        for w in (self.threats_head, self.threats, self.findings_head, self.findings_note, self.findings):
            w.hide()
            lay.addWidget(w)

        # ---- the foot of the page
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.last_line = _label("", "muted", wrap=True)
        foot.addWidget(self.last_line, 1)
        self.unhide_btn = _button("", "", self._unhide)
        foot.addWidget(self.unhide_btn)
        self._show_hidden_count()
        self.quarantine_btn = _button(tr("Defender's quarantine…"), "", self.show_quarantine)
        foot.addWidget(self.quarantine_btn)
        lay.addLayout(foot)
        lay.addWidget(_label(tr("Everything happens on this PC: Corefold uploads nothing and looks nothing up "
                                "online. Microsoft Defender, part of Windows, does the scanning."), "subtle",
                             wrap=True))
        lay.addStretch()
        self.runner.began.connect(self._began)
        self.runner.finding.connect(self._show_stage)
        self.runner.stage.connect(self._show_stage)
        self.runner.finished.connect(self._done)
        self._show_last()
        self._show_status()
        if self.runner.running:
            self._began()
        elif self.runner.result is not None:
            self.result = self.runner.result
            self._show_result(self.result)

    # ---- Microsoft Defender's state (read off the window's thread: it asks PowerShell)

    def ensure_loaded(self):
        if self.status_job is None or (not self.status_job.is_alive() and not self.runner.running):
            self.status_job = work.run(lambda _p: scan.defender_status(), lambda _: None, self._status_read,
                                       name="corefold-scan-status")

    def _status_read(self, d):
        if not work.alive(self):
            return
        if isinstance(d, Exception):
            log.warning("couldn't read Defender's status: %s", d)
            d = scan.Defender(exe=scan.find_mpcmdrun())
        self.defender = d
        self.runner.defender = d
        self._show_status()

    def defender_ok(self) -> bool:
        return self.defender is None or self.defender.can_scan

    def _show_status(self):
        t = tokens.current()
        d = self.defender
        warning, line = "", ""
        if d is None:
            icon, color, title = "shield", t.muted, tr("Checking Microsoft Defender…")
        elif not d.exe:
            icon, color = "shield_dismiss", t.danger
            title = tr("Microsoft Defender isn't on this PC")
            line = tr("Corefold's own checks still work, but scanning needs Microsoft Defender.")
        elif d.read and not d.enabled and d.others:
            icon, color = "shield", t.accent
            title = tr("{name} protects this PC", name=ltr(", ".join(d.others)))
            line = tr("Microsoft Defender rests while another antivirus is on, so scan with {name} too. "
                      "Corefold's own checks still work.", name=ltr(d.others[0]))
        elif d.read and not d.enabled:
            icon, color = "shield_dismiss", t.danger
            title = tr("Microsoft Defender is switched off")
            line = tr("Switch it on in Windows Security to scan. Corefold's own checks still work.")
        else:
            if d.realtime or not d.read:
                icon, color = "shield", t.success
                title = tr("Microsoft Defender is protecting this PC")
            else:
                icon, color = "shield_error", t.warning
                title = tr("Microsoft Defender is on, but real-time protection is off")
                warning = tr("Files are only checked when you scan them. Switch real-time protection back on in "
                             "Windows Security.")
            bits = []
            if d.updated:
                bits.append(tr("Its list of threats was updated {when}", when=time_ago(d.updated)))
            if d.last_full:
                bits.append(tr("Last full scan {when}", when=time_ago(d.last_full)))
            line = "  ·  ".join(bits)
            if d.days_old is not None and d.days_old >= 7:
                warning = tr("Defender's list of threats is {count} days old, so it may miss new threats. Windows "
                             "Update brings it up to date.", count=d.days_old)
        self.status_icon.setPixmap(icons.pixmap(icon, 30, color, self.devicePixelRatioF()))
        self.status_title.setText(title)
        self.status_line.setText(line)
        self.status_line.setVisible(bool(line))
        self.status_warning.setText(warning)
        self.status_warning.setVisible(bool(warning))
        busy = self.runner.running
        for b in (self.quick_btn, self.full_btn, self.file_btn, self.folder_btn):
            b.setEnabled(not busy)
        self.quarantine_btn.setEnabled(d is None or bool(d.exe))

    def open_security(self):
        try:
            os.startfile(SECURITY_LINK)
        except (OSError, AttributeError):
            try:
                from .core import system_tool
                subprocess.Popen([system_tool("explorer.exe"), SECURITY_LINK])
            except OSError:
                log.warning("couldn't open Windows Security")

    # ---- starting and stopping

    def pick_file(self):
        files, _ = QFileDialog.getOpenFileNames(self, tr("Choose what to scan"), str(scan.downloads()))
        if files:
            self.scan_paths(files)

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, tr("Choose what to scan"), str(scan.home()))
        if folder:
            self.scan_paths([folder])

    def scan_paths(self, paths: list[str]):
        paths = [os.path.normpath(p) for p in paths if p and os.path.exists(p)]
        if paths:
            self.start(scan.CUSTOM, paths)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and not self.runner.running:
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.scan_paths(paths)

    def start(self, kind: str, paths: list[str] | None = None) -> bool:
        return self.runner.start(kind, list(paths or []))

    def stop(self):
        self.runner.stop()
        self.stop_btn.setEnabled(False)
        self._show_stage()

    def _began(self):
        r = self.runner
        self._clear_results()
        if r.kind == scan.CUSTOM:
            title = (tr("Scanning {name}", name=ltr(os.path.basename(r.paths[0].rstrip("\\/")) or r.paths[0]))
                     if len(r.paths) == 1 else tr("Scanning {count} items", count=len(r.paths)))
        else:
            title = tr(KINDS[r.kind]) + "…"
        self.run_title.setText(_para(title))
        self.stop_btn.setEnabled(not (r.cancel and r.cancel.is_set()))
        self._tick()
        self._show_stage()
        self.running.show()
        self.clock.start()
        self._show_status()
        QTimer.singleShot(0, lambda: work.alive(self) and self.scroll.ensureWidgetVisible(self.running))

    def _tick(self):
        self.run_clock.setText(elapsed_text(time.time() - self.runner.started))

    def _show_stage(self, *_):
        r = self.runner
        if not work.alive(self) or not r.running:
            return
        if r.cancel is not None and r.cancel.is_set():
            text = tr("Stopping…")
        elif r.stage_now == "checks":
            text = tr("Finishing Corefold's own checks…")
        elif r.stage_now == "defender":
            text = tr("Microsoft Defender is scanning…")
        else:
            text = tr("Getting ready…")
        warn = sum(1 for f in r.live if f.level == "warn")
        if warn:
            text += "  ·  " + tr("Worth a look so far: {count}", count=warn)
        self.run_line.setText(text)

    # ---- what it found

    def _done(self, result):
        if not work.alive(self):
            return
        self.clock.stop()
        self.running.hide()
        self.result = result
        self._show_result(result)
        self._show_status()
        self._show_last()

    def _clear_results(self):
        for row in self.threat_rows + self.finding_rows:
            row.hide()
            row.deleteLater()
        self.threat_rows, self.finding_rows = [], []
        for w in (self.verdict, self.threats_head, self.threats, self.findings_head, self.findings_note,
                  self.findings):
            w.hide()

    def _show_result(self, r: scan.ScanResult):
        self._clear_results()
        t = tokens.current()
        icon, title, line = verdict_words(r, self.defender)
        color = {"shield": t.success, "shield_error": t.danger, "shield_dismiss": t.warning}.get(icon, t.muted)
        dealt = bool(r.threats) and all(th.get("status") in GONE for th in r.threats) and not r.defender_problem
        if dealt:
            color = t.warning
        self.verdict_icon.setPixmap(icons.pixmap(icon, 30, color, self.devicePixelRatioF()))
        self.verdict_title.setText(title)
        self.verdict_line.setText(line)
        if r.defender_problem == "cleaning":
            note = ""   # (the title says it)
        elif r.threats and not dealt:
            note = tr("Microsoft Defender couldn't deal with everything it found. Open Windows Security to see what "
                      "to do.")
        elif dealt:
            note = tr("Microsoft Defender has already dealt with what it found, so it can't do anything now.")
        elif r.defender_ran:
            note = tr("That means none of the threats Microsoft Defender knows about. No scanner can promise a file "
                      "is harmless, so stay careful with files from people or sites you don't know.")
        else:
            note = ""
        self.verdict_note.setText(note)
        self.verdict_note.setVisible(bool(note))
        self.verdict.show()
        for th in r.threats:
            row = ThreatRow(self, th)
            self.threats.add(row)
            self.threat_rows.append(row)
        self.threats_head.setVisible(bool(r.threats))
        self.threats.setVisible(bool(r.threats))
        order = sorted(r.findings, key=lambda f: (f.get("level") != "warn", f["kind"], f["path"].lower()))
        for f in order:
            row = FindingRow(self, f)
            self.findings.add(row)
            self.finding_rows.append(row)
        for w in (self.findings_head, self.findings_note, self.findings):
            w.setVisible(bool(order))
        QTimer.singleShot(0, lambda: work.alive(self) and self.scroll.ensureWidgetVisible(self.verdict))

    def delete_threat(self, row: ThreatRow):
        """A threat Defender left where it was (it's told to skip it, or couldn't remove it): deleted for good,
        after asking, since a threat in the Recycle Bin can still be opened."""
        files = [f for f in row.threat.get("files") or [] if os.path.isfile(f)]
        if not files:
            return
        names = "\n".join(ltr(f) for f in files[:5])
        if QMessageBox.question(self, tr("Delete it"), tr("Delete this for good? It can't be put back.") + "\n\n" +
                                names) != QMessageBox.Yes:
            return
        failed = []
        for f in files:
            try:
                os.remove(f)
            except OSError as e:
                log.warning("couldn't delete %s: %s", f, e)
                failed.append(f)
        if failed:
            QMessageBox.warning(self, tr("Delete it"), tr("Windows didn't let Corefold delete it. Open Windows "
                                                          "Security to remove it."))
            return
        row.threat["status"] = "deleted"
        if self.result is not None:
            self._show_result(self.result)

    def ignore(self, row: FindingRow):
        """ "Don't show this again": hidden now, and in every scan after this."""
        scan.ignore(scan.finding_key(row.f))
        if self.result is not None:
            self.result.findings = [f for f in self.result.findings if f is not row.f]
        self.finding_rows = [r for r in self.finding_rows if r is not row]
        row.hide()
        row.deleteLater()
        self._show_hidden_count()
        for w in (self.findings_head, self.findings_note, self.findings):
            w.setVisible(bool(self.finding_rows))
        if self.result is not None:
            self.verdict_line.setText(verdict_words(self.result, self.defender)[2])

    def _show_last(self):
        last = scan.load_last()
        when = float(last.get("when") or 0)
        if not when:
            self.last_line.setText("")
            return
        kind = tr(KINDS.get(last.get("kind", ""), KINDS[scan.CUSTOM]))
        if not last.get("defender"):
            what = tr("only Corefold's own checks")
        elif last.get("threats"):
            what = tr("threats found: {count}", count=int(last["threats"]))
        else:
            what = tr("no threats found")
        self.last_line.setText(tr("Last scan: {kind}, {when}, {what}", kind=kind, when=time_ago(when), what=what))

    def _show_hidden_count(self):
        """A way back for every "Don't show this again"."""
        n = len(scan.load_ignored())
        self.unhide_btn.setEnabled(True)
        self.unhide_btn.setText(tr("Show the hidden ones again ({count})", count=n))
        self.unhide_btn.setToolTip(tr("What you chose not to see again shows up in the next scan."))
        self.unhide_btn.setVisible(n > 0)

    def _unhide(self):
        scan.forget_ignored()
        self.unhide_btn.setText(tr("They show up in the next scan"))
        self.unhide_btn.setEnabled(False)

    # ---- Defender's quarantine

    def show_quarantine(self):
        QuarantineDialog(self).exec()


class QuarantineDialog(QDialog):
    """What Microsoft Defender keeps in quarantine, with a way to put a file back."""

    def __init__(self, page: ScanPage):
        super().__init__(page.window())
        self.page = page
        self.setWindowTitle(tr("Defender's quarantine"))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(12)
        lay.addWidget(_label(tr("Defender's quarantine"), "h2"))
        lay.addWidget(_label(tr("Files Microsoft Defender locked away so they can't do anything. Windows may ask for "
                                "permission to show them."), "muted", wrap=True))
        self.state = _label(tr("Reading Defender's quarantine…"), "muted", wrap=True)
        lay.addWidget(self.state)
        self.group = Group()
        self.group.hide()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        holder.setObjectName("scrollContent")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(self.group)
        hl.addStretch()
        scroll.setWidget(holder)
        scroll.setMinimumHeight(220)
        lay.addWidget(scroll, 1)
        row = QHBoxLayout()
        row.addWidget(_button(tr("Open Windows Security"), "flat", page.open_security))
        row.addStretch()
        close = _button(tr("Close"), "primary", self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.jobs: list = []
        self.load()

    def load(self):
        self.jobs.append(work.run(lambda _p: scan.quarantine(), lambda _: None, self._loaded,
                                  name="corefold-quarantine"))

    def _loaded(self, items):
        if not work.alive(self):
            return
        for i in reversed(range(self.group.lay.count())):
            w = self.group.lay.itemAt(i).widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        if isinstance(items, Exception) or items is None:
            self.state.setText(tr("Corefold couldn't read Defender's quarantine. Windows Security shows it too."))
            self.group.hide()
            return
        if not items:
            self.state.setText(tr("Defender's quarantine is empty."))
            self.group.hide()
            return
        self.state.setText(tr("In quarantine: {count}", count=len(items)))
        t = tokens.current()
        for item in items:
            back = _button(tr("Put back…"), "", lambda _=False, it=item: self.put_back(it))
            tile = QLabel()
            tile.setFixedSize(28, 28)
            tile.setPixmap(icons.pixmap("shield", 22, t.muted, self.devicePixelRatioF()))
            r = SettingRow(_para(ltr(os.path.basename(item.path) or item.threat)),
                           _para(ltr(item.threat) + "\n" + ltr(item.path) + (("\n" + ltr(item.when)) if item.when
                                                                            else "")), back, tile)
            r.description.setTextFormat(Qt.PlainText)
            self.group.add(r)
        self.group.show()

    def put_back(self, item: scan.Quarantined):
        answer = QMessageBox.question(
            self, tr("Put back"),
            tr("Put {name} back where it was? Microsoft Defender found {threat} in it. Only do this if you're sure "
               "it's harmless: Defender may lock it away again when it's opened.",
               name=ltr(os.path.basename(item.path) or item.path), threat=ltr(item.threat)))
        if answer != QMessageBox.Yes:
            return
        self.state.setText(tr("Putting it back…"))

        def done(ok):
            if not work.alive(self):
                return
            if ok is not True:
                QMessageBox.warning(self, tr("Put back"), tr("Microsoft Defender didn't put it back."))
            self.load()
        self.jobs.append(work.run(lambda _p: scan.restore(item), lambda _: None, done, name="corefold-restore"))
