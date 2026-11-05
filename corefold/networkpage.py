"""Corefold Network's page: how fast the network is going right now, which programs are using it and where
they connect, how much each one used, what other devices could reach, and a speed test on request.

Everything is read from this PC while the page is in sight (nothing runs in the background when it isn't).
The speed test is the only part that goes online, and only after the person pressed its button and was told
where it connects (see network.py)."""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QFileInfo, QPointF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QFileIconProvider, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from . import i18n, icons, network, sheet, work
from .atomic import tokens
from .atomic.widgets import FlowLayout, Group, IconButton, SettingRow
from .core import human_size, short_name
from .i18n import ltr, tr
from .theme import round_menu
from .timefmt import date_text
from .winutil import properties, reveal_in_explorer

if TYPE_CHECKING:
    from .app import Controller

log = logging.getLogger("corefold")

TICK_MS = 1000           # how often the speed is read while the page is in sight
NAMES_EVERY_S = 10       # Windows' list of names is read again this often (it changes slowly)
LOOK_EVERY = 3           # the connections are read again every this many ticks
GRAPH_POINTS = 60        # a minute of speed on the graph
MAX_PLACES_SHOWN = 3     # places named on a program's line ("and 4 more")
MAX_DETAIL_LINES = 40
SEEN_FILE = "network-seen.json"
MAX_SEEN = 3000
TABS = [("now", "Using the network now"), ("usage", "Data used"), ("open", "Open to other devices")]
PERIODS = [(1, "Today"), (7, "7 days"), (30, "30 days")]
USES = [("browse", "Websites and email"), ("video_hd", "HD video"), ("video_4k", "4K video"),
        ("call", "Video calls"), ("game", "Online games")]
FIGURES = [("down", "Download"), ("up", "Upload"), ("ping", "Response time"), ("loaded", "Response time when busy")]
NOW_GROUPS = [("internet", "Using the internet"), ("home", "Only this network")]
VERDICT_WORDS = {"good": "Great", "ok": "Fine", "poor": "May struggle"}
SEP = "  ·  "

_signatures: dict[str, object] = {}    # a program's file -> trust.Signature (read once, off the window's thread)
_sig_lock = threading.Lock()


def rate_text(bits: float) -> str:
    """"12.4 Mb/s" (bits a second, as internet plans are sold)."""
    if bits >= 1e9:
        text = f"{bits / 1e9:.2f} Gb/s"
    elif bits >= 1e6:
        text = f"{bits / 1e6:.1f} Mb/s" if bits < 1e8 else f"{bits / 1e6:.0f} Mb/s"
    elif bits >= 1e3:
        text = f"{bits / 1e3:.0f} kb/s"
    else:
        text = f"{bits:.0f} b/s"
    return f"\u202a{text}\u202c" if i18n.is_rtl() else text


def ms_text(ms: float) -> str:
    text = f"{ms:.0f} ms"
    return f"\u202a{text}\u202c" if i18n.is_rtl() else text


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    lab.setTextFormat(Qt.PlainText)   # (names of programs and places come from outside: never rich text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def _para(text: str) -> str:
    return "\u200f" + text if text and i18n.is_rtl() else text


def signature_of(path: str):
    """A program file's signature (kept: each file is checked once while Corefold runs)."""
    with _sig_lock:
        if path in _signatures:
            return _signatures[path]
    from . import trust
    sig = trust.signature(path)
    with _sig_lock:
        _signatures[path] = sig
    return sig


_names: list = [0.0, {}]   # (when, {address: name}): Windows' list of names is read again every NAMES_EVERY_S


def look() -> list[network.Program]:
    """The programs and their connections, with each program's signature (run off the window's thread)."""
    now = time.monotonic()
    if now - _names[0] > NAMES_EVERY_S or not _names[1]:
        _names[0], _names[1] = now, network.cached_names()
    found = network.programs(names=_names[1])
    for p in found:
        if p.path:
            signature_of(p.path)
    return found


# ----------------------------------------------------------------------------- remembering what was seen

def _seen_path() -> Path:
    from .settings import CONFIG_DIR
    return CONFIG_DIR / SEEN_FILE


def read_seen() -> set[str] | None:
    """The programs seen using the internet before (None: never looked before, so nothing counts as new)."""
    try:
        data = json.loads(_seen_path().read_text(encoding="utf-8"))
        return {k for k in data.get("seen", []) if isinstance(k, str)}
    except (OSError, ValueError, AttributeError):
        return None


def write_seen(seen: set[str]):
    try:
        path = _seen_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"seen": sorted(seen)[:MAX_SEEN]}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        log.debug("couldn't remember the programs seen online", exc_info=True)


# ----------------------------------------------------------------------------- the speed graph

