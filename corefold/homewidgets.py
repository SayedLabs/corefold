"""The widgets on the Home page: a box for each thing a person does often, in the sizes and the order they
choose (homelist.py lists the kinds and works out where each one goes).

Light on purpose: a widget is only made while the Home page is, nothing in here has a timer, and what a
widget has to look up (how many files wait in Downloads, which clouds are on this PC) is looked up once,
off the window's thread, and kept for a while. A widget of a tool that's switched off or resting for now
isn't made at all."""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QSize, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QDrag, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QProgressBar, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import homelist, icons, work
from . import tools as all_tools
from .atomic import tokens
from .core import human_size, short_name
from .i18n import ltr, tr
from .timefmt import locale

UNIT_H = 148     # one row of the grid
SPACING = 12
WIDE_FROM = 700  # the board's width from which it has four columns (two below it)
MIME = "application/x-corefold-widget"
KEEP_LOOKS = 120  # seconds a look-up (files waiting, clouds found, drives) is kept before it's done again

# the PDF widget's buttons, in the order they're shown (the same names as on the PDF page)
PDF_ACTIONS = (("edit", "Edit content", "text_edit_style"), ("merge", "Merge PDFs", "merge"),
               ("split", "Split", "split_vertical"), ("compress", "Compress", "arrow_minimize"),
               ("organize", "Arrange pages", "re_order_dots_vertical"), ("pictures", "Pictures to PDF",
                                                                         "image_multiple"),
               ("lock", "Lock", "lock_closed"), ("topictures", "PDF to pictures", "image"))
# the folders a shortcut can name by what they are (they're found wherever they really are)
KNOWN_FOLDERS = {"downloads": "Downloads", "documents": "Documents", "desktop": "Desktop", "pictures": "Pictures",
                 "music": "Music", "videos": "Videos"}
# the Corefold pages a shortcut can open besides the tools' own
APP_PAGES = {"History": "History", "General": "Settings", "Updates": "Updates"}

_looks: dict[str, tuple[float, object]] = {}   # what was looked up lately: key -> (when, what)
_looking: dict[str, list] = {}                   # look-ups on their way: key -> who waits for them
_jobs: dict[str, object] = {}                    # (their threads, kept until their answer is in)


def _look(key: str, fn, then):
    """Calls then(what fn found) on the window's thread: right away with what was found lately, or once fn,
    run on its own thread, is done (several widgets asking at once share one look-up)."""
    kept = _looks.get(key)
    if kept is not None and time.monotonic() - kept[0] < KEEP_LOOKS:
        then(kept[1])
        return
    if key in _looking:
        _looking[key].append(then)
        return
    _looking[key] = [then]

    def done(result):
        _jobs.pop(key, None)
        what = None if isinstance(result, Exception) else result
        _looks[key] = (time.monotonic(), what)
        for fn_ in _looking.pop(key, []):
            fn_(what)
    _jobs[key] = work.run(lambda _p: fn(), lambda _: None, done, name="corefold-home-look")


def forget_looks():
    """After a sort or a new cloud: the widgets look again the next time they're made."""
    _looks.clear()


class Fit(QLabel):
    """One line that's shortened with "…" when the widget is narrow (and never makes its column wider)."""

    def __init__(self, text: str = ""):
        super().__init__()
        self.full = text
        self.setMinimumWidth(1)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setToolTip("")
        self._fit()

    def setText(self, text: str):  # noqa: N802 (Qt's name)
        self.full = text
        self._fit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self):
        shown = self.fontMetrics().elidedText(self.full, Qt.ElideRight, max(10, self.width()))
        if shown != self.text():
            super().setText(shown)
        self.setToolTip(self.full if shown != self.full else "")


class FitButton(QPushButton):
    """A button whose words are shortened when the widget is narrow, down to just its icon."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.full = text
        self.setMinimumWidth(1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def showEvent(self, e):
        super().showEvent(e)
        self._fit()

    def _fit(self):
        if not self.full:
            return
        room = self.width() - (self.iconSize().width() + 8 if not self.icon().isNull() else 0) - 26
        fm = self.fontMetrics()
        if fm.horizontalAdvance(self.full) <= room:
            shown = self.full
        elif room < fm.horizontalAdvance("Abc…") and not self.icon().isNull():
            shown = ""
        else:
            shown = fm.elidedText(self.full, Qt.ElideRight, max(10, room))
        if shown != self.text():
            self.setText(shown)
        self.setToolTip(self.full if shown != self.full else "")


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text) if wrap else Fit(text)
    if name:
        lab.setObjectName(name)
    if wrap:
        lab.setWordWrap(True)
        lab.setMinimumWidth(1)
    return lab


def _button(text: str, name: str | None = None, on_click=None) -> QPushButton:
    b = FitButton(text)
    if name:
        b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    if on_click is not None:
        b.clicked.connect(lambda _=False: on_click())
    return b


def known_folder(key: str) -> Path | None:
    if key == "downloads":
        from .app import downloads_folder
        return downloads_folder()
    where = {"documents": QStandardPaths.StandardLocation.DocumentsLocation,
             "desktop": QStandardPaths.StandardLocation.DesktopLocation,
             "pictures": QStandardPaths.StandardLocation.PicturesLocation,
             "music": QStandardPaths.StandardLocation.MusicLocation,
             "videos": QStandardPaths.StandardLocation.MoviesLocation}.get(key)
    if where is None:
        return None
    loc = QStandardPaths.writableLocation(where)
    return Path(loc) if loc else None


def target_page(target: str) -> str:
    """The Corefold page a shortcut opens ("" for a folder, file, app or website)."""
    return target[5:] if target.startswith("page:") else ""


def target_ok(settings, target: str) -> bool:
    """A shortcut to a tool's page only works while the tool is in use (one resting for now can't be opened
    from the Home page either)."""
    page = target_page(target)
    if not page:
        return True
    tool = all_tools.BY_PAGE.get(page)
    if tool is not None:
        return all_tools.is_on(settings, tool.id)
    return page in APP_PAGES


def target_name(target: str) -> str:
    """What a shortcut is called when it wasn't given a name."""
    page = target_page(target)
    if page:
        tool = all_tools.BY_PAGE.get(page)
        return tr(tool.nav) if tool is not None else tr(APP_PAGES.get(page, page))
    if target.startswith("known:"):
        return tr(KNOWN_FOLDERS.get(target[6:], target[6:]))
    t = target.strip().rstrip("\\/")
    if "://" in t or t.lower().startswith("www."):
        host = t.split("://", 1)[-1].split("/", 1)[0]
        return host[4:] if host.lower().startswith("www.") else host
    name = Path(t).name or t
    stem, ext = os.path.splitext(name)
    return stem if ext.lower() in (".exe", ".lnk", ".url", ".appref-ms") and stem else name


