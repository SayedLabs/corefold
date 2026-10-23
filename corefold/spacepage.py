"""Corefold Space's page: a map of what's filling a drive, cleaning up junk, and finding duplicates."""
from __future__ import annotations

import gc
import html
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QApplication, QButtonGroup, QCheckBox, QFileDialog, QFrame, QGridLayout, \
    QHBoxLayout, QHeaderView, QLabel, QMenu, QMessageBox, QProgressBar, QPushButton, QSizePolicy, QStackedWidget, QToolTip, QTreeWidget, \
    QTreeWidgetItem, QVBoxLayout, QWidget

from . import icons, space, work
from .core import human_size, short_name
from .i18n import ltr, tr
from .theme import mix, round_menu
from .timefmt import date_text, time_ago
from .winutil import reveal_in_explorer

log = logging.getLogger("corefold")

if TYPE_CHECKING:
    from .app import Controller

KIND_COLORS = {"video": "#e5484d", "picture": "#30a46c", "music": "#8e4ec6", "document": "#0090ff",
               "archive": "#f5a524", "program": "#12a594", "other": "#8b8d98"}
SECTIONS = [("map", "Disk map"), ("clean", "Clean up"), ("dupes", "Duplicates")]
DUPE_FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")
KEEP_MAPS = 2             # drives (or folders) whose map is kept, so opening one again is instant
FORGET_AFTER = 20 * 60    # seconds: maps not looked at for this long are let go (a big drive's map is big)


_work = work.run


# ----------------------------------------------------------------------------- the map

class ElidedLabel(QLabel):
    """A long path that shortens itself in the middle to fit (the whole of it in its tooltip)."""

    def __init__(self):
        super().__init__()
        self.full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def set_full(self, text: str):
        self.full = text
        self.setToolTip(text)
        self._fit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self):
        self.setText(ltr(self.fontMetrics().elidedText(self.full, Qt.ElideMiddle, max(40, self.width() - 4))))


class Box:
    __slots__ = ("rect", "name", "size", "kind", "level", "folder", "parent")

    def __init__(self, rect, name, size, kind, level, folder, parent):
        self.rect, self.name, self.size, self.kind = rect, name, size, kind   # kind: folder, file or rest
        self.level, self.folder, self.parent = level, folder, parent           # folder: the Folder (folders)


class Treemap(QWidget):
    """Boxes as big as what they hold. Folders show what's inside them (a few levels down while
    there's room); click a folder to go in."""
    opened = Signal(object)            # a Folder
    menu = Signal(object, object)      # (path, Folder or None), global position
    DEPTH = 3

    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.folder: space.Folder | None = None
        self.boxes: list[Box] = []
        self.hover = -1
        self.setMouseTracking(True)
        self.setMinimumSize(240, 160)  # (small screens with big text: nothing below it gets covered)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

    def show_folder(self, folder: space.Folder | None):
        self.folder = folder
        self.hover = -1
        self._arrange()
        self.update()

    def resizeEvent(self, e):
        self._arrange()
        super().resizeEvent(e)

    @staticmethod
    def _entries(folder: space.Folder, limit: int) -> list[tuple[int, str, object]]:
        everything = [e for e in folder.entries() if e[0] > 0]
        out, left = everything[:limit], everything[limit:]
        rest, count = folder.rest + sum(e[0] for e in left), folder.rest_count + len(left)
        if rest > 0:  # (what doesn't get a box of its own still takes its share of the map)
            name = tr("{count} smaller items", count=count) if left else tr("{count} smaller files", count=count)
            out.append((rest, name, "rest"))
            out.sort(key=lambda e: e[0], reverse=True)
        return out

    def _arrange(self):
        self.boxes = []
        f = self.folder
        if f is None or f.size <= 0:
            return
        self._nest(f, QRectF(self.rect()).adjusted(1, 1, -1, -1), 0)

    def _nest(self, folder: space.Folder, r: QRectF, level: int):
        entries = self._entries(folder, 120 if level == 0 else 48)
        rects = space.squarify([e[0] for e in entries], r.x(), r.y(), r.width(), r.height())
        for (size, name, what), (x, y, w, h) in zip(entries, rects):
            if w < 2 or h < 2:
                continue
            sub = what if isinstance(what, space.Folder) else None
            kind = "folder" if sub is not None else "rest" if what == "rest" else "file"
            self.boxes.append(Box(QRectF(x, y, w, h), name, size, kind, level, sub, folder))
            if sub is not None and level + 1 < self.DEPTH and sub.size > 0 and w > 36 and h > 34:
                head = 18 if w > 60 and h > 40 else 3
                self._nest(sub, QRectF(x, y, w, h).adjusted(3, head, -3, -3), level + 1)

    def _color(self, b: Box) -> QColor:
        t = self.theme
        if b.kind == "folder":
            return mix(t.panel, t.accent, max(0.12, (0.42 if t.dark else 0.3) - 0.08 * b.level))
        if b.kind == "rest":
            return mix(t.panel, t.muted, 0.18)
        color = QColor(KIND_COLORS[space.kind_of(b.name)])
        return color.lighter(100 + 6 * b.level) if t.dark else color

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self.theme
        if not self.boxes:
            p.setPen(t.muted)
            p.drawText(self.rect(), Qt.AlignCenter, tr("Nothing here takes up space."))
            return
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, self.font().pointSizeF() - 1.5))
        fm, sfm = QFontMetrics(self.font()), QFontMetrics(small)
        left = Qt.AlignLeft | Qt.AlignTop | Qt.AlignAbsolute
        for i, b in enumerate(self.boxes):
            color = self._color(b)
            if i == self.hover:
                color = color.lighter(125)
            box = b.rect
            p.setPen(QPen(t.bg, 1.2))
            p.setBrush(color)
            radius = 4 if b.level == 0 else 2
            p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
            if b.kind == "rest":  # many small files: a quiet hatch, so the eye goes to the big ones first
                hatch = QColor(t.muted)
                hatch.setAlpha(60)
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(hatch, Qt.BDiagPattern))
                p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
            ink = QColor("#111") if color.lightness() > 150 else QColor("#fff")
            font, metrics = (self.font(), fm) if b.level == 0 else (small, sfm)
            if box.width() > 46 and box.height() > metrics.height() + 2:
                p.setPen(ink)
                p.setFont(font)
                p.drawText(box.adjusted(5, 1, -4, 0), left,
                           metrics.elidedText(b.name, Qt.ElideMiddle, int(box.width() - 10)))
                if b.kind != "folder" and box.height() > metrics.height() * 2 + 6 and b.level == 0:
                    p.setFont(small)
                    p.drawText(box.adjusted(5, 3 + metrics.height(), -4, 0), left,
                               sfm.elidedText(human_size(b.size), Qt.ElideRight, int(box.width() - 10)))

    def _at(self, pos) -> int:
        found = -1
        for i, b in enumerate(self.boxes):
            if b.rect.contains(pos):
                found = i  # (boxes inside a folder come after it: the innermost wins)
        return found

    def mouseMoveEvent(self, e):
        i = self._at(e.position())
        if i != self.hover:
            self.hover = i
            self.update()
        if i >= 0:
            b = self.boxes[i]
            where = ""
            if b.parent is not None and b.parent is not self.folder:
                where = f"{b.parent.name}  ›  "
            QToolTip.showText(e.globalPosition().toPoint(), f"{where}{b.name}\n{human_size(b.size)}", self)

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def _target(self, i: int):
        """(path, Folder or None, folder to open) for a box."""
        b = self.boxes[i]
        if b.kind == "folder":
            return b.folder.path, b.folder, b.folder
        if b.kind == "rest":
            return None, None, b.parent if b.parent is not self.folder else None
        return b.parent.path / b.name, None, b.parent if b.parent is not self.folder else None

    def mouseReleaseEvent(self, e):
        i = self._at(e.position())
        if i < 0:
            return
        path, folder, to_open = self._target(i)
        if e.button() == Qt.LeftButton and to_open is not None:
            self.opened.emit(to_open)
        elif e.button() == Qt.RightButton and path is not None:
            self.menu.emit((path, folder), e.globalPosition().toPoint())


