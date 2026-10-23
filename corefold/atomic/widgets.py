"""Shared building blocks that draw themselves from the tokens.

    Switch       an on/off switch; with text it is a whole row (words on one side, the switch on
                 the other), and it is still a QCheckBox, so isChecked()/toggled work as usual
    SettingRow   an icon, a title, a line under it, and a control at the far side
    Group        a rounded box of rows with hairlines between them
    GlowPanel    a box washed with the accent colour, for the top of a start page
    IconButton   a square button with only an icon
    ChoiceCard   a big card to pick one of a few (a checkable button: a ring, a title, a note, a chip)
    Dropdown     a list to pick one thing from (a QComboBox whose open list matches the family:
                 rounded, soft highlight under the mouse, a mark on the one picked)
    mark_rows()  hairlines between switch rows that follow each other in a box
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QFont, QFontMetrics, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLayout, QLabel, QListView,
                               QPushButton, QSizePolicy, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from . import tokens
from .tokens import MOTION, RADIUS, alpha, mix

TRACK_W, TRACK_H = 40, 20


class Switch(QCheckBox):
    """An on/off switch. Given text, it fills the row: the words wrap on the leading side, the switch
    sits on the trailing side, and clicking anywhere on the row flips it."""

    def __init__(self, text: str = "", description: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.description = description
        self.divider = False          # a hairline above (set by mark_rows)
        self._pos = 0.0               # 0 = off, 1 = on (animated in between)
        self._hover = False
        self._anim: QVariantAnimation | None = None
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        sp = QSizePolicy(QSizePolicy.Expanding if text else QSizePolicy.Fixed, QSizePolicy.Preferred)
        sp.setHeightForWidth(bool(text))
        self.setSizePolicy(sp)
        self.toggled.connect(self._slide)

    # ---- size: the words wrap, so the height follows the width

    def _pad(self) -> tuple[int, int]:
        return (10, 10) if self.text() else (0, 0)

    def _text_rect(self, width: int) -> QRect:
        return QRect(0, 0, max(40, width - TRACK_W - 16), 10000)

    def _text_height(self, width: int) -> int:
        fm = QFontMetrics(self.font())
        h = fm.boundingRect(self._text_rect(width), Qt.TextWordWrap, self.text()).height()
        if self.description:
            small = self.font()
            small.setPointSizeF(max(7.0, small.pointSizeF() * 0.9))
            h += 2 + QFontMetrics(small).boundingRect(self._text_rect(width), Qt.TextWordWrap,
                                                      self.description).height()
        return h

    def hasHeightForWidth(self) -> bool:
        return bool(self.text())

    def heightForWidth(self, width: int) -> int:
        top, bottom = self._pad()
        return max(TRACK_H + 4, self._text_height(width)) + top + bottom

    def sizeHint(self) -> QSize:
        if not self.text():
            return QSize(TRACK_W + 2, TRACK_H + 4)
        fm = QFontMetrics(self.font())
        w = min(520, fm.horizontalAdvance(self.text()) + TRACK_W + 24)
        return QSize(w, self.heightForWidth(w))

    def minimumSizeHint(self) -> QSize:
        if not self.text():
            return self.sizeHint()
        return QSize(TRACK_W + 80, TRACK_H + 4)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.text():  # (some layouts, like a scroll area's, don't ask for the height at this width)
            h = self.heightForWidth(self.width())
            if h != self.minimumHeight():
                self.setMinimumHeight(h)

    # ---- motion

    def _slide(self, on: bool):
        end = 1.0 if on else 0.0
        if self._anim is not None:
            self._anim.stop()
        if not (tokens.animations and self.isVisible()):
            self._pos = end
            self.update()
            return
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(MOTION["fast"])
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(end)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._moved)
        self._anim.start()

    def _moved(self, v):
        self._pos = float(v)
        self.update()

    def setChecked(self, on: bool):  # (set from code: no sliding)
        super().setChecked(on)
        if self._anim is not None:
            self._anim.stop()
        self._pos = 1.0 if self.isChecked() else 0.0
        self.update()

    def showEvent(self, e):
        self._pos = 1.0 if self.isChecked() else 0.0
        super().showEvent(e)

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    # ---- drawing

    def paintEvent(self, _e):
        t = tokens.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rtl = self.layoutDirection() == Qt.RightToLeft
        enabled = self.isEnabled()
        top, _bottom = self._pad()
        if self.divider:
            p.setPen(QPen(t.border, 1))
            p.drawLine(QPointF(0, 0.5), QPointF(self.width(), 0.5))
        # the switch, on the trailing side, level with the first line of words
        fm = QFontMetrics(self.font())
        first_line = fm.height()
        ty = (top + (first_line - TRACK_H) / 2) if self.text() else (self.height() - TRACK_H) / 2
        if self.text() and not self.description and self._text_height(self.width()) <= first_line + 2:
            ty = (self.height() - TRACK_H) / 2
        tx = 1 if rtl else self.width() - TRACK_W - 1
        if not self.text():
            tx = (self.width() - TRACK_W) / 2
        track = QRectF(tx, ty, TRACK_W, TRACK_H)
        on = self._pos
        off_fill = mix(t.field, t.text, 0.05) if self._hover else t.field
        fill = mix(off_fill, t.accent, on)
        if not enabled:
            fill = mix(fill, t.surface, 0.55)
        edge = mix(t.subtle if self._hover else t.line_strong, t.accent, on)
        p.setPen(QPen(edge if on < 1 else t.accent, 1))
        p.setBrush(fill)
        p.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), TRACK_H / 2, TRACK_H / 2)
        d = 12 + 2 * on + (1 if self._hover and enabled else 0)
        travel = TRACK_W - 6 - 14
        x0 = track.left() + 3 + 7
        cx = x0 + travel * (1 - on if rtl else on)
        knob = mix(t.muted, t.on_accent, on)
        if not enabled:
            knob = mix(knob, t.surface, 0.5)
        p.setPen(Qt.NoPen)
        p.setBrush(knob)
        p.drawEllipse(QPointF(cx, track.center().y()), d / 2, d / 2)
        # the words
        if self.text():
            box = QRect(0, top, self.width() - TRACK_W - 16, self.height() - top)
            if rtl:
                box.moveLeft(TRACK_W + 16)
            align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignTop | Qt.AlignAbsolute | Qt.TextWordWrap
            text_h = self._text_height(self.width())
            if text_h < self.height() - top * 2:
                box.setTop(int((self.height() - text_h) / 2))
            p.setPen(t.text if enabled else t.subtle)
            p.setFont(self.font())
            used = p.boundingRect(box, align, self.text())
            p.drawText(box, align, self.text())
            if self.description:
                small = self.font()
                small.setPointSizeF(max(7.0, small.pointSizeF() * 0.9))
                p.setFont(small)
                p.setPen(t.muted if enabled else t.subtle)
                p.drawText(box.adjusted(0, used.height() + 2, 0, 0), align, self.description)
        if self.hasFocus() and self.focusPolicy() != Qt.NoFocus and self._keyboard_focus:
            p.setPen(QPen(t.accent, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(track.adjusted(-3, -3, 3, 3), TRACK_H / 2 + 3, TRACK_H / 2 + 3)
        p.end()

    _keyboard_focus = False

    def focusInEvent(self, e):
        self._keyboard_focus = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._keyboard_focus = False
        super().focusOutEvent(e)


class _KeepWrappedText(QObject):
    """Wrapped text keeps the height it needs at its width (layouts in a scroll area can squeeze it)."""

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Resize and isinstance(obj, QLabel) and obj.wordWrap():
            h = obj.heightForWidth(obj.width())
            if h > 0 and h != obj.minimumHeight():
                obj.setMinimumHeight(h)
        return False


_keeper: _KeepWrappedText | None = None


def keep_wrapped_text(root: QWidget) -> None:
    """Every word-wrapped label under root keeps the height its lines need."""
    global _keeper
    if _keeper is None:
        _keeper = _KeepWrappedText()
    for lab in root.findChildren(QLabel):
        if lab.wordWrap() and not lab.property("keepsHeight"):
            lab.setProperty("keepsHeight", True)
            lab.installEventFilter(_keeper)


def mark_rows(root: QWidget) -> None:
    """A hairline above each switch row that comes right after another row in the same box."""
    for sw in root.findChildren(Switch):
        sw.divider = False
        parent = sw.parentWidget()
        lay = parent.layout() if parent is not None else None
        if lay is None or not sw.text():
            continue
        i = lay.indexOf(sw)
        if i <= 0:
            continue
        before = lay.itemAt(i - 1).widget()
        if isinstance(before, Switch) and before.text() and before.isVisibleTo(parent):
            sw.divider = True


class Group(QFrame):
    """A rounded box of rows with a hairline between each (add rows with add())."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("group")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)

    def add(self, row: QWidget) -> QWidget:
        self.lay.addWidget(row)
        return row

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setPen(QPen(tokens.current().border, 1))
        shown = [self.lay.itemAt(i).widget() for i in range(self.lay.count())]
        shown = [w for w in shown if w is not None and w.isVisible()]
        for w in shown[1:]:
            y = w.geometry().top()
            p.drawLine(16, y, self.width() - 16, y)
        p.end()