def target_icon(target: str) -> str:
    page = target_page(target)
    if page:
        tool = all_tools.BY_PAGE.get(page)
        return tool.icon if tool is not None else {"History": "history", "General": "settings",
                                                   "Updates": "arrow_download"}.get(page, "home")
    if target.startswith("known:"):
        return {"downloads": "arrow_download", "documents": "document", "desktop": "desktop",
                "pictures": "image", "music": "music_note_2", "videos": "video"}.get(target[6:], "folder")
    from .quick import link_icon
    return link_icon(target)


def target_hint(target: str) -> str:
    """The second, smaller line: where it leads."""
    if target_page(target):
        return tr("Corefold page")
    if target.startswith("known:"):
        folder = known_folder(target[6:])
        return ltr(str(folder)) if folder else ""
    return ltr(target)


class EditCover(QWidget):
    """Over a widget while the Home page is being changed: its own buttons can't be pressed by mistake, it can
    be dragged to another place, and it has buttons to change its size and take it off."""

    def __init__(self, frame: "WidgetFrame"):
        super().__init__(frame)
        self.frame = frame
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(tr("Drag to move it"))
        self.press: QPoint | None = None
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.addStretch()
        box = QFrame()
        box.setObjectName("editBar")
        bar = QHBoxLayout(box)
        bar.setContentsMargins(3, 3, 3, 3)
        bar.setSpacing(2)
        outer.addWidget(box, 0, Qt.AlignTop)
        t = frame.board.c.theme
        self.size_btn = QPushButton()
        self.size_btn.setObjectName("iconButton")
        self.size_btn.setIcon(icons.icon("resize_image", t.text))
        self.size_btn.setToolTip(tr("Size"))
        self.size_btn.setAccessibleName(tr("Size"))
        self.size_btn.setCursor(Qt.PointingHandCursor)
        self.size_btn.clicked.connect(lambda: frame.size_menu().exec(
            self.size_btn.mapToGlobal(QPoint(0, self.size_btn.height()))))
        self.size_btn.setVisible(len(frame.kind.sizes) > 1)
        self.remove_btn = QPushButton()
        self.remove_btn.setObjectName("iconButton")
        self.remove_btn.setIcon(icons.icon("delete", t.danger))
        self.remove_btn.setToolTip(tr("Remove"))
        self.remove_btn.setAccessibleName(tr("Remove"))
        self.remove_btn.setCursor(Qt.PointingHandCursor)
        self.remove_btn.clicked.connect(lambda: frame.board.remove(frame.entry["id"]))
        for b in (self.size_btn, self.remove_btn):
            b.setFixedSize(28, 28)
            bar.addWidget(b)

    def paintEvent(self, _e):
        t = tokens.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.setBrush(tokens.alpha(t.canvas, 150))
        p.setPen(QPen(t.accent, 1.5, Qt.DashLine))
        p.drawRoundedRect(r, 14, 14)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.press = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self.press is None or (e.position().toPoint() - self.press).manhattanLength() < 8:
            return
        self.press = None
        drag = QDrag(self)
        data = QMimeData()
        data.setData(MIME, self.frame.entry["id"].encode())
        drag.setMimeData(data)
        drag.setPixmap(self.frame.grab().scaledToWidth(min(self.frame.width(), 260), Qt.SmoothTransformation))
        drag.setHotSpot(QPoint(20, 20))
        drag.exec(Qt.MoveAction)

    def mouseReleaseEvent(self, _e):
        self.press = None


