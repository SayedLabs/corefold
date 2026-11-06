"""The window's sidebar: rows drawn by hand (a grey pill that slides to the page you're on), each section
with its tools under it while it's open, and a narrow look with only icons.

Rows hold the page name (or None for a line or the room above the rows at the bottom); icons come from
icons.py and follow the theme."""
from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation,
                            Signal)
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QListWidget, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QWidget)

from . import icons
from .theme import mix

ICON_ROLE = Qt.UserRole + 1     # the row's icon in icons.py
BADGE_ROLE = Qt.UserRole + 2    # a small number on the row
KIND_ROLE = Qt.UserRole + 3     # "page", "section" (has tools under it), "tool" (under its section), "line", "space"
OPEN_ROLE = Qt.UserRole + 4     # a section that shows its tools now
MOVE_ROLE = Qt.UserRole + 5     # a row that can be dragged to another place (a section, with its tools under it)
FULL_ROLE = Qt.UserRole + 6     # the row's own height (it's lower only while it folds in or out)
DOT = "•"                       # a badge that's only a dot (About: a new version is waiting)
COMPACT_WIDTH = 64              # the sidebar with icons only
COMPACT_BELOW = 120             # narrower than this, rows show their icon only
CHEVRON_ROOM = 34               # the end of a section's row that only opens or shuts it (where its arrow is)
FOLD_MS = 200                   # a section's tools folding in or out


