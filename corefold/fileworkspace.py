"""Rakiza Core Files: one workspace for every Files tool.

At the top: the page's name, Add files, one box to find a tool and All tools. On the left: the list of files (a
box to tick each, its name, type, size and how it's doing) with a place under it to drop more, and the tool
library under that (a tile for each tool, by category, with what's coming on its own tab). On the right: the
files selected and the tools that fit them; the tool picked shows its choices and one big button that starts it.
Across the bottom: Activity, the operations started, as they go, with what came out.

The tools come from filetools.py; the list, the selection and the operations live in filework.WorkState (kept by
the controller), so they stay while this page is made again. Nothing here is a window of its own: a tool's
choices show inside the side panel (sheet.set_inline_host)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QDateTime, QEvent, QLocale, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QProgressBar,
                               QPushButton, QScrollArea, QSizePolicy, QStyle, QStyledItemDelegate, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from . import filetools as ft
from . import filework as fw
from . import icons, sheet
from .core import human_size, short_name
from .i18n import is_rtl, tr

if TYPE_CHECKING:
    from .window import MainWindow

# the logo's teal: Files' main buttons and what's selected (the rest of Rakiza Core keeps its accent)
BRAND = {True: ("#3fb6a2", "#0b1f1b"), False: ("#0f7a6c", "#ffffff")}
PANEL_WIDTH = 372
NARROW = 980          # (px) narrower than this, the side panel folds away until it's wanted
CHIPS_SHOWN = 6       # tools offered at first beside a selection (the rest under More)
STATE_WORDS = {fw.READY: "Ready", fw.WORKING: "Working…", fw.DONE: "Done", fw.PROBLEM: "Problem",
               fw.CANCELLED: "Cancelled"}
KIND_WORDS = {"image": "Picture", "audio": "Sound", "video": "Video", "pdf": "PDF", "document": "Document",
              "archive": "Archive", "folder": "Folder", "other": "File"}
KIND_ICONS = {"image": "image", "audio": "music_note_2", "video": "video", "pdf": "document_pdf",
              "document": "document_text", "archive": "archive", "folder": "folder", "other": "document"}
CHIP_NAMES = {"convert": "Convert", "pdf": "PDF", "archive": "Archive", "organize": "Organize", "protect": "Protect"}
COL_CHECK, COL_NAME, COL_TYPE, COL_SIZE, COL_STATUS, COL_MORE = range(6)


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


def _icon_button(icon: str, color, words: str, on_click=None, size: int = 30) -> QPushButton:
    b = QPushButton()
    b.setObjectName("iconButton")
    b.setFixedSize(size, size)
    b.setCursor(Qt.PointingHandCursor)
    b.setIcon(icons.icon(icon, color))
    b.setToolTip(words)
    b.setAccessibleName(words)
    if on_click is not None:
        b.clicked.connect(lambda _=False: on_click())
    return b


def _soft(t, amount: float = 0.16) -> str:
    """The card colour with a little of the logo's teal in it (what's selected)."""
    p = t.tokens
    tc, soft = QColor(BRAND[t.dark][0]), QColor(p.surface)
    return QColor(round(soft.red() * (1 - amount) + tc.red() * amount),
                  round(soft.green() * (1 - amount) + tc.green() * amount),
                  round(soft.blue() * (1 - amount) + tc.blue() * amount)).name()


def css(t) -> str:
    """Files' own look on top of Rakiza Core's: its main buttons and selection in the logo's teal, quiet cards
    close to the page with a thin edge, and one height for its buttons."""
    p = t.tokens
    teal, on_teal = BRAND[t.dark]
    soft = _soft(t)
    hover = QColor(teal).lighter(110 if t.dark else 92).name()
    lead = "right" if is_rtl() else "left"
    return f"""
QWidget#filesWorkspace QPushButton#primary, QPushButton#addFiles, QPushButton#bigAction {{ background: {teal};
        border: 1px solid {teal}; color: {on_teal}; }}
QWidget#filesWorkspace QPushButton#primary:hover, QPushButton#addFiles:hover, QPushButton#bigAction:hover {{
        background: {hover}; border-color: {hover}; }}
QPushButton#bigAction:disabled, QPushButton#addFiles:disabled, QWidget#filesWorkspace QPushButton#primary:disabled {{
        background: {p.select.name()}; border-color: {p.select.name()}; color: {p.faint.name()}; }}
QPushButton#addFiles {{ border-radius: 10px; padding: 0 18px; font-weight: 700; min-height: 40px; max-height: 40px; }}
QPushButton#bigAction {{ border-radius: 10px; padding: 0 16px; font-weight: 700; font-size: 15px; min-height: 44px; }}
QPushButton#allTools {{ border-radius: 10px; padding: 0 14px; min-height: 40px; max-height: 40px; }}
QLineEdit#toolSearch {{ border-radius: 10px; padding: 0 10px; min-height: 30px; max-height: 30px; }}
QComboBox#saveTo, QLineEdit#outputName {{ min-height: 26px; }}
QLabel#fieldLabel {{ font-weight: 600; }}
QWidget#filesWorkspace QPushButton#link {{ background: transparent; border: none; padding: 2px 4px; color: {teal};
        font-weight: 600; text-align: {lead}; }}
QWidget#filesWorkspace QPushButton#link:hover {{ text-decoration: underline; }}
QFrame#filesCard {{ background: {p.surface.name()}; border: 1px solid {p.border.name()}; border-radius: 12px; }}
QLabel#libTitle {{ font-size: 17px; font-weight: 700; }}
QFrame#vline {{ background: {p.line_strong.name()}; }}
QFrame#libTile {{ background: transparent; border: 1px solid {p.line_strong.name()}; border-radius: 10px; }}
QFrame#libTile:hover {{ background: {p.hover.name()}; }}
QFrame#libTile:focus {{ border-color: {teal}; }}
QFrame#libTile[picked="true"] {{ border-color: {teal}; background: {soft}; }}
QFrame#comingTile {{ background: transparent; border: 1px dashed {p.line_strong.name()}; border-radius: 10px; }}
QLabel#tileName {{ font-weight: 600; }}
QLabel#statusPill {{ background: {p.raised.name()}; border: 1px solid {p.line_strong.name()}; border-radius: 8px;
        padding: 0 6px; color: {p.muted.name()}; font-weight: 600; font-size: 11px; }}
QLabel#fitNote {{ background: {p.canvas.name()}; border: 1px solid {p.border.name()}; border-{lead}: 3px solid {p.warning.name()};
        border-radius: 8px; padding: 7px 10px; }}
QLabel#fitStop {{ background: {p.canvas.name()}; border: 1px solid {p.border.name()}; border-{lead}: 3px solid {p.danger.name()};
        border-radius: 8px; padding: 7px 10px; }}
QPushButton#slot {{ padding: 5px 12px; border-radius: 8px; }}
QPushButton#slot:checked {{ background: {soft}; border-color: {teal}; }}
QPushButton#chip {{ background: transparent; border: 1px solid {p.line_strong.name()}; border-radius: 12px;
        padding: 3px 14px; min-height: 18px; color: {p.text.name()}; }}
QPushButton#chip:hover {{ background: {p.hover.name()}; }}
QPushButton#chip:checked {{ background: {teal}; border-color: {teal}; color: {on_teal}; }}
QPushButton#toolChip {{ background: transparent; border: 1px solid {p.line_strong.name()}; border-radius: 9px;
        padding: 6px 12px; }}
QPushButton#toolChip:hover {{ background: {p.hover.name()}; }}
QPushButton#toolChip:checked {{ background: {soft}; border: 2px solid {teal}; padding: 5px 11px; }}
QFrame#dropArea {{ background: transparent; border: 1.5px dashed {p.line_strong.name()}; border-radius: 10px; }}
QFrame#dropArea:hover {{ border-color: {teal}; }}
QTreeWidget#fileTable {{ background: transparent; border: none; }}
QTreeWidget#fileTable::item {{ padding: 7px 2px; border-bottom: 1px solid {p.border.name()}; }}
QTreeWidget#fileTable::item:selected {{ background: {soft}; color: {p.text.name()}; }}
QTreeWidget#fileTable::item:hover:!selected {{ background: {p.hover.name()}; }}
QTreeWidget#fileTable QHeaderView::section {{ background: {p.surface.name()}; border: none;
        border-bottom: 1px solid {p.border.name()}; padding: 8px 4px; font-weight: 600; color: {p.text.name()}; }}
QFrame#mergeRow {{ background: {p.canvas.name()}; border: 1px solid {p.border.name()}; border-radius: 8px; }}
QListWidget#mergeList {{ background: transparent; border: none; outline: none; }}
QListWidget#mergeList::item {{ background: transparent; border: none; padding: 0; }}
QLabel#orderNumber {{ background: {p.raised.name()}; border: 1px solid {p.line_strong.name()}; border-radius: 5px;
        padding: 0 6px; color: {p.muted.name()}; }}
QProgressBar#opBar {{ background: {p.select.name()}; border: none; border-radius: 4px; min-height: 8px; max-height: 8px; }}
QProgressBar#opBar::chunk {{ background: {teal}; border-radius: 4px; }}
QFrame#activityRow {{ border-top: 1px solid {p.border.name()}; }}
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


def _vline() -> QFrame:
    line = QFrame()
    line.setObjectName("vline")
    line.setFixedSize(1, 20)
    return line


_MARKS: dict = {}


