"""Rakiza Core Files: one workspace for every Files tool.

At the top: the page's name, Add files / Add folder, a box to find a tool and All tools. In the middle: the list of
files (name, kind, size, how they're doing), with a side panel of the tools that fit what's selected; picking one
shows its choices and the button that starts it, right there. Underneath: the operations started, as they go,
with what came out. Before anything is added: a place to drop files and the pinned tools, so a tool can be picked
first and its files added after.

The tools come from filetools.py; the list, the selection and the operations live in filework.WorkState (kept by
the controller), so they stay while this page is made again. Nothing here is a window of its own: the library of
tools opens over the page, and a tool's choices show inside the side panel (sheet.set_inline_host)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                               QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import filetools as ft
from . import filework as fw
from . import icons, sheet
from .core import human_size, short_name
from .i18n import is_rtl, tr

if TYPE_CHECKING:
    from .window import MainWindow

# the logo's teal: Files' main buttons and what's selected (the rest of Rakiza Core keeps its accent)
BRAND = {True: ("#3fb6a2", "#0b1f1b"), False: ("#0f7a6c", "#ffffff")}
PANEL_WIDTH = 340
NARROW = 900          # (px) narrower than this, the side panel folds away until it's wanted
STATE_WORDS = {fw.READY: "Ready", fw.WORKING: "Working…", fw.DONE: "Done", fw.PROBLEM: "Problem",
               fw.CANCELLED: "Cancelled"}
STATE_ICONS = {fw.READY: "", fw.WORKING: "arrow_sync", fw.DONE: "checkmark", fw.PROBLEM: "warning",
               fw.CANCELLED: "dismiss"}
KIND_WORDS = {"image": "Picture", "audio": "Sound", "video": "Video", "pdf": "PDF", "document": "Document",
              "archive": "Archive", "folder": "Folder", "other": "File"}
KIND_ICONS = {"image": "image", "audio": "music_note_2", "video": "video", "pdf": "document_pdf",
              "document": "document_text", "archive": "archive", "folder": "folder", "other": "document"}


def _alive(w) -> bool:
    try:
        from shiboken6 import isValid
        return w is not None and isValid(w)
    except ImportError:
        return w is not None


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    if wrap:
        lab.setMinimumWidth(1)
    return lab


def _button(text: str, name: str | None = None, on_click=None, icon: str = "", color=None) -> QPushButton:
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    if icon and color is not None:
        b.setIcon(icons.icon(icon, color))
        b.setIconSize(QSize(16, 16))
    if on_click is not None:
        b.clicked.connect(lambda _=False: on_click())
    return b


def css(t) -> str:
    """Files' own look on top of Rakiza Core's: its main buttons and selection in the logo's teal, quiet cards
    close to the page with a thin edge, and one height for its buttons."""
    p = t.tokens
    teal, on_teal = BRAND[t.dark]
    tc = QColor(teal)
    soft = QColor(p.surface)
    soft = QColor(round(soft.red() * 0.84 + tc.red() * 0.16), round(soft.green() * 0.84 + tc.green() * 0.16),
                  round(soft.blue() * 0.84 + tc.blue() * 0.16)).name()
    hover = QColor(teal).lighter(110 if t.dark else 92).name()
    lead = "right" if is_rtl() else "left"
    return f"""
QWidget#filesWorkspace QPushButton {{ min-height: 20px; }}
QWidget#filesWorkspace QPushButton#primary {{ background: {teal}; border: 1px solid {teal}; color: {on_teal}; }}
QWidget#filesWorkspace QPushButton#primary:hover {{ background: {hover}; border-color: {hover}; }}
QWidget#filesWorkspace QPushButton#primary:disabled {{ background: {p.select.name()}; border-color: {p.select.name()};
        color: {p.faint.name()}; }}
QWidget#filesWorkspace QPushButton#link {{ background: transparent; border: none; padding: 2px 4px; color: {teal};
        font-weight: 600; text-align: {lead}; }}
QWidget#filesWorkspace QPushButton#link:hover {{ text-decoration: underline; }}
QFrame#filesCard {{ background: {p.surface.name()}; border: 1px solid {p.border.name()}; border-radius: 12px; }}
QFrame#fileTool {{ background: {p.surface.name()}; border: 1px solid {p.border.name()}; border-radius: 10px; }}
QFrame#fileTool:hover {{ border-color: {p.line_strong.name()}; background: {p.hover.name()}; }}
QFrame#fileTool:focus {{ border-color: {teal}; }}
QFrame#fileTool[picked="true"] {{ border-color: {teal}; background: {soft}; }}
QFrame#fileTool[off="true"] {{ background: transparent; }}
QFrame#fileTool[off="true"]:hover {{ border-color: {p.border.name()}; background: transparent; }}
QFrame#comingCard {{ background: transparent; border: 1px dashed {p.line_strong.name()}; border-radius: 10px; }}
QLabel#statusPill {{ background: {p.raised.name()}; border: 1px solid {p.line_strong.name()}; border-radius: 9px;
        padding: 1px 8px; color: {p.muted.name()}; font-weight: 600; }}
QLabel#fitNote {{ background: {p.canvas.name()}; border: 1px solid {p.border.name()}; border-{lead}: 3px solid {p.warning.name()};
        border-radius: 8px; padding: 7px 10px; }}
QLabel#fitStop {{ background: {p.canvas.name()}; border: 1px solid {p.border.name()}; border-{lead}: 3px solid {p.danger.name()};
        border-radius: 8px; padding: 7px 10px; }}
QPushButton#slot {{ padding: 5px 12px; border-radius: 8px; }}
QPushButton#slot:checked {{ background: {soft}; border-color: {teal}; }}
QPushButton#chip {{ background: transparent; border: 1px solid {p.line_strong.name()}; border-radius: 13px;
        padding: 3px 12px; color: {p.muted.name()}; font-weight: 600; }}
QPushButton#chip:hover {{ color: {p.text.name()}; }}
QPushButton#chip:checked {{ background: {teal}; border-color: {teal}; color: {on_teal}; }}
QTreeWidget#fileTable {{ background: transparent; border: none; }}
QTreeWidget#fileTable::item {{ padding: 6px 2px; border-bottom: 1px solid {p.border.name()}; }}
QTreeWidget#fileTable::item:selected {{ background: {soft}; color: {p.text.name()}; }}
QTreeWidget#fileTable::item:hover:!selected {{ background: {p.hover.name()}; }}
QProgressBar#opBar::chunk {{ background: {teal}; }}
QFrame#libraryPanel {{ background: {p.raised.name()}; border: 1px solid {p.line_strong.name()}; border-radius: 14px; }}
"""


class ElidedLabel(QLabel):
    """One line that ends in … when it doesn't fit (the whole text when pointed at)."""

    def __init__(self, text: str = "", name: str | None = None):
        super().__init__()
        self.full = text
        if name:
            self.setObjectName(name)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(20)
        self.setToolTip(text)
        self._fit()

    def setFullText(self, text: str):
        self.full = text
        self.setToolTip(text)
        self._fit()

    def _fit(self):
        self.setText(QFontMetrics(self.font()).elidedText(self.full, Qt.ElideRight, max(20, self.width())))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._fit()


def _tile(icon_name: str, size: int, t, off: bool = False) -> QLabel:
    tile = QLabel()
    color = QColor(t.tokens.faint if off else BRAND[t.dark][0])
    tile.setPixmap(icons.soft_tile(icon_name, size, color, tile.devicePixelRatioF() or 1.0))
    tile.setFixedSize(size, size)
    tile.setAttribute(Qt.WA_TransparentForMouseEvents)
    return tile


