"""Corefold Clipboard's windows: the small popup its shortcut opens (pick something and it's pasted
where you were typing) and the page in Corefold's window (search, pin, clean up)."""
from __future__ import annotations

import ctypes
import os
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QFont, QFontMetrics, QGuiApplication, QKeySequence, \
    QPainter, QPainterPath, QPixmap, QShortcut
from PySide6.QtWidgets import QAbstractItemView, QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, \
    QFileDialog, QListWidgetItem, QMenu, QMessageBox, QPushButton, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget

from . import i18n, icons, sheet
from .clipboard import CHANGES, HOTKEY_NAMES, Item, change_text, text_item
from .i18n import ltr, tr
from .theme import round_menu
from .timefmt import time_ago

if TYPE_CHECKING:
    from .app import Controller
    from .clipboard import Clipboard

KIND_ICONS = {"text": "text_t", "link": "link", "image": "image", "files": "document_multiple"}
FILTERS = [("", "All"), ("pinned", "Pinned"), ("text", "Text"), ("link", "Links"), ("image", "Pictures"),
           ("files", "Files")]
SHOWN = 300   # rows in a list (search finds older ones)


def shortcut_name(key: str) -> str:
    return HOTKEY_NAMES.get(key) or tr("No shortcut")


def describe(item: Item) -> str:
    """The second line under an item: where it came from and when."""
    bits = []
    if item.kind == "image" and item.width:
        bits.append(f"{item.width} × {item.height}")
    elif item.kind == "files":
        n = item.size or len(item.files)  # (a list holds only the start of a long one)
        bits.append(tr("1 file") if n == 1 else tr("{count} files", count=n))
    if item.app:
        bits.append(ltr(item.app))
    bits.append(time_ago(item.used or item.created))
    return "  ·  ".join(bits)


