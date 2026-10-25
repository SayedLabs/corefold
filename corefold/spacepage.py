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
    QHBoxLayout, QHeaderView, QLabel, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, \
    QStyledItemDelegate, QToolTip, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from . import icons, space, work
from .atomic.tokens import alpha, on_color
from .atomic.widgets import Dropdown, FlowLayout, GlowPanel, Group
from .core import human_size, short_name
from .i18n import ltr, tr
from .theme import mix, round_menu
from .timefmt import date_text, time_ago
from .winutil import properties, reveal_in_explorer

log = logging.getLogger("corefold")

if TYPE_CHECKING:
    from .app import Controller

KIND_COLORS = {"video": "#e5484d", "picture": "#30a46c", "music": "#8e4ec6", "document": "#0090ff",
               "archive": "#f5a524", "program": "#12a594", "other": "#8b8d98"}
# each folder at the top of the map gets a hue of its own (what's inside it is a shade of it)
FOLDER_HUES = ("#4f8cff", "#b06cf0", "#f08a4b", "#2fb8a6", "#e8619b", "#d9b03a", "#7a7ff5", "#5cba5c")
SECTIONS = [("map", "Disk map"), ("clean", "Clean up"), ("dupes", "Duplicates")]
SECTION_ICONS = {"map": "data_pie", "clean": "broom", "dupes": "copy"}
JUNK_ICONS = {"temp": "timer", "bin": "delete", "browser": "globe", "thumbs": "image_multiple", "gpu": "top_speed",
              "dumps": "stethoscope", "installers": "box", "winupdate": "arrow_download", "winold": "history",
              "app": "apps"}
DUPE_FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")
KEEP_MAPS = 2             # drives (or folders) whose map is kept, so opening one again is instant
FORGET_AFTER = 20 * 60    # seconds: maps not looked at for this long are let go (a big drive's map is big)
SHARE = Qt.UserRole + 1   # a list row's share of the biggest row (its little bar)
TINT = Qt.UserRole + 2    # …and the bar's colour


_work = work.run


def _fill_color(theme, fill: float) -> QColor:
    """How full a drive is: the accent, then amber, then red as it fills."""
    if fill < 0.8:
        return QColor(theme.accent)
    return QColor(theme.tokens.warning) if fill < 0.9 else QColor(theme.danger or "#e5484d")


def _ring(p: QPainter, r: QRectF, fill: float | None, color: QColor, track: QColor, width: float):
    """A round gauge: a soft track and an arc for how much of it is used (from the top, clockwise)."""
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(track, width, Qt.SolidLine, Qt.RoundCap))
    p.drawEllipse(r)
    if fill:
        p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, 90 * 16, -int(360 * 16 * max(0.02, min(1.0, fill))))


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


