"""The window's sidebar: rows drawn by hand (a sliding accent mark on the page you're on), the tools
in the order the person drags them into, and a narrow look with only icons.

Rows hold the page name (or None for a heading); icons come from icons.py and follow the theme."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractItemView, QApplication, QListWidget, QStyle, QStyledItemDelegate, QWidget

from . import icons
from .theme import mix

ICON_ROLE = Qt.UserRole + 1     # the row's icon in icons.py
BADGE_ROLE = Qt.UserRole + 2    # a small number on the row (tools you can add, on the Store)
COMPACT_WIDTH = 64              # the sidebar with icons only
COMPACT_BELOW = 120             # narrower than this, rows show their icon only


class _Rows(QStyledItemDelegate):
    def __init__(self, nav: "NavList"):
        super().__init__(nav)
        self.nav = nav

    def sizeHint(self, option, index):
        return index.data(Qt.SizeHintRole) or QSize(0, 36)

    def paint(self, p: QPainter, option, index):
        nav, t = self.nav, self.nav.theme()
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        rtl = option.direction == Qt.RightToLeft
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        compact = nav.compact()
        text = index.data(Qt.DisplayRole) or ""
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
        hovered = bool(option.state & QStyle.State_MouseOver) and nav.drag_row < 0
        lifted = row == nav.drag_row
        if lifted:  # being dragged: lifted off the list
            p.setPen(QPen(t.accent, 1))
            p.setBrush(t.panel)
            p.drawRoundedRect(r, 7, 7)
        elif selected or hovered:
            p.setPen(Qt.NoPen)
            p.setBrush(mix(t.sidebar or t.panel, t.text, 0.085 if selected else 0.05))
            p.drawRoundedRect(r, 7, 7)
        ink = t.text if (selected or hovered or lifted) else mix(t.text, t.sidebar or t.panel, 0.22)
        name = index.data(ICON_ROLE)
        show_icon = name and (compact or nav.show_icons)
        if compact:
            icon_box = QRectF(r.center().x() - 10, r.center().y() - 10, 20, 20)
        else:
            x = r.right() - 12 - 20 if rtl else r.left() + 12
            icon_box = QRectF(x, r.center().y() - 10, 20, 20)
        if show_icon:
            icons.paint(p, icon_box, name, t.accent if selected else ink)
        badge = index.data(BADGE_ROLE)
        if not compact:
            lead = 12 + (30 if show_icon else 2)
            trail = 12
            fm = QFontMetrics(option.font)
            badge_w = (fm.horizontalAdvance(str(badge)) + 14) if badge else 0
            if badge:
                trail += badge_w + 6
            elif hovered and nav.can_drag(row):
                trail += 18
            box = r.adjusted(trail, 0, -lead, 0) if rtl else r.adjusted(lead, 0, -trail, 0)
            p.setPen(ink)
            f = QFont(option.font)
            if selected:
                f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            p.drawText(box, (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignVCenter | Qt.AlignAbsolute,
                       QFontMetrics(f).elidedText(text, Qt.ElideRight, int(box.width())))
            if badge:
                h = fm.height() + 2
                bx = r.left() + 10 if rtl else r.right() - 10 - badge_w
                pill = QRectF(bx, r.center().y() - h / 2, badge_w, h)
                p.setPen(Qt.NoPen)
                p.setBrush(mix(t.panel, t.accent, 0.22 if t.dark else 0.16))
                p.drawRoundedRect(pill, h / 2, h / 2)
                p.setPen(t.text)
                small = QFont(option.font)
                small.setPointSizeF(max(6.5, option.font.pointSizeF() - 1.5))
                small.setWeight(QFont.Weight.DemiBold)
                p.setFont(small)
                p.drawText(pill, Qt.AlignCenter, str(badge))
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
    """The sidebar's rows. Rows between movable_range() can be dragged up and down."""
    moved = Signal(list)   # the movable rows' pages, in their new order
    menu = Signal(int, QPoint)  # a row was right-clicked (row, global position)

    def __init__(self, theme_getter, show_icons: bool, animate: bool):
        super().__init__()
        self.setObjectName("nav")
        self.theme = theme_getter
        self.show_icons = show_icons
        self.animate = animate
        self.drag_row = -1
        self._press: QPoint | None = None
        self._press_row = -1
        self.movable: tuple[int, int] = (0, -1)   # first and last row that can be dragged
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
        # the accent mark slides from the row you were on to the one you picked
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

    def _mark_moved(self, y):
        self._mark_y = y
        self.viewport().update()

    def paintEvent(self, e):
        super().paintEvent(e)
        row = self.currentRow()
        if row < 0 or self.drag_row >= 0:
            return
        if self._mark_y is None or self._mark_anim.state() != QVariantAnimation.Running:
            self._mark_y = self._row_mark(row)
        if self._mark_y is None:
            return
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        rtl = self.layoutDirection() == Qt.RightToLeft
        x = self.viewport().width() - 2 - 3 if rtl else 2
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, self._mark_y - 8, 3, 16), 1.5, 1.5)
        p.fillPath(path, self.theme().accent)
        p.end()

    # ---- moving rows

    def can_drag(self, row: int) -> bool:
        first, last = self.movable
        return first <= row <= last and last > first

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
            self._press_row = self.indexAt(self._press).row()
            if self.can_drag(self._press_row):
                return  # (opened on release, so dragging a tool doesn't open it first)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is not None and e.buttons() & Qt.LeftButton and self.can_drag(self._press_row):
            pos = e.position().toPoint()
            if self.drag_row < 0 and (pos - self._press).manhattanLength() >= QApplication.startDragDistance():
                self.drag_row = self._press_row
                self.setCursor(Qt.ClosedHandCursor)
                self.viewport().update()
            if self.drag_row >= 0:
                first, last = self.movable
                over = self.indexAt(QPoint(self.viewport().width() // 2, pos.y())).row()
                if over < 0:
                    over = first if pos.y() < self.visualRect(self.model().index(first, 0)).top() else last
                over = max(first, min(last, over))
                if over != self.drag_row:
                    self._move_row(self.drag_row, over)
                    self.drag_row = over
            return  # (a pressed button moving over rows doesn't pick them)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        dragged = self.drag_row >= 0
        self._press = None
        if dragged:
            self.drag_row = -1
            self.setCursor(Qt.PointingHandCursor)
            self._mark_y = None
            self.viewport().update()
            self.moved.emit(self.movable_pages())
            return  # (a drop isn't a click on the row)
        row = self.indexAt(e.position().toPoint()).row()
        if e.button() == Qt.LeftButton and self.can_drag(row) and row == self._press_row:
            self._press_row = -1
            self.setCurrentRow(row)
            self.itemClicked.emit(self.item(row))
            return
        super().mouseReleaseEvent(e)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self.viewport().update()

    def _move_row(self, src: int, dst: int):
        current = self.currentRow()
        self.blockSignals(True)  # (moving a row isn't opening a page)
        item = self.takeItem(src)
        self.insertItem(dst, item)
        if current == src:
            current = dst
        elif src < current <= dst:
            current -= 1
        elif dst <= current < src:
            current += 1
        self.setCurrentRow(current)
        self.blockSignals(False)
        self._mark_y = None
        self.viewport().update()

    def move_by(self, row: int, step: int):
        """Moves a tool's row up or down one place (from its menu)."""
        dst = row + step
        if self.can_drag(row) and self.can_drag(dst):
            self._move_row(row, dst)
            self.moved.emit(self.movable_pages())

    def movable_pages(self) -> list[str]:
        first, last = self.movable
        return [self.item(r).data(Qt.UserRole) for r in range(first, last + 1)]

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