def _mark(kind: str, color: QColor, size: int = 18, dpr: float = 2.0) -> QPixmap:
    """A round mark: "done" (a tick in a filled circle) or "ring" (still going); drawn once each."""
    key = (kind, QColor(color).name(QColor.HexArgb), size, dpr)
    if key not in _MARKS:
        if len(_MARKS) > 40:
            _MARKS.clear()
        _MARKS[key] = _draw_mark(kind, QColor(color), size, dpr)
    return _MARKS[key]


def _draw_mark(kind: str, color: QColor, size: int, dpr: float) -> QPixmap:
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(1, 1, size - 2, size - 2)
    if kind == "done":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(r)
        pen = QPen(QColor("#0b1f1b") if color.lightness() > 140 else QColor("#ffffff"), size / 9)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        path = QPainterPath()
        path.moveTo(size * 0.29, size * 0.52)
        path.lineTo(size * 0.44, size * 0.66)
        path.lineTo(size * 0.72, size * 0.37)
        p.drawPath(path)
    elif kind == "ring":
        track = QColor(color)
        track.setAlpha(60)
        p.setPen(QPen(track, size / 8))
        p.drawEllipse(r.adjusted(1, 1, -1, -1))
        p.setPen(QPen(color, size / 8, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r.adjusted(1, 1, -1, -1), 90 * 16, -250 * 16)
    p.end()
    return pm


def _paint_check(p: QPainter, rect: QRect, state: Qt.CheckState, t):
    """The tick box of the list (and its heading): rounded, the logo's teal when ticked."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    box = QRectF(rect.center().x() - 8, rect.center().y() - 8, 16, 16)
    teal = QColor(BRAND[t.dark][0])
    if state == Qt.Unchecked:
        p.setPen(QPen(t.tokens.muted, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(box.adjusted(0.75, 0.75, -0.75, -0.75), 4, 4)
    else:
        p.setPen(Qt.NoPen)
        p.setBrush(teal)
        p.drawRoundedRect(box, 4, 4)
        pen = QPen(QColor(BRAND[t.dark][1]), 2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        if state == Qt.Checked:
            path = QPainterPath()
            path.moveTo(box.left() + 4, box.top() + 8.5)
            path.lineTo(box.left() + 7, box.top() + 11.2)
            path.lineTo(box.left() + 12.2, box.top() + 5.3)
            p.drawPath(path)
        else:
            p.drawLine(QPoint(round(box.left()) + 4, round(box.center().y())),
                       QPoint(round(box.right()) - 4, round(box.center().y())))
    p.restore()


# ----------------------------------------------------------------------------- the tool library's tiles

class LibTile(QFrame):
    """A tool in the library: a small square with its icon and name, all the same size, and a star when it's
    pinned (pinned tools come first beside a selection). Click it (or Enter) to pick it."""
    chosen = Signal(str)
    pin_toggled = Signal(str, bool)

    def __init__(self, key: str, name: str, icon: str, t, pinned: bool | None = None, picked: bool = False,
                 tip: str = ""):
        super().__init__()
        self.key, self.t = key, t
        self.setObjectName("libTile")
        self.setProperty("picked", picked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(name)
        self.setAccessibleDescription(tip)
        self.setToolTip(tip or name)
        self.setFixedSize(118, 86)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 12, 6, 8)
        lay.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(icons.pixmap(icon, 26, QColor(BRAND[t.dark][0]), self.devicePixelRatioF() or 1.0))
        ic.setAlignment(Qt.AlignCenter)
        ic.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(ic)
        words = QLabel(name)
        words.setObjectName("tileName")
        words.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        words.setWordWrap(True)
        words.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(words, 1)
        self.star = None
        if pinned is not None:
            self.star = QPushButton(self)
            self.star.setObjectName("iconButton")
            self.star.setCheckable(True)
            self.star.setChecked(pinned)
            self.star.setFixedSize(22, 22)
            self.star.setIconSize(QSize(14, 14))
            self.star.setCursor(Qt.PointingHandCursor)
            self.star.setFocusPolicy(Qt.TabFocus)
            self._star_look()
            self.star.toggled.connect(lambda on: (self._star_look(), self.pin_toggled.emit(key, on)))
            self._place_star()

    def _star_look(self):
        on = self.star.isChecked()
        self.star.setIcon(icons.icon("star_filled" if on else "star", "#f4b740" if on else self.t.tokens.faint))
        words = tr("Unpin") if on else tr("Pin")
        self.star.setToolTip(words)
        self.star.setAccessibleName(f"{words}: {self.accessibleName()}")
        self.star.setVisible(on or self.underMouse() or self.hasFocus())

    def _place_star(self):
        if self.star is not None:
            x = 4 if is_rtl() else self.width() - 26
            self.star.move(x, 4)

    def enterEvent(self, e):
        if self.star is not None:
            self.star.show()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self.star is not None and not self.star.isChecked():
            self.star.hide()
        super().leaveEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_star()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.chosen.emit(self.key)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.chosen.emit(self.key)
        else:
            super().keyPressEvent(e)


class ComingTile(QFrame):
    """A feature on the roadmap: its icon, name and how far along it is. Never a button: nothing to start."""

    def __init__(self, item: ft.Upcoming, t):
        super().__init__()
        self.item = item
        self.setObjectName("comingTile")
        self.setFixedSize(150, 86)
        status = tr(ft.STATUS_NAMES[item.status])
        self.setAccessibleName(f"{tr(item.name)}: {status}")
        self.setToolTip(tr(item.line))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 10, 8, 8)
        lay.setSpacing(4)
        ic = QLabel()
        ic.setPixmap(icons.pixmap(item.icon, 22, t.tokens.faint, self.devicePixelRatioF() or 1.0))
        ic.setAlignment(Qt.AlignCenter)
        lay.addWidget(ic)
        name = ElidedLabel(tr(item.name), "tileName")
        name.setAlignment(Qt.AlignCenter)
        name.setStyleSheet(f"color: {t.tokens.muted.name()};")
        lay.addWidget(name)
        self.pill = _label(status, "statusPill")
        lay.addWidget(self.pill, 0, Qt.AlignHCenter)


# ----------------------------------------------------------------------------- the list of files

class _SortItem(QTreeWidgetItem):
    """Sorted by size as a number (not as words), and by name ignoring case."""

    def __lt__(self, other):
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        a, b = self.data(col, Qt.UserRole + 1), other.data(col, Qt.UserRole + 1)
        if a is not None and b is not None:
            return a < b
        return self.text(col).lower() < other.text(col).lower()


class _CheckDelegate(QStyledItemDelegate):
    """The first column: a tick box that shows whether the row is selected."""

    def __init__(self, table: "FileTable"):
        super().__init__(table)
        self.table = table

    def paint(self, p, option, index):
        super().paint(p, option, index)
        on = bool(option.state & QStyle.State_Selected)
        _paint_check(p, option.rect, Qt.Checked if on else Qt.Unchecked, self.table.t)


class _CheckHeader(QHeaderView):
    """The list's heading: a tick box over the first column (all, some or none selected)."""
    toggled = Signal()

    def __init__(self, table: "FileTable"):
        super().__init__(Qt.Horizontal, table)
        self.table = table
        self.state = Qt.Unchecked
        self.setSectionsClickable(True)
        self.setHighlightSections(False)

    def paintSection(self, painter, rect, index):
        painter.save()
        super().paintSection(painter, rect, index)
        painter.restore()
        if index == COL_CHECK:
            _paint_check(painter, rect, self.state, self.table.t)

    def mousePressEvent(self, e):
        if self.logicalIndexAt(e.position().toPoint()) == COL_CHECK:
            self.toggled.emit()
            return
        super().mousePressEvent(e)

    def set_state(self, state):
        if state != self.state:
            self.state = state
            self.viewport().update()


class FileTable(QTreeWidget):
    """The files and folders to work on. Tick a row's box (or Ctrl/Shift + click, Ctrl + A) to select several;
    Delete takes them off the list (nothing on the PC is touched); ⋯ at the end of a row has what can be done
    with it."""
    remove_requested = Signal()
    more_requested = Signal(object, QPoint)   # (the item, where on the screen)

    def __init__(self, t):
        super().__init__()
        self.t = t
        self.setObjectName("fileTable")
        self.setColumnCount(6)
        self.head = _CheckHeader(self)
        self.setHeader(self.head)
        self.setHeaderLabels(["", tr("Name"), tr("Type"), tr("Size"), tr("Status"), ""])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAllColumnsShowFocus(True)
        self.setSortingEnabled(True)
        self.sortByColumn(-1, Qt.AscendingOrder)
        self.setIconSize(QSize(20, 20))
        self.setFrameShape(QFrame.NoFrame)
        self.setAccessibleName(tr("Files to work on"))
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setItemDelegateForColumn(COL_CHECK, _CheckDelegate(self))
        head = self.header()
        head.setStretchLastSection(False)
        head.setMinimumSectionSize(30)
        head.setDefaultAlignment(Qt.AlignLeading | Qt.AlignVCenter)
        head.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        for col, width in ((COL_CHECK, 44), (COL_TYPE, 90), (COL_SIZE, 100), (COL_STATUS, 120), (COL_MORE, 44)):
            head.setSectionResizeMode(col, QHeaderView.Fixed if col in (COL_CHECK, COL_MORE) else
                                      QHeaderView.Interactive)
            self.setColumnWidth(col, width)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.itemAt(pos) and self.more_requested.emit(self.itemAt(pos),
                                                                      self.viewport().mapToGlobal(pos)))

    def fit_columns(self, width: int):
        """A narrow list keeps the name readable: the type goes first, then the status shrinks."""
        self.setColumnHidden(COL_TYPE, width < 520)
        self.setColumnWidth(COL_STATUS, 120 if width >= 460 else 96)

    def mousePressEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        if idx.isValid() and e.button() == Qt.LeftButton and idx.column() in (COL_CHECK, COL_MORE):
            from PySide6.QtCore import QItemSelectionModel
            if idx.column() == COL_CHECK:  # (the box: this row in or out, the others stay)
                self.selectionModel().select(idx, QItemSelectionModel.Toggle | QItemSelectionModel.Rows)
                self.selectionModel().setCurrentIndex(idx, QItemSelectionModel.NoUpdate)
            else:
                item = self.itemFromIndex(idx)
                rect = self.visualRect(idx)
                self.more_requested.emit(item, self.viewport().mapToGlobal(rect.bottomLeft()))
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        if idx.isValid() and idx.column() in (COL_CHECK, COL_MORE):
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.selectedItems():
            self.remove_requested.emit()
            return
        super().keyPressEvent(e)

    def content_height(self) -> int:
        n = self.topLevelItemCount()
        row = self.sizeHintForRow(0) if n else 0
        return self.header().sizeHint().height() + n * max(row, 1) + 4