class Crumbs(QWidget):
    """Where you are in the map, as a path you can click: C:  ›  Users  ›  Sara. Folders in the middle
    fold into "…" when it doesn't fit."""
    picked = Signal(object)  # a Folder

    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.chain: list[space.Folder] = []
        self.root_title = ""   # "Windows (C:)" for a drive
        self.parts: list[tuple[QRectF, str, space.Folder | None, bool]] = []  # (where, text, folder, last)
        self.hover = -1
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setFixedHeight(34)

    def set_folder(self, folder: space.Folder | None):
        self.chain = []
        f = folder
        while f is not None:
            self.chain.insert(0, f)
            f = f.parent
        if folder is not None:
            self.setToolTip(str(folder.path))
            self.setAccessibleName(str(folder.path))
        self.hover = -1
        self._lay_out()
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._lay_out()

    def _fonts(self) -> tuple[QFont, QFont]:
        plain = QFont(self.font())
        last = QFont(self.font())
        last.setPointSizeF(self.font().pointSizeF() + 2.5)
        last.setWeight(QFont.Weight.DemiBold)
        return plain, last

    def _name(self, f: space.Folder) -> str:
        if f.parent is None:  # (marks that keep a Latin name in order come back when it's drawn)
            return (self.root_title or f.name.rstrip("\\/") or f.name).strip("\u202a\u202c")
        return f.name

    def _lay_out(self):
        self.parts = []
        if not self.chain:
            return
        plain, last = self._fonts()
        fm, lfm = QFontMetrics(plain), QFontMetrics(last)
        gap = 22  # (room for the › between two names)
        items = [(self._name(f), f) for f in self.chain]
        shown = list(range(len(items)))

        def width_of(idx):
            w = 0
            for n, i in enumerate(idx):
                text = items[i][0] if i >= 0 else "…"
                w += (lfm if i == len(items) - 1 else fm).horizontalAdvance(text) + 12 + (gap if n else 0)
            return w
        while width_of(shown) > self.width():  # fold the middle into "…"
            if len(shown) > 2 and shown[1] != -1:
                shown[1] = -1
            elif len(shown) > 3:
                shown.pop(2)
            else:
                break
        rtl = self.layoutDirection() == Qt.RightToLeft
        x = 0.0
        avail = float(self.width())
        for n, i in enumerate(shown):
            is_last = i == len(items) - 1
            m = lfm if is_last else fm
            text = items[i][0] if i >= 0 else "…"
            folder = items[i][1] if i >= 0 else items[shown[n + 1] - 1][1] if n + 1 < len(shown) else None
            if n:
                x += gap
            full = m.horizontalAdvance(text) + 12
            w = min(full, max(30.0, avail - x))
            if w < full:
                text = m.elidedText(text, Qt.ElideMiddle, int(w - 12))
            r = QRectF(avail - x - w if rtl else x, 0, w, self.height())
            self.parts.append((r, text, None if is_last else folder, is_last))
            x += w

    def paintEvent(self, e):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        plain, last = self._fonts()
        rtl = self.layoutDirection() == Qt.RightToLeft
        for n, (r, text, folder, is_last) in enumerate(self.parts):
            if n:  # the › before it
                prev = self.parts[n - 1][0]
                mid = (prev.left() + r.right()) / 2 if rtl else (prev.right() + r.left()) / 2
                icons.paint(p, QRectF(mid - 6, r.center().y() - 6, 12, 12), "chevron_left" if rtl else "chevron_right",
                            t.muted)
            if n == self.hover and folder is not None:
                p.setPen(Qt.NoPen)
                p.setBrush(t.hover)
                p.drawRoundedRect(r.adjusted(0, 3, 0, -3), 7, 7)
            p.setFont(last if is_last else plain)
            p.setPen(t.text if is_last or n == self.hover else t.muted)
            p.drawText(r, Qt.AlignCenter, ltr(text))
        p.end()

    def _at(self, pos) -> int:
        for n, (r, _t, folder, _l) in enumerate(self.parts):
            if folder is not None and r.contains(pos):
                return n
        return -1

    def mouseMoveEvent(self, e):
        n = self._at(e.position())
        if n != self.hover:
            self.hover = n
            self.setCursor(Qt.PointingHandCursor if n >= 0 else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        n = self._at(e.position())
        if n >= 0 and e.button() == Qt.LeftButton:
            self.picked.emit(self.parts[n][2])


class Box:
    __slots__ = ("rect", "name", "size", "kind", "level", "folder", "parent", "hue")

    def __init__(self, rect, name, size, kind, level, folder, parent, hue=None):
        self.rect, self.name, self.size, self.kind = rect, name, size, kind   # kind: folder, file or rest
        self.level, self.folder, self.parent = level, folder, parent           # folder: the Folder (folders)
        self.hue = hue                                                          # the hue of its top folder


class Treemap(QWidget):
    """Boxes as big as what they hold. Folders show what's inside them (a few levels down while
    there's room); click a folder to go in. Each folder at the top has a colour of its own; files
    are coloured by kind (videos red, pictures green…)."""
    opened = Signal(object)            # a Folder
    menu = Signal(object, object)      # (path, Folder or None), global position
    DEPTH = 3
    HEAD = 20                          # a folder's name strip

    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.folder: space.Folder | None = None
        self.boxes: list[Box] = []
        self.hover = -1
        self.focus_kind = ""           # one kind picked below the map: the other files step back
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
        self._nest(f, QRectF(self.rect()), 0, None)

    def _nest(self, folder: space.Folder, r: QRectF, level: int, hue):
        entries = self._entries(folder, 120 if level == 0 else 48)
        rects = space.squarify([e[0] for e in entries], r.x(), r.y(), r.width(), r.height())
        hues = 0
        for (size, name, what), (x, y, w, h) in zip(entries, rects):
            if w < 2 or h < 2:
                continue
            sub = what if isinstance(what, space.Folder) else None
            kind = "folder" if sub is not None else "rest" if what == "rest" else "file"
            own = hue
            if own is None and sub is not None:
                own = FOLDER_HUES[hues % len(FOLDER_HUES)]
                hues += 1
            self.boxes.append(Box(QRectF(x, y, w, h), name, size, kind, level, sub, folder, own))
            if sub is not None and level + 1 < self.DEPTH and sub.size > 0 and w > 40 and h > 38:
                head = self.HEAD if w > 60 and h > 44 else 4
                self._nest(sub, QRectF(x, y, w, h).adjusted(4, head, -4, -4), level + 1, own)

    def _color(self, b: Box) -> QColor:
        t = self.theme
        base = t.tokens.surface
        if b.kind == "folder":
            return mix(base, QColor(b.hue), (0.22 if t.dark else 0.19) + 0.1 * b.level)
        if b.kind == "rest":
            return mix(base, t.muted, 0.14)
        kind = space.kind_of(b.name)
        color = mix(QColor(KIND_COLORS[kind]), base, 0.12 if t.dark else 0.0)
        if self.focus_kind and kind != self.focus_kind:
            color = mix(color, base, 0.78)
        return color

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
        head = QFont(small)
        head.setWeight(QFont.Weight.DemiBold)
        name_font = QFont(self.font())
        name_font.setWeight(QFont.Weight.DemiBold)
        fm, sfm, hfm = QFontMetrics(name_font), QFontMetrics(small), QFontMetrics(head)
        rtl = self.layoutDirection() == Qt.RightToLeft
        lead = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignTop | Qt.AlignAbsolute
        trail = (Qt.AlignLeft if rtl else Qt.AlignRight) | Qt.AlignTop | Qt.AlignAbsolute
        for i, b in enumerate(self.boxes):
            color = self._color(b)
            box = b.rect.adjusted(1, 1, -1, -1)  # (a hairline of the page between boxes)
            if box.width() < 1 or box.height() < 1:
                continue
            radius = 6 if b.level == 0 else 4
            p.setPen(Qt.NoPen)
            p.setBrush(color.lighter(112) if i == self.hover else color)
            p.drawRoundedRect(box, radius, radius)
            if b.kind == "rest":  # many small files: a quiet hatch, so the eye goes to the big ones first
                p.setBrush(QBrush(alpha(t.muted, 38), Qt.BDiagPattern))
                p.drawRoundedRect(box, radius, radius)
            ink = on_color(color)
            soft = alpha(ink, 170)
            if b.kind == "folder" and box.width() > 60 and box.height() > 44 and b.level + 1 < self.DEPTH:
                # a folder showing what's inside it: its name (and size) in the strip at the top
                strip = QRectF(box.left() + 6, box.top() + 2, box.width() - 12, self.HEAD - 4)
                size_text = human_size(b.size)
                size_w = sfm.horizontalAdvance(size_text) + 8
                p.setFont(head)
                p.setPen(ink)
                room = strip.width() - (size_w if strip.width() > size_w + 50 else 0)
                p.drawText(QRectF(strip.left() + (strip.width() - room if rtl else 0), strip.top(), room, strip.height()),
                           lead | Qt.AlignVCenter, hfm.elidedText(b.name, Qt.ElideMiddle, int(room)))
                if strip.width() > size_w + 50:
                    p.setFont(small)
                    p.setPen(soft)
                    p.drawText(strip, trail | Qt.AlignVCenter, size_text)
                continue
            font, metrics = (name_font, fm) if b.level == 0 else (head, hfm)
            inner = box.adjusted(7, 4, -7, -2)
            name = metrics.elidedText(b.name, Qt.ElideMiddle, int(inner.width()))
            if box.width() > 48 and box.height() > metrics.height() + 4 and (name == b.name or len(name) >= 6):
                p.setPen(ink)
                p.setFont(font)
                p.drawText(inner, lead, name)
                if box.height() > metrics.height() + sfm.height() + 8:
                    p.setFont(small)
                    p.setPen(soft)
                    p.drawText(inner.adjusted(0, metrics.height() + 1, 0, 0), lead,
                               sfm.elidedText(human_size(b.size), Qt.ElideRight, int(inner.width())))
        if 0 <= self.hover < len(self.boxes):  # what's under the mouse: a ring of the accent
            b = self.boxes[self.hover]
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(t.accent, 2))
            r = 6 if b.level == 0 else 4
            p.drawRoundedRect(b.rect.adjusted(1.5, 1.5, -1.5, -1.5), r, r)
        p.end()

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
    """One bar split by kind of file (videos, pictures, …). Click a part to see that kind's biggest files."""
    picked = Signal(str)

    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.kinds: dict = {}
        self.focus = ""
        self.hover = ""
        self.parts: list[tuple[QRectF, str]] = []
        self.setFixedHeight(12)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set_kinds(self, kinds: dict):
        self.kinds = {k: v for k, v in kinds.items() if v > 0}
        self._lay_out()
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._lay_out()

    def _lay_out(self):
        self.parts = []
        total = sum(self.kinds.values())
        if not total:
            return
        rtl = self.layoutDirection() == Qt.RightToLeft
        gap = 3.0
        shown = [(k, v) for k, v in sorted(self.kinds.items(), key=lambda kv: kv[1], reverse=True)]
        room = self.width() - gap * (len(shown) - 1)
        x = 0.0
        for kind, size in shown:
            w = max(2.0, room * size / total)
            self.parts.append((QRectF(self.width() - x - w if rtl else x, 1, w, self.height() - 2), kind))
            x += w + gap

    def paintEvent(self, e):
        if not self.parts:
            return
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        for r, kind in self.parts:
            color = QColor(KIND_COLORS[kind])
            if self.focus and kind != self.focus:
                color = mix(color, t.tokens.canvas, 0.7)
            elif kind == self.hover:
                color = color.lighter(115)
            p.setBrush(color)
            p.drawRoundedRect(r, min(5.0, r.width() / 2), 5)
        p.end()

    def _at(self, pos) -> str:
        return next((k for r, k in self.parts if r.left() - 2 <= pos.x() <= r.right() + 2), "")

    def mouseMoveEvent(self, e):
        kind = self._at(e.position())
        if kind != self.hover:
            self.hover = kind
            self.update()
        if kind:
            QToolTip.showText(e.globalPosition().toPoint(), f"{tr(space.KINDS[kind])}  ·  {human_size(self.kinds[kind])}",
                              self)

    def leaveEvent(self, e):
        self.hover = ""
        self.update()

    def mouseReleaseEvent(self, e):
        kind = self._at(e.position())
        if kind and e.button() == Qt.LeftButton:
            self.picked.emit(kind)