class WidgetFrame(QFrame):
    """One widget on the Home page: its box, and what's in it (made by Board.make_content)."""

    def __init__(self, board: "Board", entry: dict, preview: bool = False):
        super().__init__()
        self.setObjectName("homeWidget")
        self.setMinimumWidth(1)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.board = board
        self.entry = entry
        self.kind = homelist.KINDS[entry["kind"]]
        self.size_name = entry["size"]
        self.preview = preview
        self.drops = None   # what a drop of files on it does (fn(paths)), if anything
        self.setAccessibleName(tr(self.kind.name))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        board.make_content(self, lay)
        self.setAcceptDrops(self.drops is not None and not preview)
        self.cover: EditCover | None = None  # (made the first time the Home page is changed)
        if preview:  # (in the gallery: seen, not used)
            self.blocker = QWidget(self)
            self.blocker.setCursor(Qt.ArrowCursor)

    def set_editing(self, on: bool):
        if self.preview:
            return
        if on and self.cover is None:
            self.cover = EditCover(self)
            self.cover.setGeometry(self.rect())
        if self.cover is not None:
            self.cover.setVisible(on)
            self.cover.raise_()
        self.setAcceptDrops(self.drops is not None and not on)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        for child in (self.cover, getattr(self, "blocker", None)):
            if child is not None:
                child.setGeometry(self.rect())
                child.raise_()

    def size_menu(self) -> QMenu:
        menu = QMenu(self)
        for size in self.kind.sizes:
            act = menu.addAction(tr(homelist.SIZE_NAMES[size]),
                                 lambda s=size: self.board.resize_widget(self.entry["id"], s))
            act.setCheckable(True)
            act.setChecked(size == self.size_name)
        return menu

    def contextMenuEvent(self, e):
        if self.preview:
            return
        menu = QMenu(self)
        if len(self.kind.sizes) > 1:
            sizes = self.size_menu()
            sizes.setTitle(tr("Size"))
            menu.addMenu(sizes)
        menu.addAction(tr("Move earlier"), lambda: self.board.move_by(self.entry["id"], -1))
        menu.addAction(tr("Move later"), lambda: self.board.move_by(self.entry["id"], 1))
        if self.kind.id == "shortcut":
            menu.addAction(tr("Change…"), lambda: self.board.change_shortcut(self.entry["id"]))
        menu.addSeparator()
        menu.addAction(tr("Remove"), lambda: self.board.remove(self.entry["id"]))
        menu.addSeparator()
        menu.addAction(tr("Change the Home page…"), lambda: self.board.set_editing(True))
        menu.exec(e.globalPos())

    # files dropped on it (only on the ones that do something with files)

    def _files(self, e) -> list[Path]:
        return [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]

    def dragEnterEvent(self, e):
        if self.drops is not None and e.mimeData().hasUrls() and self._files(e):
            e.setDropAction(Qt.CopyAction)
            e.accept()
            self._hot(True)
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        e.setDropAction(Qt.CopyAction)
        e.accept()

    def dragLeaveEvent(self, _e):
        self._hot(False)

    def dropEvent(self, e):
        self._hot(False)
        paths = [p for p in self._files(e) if p.exists()]
        if paths and self.drops is not None:
            e.setDropAction(Qt.CopyAction)
            e.accept()
            QTimer.singleShot(0, lambda: self.drops(paths))  # (after the drop: it may open a window)

    def _hot(self, on: bool):
        self.setProperty("hot", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class AddTile(QPushButton):
    """The last place on the board while it's being changed: adds a widget."""

    def __init__(self, board: "Board"):
        super().__init__("  " + tr("Add a widget"))
        self.setObjectName("addWidget")
        self.setCursor(Qt.PointingHandCursor)
        self.setIcon(icons.icon("add", board.c.theme.accent))
        self.setIconSize(QSize(20, 20))
        self.setMinimumWidth(1)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.clicked.connect(board.open_gallery)


class Board(QWidget):
    """The widgets on the Home page, in a grid of four columns (two in a narrow window)."""
    editing_changed = Signal(bool)

    def __init__(self, window):
        super().__init__()
        self.w = window
        self.c = window.c
        self.editing = False
        self.frames: list[WidgetFrame] = []
        self.cols = 0
        self.setAcceptDrops(True)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(SPACING)
        self.add_tile = AddTile(self)
        self.empty = QFrame()
        self.empty.setObjectName("homeWidget")
        el = QHBoxLayout(self.empty)
        el.setContentsMargins(18, 14, 18, 14)
        el.addWidget(_label(tr("Put what you use most here: shortcuts and your tools' quick actions, in the sizes "
                               "you like."), "muted", wrap=True), 1)
        el.addWidget(_button(tr("Add a widget"), "primary", self.open_gallery), 0, Qt.AlignVCenter)
        self.fill()

    @property
    def saved(self) -> list[dict]:
        return self.c.settings.home_widgets

    def shown(self) -> list[dict]:
        out = []
        for e in homelist.shown(self.c.settings, self.saved, all_tools.is_on):
            if e["kind"] == "shortcut" and not target_ok(self.c.settings, e.get("target", "")):
                continue
            out.append(e)
        return out

    def fill(self):
        """Makes the widgets (again), as they're saved now."""
        from shiboken6 import delete
        for f in self.frames:
            self.grid.removeWidget(f)
            f.hide()
            delete(f)
        self.frames = []
        for e in self.shown():
            try:
                f = WidgetFrame(self, e)
            except Exception:  # (one widget that can't be made never takes the Home page with it)
                import logging
                logging.getLogger("corefold").exception("home widget %s", e.get("kind"))
                continue
            f.set_editing(self.editing)
            self.frames.append(f)
        self._place(force=True)

    def _place(self, force: bool = False):
        width = self.width() if self.width() > 50 else 900
        cols = homelist.COLUMNS if width >= WIDE_FROM else homelist.NARROW_COLUMNS
        if cols == self.cols and not force:
            return
        self.cols = cols
        for i in reversed(range(self.grid.count())):
            self.grid.takeAt(i)
        sizes = [f.size_name for f in self.frames]
        extra = []
        if self.editing:
            sizes.append("small")
            extra.append(self.add_tile)
        places = homelist.pack(sizes, cols)
        for f, (row, col, w, h) in zip(self.frames + extra, places):
            f.setFixedHeight(h * UNIT_H + (h - 1) * SPACING)
            self.grid.addWidget(f, row, col, h, w)
            f.show()
        self.add_tile.setVisible(self.editing)
        if not self.editing:
            self.add_tile.hide()
        show_empty = not self.frames and not self.editing
        if show_empty:
            self.grid.addWidget(self.empty, 0, 0, 1, cols)
        self.empty.setVisible(show_empty)
        for c in range(homelist.COLUMNS):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def column_width(self) -> int:
        cols = self.cols or homelist.COLUMNS
        return max(150, (max(self.width(), 600) - SPACING * (cols - 1)) // cols)

    def set_editing(self, on: bool):
        if on == self.editing:
            return
        self.editing = on
        for f in self.frames:
            f.set_editing(on)
        self._place(force=True)
        self.editing_changed.emit(on)

    def apply_theme(self, _theme=None):
        self.add_tile.setIcon(icons.icon("add", self.c.theme.accent))
        self.fill()

    # ---- changes (each is saved right away: the Home page isn't a setting that waits for Save)

    def _save(self):
        self.c.settings.home_widgets = homelist.clean(self.saved)
        self.c.settings_changed()

    def add(self, kind: str, size: str = "", **extra) -> dict:
        entry = homelist.make(kind, size, **extra)
        self.saved.append(entry)
        self._save()
        self.fill()
        return entry

    def remove(self, widget_id: str):
        self.c.settings.home_widgets = [e for e in self.saved if e.get("id") != widget_id]
        self._save()
        self.fill()

    def resize_widget(self, widget_id: str, size: str):
        for e in self.saved:
            if e.get("id") == widget_id:
                e["size"] = size
        self._save()
        self.fill()

    def move(self, widget_id: str, before_id: str | None):
        """Puts a widget just before another one (None: at the end)."""
        moving = next((e for e in self.saved if e.get("id") == widget_id), None)
        if moving is None or widget_id == before_id:
            return
        rest = [e for e in self.saved if e is not moving]
        at = next((i for i, e in enumerate(rest) if e.get("id") == before_id), len(rest))
        rest.insert(at, moving)
        self.c.settings.home_widgets = rest
        self._save()
        self.fill()

    def move_by(self, widget_id: str, step: int):
        """One place earlier or later among the widgets that are shown (hidden ones keep their places)."""
        ids = [e["id"] for e in self.shown()]
        if widget_id not in ids:
            return
        i = ids.index(widget_id)
        j = i + step
        if not 0 <= j < len(ids):
            return
        if step < 0:
            self.move(widget_id, ids[j])
        else:
            self.move(widget_id, ids[j + 1] if j + 1 < len(ids) else None)

    def start_again(self):
        """Back to the widgets a new Home starts with."""
        self.c.settings.home_widgets = homelist.default()
        self._save()
        self.fill()

    # ---- dragging a widget to another place

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.setDropAction(Qt.MoveAction)
            e.accept()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.setDropAction(Qt.MoveAction)
            e.accept()

    def dropEvent(self, e):
        if not e.mimeData().hasFormat(MIME):
            return
        widget_id = bytes(e.mimeData().data(MIME)).decode()
        e.setDropAction(Qt.MoveAction)
        e.accept()
        pos = e.position().toPoint()
        target = self.drop_target(pos)
        QTimer.singleShot(0, lambda: self.move(widget_id, target))

    def drop_target(self, pos: QPoint) -> str | None:
        """The widget a drop at pos goes before: the one under it (in its first half), or the next one."""
        for i, f in enumerate(self.frames):
            g = f.geometry()
            if g.contains(pos):
                rtl = self.layoutDirection() == Qt.RightToLeft
                first_half = pos.x() > g.center().x() if rtl else pos.x() < g.center().x()
                if first_half:
                    return f.entry["id"]
                return self.frames[i + 1].entry["id"] if i + 1 < len(self.frames) else None
        below = [f for f in self.frames if f.geometry().top() > pos.y()]
        return below[0].entry["id"] if below else None

    # ---- adding

    def open_gallery(self, kind: str = ""):
        g = Gallery(self, kind)
        g.exec()
        g.deleteLater()

    def ask_shortcut(self, target: str = "", name: str = "") -> tuple[str, str] | None:
        dlg = ShortcutDialog(self, target, name)
        try:
            if dlg.exec():
                return dlg.target(), dlg.name()
            return None
        finally:
            dlg.deleteLater()

    def change_shortcut(self, widget_id: str):
        e = next((e for e in self.saved if e.get("id") == widget_id), None)
        if e is None:
            return
        got = self.ask_shortcut(e.get("target", ""), e.get("name", ""))
        if got:
            e["target"], e["name"] = got
            self._save()
            self.fill()

    # ---- what's in each kind of widget

    def make_content(self, f: WidgetFrame, lay: QVBoxLayout):
        getattr(self, "_w_" + f.kind.id)(f, lay, f.size_name)

    def _tile(self, product: str, size: int = 30) -> QLabel:
        tile = QLabel()
        tile.setPixmap(icons.product_tile(product, size, self.c.theme.dark, self.devicePixelRatioF()))
        tile.setFixedSize(size + 2, size + 2)
        return tile

    def _glyph(self, name: str, size: int = 22, color=None) -> QLabel:
        g = QLabel()
        g.setPixmap(icons.pixmap(name, size, color or self.c.theme.accent, self.devicePixelRatioF()))
        g.setFixedSize(size, size)
        return g

    def _head(self, lay: QVBoxLayout, product: str, title: str, line: str = ""):
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self._tile(product), 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(0)
        words.addWidget(_label(title, "cardTitle", wrap=True))  # (two lines in a small widget)
        if line:
            words.addWidget(_label(line, "subtle", wrap=True))
        row.addLayout(words, 1)
        lay.addLayout(row)

    def _drop_hint(self, lay: QVBoxLayout, text: str):
        """The room left in a big widget: where files can be dropped."""
        box = QFrame()
        box.setObjectName("dropzone")
        box.setAttribute(Qt.WA_TransparentForMouseEvents)
        bl = QVBoxLayout(box)
        bl.setContentsMargins(10, 10, 10, 10)
        bl.setSpacing(6)
        bl.addStretch()
        bl.addWidget(self._glyph("arrow_download", 26), 0, Qt.AlignHCenter)
        words = _label(text, "subtle", wrap=True)
        words.setAlignment(Qt.AlignCenter)
        bl.addWidget(words)
        bl.addStretch()
        lay.addWidget(box, 1)

    def _action(self, text: str, icon_name: str, fn, primary: bool = False) -> QPushButton:
        """A button in a widget: an icon and a few words."""
        b = _button(text, "primary" if primary else "widgetAction", fn)
        t = self.c.theme
        b.setIcon(icons.icon(icon_name, t.on_accent if primary else t.accent if not t.dark else t.text))
        b.setIconSize(QSize(16, 16))
        b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        b.setMinimumWidth(1)
        return b

    def _open(self, page: str):
        self.w.open_page(page)

    def _page_part(self, page: str, part: str):
        """Opens a tool's page and hands back its part (made with the page), or None."""
        self.w.open_page(page)
        return getattr(self.w, part, None) if self.w.current_page() == page else None

    # Converter

    def _w_convert(self, f, lay, size):
        f.drops = self.w.show_files
        wide = size == "wide"
        box = QHBoxLayout() if wide else None
        head_lay = QVBoxLayout() if wide else lay
        self._head(head_lay, "converter", tr("Convert files"),
                   "" if size == "small" else tr("Drop files here and pick what to make of them."))
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self._action(tr("Choose files…"), "add", self.w._choose_files, primary=True))
        if size != "small":
            buttons.addWidget(self._action(tr("Paste (Ctrl + V)"), "clipboard_paste", self.c.paste))
        if wide:
            box.addLayout(head_lay, 1)
            box.addLayout(buttons)
            lay.addStretch()
            lay.addLayout(box)
            lay.addStretch()
            return
        if size != "large":
            lay.addStretch()
        else:
            lately = [e for e in self.c.history.items if e.outputs][:3]
            self._drop_hint(lay, tr("Drop files here"))
            if lately:
                lay.addWidget(_label(tr("Lately"), "subtle"))
                for e in lately:
                    out = Path(e.outputs[0])
                    row = _button(short_name(out.name, 46), "widgetRow", lambda p=out: self._reveal(p))
                    row.setIcon(icons.icon("document", self.c.theme.muted))
                    row.setToolTip(str(out))
                    lay.addWidget(row)
            again = self.c.again_text()
            if again:
                lay.addWidget(self._action(again, "arrow_sync", self.c.do_again))
        lay.addLayout(buttons)

    def _reveal(self, path: Path):
        from .winutil import reveal_in_explorer
        reveal_in_explorer(path)

    def _w_stats(self, f, lay, size):
        st = self.c.history.stats
        files = locale().toString(st.files)
        saved = human_size(st.bytes_saved) if st.bytes_saved else "0"
        self._head(lay, "converter", tr("What Corefold did"))
        lay.addStretch()
        if size == "small":
            lay.addWidget(_label(files, "heroStat"))
            lay.addWidget(_label(tr("files converted"), "subtle"))
            if st.bytes_saved:
                lay.addWidget(_label(tr("{size} saved", size=saved), "subtle"))
            return
        row = QHBoxLayout()
        row.setSpacing(24)
        for value, text in ((files, tr("files converted")), (saved, tr("space saved"))):
            box = QVBoxLayout()
            box.setSpacing(0)
            box.addWidget(_label(value, "heroStat"))
            box.addWidget(_label(text, "subtle"))
            row.addLayout(box)
        row.addStretch()
        row.addWidget(_button(tr("See all"), "flat", lambda: self._open("History")), 0, Qt.AlignBottom)
        lay.addLayout(row)

    # PDF

    def _pdf_run(self, key: str):
        page = self._page_part("PDF", "pdf_page")
        if page is not None:
            page.start(key)

    def _pdf_dropped(self, paths: list[Path]):
        from .pdfpage import is_pdf, is_picture
        page = self._page_part("PDF", "pdf_page")
        if page is None:
            return
        pdfs = [p for p in paths if is_pdf(p)]
        if pdfs:
            page.run("merge" if len(pdfs) > 1 else "organize", pdfs)
        elif pics := [p for p in paths if is_picture(p)]:
            page.pictures_to_pdf(pics)
        else:
            self.c.tell(tr("PDF"), tr("Drop PDFs or pictures here."))

    def _w_pdf(self, f, lay, size):
        f.drops = self._pdf_dropped
        how_many = {"small": 2, "medium": 4, "large": 8, "wide": 6}[size]
        self._head(lay, "pdf", tr("PDF tools"), tr("Or drop PDFs here.") if size == "large" else "")
        lay.addStretch()
        grid = QGridLayout()
        grid.setSpacing(8)
        cols = {"small": 1, "medium": 2, "large": 2, "wide": 6}[size]
        for n, (key, text, icon_name) in enumerate(PDF_ACTIONS[:how_many]):
            grid.addWidget(self._action(tr(text), icon_name, lambda k=key: self._pdf_run(k)), n // cols, n % cols)
        lay.addLayout(grid)

    # Archive

    def _w_archive(self, f, lay, size):
        f.drops = self.w._archive_dropped
        self._head(lay, "archive", tr("Archives"),
                   tr("Drop an archive to open it, or files to pack them.") if size == "medium" else "")
        lay.addStretch()
        if size == "large":
            holder = QVBoxLayout()
            holder.setSpacing(2)
            lay.addLayout(holder, 1)
            names = list(self.c.settings.recent_archives) if self.c.settings.remember_archives else []

            def there():  # (off the window's thread: a drive that's gone can take seconds to answer)
                out = []
                for n in names:
                    try:
                        if Path(n).is_file():
                            out.append(n)
                    except OSError:
                        pass
                    if len(out) == 4:
                        break
                return out

            def show(found):
                if not work.alive(f):
                    return
                for n in found or []:
                    p = Path(n)
                    row = _button(short_name(p.name, 46), "widgetRow", lambda p=p: self.c.open_browser(p))
                    row.setIcon(icons.icon("archive", self.c.theme.muted))
                    row.setToolTip(str(p))
                    holder.addWidget(row)
                if found:
                    holder.addStretch()
                else:
                    self._drop_hint(holder, tr("Drop an archive to open it, or files to pack them."))
            _look("archives:" + "|".join(names), there, show)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self._action(tr("Open an archive…") if size != "small" else tr("Open…"), "folder_open",
                                       self.w._open_archive, primary=True))
        if size != "small":
            buttons.addWidget(self._action(tr("Make an archive…"), "folder_zip", self.w._make_archive))
        lay.addLayout(buttons)

    # File Organizer

    def _organize(self, key: str):
        page = self._page_part("Organizer", "organizer_page")
        if page is not None:
            page.pick(key)

    def _organize_dropped(self, paths: list[Path]):
        folder = next((p for p in paths if p.is_dir()), None)
        if folder is None:
            self.c.tell(tr("File Organizer"), tr("Drop a folder here to sort it."))
            return
        page = self._page_part("Organizer", "organizer_page")
        if page is not None:
            page.set_folder(folder)

    def _w_organizer(self, f, lay, size):
        f.drops = self._organize_dropped
        self._head(lay, "organizer", tr("Tidy a folder"),
                   "" if size == "small" else tr("Sort a folder into a folder for each kind of file."))
        lay.addStretch()
        row = QHBoxLayout()
        row.setSpacing(8)
        lines = {}
        for key, name, icon_name in (("downloads", "Downloads", "arrow_download"), ("desktop", "Desktop", "desktop")):
            if size == "small" and key == "desktop":
                break
            b = QPushButton()
            b.setObjectName("widgetFolder")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._organize(k))
            bl = QHBoxLayout(b)
            bl.setContentsMargins(10, 6, 10, 6)
            bl.setSpacing(8)
            glyph = self._glyph(icon_name, 18)
            glyph.setAttribute(Qt.WA_TransparentForMouseEvents)
            bl.addWidget(glyph)
            words = QVBoxLayout()
            words.setSpacing(0)
            title = _label(tr(name), "strong")
            line = _label("…", "subtle")
            for lab in (title, line):
                lab.setAttribute(Qt.WA_TransparentForMouseEvents)
                words.addWidget(lab)
            bl.addLayout(words, 1)
            b.setMinimumHeight(48)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            lines[key] = line
            row.addWidget(b)
        lay.addLayout(row)
        places = {k: known_folder(k) for k in lines}

        def count():
            from . import organizer
            return {k: organizer.loose_count(p, known=True) if p else 0 for k, p in places.items()}

        def show(result):
            if not work.alive(f):
                return
            for k, line in lines.items():
                line.setText(to_sort_text((result or {}).get(k, 0)) if result is not None else "")
        _look("organizer:" + "|".join(str(p) for p in places.values()), count, show)

    # Unlock

    def _unlock_check(self, paths: list[Path]):
        page = self._page_part("Unlock", "unlock_page")
        if page is not None:
            page.check(paths)

    def _unlock_pick(self, folder: bool = False):
        if folder:
            chosen = QFileDialog.getExistingDirectory(self.w, tr("Pick a folder"), str(Path.home()))
            paths = [Path(chosen)] if chosen else []
        else:
            names, _ = QFileDialog.getOpenFileNames(self.w, tr("Pick a file"), str(Path.home()))
            paths = [Path(n) for n in names]
        if paths:
            self._unlock_check(paths)

    def _w_unlock(self, f, lay, size):
        f.drops = self._unlock_check
        self._head(lay, "unlock", tr("What's using this?"),
                   tr("Drop a file or folder you can't delete or move.") if size != "small" else "")
        lay.addStretch()
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._action(tr("Pick a file…"), "document", self._unlock_pick, primary=True))
        if size != "small":
            row.addWidget(self._action(tr("Pick a folder…"), "folder", lambda: self._unlock_pick(True)))
        lay.addLayout(row)

    # Cloud

    def _cloud_show(self, acc, found):
        page = self._page_part("Cloud", "cloud_page")
        if page is None or acc is None:
            return
        if page.found is None:  # (not looked yet: what this widget found is the same)
            page.found = list(found)
        page.load_accounts(pick=acc.key)

    def _w_cloud(self, f, lay, size):
        self._head(lay, "cloud", tr("Your clouds"))
        holder = QVBoxLayout()
        holder.setSpacing(2)
        lay.addLayout(holder)
        lay.addStretch()
        lay.addWidget(self._action(tr("Open Cloud"), "cloud", lambda: self._open("Cloud"),
                                   primary=size == "small"), 0, Qt.AlignBottom)
        rows = {"small": 0, "medium": 2, "large": 7}[size]

        def find():  # (with the services' names: the cloud's own modules are only loaded on this thread)
            from . import cloudlocal
            from .cloud import SERVICES
            return [(acc, SERVICES.get(acc.service) or acc.who) for acc in cloudlocal.find()]

        def show(named):
            if not work.alive(f):
                return
            named = named or []
            found = [acc for acc, _ in named]
            if size == "small":
                holder.addWidget(_label(tr("1 cloud on this PC") if len(found) == 1 else
                                        tr("{count} clouds on this PC", count=len(found)) if found else
                                        tr("No cloud folders on this PC yet."), "subtle", wrap=True))
                return
            for acc, service in named[:rows]:
                row = _button(f"{service}   ·   {ltr(short_name(acc.local, 40))}", "widgetRow",
                              lambda a=acc: self._cloud_show(a, found))
                row.setIcon(icons.icon("cloud", self.c.theme.muted))
                row.setToolTip(acc.local)
                holder.addWidget(row)
            if not found:
                holder.addWidget(_label(tr("No cloud folders on this PC yet."), "subtle", wrap=True))
        _look("cloud", find, show)

    # shortcuts

    def open_target(self, target: str):
        if not target_ok(self.c.settings, target):
            return
        page = target_page(target)
        if page:
            self._open(page)
            return
        if target.startswith("known:"):
            folder = known_folder(target[6:])
            target = str(folder) if folder else ""
        from .quick import open_target
        if target and not open_target(target):
            self.c.tell(tr("Shortcut"), tr("This can't be opened. Was it moved or deleted?"))

    def _w_shortcut(self, f, lay, size):
        target = f.entry.get("target", "")
        name = f.entry.get("name") or target_name(target)
        b = QPushButton()
        b.setObjectName("widgetShortcut")
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(target_hint(target))
        b.setAccessibleName(name)
        b.clicked.connect(lambda: self.open_target(target))
        b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        b.setMinimumWidth(1)
        bl = QHBoxLayout(b) if size == "medium" else QVBoxLayout(b)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(10)
        tool = all_tools.BY_PAGE.get(target_page(target))
        art = self._tile(tool.product, 40) if tool is not None else self._glyph(target_icon(target), 34)
        words = QVBoxLayout()
        words.setSpacing(0)
        title = _label(short_name(name, 40), "cardTitle")
        hint = _label(target_hint(target), "subtle")
        for w_ in (art, title, hint):
            w_.setAttribute(Qt.WA_TransparentForMouseEvents)
        words.addStretch()
        words.addWidget(title)
        words.addWidget(hint)
        words.addStretch()
        if size == "medium":
            bl.addWidget(art, 0, Qt.AlignVCenter)
            bl.addLayout(words, 1)
        else:
            bl.addWidget(art)
            bl.addStretch()
            bl.addLayout(words)
        lay.addWidget(b, 1)

    def _w_shortcuts(self, f, lay, size):
        items = [i for i in f.entry.get("items", []) if target_ok(self.c.settings, i["target"])]
        head = QHBoxLayout()
        head.addWidget(_label(tr("Shortcuts"), "cardTitle"))
        head.addStretch()
        if not f.preview and len(f.entry.get("items", [])) < homelist.MAX_ITEMS:
            add = _button("", "iconButton", lambda: self._shortcut_add(f.entry["id"]))
            add.setIcon(icons.icon("add", self.c.theme.accent))
            add.setFixedSize(28, 28)
            add.setToolTip(tr("Add a shortcut"))
            add.setAccessibleName(tr("Add a shortcut"))
            head.addWidget(add)
        lay.addLayout(head)
        if not items:
            lay.addStretch()
            lay.addWidget(_label(tr("Add the folders, apps, websites and Corefold pages you use most."), "subtle",
                                 wrap=True))
            if not f.preview:
                lay.addWidget(self._action(tr("Add a shortcut"), "add", lambda: self._shortcut_add(f.entry["id"])),
                              0, Qt.AlignLeading)
            lay.addStretch()
            return
        per_row = {"medium": 4, "wide": 8, "large": 4}[size]
        rows = {"medium": 1, "wide": 1, "large": 3}[size]
        grid = QGridLayout()
        grid.setSpacing(6)
        for n, item in enumerate(items[:per_row * rows]):
            b = QPushButton()
            b.setObjectName("widgetShortcut")
            b.setCursor(Qt.PointingHandCursor)
            name = item.get("name") or target_name(item["target"])
            b.setToolTip(f"{name}\n{target_hint(item['target'])}")
            b.setAccessibleName(name)
            b.clicked.connect(lambda _=False, t=item["target"]: self.open_target(t))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda _pos, b=b, t=item["target"]: self._shortcut_menu(f.entry["id"], t, b))
            bl = QVBoxLayout(b)
            bl.setContentsMargins(4, 6, 4, 6)
            bl.setSpacing(4)
            tool = all_tools.BY_PAGE.get(target_page(item["target"]))
            art = self._tile(tool.product, 30) if tool is not None else self._glyph(target_icon(item["target"]), 26)
            title = _label(short_name(name, 14), "subtle")
            title.setAlignment(Qt.AlignCenter)
            for w_ in (art, title):
                w_.setAttribute(Qt.WA_TransparentForMouseEvents)
            bl.addWidget(art, 0, Qt.AlignHCenter)
            bl.addWidget(title)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setFixedHeight(76)
            b.setMinimumWidth(1)
            grid.addWidget(b, n // per_row, n % per_row)
        for c in range(per_row):
            grid.setColumnStretch(c, 1)
        lay.addLayout(grid)
        lay.addStretch()

    def _shortcut_add(self, widget_id: str):
        got = self.ask_shortcut()
        if not got:
            return
        for e in self.saved:
            if e.get("id") == widget_id:
                e.setdefault("items", []).append({"target": got[0], "name": got[1]})
        self._save()
        self.fill()

    def _shortcut_menu(self, widget_id: str, target: str, b: QPushButton):
        def drop():
            for e in self.saved:
                if e.get("id") == widget_id:
                    e["items"] = [i for i in e.get("items", []) if i.get("target") != target]
            self._save()
            self.fill()
        menu = QMenu(b)
        menu.addAction(tr("Open"), lambda: self.open_target(target))
        menu.addAction(tr("Remove"), drop)
        menu.exec(b.mapToGlobal(QPoint(0, b.height())))

    # the tools resting for now (their widgets come back with them)

    def _w_screenshot(self, f, lay, size):
        self._head(lay, "capture", tr("Screenshot"))
        lay.addStretch()
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._action(tr("Take a screenshot"), "screenshot",
                                   lambda: self.c.capture and QTimer.singleShot(250, self.c.capture.screenshot),
                                   primary=True))
        if size != "small":
            row.addWidget(self._action(tr("Screenshot with a timer"), "timer",
                                       lambda: self.c.capture and self.c.capture.screenshot_later()))
        lay.addLayout(row)

    def _w_clipboard(self, f, lay, size):
        self._head(lay, "clipboard", tr("Clipboard history"))
        lay.addStretch()
        lay.addWidget(self._action(tr("Open"), "clipboard", lambda: self.c.show_clipboard(), primary=True))

    def _w_color(self, f, lay, size):
        self._head(lay, "colors", tr("Pick a colour"))
        lay.addStretch()
        lay.addWidget(self._action(tr("Pick"), "color", lambda: self.c.colors and self.c.colors.pick(),
                                   primary=True))

    def _w_disk(self, f, lay, size):
        self._head(lay, "space", tr("Disk space"))
        holder = QVBoxLayout()
        holder.setSpacing(6)
        lay.addStretch()
        lay.addLayout(holder)
        lay.addStretch()
        rows = {"small": 1, "medium": 2, "large": 5}[size]

        def drives():
            import shutil
            out = []
            for root in _drive_roots():
                try:
                    u = shutil.disk_usage(root)
                except OSError:
                    continue
                if u.total:
                    out.append((root, u.total, u.free))
            return out

        def show(found):
            if not work.alive(f):
                return
            for root, total, free in (found or [])[:rows]:
                line = QHBoxLayout()
                line.addWidget(_label(root.rstrip("\\/") or root, "strong"))
                line.addStretch()
                line.addWidget(_label(tr("{free} free of {total}", free=human_size(free), total=human_size(total)),
                                      "subtle"))
                holder.addLayout(line)
                bar = QProgressBar()
                bar.setRange(0, 1000)
                bar.setValue(round(1000 * (total - free) / total))
                bar.setTextVisible(False)
                bar.setFixedHeight(6)
                if free < total * 0.1:
                    bar.setObjectName("low")
                holder.addWidget(bar)
        _look("disk", drives, show)
        f.mouseReleaseEvent = lambda e: self._open("Space") if e.button() == Qt.LeftButton else None