class SpeedGraph(QWidget):
    """The last minute of download and upload speed, as two soft lines."""

    def __init__(self):
        super().__init__()
        self.points: deque = deque(maxlen=GRAPH_POINTS)
        self.setMinimumHeight(64)
        self.setAccessibleName(tr("Speed over the last minute"))

    def add(self, down: float, up: float):
        self.points.append((down, up))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = tokens.current()
        w, h = self.width(), self.height()
        p.setPen(QPen(pal.border, 1))
        p.drawLine(0, h - 1, w, h - 1)
        if len(self.points) < 2:
            p.end()
            return
        top = max(1e6, max(max(d, u) for d, u in self.points)) * 1.15
        step = w / (GRAPH_POINTS - 1)
        start = w - step * (len(self.points) - 1)
        rtl = self.layoutDirection() == Qt.RightToLeft
        for idx, color, fill in ((1, pal.muted, False), (0, pal.accent, True)):
            path = QPainterPath()
            for i, pt in enumerate(self.points):
                x = start + i * step
                if rtl:
                    x = w - x
                y = h - 2 - (h - 6) * min(1.0, pt[idx] / top)
                if i == 0:
                    path.moveTo(QPointF(x, y))
                else:
                    path.lineTo(QPointF(x, y))
            if fill:
                area = QPainterPath(path)
                area.lineTo(QPointF(w if not rtl else 0, h - 1))
                area.lineTo(QPointF(start if not rtl else w - start, h - 1))
                area.closeSubpath()
                soft = color.__class__(color)
                soft.setAlphaF(0.14)
                p.fillPath(area, soft)
            p.setPen(QPen(color, 2 if fill else 1.5))
            p.drawPath(path)
        p.end()


# ----------------------------------------------------------------------------- one program

def describe(p: network.Program, sig) -> tuple[str, str]:
    """(the line under its name, a warning to show as a badge or "")."""
    bits = []
    places = p.places()
    if places:
        shown = [ltr(x) for x in places[:MAX_PLACES_SHOWN]]
        text = ", ".join(shown)
        if len(places) > MAX_PLACES_SHOWN:
            text = tr("{places} and {count} more", places=text, count=len(places) - MAX_PLACES_SHOWN)
        bits.append(text)
    warn = ""
    if p.services:
        bits.append(tr("Windows service"))
    elif p.system and not p.path:
        bits.append(tr("Part of Windows"))
    if sig is not None:
        from . import trust
        if sig.state == trust.SIGNED and sig.signer:
            bits.append(tr("Signed by {name}", name=ltr(sig.signer)))
        elif sig.state == trust.UNSIGNED:
            warn = tr("Not signed")
        elif sig.state in (trust.BROKEN, trust.UNTRUSTED):
            warn = tr("Signature not valid")
    elif p.publisher:
        bits.append(ltr(p.publisher))
    return _para("  ·  ".join(bits)), warn


def details(p: network.Program, listening: bool = False) -> str:
    """Every connection of a program, one a line (plain text)."""
    lines = []
    conns = p.open_ports if listening else [c for c in p.conns if c.where != network.THIS_PC]
    for c in conns[:MAX_DETAIL_LINES]:
        port = c.local_port if listening else c.remote_port
        what = tr(network.port_name(port)) if network.port_name(port) else tr("Port {port}", port=port)
        if listening:
            lines.append(f"{c.proto.upper()} {port}  ·  {what}")
        else:
            place = c.host or c.remote
            if c.host:
                place = f"{c.host}  ({c.remote})"
            where = tr("Internet") if c.where == network.INTERNET else tr("This network")
            lines.append(f"{ltr(place)}  ·  {what}  ·  {where}")
    if len(conns) > MAX_DETAIL_LINES:
        lines.append(tr("{count} more", count=len(conns) - MAX_DETAIL_LINES))
    if p.services:
        lines.append(tr("Windows services in it: {names}", names=", ".join(p.services[:8])))
    if p.path:
        lines.append(ltr(p.path))
    return "\n".join(_para(x) for x in lines)