class KindsBar(QWidget):
    """One bar split by kind of file (videos, pictures, …), with the sizes underneath."""

    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.kinds: dict = {}
        self.setMinimumHeight(14)
        self.setMaximumHeight(14)

    def set_kinds(self, kinds: dict):
        self.kinds = {k: v for k, v in kinds.items() if v > 0}
        self.update()

    def paintEvent(self, e):
        total = sum(self.kinds.values())
        if not total:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        x = 0.0
        w = self.width()
        for kind, size in sorted(self.kinds.items(), key=lambda kv: kv[1], reverse=True):
            part = w * size / total
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(KIND_COLORS[kind]))
            p.drawRect(QRectF(x, 2, max(1.0, part), 10))
            x += part


# ----------------------------------------------------------------------------- the drives

class DriveCard(QPushButton):
    """A drive to pick: its name, how full it is (the bar turns amber, then red, as it fills) and
    what's free. With no drive, it's the "Look at a folder" card."""

    def __init__(self, theme, drive: space.Drive | None = None, title: str = "", line: str = "",
                 seen: float | None = None):
        super().__init__()
        self.theme, self.drive = theme, drive
        if drive is not None:
            name = drive.path.rstrip("\\")
            label = drive.label or (tr("USB drive") if drive.removable else tr("Local disk"))
            self.title = ltr(f"{label} ({name})")
            self.line = tr("{free} free of {total}", free=human_size(drive.free), total=human_size(drive.total))
            if seen is not None:  # its map is kept: opening it again is instant
                self.line += "   ·   " + tr("mapped {when}", when=time_ago(seen))
            self.fill = drive.used / drive.total if drive.total else 0.0
        else:
            self.title, self.line, self.fill = title, line, None
        self.setAccessibleName(self.title)
        self.setToolTip(f"{self.title}\n{self.line}")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_Hover)
        self.setFocusPolicy(Qt.StrongFocus)

    def sizeHint(self):
        return QSize(260, self.minimumHeight())

    def _bar_color(self) -> QColor:
        t = self.theme
        if self.fill is None or self.fill < 0.8:
            return t.accent
        return QColor("#e8a33d") if self.fill < 0.9 else (t.danger or QColor("#e5484d"))

    def paintEvent(self, e):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rtl = self.layoutDirection() == Qt.RightToLeft
        hover = self.underMouse()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.drive is None:  # "Look at a folder": a quieter, dashed card
            pen = QPen(t.accent if hover else t.line_strong or t.border, 1, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(t.hover if hover else Qt.NoBrush)
        else:
            p.setPen(QPen(t.accent if self.hasFocus() else (t.line_strong if hover else t.border) or t.border, 1))
            p.setBrush(t.hover if hover else t.panel)
            if self.isDown():
                p.setBrush(mix(t.panel, t.text, 0.08))
        p.drawRoundedRect(r, 12, 12)
        pad = 16
        top = r.top() + pad if self.drive is not None else r.center().y() - 20  # (the folder card: centred)
        tile = QRectF(r.right() - pad - 40 if rtl else r.left() + pad, top, 40, 40)
        p.setPen(Qt.NoPen)
        p.setBrush(mix(t.panel, t.accent, 0.2 if t.dark else 0.13))
        p.drawRoundedRect(tile, 10, 10)
        glyph = "folder_search" if self.drive is None else "hard_drive"
        icons.paint(p, tile.adjusted(10, 10, -10, -10), glyph, t.accent)
        x0 = tile.right() + 14 if not rtl else r.left() + pad
        x1 = r.right() - pad if not rtl else tile.left() - 14
        side = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute
        title_font = QFont(self.font())
        title_font.setPointSizeF(self.font().pointSizeF() + 1.5)
        title_font.setWeight(QFont.Weight.DemiBold)
        fm = QFontMetrics(title_font)
        p.setFont(title_font)
        p.setPen(t.text)
        right_text = ""
        if self.fill is not None:
            right_text = tr("{percent}% full", percent=round(self.fill * 100))
        rfm = QFontMetrics(self.font())
        right_w = rfm.horizontalAdvance(right_text) + 12 if right_text else 0
        title_box = QRectF(x0 + (right_w if rtl else 0), tile.top() - 1, x1 - x0 - right_w, fm.height())
        p.drawText(title_box, side | Qt.AlignVCenter, fm.elidedText(self.title, Qt.ElideRight, int(title_box.width())))
        if right_text:
            p.setFont(self.font())
            p.setPen(self._bar_color() if self.fill >= 0.8 else t.muted)
            p.drawText(QRectF(x0, tile.top() - 1, x1 - x0, fm.height()),
                       ((Qt.AlignLeft if rtl else Qt.AlignRight) | Qt.AlignAbsolute) | Qt.AlignVCenter, right_text)
        p.setFont(self.font())
        p.setPen(t.muted)
        line_box = QRectF(x0, title_box.bottom() + 2, x1 - x0, rfm.height())
        p.drawText(line_box, side | Qt.AlignVCenter, rfm.elidedText(self.line, Qt.ElideRight, int(line_box.width())))
        if self.fill is not None:  # how full: a soft track and a rounded bar
            bar = QRectF(x0, r.bottom() - pad - 8, x1 - x0, 8)
            p.setPen(Qt.NoPen)
            p.setBrush(mix(t.panel, t.text, 0.1 if t.dark else 0.08))
            p.drawRoundedRect(bar, 4, 4)
            w = max(8.0, bar.width() * min(1.0, self.fill))
            done = QRectF(bar.right() - w if rtl else bar.left(), bar.top(), w, bar.height())
            p.setBrush(self._bar_color())
            p.drawRoundedRect(done, 4, 4)
        p.end()


def _confirm(parent, title: str, text: str, yes: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(text)
    ok = box.addButton(yes, QMessageBox.DestructiveRole)
    box.addButton(tr("Cancel"), QMessageBox.RejectRole)
    box.exec()
    return box.clickedButton() is ok


# ----------------------------------------------------------------------------- the page

class SpacePage(QWidget):
    def __init__(self, controller: "Controller", header):
        super().__init__()
        self.c = controller
        self.scan: space.Scan | None = None
        self.here: space.Folder | None = None
        self.cancel = threading.Event()
        self.job = None
        self.junk: list[space.Junk] = []
        self.dupes: list = []
        # the maps already made, newest last: key -> [scan, the folder open in it, when it was made]
        self.kept: OrderedDict[str, list] = OrderedDict()
        self._forget_timer = QTimer(self)
        self._forget_timer.setSingleShot(True)
        self._forget_timer.timeout.connect(self._forget_old_maps)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(12)
        lay.addLayout(header)
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.section_buttons = QButtonGroup(self)
        for key, text in SECTIONS:
            b = QPushButton(tr(text))
            b.setObjectName("subtab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(key == "map")
            b.clicked.connect(lambda _=False, k=key: self.show_section(k))
            self.section_buttons.addButton(b)
            tabs.addWidget(b)
        tabs.addStretch()
        lay.addLayout(tabs)
        self.sections = QStackedWidget()
        self.sections.addWidget(self._map_section())
        self.sections.addWidget(self._clean_section())
        self.sections.addWidget(self._dupes_section())
        lay.addWidget(self.sections, 1)
        self.section = "map"

    def show_section(self, key: str):
        self.section = key
        self.sections.setCurrentIndex([k for k, _ in SECTIONS].index(key))
        for b, (k, _t) in zip(self.section_buttons.buttons(), SECTIONS):
            b.setChecked(k == key)
        if key == "clean" and not self.junk and self.job is None:
            self.find_junk()

    def refresh(self):
        if self.scan is None and self.job is None:
            self._fill_drives()

    # ---- the disk map

    def _map_section(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        # the drives (to begin with)
        self.drives_box = QWidget()
        dl = QVBoxLayout(self.drives_box)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(8)
        dl.addWidget(_label(tr("Pick a drive to see what's filling it."), "muted"))
        self.drive_rows = QGridLayout()
        self.drive_rows.setHorizontalSpacing(12)
        self.drive_rows.setVerticalSpacing(12)
        self.drive_rows.setColumnStretch(0, 1)
        self.drive_rows.setColumnStretch(1, 1)
        dl.addLayout(self.drive_rows)
        dl.addStretch()
        lay.addWidget(self.drives_box)
        # looking (with Stop)
        self.busy_box = QFrame()
        self.busy_box.setObjectName("card")
        bl = QVBoxLayout(self.busy_box)
        bl.setContentsMargins(18, 16, 18, 16)
        self.busy_title = _label("", "h2")
        self.busy_line = _label("", "muted")
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setTextVisible(False)
        stop = _button(tr("Stop"), None, self._stop)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self._back_button())
        top.addWidget(self.busy_title, 1)
        top.addWidget(stop)
        bl.addLayout(top)
        bl.addWidget(self.busy_line)
        bl.addWidget(self.busy_bar)
        lay.addWidget(self.busy_box)
        self.busy_box.hide()
        # the map
        self.map_box = QWidget()
        ml = QVBoxLayout(self.map_box)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(8)
        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.back_btn = self._back_button()
        self.up_btn = _button(tr("Up"), None, self._up)
        self.where = ElidedLabel()
        self.where.setObjectName("h2")
        self.again_btn = _button(tr("Look again"), None, self._rescan)
        self.again_btn.setToolTip(tr("Look through it again, to see what changed since"))
        self.drives_btn = _button(tr("Other drives"), None, self._back_to_drives)
        self._icon_buttons = [(self.up_btn, "arrow_up"), (self.again_btn, "arrow_clockwise"),
                              (self.drives_btn, "hard_drive")]
        nav.addWidget(self.back_btn)
        nav.addWidget(self.where, 1)
        nav.addWidget(self.up_btn)
        nav.addWidget(self.again_btn)
        nav.addWidget(self.drives_btn)
        ml.addLayout(nav)
        self.summary = _label("", "muted", wrap=True)
        ml.addWidget(self.summary)
        body = QHBoxLayout()
        body.setSpacing(12)
        self.treemap = Treemap(self.c.theme)
        self.treemap.opened.connect(self.open_folder)
        self.treemap.menu.connect(lambda what, pos: self._menu(what[0], what[1], pos))
        body.addWidget(self.treemap, 3)
        side = QVBoxLayout()
        side.setSpacing(6)
        self.list_mode = "here"
        switch = QHBoxLayout()
        self.here_btn = _button(tr("In this folder"), "subtab", lambda: self._list_mode("here"))
        self.big_btn = _button(tr("Biggest files"), "subtab", lambda: self._list_mode("big"))
        for b in (self.here_btn, self.big_btn):
            b.setCheckable(True)
            switch.addWidget(b)
        switch.addStretch()
        self.here_btn.setChecked(True)
        side.addLayout(switch)
        self.items = QTreeWidget()
        self.items.setObjectName("history")
        self.items.setHeaderLabels([tr("Name"), tr("Size")])
        self.items.setRootIsDecorated(False)
        self.items.setUniformRowHeights(True)
        self.items.header().setStretchLastSection(False)
        self.items.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.items.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.items.setContextMenuPolicy(Qt.CustomContextMenu)
        self.items.customContextMenuRequested.connect(self._list_menu)
        self.items.itemDoubleClicked.connect(self._list_open)
        self.items.setMinimumWidth(240)  # (names stay readable next to the map)
        side.addWidget(self.items, 1)
        holder = QWidget()
        holder.setLayout(side)
        side.setContentsMargins(0, 0, 0, 0)
        body.addWidget(holder, 2)
        ml.addLayout(body, 1)
        self.kinds_bar = KindsBar(self.c.theme)
        ml.addWidget(self.kinds_bar)
        self.kinds_text = _label("", "muted", wrap=True)
        ml.addWidget(self.kinds_text)
        lay.addWidget(self.map_box, 1)
        self.map_box.hide()
        self._paint_backs()
        self._fill_drives()
        return w

    def _fill_drives(self):
        _clear_layout(self.drive_rows)
        cards = []
        for d in space.drives():
            kept = self.kept.get(_key(Path(d.path)))
            card = DriveCard(self.c.theme, d, seen=kept[2] if kept else None)
            card.setObjectName("driveCard")
            card.clicked.connect(lambda _=False, p=d.path: self.start_scan(Path(p)))
            cards.append(card)
        folder = DriveCard(self.c.theme, None, tr("Look at a folder…"), tr("Any folder, on any drive"))
        folder.clicked.connect(self._pick_folder)
        for i, card in enumerate(cards + [folder]):
            self.drive_rows.addWidget(card, i // 2, i % 2)
        self.drive_cards = cards + [folder]

    def _back_button(self) -> QPushButton:
        """← : back to the drives (or up a folder), like Esc."""
        b = QPushButton()
        b.setObjectName("iconButton")
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedSize(34, 34)
        b.setToolTip(tr("Back (Esc)"))
        b.setAccessibleName(tr("Back"))
        b.clicked.connect(self.go_back)
        self._backs = getattr(self, "_backs", [])
        self._backs.append(b)
        self._paint_backs()
        return b

    def _paint_backs(self):
        t = self.c.theme
        rtl = self.layoutDirection() == Qt.RightToLeft or QApplication.layoutDirection() == Qt.RightToLeft
        for b in getattr(self, "_backs", []):
            b.setIcon(icons.icon("arrow_right" if rtl else "arrow_left", t.text))
        for b, name in getattr(self, "_icon_buttons", []):
            b.setIcon(icons.icon(name, t.text, disabled=t.muted))

    def _pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, tr("Look at a folder"), str(Path.home()))
        if folder:
            self.start_scan(Path(folder))

    def start_scan(self, root: Path, again: bool = False):
        if self.job is not None:
            return
        kept = None if again else self.kept.get(_key(root))
        if kept is not None:  # already looked through: its map, right away
            self.kept.move_to_end(_key(root))
            self.root = root
            self._show_map(kept[0], kept[1])
            return
        self.cancel = threading.Event()
        self.root = root
        self.drives_box.hide()
        self.map_box.hide()
        self.busy_box.show()
        self.busy_title.setText(tr("Looking through {name}…", name=ltr(str(root))))
        self.busy_line.setText(tr("This takes a moment on a big drive."))
        cancel = self.cancel
        self.job = _work(lambda progress: space.scan(root, cancel, lambda *a: progress(a)), self._scan_progress,
                         self._scanned)

    def _scan_progress(self, args):
        if not _alive(self):
            return
        files, size, folder = args if isinstance(args, tuple) else (0, 0, "")
        self.busy_line.setText(tr("{count} files, {size}", count=f"{files:,}", size=human_size(size))
                               + "   ·   " + ltr(short_name(folder, 70)))

    def _stop(self):
        self.cancel.set()

    def _scanned(self, result):
        if not _alive(self):
            return
        self.job = None
        self.busy_box.hide()
        if getattr(self, "_leaving", False):
            self._leaving = False
            self._back_to_drives()
            return
        if isinstance(result, Exception) or result is None:
            log.error("looking through %s failed", self.root, exc_info=result if isinstance(result, Exception) else None)
            self.c.tell(tr("Couldn't look through it"), ltr(str(self.root)))
            self._back_to_drives()
            return
        if result.cancelled and result.root.size == 0:
            self._back_to_drives()
            return
        self.kept[_key(self.root)] = [result, result.root, time.time()]
        self.kept.move_to_end(_key(self.root))
        while len(self.kept) > KEEP_MAPS:
            self.kept.popitem(last=False)
        self._show_map(result, result.root)

    def _show_map(self, result: space.Scan, folder: space.Folder):
        self.scan = result
        self.drives_box.hide()
        self.busy_box.hide()
        self.map_box.show()
        self._forget_timer.stop()
        self.open_folder(folder)
        self.kinds_bar.set_kinds(result.kinds)
        total = sum(result.kinds.values()) or 1
        parts = [f"<span style='color:{KIND_COLORS[k]}'>■</span>&nbsp;{html.escape(tr(space.KINDS[k]))} "
                 f"{human_size(v)}" for k, v in sorted(result.kinds.items(), key=lambda kv: -kv[1])
                 if v > total * 0.005]
        self.kinds_text.setText("&nbsp;&nbsp;&nbsp;".join(parts))

    def _summary(self):
        s, f = self.scan, self.here
        if s is None or f is None:
            return
        bits = [tr("{size} in {count} files", size=human_size(f.size), count=f"{f.files:,}")]
        if s.cancelled:
            bits.append(tr("stopped before the end"))
        if f is s.root and s.denied:
            bits.append(tr("{count} folders Windows keeps private were skipped", count=s.denied))
        if f is s.root and s.cloud:
            bits.append(tr("{count} files only in the cloud take no space here", count=f"{s.cloud:,}"))
        kept = self.kept.get(_key(s.root.path))
        if kept is not None and kept[0] is s and time.time() - kept[2] >= 60:
            bits.append(tr("mapped {when}", when=time_ago(kept[2])))
        self.summary.setText("   ·   ".join(bits))

    def open_folder(self, folder: space.Folder):
        self.here = folder
        if self.scan is not None:
            kept = self.kept.get(_key(self.scan.root.path))
            if kept is not None and kept[0] is self.scan:
                kept[1] = folder  # (opening it again comes back here)
        self.where.set_full(str(folder.path))
        self.up_btn.setEnabled(folder.parent is not None)
        self.treemap.show_folder(folder)
        self._summary()
        self._fill_list()

    def _up(self):
        if self.here is not None and self.here.parent is not None:
            self.open_folder(self.here.parent)

    def _rescan(self):
        root = self.scan.root.path if self.scan else None
        self.scan = self.here = None
        self.treemap.show_folder(None)
        if root is not None:
            self.kept.pop(_key(root), None)
            self.start_scan(root, again=True)

    def go_back(self) -> bool:
        """Back (Alt + Left, the mouse's back button): up a folder, then back to the drives."""
        if self.section != "map":
            return False
        if self.job is not None and self.busy_box.isVisible():
            self._leaving = True  # (back to the drives, not to what was found so far)
            self._stop()
            return True
        if self.here is not None and self.here.parent is not None:
            self._up()
            return True
        if self.scan is not None:
            self._back_to_drives()
            return True
        return False

    def _back_to_drives(self):
        self.scan = self.here = None
        self.treemap.show_folder(None)
        self.items.clear()
        self.map_box.hide()
        self.busy_box.hide()
        self.drives_box.show()
        self._fill_drives()
        if self.kept:
            self._forget_timer.start(FORGET_AFTER * 1000)

    def _forget_old_maps(self):
        """Lets go of the maps no one has looked at for a while (a whole drive's map takes room)."""
        showing = self.scan
        for key in [k for k, v in self.kept.items() if v[0] is not showing]:
            del self.kept[key]
        gc.collect()  # (a map is a tree of folders pointing at each other)
        if showing is None:
            self._fill_drives()

    def forget_maps(self):
        """Files were deleted here: the maps kept are out of date (the next look starts afresh)."""
        self.kept.clear()

    def _list_mode(self, mode: str):
        self.list_mode = mode
        self.here_btn.setChecked(mode == "here")
        self.big_btn.setChecked(mode == "big")
        self._fill_list()

    def _fill_list(self):
        self.items.clear()
        if self.here is None or self.scan is None:
            return
        rows = []
        if self.list_mode == "big":
            for size, path in self.scan.biggest:
                rows.append((ltr(short_name(Path(path).name, 60)), size, Path(path), None, path))
        else:
            for size, name, folder in self.here.entries()[:300]:
                rows.append((ltr(name), size,
                             folder.path if folder is not None else self.here.path / name, folder, None))
            if self.here.rest:
                rows.append((tr("{count} smaller files", count=self.here.rest_count), self.here.rest, None, None, None))
        folder_icon = icons.icon("folder", self.c.theme.muted)
        for text, size, path, folder, tip in rows:
            it = QTreeWidgetItem([text, human_size(size)])
            if folder is not None:
                it.setIcon(0, folder_icon)
            it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            it.setData(0, Qt.UserRole, (path, folder))
            if tip:
                it.setToolTip(0, tip)
            self.items.addTopLevelItem(it)

    def _list_open(self, item):
        path, folder = item.data(0, Qt.UserRole) or (None, None)
        if folder is not None:
            self.open_folder(folder)
        elif path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _list_menu(self, pos):
        item = self.items.itemAt(pos)
        if item is None:
            return
        path, folder = item.data(0, Qt.UserRole) or (None, None)
        if path is not None:
            self._menu(path, folder, self.items.viewport().mapToGlobal(pos))

    def _menu(self, path: Path, folder, pos: QPoint):
        menu = QMenu(self)
        round_menu(menu)
        if folder is not None:
            menu.addAction(tr("Look inside"), lambda: self.open_folder(folder))
        else:
            menu.addAction(tr("Open"), lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        menu.addAction(tr("Show in folder"), lambda: reveal_in_explorer(path))
        menu.addSeparator()
        program = self._program_at(path)
        if program is not None:
            menu.addAction(tr("Uninstall {name}…", name=ltr(short_name(program.name, 40))),
                           lambda: self._uninstall(program, path, folder))
        act = menu.addAction(tr("Move to the Recycle Bin…"), lambda: self._remove(path, folder))
        if space.protected(path) or program is not None:  # (a program's folder in the bin would break it)
            act.setEnabled(False)
            act.setText(tr("Windows needs this (remove programs with the Uninstaller)"))
        menu.exec(pos)

    def _program_at(self, path: Path):
        """The installed program this folder belongs to (only while the Uninstaller is in use)."""
        from . import tools as all_tools
        if os.name != "nt" or not all_tools.is_on(self.c.settings, "uninstaller"):
            return None
        from . import uninstall as un
        if getattr(self, "_apps", None) is None:
            try:
                self._apps = un.list_apps(include_store=False)
            except Exception:
                log.warning("couldn't list the installed programs", exc_info=True)
                self._apps = []
        return un.app_at(path, self._apps)

    def _uninstall(self, program, path: Path, folder):
        """Uninstalls the program right here (the Uninstaller's own steps, leftovers and backup included),
        then takes its folder off the map."""
        from .appspage import UninstallDialog
        dlg = UninstallDialog(self.c, [program], self._apps or [program], False, self.window().windowIcon())
        dlg.exec()
        changed = dlg.changed
        dlg.deleteLater()
        self._apps = None  # (read again next time)
        if changed and not path.exists():
            size = folder.size if folder is not None else 0
            self._forget(path, folder, size)

    def _remove(self, path: Path, folder):
        size = folder.size if folder is not None else _size(path)
        if not _confirm(self, tr("Move to the Recycle Bin?"),
                        tr("{name} ({size}) goes to the Recycle Bin, where you can put it back. The space is freed "
                           "when the Recycle Bin is emptied.", name=ltr(path.name), size=human_size(size)),
                        tr("Move to the Recycle Bin")):
            return
        if not space.recycle([path]):
            self.c.tell(tr("Couldn't move it"), tr("{name} is in use or Windows doesn't allow it.", name=ltr(path.name)))
            return
        self._forget(path, folder, size)

    def _forget(self, path: Path, folder, size: int):
        """Takes it out of the map without looking through the drive again."""
        if self.scan is None:
            return
        if folder is not None:
            parent = folder.parent
            if parent is not None and folder in parent.children:
                parent.children.remove(folder)
        else:
            parent = self._folder_of(path.parent)
            if parent is not None:
                parent.big = [b for b in parent.big if b[1] != path.name]
        p = parent
        while p is not None:
            p.size = max(0, p.size - size)
            p = p.parent
        self.scan.biggest = [b for b in self.scan.biggest if Path(b[1]) != path and
                             not str(b[1]).startswith(str(path) + os.sep)]
        self.open_folder(self.here if self.here is not folder else (parent or self.scan.root))

    def _folder_of(self, path: Path) -> space.Folder | None:
        root = self.scan.root if self.scan else None
        if root is None:
            return None
        try:
            rel = path.relative_to(root.path)
        except ValueError:
            return None
        f = root
        for part in rel.parts:
            f = next((c for c in f.children if c.name == part), None)
            if f is None:
                return None
        return f

    # ---- cleaning up

    def _clean_section(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(_label(tr("Things that are safe to remove. Nothing personal is touched without asking."),
                             "muted", wrap=True))
        self.junk_box = QFrame()
        self.junk_box.setObjectName("card")
        self.junk_rows = QVBoxLayout(self.junk_box)
        self.junk_rows.setContentsMargins(18, 14, 18, 14)
        self.junk_rows.setSpacing(10)
        lay.addWidget(self.junk_box)
        row = QHBoxLayout()
        self.clean_btn = _button(tr("Clean up"), "primary", self._clean)
        row.addWidget(self.clean_btn)
        row.addWidget(_button(tr("Look again"), None, self.find_junk))
        row.addStretch()
        row.addWidget(_button(tr("More in Windows' Storage settings…"), "flat",
                              lambda: QDesktopServices.openUrl(QUrl("ms-settings:storagesense"))))
        lay.addLayout(row)
        self.clean_note = _label("", "muted", wrap=True)
        lay.addWidget(self.clean_note)
        lay.addStretch()
        self.junk_checks: list[tuple[QCheckBox, space.Junk]] = []
        return w

    def _clear_junk_rows(self):
        while self.junk_rows.count():
            item = self.junk_rows.takeAt(0)
            if item.widget():
                item.widget().hide()  # (not drawn again at its old place before it goes)
                item.widget().deleteLater()
            elif item.layout():
                _clear_layout(item.layout())
        self.junk_checks = []

    def find_junk(self):
        if self.job is not None:
            return
        self._clear_junk_rows()
        self.junk_rows.addWidget(_label(tr("Looking for things that can go…"), "muted"))
        self.clean_btn.setEnabled(False)
        downloads = None
        try:
            from .app import downloads_folder
            downloads = downloads_folder()
        except Exception:
            pass
        cancel = self.cancel = threading.Event()
        self.job = _work(lambda progress: space.find_junk(downloads, cancel), lambda _: None, self._junk_found)

    def _junk_found(self, result):
        if not _alive(self):
            return
        self.job = None
        self._clear_junk_rows()
        if isinstance(result, Exception):
            self.junk_rows.addWidget(_label(str(result), "muted", wrap=True))
            return
        self.junk = [j for j in result if j.size > 0 or j.key == "bin"]
        if not self.junk:
            self.junk_rows.addWidget(_label(tr("Nothing to clean up. Nice and tidy."), "muted"))
        for j in self.junk:
            row = QHBoxLayout()
            row.setSpacing(12)
            title = tr(j.title, name=j.name) if j.name else tr(j.title)
            cb = QCheckBox(title)
            cb.setCursor(Qt.PointingHandCursor)
            cb.setChecked(j.ticked and j.size > 0 and not j.busy)
            cb.setEnabled(j.size > 0 and not j.busy)
            cb.toggled.connect(self._junk_total)
            words = QVBoxLayout()
            words.setSpacing(2)
            words.addWidget(cb)
            detail = tr(j.detail)
            if j.busy:
                detail = tr("Close {name} first, then look again.", name=j.busy)
            note = _label(detail, "muted", wrap=True)
            note.setContentsMargins(28, 0, 0, 0)
            words.addWidget(note)
            row.addLayout(words, 1)
            row.addWidget(_label(human_size(j.size) if j.size else tr("Empty"), "h2"), 0, Qt.AlignTop)
            self.junk_rows.addLayout(row)
            self.junk_checks.append((cb, j))
        self._junk_total()

    def _junk_total(self, *_):
        total = sum(j.size for cb, j in self.junk_checks if cb.isChecked())
        self.clean_btn.setEnabled(total > 0 and self.job is None)
        self.clean_btn.setText(tr("Clean up {size}", size=human_size(total)) if total else tr("Clean up"))

    def _clean(self):
        chosen = [j for cb, j in self.junk_checks if cb.isChecked()]
        if not chosen or self.job is not None:
            return
        total = sum(j.size for j in chosen)
        if not _confirm(self, tr("Clean up?"), tr("{size} of temporary files and caches will be deleted. This can't "
                                                  "be undone (old installers go to the Recycle Bin).",
                                                  size=human_size(total)), tr("Clean up")):
            return
        self.clean_btn.setEnabled(False)
        self.clean_note.setText(tr("Cleaning up…"))
        cancel = self.cancel = threading.Event()
        self.job = _work(lambda progress: sum(space.clean(j, cancel) for j in chosen), lambda _: None, self._cleaned)

    def _cleaned(self, freed):
        if not _alive(self):
            return
        self.job = None
        if isinstance(freed, Exception):
            self.clean_note.setText(str(freed))
            return
        self.clean_note.setText(tr("Freed {size}. Files that were in use were left alone.", size=human_size(freed)))
        self.c.say(tr("Cleaned up"), tr("Freed {size}.", size=human_size(freed)), done=True)
        self.junk = []
        self.forget_maps()
        self.find_junk()

    # ---- duplicates

    def _dupes_section(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(_label(tr("Files that are exactly the same, byte for byte. Pick the copies to remove: they go to "
                                "the Recycle Bin."), "muted", wrap=True))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.dupe_btn = _button(tr("Find duplicates in your folders"), "primary", lambda: self.find_dupes(None))
        row.addWidget(self.dupe_btn)
        row.addWidget(_button(tr("In a folder…"), None, self._dupes_in_folder))
        row.addStretch()
        lay.addLayout(row)
        self.dupe_where = _label(tr("Your folders: {names}", names=", ".join(tr(n) for n in DUPE_FOLDERS)), "muted",
                                 wrap=True)
        lay.addWidget(self.dupe_where)
        self.dupe_bar = QProgressBar()
        self.dupe_bar.setTextVisible(False)
        self.dupe_bar.hide()
        lay.addWidget(self.dupe_bar)
        self.dupe_tree = QTreeWidget()
        self.dupe_tree.setObjectName("history")
        self.dupe_tree.setHeaderLabels([tr("File"), tr("Changed"), tr("Size")])
        self.dupe_tree.header().setStretchLastSection(False)
        self.dupe_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.dupe_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.dupe_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.dupe_tree.itemChanged.connect(lambda *_: self._dupe_total())
        self.dupe_tree.itemDoubleClicked.connect(self._dupe_open)
        lay.addWidget(self.dupe_tree, 1)
        self.dupe_tree.hide()
        row = QHBoxLayout()
        self.dupe_note = _label("", "muted", wrap=True)
        row.addWidget(self.dupe_note, 1)
        self.dupe_remove = _button(tr("Remove the ticked copies"), "primary", self._remove_dupes)
        self.dupe_remove.hide()
        row.addWidget(self.dupe_remove)
        lay.addLayout(row)
        return w

    def _dupes_in_folder(self):
        folder = QFileDialog.getExistingDirectory(self, tr("Find duplicates in"), str(Path.home()))
        if folder:
            self.find_dupes([Path(folder)])

    def find_dupes(self, folders):
        if self.job is not None:
            return
        if folders is None:
            home = Path.home()
            folders = [home / n for n in DUPE_FOLDERS]
            try:
                from .app import downloads_folder
                folders.append(downloads_folder())
            except Exception:
                pass
            self.dupe_where.setText(tr("Your folders: {names}", names=", ".join(tr(n) for n in DUPE_FOLDERS)))
        else:
            self.dupe_where.setText(ltr(str(folders[0])))
        self.dupe_tree.clear()
        self.dupe_tree.hide()
        self.dupe_remove.hide()
        self.dupe_bar.setRange(0, 0)
        self.dupe_bar.show()
        self.dupe_btn.setEnabled(False)
        self.dupe_note.setText(tr("Looking through your files…"))
        cancel = self.cancel = threading.Event()
        self.job = _work(lambda progress: space.find_duplicates(folders, cancel=cancel,
                                                                progress=lambda *a: progress(a)),
                         self._dupe_progress, self._dupes_found)

    def _dupe_progress(self, args):
        if not _alive(self):
            return
        stage, done, total = args
        if stage == "look":
            self.dupe_note.setText(tr("Looking through your files… {count}", count=f"{done:,}"))
        else:
            self.dupe_bar.setRange(0, max(1, total))
            self.dupe_bar.setValue(done)
            self.dupe_note.setText(tr("Comparing files that are the same size…"))

    def _dupes_found(self, groups):
        if not _alive(self):
            return
        self.job = None
        self.dupe_bar.hide()
        self.dupe_btn.setEnabled(True)
        if isinstance(groups, Exception):
            self.dupe_note.setText(str(groups))
            return
        self.dupes = groups
        if not groups:
            self.dupe_note.setText(tr("No duplicates found."))
            return
        self.dupe_tree.blockSignals(True)
        for group in groups[:500]:
            size = group[0][1]
            files = sorted(group, key=lambda g: g[2])  # the oldest first: it's kept
            head = QTreeWidgetItem([tr("{count} copies of {name}", count=len(group), name=ltr(Path(files[0][0]).name)),
                                    "", human_size(size)])
            f = head.font(0)
            f.setWeight(QFont.Weight.DemiBold)
            head.setFont(0, f)
            self.dupe_tree.addTopLevelItem(head)
            for i, (path, s, mtime) in enumerate(files):
                it = QTreeWidgetItem([ltr(path), date_text(mtime, True), ""])
                it.setData(0, Qt.UserRole, (path, s))
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Unchecked if i == 0 else Qt.Checked)
                it.setToolTip(0, path)
                head.addChild(it)
            head.setExpanded(True)
        self.dupe_tree.blockSignals(False)
        self.dupe_tree.show()
        self.dupe_remove.show()
        self._dupe_total()

    def _ticked(self) -> list[tuple[str, int]]:
        out = []
        for i in range(self.dupe_tree.topLevelItemCount()):
            head = self.dupe_tree.topLevelItem(i)
            for j in range(head.childCount()):
                it = head.child(j)
                if it.checkState(0) == Qt.Checked:
                    out.append(it.data(0, Qt.UserRole))
        return out

    def _all_ticked_groups(self) -> bool:
        """True if every copy of some file is ticked (then nothing of it would be left)."""
        for i in range(self.dupe_tree.topLevelItemCount()):
            head = self.dupe_tree.topLevelItem(i)
            if head.childCount() and all(head.child(j).checkState(0) == Qt.Checked for j in range(head.childCount())):
                return True
        return False

    def _dupe_total(self):
        ticked = self._ticked()
        total = sum(s for _, s in ticked)
        self.dupe_remove.setEnabled(bool(ticked))
        self.dupe_remove.setText(tr("Remove the ticked copies ({size})", size=human_size(total)) if ticked
                                 else tr("Remove the ticked copies"))
        wasted = sum(g[0][1] * (len(g) - 1) for g in self.dupes)
        self.dupe_note.setText(tr("{count} files have copies, taking {size} more than they need.",
                                  count=len(self.dupes), size=human_size(wasted)))

    def _dupe_open(self, item, _col):
        data = item.data(0, Qt.UserRole)
        if data:
            reveal_in_explorer(Path(data[0]))

    def _remove_dupes(self):
        ticked = self._ticked()
        if not ticked or self.job is not None:
            return
        if self._all_ticked_groups():
            QMessageBox.information(self, tr("Keep one copy"), tr("Every copy of a file is ticked. Untick the one "
                                                                  "you want to keep."))
            return
        total = sum(s for _, s in ticked)
        if not _confirm(self, tr("Remove these copies?"),
                        tr("{count} copies ({size}) go to the Recycle Bin, where you can put them back.",
                           count=len(ticked), size=human_size(total)), tr("Move to the Recycle Bin")):
            return
        gone = space.recycle([Path(p) for p, _ in ticked])
        self.forget_maps()
        self.c.say(tr("Copies removed"), tr("{count} copies went to the Recycle Bin.", count=gone), done=True)
        left = {os.path.normcase(p) for p, _ in ticked if os.path.lexists(p)}
        removed = {os.path.normcase(p) for p, _ in ticked} - left
        self.dupes = [g for g in ([f for f in grp if os.path.normcase(f[0]) not in removed] for grp in self.dupes)
                      if len(g) > 1]
        self.dupe_tree.clear()
        self._dupes_found(self.dupes)

    def stop_work(self):
        self.cancel.set()

    def apply_theme(self, theme):
        self.treemap.theme = theme
        self.kinds_bar.theme = theme
        for card in getattr(self, "drive_cards", []):
            card.theme = theme
            card.update()
        self._paint_backs()
        self.treemap.update()
        self.kinds_bar.update()


def _key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _size(path: Path) -> int:
    return space._size_of(path)[0]


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


def _clear_layout(lay):
    while lay.count():
        item = lay.takeAt(0)
        if item.widget():
            item.widget().hide()  # (gone at once, not when Qt gets round to deleting it)
            item.widget().hide()  # (not drawn again at its old place before it goes)
            item.widget().deleteLater()
        elif item.layout():
            _clear_layout(item.layout())



_alive = work.alive