class DropArea(QFrame):
    """Under the list, a dashed place to drop more (or click to pick them). With nothing added yet it takes the
    whole card, with buttons to choose files or a folder."""

    def __init__(self, ws: "FilesWorkspace"):
        super().__init__()
        self.ws, t = ws, ws.t
        self.setObjectName("dropArea")
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(tr("Drop more files here"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)
        lay.addStretch()
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.line = QHBoxLayout()
        self.line.setSpacing(10)
        self.line.addStretch()
        self.small_icon = QLabel()
        self.small_icon.setPixmap(icons.pixmap("arrow_download", 20, t.tokens.muted, self.devicePixelRatioF() or 1.0))
        self.small_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.line.addWidget(self.small_icon)
        self.words = _label(tr("Drop more files here"), "muted")
        self.words.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.line.addWidget(self.words)
        self.folder_link = _button(tr("Add a folder"), "link", lambda: ws.add_folder())
        self.line.addWidget(self.folder_link)
        self.line.addStretch()
        lay.addWidget(self.icon)
        lay.addLayout(self.line)
        self.sub = _label(tr("Or pick them on your PC. Nothing is uploaded."), "subtle")
        self.sub.setAlignment(Qt.AlignCenter)
        self.sub.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self.sub)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()
        self.files_btn = _button(tr("Choose files…"), "primary", lambda: ws.add_files())
        self.folder_btn = _button(tr("Choose a folder…"), None, lambda: ws.add_folder())
        self.paste_btn = _button(tr("Paste (Ctrl + V)"), None, lambda: ws.c.paste())
        for b in (self.files_btn, self.folder_btn, self.paste_btn):
            b.setMinimumHeight(34)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        lay.addStretch()
        self.big = None
        self.set_big(False)

    def set_big(self, big: bool):
        if big == self.big:
            return
        self.big = big
        t = self.ws.t
        dpr = self.devicePixelRatioF() or 1.0
        self.icon.setPixmap(icons.soft_tile("arrow_download", 52, QColor(BRAND[t.dark][0]), dpr))
        self.icon.setVisible(big)
        self.small_icon.setVisible(not big)
        self.words.setText(tr("Drop files or folders here") if big else tr("Drop more files here"))
        self.words.setObjectName("h2" if big else "muted")
        self.words.style().unpolish(self.words)
        self.words.style().polish(self.words)
        self.folder_link.setVisible(not big)
        self.sub.setVisible(big)
        for b in (self.files_btn, self.folder_btn, self.paste_btn):
            b.setVisible(big)
        self.setMinimumHeight(240 if big else 58)
        self.setMaximumHeight(16777215 if big else 110)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.ws.add_files()


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


# ----------------------------------------------------------------------------- Activity

def _when(stamp: float) -> str:
    moment = QDateTime.fromSecsSinceEpoch(int(stamp))
    loc = QLocale()
    time = loc.toString(moment.time(), QLocale.ShortFormat)
    if moment.date() == QDateTime.currentDateTime().date():
        return tr("Today, {time}", time=time)
    return f"{loc.toString(moment.date(), QLocale.ShortFormat)}, {time}"


class OpRow(QFrame):
    """One operation: what it is and on what, how far it got (or where it saved), and what can be done now."""

    def __init__(self, tray: "OpsTray", op: fw.Op):
        super().__init__()
        self.tray = tray
        self.job_id = op.job_id
        self.t = tray.t
        self.setObjectName("activityRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 8, 4, 8)
        row.setSpacing(12)
        self.mark = QLabel()
        self.mark.setFixedSize(24, 24)
        row.addWidget(self.mark, 0, Qt.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(1)
        self.title = ElidedLabel("", "cardTitle")
        self.detail = ElidedLabel("", "subtle")
        words.addWidget(self.title)
        words.addWidget(self.detail)
        row.addLayout(words, 1)
        self.bar = QProgressBar()
        self.bar.setObjectName("opBar")
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedSize(220, 8)
        row.addWidget(self.bar, 0, Qt.AlignVCenter)
        self.percent = _label("", "muted")
        self.percent.setMinimumWidth(40)
        row.addWidget(self.percent, 0, Qt.AlignVCenter)
        self.time = _label("", "muted")
        row.addWidget(self.time, 0, Qt.AlignVCenter)
        self.folder = _button(tr("Open folder"), "small", self._show, "folder_open", self.t.tokens.text)
        self.again = _button(tr("Try again"), "small", self._again, "arrow_clockwise", self.t.tokens.text)
        self.cancel = _button(tr("Cancel"), "small", lambda: tray.c.cancel_job(self.job_id))
        for b in (self.folder, self.again, self.cancel):
            row.addWidget(b, 0, Qt.AlignVCenter)
        self.more = _icon_button("more_horizontal", self.t.tokens.muted, tr("More"), self._menu)
        row.addWidget(self.more, 0, Qt.AlignVCenter)
        self.update_from(op)

    def update_from(self, op: fw.Op):
        """(Only what changed: a running job sends many updates, and every row would be redrawn for each.)"""
        sig = (op.state, round(op.progress * 100), op.detail, op.title, op.what, len(op.outputs), len(op.errors),
               round(op.when))
        if sig == getattr(self, "_sig", None):
            return
        self._sig = sig
        t = self.t
        self.op = op
        title = tr(op.title) if op.title else op.title
        self.title.setFullText(f"{title} · {op.what}" if op.what else title)
        pct = round(op.progress * 100)
        dpr = self.devicePixelRatioF() or 1.0
        teal = QColor(BRAND[t.dark][0])
        if op.state == fw.WAITING:
            detail = tr("Waiting for the one before it")
            self.mark.setPixmap(_mark("ring", QColor(t.tokens.muted), 24, max(2.0, dpr)))
        elif op.state == fw.RUNNING:
            detail = tr("Working… {percent}%", percent=pct)
            if op.detail:
                detail = f"{detail} · {op.detail}"
            self.mark.setPixmap(_mark("ring", teal, 24, max(2.0, dpr)))
        elif op.state == fw.FINISHED and not op.errors:
            out = self._output()
            where = (out if out.is_dir() else out.parent) if out is not None else None
            detail = tr("Completed") + (f" · {tr('Saved to: {folder}', folder=str(where))}" if where else
                                        (f" · {op.detail}" if op.detail else ""))
            self.mark.setPixmap(_mark("done", QColor(t.tokens.success), 24, max(2.0, dpr)))
        elif op.state == fw.FINISHED:
            detail = op.detail
            self.mark.setPixmap(icons.pixmap("warning", 22, t.tokens.warning, dpr))
        elif op.state == fw.FAILED:
            detail = tr("Couldn't do it: {why}", why=op.detail)
            self.mark.setPixmap(icons.pixmap("warning", 22, t.tokens.danger, dpr))
        else:
            detail = op.detail or tr("Cancelled")
            self.mark.setPixmap(icons.pixmap("dismiss", 22, t.tokens.muted, dpr))
        self.detail.setFullText(detail)
        look = f"color: {t.tokens.danger.name()};" if op.state == fw.FAILED else ""
        if self.detail.styleSheet() != look:
            self.detail.setStyleSheet(look)
        running = not op.over
        self.bar.setVisible(running and op.state == fw.RUNNING)
        self.bar.setValue(round(op.progress * 1000))
        self.percent.setVisible(running and op.state == fw.RUNNING)
        self.percent.setText(f"{pct}%")
        self.cancel.setVisible(False)  # (in ⋯ while it runs: the row stays calm)
        self.time.setVisible(op.over)
        self.time.setText(_when(op.when))
        out = self._output()
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

    def _menu(self):
        op = self.op
        menu = QMenu(self)
        if not op.over:
            menu.addAction(tr("Cancel"), lambda: self.tray.c.cancel_job(self.job_id))
        out = self._output()
        if op.state == fw.FINISHED and out is not None:
            if out.is_file():
                menu.addAction(tr("Open"), self._open)
            menu.addAction(tr("Show in folder"), self._show)
        if op.can_retry:
            menu.addAction(tr("Try again"), self._again)
        if op.over:
            menu.addAction(tr("Remove from Activity"), lambda: self.tray.state.forget(self.job_id))
        menu.popup(self.more.mapToGlobal(QPoint(0, self.more.height())))

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
    """Activity: the operations started from Rakiza Core's jobs, running ones with their progress, finished ones
    with where they saved. Folds away to one line."""

    def __init__(self, ws: "FilesWorkspace"):
        super().__init__()
        self.ws, self.c, self.t = ws, ws.c, ws.t
        self.state = ws.state
        self.setObjectName("filesCard")
        self.rows: dict[int, OpRow] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 10, 14, 10)
        lay.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = _label(tr("Activity"), "libTitle")
        self.summary = _label("", "subtle")
        head.addWidget(self.title)
        head.addWidget(self.summary)
        head.addStretch()
        self.clear_btn = _button(tr("Clear finished"), "small", self.state.forget_finished)
        head.addWidget(self.clear_btn)
        self.fold = _icon_button("chevron_down", self.t.tokens.muted, tr("Fold away"), self.toggle)
        head.addWidget(self.fold)
        lay.addLayout(head)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.NoFrame)
        self.area.viewport().setAutoFillBackground(False)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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

    def toggle(self):
        self.c.settings.files_ops_open = not self.c.settings.files_ops_open
        self.c.settings.save()
        self._show_open(self.c.settings.files_ops_open)

    def _show_open(self, on: bool):
        has = bool(self.state.ops)
        key = (on, has, min(len(self.state.ops), 3))
        if key == getattr(self, "_shown", None):
            return
        self._shown = key
        self.area.setVisible(on and has)
        self.empty.setVisible(on and not has)
        self.fold.setIcon(icons.icon("chevron_down" if on else ("chevron_left" if is_rtl() else "chevron_right"),
                                     self.t.tokens.muted))
        words = tr("Fold away") if on else tr("Show")
        self.fold.setToolTip(words)
        self.fold.setAccessibleName(f"{words}: {tr('Activity')}")
        if has:
            shown = min(len(self.state.ops), 3)
            self.area.setFixedHeight(shown * 58 + 2)

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
        for i, o in enumerate(ops):  # (a line between rows, none above the first)
            look = "QFrame#activityRow { border-top: none; }" if i == 0 else ""
            if self.rows[o.job_id].styleSheet() != look:
                self.rows[o.job_id].setStyleSheet(look)
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