class ProgramRow(QWidget):
    """A program: its icon, name, where it connects and who signed it, a "…" menu; click it to see every
    connection."""

    def __init__(self, page: "NetworkPage", prog: network.Program, listening: bool = False):
        super().__init__()
        self.page, self.prog, self.listening = page, prog, listening
        self.tile = QLabel()
        self.tile.setFixedSize(28, 28)
        self.tile.setAlignment(Qt.AlignCenter)
        self.tile.setPixmap(icons.pixmap("apps", 22, page.c.theme.muted, self.devicePixelRatioF()))
        more = IconButton(tr("More"), 30)
        more.setIcon(icons.icon("more_horizontal", page.c.theme.muted))
        more.clicked.connect(lambda: self._menu(more))
        self.count = _label("", "muted")
        controls = QWidget()
        h = QHBoxLayout(controls)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        h.addWidget(self.count)
        h.addWidget(more)
        self.row = SettingRow(_para(ltr(prog.name)), "", controls, self.tile)
        self.row.title.setTextFormat(Qt.PlainText)
        self.row.description.setTextFormat(Qt.PlainText)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.row)
        self.pills = QHBoxLayout()
        self.pills.setContentsMargins(0, 4, 0, 0)
        self.pills.setSpacing(6)
        self.new_pill = _label(tr("New"), "chip")
        self.warn_pill = _label("", "badge")
        self.pills.addWidget(self.new_pill)
        self.pills.addWidget(self.warn_pill)
        self.pills.addStretch()
        self.row.layout().itemAt(1 if self.row.layout().count() > 2 else 0).layout().addLayout(self.pills)
        self.more_text = _label("", "muted", wrap=True)
        self.more_text.setContentsMargins(58, 0, 16, 12)
        self.more_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.more_text.hide()
        lay.addWidget(self.more_text)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(prog.name)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self._menu(None, self.mapToGlobal(pos)))
        self.update_from(prog, False)

    def update_from(self, prog: network.Program, new: bool):
        self.prog = prog
        with _sig_lock:
            sig = _signatures.get(prog.path) if prog.path else None
        line, warn = describe(prog, sig)
        if self.listening:
            ports = sorted({c.local_port for c in prog.open_ports})
            named = [f"{port} ({tr(network.port_name(port))})" if network.port_name(port) else str(port)
                     for port in ports[:6]]
            line = _para(tr("Ports: {ports}", ports=", ".join(named)) + ("…" if len(ports) > 6 else ""))
            if any(port in network.RISKY_PORTS for port in ports) and not warn:
                warn = tr("Lets others sign in or share files")
            self.count.setText("")
        else:
            n = len([c for c in prog.conns if c.where != network.THIS_PC])
            self.count.setText(tr("{count} connections", count=n) if n != 1 else tr("1 connection"))
        if self.row.description.text() != line:
            self.row.description.setText(line)
            self.row.description.setVisible(bool(line))
        self.new_pill.setVisible(new)
        self.warn_pill.setText(warn)
        self.warn_pill.setVisible(bool(warn))
        if self.more_text.isVisible():
            self.more_text.setText(details(prog, self.listening))

    def set_icon(self, icon: QIcon):
        self.tile.setPixmap(icon.pixmap(QSize(24, 24), self.devicePixelRatioF()))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            shown = not self.more_text.isVisible()
            if shown:
                self.more_text.setText(details(self.prog, self.listening))
            self.more_text.setVisible(shown)
        super().mouseReleaseEvent(e)

    def _menu(self, button: QPushButton | None, pos=None):
        p = self.prog
        m = QMenu(self)
        round_menu(m)
        muted = self.page.c.theme.muted
        go = m.addAction(icons.icon("folder_open", muted), tr("Show in folder"))
        go.setEnabled(bool(p.path and os.path.exists(p.path)))
        go.triggered.connect(lambda: reveal_in_explorer(Path(p.path)))
        props = m.addAction(tr("Properties"))
        props.setEnabled(bool(p.path and os.path.isfile(p.path)))
        props.triggered.connect(lambda: properties(Path(p.path)))
        m.addAction(icons.icon("copy", muted), tr("Copy the details"),
                    lambda: QGuiApplication.clipboard().setText(f"{p.name}\n{details(p, self.listening)}"))
        m.addSeparator()
        m.addAction(icons.icon("search", muted), tr("Search the web for “{text}”", text=short_name(p.name, 40)),
                    self._search)
        if pos is None and button is not None:
            pos = button.mapToGlobal(button.rect().bottomLeft())
        sheet.menu(m, pos)
        sheet.forget(m)

    def _search(self):
        from .quickfind import search_url
        p = self.prog
        what = p.name if not p.exe or p.exe.lower().startswith(p.name.lower()) else f"{p.name} {p.exe}"
        QDesktopServices.openUrl(QUrl(search_url(self.page.c.settings.quick_search_engine, what)))


class UsageRow(QWidget):
    """A program and how much it used, as Windows counted it."""

    def __init__(self, page: "NetworkPage", u: network.Usage, biggest: int):
        super().__init__()
        self.u = u
        tile = QLabel()
        tile.setFixedSize(28, 28)
        tile.setAlignment(Qt.AlignCenter)
        tile.setPixmap(icons.pixmap("apps", 22, page.c.theme.muted, self.devicePixelRatioF()))
        self.tile = tile
        total = _label(human_size(u.total), "strong")
        line = tr("Received {down}", down=human_size(u.received)) + SEP + tr("Sent {up}", up=human_size(u.sent))
        name = u.name or os.path.basename(u.id) or u.id
        self.row = SettingRow(_para(ltr(name)), _para(line), total, tile)
        self.row.title.setTextFormat(Qt.PlainText)
        self.row.description.setTextFormat(Qt.PlainText)
        bar = _ShareBar(u.total / biggest if biggest else 0)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.row)
        bar.setContentsMargins(58, 0, 16, 10)
        lay.addWidget(bar)

    def set_icon(self, icon: QIcon):
        self.tile.setPixmap(icon.pixmap(QSize(24, 24), self.devicePixelRatioF()))


