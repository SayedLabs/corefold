"""Everything that used to open a window of its own shows inside the window it belongs to instead: a question
or a message, a dialog, a menu, the open list of a dropdown and a tip. A dialog rises as a sheet over the
dimmed window; a menu, a list or a tip is a small panel right by what was clicked or pointed at. None of
them is a window, so nothing can flash open on the desktop and everything moves with the app's own window.

    sheet.run(dialog)                  # instead of dialog.exec() (a QMessageBox too)
    sheet.show(dialog)                 # instead of dialog.show(), for one that doesn't wait
    sheet.question(parent, title, text, buttons, default)    # like QMessageBox.question (also warning, ...)
    sheet.menu(qmenu, pos)             # instead of qmenu.exec(pos)
    sheet.tip(widget, pos, text)       # instead of QToolTip.showText

install() (once, at the start) does the rest: tips, the menus of buttons and of text boxes, and keeping
the window behind a sheet out of reach. Native pickers (open, save, folder) stay Windows' own."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, \
    QVariantAnimation
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QIcon, QKeySequence, QPainter, QPainterPath, \
    QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox,
                               QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
                               QScrollBar, QTabBar, QTextEdit, QToolButton, QVBoxLayout, QWidget)

from .atomic import tokens
from .atomic.tokens import MOTION, RADIUS
from .i18n import tr

log = logging.getLogger("corefold")

MARGIN = 24          # (px) between a sheet and its window's edges, at least
SHADOW = 18          # how far a sheet's or a panel's shadow reaches
_provider: Callable[[], QWidget | None] | None = None   # the main window, shown (when nothing else is on screen)
_layers: list["_Layer"] = []                            # the sheets open now, oldest first
_menus: list["_MenuLayer"] = []                         # the menus and lists open now (waiting for a pick)


def _alive(w) -> bool:
    try:
        from shiboken6 import isValid
        return w is not None and isValid(w)
    except ImportError:
        return w is not None


def _animate() -> bool:
    return bool(tokens.animations)


def set_window_provider(fn: Callable[[], QWidget | None]):
    """fn() opens the main window (if it isn't on screen) and gives it: where a sheet goes that has no
    window of its own to go in (a question from the tray, or from the right-click menu in Explorer)."""
    global _provider
    _provider = fn


def host_for(parent: QWidget | None) -> QWidget | None:
    """The window a sheet or a panel for parent goes in (None: there's no window to put it in)."""
    w = parent.window() if _alive(parent) and isinstance(parent, QWidget) else None
    if w is not None and w.isVisible():
        if w.isMinimized():  # (back from the taskbar first: the sheet is in it)
            w.showNormal()
        return w
    if _provider is not None:  # (the main window, opened)
        return _provider()
    active = QApplication.activeWindow()
    return active if active is not None and active.isVisible() else None


def _shadow(p: QPainter, r: QRectF, radius: float, reach: int, strength: int):
    """A soft shadow under the rounded rectangle r (a few rings, darker near it)."""
    p.setPen(Qt.NoPen)
    steps = 6
    for i in range(steps, 0, -1):
        grow = reach * i / steps
        a = int(strength * (1 - i / (steps + 1)) ** 2 / 2)
        p.setBrush(QColor(0, 0, 0, a))
        p.drawRoundedRect(r.adjusted(-grow, -grow + reach * 0.35, grow, grow + reach * 0.35),
                          radius + grow, radius + grow)


# ---- sheets: a dialog or a question over its dimmed window


class _Card(QFrame):
    """The sheet itself: rounded, in the window's own colour, with a fine line around it."""

    def __init__(self, layer: "_Layer"):
        super().__init__(layer)
        self.setObjectName("sheetCard")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)

    def paintEvent(self, _e):
        t = tokens.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(t.line_strong, 1))
        p.setBrush(t.canvas)
        r = RADIUS["xl"]
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), r, r)
        p.end()