class SettingRow(QWidget):
    """One setting: an icon (optional), its name and a line about it, and its control at the far side."""

    def __init__(self, title: str, description: str = "", control: QWidget | None = None,
                 icon: QLabel | None = None):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(14)
        if icon is not None:
            row.addWidget(icon, 0, Qt.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(2)
        self.title = QLabel(title)
        self.title.setWordWrap(True)
        words.addWidget(self.title)
        self.description = QLabel(description)
        self.description.setObjectName("muted")
        self.description.setWordWrap(True)
        self.description.setVisible(bool(description))
        words.addWidget(self.description)
        row.addLayout(words, 1)
        if control is not None:
            row.addWidget(control, 0, Qt.AlignVCenter)
        self.control = control


class GlowPanel(QFrame):
    """A rounded box with a soft wash of the accent colour, brightest in its top trailing corner."""

    def __init__(self, parent: QWidget | None = None, color=None, rings: bool = True):
        super().__init__(parent)
        self.setObjectName("glow")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.color = color    # a colour of its own (a tool's), instead of the accent
        self.rings = rings

    def paintEvent(self, _e):
        t = tokens.current()
        accent = self.color if self.color is not None else t.accent
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, RADIUS["xl"], RADIUS["xl"])
        base = QLinearGradient(r.topLeft(), r.bottomRight())
        base.setColorAt(0, mix(t.surface, accent, 0.20 if t.dark else 0.13))
        base.setColorAt(0.55, mix(t.surface, accent, 0.06 if t.dark else 0.04))
        base.setColorAt(1, t.surface)
        p.fillPath(path, base)
        rtl = self.layoutDirection() == Qt.RightToLeft
        cx = r.left() + r.width() * (0.12 if rtl else 0.88)
        glow = QRadialGradient(QPointF(cx, r.top() - r.height() * 0.15), max(r.width(), r.height()) * 0.55)
        glow.setColorAt(0, alpha(accent, 70 if t.dark else 46))
        glow.setColorAt(1, alpha(accent, 0))
        p.fillPath(path, glow)
        # faint rings, like a ripple, in the same corner
        p.setClipPath(path)
        p.setBrush(Qt.NoBrush)
        for i, rad in enumerate((90, 150, 220, 300) if self.rings else ()):
            p.setPen(QPen(alpha(accent, (34 if t.dark else 26) - i * 6), 1))
            p.drawEllipse(QPointF(cx, r.top() + 10), rad, rad)
        p.setClipping(False)
        p.setPen(QPen(mix(t.border, accent, 0.25), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.end()


class FlowLayout(QLayout):
    """Lays items out in a row and wraps to the next line when they don't fit (buttons, chips)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._place(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._place(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _place(self, rect: QRect, test: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        rtl = self.parentWidget() is not None and self.parentWidget().layoutDirection() == Qt.RightToLeft
        x, y, line_h = area.x(), area.y(), 0
        for item in self._items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            hint = item.sizeHint()
            if x + hint.width() > area.right() + 1 and line_h > 0:
                x = area.x()
                y += line_h + self._spacing
                line_h = 0
            if not test:
                gx = area.right() + 1 - (x - area.x()) - hint.width() if rtl else x
                item.setGeometry(QRect(gx, y, hint.width(), hint.height()))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


class ResponsiveGrid(QWidget):
    """Tiles in as many columns as fit (each at least min_width wide), re-flowed as the width changes."""

    def __init__(self, min_width: int = 240, spacing: int = 12, max_columns: int = 4):
        super().__init__()
        self.min_width = min_width
        self.max_columns = max_columns
        self.tiles: list[QWidget] = []
        self.cols = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(spacing)

    def add(self, tile: QWidget) -> QWidget:
        self.tiles.append(tile)
        self._flow(force=True)
        return tile

    def _flow(self, force: bool = False):
        width = self.width() if self.width() > 50 else 800
        cols = max(1, min(self.max_columns, (width + self.grid.spacing()) // (self.min_width + self.grid.spacing())))
        if cols == self.cols and not force:
            return
        self.cols = cols
        for tile in self.tiles:
            self.grid.removeWidget(tile)
        for i, tile in enumerate(self.tiles):
            self.grid.addWidget(tile, i // cols, i % cols)
        for c in range(self.max_columns):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._flow()


class _Flash(QWidget):
    def __init__(self, target: QWidget):
        super().__init__(target.parentWidget())
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setGeometry(target.geometry().adjusted(-6, -4, 6, 4))
        self.strength = 1.0
        self.show()
        self.raise_()

    def paintEvent(self, _e):
        t = tokens.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(alpha(t.accent, int(220 * self.strength)), 2))
        p.setBrush(alpha(t.accent, int(40 * self.strength)))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), RADIUS["md"], RADIUS["md"])
        p.end()


def flash(target: QWidget, duration: int = 1400) -> None:
    """A glow around target that fades away (a setting found by search)."""
    if target.parentWidget() is None:
        return
    glow = _Flash(target)

    def step(v):
        glow.strength = float(v)
        glow.update()
    anim = QVariantAnimation(glow)
    anim.setDuration(duration)
    anim.setStartValue(1.0)
    anim.setKeyValueAt(0.6, 0.8)
    anim.setEndValue(0.0)
    anim.valueChanged.connect(step)
    anim.finished.connect(glow.deleteLater)
    anim.start()


class SearchButton(QPushButton):
    """Looks like a search box (an icon, a hint, a key to press) and opens a search when clicked."""

    def __init__(self, hint: str, keys: str = "Ctrl K", parent: QWidget | None = None):
        super().__init__(parent)
        self.hint = hint
        self.keys = keys
        self.glyph = None     # a QIcon for the magnifier
        self.setObjectName("searchButton")
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(hint)
        self.setFixedHeight(34)
        self.setAttribute(Qt.WA_Hover)

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self.font())
        return QSize(fm.horizontalAdvance(self.hint) + fm.horizontalAdvance(self.keys) + 90, 34)

    def paintEvent(self, _e):
        t = tokens.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hover = self.underMouse()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(t.subtle if hover else t.line_strong, 1))
        p.setBrush(t.hover if hover else t.field)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        rtl = self.layoutDirection() == Qt.RightToLeft
        fm = QFontMetrics(self.font())
        if self.glyph is not None:
            ix = r.right() - 14 - 16 if rtl else r.left() + 14
            self.glyph.paint(p, QRect(int(ix), int(r.center().y() - 8), 16, 16))
        small = self.font()
        small.setPointSizeF(max(6.5, small.pointSizeF() * 0.8))
        small.setWeight(small.Weight.DemiBold)
        sfm = QFontMetrics(small)
        kw = sfm.horizontalAdvance(self.keys) + 12
        kx = r.left() + 7 if rtl else r.right() - 7 - kw
        key = QRectF(kx, r.center().y() - 10, kw, 20)
        p.setPen(QPen(t.line_strong, 1))
        p.setBrush(t.canvas)
        p.drawRoundedRect(key, 6, 6)
        p.setFont(small)
        p.setPen(t.muted)
        p.drawText(key, Qt.AlignCenter, self.keys)
        p.setFont(self.font())
        p.setPen(t.muted if hover else t.subtle)
        text = QRectF(r.left() + kw + 14, r.top(), r.width() - kw - 50, r.height()) if rtl else \
            QRectF(r.left() + 38, r.top(), r.width() - kw - 50, r.height())
        p.drawText(text, (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignVCenter | Qt.AlignAbsolute,
                   fm.elidedText(self.hint, Qt.ElideRight, int(text.width())))
        if self.hasFocus():
            p.setPen(QPen(t.accent, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), r.height() / 2 - 1, r.height() / 2 - 1)
        p.end()


class IconButton(QPushButton):
    """A square button with only an icon (its words go in the tooltip)."""

    def __init__(self, tip: str = "", size: int = 34, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("iconButton")
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(size, size)
        self.setIconSize(QSize(18, 18))


class _DropdownRow(QStyledItemDelegate):
    """One row of an open Dropdown, drawn from the tokens (Qt's own drawing used the bright accent
    behind the row under the mouse, edge to edge)."""

    def __init__(self, combo: QComboBox):
        super().__init__(combo)
        self.combo = combo

    def sizeHint(self, option, index) -> QSize:
        fm = QFontMetrics(option.font)
        text = str(index.data(Qt.DisplayRole) or "")
        icon = 24 if isinstance(index.data(Qt.DecorationRole), QIcon) else 0
        return QSize(fm.horizontalAdvance(text) + icon + 40, max(32, fm.height() + 14))

    def paint(self, p: QPainter, option, index):
        t = tokens.current()
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        rtl = self.combo.layoutDirection() == Qt.RightToLeft
        enabled = bool(index.flags() & Qt.ItemIsEnabled)
        under_mouse = bool(option.state & (QStyle.State_Selected | QStyle.State_MouseOver)) and enabled
        picked = index.row() == self.combo.currentIndex()
        r = QRectF(option.rect).adjusted(4, 1, -4, -1)
        if under_mouse or picked:
            p.setPen(Qt.NoPen)
            p.setBrush(t.hover if under_mouse else t.accent_soft)
            p.drawRoundedRect(r, RADIUS["sm"], RADIUS["sm"])
        if picked:  # a short accent mark on the leading side, like the sidebar's
            mark = QRectF(r.right() - 4 if rtl else r.left() + 1, r.center().y() - 7, 3, 14)
            p.setPen(Qt.NoPen)
            p.setBrush(t.accent)
            p.drawRoundedRect(mark, 1.5, 1.5)
        x = r.adjusted(14, 0, -14, 0)
        icon = index.data(Qt.DecorationRole)
        if isinstance(icon, QIcon) and not icon.isNull():
            box = QRect(int(x.right() - 16 if rtl else x.left()), int(x.center().y() - 8), 16, 16)
            icon.paint(p, box)
            x = x.adjusted(0, 0, -24, 0) if rtl else x.adjusted(24, 0, 0, 0)
        font = QFont(option.font)
        if picked:
            font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.setPen(t.text if enabled else t.subtle)
        text = QFontMetrics(font).elidedText(str(index.data(Qt.DisplayRole) or ""), Qt.ElideRight, int(x.width()))
        p.drawText(x, (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignVCenter | Qt.AlignAbsolute, text)
        p.restore()


class Dropdown(QComboBox):
    """A list to pick one thing from. Works like any QComboBox; only the open list looks different: it
    drops below, rounded (no square corners or Windows' square shadow around it), with a soft
    highlight under the mouse and an accent mark on the one picked."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        view = QListView()
        view.setObjectName("dropdownList")
        view.setMouseTracking(True)
        view.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.setView(view)
        self.setItemDelegate(_DropdownRow(self))
        self.setMaxVisibleItems(12)
        box = view.window()  # (Qt's frame around the list: see-through, so the rounded corners show)
        box.setObjectName("dropdownBox")
        box.setWindowFlags(box.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        box.setAttribute(Qt.WA_TranslucentBackground)

    def showPopup(self):
        super().showPopup()
        box = self.view().window()
        # opens right below the box, at least as wide as it (Qt's own spot can cover the box)
        below = self.mapToGlobal(self.rect().bottomLeft())
        screen = self.screen().availableGeometry() if self.screen() else None
        geo = box.geometry()
        geo.moveTop(below.y() + 4)
        if self.layoutDirection() == Qt.RightToLeft:
            geo.moveRight(self.mapToGlobal(self.rect().bottomRight()).x())
        else:
            geo.moveLeft(below.x())
        if screen is not None and geo.bottom() > screen.bottom():  # no room below: above the box instead
            geo.moveBottom(self.mapToGlobal(self.rect().topLeft()).y() - 4)
        box.setGeometry(geo)


class ChoiceCard(QPushButton):
    """A big card to pick one of a few things: a ring on the leading side, a title, an optional note
    under it and a small chip on the trailing side. Checkable; put the cards in an exclusive
    QButtonGroup. The arrow keys move between cards in the same layout."""

    def __init__(self, title: str, note: str = "", chip: str = "", parent: QWidget | None = None):
        super().__init__(title, parent)
        self.note = note
        self.chip = chip
        self.setCheckable(True)
        self.setAutoDefault(False)  # (Enter in a dialog presses its main button, not the card)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        self.setObjectName("choiceCard")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _fonts(self) -> tuple[QFont, QFont]:
        big = QFont(self.font())
        big.setPointSizeF(self.font().pointSizeF() * 1.15)
        big.setWeight(QFont.Weight.DemiBold)
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, self.font().pointSizeF() * 0.85))
        return big, small

    def sizeHint(self) -> QSize:
        big, small = self._fonts()
        h = QFontMetrics(big).height() + (QFontMetrics(small).height() + 2 if self.note else 0) + 26
        w = QFontMetrics(big).horizontalAdvance(self.text()) + 120
        if self.chip:
            w += QFontMetrics(small).horizontalAdvance(self.chip) + 30
        return QSize(w, max(52, h))

    def minimumSizeHint(self) -> QSize:
        return QSize(160, self.sizeHint().height())

    _keyboard_focus = False

    def focusInEvent(self, e):
        self._keyboard_focus = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._keyboard_focus = False
        super().focusOutEvent(e)

    def keyPressEvent(self, e):
        step = {Qt.Key_Down: 1, Qt.Key_Right: 1, Qt.Key_Up: -1, Qt.Key_Left: -1}.get(e.key())
        group = self.group()
        if step is None or group is None:
            return super().keyPressEvent(e)
        if self.layoutDirection() == Qt.RightToLeft and e.key() in (Qt.Key_Left, Qt.Key_Right):
            step = -step
        cards = [b for b in group.buttons() if b.isVisible() and b.isEnabled()]
        if self in cards:
            nxt = cards[(cards.index(self) + step) % len(cards)]
            nxt.setFocus(Qt.TabFocusReason)
            nxt.click()

    def paintEvent(self, _e):
        t = tokens.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rtl = self.layoutDirection() == Qt.RightToLeft
        on = self.isChecked()
        hover = self.underMouse()
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(t.accent if on else (t.subtle if hover else t.line_strong), 2 if on else 1))
        p.setBrush(t.accent_soft if on else (t.hover if hover else t.surface))
        p.drawRoundedRect(r, RADIUS["lg"], RADIUS["lg"])
        if self.hasFocus() and self._keyboard_focus and not on:
            p.setPen(QPen(t.accent, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, RADIUS["lg"], RADIUS["lg"])
        # the ring
        cx = r.right() - 26 if rtl else r.left() + 26
        cy = r.center().y()
        p.setPen(QPen(t.accent if on else t.subtle, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), 9, 9)
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(t.accent)
            p.drawEllipse(QPointF(cx, cy), 5, 5)
        big, small = self._fonts()
        left, right = (r.left() + 16, r.right() - 48) if rtl else (r.left() + 48, r.right() - 16)
        if self.chip:  # the chip, on the trailing side
            cw = QFontMetrics(small).horizontalAdvance(self.chip) + 18
            ch = QFontMetrics(small).height() + 6
            chip = QRectF(left if rtl else right - cw, cy - ch / 2, cw, ch)
            p.setPen(Qt.NoPen)
            p.setBrush(alpha(t.accent, 40))
            p.drawRoundedRect(chip, ch / 2, ch / 2)
            p.setFont(small)
            p.setPen(t.text)
            p.drawText(chip, Qt.AlignCenter, self.chip)
            if rtl:
                left += cw + 10
            else:
                right -= cw + 10
        align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute
        bh = QFontMetrics(big).height()
        sh = QFontMetrics(small).height() + 2 if self.note else 0
        top = cy - (bh + sh) / 2
        p.setFont(big)
        p.setPen(t.text)
        p.drawText(QRectF(left, top, right - left, bh), align | Qt.AlignVCenter,
                   QFontMetrics(big).elidedText(self.text(), Qt.ElideRight, int(right - left)))
        if self.note:
            p.setFont(small)
            p.setPen(t.muted)
            p.drawText(QRectF(left, top + bh + 2, right - left, sh), align | Qt.AlignVCenter,
                       QFontMetrics(small).elidedText(self.note, Qt.ElideRight, int(right - left)))
        p.end()


__all__ = ["Switch", "ChoiceCard", "Dropdown", "Group", "SettingRow", "GlowPanel", "IconButton", "mark_rows", "flash", "ResponsiveGrid", "FlowLayout", "SearchButton", "keep_wrapped_text"]