class _ShareBar(QWidget):
    def __init__(self, share: float):
        super().__init__()
        self.share = max(0.0, min(1.0, share))
        self.setFixedHeight(14)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = tokens.current()
        m = self.contentsMargins()
        x0, x1 = m.left(), self.width() - m.right()
        if self.layoutDirection() == Qt.RightToLeft:
            x0, x1 = m.right(), self.width() - m.left()
        y = 4
        p.setPen(Qt.NoPen)
        p.setBrush(pal.hover)
        p.drawRoundedRect(x0, y, x1 - x0, 5, 2.5, 2.5)
        width = max(3.0, (x1 - x0) * self.share)
        p.setBrush(pal.accent)
        if self.layoutDirection() == Qt.RightToLeft:
            p.drawRoundedRect(x1 - width, y, width, 5, 2.5, 2.5)
        else:
            p.drawRoundedRect(x0, y, width, 5, 2.5, 2.5)
        p.end()


# ----------------------------------------------------------------------------- the speed test's results

class SpeedCard(QFrame):
    """The speed test: a button, then live numbers while it runs, then what they mean."""

    def __init__(self, page: "NetworkPage"):
        super().__init__()
        self.page = page
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(10)
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(_label(tr("Speed test"), "h2"))
        self.status = _label(tr("See how fast your internet really is, and what it's good for."), "muted", wrap=True)
        words.addWidget(self.status)
        top.addLayout(words, 1)
        self.go = QPushButton(tr("Test my speed"))
        self.go.setObjectName("primary")
        self.go.setIcon(icons.icon("top_speed", page.c.theme.on_accent))
        self.go.setCursor(Qt.PointingHandCursor)
        self.go.clicked.connect(page.start_test)
        self.stop = QPushButton(tr("Stop"))
        self.stop.setCursor(Qt.PointingHandCursor)
        self.stop.clicked.connect(page.stop_test)
        self.stop.hide()
        top.addWidget(self.go, 0, Qt.AlignTop)
        top.addWidget(self.stop, 0, Qt.AlignTop)
        lay.addLayout(top)
        figures = QWidget()
        flow = FlowLayout(figures, spacing=10)
        self.figs: dict[str, QLabel] = {}
        for key, name in FIGURES:
            box = QFrame()
            box.setObjectName("group")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(14, 10, 14, 10)
            bl.setSpacing(0)
            bl.addWidget(_label(tr(name), "eyebrow"))
            fig = _label("—", "stat")
            bl.addWidget(fig)
            box.setMinimumWidth(150)
            self.figs[key] = fig
            flow.addWidget(box)
        self.figures = figures
        figures.hide()
        lay.addWidget(figures)
        self.uses = QWidget()
        uses_flow = FlowLayout(self.uses, spacing=6)
        self.use_pills: dict[str, QLabel] = {}
        for key, _name in USES:
            pill = _label("", "badge")
            self.use_pills[key] = pill
            uses_flow.addWidget(pill)
        self.uses.hide()
        lay.addWidget(self.uses)
        self.note = _label("", "muted", wrap=True)
        self.note.hide()
        lay.addWidget(self.note)
        self.where = _label("", "subtle", wrap=True)
        lay.addWidget(self.where)
        self.where.setText(tr("The test downloads and uploads throw-away data with Cloudflare's speed test "
                              "(speed.cloudflare.com) for about 15 seconds. It runs only when you press the button."))

    def running(self, on: bool):
        self.go.setVisible(not on)
        self.stop.setVisible(on)
        self.stop.setEnabled(True)
        if on:
            self.figures.show()
            self.uses.hide()
            self.note.hide()
            for fig in self.figs.values():
                fig.setText("—")

    def progress(self, phase: str, value: float):
        if phase == "ping":
            self.status.setText(tr("Checking the response time…"))
            self.figs["ping"].setText(ms_text(value))
        elif phase == "down":
            self.status.setText(tr("Downloading…"))
            self.figs["down"].setText(rate_text(value))
        elif phase == "up":
            self.status.setText(tr("Uploading…"))
            self.figs["up"].setText(rate_text(value))

    def show_result(self, r: network.SpeedResult, link: network.Adapter | None, fresh: bool):
        self.figures.show()
        self.figs["down"].setText(rate_text(r.down))
        self.figs["up"].setText(rate_text(r.up))
        self.figs["ping"].setText(ms_text(r.latency))
        busy = max(r.loaded_down, r.loaded_up)
        self.figs["loaded"].setText(ms_text(busy) if busy else "—")
        when = tr("Just now") if fresh else date_text(r.when, long=True)
        self.status.setText(when + SEP + tr("Jitter {ms}", ms=ms_text(r.jitter)) +
                            (SEP + tr("A short test (metered connection)") if r.small else ""))
        verdicts = network.verdicts(r)
        for key, name in USES:
            v = verdicts.get(key, "ok")
            self.use_pills[key].setText(f"{tr(name)}: {tr(VERDICT_WORDS[v])}")
            self.use_pills[key].setObjectName("chip" if v == "good" else "badge")
            self.use_pills[key].style().unpolish(self.use_pills[key])
            self.use_pills[key].style().polish(self.use_pills[key])
        self.uses.show()
        notes = []
        if link is not None and network.link_limits(r, link.link_down, link.kind):
            notes.append(tr("Your Wi-Fi is what holds the speed back: its link to the router is {link}. Moving "
                            "closer to the router, or using a cable, would likely make it faster.",
                            link=rate_text(link.link_down)) if link.kind == "wifi" else
                         tr("The cable link of this PC ({link}) is what holds the speed back.",
                            link=rate_text(link.link_down)))
        elif r.down:
            notes.append(tr("A 50 GB game would take about {time} to download.",
                            time=_duration(network.download_time(r, 50 * 10 ** 9))))
        if busy and r.latency and busy > r.latency + 100:
            notes.append(tr("The response time goes up a lot when the line is busy, so calls and games can lag "
                            "while something downloads. A router with \"Smart Queue\" or \"SQM\" fixes this."))
        self.note.setText("\n".join(notes))
        self.note.setVisible(bool(notes))
        bits = []
        if r.server:
            bits.append(tr("Measured with Cloudflare's server in {place}", place=ltr(r.server)))
        if r.used:
            bits.append(tr("Data used: {size}", size=human_size(r.used)))
        self.where.setText(SEP.join(bits) if bits else self.where.text())