# ----------------------------------------------------------------------------- the side panel

class SaveTo(QComboBox):
    """Where a tool started from the side panel saves: next to the files, Settings' folder, or one picked."""

    def __init__(self, panel: "ToolPanel"):
        super().__init__()
        self.panel = panel
        self.setObjectName("saveTo")
        self.setAccessibleName(tr("Save to"))
        s = panel.c.settings
        self.addItem(icons.icon("folder", panel.t.tokens.text), tr("Same folder"), "")
        if s.save_mode == "folder" and s.output_folder:
            self.addItem(icons.icon("folder", panel.t.tokens.text),
                         tr("Default folder · {name}", name=Path(s.output_folder).name or s.output_folder),
                         str(s.output_folder))
        for folder in panel.state.__dict__.get("picked_folders", []):
            self.addItem(icons.icon("folder", panel.t.tokens.text), short_name(Path(folder).name or folder, 40),
                         folder)
        self.addItem(tr("Choose a folder…"), None)
        want = panel.state.__dict__.get("save_choice")
        if want is None and s.save_mode == "folder" and s.output_folder:
            want = str(s.output_folder)
        i = self.findData(want if want is not None else "")
        self.setCurrentIndex(max(0, i))
        self._last = self.currentIndex()
        self.activated.connect(self._picked)

    def value(self) -> str:
        return self.currentData() or ""

    def _picked(self, i: int):
        if self.itemData(i) is not None:
            self._last = i
            self.panel.state.save_choice = self.itemData(i)
            return
        name = QFileDialog.getExistingDirectory(self, tr("Save to"), str(Path.home()))
        if not name:
            self.setCurrentIndex(self._last)
            return
        picked = self.panel.state.__dict__.setdefault("picked_folders", [])
        if name not in picked:
            picked.insert(0, name)
            del picked[5:]
        self.insertItem(self.count() - 1, icons.icon("folder", self.panel.t.tokens.text),
                        short_name(Path(name).name or name, 40), name)
        self.setCurrentIndex(self.findData(name))
        self._last = self.currentIndex()
        self.panel.state.save_choice = name


class MergeForm(QWidget):
    """Merge, right in the side panel: the PDFs in the order they'll be put together (drag to change it), the new
    file's name and where it goes."""

    def __init__(self, panel: "ToolPanel", files: list[Path]):
        super().__init__()
        self.panel, t = panel, panel.t
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.list = QListWidget()
        self.list.setObjectName("mergeList")
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setSpacing(3)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setAccessibleName(tr("Order of the PDFs (drag to change it)"))
        self.list.setToolTip(tr("Drag to change the order"))
        dpr = self.devicePixelRatioF() or 1.0
        for f in files:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, str(f))
            item.setSizeHint(QSize(10, 44))
            self.list.addItem(item)
            row = QFrame()
            row.setObjectName("mergeRow")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 4, 10, 4)
            rl.setSpacing(10)
            grip = QLabel()
            grip.setPixmap(icons.pixmap("re_order_dots_vertical", 16, t.tokens.faint, dpr))
            rl.addWidget(grip)
            num = _label("", "orderNumber")
            num.setAlignment(Qt.AlignCenter)
            num.setMinimumWidth(24)
            rl.addWidget(num)
            ic = QLabel()
            ic.setPixmap(icons.pixmap("document_pdf", 20, QColor("#e5534b"), dpr))
            rl.addWidget(ic)
            rl.addWidget(ElidedLabel(f.name), 1)
            for w in (grip, num, ic):
                w.setAttribute(Qt.WA_TransparentForMouseEvents)
            row.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.list.setItemWidget(item, row)
        self.list.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self, self._number))
        self.list.setFixedHeight(min(5, len(files)) * 50 + 4)
        lay.addWidget(self.list)
        lay.addSpacing(4)
        lay.addWidget(_label(tr("Output name"), "fieldLabel"))
        self.name = QLineEdit(tr("Combined") + ".pdf")
        self.name.setObjectName("outputName")
        self.name.setAccessibleName(tr("Output name"))
        lay.addWidget(self.name)
        self._number()

    def _number(self):
        for i in range(self.list.count()):
            row = self.list.itemWidget(self.list.item(i))
            if row is not None:
                num = row.findChild(QLabel, "orderNumber")
                if num is not None:
                    num.setText(str(i + 1))

    def order(self) -> list[Path]:
        return [Path(self.list.item(i).data(Qt.UserRole)) for i in range(self.list.count())]

    def start(self):
        from . import pdftools
        files = self.order()
        self.panel.state.save_to = self.panel.save_value()
        try:
            self.panel.c.start_job(pdftools.MERGE, files, {"name": self.name.text().strip()})
        finally:
            self.panel.state.save_to = None


