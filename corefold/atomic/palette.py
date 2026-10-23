"""The command palette: press a key, type a few letters, press Enter.

It covers its window with a dim layer and shows a search box with the best matches under it. The app
gives it a list of Command (a page to open, a setting to find, something to do).

    pal = CommandPalette(window, tr("Search pages, settings and actions"), tr("Nothing found"))
    pal.open([Command("Open the Store", "Pages", icon, lambda: ...), ...])
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QEvent, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QIcon, QKeyEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from . import motion, tokens
from .tokens import RADIUS, alpha, mix

CMD_ROLE = Qt.UserRole + 40


@dataclass
class Command:
    title: str
    where: str = ""                         # a group or a path, shown on the far side ("Settings › General")
    icon: QIcon | None = None
    run: Callable[[], None] | None = None
    keywords: str = ""                      # more words it can be found by (hidden)
    hint: str = ""                          # a shortcut to show, like "Ctrl + K"
    listed: bool = True                     # shown before anything is typed (False: only found by typing)
    score: float = field(default=0.0, repr=False)


def score(query: str, cmd: Command) -> float:
    """How well cmd matches what was typed (0 = not at all). Whole words beat the start of words, which
    beat letters in order."""
    q = query.casefold().strip()
    if not q:
        return 1.0
    title = cmd.title.casefold()
    hay = f"{title} {cmd.where.casefold()} {cmd.keywords.casefold()}"
    total = 0.0
    for part in q.split():
        if title.startswith(part):
            total += 10
        elif f" {part}" in f" {title}":
            total += 7
        elif part in title:
            total += 5
        elif f" {part}" in f" {hay}":
            total += 3
        elif part in hay:
            total += 2
        else:
            # letters in order, like "cnv" for Converter
            i = 0
            for ch in title:
                if i < len(part) and ch == part[i]:
                    i += 1
            if i < len(part) or len(part) < 2:
                return 0.0
            total += 1
    return total - len(title) / 200


class _Rows(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(0, 44 if index.data(CMD_ROLE) else 30)

    def paint(self, p: QPainter, option, index):
        t = tokens.current()
        cmd: Command | None = index.data(CMD_ROLE)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        rtl = option.direction == Qt.RightToLeft
        r = QRectF(option.rect).adjusted(6, 1, -6, -1)
        side = Qt.AlignRight if rtl else Qt.AlignLeft
        if cmd is None:  # a heading
            f = QFont(option.font)
            f.setPointSizeF(max(7.0, f.pointSizeF() - 1.5))
            f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            p.setPen(t.subtle)
            p.drawText(r.adjusted(12, 6, -12, 0), side | Qt.AlignVCenter | Qt.AlignAbsolute, index.data(Qt.DisplayRole))
            p.restore()
            return
        selected = bool(option.state & QStyle.State_Selected)
        if selected:
            p.setPen(Qt.NoPen)
            p.setBrush(t.accent_soft)
            p.drawRoundedRect(r, RADIUS["md"], RADIUS["md"])
            bar = QRectF(r.right() - 3 if rtl else r.left(), r.top() + 12, 3, r.height() - 24)
            p.setBrush(t.accent)
            p.drawRoundedRect(bar, 1.5, 1.5)
        x = 14
        if cmd.icon is not None:
            ib = QRect(int(r.right() - x - 20) if rtl else int(r.left() + x), int(r.center().y() - 10), 20, 20)
            cmd.icon.paint(p, ib)
            x += 32
        fm = QFontMetrics(option.font)
        where_w = min(int(r.width() * 0.4), fm.horizontalAdvance(cmd.where) + 8) if cmd.where else 0
        hint_w = fm.horizontalAdvance(cmd.hint) + 14 if cmd.hint else 0
        right = 14 + where_w + (hint_w + 8 if hint_w else 0)
        box = r.adjusted(right, 0, -x, 0) if rtl else r.adjusted(x, 0, -right, 0)
        p.setPen(t.text)
        f = QFont(option.font)
        if selected:
            f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.drawText(box, side | Qt.AlignVCenter | Qt.AlignAbsolute,
                   QFontMetrics(f).elidedText(cmd.title, Qt.ElideRight, int(box.width())))
        p.setFont(option.font)
        end = Qt.AlignLeft if rtl else Qt.AlignRight
        if cmd.where:
            wb = QRectF(r.left() + 14 + (hint_w + 8 if hint_w else 0), r.top(), where_w, r.height()) if rtl else \
                QRectF(r.right() - 14 - where_w - (hint_w + 8 if hint_w else 0), r.top(), where_w, r.height())
            p.setPen(t.muted)
            p.drawText(wb, end | Qt.AlignVCenter | Qt.AlignAbsolute,
                       fm.elidedText(cmd.where, Qt.ElideMiddle, int(wb.width())))
        if cmd.hint:
            hb = QRectF(r.left() + 14 if rtl else r.right() - 14 - hint_w, r.center().y() - 10, hint_w, 20)
            p.setPen(QPen(t.line_strong, 1))
            p.setBrush(t.field)
            p.drawRoundedRect(hb, 5, 5)
            p.setPen(t.muted)
            p.drawText(hb, Qt.AlignCenter, cmd.hint)
        p.restore()


class CommandPalette(QWidget):
    """Covers its window (a child of it, so it needs no window of its own) until Esc or a pick."""
    closed = Signal()

    def __init__(self, host: QWidget, placeholder: str, empty: str, heading: Callable[[str], str] | None = None):
        super().__init__(host)
        self.host = host
        self.heading = heading  # where → the heading it's listed under when nothing is typed
        self.commands: list[Command] = []
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.hide()
        self.panel = QFrame(self)
        self.panel.setObjectName("palettePanel")
        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        self.search = QLineEdit()
        self.search.setObjectName("paletteSearch")
        self.search.setPlaceholderText(placeholder)
        self.search.setClearButtonEnabled(False)
        self.search.textChanged.connect(self._filter)
        self.search.installEventFilter(self)
        lay.addWidget(self.search)
        line = QFrame()
        line.setObjectName("line")
        lay.addWidget(line)
        self.list = QListWidget()
        self.list.setObjectName("paletteList")
        self.list.setItemDelegate(_Rows(self.list))
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setMouseTracking(True)
        self.list.itemClicked.connect(self._run_item)
        lay.addWidget(self.list, 1)
        self.empty = QLabel(empty)
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(70)
        lay.addWidget(self.empty)
        host.installEventFilter(self)

    # ---- opening and closing

    def open(self, commands: list[Command], text: str = ""):
        self.commands = commands
        self.setGeometry(self.host.rect())
        self._place()
        self.search.setText(text)
        self._filter(text)
        self.show()
        self.raise_()
        self.search.setFocus(Qt.ShortcutFocusReason)
        if tokens.animations:
            motion.fade_in(self, tokens.MOTION["fast"])

    def close_palette(self):
        if self.isVisible():
            self.hide()
            self.closed.emit()

    def _place(self):
        w = min(640, self.width() - 48)
        h = min(460, self.height() - 120)
        self.panel.setGeometry((self.width() - w) // 2, max(24, min(96, self.height() // 7)), w, h)

    def eventFilter(self, obj, e):
        if obj is self.host and e.type() == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(self.host.rect())
            self._place()
        elif obj is self.search and e.type() == QEvent.Type.KeyPress:
            return self._key(e)
        return False

    def _key(self, e: QKeyEvent) -> bool:
        k = e.key()
        if k == Qt.Key_Escape:
            self.close_palette()
            return True
        if k in (Qt.Key_Down, Qt.Key_Up, Qt.Key_PageDown, Qt.Key_PageUp, Qt.Key_Tab, Qt.Key_Backtab):
            step = {Qt.Key_Down: 1, Qt.Key_Tab: 1, Qt.Key_Up: -1, Qt.Key_Backtab: -1,
                    Qt.Key_PageDown: 6, Qt.Key_PageUp: -6}[k]
            self._move(step)
            return True
        if k in (Qt.Key_Return, Qt.Key_Enter):
            item = self.list.currentItem()
            if item is not None:
                self._run_item(item)
            return True
        return False

    def _move(self, step: int):
        rows = [i for i in range(self.list.count()) if self.list.item(i).data(CMD_ROLE)]
        if not rows:
            return
        cur = self.list.currentRow()
        pos = rows.index(cur) if cur in rows else -1
        pos = max(0, min(len(rows) - 1, pos + step))
        self.list.setCurrentRow(rows[pos])
        self.list.scrollToItem(self.list.currentItem())

    def mousePressEvent(self, e):
        if not self.panel.geometry().contains(e.position().toPoint()):
            self.close_palette()

    # ---- matching

    def _filter(self, text: str):
        self.list.clear()
        q = text.strip()
        if q:
            found = []
            for cmd in self.commands:
                s = score(q, cmd)
                if s > 0:
                    cmd.score = s
                    found.append(cmd)
            found.sort(key=lambda c: -c.score)
            for cmd in found[:60]:
                self._add(cmd)
        else:  # everything, under its heading
            last = None
            for cmd in self.commands:
                if not cmd.listed:
                    continue
                group = self.heading(cmd.where) if self.heading else ""
                if group and group != last:
                    head = QListWidgetItem(group)
                    head.setFlags(Qt.NoItemFlags)
                    self.list.addItem(head)
                    last = group
                self._add(cmd)
        has = self.list.count() > 0
        self.list.setVisible(has)
        self.empty.setVisible(not has)
        self._move(0) if has else None
        if has:
            first = next(i for i in range(self.list.count()) if self.list.item(i).data(CMD_ROLE))
            self.list.setCurrentRow(first)
            self.list.scrollToTop()

    def _add(self, cmd: Command):
        if not self.search.text().strip() and self.heading is not None:  # (said once, by the heading)
            cmd = Command(cmd.title, "", cmd.icon, cmd.run, cmd.keywords, cmd.hint)
        item = QListWidgetItem(cmd.title)
        item.setData(CMD_ROLE, cmd)
        item.setToolTip(cmd.where)
        self.list.addItem(item)

    def _run_item(self, item: QListWidgetItem):
        cmd: Command | None = item.data(CMD_ROLE)
        if cmd is None:
            return
        self.close_palette()
        if cmd.run is not None:
            cmd.run()

    # ---- drawing: the dim layer and the panel's shadow

    def paintEvent(self, _e):
        t = tokens.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), t.scrim)
        r = QRectF(self.panel.geometry())
        for i in range(10, 0, -2):  # a soft shadow under the panel
            path = QPainterPath()
            path.addRoundedRect(r.adjusted(-i, -i + 6, i, i + 6), RADIUS["xl"] + i, RADIUS["xl"] + i)
            p.fillPath(path, alpha(mix(t.scrim, t.backdrop, 0.5), 10))
        path = QPainterPath()
        path.addRoundedRect(r, RADIUS["xl"], RADIUS["xl"])
        p.fillPath(path, t.raised)
        p.setPen(QPen(t.line_strong, 1))
        p.drawPath(path)
        p.end()


__all__ = ["Command", "CommandPalette", "score"]