class Rows(QStyledItemDelegate):
    """Draws a copied item: a small picture or a sign of what it is, its text, and where and when it
    was copied (with a pin if it's pinned)."""

    def __init__(self, clip: "Clipboard", theme, parent=None, tall: int = 56):
        super().__init__(parent)
        self.clip, self.theme, self.tall = clip, theme, tall
        self.thumbs: dict[str, QPixmap] = {}   # by picture file (an id is given again after the newest goes)
        self.signs: dict[tuple, QPixmap] = {}
        self.numbers = False   # the first nine rows show their number (Ctrl + that number pastes it)

    def sizeHint(self, option, index):
        return QSize(100, self.tall)

    def _thumb(self, item: Item) -> QPixmap | None:
        pm = self.thumbs.get(item.image)
        if pm is None:
            path = self.clip.history.thumb_path(item)
            if not path.exists():
                path = self.clip.history.image_path(item)
            pm = QPixmap(str(path))
            if len(self.thumbs) > 300:
                self.thumbs.clear()
            self.thumbs[item.image] = pm
        return None if pm.isNull() else pm

    def _sign(self, name: str, color: QColor, size: int, dpr: float) -> QPixmap:
        key = (name, color.name(), size, dpr)
        if key not in self.signs:
            self.signs[key] = icons.pixmap(name, size, color, dpr)
        return self.signs[key]

    def paint(self, p: QPainter, opt, index):
        item: Item = index.data(Qt.UserRole)
        if item is None:
            return
        t = self.theme
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        rtl = opt.direction == Qt.RightToLeft
        r = opt.rect.adjusted(2, 1, -2, -1)
        if opt.state & QStyle.State_Selected:
            p.setPen(Qt.NoPen)
            soft = QColor(t.accent)
            soft.setAlpha(46 if t.dark else 34)
            p.setBrush(soft)
            p.drawRoundedRect(r, 8, 8)
        elif opt.state & QStyle.State_MouseOver:
            p.setPen(Qt.NoPen)
            p.setBrush(t.hover or t.field)
            p.drawRoundedRect(r, 8, 8)
        box = self.tall - 16
        lead = r.right() - 8 - box if rtl else r.left() + 8
        tile = QRect(lead, r.top() + (r.height() - box) // 2, box, box)
        dpr = p.device().devicePixelRatioF() if p.device() else 1.0
        shown = self._thumb(item) if item.kind == "image" else None
        if shown is not None:
            clip = QPainterPath()
            clip.addRoundedRect(tile, 6, 6)
            p.setClipPath(clip)
            p.fillRect(tile, t.field)
            scaled = shown.size().scaled(tile.size(), Qt.KeepAspectRatioByExpanding)
            target = QRect(0, 0, scaled.width(), scaled.height())
            target.moveCenter(tile.center())
            p.drawPixmap(target, shown)
            p.setClipping(False)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(t.field)
            p.drawRoundedRect(tile, 6, 6)
            s = 18
            sign = self._sign(KIND_ICONS.get(item.kind, "document"), t.muted, s, dpr)
            p.drawPixmap(QRect(tile.center().x() - s // 2, tile.center().y() - s // 2, s, s), sign)
        number = str(index.row() + 1) if self.numbers and index.row() < 9 else ""
        pin_w = (20 if item.pinned else 0) + (18 if number else 0)
        if rtl:
            text = QRect(r.left() + 10 + pin_w, r.top() + 6, tile.left() - 10 - (r.left() + 10 + pin_w), r.height() - 12)
        else:
            text = QRect(tile.right() + 10, r.top() + 6, r.right() - 10 - pin_w - (tile.right() + 10), r.height() - 12)
        title = item.title() or (tr("Picture") if item.kind == "image" else "")
        f = QFont(opt.font)
        p.setFont(f)
        fm = QFontMetrics(f)
        small = QFont(opt.font)
        small.setPointSizeF(max(7.0, f.pointSizeF() - 1.5))
        sfm = QFontMetrics(small)
        line_h = fm.height()
        top = text.top() + max(0, (text.height() - line_h - sfm.height() - 2) // 2)
        align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute | Qt.AlignVCenter
        p.setPen(t.text)
        p.drawText(QRect(text.left(), top, text.width(), line_h), align, fm.elidedText(title, Qt.ElideRight, text.width()))
        p.setFont(small)
        p.setPen(t.muted)
        p.drawText(QRect(text.left(), top + line_h + 2, text.width(), sfm.height()), align,
                   sfm.elidedText(describe(item), Qt.ElideRight, text.width()))
        if item.pinned:
            s = 14
            x = r.left() + 10 if rtl else r.right() - 10 - s
            p.drawPixmap(QRect(x, r.center().y() - s // 2, s, s), self._sign("pin", t.accent, s, dpr))
        if number:
            end = 10 + (20 if item.pinned else 0)
            spot = QRect(r.left() + end if rtl else r.right() - end - 14, r.top(), 14, r.height())
            p.setFont(small)
            faint = QColor(t.muted)
            if not opt.state & QStyle.State_Selected:
                faint.setAlpha(150)
            p.setPen(faint)
            p.drawText(spot, Qt.AlignCenter, number)
        p.restore()


class ClipList(QListWidget):
    """The list of copies. Enter (or a click, in the popup) uses one; Delete forgets it."""
    used = Signal(object, bool)   # item, as plain text
    removed = Signal(object)
    pinned = Signal(object, bool)
    text_used = Signal(str)       # edited or changed text, to paste (or copy) instead of the item

    def __init__(self, clip: "Clipboard", theme, tall: int, click_uses: bool, controller=None):
        super().__init__()
        self.clip = clip
        self.controller = controller
        self.setObjectName("clips")
        self.rows = Rows(clip, theme, self, tall)
        self.setItemDelegate(self.rows)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)  # Ctrl+click: several, pasted together
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.click_uses = click_uses
        if click_uses:
            self.itemClicked.connect(self._clicked)
        self.itemActivated.connect(lambda it: self.used.emit(it.data(Qt.UserRole), False))
        self.menu_open = False

    def fill(self, items: list[Item], keep: int | None = None):
        self.setUpdatesEnabled(False)
        self.clear()
        for item in items:
            row = QListWidgetItem()
            row.setData(Qt.UserRole, item)
            row.setToolTip(_tip(item))
            self.addItem(row)
        if self.count():
            want = 0
            if keep is not None:
                want = next((i for i in range(self.count()) if self.item(i).data(Qt.UserRole).id == keep), 0)
            self.setCurrentRow(want)
        self.setUpdatesEnabled(True)

    def _clicked(self, it):
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.keyboardModifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
            return  # picking several
        self.used.emit(it.data(Qt.UserRole), False)

    def picked_texts(self) -> list[Item]:
        """The texts picked with Ctrl+click, in the order they were picked."""
        return [it.data(Qt.UserRole) for it in self.selectedItems() if it.data(Qt.UserRole).kind in ("text", "link")]

    def _use_together(self, items: list[Item]):
        self.text_used.emit("\n".join(self._full_text(i).rstrip("\r\n") for i in items))

    def current(self) -> Item | None:
        it = self.currentItem()
        return it.data(Qt.UserRole) if it is not None else None

    def keyPressEvent(self, e):
        item = self.current()
        if item is not None:
            if e.key() in (Qt.Key_Return, Qt.Key_Enter):
                several = self.picked_texts()
                if len(several) > 1:
                    self._use_together(several)
                else:
                    self.used.emit(item, bool(e.modifiers() & Qt.ShiftModifier))
                return
            if e.key() == Qt.Key_Delete:
                self.removed.emit(item)
                return
            if e.key() == Qt.Key_P and e.modifiers() & Qt.ControlModifier:
                self.pinned.emit(item, not item.pinned)
                return
        super().keyPressEvent(e)

    def _menu(self, pos):
        it = self.itemAt(pos)
        if it is None:
            return
        item: Item = it.data(Qt.UserRole)
        menu = QMenu(self)
        round_menu(menu)
        several = self.picked_texts() if it.isSelected() else []
        if len(several) > 1:
            menu.addAction(tr("Paste these together ({count})", count=len(several)) if self.click_uses else
                           tr("Copy these together ({count})", count=len(several)),
                           lambda: self._use_together(several))
            menu.addSeparator()
        menu.addAction(tr("Paste") if self.click_uses else tr("Copy"), lambda: self.used.emit(item, False))
        if item.kind in ("text", "link") and item.html:
            menu.addAction(tr("Paste as plain text") if self.click_uses else tr("Copy as plain text"),
                           lambda: self.used.emit(item, True))
        if item.kind in ("text", "link"):
            menu.addAction(tr("Edit, then paste…") if self.click_uses else tr("Edit, then copy…"),
                           lambda: self._edit(item))
            change = menu.addMenu(tr("Change it"))
            round_menu(change)
            for how, name in CHANGES:
                change.addAction(tr(name), lambda h=how: self._change(item, h))
            if item.pinned:
                menu.addAction(tr("Change the saved text…"), lambda: self._resave(item))
        if item.kind == "link":
            menu.addAction(tr("Open the link"), lambda: QDesktopServices.openUrl(QUrl(_web(item.text))))
        if item.kind == "image":
            menu.addAction(tr("Copy the text in it"), lambda: self._read_text(item))
            menu.addAction(tr("Save as…"), lambda: self._save_picture(item))
        if item.kind == "files":
            from pathlib import Path

            from .winutil import reveal_in_explorer
            menu.addAction(tr("Show in folder"), lambda: reveal_in_explorer(Path(item.files[0])))
        menu.addAction(tr("Unpin") if item.pinned else tr("Pin"), lambda: self.pinned.emit(item, not item.pinned))
        menu.addSeparator()
        menu.addAction(tr("Delete"), lambda: self.removed.emit(item))
        self.menu_open = True
        try:
            sheet.menu(menu, self.viewport().mapToGlobal(pos))
        finally:
            self.menu_open = False


    def _full_text(self, item: Item) -> str:
        if item.partial and item.id:
            try:
                return (self.clip.history.get(item.id) or item).text
            except Exception:
                pass
        return item.text

    def _edit(self, item: Item):
        text = edit_text(self, self._full_text(item), tr("Paste") if self.click_uses else tr("Copy"))
        if text is not None and text.strip():
            self.text_used.emit(text)

    def _resave(self, item: Item):
        text = edit_text(self, self._full_text(item), tr("Save"), tr("Change the saved text…").rstrip("…"))
        if text is not None and text.strip():
            self.clip.save_text(text, instead_of=item)

    def _change(self, item: Item, how: str):
        self.text_used.emit(change_text(self._full_text(item), how))

    def _read_text(self, item: Item):
        """Windows' own text reading, like Capture's "Copy text"."""
        from .converters import catalog
        path = self.clip.history.image_path(item)
        tool = next((t for kind in catalog().values() for t in kind if t.key.endswith(":ocr")), None)
        c = self.controller
        if tool is None or c is None:
            sheet.information(self, tr("Copy the text in it"), tr("Reading text needs Windows 10 or 11"))
            return
        if not path.exists():
            sheet.information(self, tr("Copy the text in it"), tr("This picture isn't there any more."))
            return
        import shutil
        import tempfile
        from pathlib import Path
        copy = Path(tempfile.mkdtemp(prefix="corefold-clip-")) / "picture.png"
        shutil.copy2(path, copy)
        c.start_job(tool, [copy])

    def _save_picture(self, item: Item):
        import shutil
        from pathlib import Path

        from .capture import new_name, screenshots_folder
        path = self.clip.history.image_path(item)
        start = new_name(screenshots_folder().parent, ".png", "Copied picture")
        name, _ = QFileDialog.getSaveFileName(self, tr("Save as"), str(start), "PNG (*.png)")
        if not name:
            return
        try:
            shutil.copy2(path, Path(name))
        except OSError as e:
            sheet.warning(self, tr("Couldn't save"), str(e))


def _web(text: str) -> str:
    text = text.strip()
    return text if "://" in text else "https://" + text


def _tip(item: Item) -> str:
    if item.kind == "image":
        return ""
    text = item.text if len(item.text) < 1200 else item.text[:1200] + "…"
    return text


# ----------------------------------------------------------------------------- the popup

class QuickPaste(QWidget):
    """Opens with the shortcut, near where you're typing: type to search, pick with the arrow keys
    and Enter (or a click), and it's pasted. Esc or clicking elsewhere closes it."""

    def __init__(self, controller: "Controller", clip: "Clipboard"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.c, self.clip = controller, clip
        self.target = 0
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(tr("Clipboard"))
        self.resize(400, 480)
        frame = QFrame(self)
        frame.setObjectName("clipPopup")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(10, 10, 10, 8)
        lay.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("clipSearch")
        self.search.setPlaceholderText(tr("Search your clipboard"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.search_timer.start(90))
        self.search.installEventFilter(self)
        lay.addWidget(self.search)
        self.paused = QPushButton(tr("Paused: new copies aren't kept. Resume"))
        self.paused.setObjectName("flat")
        self.paused.setCursor(Qt.PointingHandCursor)
        self.paused.clicked.connect(self._resume)
        lay.addWidget(self.paused)
        self.list = ClipList(clip, controller.theme, 54, click_uses=True, controller=controller)
        self.list.rows.numbers = True
        self.list.used.connect(self.use)
        self.list.removed.connect(self._remove)
        self.list.pinned.connect(self._pin)
        self.list.text_used.connect(lambda text: self.use(text_item(text)))
        lay.addWidget(self.list, 1)
        self.empty = QLabel()
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        lay.addWidget(self.empty, 1)
        foot = QHBoxLayout()
        foot.setSpacing(6)
        self.hint = QLabel(tr("Enter pastes  ·  Ctrl+1…9: that row  ·  Shift+Enter: plain text  ·  Ctrl+P pins"))
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)  # (two lines in longer languages, never wider than the popup)
        small = self.hint.font()
        small.setPointSizeF(8)
        self.hint.setFont(small)
        foot.addWidget(self.hint, 1)
        more = QPushButton()
        more.setObjectName("flat")
        more.setCursor(Qt.PointingHandCursor)
        more.setToolTip(tr("Open the Clipboard in Corefold"))
        more.setIcon(icons.icon("settings", controller.theme.muted))
        more.clicked.connect(self._open_page)
        foot.addWidget(more)
        lay.addLayout(foot)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.refresh)
        for n in range(1, 10):  # Ctrl+1…9 pastes that row
            sc = QShortcut(QKeySequence(f"Ctrl+{n}"), self)
            sc.activated.connect(lambda n=n: self._use_row(n - 1))
        QShortcut(QKeySequence("Esc"), self).activated.connect(self.cancel)
        self.clip.changed.connect(self._changed)

    def eventFilter(self, obj, e):
        if obj is self.search and e.type() == QEvent.KeyPress:
            key = e.key()
            if key in (Qt.Key_Down, Qt.Key_Up, Qt.Key_PageDown, Qt.Key_PageUp):
                self.list.keyPressEvent(e)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                item = self.list.current()
                if item is not None:
                    self.use(item, bool(e.modifiers() & Qt.ShiftModifier))
                return True
            if key == Qt.Key_P and e.modifiers() & Qt.ControlModifier:
                item = self.list.current()
                if item is not None:
                    self._pin(item, not item.pinned)
                return True
            if key == Qt.Key_Delete and not self.search.text():
                item = self.list.current()
                if item is not None:
                    self._remove(item)
                return True
        return super().eventFilter(obj, e)

    def changeEvent(self, e):
        if e.type() == QEvent.ActivationChange and not self.isActiveWindow() and self.isVisible() \
                and not self.list.menu_open:
            QTimer.singleShot(0, self._hide_if_away)
        super().changeEvent(e)

    def _hide_if_away(self):
        if not self.isActiveWindow() and not self.list.menu_open and QApplication.activeModalWidget() is None:
            self.hide()

    def open(self, target: int):
        """Shows it near the caret (or the mouse), ready to type."""
        self.target = target
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.refresh()
        self._place(target)
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()
        _bring_to_front(int(self.winId()))

    def _place(self, target: int):
        spot = caret_point(target) or QCursor.pos()
        screen = QGuiApplication.screenAt(spot) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        w, h = self.width(), self.height()
        x = spot.x() - (w - 40 if i18n.is_rtl() else 40)
        y = spot.y() + 8
        if y + h > area.bottom():
            y = spot.y() - h - 28
        x = max(area.left() + 8, min(x, area.right() - w - 8))
        y = max(area.top() + 8, min(y, area.bottom() - h - 8))
        self.move(x, y)

    def refresh(self):
        keep = self.list.current()
        try:
            items = self.clip.history.items(self.search.text(), limit=SHOWN)
        except Exception:
            items = []
        self.list.fill(items, keep.id if keep is not None and self.search.text() else None)
        self.paused.setVisible(bool(self.c.settings.clip_paused))
        nothing = not items
        self.list.setVisible(not nothing)
        self.empty.setVisible(nothing)
        if nothing:
            self.empty.setText(tr("Nothing matches.") if self.search.text().strip()
                               else tr("Things you copy show up here."))

    def _changed(self):
        if self.isVisible():
            self.refresh()

    def use(self, item: Item, plain: bool = False):
        self.hide()
        if not self.clip.paste(item, self.target, plain):
            self.c.tell(tr("Couldn't paste"), tr("This picture isn't there any more."))
        elif not self.clip.pasted:
            self.c.say(tr("Copied"), tr("Paste it anywhere with Ctrl+V."))

    def hideEvent(self, e):
        self.list.clear()  # (nothing is held while it's closed)
        self.list.rows.thumbs.clear()
        self.c.popup_closed()
        super().hideEvent(e)

    def cancel(self):
        """Esc: closes it and goes back to where you were typing (Windows would otherwise bring up
        whichever window happens to be next, and your typing would land somewhere else)."""
        self.hide()
        if self.target:
            _bring_to_front(self.target)

    def _use_row(self, row: int):
        if 0 <= row < self.list.count():
            self.use(self.list.item(row).data(Qt.UserRole))

    def _remove(self, item: Item):
        self.clip.delete(item)

    def _pin(self, item: Item, on: bool):
        self.clip.pin(item, on)

    def _resume(self):
        self.c.set_clipboard_paused(False)
        self.refresh()

    def _open_page(self):
        self.hide()
        self.c.show_window("Clipboard")

    def apply_theme(self, theme):
        self.list.rows.theme = theme
        self.list.rows.signs.clear()
        self.list.viewport().update()


def caret_point(hwnd: int) -> QPoint | None:
    """Where the text cursor is in that window (on screen, below it), if the app shows a standard one."""
    if os.name != "nt" or not hwnd:
        return None
    try:
        import ctypes.wintypes as wt

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hwndActive", wt.HWND), ("hwndFocus", wt.HWND),
                        ("hwndCapture", wt.HWND), ("hwndMenuOwner", wt.HWND), ("hwndMoveSize", wt.HWND),
                        ("hwndCaret", wt.HWND), ("rcCaret", wt.RECT)]
        u = ctypes.windll.user32
        u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        tid = u.GetWindowThreadProcessId(hwnd, None)
        info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        if not tid or not u.GetGUIThreadInfo(tid, ctypes.byref(info)) or not info.hwndCaret:
            return None
        pt = wt.POINT(info.rcCaret.left, info.rcCaret.bottom)
        if not u.ClientToScreen(info.hwndCaret, ctypes.byref(pt)):
            return None
        return _from_native(pt.x, pt.y)
    except Exception:
        return None


def _from_native(x: int, y: int) -> QPoint | None:
    """A point in Windows' pixels as Qt sees it (screens can have different scaling)."""
    for screen in QGuiApplication.screens():
        g, ratio = screen.geometry(), screen.devicePixelRatio()
        native = QRect(g.x(), g.y(), round(g.width() * ratio), round(g.height() * ratio))
        if native.contains(x, y):
            return QPoint(g.x() + round((x - g.x()) / ratio), g.y() + round((y - g.y()) / ratio))
    return None


def edit_text(parent, text: str, ok_text: str, title: str = "") -> str | None:
    """A small box to change copied text before it's used. None if cancelled."""
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit
    d = QDialog(parent)
    d.setWindowTitle(title or (tr("Edit, then paste…").rstrip("…") if ok_text == tr("Paste") else
                               tr("Edit, then copy…").rstrip("…")))
    d.setWindowFlag(Qt.WindowStaysOnTopHint)
    d.resize(460, 300)
    lay = QVBoxLayout(d)
    box = QPlainTextEdit(text)
    box.selectAll()
    lay.addWidget(box, 1)
    buttons = QDialogButtonBox()
    ok = buttons.addButton(ok_text, QDialogButtonBox.AcceptRole)
    ok.setObjectName("primary")
    buttons.addButton(tr("Cancel"), QDialogButtonBox.RejectRole)
    buttons.accepted.connect(d.accept)
    buttons.rejected.connect(d.reject)
    lay.addWidget(buttons)
    QShortcut(QKeySequence("Ctrl+Return"), d).activated.connect(d.accept)
    box.setFocus()
    return box.toPlainText() if sheet.run(d) == QDialog.Accepted else None


def _bring_to_front(hwnd: int):
    if os.name != "nt":
        return
    try:
        u = ctypes.windll.user32
        u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        u.SetForegroundWindow(hwnd)
    except Exception:
        pass


# ----------------------------------------------------------------------------- the page in the window

class ClipboardPage(QWidget):
    """Corefold Clipboard in the window: everything kept, searchable, with pins and filters."""

    def __init__(self, controller: "Controller", header):
        super().__init__()
        self.c = controller
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(12)
        lay.addLayout(header)
        self.key_hint = QLabel()
        self.key_hint.setObjectName("muted")
        self.key_hint.setWordWrap(True)
        lay.addWidget(self.key_hint)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search your clipboard"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.timer.start(120))
        row.addWidget(self.search, 1)
        self.kind = ""
        self.filters = QButtonGroup(self)
        self.filters.setExclusive(True)
        for key, text in FILTERS:
            b = QPushButton(tr(text))
            b.setObjectName("subtab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(key == "")
            b.clicked.connect(lambda _=False, k=key: self._filter(k))
            self.filters.addButton(b)
            row.addWidget(b)
        lay.addLayout(row)
        self.paused = QLabel(tr("Paused: new copies aren't kept."))
        self.paused.setObjectName("warning")
        lay.addWidget(self.paused)
        self.list = None
        self.empty = QLabel()
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setMinimumHeight(80)  # (the smallest window still has room for the buttons below)
        lay.addWidget(self.empty, 1)
        if self.clip is not None:
            self.list = ClipList(self.clip, controller.theme, 60, click_uses=False, controller=controller)
            self.list.setObjectName("clipsPage")
            self.list.used.connect(self._copy)
            self.list.removed.connect(lambda item: self.clip.delete(item))
            self.list.pinned.connect(lambda item, on: self.clip.pin(item, on))
            self.list.text_used.connect(lambda text: self._copy(text_item(text), True))
            lay.addWidget(self.list, 1)
            self.clip.changed.connect(self._changed)
        foot = QHBoxLayout()
        self.count = QLabel()
        self.count.setObjectName("muted")
        foot.addWidget(self.count)
        foot.addStretch()
        self.save_btn = QPushButton(tr("Save a text…"), self)  # (shown before it's laid out: never a window of its own)
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setToolTip(tr("Keep a text you use often (an address, a reply) pinned at the top, "
                                    "ready to paste"))
        self.save_btn.clicked.connect(self._new_text)
        self.save_btn.setVisible(self.clip is not None)
        foot.addWidget(self.save_btn)
        self.pause_btn = QPushButton()
        self.pause_btn.setCursor(Qt.PointingHandCursor)
        self.pause_btn.clicked.connect(lambda: self.c.set_clipboard_paused(not self.c.settings.clip_paused))
        foot.addWidget(self.pause_btn)
        self.clear_btn = QPushButton(tr("Clear…"))
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self._clear)
        foot.addWidget(self.clear_btn)
        lay.addLayout(foot)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    @property
    def clip(self):
        return self.c.clip

    def _filter(self, key: str):
        self.kind = key
        self.refresh()

    def _changed(self):
        if self.isVisible():
            self.timer.start(150)

    def refresh(self):
        s = self.c.settings
        key = s.clip_shortcut
        self.key_hint.setText(tr("Press {key} in any app to open your clipboard, then pick what to paste.",
                                 key=shortcut_name(key)) if key != "off" else
                              tr("Pick a shortcut in Settings to open your clipboard from any app."))
        self.paused.setVisible(bool(s.clip_paused))
        self.pause_btn.setText(tr("Resume") if s.clip_paused else tr("Pause"))
        if self.list is None:
            self.empty.setText(tr("Things you copy show up here."))
            return
        try:
            items = self.clip.history.items(self.search.text(), self.kind, limit=SHOWN)
            total = self.clip.history.count()
        except Exception:
            items, total = [], 0
        keep = self.list.current()
        self.list.fill(items, keep.id if keep is not None else None)
        self.list.setVisible(bool(items))
        self.empty.setVisible(not items)
        searching = bool(self.search.text().strip() or self.kind)
        self.empty.setText(tr("Nothing matches.") if searching else tr("Things you copy show up here."))
        self.count.setText(tr("1 item") if total == 1 else tr("{count} items", count=total))
        self.clear_btn.setEnabled(total > 0)

    def _copy(self, item: Item, plain: bool):
        if self.clip.copy(item, plain):
            self.c.say(tr("Copied"), tr("Paste it anywhere with Ctrl+V."))

    def _new_text(self):
        text = edit_text(self, "", tr("Save"), tr("Save a text…").rstrip("…"))
        if text is not None and text.strip():
            self.clip.save_text(text)
            self.c.say(tr("Saved"), tr("It's pinned at the top of your clipboard."))

    def _clear(self):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("Clear the clipboard history?"))
        box.setText(tr("Everything kept is forgotten, except what you pinned."))
        yes = box.addButton(tr("Clear"), QMessageBox.DestructiveRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        sheet.run(box)
        if box.clickedButton() is yes:
            self.clip.clear()

    def apply_theme(self, theme):
        if self.list is not None:
            self.list.rows.theme = theme
            self.list.rows.signs.clear()
            self.list.viewport().update()