class ToolTile(QFrame):
    """A tool as a small card of the same size as every other: its icon, its name, what it does in one line, and a
    pin. Click it (or Enter) to pick it. A tool that doesn't fit what's selected is shown dimmed, with why."""
    chosen = Signal(str)
    pin_toggled = Signal(str, bool)

    def __init__(self, tool: ft.FileTool, t, pinned: bool, why_not: str = "", picked: bool = False,
                 show_pin: bool = True):
        super().__init__()
        self.tool = tool
        self.why_not = why_not
        self.setObjectName("fileTool")
        self.setProperty("off", bool(why_not))
        self.setProperty("picked", picked)
        self.setCursor(Qt.ArrowCursor if why_not else Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus if why_not else Qt.StrongFocus)
        self.setAccessibleName(tr(tool.name))
        self.setAccessibleDescription(why_not or tr(tool.line))
        self.setFixedHeight(64)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 8, 10)
        lay.setSpacing(12)
        lay.addWidget(_tile(tool.icon, 36, t, off=bool(why_not)), 0, Qt.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(1)
        name = ElidedLabel(tr(tool.name), "cardTitle")
        line = ElidedLabel(why_not or tr(tool.line), "subtle")
        for w in (name, line):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        if why_not:
            name.setStyleSheet(f"color: {t.tokens.muted.name()};")
        words.addWidget(name)
        words.addWidget(line)
        lay.addLayout(words, 1)
        self.pin = None
        if show_pin:
            self.pin = QPushButton()
            self.pin.setObjectName("iconButton")
            self.pin.setFixedSize(30, 30)
            self.pin.setCursor(Qt.PointingHandCursor)
            self.pin.setCheckable(True)
            self.pin.setChecked(pinned)
            self.pin.setFocusPolicy(Qt.TabFocus)
            self._pin_look(t)
            self.pin.toggled.connect(lambda on: (self._pin_look(t), self.pin_toggled.emit(tool.id, on)))
            lay.addWidget(self.pin, 0, Qt.AlignVCenter)

    def _pin_look(self, t):
        on = self.pin.isChecked()
        self.pin.setIcon(icons.icon("pin" if on else "pin_off", BRAND[t.dark][0] if on else t.tokens.faint))
        words = tr("Unpin") if on else tr("Pin")
        self.pin.setToolTip(words)
        self.pin.setAccessibleName(f"{words}: {tr(self.tool.name)}")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()) and not self.why_not:
            self.chosen.emit(self.tool.id)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and not self.why_not:
            self.chosen.emit(self.tool.id)
        else:
            super().keyPressEvent(e)