class ToolPanel(QFrame):
    """Beside the files: what's selected and the tools that fit it, as buttons; the tool picked shows its choices
    (its own, or the dialog it asks with, shown right here) and one big button that starts it."""

    def __init__(self, ws: "FilesWorkspace"):
        super().__init__()
        self.ws, self.c, self.w, self.t = ws, ws.c, ws.w, ws.t
        self.state = ws.state
        self.setObjectName("filesCard")
        self.armed = False             # a tool was started from here: the next dialog it asks with shows here
        self.inline = None             # the dialog showing here now
        self._mirror = None            # (its own start button, pressed by the big one)
        self._hidden: list = []        # (its own row of buttons, hidden while it shows here)
        self._after = None             # what to do once that dialog has gone
        self._launched: tuple | None = None   # the tool and files whose choices were shown by themselves already
        self._show_off = False
        self._more = False
        self.slot = None               # the Converter action picked (Convert and edit, the archive tools)
        self.merge = None              # Merge's own choices
        self.save_to = None
        self.run_btn = None
        self.page_btn = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.NoFrame)
        self.area.viewport().setAutoFillBackground(False)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.area, 1)
        self.foot = QWidget()  # (the big button: always in sight, under the choices)
        self.foot_lay = QVBoxLayout(self.foot)
        self.foot_lay.setContentsMargins(18, 6, 18, 14)
        self.foot_lay.setSpacing(4)
        outer.addWidget(self.foot)
        self.setMinimumWidth(PANEL_WIDTH)
        self.setMaximumWidth(PANEL_WIDTH)
        self._pending_slots = False
        self._sync = QTimer(self)
        self._sync.setInterval(150)
        self._sync.timeout.connect(self._mirror_look)

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
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)
        self.body_lay = lay
        self.run_btn = self.page_btn = self.save_to = self.merge = None
        while self.foot_lay.count():
            item = self.foot_lay.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self.foot.hide()
        self._head(lay)
        self._chips(lay)
        line = QFrame()
        line.setObjectName("line")
        lay.addWidget(line)
        tool = self.tool()
        if tool is None:
            self.state.tool = None
            self._idle(lay)
        else:
            self._tool_view(lay, tool)
        lay.addStretch()
        self.area.setWidget(body)
        width = max(self.width(), PANEL_WIDTH)
        want = (body.heightForWidth(width) if body.hasHeightForWidth() else body.sizeHint().height()) + \
            (self.foot.sizeHint().height() if not self.foot.isHidden() else 0)
        self.setMinimumHeight(min(want, 720))  # (the page scrolls before the panel does)

    def _head(self, lay):
        items, sel = self.state.items, self.state.selected
        head = QHBoxLayout()
        head.setSpacing(6)
        if sel:
            words = tr("{count} files selected", count=len(sel))
        elif items:
            words = tr("{count} items", count=len(items))
        else:
            words = tr("No files yet")
        self.head_words = _label(words, "h2", wrap=True)
        head.addWidget(self.head_words, 1)
        if sel:
            head.addWidget(_icon_button("dismiss", self.t.tokens.muted,
                                        f"{tr('Remove from list')}\n"
                                        f"{tr('Takes them off this list only: nothing on your PC is deleted.')}",
                                        self.ws.remove_selected), 0, Qt.AlignTop)
        self.back_btn = _icon_button("arrow_right" if is_rtl() else "arrow_left", self.t.tokens.text, tr("Back"),
                                     self.ws._toggle_panel)  # (narrow: the files again)
        self.back_btn.setVisible(self.ws._narrow)
        head.insertWidget(0, self.back_btn, 0, Qt.AlignTop)
        lay.addLayout(head)
        if items and not sel:
            lay.addWidget(_label(tr("None selected: the tools work on all of them."), "subtle", wrap=True))

    def _candidates(self) -> tuple[list, list]:
        """The tools offered as buttons (fitting what's chosen, the picked tile's when one is), and the rest."""
        items = self.state.chosen()
        fam = self.state.family
        pool = ft.family_tools(self.c.settings, fam) if fam else ft.offered(self.c.settings)
        if not items:
            return ft.suggested_order(self.c.settings, pool), []
        fits, misfits = [], []
        for tool in pool:
            f = ft.fit(tool, items, self.state.kinds)
            (fits if f.ok and f.usable else misfits).append((tool, f))
        return ft.suggested_order(self.c.settings, [t for t, _ in fits]), misfits

    def _chips(self, lay):
        from .atomic.widgets import FlowLayout
        fits, misfits = self._candidates()
        picked = self.state.tool
        limit = len(fits) if self._more else CHIPS_SHOWN
        shown = fits[:limit]
        if picked and not any(t.id == picked for t in shown):  # (the tool picked is always among them)
            extra = next((t for t in fits if t.id == picked), None)
            if extra is not None:
                shown.append(extra)
        holder = QWidget()
        flow = FlowLayout(holder, spacing=8)
        flow.setContentsMargins(0, 0, 0, 0)
        self.chip_group = QButtonGroup(holder)
        self.chip_group.setExclusive(True)
        for tool in shown:
            b = QPushButton(tr(tool.name))
            b.setObjectName("toolChip")
            b.setCheckable(True)
            b.setChecked(tool.id == picked)
            b.setCursor(Qt.PointingHandCursor)
            b.setIcon(icons.icon(tool.icon, self.t.tokens.text))
            b.setIconSize(QSize(16, 16))
            b.setToolTip(tr(tool.line))
            b.setProperty("tool", tool.id)
            b.clicked.connect(lambda _=False, i=tool.id: self.ws.choose_tool(i, keep_family=True))
            self.chip_group.addButton(b)
            flow.addWidget(b)
        if len(fits) > limit or (self._more and len(fits) > CHIPS_SHOWN):
            more = QPushButton(tr("More ({count})", count=len(fits) - limit) if not self._more else tr("Fewer"))
            more.setObjectName("toolChip")
            more.setCursor(Qt.PointingHandCursor)
            more.clicked.connect(self._toggle_more)
            flow.addWidget(more)
        if shown:
            lay.addWidget(holder)
        elif self.state.items:
            lay.addWidget(_label(tr("No tool works on what's selected. Select other files, or add some."), "muted",
                                 wrap=True))
        if misfits and self.state.items and self._more:
            off = _button(tr("Not for this selection ({count})", count=len(misfits)), "flat", self._toggle_off)
            off.setIcon(icons.icon("chevron_down" if self._show_off else
                                   ("chevron_left" if is_rtl() else "chevron_right"), self.t.tokens.muted))
            lay.addWidget(off, 0, Qt.AlignLeading)
            if self._show_off:
                for tool, f in misfits:
                    lab = _label(f"{tr(tool.name)}: {f.why_not}", "subtle", wrap=True)
                    lab.setContentsMargins(8, 0, 8, 0)
                    lay.addWidget(lab)

    def _toggle_more(self):
        self._more = not self._more
        self.refresh()

    def _toggle_off(self):
        self._show_off = not self._show_off
        self.refresh()

    def _idle(self, lay):
        if self.state.items:
            text = tr("Pick a tool above to see its choices here.")
        else:
            text = tr("Add files to see the tools that fit them, or pick a tool in the library first.")
        hint = _label(text, "muted", wrap=True)
        lay.addWidget(hint)

    def _tool_view(self, lay, tool: ft.FileTool):
        items = self.state.chosen()
        f = ft.fit(tool, items, self.state.kinds)
        lay.addWidget(_label(tr(tool.name), "h2", wrap=True))
        lay.addWidget(_label(tr(tool.line), "muted", wrap=True))
        if f.skipped and f.ok:  # (nothing is done to what it doesn't take: said before it starts)
            names = ", ".join(short_name(p.name, 30) for p, _ in f.skipped[:3])
            more = f" {tr('…and {count} more', count=len(f.skipped) - 3)}" if len(f.skipped) > 3 else ""
            lay.addWidget(_label(tr("Left out, as {tool} doesn't take them: {names}", tool=tr(tool.name),
                                    names=names) + more, "fitNote", wrap=True))
        if not f.ok and items:
            lay.addWidget(_label(f.why_not, "fitStop", wrap=True))
        elif not items and not tool.empty_ok:
            lay.addWidget(_label(tr("Works on: {what} · {many}", what=ft.what_it_takes(tool), many=ft.how_many(tool)),
                                 "subtle", wrap=True))
        self.settings_lay = QVBoxLayout()
        self.settings_lay.setSpacing(8)
        lay.addLayout(self.settings_lay)
        self.slot = None
        usable = f.usable if f.ok else []
        if tool.id == "pdf.merge" and len(usable) >= 2 and all(self.state.kinds.get(str(p)) == "pdf" for p in usable):
            self.merge = MergeForm(self, usable)
            self.settings_lay.addWidget(self.merge)
        elif tool.how.startswith("slots:") and usable:
            self._slots(tool, usable)
        elif tool.view:
            self.settings_lay.addWidget(_label(tr("Opens here, in its own view, with the files you picked. "
                                                  "Files › at the top brings you back to the list.")
                                               if usable else tr("Opens here, in its own view."), "subtle", wrap=True))
        if usable and (not tool.view or self.merge is not None) and tool.how != "archive-open":
            lay.addWidget(_label(tr("Save to"), "fieldLabel"))
            self.save_to = SaveTo(self)
            lay.addWidget(self.save_to)
        self.run_btn = _button("", "bigAction", self._run)
        self.run_btn.setIcon(icons.icon(tool.icon, QColor(BRAND[self.t.dark][1])))
        self.run_btn.setIconSize(QSize(20, 20))
        self.foot_lay.addWidget(self.run_btn)
        self.page_btn = _button("", "link", self._open_page)
        self.foot_lay.addWidget(self.page_btn, 0, Qt.AlignHCenter)
        self.foot.show()
        self._foot(tool, f)
        key = (tool.id, tuple(str(p) for p in usable))
        if tool.settings and not tool.how.startswith("slots:") and usable and self._launched != key:
            self._launched = key  # (its choices show by themselves once; Cancel leaves them closed)
            QTimer.singleShot(0, self, lambda: self._start(tool, usable))

    def save_value(self) -> str | None:
        return self.save_to.value() if self.save_to is not None and _alive(self.save_to) else None

    def _count_words(self, action: str, n: int) -> str:
        return f"{action} {tr('{count} files', count=n)}" if n > 1 else action

    def _foot(self, tool: ft.FileTool, f: ft.Fit):
        usable = f.usable if f.ok else []
        n = len(usable)
        b = self.run_btn
        b.setEnabled(True)
        if not self.state.items:
            b.setText(tr("Open {name}", name=tr(tool.name)) if tool.empty_ok else tr("Add the files it needs…"))
        elif not f.ok:
            b.setText(tr("Add the files it needs…"))
        elif self.merge is not None:
            b.setText(self._count_words(tr("Merge"), n))
        elif tool.how.startswith("slots:"):
            b.setText(self.slot.tool.description if self.slot is not None else tr("Pick what to do above"))
            b.setEnabled(self.slot is not None)
        elif tool.view:
            b.setText(tr("Open with {count} items", count=n) if n > 1 else tr("Open {name}", name=tr(tool.name)))
        elif tool.settings:
            b.setText(tr("Choose how…"))
        else:
            b.setText(tr("Start on {count} items", count=n) if n > 1 else tr("Start"))
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
            flow.setContentsMargins(0, 0, 0, 0)
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
        if self.inline is not None and self._mirror is not None and _alive(self._mirror):
            self._mirror.click()  # (the choices showing here: their own button starts it)
            return
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
        if self.merge is not None and _alive(self.merge):
            self.merge.start()
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
        if tool.settings or not tool.view:
            self._arm()
        run(self.w, tool, usable)
        if not tool.settings:  # (nothing asked: started already, or opened)
            QTimer.singleShot(0, self, self.disarm)

    def _open_page(self):
        tool = self.tool()
        if tool is not None and tool.page:
            self.later(lambda: self.w.open_page(tool.page))

    def _arm(self):
        self.armed = True
        self.state.save_to = self.save_value()

    def disarm(self, *_):
        self.armed = False
        self.state.save_to = None

    # ---- a tool's choices, shown here instead of in a sheet

    def wants(self, dlg) -> bool:
        return (self.armed and _alive(self) and self.isVisible() and self.ws.isVisible()
                and hasattr(dlg, "lay") and hasattr(dlg, "params"))

    def run_inline(self, dlg) -> int:
        """Shows dlg in the panel (under the tool's name) until it's answered; the big button presses its own."""
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
        self._mirror = getattr(dlg, "ok", None) if isinstance(getattr(dlg, "ok", None), QPushButton) else None
        self._hidden = []
        if self._mirror is not None and self.run_btn is not None:
            for i in range(dlg.lay.count()):  # (its own Cancel / start row: the big button does it)
                row = dlg.lay.itemAt(i).layout()
                if row is not None and row.indexOf(self._mirror) >= 0:
                    for j in range(row.count()):
                        w = row.itemAt(j).widget()
                        if w is not None and not w.isHidden():
                            w.hide()
                            self._hidden.append(w)
        dlg.style().unpolish(dlg)
        dlg.style().polish(dlg)
        box.addWidget(dlg)
        self.inline = dlg
        if self._hidden:
            self._mirror_look()
            self._sync.start()
        elif self.run_btn is not None:
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
            self._sync.stop()
            if _alive(dlg):
                for w in self._hidden:
                    if _alive(w):
                        w.show()
                box.removeWidget(dlg)
                dlg.hide()
                dlg.setProperty("sheet", None)
                dlg.setParent(home if _alive(home) else None, flags)
            self.inline = None
            self._mirror = None
            self._hidden = []
            if _alive(self):
                self.setMinimumWidth(PANEL_WIDTH)
                self.setMaximumWidth(PANEL_WIDTH)
                if not result:
                    self.disarm()
                after, self._after = self._after, None
                QTimer.singleShot(0, self, after if after is not None else self._after_dialog)
        return result

    def _mirror_look(self):
        """The big button says what the dialog's own would, and is off while it is."""
        ok, b = self._mirror, self.run_btn
        if ok is None or b is None or not _alive(ok) or not _alive(b):
            return
        tool = self.tool()
        n = len(ft.fit(tool, self.state.chosen(), self.state.kinds).usable) if tool is not None else 0
        words = self._count_words(ok.text(), n)
        if b.text() != words:
            b.setText(words)
        b.setEnabled(ok.isEnabled())
        b.show()

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