class _Rows(QStyledItemDelegate):
    def __init__(self, nav: "NavList"):
        super().__init__(nav)
        self.nav = nav

    def sizeHint(self, option, index):
        size = index.data(Qt.SizeHintRole)
        return size if size is not None else QSize(0, 36)  # (0 high is a row folded away, not "no size")

    def paint(self, p: QPainter, option, index):
        nav, t = self.nav, self.nav.theme()
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        rtl = option.direction == Qt.RightToLeft
        full = index.data(FULL_ROLE)
        if full and option.rect.height() < full:  # folding in or out: drawn whole where it'll be, cut to the
            if option.rect.height() <= 0:         # room it has so far, and fading in
                p.restore()
                return
            p.setClipRect(option.rect)
            p.setOpacity(option.rect.height() / full)
            option = QStyleOptionViewItem(option)
            option.rect = QRect(option.rect.left(), option.rect.top(), option.rect.width(), full)
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        compact = nav.compact()
        text = index.data(Qt.DisplayRole) or ""
        kind = index.data(KIND_ROLE) or "page"
        if kind == "space":  # (the room that keeps the last rows at the bottom)
            p.restore()
            return
        if kind == "line":  # a hairline between Home and the sections
            y = r.center().y()
            p.setPen(QPen(t.border, 1))
            inset = 14 if compact else 12
            p.drawLine(int(r.left() + inset), int(y), int(r.right() - inset), int(y))
            p.restore()
            return
        if not index.flags() & Qt.ItemIsSelectable:  # a heading (a short line when only icons show)
            if compact:
                y = r.center().y()
                p.setPen(QPen(t.border, 1))
                p.drawLine(int(r.left() + 14), int(y), int(r.right() - 14), int(y))
            else:
                f = QFont(option.font)
                f.setPointSizeF(max(6.5, option.font.pointSizeF() - 2.2))
                f.setWeight(QFont.Weight.DemiBold)
                if not rtl:  # (spaced-out letters would pull Arabic apart)
                    f.setLetterSpacing(QFont.PercentageSpacing, 106)
                p.setFont(f)
                p.setPen(t.muted)
                box = r.adjusted(12, 6, -12, 0)
                p.drawText(box, (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignVCenter | Qt.AlignAbsolute,
                           text.upper())
            p.restore()
            return
        row = index.row()
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver) and nav.drag_row < 0 and not nav.folding()
        lifted = row == nav.drag_row
        if lifted:  # being dragged: lifted off the list
            p.setPen(QPen(t.line_strong, 1))
            p.setBrush(t.tokens.raised)
            p.drawRoundedRect(r, 7, 7)
        elif hovered and not selected:  # (the page you're on has a pill that slides, see NavList.paintEvent)
            p.setPen(Qt.NoPen)
            p.setBrush(mix(nav.ground(), t.text, 0.05))
            p.drawRoundedRect(r, 8, 8)
        ink = t.text if (selected or hovered or lifted) else mix(t.text, nav.ground(), 0.25)
        sub = kind == "tool" and not compact
        if sub:  # a tool under its section: a thin line down from the section's icon, the name in line with its
            ink = t.text if (selected or hovered) else mix(t.text, nav.ground(), 0.32)  # section's name
            gx = r.right() - 12 - 10 if rtl else r.left() + 12 + 10
            p.setPen(QPen(selected and t.accent or t.border, 2 if selected else 1))
            p.drawLine(QPointF(gx, option.rect.top()), QPointF(gx, option.rect.bottom() + 1))
        name = None if sub else index.data(ICON_ROLE)
        show_icon = name and (compact or nav.show_icons)
        if compact:
            icon_box = QRectF(r.center().x() - 10, r.center().y() - 10, 20, 20)
        else:
            x = r.right() - 12 - 20 if rtl else r.left() + 12
            icon_box = QRectF(x, r.center().y() - 10, 20, 20)
        if show_icon:
            icons.paint(p, icon_box, name, ink)
        badge = index.data(BADGE_ROLE)
        if not compact:
            mark = badge == DOT
            if mark:
                badge = None
            lead = 12 + (30 if show_icon or (sub and nav.show_icons) else 2) + (8 if sub and not nav.show_icons else 0)
            trail = 12
            fm = QFontMetrics(option.font)
            f = QFont(option.font)
            if selected or kind == "section":
                f.setWeight(QFont.Weight.DemiBold)
            if (kind == "section" and not badge) or mark:
                trail += 18
            if kind == "section" and not badge and (hovered or lifted) and nav.can_drag(row):
                trail += 18  # (and its grip beside the arrow)
            badge_w = (fm.horizontalAdvance(str(badge)) + 14) if badge else 0
            dot = False
            if badge:
                trail += badge_w + 6
                # a long name ("Images et écran"): the name whole and a dot, rather than "Images et éc…" and the
                # word (it's in the row's tip). The rows with the same word all do the same, so they match.
                bold = QFont(option.font)
                bold.setWeight(QFont.Weight.DemiBold)
                room = r.width() - lead - trail
                model = index.model()
                for i in range(model.rowCount()):
                    other = model.index(i, 0)
                    if other.data(BADGE_ROLE) == badge and \
                            QFontMetrics(bold).horizontalAdvance(str(other.data(Qt.DisplayRole) or "")) > room:
                        dot = True
                        trail -= badge_w - 8
                        break
            elif hovered and nav.can_drag(row):
                trail += 18
            box = r.adjusted(trail, 0, -lead, 0) if rtl else r.adjusted(lead, 0, -trail, 0)
            p.setPen(ink)
            p.setFont(f)
            p.drawText(box, (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignVCenter | Qt.AlignAbsolute,
                       QFontMetrics(f).elidedText(text, Qt.ElideRight, int(box.width())))
            if mark:
                p.setPen(Qt.NoPen)
                p.setBrush(t.accent)
                cx = r.left() + 16 if rtl else r.right() - 16
                p.drawEllipse(QRectF(cx - 4, r.center().y() - 4, 8, 8))
            elif dot:
                p.setPen(Qt.NoPen)
                p.setBrush(t.muted)
                cx = r.left() + 14 if rtl else r.right() - 14
                p.drawEllipse(QRectF(cx - 3.5, r.center().y() - 3.5, 7, 7))
            elif badge:
                h = fm.height() + 2
                bx = r.left() + 10 if rtl else r.right() - 10 - badge_w
                pill = QRectF(bx, r.center().y() - h / 2, badge_w, h)
                p.setPen(Qt.NoPen)
                p.setBrush(t.tokens.select if not selected else t.tokens.raised)
                p.drawRoundedRect(pill, h / 2, h / 2)
                p.setPen(t.text)
                small = QFont(option.font)
                small.setPointSizeF(max(6.5, option.font.pointSizeF() - 1.5))
                small.setWeight(QFont.Weight.DemiBold)
                p.setFont(small)
                p.drawText(pill, Qt.AlignCenter, str(badge))
            elif kind == "section":  # open (its tools show under it) or shut: the arrow turns down as it opens
                cx = r.left() + 10 if rtl else r.right() - 10 - 14
                box = QRectF(cx, r.center().y() - 7, 14, 14)
                turn = nav.turn(index)
                p.save()
                p.translate(box.center())
                p.rotate(-90 * turn if rtl else 90 * turn)
                p.translate(-box.center())
                hot = hovered and nav.over_arrow(row)
                icons.paint(p, box, "chevron_left" if rtl else "chevron_right", t.text if hot else t.muted)
                p.restore()
                if (hovered or lifted) and nav.can_drag(row):  # the grip beside it: sections can be moved
                    gx = cx + 14 + 4 if rtl else cx - 4 - 16
                    icons.paint(p, QRectF(gx, r.center().y() - 8, 16, 16), "re_order_dots_vertical",
                                QColor(t.muted.red(), t.muted.green(), t.muted.blue(), 200))
            elif (hovered or lifted) and nav.can_drag(row):  # the grip: these rows can be moved
                gx = r.left() + 8 if rtl else r.right() - 8 - 16
                icons.paint(p, QRectF(gx, r.center().y() - 8, 16, 16), "re_order_dots_vertical",
                            QColor(t.muted.red(), t.muted.green(), t.muted.blue(), 200))
        elif badge:  # icons only: a dot on the icon
            p.setPen(Qt.NoPen)
            p.setBrush(t.accent)
            p.drawEllipse(QRectF(icon_box.right() - 3, icon_box.top() - 2, 7, 7))
        p.restore()


class NavList(QListWidget):
    """The sidebar's rows. A row marked MOVE_ROLE (a section) can be dragged up and down, with the tools under
    it; a section's tools fold in and out under it."""
    moved = Signal(list)   # the movable rows' pages, in their new order
    resized = Signal()     # (the room at the bottom is measured again)
    menu = Signal(int, QPoint)  # a row was right-clicked (row, global position)
    fold_row = Signal(int, int)  # a section to open (1), shut (0) or the other way round (-1): its arrow, the keys
    again = Signal(int)    # the lit row was clicked again (a section's: it opens or shuts)
    folded = Signal()      # a step of the tools folding in or out (the room at the bottom follows)

    def __init__(self, theme_getter, show_icons: bool, animate: bool):
        super().__init__()
        self.setObjectName("nav")
        self.theme = theme_getter
        self.show_icons = show_icons
        self.animate = animate
        self.drag_row = -1
        self.on_canvas = False   # True for a list on the page area rather than the sidebar
        self._press: QPoint | None = None
        self._press_row = -1
        self._was_current = False
        self._lifted: list = []   # a section's tools folded away while it's dragged (shown again on the drop)
        # the tools folding in or out: their items and heights at the start and the end, and the arrows' turns
        self._fold_rows: list = []
        self._turns: dict = {}
        self._hiding: set = set()
        self._fold_anim = QVariantAnimation(self)
        self._fold_anim.setDuration(FOLD_MS)
        self._fold_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._fold_anim.setStartValue(0.0)
        self._fold_anim.setEndValue(1.0)
        self._fold_anim.valueChanged.connect(self._fold_step)
        self._fold_anim.finished.connect(self._fold_done)
        self.setItemDelegate(_Rows(self))
        self.setMouseTracking(True)
        self.setFrameShape(QListWidget.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # (it still scrolls on tiny screens)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setCursor(Qt.PointingHandCursor)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self.menu.emit(self.indexAt(pos).row(),
                                                                           self.viewport().mapToGlobal(pos)))
        # the pill slides from the row you were on to the one you picked
        self._mark_y = None
        self._mark_anim = QVariantAnimation(self)
        self._mark_anim.setDuration(220)
        self._mark_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._mark_anim.valueChanged.connect(self._mark_moved)
        self.currentRowChanged.connect(self._slide_mark)

    # ---- looks

    def compact(self) -> bool:
        return self.viewport().width() < COMPACT_BELOW

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._mark_y = None
        self.resized.emit()
        self.viewport().update()

    def _row_mark(self, row: int) -> float | None:
        if row < 0:
            return None
        r = self.visualRect(self.model().index(row, 0))
        return r.center().y() if r.isValid() else None

    def _slide_mark(self, row: int):
        target = self._row_mark(row)
        if target is None:
            return
        start = self._mark_y
        if self.animate and start is not None and self.drag_row < 0 and self.isVisible():
            self._mark_anim.stop()
            self._mark_anim.setStartValue(float(start))
            self._mark_anim.setEndValue(float(target))
            self._mark_anim.start()
        else:
            self._mark_y = target
            self.viewport().update()

    def _follow_mark(self):
        """The rows moved under the pill (tools folding in or out): it heads for where its row is now."""
        if self._mark_anim.state() == QVariantAnimation.Running:
            target = self._row_mark(self.currentRow())
            if target is not None:
                self._mark_anim.setEndValue(float(target))
        else:
            self._mark_y = None

    def _mark_moved(self, y):
        self._mark_y = y
        self.viewport().update()

    def ground(self) -> QColor:
        """The colour behind the rows (the window's backdrop, or the page area for the Settings list)."""
        t = self.theme()
        return t.bg if self.on_canvas else (t.sidebar or t.panel)

    def paintEvent(self, e):
        row = self.currentRow()
        if row >= 0 and self.drag_row < 0:
            if self._mark_y is None or self._mark_anim.state() != QVariantAnimation.Running:
                self._mark_y = self._row_mark(row)
            if self._mark_y is not None:  # the pill behind the page you're on, under the rows
                t = self.theme()
                p = QPainter(self.viewport())
                p.setRenderHint(QPainter.Antialiasing)
                index = self.model().index(row, 0)
                h = self.visualRect(index).height() - 4
                pill = QRectF(2, self._mark_y - h / 2, self.viewport().width() - 4, h)
                if index.data(KIND_ROLE) == "tool" and not self.compact():  # (in from the line on the side)
                    cut = 12 + 10 + 8
                    pill = pill.adjusted(0 if self.isRightToLeft() else cut, 0, -cut if self.isRightToLeft() else 0, 0)
                path = QPainterPath()
                path.addRoundedRect(pill, 8, 8)
                p.fillPath(path, t.tokens.hover if self.on_canvas else t.tokens.select)
                p.end()
        super().paintEvent(e)

    # ---- a section's tools folding in and out

    def turn(self, index) -> float:
        """How far a section's arrow has turned down (1: open, 0: shut, in between while it moves)."""
        item = self.item(index.row())
        if item is not None and id(item) in self._turns:
            return self._turns[id(item)][2]
        return 1.0 if index.data(OPEN_ROLE) else 0.0

    def folding(self) -> bool:
        return self._fold_anim.state() == QVariantAnimation.Running

    def hidden_soon(self, row: int) -> bool:
        """The row is hidden, or on its way out (folding in under its section)."""
        return self.isRowHidden(row) or id(self.item(row)) in self._hiding

    def fold(self, hide: dict, opened: dict, animate: bool):
        """Rows to show or hide ({row: hidden}) and sections open or shut ({row: open}). Only what changes moves:
        the tools fold out from under their section (or into it) while its arrow turns, or at once without
        animations."""
        if not any(hidden != self.hidden_soon(row) for row, hidden in hide.items()) and \
                not any(bool(self.item(row).data(OPEN_ROLE)) != is_open for row, is_open in opened.items()):
            return  # (nothing changes: a fold on its way there goes on)
        self._finish_fold()
        grow, shrink = [], []
        for row, hidden in hide.items():
            if hidden != self.isRowHidden(row):
                (shrink if hidden else grow).append(self.item(row))
        turns = {}
        for row, is_open in opened.items():
            item = self.item(row)
            if bool(item.data(OPEN_ROLE)) != is_open:
                turns[id(item)] = (0.0 if is_open else 1.0, 1.0 if is_open else 0.0)
                item.setData(OPEN_ROLE, is_open)
        if not animate or not self.isVisible() or not (grow or shrink or turns):
            for item in grow:
                self.setRowHidden(self.row(item), False)
            for item in shrink:
                self.setRowHidden(self.row(item), True)
            if grow or shrink:
                self.doItemsLayout()
                self._follow_mark()
            return
        # (the rows come in from the top down, and go from the bottom up: one row at a time is cut, like a
        # drawer sliding open or shut, rather than every row squashed at once)
        self._fold_rows = []
        for growing, items in ((True, grow), (False, shrink)):
            items = sorted(items, key=self.row)
            fulls = [item.data(FULL_ROLE) or item.sizeHint().height() for item in items]
            for item in items:
                if growing:
                    item.setSizeHint(QSize(0, 0))
                    self.setRowHidden(self.row(item), False)
                else:
                    self._hiding.add(id(item))
            if items:
                self._fold_rows.append((growing, items, fulls))
        self._turns = {key: (a, b, a) for key, (a, b) in turns.items()}
        self._fold_anim.start()

    def _fold_step(self, v):
        for growing, items, fulls in self._fold_rows:
            room = round(sum(fulls) * (v if growing else 1 - v))
            for item, full in zip(items, fulls):
                h = max(0, min(full, room))
                room -= h
                item.setSizeHint(QSize(0, h))
        for key, (a, b, _) in list(self._turns.items()):
            self._turns[key] = (a, b, a + (b - a) * v)
        if self._fold_rows:
            self.doItemsLayout()
            self.folded.emit()
            self._follow_mark()
        self.viewport().update()

    def _fold_done(self):
        rows, self._fold_rows = self._fold_rows, []
        self._turns = {}
        self._hiding = set()
        for growing, items, fulls in rows:
            for item, full in zip(items, fulls):
                if self.row(item) < 0:  # (taken out of the list meanwhile)
                    continue
                item.setSizeHint(QSize(0, full))
                if not growing:
                    self.setRowHidden(self.row(item), True)
        if rows:
            self.doItemsLayout()
            self.folded.emit()
            self._follow_mark()
        self.viewport().update()

    def _finish_fold(self):
        """Jumps a fold that's still moving to its end (before the next one, or the rows are made again)."""
        if self.folding():
            self._fold_anim.stop()
            self._fold_step(1.0)
            self._fold_done()

    def over_arrow(self, row: int, x: int | None = None) -> bool:
        """The pointer (or x) is on the end of a section's row with its arrow, which only opens or shuts it."""
        item = self.item(row)
        if item is None or item.data(KIND_ROLE) != "section" or self.compact():
            return False
        if x is None:
            x = self.viewport().mapFromGlobal(QCursor.pos()).x()
        w = self.viewport().width()
        return x < CHEVRON_ROOM if self.isRightToLeft() else x > w - CHEVRON_ROOM

    def keyPressEvent(self, e):
        """Right opens the section on the lit row, Left shuts it (the other way round in Arabic)."""
        row = self.currentRow()
        item = self.item(row)
        if item is not None and item.data(KIND_ROLE) == "section" and e.key() in (Qt.Key_Left, Qt.Key_Right):
            opening = (e.key() == Qt.Key_Right) != self.isRightToLeft()
            self.fold_row.emit(row, 1 if opening else 0)
            return
        super().keyPressEvent(e)

    # ---- moving rows (a section with the tools under it)

    def can_drag(self, row: int) -> bool:
        item = self.item(row)
        return item is not None and bool(item.data(MOVE_ROLE)) and \
            sum(1 for r in range(self.count()) if self.item(r).data(MOVE_ROLE)) > 1

    def _group(self, row: int) -> range:
        """A movable row and the tools under it."""
        end = row + 1
        while end < self.count() and self.item(end).data(KIND_ROLE) == "tool":
            end += 1
        return range(row, end)

    def _group_span(self, row: int) -> tuple[int, int]:
        """Where a section and its tools showing are, top to bottom (in the viewport)."""
        rows = [r for r in self._group(row) if not self.isRowHidden(r)]
        top = self.visualRect(self.model().index(rows[0], 0)).top()
        bottom = self.visualRect(self.model().index(rows[-1], 0)).bottom()
        return top, bottom

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
            self._press_row = self.indexAt(self._press).row()
            self._was_current = self._press_row >= 0 and self._press_row == self.currentRow()
            if self.can_drag(self._press_row) or self.over_arrow(self._press_row, self._press.x()):
                return  # (opened on release, so dragging a section doesn't open it first)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        row = self.indexAt(e.position().toPoint()).row()
        if e.button() == Qt.LeftButton and (self.can_drag(row) or self.over_arrow(row, int(e.position().x()))):
            self.mousePressEvent(e)  # (a second click, as quick as it comes)
            return
        super().mouseDoubleClickEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is not None and e.buttons() & Qt.LeftButton and self.can_drag(self._press_row):
            pos = e.position().toPoint()
            if self.drag_row < 0 and (pos - self._press).manhattanLength() >= QApplication.startDragDistance():
                self._lift(self._press_row)
            if self.drag_row >= 0:
                self._drag_to(pos.y())
            return  # (a pressed button moving over rows doesn't pick them)
        if self.drag_row < 0:
            self.viewport().update()  # (the arrow lights up under the pointer)
        super().mouseMoveEvent(e)

    def _lift(self, row: int):
        """A section picked up: its tools fold away under it while it's carried (and come back on the drop)."""
        self._finish_fold()
        self.drag_row = row
        self.setCursor(Qt.ClosedHandCursor)
        group = self._group(row)
        showing = [r for r in group[1:] if not self.isRowHidden(r)]
        self._lifted = [self.item(r) for r in showing]
        if showing:  # (its arrow turns while they fold away)
            self.fold({r: True for r in showing}, {row: False}, self.animate)
        self.viewport().update()

    def _drag_to(self, y: int):
        """The carried section changes places with the one it's dragged over halfway (so it doesn't jump back)."""
        movable = [r for r in range(self.count()) if self.item(r).data(MOVE_ROLE)]
        here = self.drag_row
        for r in movable:
            if r == here or self.isRowHidden(r):
                continue
            top, bottom = self._group_span(r)
            middle = (top + bottom) / 2
            if r > here and y > middle:  # past the middle of the next one down
                if r == min((m for m in movable if m > here), default=-1):
                    self._move_group(here, r)
                    return
            elif r < here and y < middle and r == max((m for m in movable if m < here), default=-1):
                self._move_group(here, r)
                return

    def _move_group(self, src: int, other: int):
        """The section at src (and its tools) and the one at other (with its tools) change places."""
        self._finish_fold()
        current = self.item(self.currentRow()) if self.currentRow() >= 0 else None
        hidden = {id(self.item(r)): self.isRowHidden(r) for r in range(self.count())}
        mine = list(self._group(src))
        theirs = list(self._group(other))
        self.blockSignals(True)  # (moving a row isn't opening a page)
        try:
            items = [self.takeItem(mine[0]) for _ in mine]
            if other > src:  # (theirs moved up by as many rows as were taken out)
                at = theirs[-1] - len(mine) + 1
            else:
                at = theirs[0]
            for i, item in enumerate(items):
                self.insertItem(at + i, item)
            for r in range(self.count()):
                self.setRowHidden(r, hidden.get(id(self.item(r)), False))
            self.drag_row = at
            if current is not None:
                self.setCurrentRow(self.row(current))
        finally:
            self.blockSignals(False)
        self._mark_y = None
        self.viewport().update()

    def mouseReleaseEvent(self, e):
        dragged = self.drag_row >= 0
        self._press = None
        if dragged:
            self.drag_row = -1
            self.setCursor(Qt.PointingHandCursor)
            self._mark_y = None
            lifted, self._lifted = self._lifted, []
            if lifted:  # (its tools fold out again where it was dropped)
                rows = [self.row(item) for item in lifted if self.row(item) >= 0]
                head = rows[0] - 1 if rows else -1
                self.fold({r: False for r in rows}, {head: True} if head >= 0 else {}, self.animate)
            self.viewport().update()
            self.moved.emit(self.movable_pages())
            return  # (a drop isn't a click on the row)
        row = self.indexAt(e.position().toPoint()).row()
        if e.button() == Qt.LeftButton and row == self._press_row and row >= 0:
            if self.over_arrow(row, int(e.position().x())):  # the arrow: open or shut, nothing else
                self._press_row = -1
                self.fold_row.emit(row, -1)
                return
            if self._was_current:  # (before the click goes to the row's own page, from a page under it)
                self.again.emit(row)
            if self.can_drag(row):
                self._press_row = -1
                self.setCurrentRow(row)
                self.itemClicked.emit(self.item(row))
                return
        super().mouseReleaseEvent(e)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self.viewport().update()

    def movable_pages(self) -> list[str]:
        return [self.item(r).data(Qt.UserRole) for r in range(self.count()) if self.item(r).data(MOVE_ROLE)]

    def event(self, e):
        if e.type() == QEvent.Type.LayoutDirectionChange:
            self._mark_y = None
        return super().event(e)


class EdgeHandle(QWidget):
    """The sidebar's inner edge: drag it to make the sidebar wider or narrower (narrow enough and it
    shows icons only); double-click it to switch between the two."""
    dragged = Signal(int)      # the width the sidebar should have
    released = Signal()
    toggled = Signal()

    def __init__(self, sidebar: QWidget):
        super().__init__()
        self.sidebar = sidebar
        self.setFixedWidth(6)
        self.setCursor(Qt.SizeHorCursor)
        self._dragging = False

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = True

    def mouseMoveEvent(self, e):
        if not self._dragging:
            return
        g = e.globalPosition().toPoint()
        left = self.sidebar.mapToGlobal(QPoint(0, 0)).x()
        if self.layoutDirection() == Qt.RightToLeft:
            width = left + self.sidebar.width() - g.x()
        else:
            width = g.x() - left
        self.dragged.emit(int(width))

    def mouseReleaseEvent(self, e):
        if self._dragging:
            self._dragging = False
            self.released.emit()

    def mouseDoubleClickEvent(self, e):
        self.toggled.emit()