class KindChip(QPushButton):
    """A kind of file under the map: its colour, its name and how much of it there is. Click it to see
    its biggest files."""

    def __init__(self, theme, kind: str, size: int):
        super().__init__()
        self.theme, self.kind, self.size = theme, kind, size
        self.name = tr(space.KINDS[kind])
        self.amount = human_size(size)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        self.setAccessibleName(f"{self.name} {self.amount}")
        self.setToolTip(tr("Click a kind to see its biggest files"))

    def sizeHint(self):
        bold = QFont(self.font())
        bold.setWeight(QFont.Weight.DemiBold)
        w = QFontMetrics(bold).horizontalAdvance(self.name) + self.fontMetrics().horizontalAdvance(self.amount)
        return QSize(w + 44, max(28, self.fontMetrics().height() + 12))

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, e):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.isChecked():
            p.setPen(QPen(QColor(KIND_COLORS[self.kind]), 1))
            p.setBrush(mix(t.tokens.surface, QColor(KIND_COLORS[self.kind]), 0.16 if t.dark else 0.1))
        else:
            p.setPen(QPen(t.line_strong if self.underMouse() else t.border, 1))
            p.setBrush(t.hover if self.underMouse() else t.tokens.surface)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        rtl = self.layoutDirection() == Qt.RightToLeft
        dot = QRectF(r.right() - 20 if rtl else r.left() + 12, r.center().y() - 4, 8, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(KIND_COLORS[self.kind]))
        p.drawEllipse(dot)
        bold = QFont(self.font())
        bold.setWeight(QFont.Weight.DemiBold)
        name_w = QFontMetrics(bold).horizontalAdvance(self.name)
        x = dot.left() - 7 - name_w if rtl else dot.right() + 7
        p.setFont(bold)
        p.setPen(t.text)
        p.drawText(QRectF(x, r.top(), name_w + 1, r.height()), Qt.AlignVCenter | Qt.AlignLeft | Qt.AlignAbsolute, self.name)
        amount_w = self.fontMetrics().horizontalAdvance(self.amount)
        x = x - 6 - amount_w if rtl else x + name_w + 6
        p.setFont(self.font())
        p.setPen(t.muted)
        p.drawText(QRectF(x, r.top(), amount_w + 1, r.height()), Qt.AlignVCenter | Qt.AlignLeft | Qt.AlignAbsolute,
                   self.amount)
        p.end()


class ShareDelegate(QStyledItemDelegate):
    """A list row with a thin bar under its name: its size next to the biggest row's."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme

    def paint(self, p, option, index):
        super().paint(p, option, index)
        share = index.data(SHARE)
        if not share:
            return
        r = QRectF(option.rect)
        rtl = option.direction == Qt.RightToLeft
        room = max(0.0, r.width() - 16)
        w = max(3.0, room * min(1.0, share))
        bar = QRectF(r.right() - 8 - w if rtl else r.left() + 8, r.bottom() - 3.5, w, 2.5)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(alpha(QColor(index.data(TINT) or self.theme.accent), 150))
        p.drawRoundedRect(bar, 1.5, 1.5)
        p.restore()


class Spinner(QWidget):
    """A ring that goes round while Space looks through a drive, with how much it found so far inside."""

    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.angle = 0
        self.text = ""
        self.sub = ""
        self.setFixedSize(150, 150)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._turn)

    def set_text(self, text: str, sub: str = ""):
        self.text, self.sub = text, sub
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        self.timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.timer.stop()  # (nothing goes round while no one sees it)

    def _turn(self):
        self.angle = (self.angle + 5) % 360
        self.update()

    def paintEvent(self, e):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        p.setPen(Qt.NoPen)
        p.setBrush(mix(t.tokens.surface, t.accent, 0.07 if t.dark else 0.05))
        p.drawEllipse(r)
        _ring(p, r, None, t.accent, mix(t.tokens.surface, t.text, 0.08), 7)
        p.setPen(QPen(t.accent, 7, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, -self.angle * 16, 100 * 16)
        big = QFont(self.font())
        big.setPointSizeF(self.font().pointSizeF() + 5)
        big.setWeight(QFont.Weight.DemiBold)
        p.setFont(big)
        p.setPen(t.text)
        fm = QFontMetrics(big)
        p.drawText(QRectF(r.left(), r.center().y() - fm.height() + 4, r.width(), fm.height()), Qt.AlignCenter,
                   fm.elidedText(self.text, Qt.ElideRight, int(r.width() - 24)))
        p.setFont(self.font())
        p.setPen(t.muted)
        p.drawText(QRectF(r.left() + 14, r.center().y() + 6, r.width() - 28, self.fontMetrics().height()), Qt.AlignCenter,
                   self.fontMetrics().elidedText(self.sub, Qt.ElideRight, int(r.width() - 28)))
        p.end()


# ----------------------------------------------------------------------------- the drives

class DriveCard(QPushButton):
    """A drive to pick: a ring for how full it is (amber, then red, as it fills), its name and what's
    free. With no drive, it's the "Look at a folder" card."""

    def __init__(self, theme, drive: space.Drive | None = None, title: str = "", line: str = "",
                 seen: float | None = None):
        super().__init__()
        self.theme, self.drive = theme, drive
        self.seen = ""
        if drive is not None:
            self.title = _drive_title(drive)
            self.line = tr("{free} free of {total}", free=human_size(drive.free), total=human_size(drive.total))
            if seen is not None:  # its map is kept: opening it again is instant
                self.seen = tr("mapped {when}", when=time_ago(seen))
            self.fill = drive.used / drive.total if drive.total else 0.0
        else:
            self.title, self.line, self.fill = title, line, None
        self.setAccessibleName(self.title)
        self.setToolTip("\n".join(x for x in (self.title, self.line, self.seen) if x))
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(98)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_Hover)
        self.setFocusPolicy(Qt.StrongFocus)

    def sizeHint(self):
        return QSize(280, self.minimumHeight())

    def paintEvent(self, e):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rtl = self.layoutDirection() == Qt.RightToLeft
        hover = self.underMouse()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.drive is None:  # "Look at a folder": a quieter, dashed card
            p.setPen(QPen(t.accent if hover else t.line_strong or t.border, 1, Qt.DashLine))
            p.setBrush(t.hover if hover else Qt.NoBrush)
        else:
            p.setPen(QPen(t.accent if self.hasFocus() else (t.line_strong if hover else t.border) or t.border, 1))
            p.setBrush(t.hover if hover else t.panel)
            if self.isDown():
                p.setBrush(mix(t.panel, t.text, 0.08))
        p.drawRoundedRect(r, 14, 14)
        pad = 18
        size = 58.0
        ring = QRectF(r.right() - pad - size if rtl else r.left() + pad, r.center().y() - size / 2, size, size)
        track = mix(t.panel, t.text, 0.1 if t.dark else 0.08)
        if self.fill is None:
            p.setPen(Qt.NoPen)
            p.setBrush(mix(t.panel, t.text, 0.08))
            p.drawEllipse(ring.adjusted(3, 3, -3, -3))
            icons.paint(p, ring.adjusted(17, 17, -17, -17), "folder_search", t.text)
        else:
            color = _fill_color(t, self.fill)
            _ring(p, ring.adjusted(3, 3, -3, -3), self.fill, color, track, 6)
            pct = QFont(self.font())
            pct.setWeight(QFont.Weight.DemiBold)
            pct.setPointSizeF(self.font().pointSizeF() + 0.5)
            p.setFont(pct)
            p.setPen(t.text)
            p.drawText(ring, Qt.AlignCenter, ltr(f"{round(self.fill * 100)}%"))
        chevron = 16.0
        x0 = ring.right() + 16 if not rtl else r.left() + pad + chevron + 8
        x1 = r.right() - pad - chevron - 8 if not rtl else ring.left() - 16
        side = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute
        title_font = QFont(self.font())
        title_font.setPointSizeF(self.font().pointSizeF() + 1.5)
        title_font.setWeight(QFont.Weight.DemiBold)
        fm, rfm = QFontMetrics(title_font), QFontMetrics(self.font())
        lines = 2 + (1 if self.seen else 0) + (1 if self.fill is not None and self.fill >= 0.9 and not self.seen else 0)
        block = fm.height() + (lines - 1) * (rfm.height() + 2)
        y = r.center().y() - block / 2
        p.setFont(title_font)
        p.setPen(t.text)
        p.drawText(QRectF(x0, y, x1 - x0, fm.height()), side | Qt.AlignVCenter,
                   fm.elidedText(self.title, Qt.ElideRight, int(x1 - x0)))
        y += fm.height() + 2
        p.setFont(self.font())
        p.setPen(t.muted)
        p.drawText(QRectF(x0, y, x1 - x0, rfm.height()), side | Qt.AlignVCenter,
                   rfm.elidedText(self.line, Qt.ElideRight, int(x1 - x0)))
        y += rfm.height() + 2
        if self.fill is not None and self.fill >= 0.9 and not self.seen:
            p.setPen(_fill_color(t, self.fill))
            p.drawText(QRectF(x0, y, x1 - x0, rfm.height()), side | Qt.AlignVCenter,
                       rfm.elidedText(tr("Almost full"), Qt.ElideRight, int(x1 - x0)))
        elif self.seen:
            p.setPen(t.tokens.subtle)
            p.drawText(QRectF(x0, y, x1 - x0, rfm.height()), side | Qt.AlignVCenter,
                       rfm.elidedText(self.seen, Qt.ElideRight, int(x1 - x0)))
        arrow = QRectF(r.left() + pad if rtl else r.right() - pad - chevron, r.center().y() - chevron / 2, chevron, chevron)
        icons.paint(p, arrow, "chevron_left" if rtl else "chevron_right", t.accent if hover else t.tokens.subtle)
        p.end()