# ----------------------------------------------------------------------------- the tool library

class ToolLibrary(QFrame):
    """Under the files: a tile for each tool, by category (the box at the top finds any of their actions by name
    or what it does), pins, what's coming on its own tab, and switching tools on or off."""

    def __init__(self, ws: "FilesWorkspace"):
        super().__init__()
        self.ws, self.c, self.t = ws, ws.c, ws.t
        self.setObjectName("filesCard")
        self.category = ""
        self.query = ""
        self.tab = "now"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 14, 16)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(_label(tr("Tool library"), "libTitle"))
        seg = QFrame()
        seg.setObjectName("segmented")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.tabs = QButtonGroup(self)
        self.tab_buttons = {}
        for key, words_ in (("now", tr("Available")), ("soon", tr("Upcoming"))):
            b = QPushButton(words_)
            b.setObjectName("segment")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            self.tabs.addButton(b)
            self.tab_buttons[key] = b
            sl.addWidget(b)
        head.addWidget(seg)
        self.sep = _vline()
        head.addWidget(self.sep)
        self.explore = _button(tr("Explore upcoming features"), "link", lambda: ws.w.open_page("Soon"))
        self.explore.setIcon(icons.icon("arrow_left" if is_rtl() else "arrow_right", BRAND[self.t.dark][0]))
        self.explore.setLayoutDirection(Qt.LeftToRight if is_rtl() else Qt.RightToLeft)  # (the arrow after the words)
        self.explore.setIconSize(QSize(14, 14))
        head.addWidget(self.explore)
        head.addStretch()
        self.manage_btn = _icon_button("settings", self.t.tokens.muted, tr("Switch tools on or off"),
                                       lambda: self.show_tab("manage" if self.tab != "manage" else "now"))
        head.addWidget(self.manage_btn)
        lay.addLayout(head)
        from .atomic.widgets import FlowLayout
        self.chips_box = QWidget()
        chips = FlowLayout(self.chips_box, spacing=8)
        chips.setContentsMargins(0, 0, 0, 0)
        self.chip_group = QButtonGroup(self)
        for key, name in [("", tr("All"))] + [(k, tr(CHIP_NAMES[k])) for k, _, _ in ft.CATEGORIES]:
            b = QPushButton(name)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setChecked(key == "")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._category(k))
            self.chip_group.addButton(b)
            chips.addWidget(b)
        lay.addWidget(self.chips_box)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(8)
        lay.addWidget(self.body)
        self.search = ws.find   # (one box finds a tool: the one at the top)
        self.show_tab(ws.state.library if ws.state.library in ("now", "soon", "manage") else "now")

    def _category(self, key: str):
        self.category = key
        self.fill()

    def show_tab(self, tab: str):
        self.tab = tab
        self.ws.state.library = tab
        for key, b in self.tab_buttons.items():
            b.setChecked(key == tab)
        self.chips_box.setVisible(tab == "now" and not self.query)
        self.fill()

    def set_query(self, text: str):
        self.query = text.strip()
        if self.query and self.tab != "now":
            self.show_tab("now")
            return
        self.chips_box.setVisible(self.tab == "now" and not self.query)
        self.fill()

    def matches(self) -> list[ft.FileTool]:
        q = self.query.lower()
        return [t for t in ft.offered(self.c.settings)
                if q and (q in tr(t.name).lower() or q in tr(t.line).lower() or q in t.name.lower()
                          or q in t.line.lower())]

    def _clear(self):
        while self.body_lay.count():
            item = self.body_lay.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()

    def fill(self):
        from .atomic.widgets import FlowLayout
        self._clear()
        holder = QWidget()
        flow = FlowLayout(holder, spacing=10)
        flow.setContentsMargins(0, 0, 0, 0)
        self.tiles = []
        if self.tab == "now":
            items = self.ws.state.chosen()
            if self.query:
                tools = self.matches()
                for tool in tools:
                    tip = tr(tool.line)
                    if items:
                        f = ft.fit(tool, items, self.ws.state.kinds)
                        if not f.ok:
                            tip = f"{tip}\n{f.why_not}"
                    tile = LibTile(tool.id, tr(tool.name), tool.icon, self.t, picked=tool.id == self.ws.state.tool,
                                   tip=tip)
                    tile.tool = tool
                    tile.chosen.connect(lambda i: self.ws.choose_tool(i))
                    flow.addWidget(tile)
                    self.tiles.append(tile)
                if not tools:
                    self.body_lay.addWidget(_label(tr("No tool matches “{words}”.", words=self.query), "muted",
                                                   wrap=True))
            else:
                pins = set(ft.pinned_families(self.c.settings))
                fams = [f for f in ft.families(self.c.settings) if not self.category or f.category == self.category]
                for fam in fams:
                    tools = ft.family_tools(self.c.settings, fam.id)
                    tip = tr(tools[0].line) if len(tools) == 1 else tr("{count} tools", count=len(tools))
                    tile = LibTile(fam.id, tr(fam.name), fam.icon, self.t, pinned=fam.id in pins,
                                   picked=fam.id == self.ws.state.family, tip=tip)
                    tile.family = fam
                    tile.chosen.connect(self.ws.tile_clicked)
                    tile.pin_toggled.connect(self.ws.pin)
                    flow.addWidget(tile)
                    self.tiles.append(tile)
                if not fams:
                    self.body_lay.addWidget(_label(tr("No tools here are switched on."), "muted", wrap=True))
            if self.tiles:
                self.body_lay.addWidget(holder)
                self._size_tiles()
        elif self.tab == "soon":
            coming = ft.upcoming()
            for u in coming:
                flow.addWidget(ComingTile(u, self.t))
            if coming:
                self.body_lay.addWidget(holder)
            self.body_lay.addWidget(_label(tr("On Rakiza Core's roadmap for Files. They can't be used yet, and they "
                                              "have no dates.") if coming else
                                           tr("Nothing else is confirmed for now. When a feature joins the roadmap "
                                              "it shows up here, with how far along it is."), "subtle", wrap=True))
        else:
            self._manage()

    def _size_tiles(self):
        """The tiles share the row: all on one line when they fit (none narrower than 92 px), as in a shelf."""
        tiles = getattr(self, "tiles", [])
        if not tiles:
            return
        room = max(200, self.width() - 38)
        n, gap = len(tiles), 10
        cols = n if n * 92 + (n - 1) * gap <= room else max(1, (room + gap) // (102 + gap))
        width = max(92, min(132, (room - (cols - 1) * gap) // cols))
        for tile in tiles:
            if _alive(tile) and tile.width() != width:
                tile.setFixedWidth(width)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        roomy = self.width() > 640
        self.explore.setVisible(roomy)  # (also in All tools)
        self.sep.setVisible(roomy)
        self._size_tiles()

    def _manage(self):
        """Which of Files' tools are switched on (one that's off isn't loaded at all)."""
        from . import tools as all_tools
        from .atomic.widgets import ResponsiveGrid
        from .window import ToolSwitchCard
        row = QHBoxLayout()
        row.addWidget(_label(tr("Switch off what you don't use: it isn't loaded at all, so Rakiza Core stays light. "
                                "Its settings are kept for when you switch it back on."), "subtle", wrap=True), 1)
        row.addWidget(_button(tr("Done"), "small", lambda: self.show_tab("now")), 0, Qt.AlignTop)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        hl.addLayout(row)
        sec = all_tools.SECTION_BY_PAGE["Files"]
        on = {t.id for t in self.ws.w.tools_on}
        fresh = {t.id for t in all_tools.new_tools(self.c.settings)}
        grid = ResponsiveGrid(300, 10, 3)
        tools = all_tools.in_section(self.c.settings, sec)
        for tool in [x for x in tools if x.id in on] + [x for x in tools if x.id not in on]:
            grid.add(ToolSwitchCard(tool, tool.id in on, lambda n=tool.page: self.ws.w.open_page(n),
                                    self.ws.w._tool_switched, self.t.dark, self.devicePixelRatioF(),
                                    fresh=tool.id in fresh, accent=QColor(BRAND[self.t.dark][0])))
        hl.addWidget(grid)
        self.body_lay.addWidget(holder)

    def mark(self):
        for tile in getattr(self, "tiles", []):
            fam = getattr(tile, "family", None)
            on = fam.id == self.ws.state.family if fam is not None else tile.key == self.ws.state.tool
            if tile.property("picked") != on:
                tile.setProperty("picked", on)
                tile.style().unpolish(tile)
                tile.style().polish(tile)


# ----------------------------------------------------------------------------- the page

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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        self.body_host = QWidget()
        self.body_host.setObjectName("scrollContent")
        self.body_host.setMinimumHeight(600)
        lay = QVBoxLayout(self.body_host)
        lay.setContentsMargins(0, 0, 10, 4)
        lay.setSpacing(14)
        lay.addWidget(self._header())
        body = QHBoxLayout()
        body.setSpacing(16)
        self.left_box = QWidget()
        left = QVBoxLayout(self.left_box)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        left.addLayout(self._toolbar())
        self.files_card = self._files_card()
        left.addWidget(self.files_card, 1)
        self.library = ToolLibrary(self)
        left.addWidget(self.library)
        body.addWidget(self.left_box, 1)
        self.center = self.files_card  # (what the side panel sits beside)
        self.files_card.installEventFilter(self)
        self.panel = ToolPanel(self)
        body.addWidget(self.panel)
        lay.addLayout(body, 1)
        self.ops = OpsTray(self)
        lay.addWidget(self.ops)
        self.scroll.setWidget(self.body_host)
        self.cover = _DropCover(self, self.t)
        self.state.files_changed.connect(self._files_changed)
        self.state.statuses_changed.connect(self._statuses_changed)
        self.state.job_started.connect(self._job_started)
        sheet.set_inline_host(self._inline_target)
        self._files_changed()

    # ---- the parts

    def _header(self) -> QWidget:
        box = QWidget()
        head = QHBoxLayout(box)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(14)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("folder", 34, self.t.tokens.text, self.devicePixelRatioF() or 1.0))
        head.addWidget(ic, 0, Qt.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(0)
        words.addWidget(_label(tr(self.sec.name), "h1"))
        words.addWidget(_label(tr("One workspace for all your files"), "muted", wrap=True))
        head.addLayout(words, 1)
        return box

    def _toolbar(self) -> QHBoxLayout:
        t = self.t
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.add_btn = _button(tr("Add files"), "addFiles", self.add_files, "add", QColor(BRAND[t.dark][1]))
        self.find = QLineEdit()
        self.find.setObjectName("toolSearch")
        self.find.setPlaceholderText(tr("Search tools (e.g., merge, compress, convert)"))
        self.find.setAccessibleName(tr("Find a tool"))
        self.find.setClearButtonEnabled(True)
        self.find.addAction(icons.icon("search", t.tokens.muted), QLineEdit.LeadingPosition)
        self.find.setMinimumWidth(160)
        self.find.textChanged.connect(self._find)
        self.find.returnPressed.connect(self._find_enter)
        self.all_btn = _button(tr("All tools"), "allTools", self._all_menu, "grid", t.text)
        self.all_btn.setLayoutDirection(Qt.LeftToRight if not is_rtl() else Qt.RightToLeft)
        self.tools_btn = _button(tr("Tools"), "allTools", self._toggle_panel, "puzzle_piece", t.text)
        self.tools_btn.setCheckable(True)
        bar.addWidget(self.add_btn)
        bar.addWidget(self.find, 1)
        bar.addWidget(self.all_btn)
        bar.addWidget(self.tools_btn)
        return bar

    def _all_menu(self):
        """All tools, by category (every PDF action and the archive tools too), as a menu under the button."""
        menu = QMenu(self)
        menu.setObjectName("allToolsMenu")
        offered = ft.offered(self.c.settings)
        for key, name, icon in ft.CATEGORIES:
            tools = [t for t in offered if t.category == key]
            if not tools:
                continue
            sub = menu.addMenu(icons.icon(icon, self.t.tokens.text), tr(name))
            for tool in tools:
                sub.addAction(icons.icon(tool.icon, self.t.tokens.text), tr(tool.name),
                              lambda i=tool.id: self.choose_tool(i))
        menu.addSeparator()
        menu.addAction(icons.icon("settings", self.t.tokens.text), tr("Switch tools on or off"),
                       lambda: self.open_library("manage"))
        menu.addAction(icons.icon("sparkle", self.t.tokens.text), tr("Explore upcoming features"),
                       lambda: self.w.open_page("Soon"))
        self._menu = menu
        menu.popup(self.all_btn.mapToGlobal(QPoint(0, self.all_btn.height() + 4)))

    def _files_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("filesCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(8, 4, 8, 10)
        lay.setSpacing(8)
        self.table = FileTable(self.t)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.remove_requested.connect(self.remove_selected)
        self.table.itemDoubleClicked.connect(self._open_item)
        self.table.more_requested.connect(self._row_menu)
        self.table.head.toggled.connect(self._toggle_all)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay.addWidget(self.table, 1)
        self.drop = DropArea(self)
        lay.addWidget(self.drop, 0)
        card.setMinimumHeight(200)
        return card

    # ---- the list

    def _files_changed(self):
        if not _alive(self):
            return
        has = bool(self.state.items)
        self.table.setVisible(has)
        self.drop.set_big(not has)
        self._fill_table()
        self._fit_table()
        self._fit_panel()
        self.panel.refresh()
        self.library.fill() if self.library.query else self.library.mark()

    def _icon(self, name: str, color) -> object:
        """(One icon of each kind for the whole list: thousands of rows share them.)"""
        key = (name, QColor(color).name())
        cache = self.__dict__.setdefault("_icons", {})
        if key not in cache:
            cache[key] = icons.icon(name, color)
        return cache[key]

    def _mark_icon(self, kind: str, color) -> object:
        from PySide6.QtGui import QIcon
        key = ("mark:" + kind, QColor(color).name())
        cache = self.__dict__.setdefault("_icons", {})
        if key not in cache:
            cache[key] = QIcon(_mark(kind, QColor(color), 18, 2.0))
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
            more = self._icon("more_horizontal", t.tokens.muted)
            pdf_red = QColor("#e5534b")
            for p in self.state.items:
                name = str(p)
                kind = self.state.kinds.get(name, "other")
                item = _SortItem()
                item.setData(COL_NAME, Qt.UserRole, name)
                item.setText(COL_NAME, short_name(p.name, 70))
                item.setToolTip(COL_NAME, name)
                item.setIcon(COL_NAME, self._icon(KIND_ICONS.get(kind, "document"),
                                                  pdf_red if kind == "pdf" else BRAND[t.dark][0] if kind == "image"
                                                  else t.tokens.muted))
                item.setText(COL_TYPE, tr("Folder") if kind == "folder" else
                             p.suffix[1:].upper() if p.suffix else tr(KIND_WORDS.get(kind, "File")))
                self._size_cell(item, p)
                self._status_cell(item, name)
                item.setIcon(COL_MORE, more)
                item.setToolTip(COL_MORE, tr("More"))
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
        self._head_check()

    def _head_check(self):
        n, sel = len(self.state.items), len(self.state.selected)
        self.table.head.set_state(Qt.Unchecked if not sel else Qt.Checked if sel >= n else Qt.PartiallyChecked)

    def _fit_table(self):
        """The list as tall as its rows (the place to drop more takes what's left), scrolling once it's full."""
        if self.state.items:
            n = self.table.topLevelItemCount()
            row = self.table.sizeHintForRow(0) if n else 0
            full = self.table.content_height()
            shown = self.table.header().sizeHint().height() + min(n, 8) * max(row, 1) + 4
            self.table.setMaximumHeight(max(80, full))
            self.table.setMinimumHeight(max(80, min(full, shown)))  # (more than 8 rows scroll in the list)
        else:
            self.table.setMinimumHeight(0)
            self.table.setMaximumHeight(16777215)

    def _size_cell(self, item, p: Path):
        size = self.state.size_of(p)
        if size is None:
            item.setText(COL_SIZE, "…")
            item.setData(COL_SIZE, Qt.UserRole + 1, -1)
        else:
            item.setText(COL_SIZE, human_size(size[0]) + ("" if size[1] else "+"))
            item.setData(COL_SIZE, Qt.UserRole + 1, size[0])

    def _status_cell(self, item, name: str):
        state, words = self.state.status.get(name, (fw.READY, ""))
        t = self.t
        tool = self.panel.tool() if hasattr(self, "panel") else None
        if state == fw.READY and tool is not None and not ft.takes(tool, Path(name), self.state.kinds.get(name)):
            item.setText(COL_STATUS, "—")  # (the tool picked doesn't take it: left as it is)
            item.setForeground(COL_STATUS, t.tokens.muted)
            item.setIcon(COL_STATUS, self._icon("circle", QColor(0, 0, 0, 0)))
            item.setToolTip(COL_STATUS, tr("{tool} doesn't take this kind of item", tool=tr(tool.name)))
            return
        color = {fw.DONE: t.tokens.success, fw.READY: t.tokens.success, fw.PROBLEM: t.tokens.danger,
                 fw.WORKING: QColor(BRAND[t.dark][0])}.get(state, t.tokens.muted)
        item.setText(COL_STATUS, tr(STATE_WORDS[state]))
        item.setForeground(COL_STATUS, t.tokens.text if state in (fw.READY, fw.DONE) else color)
        if state in (fw.READY, fw.DONE):
            item.setIcon(COL_STATUS, self._mark_icon("done", color))
        elif state == fw.WORKING:
            item.setIcon(COL_STATUS, self._mark_icon("ring", color))
        else:
            item.setIcon(COL_STATUS, self._icon("warning" if state == fw.PROBLEM else "dismiss", color))
        item.setToolTip(COL_STATUS, words or tr(STATE_WORDS[state]))

    def _statuses_changed(self):
        if not _alive(self):
            return
        for i in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(i)
            name = item.data(COL_NAME, Qt.UserRole)
            self._size_cell(item, Path(name))
            self._status_cell(item, name)

    def _selection_changed(self):
        if self._syncing:
            return
        names = [it.data(COL_NAME, Qt.UserRole) for it in self.table.selectedItems()]
        order = {str(p): i for i, p in enumerate(self.state.items)}
        names.sort(key=lambda n: order.get(n, 0))
        self._syncing = True
        try:
            self.state.selected = names
        finally:
            self._syncing = False
        self._head_check()
        QTimer.singleShot(0, self, self.panel.refresh)  # (once, after a drag across many rows)

    def _toggle_all(self):
        if len(self.state.selected) >= len(self.state.items):
            self.table.clearSelection()
        else:
            self.table.selectAll()

    def _row_menu(self, item, where: QPoint):
        from .core import os_open
        from .winutil import reveal_in_explorer
        p = Path(item.data(COL_NAME, Qt.UserRole))
        menu = QMenu(self)
        menu.addAction(tr("Open"), lambda: os_open(p) if p.exists() else None)
        menu.addAction(tr("Show in folder"), lambda: reveal_in_explorer(p))
        menu.addSeparator()
        menu.addAction(icons.icon("dismiss", self.t.tokens.text), tr("Remove from list"),
                       lambda: self.panel.later(lambda: self.state.remove([str(p)])))
        menu.addAction(tr("Clear list"), lambda: self.panel.later(self.state.clear))
        self._menu = menu
        menu.popup(where)

    def _open_item(self, item, col):
        from .core import os_open
        if col in (COL_CHECK, COL_MORE):
            return
        try:
            os_open(Path(item.data(COL_NAME, Qt.UserRole)))
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

    def choose_tool(self, tool_id: str, keep_family: bool = False):
        def go():
            tool = ft.by_id(tool_id)
            if not keep_family and tool is not None:
                fam = ft.family_of(tool)
                self.state.family = fam.id if fam is not None else None
            self.state.tool = tool_id
            self.panel.slot = None
            self._panel_open = True
            self._fit_panel()
            self.panel.refresh()
            self.library.mark()
            self._statuses_changed()
        self.panel.later(go)

    def choose_family(self, family_id: str):
        """A tile picked: its tools beside the files, the one that fits them best already picked."""
        tools = ft.family_tools(self.c.settings, family_id)
        if not tools:
            return
        if len(tools) == 1:
            self.choose_tool(tools[0].id)
            return
        items = self.state.chosen()
        current = ft.by_id(self.state.tool) if self.state.tool else None
        best = current if current in tools else None
        if best is None and items:
            best = next((t for t in ft.suggested_order(self.c.settings, tools)
                         if ft.fit(t, items, self.state.kinds).ok), None)

        def go():
            self.state.family = family_id
            self.state.tool = best.id if best is not None else None
            self.panel.slot = None
            self.panel._show_off = False
            self._panel_open = True
            self._fit_panel()
            self.panel.refresh()
            self.library.mark()
            self._statuses_changed()
        self.panel.later(go)

    def tile_clicked(self, family_id: str):
        """A tile in the library: its tools beside the files; the tile picked already, all of them again."""
        if family_id == self.state.family:
            self.clear_family()
        else:
            self.choose_family(family_id)

    def clear_family(self):
        def go():
            self.state.family = None
            self.panel.refresh()
            self.library.mark()
        self.panel.later(go)

    def unchoose_tool(self):
        def go():
            self.state.tool = None
            self.panel._launched = None
            self._fit_panel()
            self.panel.refresh()
            self.library.mark()
            self._statuses_changed()
        self.panel.later(go)

    def pin(self, family_id: str, on: bool):
        ft.set_pinned(self.c.settings, family_id, on)
        self.c.settings.save()
        QTimer.singleShot(0, self, self.panel.refresh)

    def _job_started(self, _job_id: int):
        QTimer.singleShot(0, self, self.panel.disarm)  # (a tool may start several jobs at once: all save alike)
        self.ops.open_up()

    def _inline_target(self, dlg):
        if not _alive(self) or not _alive(self.panel):
            return None
        return self.panel if self.panel.wants(dlg) else None

    # ---- finding a tool

    def _find(self, text: str):
        self.library.set_query(text)

    def _find_enter(self):
        hits = self.library.matches()
        if hits:
            self.find.clear()
            self.choose_tool(hits[0].id)

    def open_library(self, tab: str = "now"):
        self.library.show_tab(tab)
        self.scroll.ensureWidgetVisible(self.library)
        if tab == "now":
            self.find.setFocus()

    def close_library(self):
        if self.find.text():
            self.find.clear()
        if self.library.tab != "now":
            self.library.show_tab("now")

    # ---- room: the side panel folds away when the window is narrow

    def _toggle_panel(self):
        self._panel_open = not self.panel.isVisible()
        self._fit_panel()

    def panel_wanted(self, on: bool):
        if on and not self.panel.isVisible():
            self._panel_open = True
            self._fit_panel()

    def _fit_panel(self):
        was = self._narrow
        narrow = 50 < self.width() < NARROW
        self._narrow = narrow
        if narrow:
            show = self._panel_open or self.panel.inline is not None
        else:
            show = True
        self.panel.setVisible(show)
        self.left_box.setVisible(not (narrow and show))  # (narrow: the panel takes the room, ‹ brings the files back)
        if self.panel.inline is None:
            self.panel.setMaximumWidth(16777215 if narrow else PANEL_WIDTH)
        self.tools_btn.setVisible(narrow)
        self.tools_btn.setChecked(show)
        back = getattr(self.panel, "back_btn", None)
        if was != narrow and back is not None and _alive(back):
            back.setVisible(narrow)

    def _fit_toolbar(self):
        """Narrow: the toolbar's buttons keep only their icons (none is ever cut short)."""
        tight = self.width() < 760
        for b, words in ((self.all_btn, tr("All tools")), (self.tools_btn, tr("Tools"))):
            b.setText("" if tight else words)
            b.setAccessibleName(words)
            b.setToolTip(words if tight else "")
        self.find.setPlaceholderText(tr("Find a tool") if tight or self.find.width() < 330 else
                                     tr("Search tools (e.g., merge, compress, convert)"))

    def eventFilter(self, obj, e):
        if obj is self.files_card and e.type() == QEvent.Resize:
            self.table.fit_columns(self.files_card.width())
        return False

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if (50 < self.width() < NARROW) != self._narrow:
            self._fit_panel()
        self._fit_toolbar()
        self.cover.setGeometry(self.rect())

    def showEvent(self, e):
        super().showEvent(e)
        sheet.set_inline_host(self._inline_target)
        self._fit_panel()
        self._fit_toolbar()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.panel.close_inline()

    # ---- dropping files anywhere on the workspace

    def dragEnterEvent(self, e):
        if any(u.isLocalFile() for u in e.mimeData().urls()):
            e.setDropAction(Qt.CopyAction)
            e.accept()
            self.cover.setGeometry(self.rect())
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
        if e.key() == Qt.Key_Escape and (self.find.text() or self.library.tab != "now"):
            self.close_library()
            return
        super().keyPressEvent(e)

    def go_back(self) -> bool:
        """Back (Alt + Left, the mouse's back button) clears the search or leaves Upcoming first."""
        if self.find.text() or self.library.tab != "now":
            self.close_library()
            return True
        return False