class ComingCard(QFrame):
    """A feature on the roadmap: its name, a line, and how far along it is. Never a button: nothing to start."""

    def __init__(self, item: ft.Upcoming, t):
        super().__init__()
        self.item = item
        self.setObjectName("comingCard")
        self.setFixedHeight(64)
        self.setAccessibleName(f"{tr(item.name)}: {tr(ft.STATUS_NAMES[item.status])}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)
        lay.addWidget(_tile(item.icon, 36, t, off=True), 0, Qt.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(1)
        name = ElidedLabel(tr(item.name), "cardTitle")
        name.setStyleSheet(f"color: {t.tokens.muted.name()};")
        words.addWidget(name)
        words.addWidget(ElidedLabel(tr(item.line), "subtle"))
        lay.addLayout(words, 1)
        self.pill = _label(tr(ft.STATUS_NAMES[item.status]), "statusPill")
        lay.addWidget(self.pill, 0, Qt.AlignVCenter)


class _SortItem(QTreeWidgetItem):
    """Sorted by size as a number (not as words), and by name ignoring case."""

    def __lt__(self, other):
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        a, b = self.data(col, Qt.UserRole + 1), other.data(col, Qt.UserRole + 1)
        if a is not None and b is not None:
            return a < b
        return self.text(col).lower() < other.text(col).lower()


class FileTable(QTreeWidget):
    """The files and folders to work on. Several can be selected (Ctrl, Shift, Ctrl + A); Delete takes them off the
    list (nothing on the PC is touched). More can be dropped on it."""
    remove_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("fileTable")
        self.setColumnCount(4)
        self.setHeaderLabels([tr("Name"), tr("Kind"), tr("Size"), tr("Status")])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAllColumnsShowFocus(True)
        self.setSortingEnabled(True)
        self.sortByColumn(-1, Qt.AscendingOrder)
        self.setIconSize(QSize(18, 18))
        self.setFrameShape(QFrame.NoFrame)
        self.setAccessibleName(tr("Files to work on"))
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        head = self.header()
        head.setStretchLastSection(False)
        head.setSectionResizeMode(0, QHeaderView.Stretch)
        for col, width in ((1, 120), (2, 96), (3, 130)):
            head.setSectionResizeMode(col, QHeaderView.Interactive)
            self.setColumnWidth(col, width)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.selectedItems():
            self.remove_requested.emit()
            return
        super().keyPressEvent(e)


class _DropCover(QWidget):
    """While files are dragged over the workspace: a clear edge and what letting go does."""

    def __init__(self, parent: QWidget, t):
        super().__init__(parent)
        self.t = t
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        teal = QColor(BRAND[self.t.dark][0])
        back = QColor(self.t.tokens.canvas)
        back.setAlpha(215)
        r = self.rect().adjusted(2, 2, -2, -2)
        p.setBrush(back)
        pen = QPen(teal, 2, Qt.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(r, 14, 14)
        f = self.font()
        f.setPointSizeF(f.pointSizeF() * 1.35)
        f.setBold(True)
        p.setFont(f)
        p.setPen(self.t.tokens.text)
        p.drawText(r, Qt.AlignCenter, tr("Let go to add them to the list"))


class OpRow(QFrame):
    """One operation: what it is, how far it got, and what can be done with it now."""

    def __init__(self, tray: "OpsTray", op: fw.Op):
        super().__init__()
        self.tray = tray
        self.job_id = op.job_id
        t = tray.t
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.mark = QLabel()
        self.mark.setFixedSize(18, 18)
        row.addWidget(self.mark, 0, Qt.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(0)
        self.title = ElidedLabel("", None)
        self.detail = ElidedLabel("", "subtle")
        words.addWidget(self.title)
        words.addWidget(self.detail)
        row.addLayout(words, 1)
        self.cancel = _button(tr("Cancel"), "ghost", lambda: tray.c.cancel_job(self.job_id))
        self.open = _button(tr("Open"), "ghost", self._open)
        self.folder = _button(tr("Show in folder"), "ghost", self._show)
        self.again = _button(tr("Try again"), "ghost", self._again)
        for b in (self.cancel, self.open, self.folder, self.again):
            row.addWidget(b, 0, Qt.AlignVCenter)
        lay.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setObjectName("opBar")
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        lay.addWidget(self.bar)
        self.t = t
        self.update_from(op)

    def update_from(self, op: fw.Op):
        t = self.t
        self.op = op
        self.title.setFullText(f"{op.title} · {op.what}" if op.what else op.title)
        if op.state == fw.WAITING:
            detail, icon, color = tr("Waiting for the one before it"), "timer", t.tokens.muted
        elif op.state == fw.RUNNING:
            detail = op.detail or tr("{percent}%", percent=round(op.progress * 100))
            icon, color = "arrow_sync", BRAND[t.dark][0]
        elif op.state == fw.FINISHED:
            detail, icon = op.detail, "warning" if op.errors else "checkmark"
            color = t.tokens.warning if op.errors else t.tokens.success
        elif op.state == fw.FAILED:
            detail, icon, color = tr("Couldn't do it: {why}", why=op.detail), "warning", t.tokens.danger
        else:
            detail, icon, color = op.detail, "dismiss", t.tokens.muted
        self.detail.setFullText(detail)
        self.detail.setStyleSheet(f"color: {t.tokens.danger.name()};" if op.state == fw.FAILED else "")
        self.mark.setPixmap(icons.pixmap(icon, 18, color, self.devicePixelRatioF() or 1.0))
        self.mark.setToolTip(detail)
        running = not op.over
        self.bar.setVisible(running)
        self.bar.setValue(round(op.progress * 1000))
        self.cancel.setVisible(running)
        out = self._output()
        self.open.setVisible(op.state == fw.FINISHED and out is not None and out.is_file())
        self.folder.setVisible(op.state == fw.FINISHED and out is not None)
        self.again.setVisible(op.can_retry)
        self.setAccessibleName(f"{self.title.full}: {detail}")

    def _output(self) -> Path | None:
        for p in self.op.outputs:
            try:
                if Path(p).exists():
                    return Path(p)
            except OSError:
                continue
        return None

    def _open(self):
        from .core import os_open
        out = self._output()
        if out is not None:
            try:
                os_open(out)
            except OSError as e:
                self.tray.c.tell(tr("Couldn't open the file"), str(e))

    def _show(self):
        from .winutil import reveal_in_explorer
        out = self._output()
        if out is not None:
            reveal_in_explorer(out)

    def _again(self):
        op = self.op
        if op.can_retry:
            self.tray.c.start_job(op.tool, [Path(f) for f in op.files], dict(op.params), dict(op.rename))


class OpsTray(QFrame):
    """The operations started from Rakiza Core's jobs: running ones with their progress, finished ones with what
    came out. Folds away to one line."""

    def __init__(self, ws: "FilesWorkspace"):
        super().__init__()
        self.ws, self.c, self.t = ws, ws.c, ws.t
        self.state = ws.state
        self.setObjectName("filesCard")
        self.rows: dict[int, OpRow] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 8, 12, 8)
        lay.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = _label(tr("Operations"), "cardTitle")
        self.summary = _label("", "subtle")
        head.addWidget(self.title)
        head.addWidget(self.summary)
        head.addStretch()
        self.clear_btn = _button(tr("Clear finished"), "small", self.state.forget_finished)
        head.addWidget(self.clear_btn)
        self.fold = QPushButton()
        self.fold.setObjectName("iconButton")
        self.fold.setFixedSize(30, 30)
        self.fold.setCursor(Qt.PointingHandCursor)
        self.fold.clicked.connect(self.toggle)
        head.addWidget(self.fold)
        lay.addLayout(head)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.NoFrame)
        self.area.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner.setObjectName("scrollContent")
        self.list = QVBoxLayout(inner)
        self.list.setContentsMargins(0, 0, 4, 0)
        self.list.setSpacing(0)
        self.list.addStretch()
        self.area.setWidget(inner)
        lay.addWidget(self.area)
        self.empty = _label(tr("What you start here shows up here, with its progress and what came out."), "subtle",
                            wrap=True)
        lay.addWidget(self.empty)
        self.state.ops_changed.connect(self.refresh)
        self.refresh()
        self._show_open(self.c.settings.files_ops_open)

    def toggle(self):
        self.c.settings.files_ops_open = not self.c.settings.files_ops_open
        self.c.settings.save()
        self._show_open(self.c.settings.files_ops_open)

    def _show_open(self, on: bool):
        has = bool(self.state.ops)
        self.area.setVisible(on and has)
        self.empty.setVisible(on and not has)
        self.fold.setIcon(icons.icon("chevron_down" if on else ("chevron_left" if is_rtl() else "chevron_right"),
                                     self.t.tokens.muted))
        words = tr("Fold away") if on else tr("Show")
        self.fold.setToolTip(words)
        self.fold.setAccessibleName(f"{words}: {tr('Operations')}")
        if has:
            shown = min(len(self.state.ops), 3)
            self.area.setFixedHeight(shown * 52 + 4)

    def open_up(self):
        if not self.c.settings.files_ops_open:
            self.toggle()

    def refresh(self):
        if not _alive(self):
            return
        ops = self.state.ops
        ids = {o.job_id for o in ops}
        for job_id in [j for j in self.rows if j not in ids]:
            row = self.rows.pop(job_id)
            row.hide()
            row.deleteLater()
        for i, o in enumerate(ops):
            row = self.rows.get(o.job_id)
            if row is None:
                row = OpRow(self, o)
                self.rows[o.job_id] = row
                self.list.insertWidget(i, row)
            else:
                row.update_from(o)
                if self.list.indexOf(row) != i:
                    self.list.removeWidget(row)
                    self.list.insertWidget(i, row)
        running = self.state.running()
        done = len(ops) - running
        bits = []
        if running:
            bits.append(tr("{count} running", count=running))
        if done:
            bits.append(tr("{count} finished", count=done))
        self.summary.setText(" · ".join(bits))
        self.clear_btn.setVisible(done > 0)
        self._show_open(self.c.settings.files_ops_open)


class ToolPanel(QFrame):
    """Beside the files: the tools that fit what's selected (and, folded under them, the ones that don't, with
    why); a tool picked shows what it works on, its choices and the button that starts it."""

    def __init__(self, ws: "FilesWorkspace"):
        super().__init__()
        self.ws, self.c, self.w, self.t = ws, ws.c, ws.w, ws.t
        self.state = ws.state
        self.setObjectName("filesCard")
        self.armed = False             # a tool was started from here: the next dialog it asks with shows here
        self.inline = None             # the dialog showing here now
        self._after = None             # what to do once that dialog has gone
        self._launched: tuple | None = None   # the tool and files whose choices were shown by themselves already
        self._show_off = False
        self.slot = None               # the Converter action picked (Convert and edit, the archive tools)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.NoFrame)
        self.area.viewport().setAutoFillBackground(False)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.area, 1)
        self.foot = QWidget()
        fl = QVBoxLayout(self.foot)
        fl.setContentsMargins(16, 6, 16, 14)
        fl.setSpacing(6)
        self.run_btn = _button("", "primary", self._run)
        self.run_btn.setMinimumHeight(38)
        fl.addWidget(self.run_btn)
        self.page_btn = _button("", "link", self._open_page)
        fl.addWidget(self.page_btn, 0, Qt.AlignHCenter)
        outer.addWidget(self.foot)
        self.setMinimumWidth(PANEL_WIDTH)
        self.setMaximumWidth(PANEL_WIDTH)
        self._pending_slots = False

    # ---- what's shown

    def tool(self) -> ft.FileTool | None:
        tool = ft.by_id(self.state.tool) if self.state.tool else None
        return tool if tool is not None and tool in ft.offered(self.c.settings) else None

    def refresh(self):
        """Shown again for the files and the tool as they are now (a dialog showing here goes first)."""
        if self.inline is not None:
            self.later(self.refresh)
            return
        old = self.area.takeWidget()
        if old is not None:
            old.hide()
            old.deleteLater()
        body = QWidget()
        body.setObjectName("scrollContent")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 14, 16, 8)
        lay.setSpacing(10)
        self.body_lay = lay
        tool = self.tool()
        if tool is None:
            self.state.tool = None
            self._list(lay)
            self.foot.hide()
        else:
            self._tool_view(lay, tool)
        lay.addStretch()
        self.area.setWidget(body)

    def _summary(self, items: list[Path]) -> str:
        if not items:
            return tr("Nothing added yet")
        kinds = sorted({tr(KIND_WORDS.get(self.state.kinds.get(str(p), "other"), "File")) for p in items})
        if self.state.selected:
            head = tr("{count} selected", count=len(items))
        else:
            head = tr("All {count} items", count=len(items))
        return f"{head} · {', '.join(kinds[:3])}{'…' if len(kinds) > 3 else ''}"

    def _list(self, lay):
        items = self.state.chosen()
        lay.addWidget(_label(tr("Tools for your selection") if self.state.selected else tr("Tools for these files"),
                             "h2", wrap=True))
        lay.addWidget(_label(self._summary(items), "subtle", wrap=True))
        fits, misfits = [], []
        for tool in ft.offered(self.c.settings):
            f = ft.fit(tool, items, self.state.kinds)
            (fits if f.ok and f.usable else misfits).append((tool, f))
        if not fits:
            lay.addWidget(_label(tr("No tool works on what's selected. Select other files, or add some."), "muted",
                                 wrap=True))
        last = None
        for tool, f in fits:
            if tool.category != last:
                last = tool.category
                lay.addWidget(_label(tr(ft.CATEGORY_NAMES[tool.category]), "section"))
            tile = ToolTile(tool, self.t, False, show_pin=False)
            tile.chosen.connect(self.ws.choose_tool)
            if f.skipped:
                tile.setToolTip(tr("Leaves out {count} that it doesn't take", count=len(f.skipped)))
            lay.addWidget(tile)
        if misfits and items:
            more = _button(tr("Not for this selection ({count})", count=len(misfits)), "flat",
                           self._toggle_off)
            more.setIcon(icons.icon("chevron_down" if self._show_off else
                                    ("chevron_left" if is_rtl() else "chevron_right"), self.t.tokens.muted))
            lay.addSpacing(4)
            lay.addWidget(more, 0, Qt.AlignLeading)
            if self._show_off:
                for tool, f in misfits:
                    lay.addWidget(ToolTile(tool, self.t, False, why_not=f.why_not, show_pin=False))

    def _toggle_off(self):
        self._show_off = not self._show_off
        self.refresh()

    def _tool_view(self, lay, tool: ft.FileTool):
        t = self.t
        items = self.state.chosen()
        f = ft.fit(tool, items, self.state.kinds)
        back = _button(tr("All tools for this selection") if items else tr("Back"), "flat", self.ws.unchoose_tool,
                       "arrow_right" if is_rtl() else "arrow_left", t.tokens.muted)
        lay.addWidget(back, 0, Qt.AlignLeading)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(_tile(tool.icon, 40, t), 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(_label(tr(tool.name), "h2", wrap=True))
        words.addWidget(_label(tr(tool.line), "muted", wrap=True))
        head.addLayout(words, 1)
        lay.addLayout(head)
        lay.addWidget(_label(tr("Works on: {what} · {many}", what=ft.what_it_takes(tool), many=ft.how_many(tool)),
                             "subtle", wrap=True))
        if f.skipped and f.ok:  # (nothing is done to what it doesn't take: said before it starts)
            names = ", ".join(short_name(p.name, 30) for p, _ in f.skipped[:3])
            more = f" {tr('…and {count} more', count=len(f.skipped) - 3)}" if len(f.skipped) > 3 else ""
            lay.addWidget(_label(tr("Left out, as {tool} doesn't take them: {names}", tool=tr(tool.name),
                                    names=names) + more, "fitNote", wrap=True))
        if not f.ok and items:
            lay.addWidget(_label(f.why_not, "fitStop", wrap=True))
        line = QFrame()
        line.setObjectName("line")
        lay.addWidget(line)
        self.settings_lay = QVBoxLayout()
        self.settings_lay.setSpacing(8)
        lay.addLayout(self.settings_lay)
        self.slot = None
        usable = f.usable if f.ok else []
        if tool.how.startswith("slots:") and usable:
            self._slots(tool, usable)
        elif tool.view:
            self.settings_lay.addWidget(_label(tr("Opens here, in its own view, with the files you picked. "
                                                  "Files › at the top brings you back to the list.")
                                               if usable else tr("Opens here, in its own view."), "subtle", wrap=True))
        self._foot(tool, f)
        key = (tool.id, tuple(str(p) for p in usable))
        if tool.settings and not tool.how.startswith("slots:") and usable and self._launched != key:
            self._launched = key  # (its choices show by themselves once; Cancel leaves them closed)
            QTimer.singleShot(0, self, lambda: self._start(tool, usable))

    def _foot(self, tool: ft.FileTool, f: ft.Fit):
        self.foot.show()
        usable = f.usable if f.ok else []
        n = len(usable)
        self.run_btn.setEnabled(True)
        self.run_btn.show()
        if not self.state.items:
            if tool.empty_ok:
                self.run_btn.setText(tr("Open {name}", name=tr(tool.name)))
            else:
                self.run_btn.setText(tr("Add the files it needs…"))
        elif not f.ok:
            self.run_btn.setText(tr("Add the files it needs…"))
        elif tool.how.startswith("slots:"):
            self.run_btn.setText(self.slot.tool.description if self.slot is not None else tr("Pick what to do above"))
            self.run_btn.setEnabled(self.slot is not None)
        elif tool.view:
            self.run_btn.setText(tr("Open with {count} items", count=n) if n > 1 else tr("Open {name}",
                                                                                            name=tr(tool.name)))
        elif tool.settings:
            self.run_btn.setText(tr("Choose how…"))
        else:
            self.run_btn.setText(tr("Start on {count} items", count=n) if n > 1 else tr("Start"))
        self.page_btn.setVisible(bool(tool.page))
        if tool.page:
            from .tools import BY_PAGE
            owner = BY_PAGE.get(tool.page)
            self.page_btn.setText(tr("Everything {name} can do", name=tr(owner.nav)) if owner else "")

    # ---- the Converter's actions (Convert and edit, and the archive tools)

    def _slots(self, tool: ft.FileTool, usable: list[Path]):
        slots, pending = slots_for(self.c, tool, usable)
        if isinstance(slots, str):
            self.settings_lay.addWidget(_label(slots, "fitStop", wrap=True))
            return
        from .atomic.widgets import FlowLayout
        from .wheel import GROUP_NAMES, GROUPS, action_of
        groups: dict[str, list] = {}
        for sl in slots:
            groups.setdefault(GROUPS.get(action_of(sl.tool.key), "convert"), []).append(sl)
        self.slot_group = QButtonGroup(self)
        self.slot_group.setExclusive(True)
        dimmed = 0
        for group in ("convert", "edit", "shrink", "read", "archive"):
            if group not in groups:
                continue
            self.settings_lay.addWidget(_label(tr(GROUP_NAMES[group]), "section"))
            holder = QWidget()
            flow = FlowLayout(holder, spacing=6)
            for sl in groups[group]:
                b = QPushButton(sl.tool.label)
                b.setObjectName("slot")
                b.setCheckable(True)
                b.setEnabled(sl.ok)
                b.setCursor(Qt.PointingHandCursor if sl.ok else Qt.ForbiddenCursor)
                b.setToolTip(sl.reason or sl.tool.description)
                b.setAccessibleDescription(sl.reason or sl.tool.description)
                if not sl.ok:
                    dimmed += 1
                b.clicked.connect(lambda _=False, s=sl: self._pick_slot(s))
                self.slot_group.addButton(b)
                flow.addWidget(b)
            self.settings_lay.addWidget(holder)
        if not slots:
            self.settings_lay.addWidget(_label(tr("Nothing to do with these files. Check Settings → Converter → "
                                                  "Formats if you hid some options."), "muted", wrap=True))
        if dimmed:
            self.settings_lay.addWidget(_label(tr("{count} don't fit (greyed out, point at one to see why)",
                                                  count=dimmed), "subtle", wrap=True))
        self.slot_note = _label("", "subtle", wrap=True)
        self.settings_lay.addWidget(self.slot_note)
        if pending and not self._pending_slots:  # (a slow look at the file first, off the window's thread)
            self._pending_slots = True
            warm_then(self, usable, self._warmed)

    def _warmed(self):
        self._pending_slots = False
        if _alive(self) and self.inline is None and self.slot is None:
            self.refresh()

    def _pick_slot(self, sl):
        """An action picked: one with choices shows them here at once; any other waits for the button."""
        self.later(lambda: self._picked(sl))

    def _picked(self, sl):
        tool = self.tool()
        if tool is None:
            return
        self.slot = sl
        self.slot_note.setText(sl.tool.description)
        f = ft.fit(tool, self.state.chosen(), self.state.kinds)
        self._foot(tool, f)
        if sl.tool.ask or sl.tool.opens:
            self._arm()
            self.c.tool_chosen(sl.tool, f.usable)

    # ---- starting

    def _run(self):
        self.later(self._run_now)

    def _run_now(self):
        tool = self.tool()
        if tool is None:
            return
        items = self.state.chosen()
        f = ft.fit(tool, items, self.state.kinds)
        if not f.ok and not (tool.empty_ok and not items):
            self.ws.add_for(tool)
            return
        if tool.how.startswith("slots:"):
            if self.slot is not None:
                self._arm()
                self.c.tool_chosen(self.slot.tool, f.usable)
            return
        self._start(tool, f.usable)

    def _start(self, tool: ft.FileTool, usable: list[Path]):
        if not _alive(self) or self.tool() is not tool:
            return
        if tool.settings:
            self._arm()
        run(self.w, tool, usable)

    def _open_page(self):
        tool = self.tool()
        if tool is not None and tool.page:
            self.later(lambda: self.w.open_page(tool.page))

    def _arm(self):
        self.armed = True

    def disarm(self, *_):
        self.armed = False

    # ---- a tool's choices, shown here instead of in a sheet

    def wants(self, dlg) -> bool:
        return (self.armed and _alive(self) and self.isVisible() and self.ws.isVisible()
                and hasattr(dlg, "lay") and hasattr(dlg, "params"))

    def run_inline(self, dlg) -> int:
        """Shows dlg in the panel (under the tool's name), with its own buttons, until it's answered."""
        flags = dlg.windowFlags()
        home = dlg.parentWidget()
        box = self.settings_lay
        dlg.setParent(self.area.widget(), Qt.Widget)
        dlg.setProperty("sheet", "true")
        dlg.setAttribute(Qt.WA_StyledBackground, False)
        dlg.setAutoFillBackground(False)
        dlg.lay.setContentsMargins(0, 4, 0, 4)
        tool = self.tool()
        for lab in dlg.findChildren(QLabel, "h2")[:1]:  # (the tool's name is just above)
            if tool is not None and lab.text().strip().rstrip("…") == tr(tool.name).strip():
                lab.hide()
        dlg.style().unpolish(dlg)
        dlg.style().polish(dlg)
        box.addWidget(dlg)
        self.inline = dlg
        self.run_btn.hide()
        want = dlg.sizeHint().width() + 40
        room = max(PANEL_WIDTH, int(self.ws.width() * 0.55))
        self.setMaximumWidth(max(PANEL_WIDTH, min(want, room)))
        self.setMinimumWidth(max(PANEL_WIDTH, min(want, room)))
        self.ws.panel_wanted(True)
        dlg.show()
        QTimer.singleShot(0, self, lambda: self.area.ensureWidgetVisible(dlg))
        try:
            result = dlg.exec()
        finally:
            if _alive(dlg):
                box.removeWidget(dlg)
                dlg.hide()
                dlg.setProperty("sheet", None)
                dlg.setParent(home if _alive(home) else None, flags)
            self.inline = None
            if _alive(self):
                self.setMinimumWidth(PANEL_WIDTH)
                self.setMaximumWidth(PANEL_WIDTH)
                if not result:
                    self.disarm()
                after, self._after = self._after, None
                QTimer.singleShot(0, self, after if after is not None else self._after_dialog)
        return result

    def _after_dialog(self):
        if _alive(self) and self.inline is None:
            self.refresh()

    def later(self, fn):
        """fn now, or (while a dialog shows here) once it has gone: it's cancelled first."""
        if self.inline is not None and _alive(self.inline):
            self._after = fn
            self.inline.reject()
        else:
            fn()

    def close_inline(self):
        if self.inline is not None and _alive(self.inline):
            self.inline.reject()


def slots_for(c, tool: ft.FileTool, files: list[Path]):
    """The Converter's actions for these files that belong to this tool, with why some don't fit: (slots, still to
    look closer), or a sentence when there's nothing to show."""
    from .converters import Slot, catalog, pick
    s = c.settings
    if tool.how == "slots:convert":
        kinds = {ft.kind_of(f) for f in files}
        if len(kinds) > 1:
            return tr("Select files of one kind (only pictures, say) to convert them together"), False
        pk = pick(files, set(s.hidden_tools), s.favorites, True, s.tool_order, False, quick=True)
        return [sl for sl in pk.slots if not sl.tool.key.startswith(("pack:", "files:", "archive:"))], pk.pending
    if tool.how == "slots:archive":
        tools = [t for t in catalog(len(files) > 1)["archive"] if t.key.startswith("archive:")]
    else:
        tools = [t for t in catalog(len(files) > 1)["files"] if t.key.startswith("pack:")]
    out = []
    for t in tools:
        if t.key in s.hidden_tools:
            continue
        try:
            reason = t.check(files) if t.check else None
        except Exception:
            reason = None
        out.append(Slot(t, reason))
    return out, False


def warm_then(owner, files: list[Path], then):
    """Reads what the slower checks need (in the background), then calls then() on the window's thread."""
    import threading

    from PySide6.QtCore import QObject

    class _Done(QObject):
        done = Signal()
    note = _Done(owner)
    note.done.connect(then)
    note.done.connect(note.deleteLater)

    def work():
        from .converters import warm_up
        try:
            warm_up(files)
        except Exception:
            pass
        try:
            note.done.emit()
        except RuntimeError:
            pass
    threading.Thread(target=work, daemon=True, name="corefold-files-look").start()


def run(w: "MainWindow", tool: ft.FileTool, files: list[Path]):
    """Starts a Files tool on these files the way its own page would."""
    c = w.c
    how = tool.how
    if how.startswith("pdf:"):
        key = how[4:]
        w._build_page("PDF")
        pdf = getattr(w, "pdf_page", None)
        if pdf is None:
            return
        if tool.view:
            w.open_page("PDF")
        if key == "pictures":
            pdf.pictures_to_pdf(files)
        elif files:
            pdf.run(key, files)
        else:
            pdf.start(key)
    elif how == "archive-open":
        if files:
            for p in files[:5]:
                c.open_browser(p)
        else:
            c.choose_archive(w)
    elif how.startswith("page:"):
        page = how[5:]
        w.open_page(page)
        if not files:
            return
        if page == "Rename" and getattr(w, "rename_page", None) is not None:
            w.rename_page.add(files)
        elif page == "Check" and getattr(w, "check_page", None) is not None:
            w.check_page.add(files)
        elif page == "Unlock" and getattr(w, "unlock_page", None) is not None:
            w.unlock_page.check(files)
        elif page == "Organizer" and getattr(w, "organizer_page", None) is not None:
            w.organizer_page.set_folder(files[0])
        elif page == "Backup" and getattr(w, "backup_page", None) is not None:
            for p in files:
                if p.is_dir():
                    w.backup_page.add_folder(p)
            w.backup_page.refresh()


class ToolLibrary(QFrame):
    """All tools, over the workspace (never a window of its own): find one by its name or what it does, by
    category, pin the ones used often; and, on its own tab, what's coming."""

    def __init__(self, ws: "FilesWorkspace"):
        super().__init__(ws.body_host)
        self.ws, self.c, self.t = ws, ws.c, ws.t
        self.setObjectName("libraryPanel")
        self.setAttribute(Qt.WA_StyledBackground)
        self.category = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 16, 16)
        lay.setSpacing(12)
        head = QHBoxLayout()
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(_label(tr("All tools"), "title"))
        words.addWidget(_label(tr("Pick a tool to see its choices beside your files. Pin the ones you use often."),
                               "subtle", wrap=True))
        head.addLayout(words, 1)
        close = QPushButton()
        close.setObjectName("iconButton")
        close.setFixedSize(32, 32)
        close.setCursor(Qt.PointingHandCursor)
        close.setIcon(icons.icon("dismiss", self.t.tokens.muted))
        close.setToolTip(tr("Close"))
        close.setAccessibleName(tr("Close"))
        close.clicked.connect(ws.close_library)
        head.addWidget(close, 0, Qt.AlignTop)
        lay.addLayout(head)
        bar = QHBoxLayout()
        bar.setSpacing(10)
        seg = QFrame()
        seg.setObjectName("segmented")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.tabs = QButtonGroup(self)
        self.tab_buttons = {}
        for key, words_ in (("now", tr("Available now")), ("soon", tr("Coming"))):
            b = QPushButton(words_)
            b.setObjectName("segment")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            self.tabs.addButton(b)
            self.tab_buttons[key] = b
            sl.addWidget(b)
        bar.addWidget(seg)
        bar.addStretch()
        self.manage_btn = _button(tr("Switch tools on or off"), "small", lambda: self.show_tab("manage"))
        bar.addWidget(self.manage_btn)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Find by name or what it does"))
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName(tr("Find a tool"))
        self.search.setMinimumWidth(220)
        self.search.textChanged.connect(lambda _t: self.fill())
        bar.addWidget(self.search)
        lay.addLayout(bar)
        self.chips_box = QWidget()
        from .atomic.widgets import FlowLayout
        chips = FlowLayout(self.chips_box, spacing=6)
        self.chip_group = QButtonGroup(self)
        for key, name in [("", tr("All"))] + [(k, tr(n)) for k, n, _ in ft.CATEGORIES]:
            b = QPushButton(name)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setChecked(key == "")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._category(k))
            self.chip_group.addButton(b)
            chips.addWidget(b)
        lay.addWidget(self.chips_box)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.NoFrame)
        self.area.viewport().setAutoFillBackground(False)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.area, 1)
        self.tab = "now"
        QShortcut(QKeySequence(Qt.Key_Escape), self, ws.close_library, context=Qt.WidgetWithChildrenShortcut)
        self.hide()

    def _category(self, key: str):
        self.category = key
        self.fill()

    def show_tab(self, tab: str):
        self.tab = tab
        self.ws.state.library = tab
        for key, b in self.tab_buttons.items():
            b.setChecked(key == tab)
        self.chips_box.setVisible(tab == "now")
        self.search.setVisible(tab != "manage")
        self.manage_btn.setVisible(tab != "manage")
        self.fill()

    def fill(self):
        from .atomic.widgets import ResponsiveGrid
        old = self.area.takeWidget()
        if old is not None:
            old.hide()
            old.deleteLater()
        body = QWidget()
        body.setObjectName("scrollContent")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(10)
        query = self.search.text().strip().lower()
        if self.tab == "now":
            pins = {t.id for t in ft.pinned(self.c.settings)}
            items = self.ws.state.chosen()
            shown = 0
            for key, name, _ in ft.CATEGORIES:
                if self.category and key != self.category:
                    continue
                tools = [t for t in ft.offered(self.c.settings) if t.category == key and
                         (not query or query in tr(t.name).lower() or query in tr(t.line).lower()
                          or query in t.name.lower() or query in t.line.lower())]
                if not tools:
                    continue
                lay.addWidget(_label(tr(name), "section"))
                grid = ResponsiveGrid(250, 10, 3)
                for tool in tools:
                    tile = ToolTile(tool, self.t, tool.id in pins, picked=tool.id == self.ws.state.tool)
                    if items:  # (said under the name, so it's clear before it's picked)
                        f = ft.fit(tool, items, self.ws.state.kinds)
                        if not f.ok:
                            tile.setToolTip(f"{tr(tool.line)}\n{f.why_not}")
                    tile.chosen.connect(self.ws.choose_tool)
                    tile.pin_toggled.connect(self.ws.pin)
                    grid.add(tile)
                    shown += 1
                lay.addWidget(grid)
            if not shown:
                lay.addWidget(_label(tr("No tool matches “{words}”.", words=self.search.text().strip())
                                     if query else tr("No tools here are switched on."), "muted", wrap=True))
        elif self.tab == "soon":
            lay.addWidget(_label(tr("On Rakiza Core's roadmap for Files. They can't be used yet, and they have no "
                                    "dates."), "muted", wrap=True))
            items = [u for u in ft.upcoming() if not query or query in tr(u.name).lower()
                     or query in tr(u.line).lower()]
            if items:
                grid = ResponsiveGrid(250, 10, 3)
                for u in items:
                    grid.add(ComingCard(u, self.t))
                lay.addWidget(grid)
            else:
                lay.addWidget(_label(tr("Nothing else is confirmed for now. When a feature joins the roadmap it "
                                        "shows up here, with how far along it is."), "subtle", wrap=True))
            lay.addSpacing(8)
            lay.addWidget(_label(tr("Coming soon for the rest of Rakiza Core"), "cardTitle"))
            lay.addWidget(_label(tr("The tools of the sections that are resting for now are on Coming soon, in "
                                    "the sidebar."), "subtle", wrap=True))
            lay.addWidget(_button(tr("Open Coming soon"), "link", lambda: self.ws.w.open_page("Soon")), 0,
                          Qt.AlignLeading)
        else:
            self._manage(lay)
        lay.addStretch()
        self.area.setWidget(body)

    def _manage(self, lay):
        """Which of Files' tools are switched on (one that's off isn't loaded at all)."""
        from . import tools as all_tools
        from .atomic.widgets import ResponsiveGrid
        from .window import ToolSwitchCard
        back = _button(tr("All tools"), "flat", lambda: self.show_tab("now"),
                       "arrow_right" if is_rtl() else "arrow_left", self.t.tokens.muted)
        lay.addWidget(back, 0, Qt.AlignLeading)
        lay.addWidget(_label(tr("Switch off what you don't use: it isn't loaded at all, so Rakiza Core stays light. "
                                "Its settings are kept for when you switch it back on."), "subtle", wrap=True))
        sec = all_tools.SECTION_BY_PAGE["Files"]
        on = {t.id for t in self.ws.w.tools_on}
        fresh = {t.id for t in all_tools.new_tools(self.c.settings)}
        grid = ResponsiveGrid(320, 10, 2)
        tools = all_tools.in_section(self.c.settings, sec)
        for tool in [x for x in tools if x.id in on] + [x for x in tools if x.id not in on]:
            grid.add(ToolSwitchCard(tool, tool.id in on, lambda n=tool.page: self.ws.w.open_page(n),
                                    self.ws.w._tool_switched, self.t.dark, self.devicePixelRatioF(),
                                    fresh=tool.id in fresh, accent=QColor(BRAND[self.t.dark][0])))
        lay.addWidget(grid)