def to_sort_text(n: int) -> str:
    """(the same words as on the File Organizer's page, whose module isn't loaded for the Home page)"""
    if not n:
        return tr("Nothing to sort")
    return tr("1 file to sort") if n == 1 else tr("{count} files to sort", count=n)


def _drive_roots() -> list[str]:
    """The PC's fixed drives (never network ones: one that's gone can take long to answer)."""
    if os.name != "nt":
        return ["/"]
    import ctypes
    import string
    out = []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for n, letter in enumerate(string.ascii_uppercase):
        if mask & (1 << n):
            root = f"{letter}:\\"
            if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:  # (DRIVE_FIXED)
                out.append(root)
    return out


class Gallery(QDialog):
    """Adding a widget: the kinds on one side, the chosen one as it will look on the other, in each size."""

    def __init__(self, board: Board, kind: str = ""):
        super().__init__(board.window(), Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.board = board
        self.setWindowTitle(tr("Add a widget"))
        self.setObjectName("gallery")
        self.resize(820, 520)
        self.size = ""
        self.preview: WidgetFrame | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(18)
        self.list = QListWidget()
        self.list.setObjectName("settingsNav")
        self.list.setFixedWidth(250)
        self.list.setIconSize(QSize(20, 20))
        lay.addWidget(self.list)
        side = QVBoxLayout()
        side.setSpacing(10)
        self.title = _label("", "h2")
        self.line = _label("", "muted", wrap=True)
        side.addWidget(self.title)
        side.addWidget(self.line)
        strip = QFrame()
        strip.setObjectName("segmented")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.size_buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        for size, text in homelist.SIZE_NAMES.items():
            b = QPushButton(tr(text))
            b.setObjectName("segment")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, s=size: self._show(s))
            group.addButton(b)
            sl.addWidget(b)
            self.size_buttons[size] = b
        row = QHBoxLayout()
        row.addWidget(strip)
        row.addStretch()
        side.addLayout(row)
        self.stage = QFrame()
        self.stage.setObjectName("galleryStage")
        self.stage_lay = QGridLayout(self.stage)
        self.stage_lay.setContentsMargins(16, 16, 16, 16)
        side.addWidget(self.stage, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        close = _button(tr("Close"), None, self.reject)
        self.add_btn = _button(tr("Add to Home"), "primary", self._add)
        self.add_btn.setDefault(True)
        buttons.addWidget(close)
        buttons.addWidget(self.add_btn)
        side.addLayout(buttons)
        lay.addLayout(side, 1)
        self._fill(kind)

    def _fill(self, kind: str):
        b = self.board
        offered = {k.id for k in homelist.offered(b.c.settings, b.saved, all_tools.is_on)}
        t = b.c.theme
        groups: dict[str, list] = {}
        for k in homelist.KINDS.values():  # (the ones on the page already are named too, but can't be added again)
            if not k.tool or all_tools.is_on(b.c.settings, k.tool):
                groups.setdefault(k.tool, []).append(k)
        order = [""] + [tool.id for tool in all_tools.enabled(b.c.settings)]
        first = None
        for tool_id in order:
            kinds = groups.get(tool_id)
            if not kinds:
                continue
            head = QListWidgetItem(tr("Shortcuts") if not tool_id else tr(all_tools.BY_ID[tool_id].nav))
            head.setFlags(Qt.NoItemFlags)
            font = head.font()
            font.setBold(True)
            head.setFont(font)
            self.list.addItem(head)
            for k in kinds:
                item = QListWidgetItem(icons.icon(k.icon, t.text, disabled=t.tokens.subtle), tr(k.name))
                item.setData(Qt.UserRole, k.id)
                if k.id not in offered:
                    item.setFlags(Qt.NoItemFlags)
                    item.setToolTip(tr("On your Home page already"))
                    item.setText(tr(k.name) + "  ✓")
                self.list.addItem(item)
                if k.id in offered and (first is None or k.id == kind):
                    first = item
        self.list.currentItemChanged.connect(lambda item, _old: self._picked(item))
        if first is not None:
            self.list.setCurrentItem(first)
        else:
            self.title.setText(tr("Everything's on your Home page already"))
            self.add_btn.setEnabled(False)

    def current_kind(self) -> homelist.Kind | None:
        item = self.list.currentItem()
        return homelist.KINDS.get(item.data(Qt.UserRole)) if item is not None else None

    def _picked(self, item):
        k = self.current_kind()
        if k is None:
            return
        self.title.setText(tr(k.name))
        self.line.setText(tr(k.line))
        for size, btn in self.size_buttons.items():
            btn.setVisible(size in k.sizes)
        self._show(k.sizes[0])

    def _show(self, size: str):
        from shiboken6 import delete
        k = self.current_kind()
        if k is None:
            return
        self.size = size
        self.size_buttons[size].setChecked(True)
        if self.preview is not None:
            self.stage_lay.removeWidget(self.preview)
            self.preview.hide()
            delete(self.preview)
            self.preview = None
        extra = {}
        if k.id == "shortcut":
            extra = {"target": homelist.DOWNLOADS, "name": ""}
        entry = homelist.make(k.id, size, **extra)
        self.preview = WidgetFrame(self.board, entry, preview=True)
        cols, rows = homelist.span(size, homelist.COLUMNS)
        unit = min(self.board.column_width(), 150 if cols == 4 else 200)
        self.preview.setFixedSize(cols * unit + (cols - 1) * SPACING, rows * UNIT_H + (rows - 1) * SPACING)
        self.stage_lay.addWidget(self.preview, 0, 0, Qt.AlignCenter)

    def _add(self):
        k = self.current_kind()
        if k is None:
            return
        extra = {}
        if k.id == "shortcut":
            got = self.board.ask_shortcut()
            if not got:
                return
            extra = {"target": got[0], "name": got[1]}
        elif k.id == "shortcuts":
            extra = {"items": []}
        self.board.add(k.id, self.size, **extra)
        self.accept()


class ShortcutDialog(QDialog):
    """What a shortcut opens: a Corefold page, a folder, a file or app, or a website."""

    def __init__(self, board: Board, target: str = "", name: str = ""):
        super().__init__(board.window(), Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        from .atomic.widgets import Dropdown
        self.board = board
        self.setWindowTitle(tr("Shortcut"))
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)
        lay.addWidget(_label(tr("What should it open?"), "h2"))
        self.kind = Dropdown()
        for key, text in (("page", tr("A Corefold page")), ("known", tr("A folder you use often")),
                          ("folder", tr("Another folder")), ("file", tr("A file or app")),
                          ("web", tr("A website"))):
            self.kind.addItem(text, key)
        lay.addWidget(self.kind)
        self.pages = Dropdown()
        for tool in all_tools.enabled(board.c.settings):
            self.pages.addItem(tr(tool.nav), "page:" + tool.page)
        for page, text in APP_PAGES.items():
            self.pages.addItem(tr(text), "page:" + page)
        self.known = Dropdown()
        for key, text in KNOWN_FOLDERS.items():
            self.known.addItem(tr(text), "known:" + key)
        self.path = QLineEdit()
        self.path.setPlaceholderText(tr("Pick it, or type where it is"))
        self.browse = _button(tr("Browse…"), None, self._browse)
        self.web = QLineEdit()
        self.web.setPlaceholderText("https://")
        path_row = QHBoxLayout()
        path_row.addWidget(self.path, 1)
        path_row.addWidget(self.browse)
        self.path_box = QWidget()
        self.path_box.setLayout(path_row)
        path_row.setContentsMargins(0, 0, 0, 0)
        for w_ in (self.pages, self.known, self.path_box, self.web):
            lay.addWidget(w_)
        lay.addWidget(_label(tr("Name (if you like)"), "muted"))
        self.name_box = QLineEdit(name)
        self.name_box.setMaxLength(60)
        self.name_box.setPlaceholderText(tr("Its own name"))
        lay.addWidget(self.name_box)
        self.problem = _label("", "subtle", wrap=True)
        lay.addWidget(self.problem)
        row = QHBoxLayout()
        row.addStretch()
        cancel = _button(tr("Cancel"), None, self.reject)
        self.ok = _button(tr("Save"), "primary", self._ok)
        self.ok.setDefault(True)
        row.addWidget(cancel)
        row.addWidget(self.ok)
        lay.addSpacing(4)
        lay.addLayout(row)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._set(target)
        self._kind_changed()

    def _set(self, target: str):
        if not target:
            return
        if target.startswith("page:"):
            self.kind.setCurrentIndex(self.kind.findData("page"))
            self.pages.setCurrentIndex(max(0, self.pages.findData(target)))
        elif target.startswith("known:"):
            self.kind.setCurrentIndex(self.kind.findData("known"))
            self.known.setCurrentIndex(max(0, self.known.findData(target)))
        elif "://" in target or target.lower().startswith("www."):
            self.kind.setCurrentIndex(self.kind.findData("web"))
            self.web.setText(target)
        else:
            try:
                is_dir = not target.startswith(("\\\\", "//")) and Path(target).is_dir()
            except (OSError, ValueError):
                is_dir = False
            self.kind.setCurrentIndex(self.kind.findData("folder" if is_dir else "file"))
            self.path.setText(target)

    def _kind_changed(self, *_):
        k = self.kind.currentData()
        self.pages.setVisible(k == "page")
        self.known.setVisible(k == "known")
        self.path_box.setVisible(k in ("folder", "file"))
        self.web.setVisible(k == "web")
        self.problem.setText("")
        self.adjustSize()

    def _browse(self):
        start = self.path.text().strip() or str(Path.home())
        if self.kind.currentData() == "folder":
            chosen = QFileDialog.getExistingDirectory(self, tr("Pick a folder"), start)
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, tr("Pick a file or app"), start)
        if chosen:
            self.path.setText(os.path.normpath(chosen))

    def target(self) -> str:
        k = self.kind.currentData()
        if k == "page":
            return self.pages.currentData() or ""
        if k == "known":
            return self.known.currentData() or ""
        if k == "web":
            t = self.web.text().strip()
            return t if "://" in t or not t else "https://" + t
        return self.path.text().strip().strip('"')

    def name(self) -> str:
        return self.name_box.text().strip()[:60]

    def _ok(self):
        t = self.target()
        if not t:
            self.problem.setText(tr("Pick what it should open first."))
            return
        if self.kind.currentData() == "web" and (" " in t or "." not in t.split("://", 1)[-1]):
            self.problem.setText(tr("That doesn't look like a website's address."))
            return
        self.accept()