def _duration(seconds: float) -> str:
    if seconds < 90:
        return tr("{count} seconds", count=max(1, round(seconds)))
    if seconds < 90 * 60:
        return tr("{count} minutes", count=round(seconds / 60))
    return tr("{count} hours", count=round(seconds / 3600, 1) if seconds < 36000 else round(seconds / 3600))


# ----------------------------------------------------------------------------- the page

class NetworkPage(QWidget):
    """Corefold Network in the window."""

    def __init__(self, controller: "Controller", header):
        super().__init__()
        self.c = controller
        self.meter = network.Meter()
        self.ticks = 0
        self.job = None
        self.usage_job = None
        self.test_job = None
        self.test: network.SpeedTest | None = None
        self.tab = "now"
        self.days = 1
        self.rows: dict[str, ProgramRow] = {}
        self.open_rows: dict[str, ProgramRow] = {}
        self.adapters: list[network.Adapter] = []
        self.seen = read_seen()
        self.first_look = self.seen is None
        self.new_keys: set[str] = set()
        self._icons = QFileIconProvider()
        self._icon_cache: dict[str, QIcon | None] = {}
        self._icon_rows: dict[str, list] = {}
        self._icon_cancel = threading.Event()
        from .startuppage import _IconMade
        self._icon_signal = _IconMade(self)
        self._icon_signal.made.connect(self._icon_made)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(12)
        lay.addLayout(header)
        holder = QWidget()
        holder.setObjectName("scrollContent")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(12)

        # -- right now
        live = QFrame()
        live.setObjectName("card")
        ll = QVBoxLayout(live)
        ll.setContentsMargins(16, 14, 16, 12)
        ll.setSpacing(8)
        nums = QHBoxLayout()
        nums.setSpacing(24)
        self.down_lab = _label("—", "stat")
        self.up_lab = _label("—", "stat")
        for title, lab, icon in ((tr("Download"), self.down_lab, "arrow_download"),
                                 (tr("Upload"), self.up_lab, "arrow_upload")):
            box = QVBoxLayout()
            box.setSpacing(0)
            head = QHBoxLayout()
            head.setSpacing(4)
            mark = QLabel()
            mark.setPixmap(icons.pixmap(icon, 14, self.c.theme.accent if icon == "arrow_download" else
                                        self.c.theme.muted, self.devicePixelRatioF()))
            head.addWidget(mark)
            head.addWidget(_label(title, "eyebrow"))
            head.addStretch()
            box.addLayout(head)
            box.addWidget(lab)
            nums.addLayout(box)
        nums.addStretch()
        self.link = _label("", "muted", wrap=True)
        self.link.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        nums.addWidget(self.link, 1)
        ll.addLayout(nums)
        self.graph = SpeedGraph()
        ll.addWidget(self.graph)
        hl.addWidget(live)

        # -- the speed test
        self.speed = SpeedCard(self)
        hl.addWidget(self.speed)

        # -- the programs
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.tabs = QButtonGroup(self)
        self.tabs.setExclusive(True)
        self.tab_buttons: dict[str, QPushButton] = {}
        for key, text in TABS:
            b = QPushButton(tr(text))
            b.setObjectName("subtab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(key == "now")
            b.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            self.tabs.addButton(b)
            self.tab_buttons[key] = b
            bar.addWidget(b)
        bar.addStretch()
        self.refresh_btn = IconButton(tr("Look again"))
        self.refresh_btn.setIcon(icons.icon("arrow_clockwise", self.c.theme.muted))
        self.refresh_btn.clicked.connect(self.look_again)
        bar.addWidget(self.refresh_btn)
        hl.addLayout(bar)
        # (one part shown at a time, each at its own height)
        self.parts = {"now": self._now_part(), "usage": self._usage_part(), "open": self._open_part()}
        for key, part in self.parts.items():
            hl.addWidget(part)   # (put in its place first: shown before, it'd be a window of its own)
            part.setVisible(key == self.tab)
        hl.addWidget(_label(tr("Everything on this page is read from this PC: nothing is sent anywhere, and "
                               "nothing keeps running when the page is closed. Only the speed test goes online, "
                               "when you ask for it."), "subtle", wrap=True))
        hl.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(holder)
        self.scroll.viewport().setAutoFillBackground(False)
        lay.addWidget(self.scroll, 1)

        self.timer = QTimer(self, interval=TICK_MS)
        self.timer.timeout.connect(self._tick)
        last = self._last_result()
        if last is not None:
            self.speed.show_result(last, None, False)

    def _now_part(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.now_heads: dict[str, QLabel] = {}
        self.now_groups: dict[str, Group] = {}
        for key, title in NOW_GROUPS:
            head = _label(tr(title), "section")
            self.now_heads[key] = head
            self.now_groups[key] = Group()
            v.addWidget(head)
            v.addWidget(self.now_groups[key])
        self.now_empty = _label(tr("Looking at what's connected…"), "muted", wrap=True)
        self.now_empty.setAlignment(Qt.AlignCenter)
        self.now_empty.setMinimumHeight(60)
        v.addWidget(self.now_empty)
        return w

    def _usage_part(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        pills = QHBoxLayout()
        pills.setSpacing(6)
        self.periods = QButtonGroup(self)
        self.periods.setExclusive(True)
        for days, text in PERIODS:
            b = QPushButton(tr(text))
            b.setObjectName("subtab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(days == self.days)
            b.clicked.connect(lambda _=False, d=days: self._period(d))
            self.periods.addButton(b)
            pills.addWidget(b)
        pills.addStretch()
        v.addLayout(pills)
        self.usage_sum = _label("", "muted", wrap=True)
        v.addWidget(self.usage_sum)
        self.usage_group = Group()
        v.addWidget(self.usage_group)
        self.usage_empty = _label("", "muted", wrap=True)
        self.usage_empty.setAlignment(Qt.AlignCenter)
        self.usage_empty.setMinimumHeight(60)
        v.addWidget(self.usage_empty)
        self.usage_open = QPushButton(tr("Open data usage in Windows Settings"))
        self.usage_open.setCursor(Qt.PointingHandCursor)
        self.usage_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("ms-settings:datausage")))
        self.usage_open.hide()
        v.addWidget(self.usage_open, 0, Qt.AlignCenter)
        v.addWidget(_label(tr("These are Windows' own counts, on every network this PC used, kept by Windows "
                              "even while this app is closed."), "subtle", wrap=True))
        return w

    def _open_part(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        v.addWidget(_label(tr("These programs wait for other devices on your network to connect to them. That's "
                              "normal for file and printer sharing, games, casting and remote help, but a "
                              "program you don't know here is worth a look. Windows Firewall decides who can "
                              "really connect: on public networks it blocks most of them."), "muted", wrap=True))
        fw = QPushButton(tr("Open Windows Firewall"))
        fw.setIcon(icons.icon("shield", self.c.theme.muted))
        fw.setCursor(Qt.PointingHandCursor)
        fw.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("windowsdefender://network/")))
        v.addWidget(fw, 0, Qt.AlignLeft if not i18n.is_rtl() else Qt.AlignRight)
        self.open_group = Group()
        v.addWidget(self.open_group)
        self.open_empty = _label(tr("No program is waiting for other devices to connect."), "muted", wrap=True)
        self.open_empty.setAlignment(Qt.AlignCenter)
        self.open_empty.setMinimumHeight(60)
        self.open_empty.hide()
        v.addWidget(self.open_empty)
        return w

    # ---- in sight or not

    def ensure_loaded(self):
        """The page was opened: read everything now (the speed is read every second while it's in sight)."""
        self.look_again(quiet=True)

    def showEvent(self, e):
        super().showEvent(e)
        if not self.timer.isActive():
            self.meter = network.Meter()   # (a new start: the time it was hidden isn't counted as speed)
            self.timer.start()
            self._tick(force=True)

    def hideEvent(self, e):
        super().hideEvent(e)
        self.timer.stop()

    def _tick(self, force: bool = False):
        if not work.alive(self):
            return
        fresh = not self.meter.at   # (the first reading only starts the count)
        try:
            down, up, self.adapters = self.meter.read()
        except Exception:
            log.debug("couldn't read the network adapters", exc_info=True)
            down = up = 0.0
        if not fresh:
            self.down_lab.setText(rate_text(down * 8))
            self.up_lab.setText(rate_text(up * 8))
            self.graph.add(down * 8, up * 8)
        self._show_link()
        self.ticks += 1
        if (force or self.ticks % LOOK_EVERY == 0) and self.tab in ("now", "open"):
            self.look_again(quiet=True)

    def _show_link(self):
        main = self.main_adapter()
        if main is None:
            self.link.setText(tr("Not connected") if not self.adapters else "")
            return
        kind = {"wifi": tr("Wi-Fi"), "ethernet": tr("Cable (Ethernet)"), "mobile": tr("Mobile data")}.get(
            main.kind, ltr(main.name))
        bits = [kind]
        if main.link_down:
            bits.append(tr("Link {speed}", speed=rate_text(main.link_down)))
        self.link.setText(i18n.join(bits) + "\n" + ltr(short_name(main.description, 48)))

    def main_adapter(self) -> network.Adapter | None:
        return next((a for a in self.adapters if a.main), self.adapters[0] if self.adapters else None)

    # ---- the programs

    def look_again(self, quiet: bool = False):
        if self.job is not None and self.job.is_alive():
            return
        if not quiet:
            self.refresh_btn.setEnabled(False)
        if self.tab == "usage":
            self.load_usage()
            return
        self.job = work.run(lambda _p: look(), lambda _: None, self._looked, name="corefold-network-look")

    def _looked(self, result):
        if not work.alive(self):
            return
        self.refresh_btn.setEnabled(True)
        if isinstance(result, Exception):
            log.warning("couldn't read the connections: %s", result)
            self.now_empty.setText(tr("Couldn't read the connections."))
            self.now_empty.show()
            return
        online = {p.key for p in result if p.internet}
        if self.seen is None:
            self.seen = set()
        fresh = online - self.seen
        if fresh:
            if not self.first_look:
                self.new_keys |= fresh
            self.seen |= fresh
            write_seen(self.seen)
        self.first_look = False
        self._fill_now(result)
        self._fill_open(result)

    def _fill_now(self, progs: list[network.Program]):
        wanted = {}
        for p in progs:
            if any(c.where == network.INTERNET for c in p.conns):
                wanted[p.key] = ("internet", p)
            elif any(c.where == network.HOME for c in p.conns):
                wanted[p.key] = ("home", p)
        for key in list(self.rows):
            row = self.rows[key]
            if key not in wanted or wanted[key][0] != row.group:
                self.now_groups[row.group].lay.removeWidget(row)
                row.hide()
                row.deleteLater()
                del self.rows[key]
        for key, (group, p) in wanted.items():
            row = self.rows.get(key)
            if row is None:
                row = self.rows[key] = ProgramRow(self, p)
                row.group = group
                self.now_groups[group].add(row)
                self._want_icon(row, p.path)
            row.update_from(p, key in self.new_keys)
        for key, group in self.now_groups.items():
            has = any(r.group == key for r in self.rows.values())
            group.setVisible(has)
            self.now_heads[key].setVisible(has)
            group.update()
        self.now_empty.setText(tr("No program is using the network right now."))
        self.now_empty.setVisible(not self.rows)
        self._start_icons()

    def _fill_open(self, progs: list[network.Program]):
        wanted = {p.key: p for p in progs if p.open_ports}
        for key in list(self.open_rows):
            if key not in wanted:
                row = self.open_rows.pop(key)
                self.open_group.lay.removeWidget(row)
                row.hide()
                row.deleteLater()
        for key, p in wanted.items():
            row = self.open_rows.get(key)
            if row is None:
                row = self.open_rows[key] = ProgramRow(self, p, listening=True)
                self.open_group.add(row)
                self._want_icon(row, p.path)
            row.update_from(p, False)
        self.open_group.setVisible(bool(self.open_rows))
        self.open_group.update()
        self.open_empty.setVisible(not self.open_rows)
        self._start_icons()

    # ---- data used

    def _period(self, days: int):
        self.days = days
        self.load_usage()

    def load_usage(self):
        if self.usage_job is not None and self.usage_job.is_alive():
            return
        self.usage_empty.setText(tr("Reading Windows' counts…"))
        self.usage_empty.show()
        days = self.days
        if days == 1:   # (today: since midnight)
            now = time.localtime()
            days = (now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec) / 86400 or 1 / 1440
        self.usage_job = work.run(lambda _p: network.usage(days), lambda _: None, self._usage_read,
                                  name="corefold-network-usage")

    def _usage_read(self, result):
        if not work.alive(self):
            return
        self.refresh_btn.setEnabled(True)
        while self.usage_group.lay.count():
            w = self.usage_group.lay.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        if isinstance(result, Exception):
            log.warning("couldn't read the data usage: %s", result)
            result = network.UsageReport()
        apps = result.apps
        if result.received is not None:
            self.usage_sum.setText(i18n.join([tr("Received {down}", down=human_size(result.received)),
                                              tr("Sent {up}", up=human_size(result.sent or 0))]))
        self.usage_sum.setVisible(result.received is not None)
        # (Windows Settings shows each program's share when Windows doesn't let other programs read it)
        self.usage_open.setVisible(apps is None)
        if apps is None:
            self.usage_group.hide()
            self.usage_empty.setText(tr("Windows doesn't share how much each program used with other programs on "
                                        "this PC. Windows Settings shows it.") if result.received is not None else
                                     tr("Windows doesn't share how much this PC used with other programs. Windows "
                                        "Settings shows it."))
            self.usage_empty.show()
            return
        biggest = apps[0].total if apps else 0
        for u in apps[:60]:
            row = UsageRow(self, u, biggest)
            self.usage_group.add(row)
            if u.id and os.path.isabs(u.id):
                self._want_icon(row, u.id)
        self.usage_group.setVisible(bool(apps))
        self.usage_empty.setText(tr("Nothing used the network in this time."))
        self.usage_empty.setVisible(not apps)
        self._start_icons()

    # ---- tabs

    def show_tab(self, key: str):
        self.tab = key
        self.tab_buttons[key].setChecked(True)
        for k, part in self.parts.items():
            part.setVisible(k == key)
        if key == "usage":
            self.load_usage()
        else:
            self.look_again(quiet=True)

    # ---- the speed test

    def _last_result(self) -> network.SpeedResult | None:
        tests = getattr(self.c.settings, "network_tests", []) or []
        return network.SpeedResult.from_dict(tests[-1]) if tests else None

    def start_test(self):
        if self.test_job is not None and self.test_job.is_alive():
            return
        small = network.metered() is True
        s = self.c.settings
        if small or not s.network_test_ok:
            text = tr("The speed test downloads and uploads throw-away data with Cloudflare's speed test "
                      "(speed.cloudflare.com) for about 15 seconds. Like any website, Cloudflare sees your "
                      "internet address. Nothing about you or this PC is sent, and the results stay on this PC.")
            if small:
                text += "\n\n" + tr("Your connection is metered, so this is a short test that uses about {size}.",
                                    size=human_size(network.SMALL_DOWN * 2 << 20))
            else:
                text += "\n\n" + tr("On a fast line it can use up to {size} of data.",
                                    size=human_size(network.MAX_DOWN + network.MAX_UP))
            box = QMessageBox(QMessageBox.Question, tr("Test your speed?"), text, parent=self)
            box.setTextFormat(Qt.PlainText)
            yes = box.addButton(tr("Start the test"), QMessageBox.AcceptRole)
            box.addButton(tr("Cancel"), QMessageBox.RejectRole)
            sheet.run(box)
            go = box.clickedButton() is yes
            box.deleteLater()
            if not go:
                return
            if not small:
                s.network_test_ok = True
                self.c.settings_changed()
        self.test = network.SpeedTest(small=small)
        self.speed.running(True)
        self.speed.status.setText(tr("Starting…"))
        test = self.test
        self.test_job = work.run(lambda progress: test.run(lambda phase, value: progress((phase, value))),
                                 self._test_progress, self._tested, name="corefold-network-speed")

    def stop_test(self):
        if self.test is not None:
            self.test.stop()
            self.speed.stop.setEnabled(False)
            self.speed.status.setText(tr("Stopping…"))

    def _test_progress(self, info):
        if work.alive(self):
            self.speed.progress(*info)

    def _tested(self, result):
        if not work.alive(self):
            return
        self.speed.running(False)
        self.test = None
        if isinstance(result, network.Stopped):
            self.speed.status.setText(tr("The test was stopped."))
            self.speed.figures.hide()
            return
        if isinstance(result, Exception) or not isinstance(result, network.SpeedResult):
            log.warning("the speed test didn't finish: %s", result)
            self.speed.figures.hide()
            self.speed.status.setText(tr("The speed test couldn't reach Cloudflare. Check that you're online, "
                                         "then try again."))
            return
        s = self.c.settings
        s.network_tests = (list(s.network_tests or []) + [result.as_dict()])[-network.MAX_TESTS:]
        self.c.settings_changed()
        self.speed.show_result(result, self.main_adapter(), True)

    # ---- icons

    def _want_icon(self, row, path: str):
        if not path:
            return
        key = path
        if key in self._icon_cache:
            if self._icon_cache[key] is not None:
                row.set_icon(self._icon_cache[key])
            return
        if sys.platform != "win32":   # (Qt's own, for tests elsewhere)
            ic = self._icons.icon(QFileInfo(path)) if os.path.isfile(path) else None
            self._icon_cache[key] = ic if ic is not None and not ic.isNull() else None
            if self._icon_cache[key] is not None:
                row.set_icon(self._icon_cache[key])
            return
        self._icon_rows.setdefault(key, []).append(row)

    def _start_icons(self):
        if not self._icon_rows or sys.platform != "win32":
            return
        from .startuppage import _make_icons
        wanted = {key: [key] for key in self._icon_rows if key not in self._icon_cache}
        for key in wanted:
            self._icon_cache[key] = None   # (asked for: not asked again while it's read)
        if not wanted:
            return
        self._icon_cancel = threading.Event()
        threading.Thread(target=_make_icons, args=(wanted, self._icon_cancel, self._icon_signal.made),
                         name="corefold-network-icons", daemon=True).start()

    def _icon_made(self, key: str, image):
        ic = QIcon(QPixmap.fromImage(image)) if image is not None else None
        self._icon_cache[key] = ic if ic is not None and not ic.isNull() else None
        for row in self._icon_rows.pop(key, []):
            if ic is not None and work.alive(row):
                row.set_icon(ic)

    def closeEvent(self, e):
        self._icon_cancel.set()
        if self.test is not None:
            self.test.stop()
        super().closeEvent(e)