class _Layer(QWidget):
    """Covers its window: dims it, keeps it out of reach and holds one sheet (a dialog in it)."""

    def __init__(self, host: QWidget, dlg: QDialog | None, title: str = ""):
        super().__init__(host)
        self.setObjectName("sheetLayer")
        self.host = host
        self.dlg = dlg
        self.dim = 0.0          # 0..1 how far the window is dimmed
        self.rise = 0.0         # px the sheet still has to rise
        self.ghost: QPixmap | None = None   # the sheet as it was, fading away after it closed
        self.ghost_opacity = 1.0
        self.closing = False
        self.anim: QVariantAnimation | None = None
        self.fixed: QSize | None = None    # the size the dialog asked for with resize()
        self.on_closed: Callable[[], None] | None = None
        self.card = _Card(self)
        self.header: QWidget | None = None
        if title:
            self.header = self._make_header(title)
            self.card.lay.addWidget(self.header)
        self.area = QScrollArea(self.card)
        self.area.setObjectName("sheetArea")
        self.area.setFrameShape(QFrame.NoFrame)
        self.area.setWidgetResizable(True)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.area.viewport().setAutoFillBackground(False)
        self.area.setStyleSheet("QScrollArea#sheetArea { background: transparent; }")
        self.card.lay.addWidget(self.area, 1)
        self.setGeometry(host.rect())
        host.installEventFilter(self)

    def _make_header(self, title: str) -> QWidget:
        bar = QWidget(self.card)
        lay = QHBoxLayout(bar)
        rtl = QApplication.layoutDirection() == Qt.RightToLeft  # (margins aren't mirrored: the title's side gets more)
        lay.setContentsMargins(14 if rtl else 22, 14, 22 if rtl else 14, 0)
        lay.setSpacing(8)
        head = QLabel(title, bar)
        head.setObjectName("h2")
        head.setWordWrap(True)
        lay.addWidget(head, 1)
        close = QPushButton(bar)
        close.setObjectName("iconButton")
        close.setFixedSize(30, 30)
        close.setCursor(Qt.PointingHandCursor)
        close.setToolTip(tr("Close"))
        close.setAccessibleName(tr("Close"))
        close.setFocusPolicy(Qt.TabFocus)
        try:
            from . import icons
            close.setIcon(icons.icon("dismiss", tokens.current().muted))
        except Exception:
            close.setText("×")
        close.clicked.connect(self._close_clicked)
        lay.addWidget(close, 0, Qt.AlignTop)
        return bar

    def _close_clicked(self):
        if _alive(self.dlg):
            self.dlg.reject()

    # ---- size and place

    def wanted(self) -> QSize:
        d = self.dlg
        if d is None or not _alive(d):  # (a dialog that deletes itself as it closes may be gone already)
            return QSize(420, 200)
        hint = self.fixed if self.fixed is not None else d.sizeHint()
        hint = hint.expandedTo(d.minimumSize()).expandedTo(d.minimumSizeHint())
        if d.maximumWidth() < hint.width():
            hint.setWidth(d.maximumWidth())
        w = hint.width()
        h = hint.height()
        if d.hasHeightForWidth():
            h = max(h, d.heightForWidth(min(w, self.room().width())))
        if self.header is not None:
            h += self.header.sizeHint().height()
        return QSize(w, h)

    def room(self) -> QSize:
        return QSize(max(200, self.width() - 2 * MARGIN), max(160, self.height() - 2 * MARGIN))

    def target_rect(self) -> QRect:
        want, room = self.wanted(), self.room()
        w, h = min(want.width(), room.width()), min(want.height(), room.height())
        if h < want.height():  # (a scroll bar takes some of the width then)
            w = min(room.width(), w + self.area.verticalScrollBar().sizeHint().width())
        x = (self.width() - w) // 2
        y = max(MARGIN, min((self.height() - h) // 2 - self.height() // 14, self.height() - h - MARGIN))
        return QRect(x, y, w, h)

    def place(self):
        r = self.target_rect()
        self.card.setGeometry(r.translated(0, int(self.rise)))
        self.update()

    def refit(self):
        """The dialog changed what it needs (it showed or hid a part): the sheet glides to its new size."""
        if self.closing or not self.isVisible() or (self.dlg is not None and not _alive(self.dlg)):
            return
        start, end = self.card.geometry(), self.target_rect().translated(0, int(self.rise))
        if start == end:
            return
        if not _animate() or self.anim is not None:
            self.card.setGeometry(end)
            return

        def step(v):
            self.card.setGeometry(QRect(
                int(start.x() + (end.x() - start.x()) * v), int(start.y() + (end.y() - start.y()) * v),
                int(start.width() + (end.width() - start.width()) * v),
                int(start.height() + (end.height() - start.height()) * v)))
            self.update()
        _glide(self, step, MOTION["fast"])

    # ---- coming and going

    def open(self):
        self.setGeometry(self.host.rect())
        self.show()
        self.raise_()
        if not _animate():
            self.dim, self.rise = 1.0, 0.0
            self.place()
            return
        self.dim, self.rise = 0.0, 18.0
        self.place()
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        effect = QGraphicsOpacityEffect(self.card)
        effect.setOpacity(0.0)
        self.card.setGraphicsEffect(effect)

        def step(v):
            self.dim = v
            self.rise = 18.0 * (1 - v)
            if _alive(effect):
                effect.setOpacity(min(1.0, v * 1.4))
            self.place()

        def done():
            self.anim = None
            if _alive(self.card):
                self.card.setGraphicsEffect(None)
            self.refit()
        self.anim = _glide(self, step, MOTION["normal"], done)

    def close_sheet(self):
        """Fades out what the sheet looked like a moment ago (the dialog itself has already gone back)."""
        if self.closing:
            return
        self.closing = True
        if self in _layers:
            _layers.remove(self)
        if self.anim is not None:
            self.anim.stop()
            self.anim = None
        self.host.removeEventFilter(self)
        if self.on_closed is not None:
            self.on_closed()
        if not _animate() or not self.isVisible():
            self.hide()
            self.deleteLater()
            return
        self.card.setGraphicsEffect(None)
        self.ghost_rect = self.card.geometry()
        self.ghost = self.ghost or self.card.grab()
        self.card.hide()
        start_dim = self.dim

        def step(v):
            self.dim = start_dim * (1 - v)
            self.ghost_opacity = 1 - v
            self.rise = 10.0 * v
            self.update()
        _glide(self, step, MOTION["fast"], self.deleteLater, QEasingCurve.InCubic)

    def snapshot(self):
        """What the sheet looks like now (taken just before the dialog in it goes)."""
        try:
            pix = self.card.grab()
            if _alive(self.dlg):
                inner = self.dlg.grab()
                p = QPainter(pix)
                p.drawPixmap(self.dlg.mapTo(self.card, QPoint(0, 0)), inner)
                p.end()
            self.ghost = pix
        except Exception:
            self.ghost = None

    # ---- painting and events

    def paintEvent(self, _e):
        t = tokens.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        scrim = QColor(t.scrim)
        scrim.setAlpha(int(scrim.alpha() * self.dim))
        p.fillRect(self.rect(), scrim)
        if self.closing:
            if self.ghost is not None:
                r = QRectF(self.ghost_rect.translated(0, int(self.rise)))
                p.setOpacity(self.ghost_opacity)
                _shadow(p, r, RADIUS["xl"], SHADOW, 70 if t.dark else 34)
                p.drawPixmap(r.topLeft(), self.ghost)
        elif self.card.isVisible():
            p.setOpacity(min(1.0, self.dim * 1.4))
            _shadow(p, QRectF(self.card.geometry()), RADIUS["xl"], SHADOW, 70 if t.dark else 34)
        p.end()

    def mousePressEvent(self, e):
        e.accept()
        if not self.card.geometry().contains(e.position().toPoint()) and not self.closing:
            self._nudge()

    def mouseReleaseEvent(self, e):
        e.accept()

    def wheelEvent(self, e):
        e.accept()

    def _nudge(self):
        """A click beside the sheet: it shakes a little (the answer is in it)."""
        if not _animate() or self.anim is not None:
            return
        base = self.target_rect()

        def step(v):
            import math
            self.card.move(base.x() + int(6 * math.sin(v * math.pi * 4) * (1 - v)), base.y())
            self.update()
        _glide(self, step, MOTION["slow"], curve=QEasingCurve.Linear)

    def eventFilter(self, obj, e):
        if obj is self.host and e.type() == QEvent.Type.Resize:
            self.setGeometry(self.host.rect())
            if not self.closing:
                self.place()
        elif obj is self.dlg and not self.closing:
            et = e.type()
            if et == QEvent.Type.LayoutRequest:
                QTimer.singleShot(0, self.refit)
            elif et == QEvent.Type.Hide:
                self.snapshot()
                QTimer.singleShot(0, self._dialog_gone)
            elif et == QEvent.Type.WindowTitleChange and self.header is not None:
                head = self.header.findChild(QLabel)
                if head is not None:
                    head.setText(self.dlg.windowTitle())
        return False

    def _dialog_gone(self):
        if not self.closing and (not _alive(self.dlg) or self.dlg.isHidden()):
            give_back(self)
            self.close_sheet()

    def focus_inside(self):
        """The first thing in the sheet that takes the keyboard (its main button if nothing else does)."""
        d = self.dlg
        if not _alive(d):
            return
        fw = QApplication.focusWidget()
        if fw is not None and d.isAncestorOf(fw):
            return
        first = None
        w = d.nextInFocusChain()
        seen = 0
        while w is not None and w is not d and seen < 400:
            seen += 1
            if (d.isAncestorOf(w) and w.isVisibleTo(d) and w.isEnabled()
                    and w.focusPolicy() & Qt.TabFocus):
                first = w
                break
            w = w.nextInFocusChain()
        target = first or d
        target.setFocus(Qt.TabFocusReason if first is not None else Qt.OtherFocusReason)


def _glide(owner, on_value, duration: int, then=None, curve=QEasingCurve.OutCubic) -> QVariantAnimation:
    anim = QVariantAnimation(owner)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(curve)
    anim.valueChanged.connect(on_value)
    if then is not None:
        anim.finished.connect(then)
    anim.start(QVariantAnimation.DeleteWhenStopped)
    return anim


def _has_heading(dlg: QDialog, title: str) -> bool:
    """The dialog already says its title in big letters (then the sheet doesn't say it again)."""
    if not title:
        return True
    want = title.strip().rstrip("…").strip()
    for name in ("h1", "h2", "title", "heading"):
        for label in dlg.findChildren(QLabel, name):
            if label.text().strip().rstrip("…").strip() == want:
                return True
    return False


def embed(dlg: QDialog, host: QWidget) -> _Layer:
    """Puts dlg in a new sheet over host (it's given back with give_back)."""
    title = dlg.windowTitle()
    fixed = QWidget.size(dlg) if dlg.testAttribute(Qt.WA_Resized) else None  # (QWidget's: some name a field "size")
    layer = _Layer(host, dlg, "" if _has_heading(dlg, title) else title)
    layer.fixed = fixed
    layer.home = (dlg.parentWidget(), dlg.windowFlags())
    dlg.setParent(layer.area.viewport(), Qt.Widget)
    dlg.setProperty("sheet", "true")
    dlg.setAttribute(Qt.WA_StyledBackground, False)
    dlg.setAutoFillBackground(False)
    dlg.style().unpolish(dlg)
    dlg.style().polish(dlg)
    layer.area.setWidget(dlg)
    dlg.installEventFilter(layer)
    _layers.append(layer)
    layer.open()
    return layer


def give_back(layer: _Layer):
    """The dialog goes back where it came from (hidden), so it lives on for as long as its owner wants."""
    d = layer.dlg
    if not _alive(d) or getattr(layer, "given_back", False):
        return
    layer.given_back = True
    d.removeEventFilter(layer)
    if layer.area.widget() is d:
        layer.area.takeWidget()
    parent, flags = layer.home
    d.hide()
    d.setProperty("sheet", None)
    d.setParent(parent if _alive(parent) else None, flags)


def run(dlg: QDialog) -> int:
    """Like dlg.exec(), in a sheet over its window. Gives what exec() would."""
    if isinstance(dlg, QMessageBox):
        return _run_box(dlg)
    host = host_for(dlg.parentWidget())
    if host is None:  # (nothing to put it in, as on the very first start before Corefold's window)
        return dlg.exec()
    _bring_forward(host)
    layer = embed(dlg, host)
    QTimer.singleShot(0, layer.focus_inside)
    result = dlg.exec()
    if _alive(layer):
        if not layer.closing:
            layer.snapshot()
        give_back(layer)
        layer.close_sheet()
    return result


def show(dlg: QDialog) -> QDialog:
    """Like dlg.show(), in a sheet over its window that goes when the dialog does."""
    host = host_for(dlg.parentWidget())
    if host is None:
        dlg.show()
        return dlg
    _bring_forward(host)
    layer = embed(dlg, host)
    dlg.show()
    QTimer.singleShot(0, layer.focus_inside)
    return dlg


def open_sheets(host: QWidget | None = None) -> list[_Layer]:
    return [la for la in _layers if host is None or la.host is host]


def waiting() -> QWidget | None:
    """What's waiting for an answer now, newest first: an open menu or list, else the dialog in the newest
    sheet (a question is an _Alert). What the test scripts answer, as activeModalWidget() was."""
    for m in reversed(_menus):
        if _alive(m) and m.loop is not None:
            return m
    for layer in reversed(_layers):
        if _alive(layer) and not layer.closing and layer.dlg is not None and _alive(layer.dlg):
            return layer.dlg
    return None


def dismiss(w: QWidget, accept: bool = False) -> str:
    """Answers what waiting() gave as a person would: a menu closes with nothing picked; a question gets its
    Cancel-like button (with accept, its default one); a dialog is accepted or rejected. Says what it was."""
    if isinstance(w, _MenuLayer):
        w.picked(None)
        return "a menu or list"
    if isinstance(w, _Alert):
        said = f"{w.windowTitle()}: {w.box.text()}"
        buttons = list(w.mine)
        want = next((b for b in buttons if w.mine[b].isDefault()), None) if accept else w.escape
        want = want or (buttons[0] if buttons else None)
        if want is not None:
            w._press(want)
        else:
            w.done(0)
        return said
    said = w.windowTitle()
    if isinstance(w, QDialog):
        w.accept() if accept else w.reject()
    else:
        w.close()
    return said


def _bring_forward(host: QWidget):
    if not host.isActiveWindow():
        host.raise_()
        host.activateWindow()


# ---- questions and messages (what QMessageBox showed in a window of its own)


class _Alert(QDialog):
    """A QMessageBox's question, laid out as a sheet: a mark, the title, the words and the buttons.
    Each button presses the message box's own, so whatever asked reads its answer the way it always did."""

    def __init__(self, box: QMessageBox):
        super().__init__(None)
        self.box = box
        self.setObjectName("sheetAlert")
        if not box.buttons():
            box.addButton(QMessageBox.Ok)
        rtl = QApplication.layoutDirection() == Qt.RightToLeft
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(10)
        title, text, info = box.windowTitle().strip(), box.text().strip(), box.informativeText().strip()
        from .core import APP_NAME
        if not title or title == APP_NAME or title == text:
            title, text = text, info
            info = ""
        top = QHBoxLayout()
        top.setSpacing(14)
        kind = box.icon()
        if kind != QMessageBox.NoIcon:
            top.addWidget(_Mark(kind, self), 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(6)
        head = QLabel(title, self)
        head.setObjectName("h2")
        head.setWordWrap(True)
        words.addWidget(head)
        # (a message often holds outside text, a file's or a cloud file's name: it's only read as HTML when the
        # code asked for that with setTextFormat(Qt.RichText), never because it looks like HTML; see safetext.py)
        rich = box.textFormat() == Qt.RichText
        for part in (text, info):
            if part:
                label = QLabel(part, self)
                label.setWordWrap(True)
                label.setTextFormat(Qt.RichText if rich and part is text else Qt.PlainText)
                label.setObjectName("muted")
                if rich and part is text:
                    label.setOpenExternalLinks(True)
                    label.setTextInteractionFlags(Qt.TextBrowserInteraction)
                else:
                    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                words.addWidget(label)
        top.addLayout(words, 1)
        lay.addLayout(top)
        details = box.detailedText()
        if details:
            more = QPushButton(tr("Show details"), self)
            more.setObjectName("flat")
            more.setCursor(Qt.PointingHandCursor)
            view = QPlainTextEdit(details, self)
            view.setReadOnly(True)
            view.setMinimumHeight(140)
            view.hide()

            def toggle():
                view.setVisible(not view.isVisible())
                more.setText(tr("Hide details") if view.isVisible() else tr("Show details"))
            more.clicked.connect(toggle)
            lay.addWidget(more, 0, Qt.AlignRight if not rtl else Qt.AlignLeft)
            lay.addWidget(view)
        if box.checkBox() is not None:
            cb = box.checkBox()
            mine = QCheckBox(cb.text(), self)
            mine.setChecked(cb.isChecked())
            mine.toggled.connect(cb.setChecked)
            lay.addWidget(mine)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()
        order = {QMessageBox.HelpRole: 0, QMessageBox.ResetRole: 1, QMessageBox.RejectRole: 2,
                 QMessageBox.NoRole: 3, QMessageBox.ActionRole: 4, QMessageBox.ApplyRole: 5,
                 QMessageBox.DestructiveRole: 6, QMessageBox.YesRole: 7, QMessageBox.AcceptRole: 8}
        buttons = sorted(box.buttons(), key=lambda b: order.get(box.buttonRole(b), 4))
        default = box.defaultButton()
        if default is None:
            default = next((b for b in reversed(buttons)
                            if box.buttonRole(b) in (QMessageBox.AcceptRole, QMessageBox.YesRole)), None)
        if default is None and len(buttons) == 1:
            default = buttons[0]
        self.escape = box.escapeButton()
        if self.escape is None:
            self.escape = next((b for b in buttons if box.buttonRole(b) in (QMessageBox.RejectRole, QMessageBox.NoRole)),
                               buttons[0] if len(buttons) == 1 else None)
        self.mine: dict = {}
        for b in buttons:
            mine = QPushButton(b.text().replace("&&", "\0").replace("&", "").replace("\0", "&"), self)
            mine.setCursor(Qt.PointingHandCursor)
            role = box.buttonRole(b)
            if role == QMessageBox.DestructiveRole:
                mine.setObjectName("danger")
            elif b is default:
                mine.setObjectName("primary")
            if b is default:
                mine.setDefault(True)
            mine.setEnabled(b.isEnabled())
            mine.clicked.connect(lambda _=False, b=b: self._press(b))
            row.addWidget(mine)
            self.mine[b] = mine
        lay.addSpacing(6)
        lay.addLayout(row)
        self.setMinimumWidth(380)
        self.setMaximumWidth(560)
        self.setWindowTitle(title)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(max(420, min(hint.width(), 520)), hint.height())

    def _press(self, b: QAbstractButton):
        if _alive(b):
            b.click()  # (the message box answers and closes, as if its own button was pressed)
        self.done(0)

    def reject(self):
        if self.escape is not None:
            self._press(self.escape)
        # (with no Cancel-like button the question must be answered: Esc does nothing)


class _Mark(QWidget):
    """A round mark by a question: ! for a warning, × for a problem, ? for a question, i for a note."""

    def __init__(self, kind, parent):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(36, 36)

    def paintEvent(self, _e):
        t = tokens.current()
        color = {QMessageBox.Warning: t.warning, QMessageBox.Critical: t.danger}.get(self.kind, t.muted)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        soft = QColor(color)
        soft.setAlpha(46 if t.dark else 34)
        p.setPen(Qt.NoPen)
        p.setBrush(soft)
        p.drawEllipse(QRectF(0, 0, 36, 36))
        p.setPen(QPen(color, 2.2, Qt.SolidLine, Qt.RoundCap))
        if self.kind == QMessageBox.Critical:
            p.drawLine(QPointF(13, 13), QPointF(23, 23))
            p.drawLine(QPointF(23, 13), QPointF(13, 23))
        elif self.kind == QMessageBox.Question:
            f = QFont(self.font())
            f.setPixelSize(19)
            f.setWeight(QFont.Weight.Bold)
            p.setFont(f)
            p.drawText(QRectF(0, 0, 36, 36), Qt.AlignCenter, "?")
        else:
            top, bottom = (11, 21) if self.kind == QMessageBox.Warning else (16, 25)
            dot = 25 if self.kind == QMessageBox.Warning else 11
            p.drawLine(QPointF(18, top), QPointF(18, bottom))
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(QPointF(18, dot + 0.5), 1.6, 1.6)
        p.end()


def _run_box(box: QMessageBox) -> int:
    host = host_for(box.parentWidget())
    if host is None:
        return box.exec()
    alert = _Alert(box)
    alert.setParent(box.parentWidget() or None, alert.windowFlags())
    run(alert)
    alert.deleteLater()
    clicked = box.clickedButton()
    if clicked is None:
        return 0
    sb = box.standardButton(clicked)
    return int(sb.value) if hasattr(sb, "value") and sb != QMessageBox.NoButton else box.result()


def _static(icon, parent, title, text, buttons, default):
    box = QMessageBox(icon, title, text, buttons, parent)
    if default != QMessageBox.NoButton:
        box.setDefaultButton(default)
    _run_box(box)
    clicked = box.clickedButton()
    answer = box.standardButton(clicked) if clicked is not None else QMessageBox.NoButton
    if answer == QMessageBox.NoButton:  # (no answer: the way Esc answers)
        esc = box.escapeButton()
        answer = box.standardButton(esc) if esc is not None else QMessageBox.Cancel
    box.deleteLater()
    return answer


def question(parent, title: str, text: str, buttons=QMessageBox.Yes | QMessageBox.No,
             default=QMessageBox.NoButton):
    """Like QMessageBox.question, in a sheet."""
    return _static(QMessageBox.Question, parent, title, text, buttons, default)


def warning(parent, title: str, text: str, buttons=QMessageBox.Ok, default=QMessageBox.NoButton):
    return _static(QMessageBox.Warning, parent, title, text, buttons, default)


def information(parent, title: str, text: str, buttons=QMessageBox.Ok, default=QMessageBox.NoButton):
    return _static(QMessageBox.Information, parent, title, text, buttons, default)


def critical(parent, title: str, text: str, buttons=QMessageBox.Ok, default=QMessageBox.NoButton):
    return _static(QMessageBox.Critical, parent, title, text, buttons, default)


def get_color(initial: QColor, parent: QWidget | None, title: str = "") -> QColor:
    """Like QColorDialog.getColor, in a sheet (an invalid colour when cancelled)."""
    dlg = QColorDialog(initial, parent)
    dlg.setOption(QColorDialog.DontUseNativeDialog, True)
    if title:
        dlg.setWindowTitle(title)
    ok = run(dlg) == QDialog.Accepted
    color = dlg.selectedColor() if ok else QColor()
    dlg.deleteLater()
    return color


# ---- menus and lists: a small panel by what was clicked


@dataclass
class Row:
    text: str = ""
    icon: QIcon | None = None
    checked: bool | None = None     # None: nothing to tick
    enabled: bool = True
    sub: Callable[[], list["Row"]] | None = None
    sep: bool = False
    key: str = ""                   # a keyboard shortcut, shown on the trailing side
    value: object = None            # what picking it gives
    picked: bool = False            # the one chosen now (a dropdown's): a soft grey row, in bold
    tip: str = ""
    kids: list = field(default_factory=list)


ROW_H, SEP_H, PAD = 32, 9, 6


class _Panel(QWidget):
    """The rows of a menu or a list, drawn in one go (light: no widget per row)."""

    def __init__(self, layer: "_MenuLayer", rows: list[Row], min_width: int = 0, parent_panel=None):
        super().__init__(layer)
        self.layer = layer
        self.rows = rows
        self.parent_panel = parent_panel
        self.child: _Panel | None = None
        self.hot = -1
        self.scroll = 0
        self.opacity = 0.0
        self.drop = 6.0
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.rtl = layer.layoutDirection() == Qt.RightToLeft
        self.ensurePolished()  # (the app's font, before it's measured)
        fm = QFontMetrics(self.font())
        self.lead = 0
        if any(r.checked is not None for r in rows):
            self.lead += 22
        self.icons = any(r.icon is not None and not r.icon.isNull() for r in rows)
        if self.icons:
            self.lead += 24
        trail = 0
        if any(r.sub for r in rows):
            trail = 22
        keys = max((fm.horizontalAdvance(r.key) for r in rows if r.key), default=0)
        if keys:
            trail += keys + 24
        bold = QFont(self.font())
        bold.setWeight(QFont.Weight.DemiBold)
        bfm = QFontMetrics(bold)
        text_w = max((bfm.horizontalAdvance(r.text) for r in rows if not r.sep), default=80)
        width = PAD * 2 + 14 + self.lead + text_w + trail + 22
        self.full_h = PAD * 2 + sum(SEP_H if r.sep else ROW_H for r in rows)
        self.resize(max(min_width, min(max(width, 170), 420)), self.full_h)
        self.sub_timer = QTimer(self)
        self.sub_timer.setSingleShot(True)
        self.sub_timer.setInterval(180)
        self.sub_timer.timeout.connect(self._open_sub)
        self.typed = ""
        self.type_timer = QTimer(self)
        self.type_timer.setSingleShot(True)
        self.type_timer.setInterval(700)
        self.type_timer.timeout.connect(lambda: setattr(self, "typed", ""))

    # ---- rows

    def row_rect(self, i: int) -> QRect:
        y = PAD - self.scroll
        for j, r in enumerate(self.rows):
            h = SEP_H if r.sep else ROW_H
            if j == i:
                return QRect(PAD, y, self.width() - 2 * PAD, h)
            y += h
        return QRect()

    def row_at(self, pos: QPoint) -> int:
        y = PAD - self.scroll
        for j, r in enumerate(self.rows):
            h = SEP_H if r.sep else ROW_H
            if y <= pos.y() < y + h and 0 <= pos.x() < self.width():
                return j if not r.sep and r.enabled else -1
            y += h
        return -1

    def usable(self, i: int) -> bool:
        return 0 <= i < len(self.rows) and not self.rows[i].sep and self.rows[i].enabled

    def max_scroll(self) -> int:
        return max(0, self.full_h - self.height())

    def show_row(self, i: int):
        r = self.row_rect(i)
        if r.top() < PAD:
            self.scroll = max(0, self.scroll - (PAD - r.top()))
        elif r.bottom() > self.height() - PAD:
            self.scroll = min(self.max_scroll(), self.scroll + r.bottom() - (self.height() - PAD))

    # ---- painting

    def paintEvent(self, _e):
        t = tokens.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self.opacity)
        p.translate(0, -self.drop)
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(t.line_strong, 1))
        p.setBrush(t.raised)
        p.drawRoundedRect(body, RADIUS["lg"], RADIUS["lg"])
        clip = QPainterPath()
        clip.addRoundedRect(body.adjusted(1, 1, -1, -1), RADIUS["lg"] - 1, RADIUS["lg"] - 1)
        p.setClipPath(clip)
        fm = QFontMetrics(self.font())
        bold = QFont(self.font())
        bold.setWeight(QFont.Weight.DemiBold)
        align = (Qt.AlignRight if self.rtl else Qt.AlignLeft) | Qt.AlignVCenter | Qt.AlignAbsolute
        for i, r in enumerate(self.rows):
            rect = self.row_rect(i)
            if rect.bottom() < 0 or rect.top() > self.height():
                continue
            if r.sep:
                p.setPen(QPen(t.border, 1))
                y = rect.center().y() + 0.5
                p.drawLine(QPointF(rect.left() + 6, y), QPointF(rect.right() - 6, y))
                continue
            rf = QRectF(rect)
            hot = i == self.hot and r.enabled
            if hot or r.picked:
                p.setPen(Qt.NoPen)
                p.setBrush(t.hover if hot else t.select)
                p.drawRoundedRect(rf, RADIUS["sm"], RADIUS["sm"])
            x = rf.adjusted(12, 0, -12, 0)
            color = t.text if r.enabled else t.faint
            if r.checked is not None:
                if r.checked:
                    cx = x.right() - 8 if self.rtl else x.left() + 8
                    cy = x.center().y()
                    p.setPen(QPen(t.accent if r.enabled else t.faint, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    p.drawPolyline([QPointF(cx - 5, cy), QPointF(cx - 1.5, cy + 3.5), QPointF(cx + 5, cy - 4)])
                x = x.adjusted(0, 0, -22, 0) if self.rtl else x.adjusted(22, 0, 0, 0)
            if self.icons:
                if r.icon is not None and not r.icon.isNull():
                    box = QRect(int(x.right() - 16 if self.rtl else x.left()), int(x.center().y() - 8), 16, 16)
                    r.icon.paint(p, box, Qt.AlignCenter, QIcon.Normal if r.enabled else QIcon.Disabled)
                x = x.adjusted(0, 0, -24, 0) if self.rtl else x.adjusted(24, 0, 0, 0)
            if r.sub:
                p.setPen(QPen(t.muted, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                cy = x.center().y()
                if self.rtl:
                    cx = x.left() + 5
                    p.drawPolyline([QPointF(cx + 2.5, cy - 4.5), QPointF(cx - 2, cy), QPointF(cx + 2.5, cy + 4.5)])
                    x = x.adjusted(18, 0, 0, 0)
                else:
                    cx = x.right() - 5
                    p.drawPolyline([QPointF(cx - 2.5, cy - 4.5), QPointF(cx + 2, cy), QPointF(cx - 2.5, cy + 4.5)])
                    x = x.adjusted(0, 0, -18, 0)
            if r.key:
                p.setPen(t.subtle)
                p.setFont(self.font())
                p.drawText(x, (Qt.AlignLeft if self.rtl else Qt.AlignRight) | Qt.AlignVCenter | Qt.AlignAbsolute, r.key)
                kw = fm.horizontalAdvance(r.key) + 18
                x = x.adjusted(kw, 0, 0, 0) if self.rtl else x.adjusted(0, 0, -kw, 0)
            font = bold if r.picked else self.font()
            p.setFont(font)
            p.setPen(color)
            p.drawText(x, align, QFontMetrics(font).elidedText(r.text, Qt.ElideRight, int(x.width())))
        if self.max_scroll():  # (a thin bar: there's more than fits)
            p.setClipping(False)
            p.setPen(Qt.NoPen)
            p.setBrush(tokens.alpha(t.muted, 90))
            h = max(24, self.height() * self.height() // self.full_h)
            y = (self.height() - h) * self.scroll // self.max_scroll()
            p.drawRoundedRect(QRectF(3 if self.rtl else self.width() - 6, y + 4, 3, h - 8), 1.5, 1.5)
        p.end()

    # ---- mouse and keys

    def set_hot(self, i: int):
        if i != self.hot:
            self.hot = i
            self.update()
            if self.child is not None and (i < 0 or i != self.child.from_row):
                self.close_child()
            if self.usable(i) and self.rows[i].sub:
                self.sub_timer.start()
            else:
                self.sub_timer.stop()

    def mouseMoveEvent(self, e):
        i = self.row_at(e.position().toPoint())
        if i >= 0 or self.child is None:
            self.set_hot(i)

    def leaveEvent(self, _e):
        if self.child is None:
            self.set_hot(-1)

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        i = self.row_at(e.position().toPoint())
        if self.usable(i):
            self.activate(i)

    def wheelEvent(self, e):
        if self.max_scroll():
            self.scroll = max(0, min(self.max_scroll(), self.scroll - e.angleDelta().y() // 2))
            self.update()
        e.accept()

    def activate(self, i: int):
        r = self.rows[i]
        if r.sub:
            self.sub_timer.stop()
            self._open_sub(focus=True)
            return
        self.layer.picked(r)

    def _open_sub(self, focus: bool = False):
        i = self.hot
        if not self.usable(i) or not self.rows[i].sub:
            return
        if self.child is not None and self.child.from_row == i:
            if focus:
                self.child.setFocus()
            return
        self.close_child()
        rows = self.rows[i].sub()
        if not rows:
            return
        child = _Panel(self.layer, rows, 0, self)
        child.from_row = i
        self.child = child
        rr = self.row_rect(i)
        top_left = self.mapTo(self.layer, rr.topLeft())
        top_right = self.mapTo(self.layer, rr.topRight())
        self.layer.place(child, QRect(top_left, top_right).adjusted(0, -PAD, 0, 0), side=True)
        child.show()
        child.come_in()
        if focus:
            child.hot = next((j for j in range(len(rows)) if child.usable(j)), -1)
            child.setFocus()

    def close_child(self):
        if self.child is not None:
            self.child.close_child()
            self.child.hide()
            self.child.deleteLater()
            self.child = None
            self.setFocus()

    def come_in(self):
        if not _animate():
            self.opacity, self.drop = 1.0, 0.0
            self.update()
            return

        def step(v):
            self.opacity = v
            self.drop = 6.0 * (1 - v) * (-1 if getattr(self, "above", False) else 1)
            self.update()
        _glide(self, step, MOTION["fast"])

    def keyPressEvent(self, e):
        k = e.key()
        back = Qt.Key_Right if self.rtl else Qt.Key_Left
        forth = Qt.Key_Left if self.rtl else Qt.Key_Right
        if k in (Qt.Key_Down, Qt.Key_Up, Qt.Key_Tab, Qt.Key_Backtab, Qt.Key_Home, Qt.Key_End,
                 Qt.Key_PageDown, Qt.Key_PageUp):
            step = -1 if k in (Qt.Key_Up, Qt.Key_Backtab, Qt.Key_End, Qt.Key_PageUp) else 1
            i = self.hot
            if k in (Qt.Key_Home, Qt.Key_End):
                i = -1 if k == Qt.Key_Home else len(self.rows)
            for _ in range(len(self.rows)):
                i = (i + step) % len(self.rows) if self.rows else -1
                if self.usable(i):
                    break
            if self.usable(i):
                self.show_row(i)
                self.set_hot(i)
                self.sub_timer.stop()
        elif k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            if self.usable(self.hot):
                self.activate(self.hot)
        elif k == forth:
            if self.usable(self.hot) and self.rows[self.hot].sub:
                self._open_sub(focus=True)
        elif k == Qt.Key_Escape or k == back:
            if self.parent_panel is not None:
                self.parent_panel.close_child()
            elif k == Qt.Key_Escape:
                self.layer.picked(None)
        elif e.text() and e.text().isprintable():  # (typing a letter goes to the row that starts with it)
            self.typed += e.text().lower()
            self.type_timer.start()
            for j, r in enumerate(self.rows):
                if self.usable(j) and r.text.lower().lstrip().startswith(self.typed):
                    self.show_row(j)
                    self.set_hot(j)
                    break
        e.accept()


class _MenuLayer(QWidget):
    """Covers the window while a menu or a list is open: a click anywhere else closes it."""

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.setObjectName("menuLayer")
        self.host = host
        self.result: Row | None = None
        self.loop = None
        self.root: _Panel | None = None
        self.setGeometry(host.rect())
        host.installEventFilter(self)

    def place(self, panel: _Panel, anchor: QRect, side: bool = False, above_first: bool = False):
        """Puts panel by anchor (in this layer's place): below it (or above, when there's no room below),
        or beside it for a menu inside a menu. Always inside the window."""
        room = self.rect().adjusted(8, 8, -8, -8)
        h = min(panel.full_h, room.height())
        panel.resize(panel.width(), h)
        rtl = self.layoutDirection() == Qt.RightToLeft
        w = min(panel.width(), room.width())
        if side:
            x = anchor.left() - w + 2 if rtl else anchor.right() - 2
            if x + w > room.right() or x < room.left():
                x = anchor.right() - 2 if rtl else anchor.left() - w + 2
            y = anchor.top()
        else:
            x = anchor.right() - w + 1 if rtl else anchor.left()
            below, above = anchor.bottom() + 4, anchor.top() - 4 - h
            panel.above = (above_first and above >= room.top()) or (below + h > room.bottom() and above >= room.top())
            y = above if panel.above else below
        x = max(room.left(), min(x, room.right() - w + 1))
        y = max(room.top(), min(y, room.bottom() - h + 1))
        panel.setGeometry(x, y, w, h)

    def picked(self, row: Row | None):
        self.result = row
        if self.loop is not None:
            self.loop.quit()

    def mousePressEvent(self, e):
        e.accept()
        self.picked(None)

    def eventFilter(self, obj, e):
        if obj is self.host and e.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Hide,
                                             QEvent.Type.WindowDeactivate):
            self.picked(None)
        return False

    def go(self):
        self.result = None
        self.hide()
        self.show()
        self.raise_()

    def leave(self):
        self.host.removeEventFilter(self)
        if not _animate() or self.root is None or not self.isVisible():
            self.hide()
            self.deleteLater()
            return
        for panel in self.findChildren(_Panel):
            panel.setMouseTracking(False)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        panels = self.findChildren(_Panel)
        start = {p_: p_.opacity for p_ in panels}

        def step(v):
            for p_ in panels:
                if _alive(p_):
                    p_.opacity = start[p_] * (1 - v)
                    p_.update()
        _glide(self, step, MOTION["instant"], self.deleteLater)


def pick(anchor: QWidget | QPoint, rows: list[Row], min_width: int = 0, owner: QWidget | None = None,
         above: bool = False) -> Row | None:
    """Shows rows in a panel by anchor (a widget: right below it; a point on the screen: there) and waits
    for a pick. The row picked, or None."""
    from PySide6.QtCore import QEventLoop
    if not rows:
        return None
    if isinstance(anchor, QWidget):
        host = host_for(anchor)
    else:
        host = host_for(owner) if owner is not None else None
        if host is None:
            under = QApplication.widgetAt(anchor)
            host = under.window() if under is not None else QApplication.activeWindow()
    if host is None:
        return None
    layer = _MenuLayer(host)
    layer.setLayoutDirection(anchor.layoutDirection() if isinstance(anchor, QWidget) else QApplication.layoutDirection())
    panel = _Panel(layer, rows, min_width)
    layer.root = panel
    if isinstance(anchor, QWidget):
        top_left = anchor.mapTo(host, QPoint(0, 0))
        rect = QRect(top_left, anchor.size())
    else:
        rect = QRect(host.mapFromGlobal(anchor), QSize(1, 1))
    layer.place(panel, rect, above_first=above)
    picked_now = next((i for i, r in enumerate(rows) if r.picked), -1)
    if picked_now >= 0:
        panel.show_row(picked_now)
        panel.scroll = min(panel.max_scroll(), max(0, panel.row_rect(picked_now).top() + panel.scroll
                                                   - panel.height() // 2 + ROW_H // 2))
    layer.go()
    panel.show()
    panel.setFocus(Qt.PopupFocusReason)
    if picked_now >= 0:
        panel.hot = picked_now
    panel.come_in()
    loop = QEventLoop()
    layer.loop = loop
    _menus.append(layer)
    loop.exec()
    _menus.remove(layer)
    result = layer.result
    layer.loop = None
    layer.leave()
    return result


def forget(m: QMenu):
    """m.deleteLater(), unless the menu already went with what it belonged to."""
    if _alive(m):
        m.deleteLater()


def _clean(text: str) -> str:
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&").split("\t")[0]


def rows_of(menu: QMenu) -> list[Row]:
    """A QMenu's actions as rows (its "about to show" runs first, as when Qt shows it)."""
    menu.aboutToShow.emit()
    rows: list[Row] = []
    for act in menu.actions():
        if not act.isVisible():
            continue
        if act.isSeparator():
            if rows and not rows[-1].sep:
                rows.append(Row(sep=True))
            continue
        sub = act.menu()
        key = act.shortcut().toString(QKeySequence.NativeText) if not act.shortcut().isEmpty() else ""
        text = _clean(act.text())
        if "\t" in act.text() and not key:
            key = act.text().split("\t", 1)[1]
        rows.append(Row(text=text, icon=act.icon() if not act.icon().isNull() else None,
                        checked=act.isChecked() if act.isCheckable() else None, enabled=act.isEnabled(),
                        sub=(lambda m=sub: rows_of(m)) if sub is not None else None, key=key,
                        value=(act, menu)))
    while rows and rows[-1].sep:
        rows.pop()
    return rows


def menu(m: QMenu, at: QPoint | QWidget | None = None, owner: QWidget | None = None, above: bool = False):
    """Like m.exec(at): the menu in a panel inside the window (at: a point on the screen, or a button to
    open it right below). Returns the action picked (already run)."""
    if at is None:
        at = QCursor.pos()
    owner = owner or m.parentWidget()
    row = pick(at, rows_of(m), owner=owner if isinstance(at, QPoint) else None,
               min_width=at.width() if isinstance(at, QWidget) else 0, above=above)
    if not _alive(m):  # (what it belonged to went while it was open: a list filled again under it)
        return None
    m.aboutToHide.emit()
    if row is None or row.value is None:
        return None
    act, inside = row.value
    act.trigger()
    if _alive(inside):
        inside.triggered.emit(act)
        parent = inside.parentWidget()
        while isinstance(parent, QMenu):  # (each menu around it says it too, as Qt's do)
            parent.triggered.emit(act)
            parent = parent.parentWidget()
    return act


def drop_list(combo: QComboBox):
    """A dropdown's list, opened in a panel right below it."""
    model = combo.model()
    rows = []
    for i in range(combo.count()):
        item_flags = model.flags(model.index(i, combo.modelColumn()))
        icon = combo.itemIcon(i)
        rows.append(Row(text=combo.itemText(i), icon=icon if not icon.isNull() else None,
                        enabled=bool(item_flags & Qt.ItemIsEnabled), picked=i == combo.currentIndex(), value=i))
    row = pick(combo, rows, min_width=combo.width())
    if row is not None and _alive(combo):
        i = row.value
        if i != combo.currentIndex():
            combo.setCurrentIndex(i)
        combo.activated.emit(i)
        combo.textActivated.emit(combo.itemText(i))
    if _alive(combo):
        combo.setFocus(Qt.PopupFocusReason)


# ---- tips: a small label by what's pointed at, inside the window


class _Tip(QWidget):
    def __init__(self, host: QWidget):
        super().__init__(host)
        self.setObjectName("inTip")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.text = ""
        self.opacity = 0.0
        self.owner: QWidget | None = None
        self.area = QRect()      # (in the owner's place) where the pointer has to stay for the tip to stay
        self.watch = QTimer(self)   # (runs only while the tip is up)
        self.watch.setInterval(120)
        self.watch.timeout.connect(self._check)
        self.late = QTimer(self)
        self.late.setSingleShot(True)
        self.late.timeout.connect(self.vanish)
        self.hide()

    def say(self, owner: QWidget, global_pos: QPoint, text: str, area: QRect):
        host = self.parentWidget()
        self.owner, self.area = owner, area
        if text != self.text or not self.isVisible():
            self.text = text
            self.ensurePolished()
            fm = QFontMetrics(self.font())
            box = fm.boundingRect(QRect(0, 0, 340, 2000), Qt.TextWordWrap, text)
            self.resize(box.width() + 20, box.height() + 12)
        local = host.mapFromGlobal(global_pos)
        x = local.x() - (self.width() if self.layoutDirection() == Qt.RightToLeft else 0) + \
            (-4 if self.layoutDirection() == Qt.RightToLeft else 4)
        y = local.y() + 22
        if y + self.height() > host.height() - 6:
            y = local.y() - self.height() - 10
        x = max(6, min(x, host.width() - self.width() - 6))
        y = max(6, y)
        self.move(x, y)
        was = self.isVisible()
        self.show()
        self.raise_()
        self.watch.start()
        self.late.start(10000 + len(text) * 40)
        if not was and _animate():
            self.opacity = 0.0

            def step(v):
                self.opacity = v
                self.update()
            _glide(self, step, MOTION["instant"])
        else:
            self.opacity = 1.0
            self.update()

    def _check(self):
        o = self.owner
        if not _alive(o) or not o.isVisible() or not self.parentWidget().isActiveWindow():
            self.vanish()
            return
        if not self.area.contains(o.mapFromGlobal(QCursor.pos())):
            self.vanish()

    def vanish(self):
        self.watch.stop()
        self.late.stop()
        self.owner = None
        if self.isVisible():
            self.hide()

    def paintEvent(self, _e):
        t = tokens.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self.opacity)
        p.setPen(QPen(t.line_strong, 1))
        p.setBrush(t.raised)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS["sm"], RADIUS["sm"])
        p.setPen(t.text)
        flags = Qt.TextWordWrap | (Qt.AlignRight if self.layoutDirection() == Qt.RightToLeft else Qt.AlignLeft)
        p.drawText(self.rect().adjusted(10, 6, -10, -6), flags | Qt.AlignVCenter, self.text)
        p.end()


def _tip_for(host: QWidget) -> _Tip:
    tip = getattr(host, "_sheet_tip", None)
    if not _alive(tip):
        tip = _Tip(host)
        host._sheet_tip = tip
    return tip


def tip(widget: QWidget, global_pos: QPoint, text: str, area: QRect | None = None):
    """Like QToolTip.showText(global_pos, text, widget): a tip inside widget's window."""
    host = widget.window() if _alive(widget) else None
    if host is None:
        return
    if not text:
        hide_tip(widget)
        return
    _tip_for(host).say(widget, global_pos, text, area if area is not None else widget.rect())


def tip_at(global_pos: QPoint, text: str, widget: QWidget | None = None, rect: QRect | None = None):
    """QToolTip.showText's way round."""
    if widget is not None:
        tip(widget, global_pos, text, rect)


def hide_tip(widget: QWidget | None = None):
    hosts = [widget.window()] if _alive(widget) else QApplication.topLevelWidgets()
    for host in hosts:
        t = getattr(host, "_sheet_tip", None)
        if _alive(t):
            t.vanish()


def _tip_text(obj: QWidget, pos: QPoint) -> tuple[str, QRect]:
    """What a tip says for obj at pos (in obj's place), and where it stays."""
    parent = obj.parentWidget()
    if isinstance(parent, QAbstractItemView) and obj is parent.viewport():
        idx = parent.indexAt(pos)
        if idx.isValid():
            text = idx.data(Qt.ToolTipRole)
            return (str(text) if text else ""), parent.visualRect(idx)
        return "", QRect()
    if isinstance(obj, QTabBar):
        i = obj.tabAt(pos)
        return (obj.tabToolTip(i) if i >= 0 else ""), obj.tabRect(i)
    return obj.toolTip(), obj.rect()


# ---- the one filter that keeps it all in the window


_INPUT = {QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseButtonRelease,
          QEvent.Type.Wheel, QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ContextMenu,
          QEvent.Type.Shortcut, QEvent.Type.DragEnter, QEvent.Type.Drop}
_WATCHED = _INPUT | {QEvent.Type.ToolTip, QEvent.Type.FocusIn, QEvent.Type.Leave}
_GONE_TIP = {QEvent.Type.MouseButtonPress, QEvent.Type.Wheel, QEvent.Type.KeyPress, QEvent.Type.WindowDeactivate}


def _inside(w: QWidget, kinds: tuple) -> QWidget | None:
    while w is not None:
        if isinstance(w, kinds):
            return w
        w = w.parentWidget()
    return None


class _Guard(QObject):
    """Tips, the menus of buttons and text boxes, and nothing behind a sheet taking a click or a key."""

    def eventFilter(self, obj, e):  # noqa: C901 (one place for all of it keeps the cost to one call per event)
        et = e.type()
        if et not in _WATCHED and et not in _GONE_TIP:
            return False
        try:
            return self._filter(obj, e, et)
        except Exception:
            log.debug("sheet filter", exc_info=True)
            return False

    def _filter(self, obj, e, et) -> bool:
        if not isinstance(obj, QWidget):
            if et == QEvent.Type.Shortcut and _layers:
                owner = obj.parent()
                if isinstance(owner, QWidget) and _blocked(owner):
                    return True
            return False
        if et in _GONE_TIP:  # (a click, a key or the wheel: the tip goes)
            t = getattr(obj.window(), "_sheet_tip", None)
            if t is not None and _alive(t) and t.isVisible():
                t.vanish()
        if _layers and et in _INPUT and _blocked(obj):
            return True
        if et == QEvent.Type.FocusIn and _layers:
            layer = _top_layer(obj.window())
            if layer is not None and not layer.isAncestorOf(obj) and not _inside(obj, (_MenuLayer,)):
                QTimer.singleShot(0, layer.focus_inside)
            return False
        if et == QEvent.Type.ToolTip:
            if _inside(obj, (QMenu,)) is not None or obj.window().windowType() == Qt.Popup:
                return False
            text, area = _tip_text(obj, e.pos())
            if not text:
                return False  # (goes on to the widget it's in, as Qt's tips do)
            tip(obj, e.globalPos(), text, area)
            e.accept()
            return True
        if et == QEvent.Type.Leave:
            t = getattr(obj.window(), "_sheet_tip", None)
            if t is not None and _alive(t) and t.isVisible() and t.owner is obj:
                t.vanish()
            return False
        if et == QEvent.Type.ContextMenu:
            return _text_menu(obj, e)
        if et == QEvent.Type.MouseButtonPress and e.button() == Qt.LeftButton:
            return _menu_button(obj)
        if et == QEvent.Type.KeyPress and e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Down) \
                and not e.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
            if isinstance(obj, (QPushButton, QToolButton)) and obj.menu() is not None:
                return _menu_button(obj)
        return False


def _top_layer(host: QWidget) -> _Layer | None:
    for layer in reversed(_layers):
        if layer.host is host and not layer.closing:
            return layer
    return None


def _blocked(w: QWidget) -> bool:
    """w is behind a sheet (in a window that has one open, and not in that sheet or a menu over it)."""
    layer = _top_layer(w.window())
    if layer is None:
        return False
    if w is layer or layer.isAncestorOf(w):
        return False
    if _inside(w, (_MenuLayer, _Tip)) is not None:
        return False
    # a sheet opened from a menu or panel over this one is above it: the top one decides
    return True


def _menu_button(button: QWidget) -> bool:
    if not isinstance(button, (QPushButton, QToolButton)) or button.menu() is None or not button.isEnabled():
        return False
    m = button.menu()
    button.setDown(True)
    try:
        menu(m, button)
    finally:
        if _alive(button):
            button.setDown(False)
    return True


def _text_menu(obj: QWidget, e) -> bool:
    """The right-click menu of a text box (Copy, Paste, ...) and of a scroll bar, in the window."""
    if isinstance(obj, QScrollBar):
        return True  # (Qt's "Scroll here / Top / Bottom": nobody misses it, and it was a window of its own)
    target = None
    if isinstance(obj, QLineEdit) and obj.contextMenuPolicy() == Qt.DefaultContextMenu:
        target = obj
    elif isinstance(obj, QAbstractSpinBox) and obj.contextMenuPolicy() == Qt.DefaultContextMenu:
        target = obj.lineEdit()
    else:
        holder = obj.parentWidget() if isinstance(obj.parentWidget(), (QTextEdit, QPlainTextEdit)) else obj
        if isinstance(holder, (QTextEdit, QPlainTextEdit)) and holder.contextMenuPolicy() == Qt.DefaultContextMenu \
                and (obj is holder or obj is holder.viewport()):
            target = holder
    if target is None or _overridden(target):
        return False
    if isinstance(target, (QTextEdit, QPlainTextEdit)):
        pos = target.viewport().mapFrom(obj, e.pos()) if obj is not target else e.pos()
        m = target.createStandardContextMenu(pos)
    else:
        m = target.createStandardContextMenu()
    m.setParent(target, m.windowFlags())
    menu(m, e.globalPos(), owner=target)
    m.deleteLater()
    return True


def _overridden(w: QWidget) -> bool:
    """w's class has its own contextMenuEvent (it shows its own menu: it's left to it)."""
    for klass in type(w).__mro__:
        if klass in (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QWidget):
            return False
        if "contextMenuEvent" in klass.__dict__:
            return True
    return False


_guard: _Guard | None = None


@contextmanager
def quiet():
    """The filter above steps aside while every widget is restyled: a new look sends each widget a dozen
    events (13,000 for the window), none of them a click, a key or a tip, and each one was a call into
    Python: about a fifth of a dark/light switch (0.74 s down to 0.61 s on Windows, three runs each)."""
    app = QApplication.instance()
    if _guard is None or app is None:
        yield
        return
    app.removeEventFilter(_guard)
    try:
        yield
    finally:
        app.installEventFilter(_guard)


def install(app: QApplication, provider: Callable[[], QWidget | None] | None = None):
    """Once, at the start: tips, button and text box menus in the window, and sheets kept on top."""
    global _guard
    if provider is not None:
        set_window_provider(provider)
    if _guard is None:
        _guard = _Guard(app)
        app.installEventFilter(_guard)


__all__ = ["install", "set_window_provider", "run", "show", "question", "warning", "information", "critical",
           "get_color", "menu", "forget", "quiet", "pick", "Row", "drop_list", "tip", "tip_at", "hide_tip", "open_sheets", "host_for"]