class FilesWorkspace(QWidget):
    """The Files page (see the module's words)."""

    def __init__(self, window: "MainWindow", sec):
        super().__init__()
        self.w, self.c, self.sec = window, window.c, sec
        self.t = self.c.theme
        self.state: fw.WorkState = self.c.files_work
        self.setObjectName("filesWorkspace")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAcceptDrops(True)
        self.setStyleSheet(css(self.t))
        self._narrow = False
        self._panel_open = False
        self._syncing = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(14)
        lay.addWidget(self._header())
        self.body_host = QWidget()
        bh = QVBoxLayout(self.body_host)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(12)
        body = QHBoxLayout()
        body.setSpacing(16)
        self.center = QStackedWidget()
        self.center.addWidget(self._empty_view())
        self.center.addWidget(self._table_view())
        body.addWidget(self.center, 1)
        self.center.installEventFilter(self)
        self.panel = ToolPanel(self)
        body.addWidget(self.panel)
        bh.addLayout(body, 1)
        self.ops = OpsTray(self)
        bh.addWidget(self.ops)
        lay.addWidget(self.body_host, 1)
        self.cover = _DropCover(self.body_host, self.t)
        self.library = ToolLibrary(self)
        self.state.files_changed.connect(self._files_changed)
        self.state.statuses_changed.connect(self._statuses_changed)
        self.state.job_started.connect(self._job_started)
        sheet.set_inline_host(self._inline_target)
        self._files_changed()
        if self.state.library:
            QTimer.singleShot(0, self, lambda: self.open_library(self.state.library))

    # ---- the parts

    def _header(self) -> QWidget:
        from PySide6.QtWidgets import QBoxLayout
        t = self.t
        self.head_box = QWidget()
        head = QBoxLayout(QBoxLayout.LeftToRight, self.head_box)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(16)
        self.head_lay = head
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(_label(tr(self.sec.name), "h1"))
        words.addWidget(_label(tr("Add files or folders, then pick what to do with them. Everything happens on "
                                  "your PC."), "muted", wrap=True))
        coming = _button(tr("Discover what's coming"), "link", lambda: self.open_library("soon"))
        coming.setIcon(icons.icon("sparkle", BRAND[t.dark][0]))
        coming.setIconSize(QSize(14, 14))
        words.addWidget(coming, 0, Qt.AlignLeading)
        head.addLayout(words, 1)
        acts = QHBoxLayout()
        acts.setSpacing(8)
        self.all_btn = _button(tr("All tools"), None, lambda: self.open_library("now"), "grid", t.text)
        self.find = QLineEdit()
        self.find.setPlaceholderText(tr("Find a tool"))
        self.find.setAccessibleName(tr("Find a tool"))
        self.find.setClearButtonEnabled(True)
        self.find.setMinimumWidth(130)
        self.find.setMaximumWidth(220)
        self.find.textEdited.connect(self._find)
        self.find.returnPressed.connect(self._find_enter)
        self.folder_btn = _button(tr("Add folder"), None, self.add_folder, "folder_open", t.text)
        self.add_btn = _button(tr("Add files"), "primary", self.add_files, "add", QColor(BRAND[t.dark][1]))
        for b in (self.all_btn, self.find, self.folder_btn, self.add_btn):
            b.setFixedHeight(36)
            acts.addWidget(b)
        acts.addStretch(0)
        head.addLayout(acts)
        head.setAlignment(acts, Qt.AlignTop)
        self.acts = acts
        return self.head_box

    def _empty_view(self) -> QWidget:
        from .atomic.widgets import ResponsiveGrid
        from .panel import DropZone
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        # (clicked() sends a "checked" flag: never taken for a tool)
        self.drop = DropZone(lambda *_: self.add_files(), lambda *_: self.c.paste(),
                             title=tr("Drop files or folders here"),
                             sub=tr("Or pick them on your PC. Nothing is uploaded."),
                             hot_title=tr("Let go to add them to the list"),
                             buttons=[(tr("Choose files…"), lambda *_: self.add_files(), "primary"),
                                      (tr("Choose a folder…"), lambda *_: self.add_folder()),
                                      (tr("Paste (Ctrl + V)"), lambda *_: self.c.paste())])
        self.drop.setMinimumHeight(170)
        self.drop.dropped.connect(lambda paths: self.state.add(paths))
        lay.addWidget(self.drop)
        head = QHBoxLayout()
        words = QVBoxLayout()
        words.setSpacing(0)
        words.addWidget(_label(tr("Pinned tools"), "title"))
        words.addWidget(_label(tr("Pick a tool first, then add the files it needs."), "subtle", wrap=True))
        head.addLayout(words, 1)
        head.addWidget(_button(tr("All tools"), "link", lambda: self.open_library("now")), 0, Qt.AlignBottom)
        lay.addLayout(head)
        self.pins_grid = ResponsiveGrid(250, 10, 3)
        lay.addWidget(self.pins_grid)
        self.no_pins = _label(tr("Pin the tools you use often in All tools, and they wait for you here."), "muted",
                              wrap=True)
        lay.addWidget(self.no_pins)
        lay.addStretch()
        self._fill_pins()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.viewport().setAutoFillBackground(False)
        w.setObjectName("scrollContent")
        scroll.setWidget(w)
        return scroll

    def _fill_pins(self):
        grid = self.pins_grid
        for tile in grid.tiles:
            grid.grid.removeWidget(tile)
            tile.hide()
            tile.deleteLater()
        grid.tiles = []
        pins = ft.pinned(self.c.settings)
        for tool in pins:
            tile = ToolTile(tool, self.t, True, picked=tool.id == self.state.tool)
            tile.chosen.connect(self.choose_tool)
            tile.pin_toggled.connect(self.pin)
            grid.add(tile)
        self.no_pins.setVisible(not pins)

    def _table_view(self) -> QWidget:
        card = QFrame()
        card.setObjectName("filesCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(4, 6, 4, 4)
        lay.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 2, 8, 6)
        bar.setSpacing(6)
        self.count = _label("", "cardTitle")
        bar.addWidget(self.count)
        bar.addStretch()
        t = self.t
        self.remove_btn = _button(tr("Remove from list"), "ghost", self.remove_selected, "dismiss", t.text)
        self.remove_btn.setToolTip(tr("Takes them off this list only: nothing on your PC is deleted."))
        self.clear_btn = _button(tr("Clear list"), "ghost", self.state.clear)
        self.clear_btn.setToolTip(tr("Takes everything off this list only: nothing on your PC is deleted."))
        self.tools_btn = _button(tr("Tools"), None, self._toggle_panel, "puzzle_piece", t.text)
        self.tools_btn.setCheckable(True)
        for b in (self.remove_btn, self.clear_btn, self.tools_btn):
            bar.addWidget(b)
        lay.addLayout(bar)
        self.table = FileTable()
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.remove_requested.connect(self.remove_selected)
        self.table.itemDoubleClicked.connect(self._open_item)
        lay.addWidget(self.table, 1)
        self.hint = _label(tr("Drop more here to add them · Delete takes the selected ones off the list"), "subtle")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setContentsMargins(8, 6, 8, 4)
        lay.addWidget(self.hint)
        return card

    # ---- the list

    def _files_changed(self):
        if not _alive(self):
            return
        has = bool(self.state.items)
        was = self.center.currentIndex()
        self.center.setCurrentIndex(1 if has else 0)
        if has and was == 0 and self.isVisible():  # (from the drop area to the list, gently)
            from .atomic import motion, tokens
            if tokens.animations:
                motion.fade_in(self.center.currentWidget(), 180)
        self._fill_table()
        self._fit_panel()
        self.panel.refresh()

    def _icon(self, name: str, color) -> object:
        """(One icon of each kind for the whole list: thousands of rows share them.)"""
        key = (name, QColor(color).name())
        cache = self.__dict__.setdefault("_icons", {})
        if key not in cache:
            cache[key] = icons.icon(name, color)
        return cache[key]

    def _fill_table(self):
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        t = self.t
        table = self.table
        self._syncing = True
        try:
            table.setSortingEnabled(False)
            table.clear()
            chosen = set(self.state.selected)
            rows, picked = [], []
            for p in self.state.items:
                name = str(p)
                kind = self.state.kinds.get(name, "other")
                item = _SortItem()
                item.setData(0, Qt.UserRole, name)
                item.setText(0, short_name(p.name, 70))
                item.setToolTip(0, name)
                item.setIcon(0, self._icon(KIND_ICONS.get(kind, "document"), t.tokens.muted))
                words = tr(KIND_WORDS.get(kind, "File"))
                if kind not in ("folder", "pdf") and p.suffix:
                    words = f"{words} · {p.suffix[1:].upper()}"
                item.setText(1, words)
                self._size_cell(item, p)
                self._status_cell(item, name)
                rows.append(item)
                if name in chosen:
                    picked.append(len(rows) - 1)
            table.addTopLevelItems(rows)  # (all at once: one change for the view, not thousands)
            if picked:
                model = table.model()
                sel = QItemSelection()
                start = prev = picked[0]
                for i in picked[1:] + [None]:  # (runs of rows next to each other: one range each)
                    if i is not None and i == prev + 1:
                        prev = i
                        continue
                    sel.select(model.index(start, 0), model.index(prev, model.columnCount() - 1))
                    if i is not None:
                        start = prev = i
                table.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
            table.setSortingEnabled(True)
        finally:
            self._syncing = False
        n, sel_n = len(self.state.items), len(self.state.selected)
        self.count.setText(tr("{count} items", count=n) + (f" · {tr('{count} selected', count=sel_n)}"
                                                           if sel_n else ""))
        self.remove_btn.setEnabled(sel_n > 0)

    def _size_cell(self, item, p: Path):
        size = self.state.size_of(p)
        if size is None:
            item.setText(2, "…")
            item.setData(2, Qt.UserRole + 1, -1)
        else:
            item.setText(2, human_size(size[0]) + ("" if size[1] else "+"))
            item.setData(2, Qt.UserRole + 1, size[0])

    def _status_cell(self, item, name: str):
        state, words = self.state.status.get(name, (fw.READY, ""))
        t = self.t
        color = {fw.DONE: t.tokens.success, fw.PROBLEM: t.tokens.danger, fw.WORKING: QColor(BRAND[t.dark][0])}.get(
            state, t.tokens.muted)
        item.setText(3, tr(STATE_WORDS[state]))
        item.setForeground(3, color)
        icon = STATE_ICONS[state]
        item.setIcon(3, self._icon(icon, color) if icon else self._icon("circle", t.tokens.faint))
        item.setToolTip(3, words or tr(STATE_WORDS[state]))

    def _statuses_changed(self):
        if not _alive(self):
            return
        for i in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(i)
            name = item.data(0, Qt.UserRole)
            self._size_cell(item, Path(name))
            self._status_cell(item, name)

    def _selection_changed(self):
        if self._syncing:
            return
        names = [it.data(0, Qt.UserRole) for it in self.table.selectedItems()]
        order = {str(p): i for i, p in enumerate(self.state.items)}
        names.sort(key=lambda n: order.get(n, 0))
        self._syncing = True
        try:
            self.state.selected = names
        finally:
            self._syncing = False
        n, sel = len(self.state.items), len(names)
        self.count.setText(tr("{count} items", count=n) + (f" · {tr('{count} selected', count=sel)}" if sel else ""))
        self.remove_btn.setEnabled(sel > 0)
        QTimer.singleShot(0, self, self.panel.refresh)  # (once, after a drag across many rows)

    def _open_item(self, item, _col):
        from .core import os_open
        try:
            os_open(Path(item.data(0, Qt.UserRole)))
        except OSError:
            pass

    def remove_selected(self):
        self.panel.later(lambda: self.state.remove(list(self.state.selected)))

    def add_files(self, for_tool: ft.FileTool | None = None):
        from .core import IMAGE_EXT
        kinds = for_tool.kinds if isinstance(for_tool, ft.FileTool) else ()
        filters = ""
        if kinds == ("pdf",):
            filters = "PDF (*.pdf)"
        elif kinds == ("image",):
            filters = f"{tr('Pictures')} ({' '.join('*' + e for e in sorted(IMAGE_EXT))})"
        names, _ = QFileDialog.getOpenFileNames(self, tr("Add files"), str(Path.home()), filters)
        if names:
            self.state.add([Path(n) for n in names])

    def add_folder(self):
        name = QFileDialog.getExistingDirectory(self, tr("Add folder"), str(Path.home()))
        if name:
            self.state.add([Path(name)])

    def add_for(self, tool: ft.FileTool):
        """The files a picked tool still needs (the tool stays picked as they're added)."""
        if tool.kinds == ("folder",):
            self.add_folder()
        else:
            self.add_files(tool)

    # ---- the tools

    def choose_tool(self, tool_id: str):
        def go():
            self.state.tool = tool_id
            self.panel.slot = None
            self.close_library()
            self._panel_open = True
            self._fit_panel()
            self.panel.refresh()
            self._mark_pins()
        self.panel.later(go)

    def unchoose_tool(self):
        def go():
            self.state.tool = None
            self.panel._launched = None
            self._fit_panel()
            self.panel.refresh()
            self._mark_pins()
        self.panel.later(go)

    def _mark_pins(self):
        for tile in self.pins_grid.tiles:
            tile.setProperty("picked", tile.tool.id == self.state.tool)
            tile.style().unpolish(tile)
            tile.style().polish(tile)

    def pin(self, tool_id: str, on: bool):
        ft.set_pinned(self.c.settings, tool_id, on)
        self.c.settings.save()
        QTimer.singleShot(0, self, self._fill_pins)

    def _job_started(self, _job_id: int):
        self.panel.disarm()
        self.ops.open_up()

    def _inline_target(self, dlg):
        if not _alive(self) or not _alive(self.panel):
            return None
        return self.panel if self.panel.wants(dlg) else None

    # ---- finding a tool, and the library over the page

    def _find(self, text: str):
        if text.strip():
            self.open_library("now")
            if self.library.search.text() != text:
                self.library.search.setText(text)
        elif self.library.isVisible() and self.library.tab == "now":
            self.library.search.clear()

    def _find_enter(self):
        query = self.find.text().strip().lower()
        hits = [t for t in ft.offered(self.c.settings)
                if query and (query in tr(t.name).lower() or query in t.name.lower())]
        if len(hits) == 1:
            self.find.clear()
            self.choose_tool(hits[0].id)

    def open_library(self, tab: str = "now"):
        lib = self.library
        lib.show_tab(tab)
        if not lib.isVisible():
            self._place_library()
            lib.show()
            lib.raise_()
            from .atomic import motion, tokens
            if tokens.animations and self.isVisible():
                motion.fade_in(lib, 160)
        if tab != "now" or not self.find.hasFocus():
            lib.search.setFocus() if tab != "manage" else lib.setFocus()

    def close_library(self):
        self.state.library = ""
        if self.library.isVisible():
            self.library.hide()
            if self.find.text():
                self.find.clear()

    def _place_library(self):
        self.library.setGeometry(self.body_host.rect())

    # ---- room: the side panel folds away when the window is narrow

    def _toggle_panel(self):
        self._panel_open = not self.panel.isVisible()
        self._fit_panel()

    def panel_wanted(self, on: bool):
        if on and not self.panel.isVisible():
            self._panel_open = True
            self._fit_panel()

    def _fit_panel(self):
        narrow = self.width() < NARROW and self.width() > 50
        self._narrow = narrow
        has = bool(self.state.items)
        picked = self.state.tool is not None
        if not has:  # (before files are added: only a tool picked first needs it)
            show = picked
        elif narrow:
            show = self._panel_open or picked or self.panel.inline is not None
        else:
            show = True
        self.panel.setVisible(show)
        self.tools_btn.setVisible(narrow and has)
        self.tools_btn.setChecked(show)

    def _fit_header(self):
        """Narrow: the buttons go under the title (none is ever cut short); the table's own buttons keep only
        their icons."""
        from PySide6.QtWidgets import QBoxLayout
        stacked = self.width() < 1040
        want = QBoxLayout.TopToBottom if stacked else QBoxLayout.LeftToRight
        if self.head_lay.direction() != want:
            self.head_lay.setDirection(want)
            self.head_lay.setAlignment(self.acts, Qt.AlignLeading if stacked else Qt.AlignTop)
        self._fit_table_bar()

    def _fit_table_bar(self):
        tight = self.center.width() < 600
        for b, words in ((self.remove_btn, tr("Remove from list")), (self.tools_btn, tr("Tools"))):
            b.setText("" if tight else words)
            b.setAccessibleName(words)
            if tight:
                b.setToolTip(words if b is self.tools_btn else
                             f"{words}\n{tr('Takes them off this list only: nothing on your PC is deleted.')}")

    def eventFilter(self, obj, e):
        if obj is self.center and e.type() == QEvent.Resize:
            self._fit_table_bar()
        return False

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_header()
        if (self.width() < NARROW) != self._narrow:
            self._fit_panel()
        self.cover.setGeometry(self.body_host.rect())
        if self.library.isVisible():
            self._place_library()

    def showEvent(self, e):
        super().showEvent(e)
        sheet.set_inline_host(self._inline_target)
        self._fit_panel()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.panel.close_inline()

    # ---- dropping files anywhere on the workspace

    def dragEnterEvent(self, e):
        if any(u.isLocalFile() for u in e.mimeData().urls()):
            e.setDropAction(Qt.CopyAction)
            e.accept()
            self.cover.setGeometry(self.body_host.rect())
            self.cover.show()
            self.cover.raise_()

    def dragMoveEvent(self, e):
        e.setDropAction(Qt.CopyAction)
        e.accept()

    def dragLeaveEvent(self, e):
        self.cover.hide()

    def dropEvent(self, e):
        self.cover.hide()
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.setDropAction(Qt.CopyAction)
            e.accept()
            self.panel.later(lambda: self.state.add(paths))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.library.isVisible():
            self.close_library()
            return
        super().keyPressEvent(e)

    def go_back(self) -> bool:
        """Back (Alt + Left, the mouse's back button) closes the library first."""
        if self.library.isVisible():
            self.close_library()
            return True
        return False