def _drive_title(drive: space.Drive) -> str:
    """"Windows (C:)", "USB drive (E:)"."""
    label = drive.label or (tr("USB drive") if drive.removable else tr("Local disk"))
    return ltr(f"{label} ({drive.path.rstrip(chr(92))})")


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
        # the running job's stop, set when the page goes (tool switched off, window made again): a whole
        # drive's look or the duplicates' reading would otherwise go on to the end for nothing
        self._live = [self.cancel]
        self.destroyed.connect(lambda *_a, live=self._live: live[0].set())
        self._recycling = None    # (the Recycle Bin job of the map, kept while it runs)
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
        # the tool's banner steps aside while a map is open (the map needs the room)
        self.banner = header.itemAt(0).widget() if header.count() else None
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        strip = QFrame()
        strip.setObjectName("segmented")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.section_buttons = QButtonGroup(self)
        for key, text in SECTIONS:
            b = QPushButton(" " + tr(text))  # (a little room after the icon)
            b.setObjectName("segment")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(key == "map")
            b.setIconSize(QSize(16, 16))
            b.clicked.connect(lambda _=False, k=key: self.show_section(k))
            self.section_buttons.addButton(b)
            sl.addWidget(b)
        tabs.addWidget(strip)
        tabs.addStretch()
        lay.addLayout(tabs)
        self.sections = QStackedWidget()
        self.sections.addWidget(self._map_section())
        self.sections.addWidget(self._clean_section())
        self.sections.addWidget(self._dupes_section())
        lay.addWidget(self.sections, 1)
        self.section = "map"
        self._paint_backs()

    def _new_cancel(self) -> threading.Event:
        self.cancel = self._live[0] = threading.Event()
        return self.cancel

    def _junk_if_waiting(self):
        """Clean up was opened while another job ran: what can go is looked for now."""
        if self.section == "clean" and not self.junk and self.job is None:
            self.find_junk()

    def show_section(self, key: str):
        self.section = key
        self.sections.setCurrentIndex([k for k, _ in SECTIONS].index(key))
        for b, (k, _t) in zip(self.section_buttons.buttons(), SECTIONS):
            b.setChecked(k == key)
        self._show_banner()
        if key == "clean" and not self.junk and self.job is None:
            self.find_junk()

    def _show_banner(self):
        if self.banner is not None:
            self.banner.setVisible(self.section != "map" or self.drives_box.isVisible())

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
        dl.setContentsMargins(0, 2, 0, 0)
        dl.setSpacing(10)
        dl.addWidget(_label(tr("Pick a drive to see what's filling it."), "muted"))
        cards = QWidget()  # (scrolls when there are more drives than fit, or the window is small)
        cards.setObjectName("scrollContent")
        cl = QVBoxLayout(cards)
        cl.setContentsMargins(0, 0, 0, 0)
        self.drive_rows = QGridLayout()
        self.drive_rows.setHorizontalSpacing(12)
        self.drive_rows.setVerticalSpacing(12)
        self.drive_rows.setColumnStretch(0, 1)
        self.drive_rows.setColumnStretch(1, 1)
        cl.addLayout(self.drive_rows)
        cl.addStretch()
        dl.addWidget(_scroller(cards), 1)
        lay.addWidget(self.drives_box, 1)
        # looking (with Stop): a ring that goes round, with what it found so far
        self.busy_box = QWidget()
        bl = QVBoxLayout(self.busy_box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(self._back_button())
        top.addStretch()
        bl.addLayout(top)
        bl.addStretch(2)
        self.spinner = Spinner(self.c.theme)
        bl.addWidget(self.spinner, 0, Qt.AlignHCenter)
        bl.addSpacing(6)
        self.busy_title = _label("", "h2")
        self.busy_title.setAlignment(Qt.AlignCenter)
        bl.addWidget(self.busy_title)
        self.busy_line = ElidedLabel()
        self.busy_line.setObjectName("muted")
        self.busy_line.setAlignment(Qt.AlignCenter)
        bl.addWidget(self.busy_line)
        bl.addSpacing(6)
        stop = _button(tr("Stop"), None, self._stop)
        stop.setMinimumWidth(120)
        bl.addWidget(stop, 0, Qt.AlignHCenter)
        bl.addStretch(3)
        lay.addWidget(self.busy_box, 1)
        self.busy_box.hide()
        # the map
        self.map_box = QWidget()
        ml = QVBoxLayout(self.map_box)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(8)
        nav = QHBoxLayout()
        nav.setSpacing(6)
        self.back_btn = self._back_button()
        self.where = Crumbs(self.c.theme)
        self.where.picked.connect(self.open_folder)
        self.again_btn = _button(tr("Look again"), "ghost", self._rescan)
        self.again_btn.setToolTip(tr("Look through it again, to see what changed since"))
        self.drives_btn = _button(tr("Other drives"), "ghost", self._back_to_drives)
        self._icon_buttons = [(self.again_btn, "arrow_clockwise"), (self.drives_btn, "hard_drive")]
        nav.addWidget(self.back_btn)
        nav.addWidget(self.where, 1)
        nav.addWidget(self.again_btn)
        nav.addWidget(self.drives_btn)
        ml.addLayout(nav)
        self.summary = _label("", "muted", wrap=True)
        self.summary.setTextFormat(Qt.RichText)
        ml.addWidget(self.summary)
        body = QHBoxLayout()
        body.setSpacing(12)
        self.treemap = Treemap(self.c.theme)
        self.treemap.opened.connect(self.open_folder)
        self.treemap.menu.connect(lambda what, pos: self._menu(what[0], what[1], pos))
        body.addWidget(self.treemap, 3)
        side = QVBoxLayout()
        side.setSpacing(8)
        self.list_mode = "here"
        self.big_kind = ""             # the biggest files of one kind only ("video"…), from the kinds below
        self.modes = ["here", "big", "old", "grew"]
        self.mode_box = Dropdown()
        self.mode_box.setAccessibleName(tr("Show"))
        self.mode_box.currentIndexChanged.connect(self._mode_picked)
        side.addWidget(self.mode_box)
        self.items = QTreeWidget()
        self.items.setObjectName("history")
        self.items.setHeaderLabels([tr("Name"), tr("Size")])
        self.items.headerItem().setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        self.items.setRootIsDecorated(False)
        self.items.setUniformRowHeights(True)
        self.items.setItemDelegateForColumn(0, ShareDelegate(self.c.theme, self.items))
        self.items.setStyleSheet("QTreeView::item { padding: 6px 2px 9px 2px; }")  # (room for the bars)
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
        ml.addSpacing(2)
        self.kinds_bar = KindsBar(self.c.theme)
        self.kinds_bar.picked.connect(self._show_kind)
        ml.addWidget(self.kinds_bar)
        self.kinds_holder = QWidget()
        self.kinds_row = FlowLayout(self.kinds_holder, 6)
        self.kind_chips: list[KindChip] = []
        ml.addWidget(self.kinds_holder)
        lay.addWidget(self.map_box, 1)
        self.map_box.hide()
        self._fill_modes()
        self._fill_drives()
        return w

    def _fill_drives(self):
        _clear_layout(self.drive_rows)
        cards = []
        self.drives = space.drives()
        for d in self.drives:
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
        return b

    def _paint_backs(self):
        t = self.c.theme
        rtl = self.layoutDirection() == Qt.RightToLeft or QApplication.layoutDirection() == Qt.RightToLeft
        for b in getattr(self, "_backs", []):
            b.setIcon(icons.icon("arrow_right" if rtl else "arrow_left", t.text))
        for b, name in getattr(self, "_icon_buttons", []):
            b.setIcon(icons.icon(name, t.text, disabled=t.muted))
        for b, (key, _t) in zip(self.section_buttons.buttons() if hasattr(self, "section_buttons") else [], SECTIONS):
            b.setIcon(icons.icon(SECTION_ICONS[key], t.muted))

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
        self._new_cancel()
        self.root = root
        self.drives_box.hide()
        self.map_box.hide()
        self.busy_box.show()
        self._show_banner()
        self.busy_title.setText(tr("Looking through {name}…", name=ltr(str(root))))
        self.busy_line.set_full(tr("This takes a moment on a big drive."))
        self.spinner.set_text("", "")
        cancel = self.cancel
        def work(progress):
            result = space.scan(root, cancel, lambda *a: progress(a))
            space.remember(result)
            return result
        self.job = _work(work, self._scan_progress, self._scanned)

    def _scan_progress(self, args):
        if not _alive(self):
            return
        files, size, folder = args if isinstance(args, tuple) else (0, 0, "")
        self.spinner.set_text(human_size(size), tr("{count} files", count=f"{files:,}"))
        self.busy_line.set_full(str(folder))

    def _stop(self):
        self.cancel.set()

    def _scanned(self, result):
        if not _alive(self):
            return
        self.job = None
        self._junk_if_waiting()
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
        self._show_banner()
        self._forget_timer.stop()
        if self.list_mode == "grew" and not result.since:
            self.list_mode = "here"
        self._fill_modes()
        root = _key(result.root.path)
        self.where.root_title = next((_drive_title(d) for d in self.drives if _key(Path(d.path)) == root), "")
        self.open_folder(folder)
        self.kinds_bar.set_kinds(result.kinds)
        self._fill_kinds()

    def _fill_kinds(self):
        """The kinds of file under the map, biggest first (the very small ones left out)."""
        for chip in self.kind_chips:
            self.kinds_row.removeWidget(chip)
            chip.hide()
            chip.deleteLater()
        self.kind_chips = []
        result = self.scan
        if result is None:
            return
        total = sum(result.kinds.values()) or 1
        for k, v in sorted(result.kinds.items(), key=lambda kv: -kv[1]):
            if v <= total * 0.005:
                continue
            chip = KindChip(self.c.theme, k, v)
            chip.clicked.connect(lambda _=False, kind=k: self._show_kind(kind))
            self.kinds_row.addWidget(chip)
            self.kind_chips.append(chip)
        self._mark_kind()

    def _mark_kind(self):
        """The kind picked (if any): its chip stands out, and on the map the other files step back."""
        kind = self.big_kind if self.list_mode == "big" else ""
        for chip in self.kind_chips:
            chip.setChecked(chip.kind == kind)
        self.kinds_bar.focus = self.treemap.focus_kind = kind
        self.kinds_bar.update()
        self.treemap.update()

    def _all_biggest(self):
        self.big_kind = ""
        self._list_mode("big")

    def _show_kind(self, kind: str):
        """The biggest videos (or pictures, music…) of the whole scan; clicked again, all kinds."""
        same = self.list_mode == "big" and self.big_kind == kind
        self.big_kind = "" if same else kind
        self._list_mode("big")

    def _summary(self):
        s, f = self.scan, self.here
        if s is None or f is None:
            return
        size = f"<b style='color:{self.c.theme.text.name()}'>{html.escape(human_size(f.size))}</b>"
        bits = [tr("{size} in {count} files", size=size, count=f"{f.files:,}")]
        if s.cancelled:
            bits.append(html.escape(tr("stopped before the end")))
        if f is s.root and s.denied:
            bits.append(html.escape(tr("{count} folders Windows keeps private were skipped", count=s.denied)))
        if f is s.root and s.cloud:
            bits.append(html.escape(tr("{count} files only in the cloud take no space here", count=f"{s.cloud:,}")))
        kept = self.kept.get(_key(s.root.path))
        if kept is not None and kept[0] is s and time.time() - kept[2] >= 60:
            bits.append(html.escape(tr("mapped {when}", when=time_ago(kept[2]))))
        self.summary.setText("&nbsp;&nbsp;·&nbsp;&nbsp;".join(bits))

    def open_folder(self, folder: space.Folder):
        self.here = folder
        if self.scan is not None:
            kept = self.kept.get(_key(self.scan.root.path))
            if kept is not None and kept[0] is self.scan:
                kept[1] = folder  # (opening it again comes back here)
        self.where.set_folder(folder)
        self.treemap.show_folder(folder)
        self._summary()
        self._fill_list()

    def _up(self):
        if self.here is not None and self.here.parent is not None:
            self.open_folder(self.here.parent)

    def _rescan(self):
        if self.job is not None:  # (another job runs: the map stays as it is, rather than go blank)
            return
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
        self._show_banner()
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

    def _fill_modes(self):
        """What the list beside the map shows: this folder, the biggest files (of one kind), the ones
        unused for a year, what grew since the last look."""
        names = {"here": tr("In this folder"),
                 "big": tr("Biggest files") if not self.big_kind else
                 tr("Biggest: {kind}", kind=tr(space.KINDS[self.big_kind])),
                 "old": tr("Unused for a year")}
        since = self.scan.since if self.scan is not None else 0
        if since:
            names["grew"] = tr("Grew since {date}", date=time.strftime("%Y-%m-%d", time.localtime(since)))
        self.modes = [m for m in ("here", "big", "old", "grew") if m in names]
        self.mode_box.blockSignals(True)
        self.mode_box.clear()
        for m in self.modes:
            self.mode_box.addItem(names[m])
        self.mode_box.setItemData(self.modes.index("old"), tr("Big files nobody has opened or changed for over a year"),
                                  Qt.ToolTipRole)
        self.mode_box.setCurrentIndex(self.modes.index(self.list_mode) if self.list_mode in self.modes else 0)
        self.mode_box.blockSignals(False)

    def _mode_picked(self, i: int):
        if 0 <= i < len(self.modes):
            mode = self.modes[i]
            if mode == "big":
                self.big_kind = ""
            self._list_mode(mode)

    def _list_mode(self, mode: str):
        if mode != "big":
            self.big_kind = ""
        self.list_mode = mode
        self._fill_modes()
        self._fill_list()
        self._mark_kind()

    def _fill_list(self):
        self.items.clear()
        if self.here is None or self.scan is None:
            return
        rows = []
        if self.list_mode == "big":
            biggest = self.scan.biggest_kind.get(self.big_kind, []) if self.big_kind else self.scan.biggest
            for size, path in biggest:
                rows.append((ltr(short_name(Path(path).name, 60)), size, Path(path), None, path))
        elif self.list_mode == "old":
            for size, path, used in self.scan.old:
                when = time.strftime("%Y-%m-%d", time.localtime(used))
                rows.append((ltr(short_name(Path(path).name, 60)), size, Path(path), None,
                             path + "\n" + tr("Last used {date}", date=when)))
        elif self.list_mode == "grew":
            for delta, path in self.scan.grew:
                p = Path(path)
                folder = self._folder_of(p)
                rows.append((ltr(short_name(p.name or str(p), 60)), delta, p, folder, path))
        else:
            for size, name, folder in self.here.entries()[:300]:
                rows.append((ltr(name), size,
                             folder.path if folder is not None else self.here.path / name, folder, None))
            if self.here.rest:
                rows.append((tr("{count} smaller files", count=self.here.rest_count), self.here.rest, None, None, None))
        folder_icon = icons.icon("folder", self.c.theme.muted)
        if not rows and self.list_mode in ("old", "grew"):
            empty = tr("Nothing big has gone unused for a year.") if self.list_mode == "old" else \
                tr("Nothing grew much since the last look.")
            it = QTreeWidgetItem([empty, ""])
            it.setFlags(Qt.NoItemFlags)
            self.items.addTopLevelItem(it)
        top = max((r[1] for r in rows), default=0) or 1
        folder_tint = mix(self.c.theme.muted, self.c.theme.accent, 0.5)
        for text, size, path, folder, tip in rows:
            it = QTreeWidgetItem([text, ("+" if self.list_mode == "grew" else "") + human_size(size)])
            if folder is not None:
                it.setIcon(0, folder_icon)
            it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            it.setData(0, Qt.UserRole, (path, folder))
            it.setData(0, SHARE, size / top)
            it.setData(0, TINT, folder_tint if folder is not None or path is None
                       else QColor(KIND_COLORS[space.kind_of(str(path))]))
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
        menu.addAction(tr("Copy the path"), lambda: QApplication.clipboard().setText(str(path)))
        menu.addAction(tr("Properties"), lambda: properties(path))
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
        if self._recycling is not None:  # (one at a time)
            return
        if not _confirm(self, tr("Move to the Recycle Bin?"),
                        tr("{name} ({size}) goes to the Recycle Bin, where you can put it back. The space is freed "
                           "when the Recycle Bin is emptied.", name=ltr(path.name), size=human_size(size)),
                        tr("Move to the Recycle Bin")):
            return
        scan = self.scan

        def done(gone):
            if not _alive(self):
                return
            self._recycling = None
            if isinstance(gone, Exception) or not gone:
                self.c.tell(tr("Couldn't move it"), tr("{name} is in use or Windows doesn't allow it.",
                                                        name=ltr(path.name)))
                return
            if self.scan is scan:  # (the same map is still open)
                self._forget(path, folder, size)
        # (a big folder takes a while to go: the window keeps answering meanwhile)
        self._recycling = _work(lambda _p: space.recycle([path]), lambda _: None, done, busy=True)

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
        def gone(b):
            return Path(b[1]) == path or str(b[1]).startswith(str(path) + os.sep)
        self.scan.biggest = [b for b in self.scan.biggest if not gone(b)]
        self.scan.old = [b for b in self.scan.old if not gone(b)]
        self.scan.biggest_kind = {k: [b for b in v if not gone(b)] for k, v in self.scan.biggest_kind.items()}
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
        lay.setSpacing(12)
        # how much can go, and the button that does it
        hero = GlowPanel(rings=False, boxed=True)
        hl = QHBoxLayout(hero)
        hl.setContentsMargins(20, 16, 20, 16)
        hl.setSpacing(16)
        hl.addWidget(IconTile("broom", 48), 0, Qt.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(2)
        self.clean_head = _label(tr("Can be freed"), "subtle")
        words.addWidget(self.clean_head)
        self.clean_total = _label("", "stat")
        words.addWidget(self.clean_total)
        words.addWidget(_label(tr("Things that are safe to remove. Nothing personal is touched without asking."),
                               "muted", wrap=True))
        hl.addLayout(words, 1)
        self.clean_btn = _button(tr("Clean up"), "primary", self._clean)
        self.clean_btn.setMinimumHeight(38)
        hl.addWidget(self.clean_btn, 0, Qt.AlignVCenter)
        lay.addWidget(hero)
        # what was found, one row each
        self.junk_box = Group()
        self.junk_rows = self.junk_box.lay
        inner = QWidget()
        inner.setObjectName("scrollContent")
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.addWidget(self.junk_box)
        il.addStretch()
        lay.addWidget(_scroller(inner), 1)
        row = QHBoxLayout()
        again = _button(tr("Look again"), "ghost", self.find_junk)
        self._icon_buttons.append((again, "arrow_clockwise"))
        row.addWidget(again)
        self.clean_note = _label("", "muted", wrap=True)
        row.addWidget(self.clean_note, 1)
        row.addWidget(_button(tr("More in Windows' Storage settings…"), "flat",
                              lambda: QDesktopServices.openUrl(QUrl("ms-settings:storagesense"))))
        lay.addLayout(row)
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
        self.junk_rows.addWidget(_note_row(tr("Looking for things that can go…")))
        self.clean_total.setText("…")
        self.clean_btn.setEnabled(False)
        downloads = None
        try:
            from .app import downloads_folder
            downloads = downloads_folder()
        except Exception:
            pass
        cancel = self._new_cancel()
        self.job = _work(lambda progress: space.find_junk(downloads, cancel), lambda _: None, self._junk_found)

    def _junk_found(self, result):
        if not _alive(self):
            return
        self.job = None
        self._clear_junk_rows()
        if isinstance(result, Exception):
            self.junk_rows.addWidget(_note_row(str(result)))
            self.clean_total.setText(human_size(0))
            return
        self.junk = [j for j in result if j.size > 0 or j.key == "bin"]
        if not self.junk:
            self.junk_rows.addWidget(_note_row(tr("Nothing to clean up. Nice and tidy.")))
        for j in self.junk:
            row = JunkRow(j)
            row.check.toggled.connect(self._junk_total)
            self.junk_rows.addWidget(row)
            self.junk_checks.append((row.check, j))
        self._junk_total()

    def _junk_total(self, *_):
        total = sum(j.size for cb, j in self.junk_checks if cb.isChecked())
        found = sum(j.size for j in self.junk if not j.busy)
        # (the big number is what the button frees, so the two never disagree)
        self.clean_total.setText(human_size(total))
        self.clean_head.setText(tr("Can be freed") if total >= found else
                                tr("Ticked below (all found: {size})", size=human_size(found)))
        self.clean_btn.setEnabled(total > 0 and self.job is None)
        self.clean_btn.setText(tr("Clean up {size}", size=human_size(total)) if total else tr("Clean up"))

    def _clean(self):
        chosen = [j for cb, j in self.junk_checks if cb.isChecked()]
        if not chosen or self.job is not None:
            return
        total = sum(j.size for j in chosen)
        admin = [j.key for j in chosen if j.admin]
        text = tr("{size} of temporary files and caches will be deleted. This can't be undone (old installers go "
                  "to the Recycle Bin).", size=human_size(total))
        if any(j.key == "bin" for j in chosen):
            text += "\n\n" + tr("The Recycle Bin is emptied too: what's in it is deleted for good.")
        if admin:
            text += "\n\n" + tr("Windows asks for an administrator for the Windows files.")
        if not _confirm(self, tr("Clean up?"), text, tr("Clean up")):
            return
        self.clean_btn.setEnabled(False)
        self.clean_note.setText(tr("Cleaning up…"))
        cancel = self._new_cancel()

        def work(progress):
            freed = sum(space.clean(j, cancel) for j in chosen if not j.admin)
            if admin and not cancel.is_set():
                from . import uninstall as un
                from .appspage import _app_command
                answer = un.run_elevated(_app_command(), "space-clean", {"keys": admin}, cancel)
                if answer is None:  # "No" at Windows' question: the rest was still cleaned
                    log.info("the administrator clean-up was turned down")
                else:
                    freed += int(answer.get("freed") or 0)
            return freed
        self.job = _work(work, lambda _: None, self._cleaned, busy=True)

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
        lay.setSpacing(12)
        top = QFrame()
        top.setObjectName("card")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(18, 16, 18, 16)
        tl.setSpacing(16)
        tl.addWidget(IconTile("copy", 48), 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(4)
        words.addWidget(_label(tr("Files that are exactly the same, byte for byte. Pick the copies to remove: they go "
                                  "to the Recycle Bin."), None, wrap=True))
        self.dupe_where = _label(tr("Your folders: {names}", names=", ".join(tr(n) for n in DUPE_FOLDERS)), "muted",
                                 wrap=True)
        words.addWidget(self.dupe_where)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.setContentsMargins(0, 6, 0, 0)
        self.dupe_btn = _button(tr("Find duplicates in your folders"), "primary", lambda: self.find_dupes(None))
        row.addWidget(self.dupe_btn)
        row.addWidget(_button(tr("In a folder…"), None, self._dupes_in_folder))
        row.addStretch()

        def empty_folders():  # (a window of its own: corefold/emptyview.py, loaded only when asked for)
            from .emptyview import EmptyFoldersDialog
            EmptyFoldersDialog.show_for(self)
        row.addWidget(_button(tr("Find empty folders…"), None, empty_folders))
        words.addLayout(row)
        tl.addLayout(words, 1)
        lay.addWidget(top)
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
        self.dupe_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.dupe_tree.customContextMenuRequested.connect(self._dupe_menu)
        lay.addWidget(self.dupe_tree, 1)
        self.dupe_tree.hide()
        row = QHBoxLayout()
        self.dupe_note = _label("", "muted", wrap=True)
        row.addWidget(self.dupe_note, 1)
        self.dupe_pick = _button(tr("Tick…"), None, None)  # (which copy stays, for every file at once)
        pick = QMenu(self.dupe_pick)
        round_menu(pick)
        pick.addAction(tr("Keep the oldest copy of each"), lambda: self._dupe_keep("oldest"))
        pick.addAction(tr("Keep the newest copy of each"), lambda: self._dupe_keep("newest"))
        pick.addAction(tr("Untick everything"), lambda: self._dupe_keep(None))
        self.dupe_pick.setMenu(pick)
        self.dupe_pick.hide()
        row.addWidget(self.dupe_pick)
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
        self.dupe_pick.hide()
        self.dupe_remove.hide()
        self.dupe_bar.setRange(0, 0)
        self.dupe_bar.show()
        self.dupe_btn.setEnabled(False)
        self.dupe_note.setText(tr("Looking through your files…"))
        cancel = self._new_cancel()
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
        self._junk_if_waiting()
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
        self.dupe_tree.setUpdatesEnabled(False)  # (all the rows go in at once, drawn once)
        heads = []
        for group in groups[:500]:
            size = group[0][1]
            files = sorted(group, key=lambda g: g[2])  # the oldest first: it's kept
            head = QTreeWidgetItem([tr("{count} copies of {name}", count=len(group), name=ltr(Path(files[0][0]).name)),
                                    "", human_size(size)])
            f = head.font(0)
            f.setWeight(QFont.Weight.DemiBold)
            head.setFont(0, f)
            heads.append(head)
            for i, (path, s, mtime) in enumerate(files):
                it = QTreeWidgetItem([ltr(path), date_text(mtime, True), ""])
                it.setData(0, Qt.UserRole, (path, s))
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Unchecked if i == 0 else Qt.Checked)
                it.setToolTip(0, path)
                head.addChild(it)
        self.dupe_tree.addTopLevelItems(heads)
        self.dupe_tree.expandAll()
        self.dupe_tree.setUpdatesEnabled(True)
        self.dupe_tree.blockSignals(False)
        self.dupe_tree.show()
        self.dupe_pick.show()
        self.dupe_remove.show()
        self._dupe_total()

    def _dupe_keep(self, which: str | None, keep=None):
        """Ticks every copy but one in each group: the oldest, the newest (each group's rows go oldest
        first), or this one (keep: a row). which None: nothing is ticked."""
        self.dupe_tree.blockSignals(True)
        heads = [keep.parent()] if keep is not None else \
            [self.dupe_tree.topLevelItem(i) for i in range(self.dupe_tree.topLevelItemCount())]
        for head in heads:
            n = head.childCount()
            for j in range(n):
                it = head.child(j)
                stays = it is keep if keep is not None else (which is None or j == (0 if which == "oldest" else n - 1))
                it.setCheckState(0, Qt.Unchecked if stays else Qt.Checked)
        self.dupe_tree.blockSignals(False)
        self._dupe_total()

    def _dupe_menu(self, pos):
        item = self.dupe_tree.itemAt(pos)
        data = item.data(0, Qt.UserRole) if item is not None else None
        if not data:
            return
        path = Path(data[0])
        menu = QMenu(self)
        round_menu(menu)
        menu.addAction(tr("Keep this one (tick the other copies)"), lambda: self._dupe_keep(None, item))
        menu.addSeparator()
        menu.addAction(tr("Open"), lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        menu.addAction(tr("Show in folder"), lambda: reveal_in_explorer(path))
        menu.addAction(tr("Copy the path"), lambda: QApplication.clipboard().setText(str(path)))
        menu.exec(self.dupe_tree.viewport().mapToGlobal(pos))
        menu.deleteLater()

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
        groups = self._ticked_groups()
        dupes = self.dupes
        self.dupe_remove.setEnabled(False)

        def work(_progress):
            # (only copies whose kept copy is still there and unchanged go; checked now, not when found)
            paths = space.copies_to_remove(groups)
            gone = space.recycle([Path(p) for p in paths]) if paths else 0
            # what's still there of each file (copies removed meanwhile in File Explorer go from the list too)
            return gone, [g for g in ([f for f in grp if os.path.lexists(f[0])] for grp in dupes) if len(g) > 1]
        # (many files take a while to go: the window keeps answering meanwhile)
        self.job = _work(work, lambda _: None, self._dupes_removed, busy=True)

    def _ticked_groups(self) -> list[tuple[list, list]]:
        """For each file with a copy ticked: (the ticked copies, the others), each as (path, size, modified)."""
        when = {path: mtime for group in self.dupes for path, _size, mtime in group}
        out = []
        for i in range(self.dupe_tree.topLevelItemCount()):
            head = self.dupe_tree.topLevelItem(i)
            ticked, kept = [], []
            for j in range(head.childCount()):
                it = head.child(j)
                path, size = it.data(0, Qt.UserRole)
                (ticked if it.checkState(0) == Qt.Checked else kept).append((path, size, when.get(path, 0.0)))
            if ticked:
                out.append((ticked, kept))
        return out

    def _dupes_removed(self, result):
        if not _alive(self):
            return
        self.job = None
        if isinstance(result, Exception):
            self.dupe_note.setText(str(result))
            self.dupe_remove.setEnabled(True)
            return
        gone, self.dupes = result
        self.forget_maps()
        self.c.say(tr("Copies removed"), tr("{count} copies went to the Recycle Bin.", count=gone), done=True)
        self.dupe_tree.clear()
        self._dupes_found(self.dupes)

    def stop_work(self):
        self.cancel.set()

    def apply_theme(self, theme):
        for part in (self.treemap, self.kinds_bar, self.where, self.spinner, *self.kind_chips,
                     *getattr(self, "drive_cards", [])):
            part.theme = theme
            part.update()
        self.items.itemDelegateForColumn(0).theme = theme
        self._paint_backs()
        self._summary()
        self._fill_list()


class IconTile(QWidget):
    """A rounded square washed with the accent, with a line icon on it."""

    def __init__(self, icon: str, size: int = 40):
        super().__init__()
        self.icon = icon
        self.setFixedSize(size, size)

    def paintEvent(self, e):
        from .atomic import tokens
        t = tokens.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(mix(t.surface, t.text, 0.08))
        p.drawRoundedRect(r, r.width() * 0.28, r.width() * 0.28)
        pad = r.width() * 0.26
        icons.paint(p, r.adjusted(pad, pad, -pad, -pad), self.icon, t.text)
        p.end()


class JunkRow(QWidget):
    """Something that can go: an icon, a tick, what it is, and how big. Clicking the row ticks it."""

    def __init__(self, j: space.Junk):
        super().__init__()
        self.junk = j
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(14)
        lay.addWidget(IconTile(JUNK_ICONS.get(j.key.split(":")[0], "delete"), 36), 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(2)
        title = tr(j.title, name=j.name) if j.name else tr(j.title)
        self.check = QCheckBox(title)
        self.check.setCursor(Qt.PointingHandCursor)
        self.check.setChecked(j.ticked and j.size > 0 and not j.busy)
        self.check.setEnabled(j.size > 0 and not j.busy)
        bold = QFont(self.check.font())
        bold.setWeight(QFont.Weight.DemiBold)
        self.check.setFont(bold)
        words.addWidget(self.check)
        detail = tr(j.detail)
        if j.busy:
            detail = tr("Close {name} first, then look again.", name=j.busy)
        note = _label(detail, "muted", wrap=True)
        note.setContentsMargins(28, 0, 0, 0)
        words.addWidget(note)
        lay.addLayout(words, 1)
        size = _label(human_size(j.size) if j.size else tr("Empty"), "h2" if j.size else "muted")
        lay.addWidget(size, 0, Qt.AlignTop)
        if self.check.isEnabled():
            self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.check.isEnabled() and self.rect().contains(e.position().toPoint()):
            self.check.toggle()


def _note_row(text: str) -> QWidget:
    """A quiet line inside a box of rows ("Looking…", "Nothing to clean up")."""
    lab = _label(text, "muted", wrap=True)
    lab.setContentsMargins(16, 14, 16, 14)
    return lab


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


def _scroller(inner: QWidget) -> QScrollArea:
    """inner, scrolling up and down when it doesn't fit (no frame, the page shows through)."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(inner)
    scroll.viewport().setAutoFillBackground(False)
    return scroll


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
