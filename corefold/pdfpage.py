"""Corefold PDF's page: what you can do with PDFs, as cards, and a board of page pictures where
pages from one or more PDFs are put in order, turned, deleted and saved as a new PDF.

Nothing here is loaded unless Corefold PDF is switched on (in Files) and its page is opened."""
from __future__ import annotations

import collections
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut, QTransform
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListView,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea,
                               QStackedWidget, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout,
                               QWidget)

from . import icons, pdftools, sheet, work
from .atomic import tokens
from .atomic.widgets import FlowLayout, IconButton, ResponsiveGrid
from .core import IMAGE_EXT, norm, short_name
from .i18n import ltr, tr
from .theme import round_menu

if TYPE_CHECKING:
    from .app import Controller

log = logging.getLogger("corefold")

THUMB = 150
FILE, PAGE, TURN = Qt.UserRole, Qt.UserRole + 1, Qt.UserRole + 2
# a colour for each file on the board (so you can tell whose pages are whose after merging)
FILE_COLORS = ("#e5484d", "#3e8ef7", "#30a46c", "#f5a524", "#8e4ec6", "#12a594", "#d6409f", "#978365")
# the cards: (what it does, title, the line under it, icon in icons.py). Some open the board, some ask
# a question and make the new file straight away.
ACTIONS = [("edit", "Edit content", "Type text, change words, add pictures, highlight, draw, sign and fill in forms",
            "text_edit_style"),
           ("merge", "Merge PDFs", "Put several PDFs together in the order you want", "merge"),
           ("organize", "Arrange pages", "Move, turn and delete pages, and see them as you go", "grid"),
           ("split", "Split", "Cut a PDF into several, page by page or in parts", "split_vertical"),
           ("extract", "Take out pages", "Save the pages you pick as a new PDF", "document_multiple"),
           ("compress", "Compress", "Make a big PDF small enough to send", "arrow_minimize"),
           ("searchable", "Make searchable", "Read the words on scanned pages, so you can search and copy them",
            "scan_text"),
           ("pictures", "Pictures to PDF", "Photos and scans into one PDF, on A4 or Letter if you want",
            "image_multiple"),
           ("topictures", "PDF to pictures", "Every page, or the ones you pick, as PNG or JPG", "image"),
           ("lock", "Lock", "Protect a PDF with a password", "lock_closed"),
           ("unlock", "Unlock", "Remove the password from a PDF that's yours", "lock_open")]
# the Converter's own PDF tools that are shown here too (when this Corefold has them)
MORE = [("pdf:numbers", "Page numbers", "Number every page", "text_t"),
        ("pdf:watermark", "Watermark", "Add text or a logo to every page", "paint_brush"),
        ("pdf:sign", "Sign", "Put your signature on it", "rename"),
        ("pdf:fit", "Fit a size limit", "Make it small enough for an email or a form", "target"),
        ("pdf:text", "Copy the text", "Pull all the text out into a .txt file", "document_text"),
        ("pdf:images", "Save the pictures inside", "Every picture in the PDF, as it is", "filmstrip_image")]
# how the cards are grouped on the page (the first group is shown big, on top)
GROUPS = [("", ("edit", "merge")),
          ("Pages", ("organize", "split", "extract")),
          ("Add to it", ("searchable", "pdf:sign", "pdf:numbers", "pdf:watermark")),
          ("Smaller or safer", ("compress", "pdf:fit", "lock", "unlock")),
          ("Turn it into something else", ("topictures", "pictures", "pdf:text", "pdf:images"))]
TIPS = {"merge": "Drag the pages into the order you want, then press Merge and save.",
        "organize": "Drag pages to move them. Select some to turn or delete them. Double-click a page to see it big.",
        "extract": "Select the pages you want (or type them in the box), then Save selected pages."}
PDF_FILTER = "PDF (*.pdf)"
PICTURE_ALIASES = {".jpeg", ".jfif", ".tif", ".heif"}


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def is_pdf(p: Path) -> bool:
    return p.is_file() and norm(p.suffix) == ".pdf"


def is_picture(p: Path) -> bool:
    return p.is_file() and norm(p.suffix) in IMAGE_EXT


def _tile(icon_name: str, ink: QColor, dpr: float, size: int = 36) -> QPixmap:
    """A card's icon on its soft rounded square, drawn once (a style sheet on every card was slow to set)."""
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(tokens.alpha(ink, 20))
    p.drawRoundedRect(0, 0, size, size, size * 0.28, size * 0.28)
    glyph = round(size * 0.55)
    p.drawPixmap((size - glyph) // 2, (size - glyph) // 2, icons.pixmap(icon_name, glyph, ink, dpr))
    p.end()
    return pm

class ActionCard(QFrame):
    """One thing Corefold PDF does: click it (or Enter) to start."""

    def __init__(self, key: str, title: str, line: str, icon_name: str, on_click, big: bool = False):
        super().__init__()
        self.key = key
        self.setObjectName("toolCard")
        self.setProperty("featured", "true" if big else "false")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(title)
        self.on_click = on_click
        t = tokens.current()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16) if big else lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(14 if big else 12)
        tile = QLabel()
        size = 48 if big else 36
        tile.setFixedSize(size, size)
        tile.setAlignment(Qt.AlignCenter)
        tile.setObjectName("pdfCardIcon")
        tile.setPixmap(_tile(icon_name, t.glyph, self.devicePixelRatioF(), size))
        lay.addWidget(tile, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(3 if big else 2)
        self.title = _label(title, "bigCardTitle" if big else "cardTitle")
        words.addWidget(self.title)
        sub = _label(line, "muted", wrap=True)
        sub.setMinimumWidth(1)
        words.addWidget(sub)
        words.addStretch()
        lay.addLayout(words, 1)
        for w in (tile, self.title, sub):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)

    def _down(self, down: bool):
        """Pressed in a little while the button is held, like a real button."""
        if (self.property("down") == "true") != down:
            self.setProperty("down", "true" if down else "false")
            self.style().unpolish(self)
            self.style().polish(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._down(True)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._down(False)
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.on_click()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.on_click()
        else:
            super().keyPressEvent(e)


class PageLook(QStyledItemDelegate):
    """A page on the board looks like a sheet of paper: a fine edge and a soft shadow around it (a white page on
    the light board was only its number)."""

    def paint(self, p: QPainter, opt, index):
        t = tokens.current()
        picked = bool(opt.state & QStyle.StateFlag.State_Selected)
        plain = QStyleOptionViewItem(opt)
        if picked:  # (a picked page keeps its own colours, with a ring round it, instead of being tinted blue)
            plain.state &= ~QStyle.StateFlag.State_Selected
            p.save()
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(t.accent_soft)
            p.drawRoundedRect(QRectF(opt.rect).adjusted(2, 2, -2, -2), 10, 10)
            p.restore()
        super().paint(p, plain, index)
        o = QStyleOptionViewItem(plain)
        self.initStyleOption(o, index)
        if o.icon.isNull():
            return
        widget = o.widget
        style = widget.style() if widget is not None else None
        if style is None:
            return
        box = style.subElementRect(QStyle.SubElement.SE_ItemViewItemDecoration, o, widget)
        size = o.icon.actualSize(o.decorationSize)
        sheet = QStyle.alignedRect(o.direction, o.decorationAlignment, size, box)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(sheet).adjusted(-0.5, -0.5, 0.5, 0.5)
        for grow, a in ((1.5, 26 if t.dark else 22), (3.0, 12 if t.dark else 9)):  # the shadow, fading out
            p.setPen(QPen(QColor(0, 0, 0, a), 1.5))
            p.drawRoundedRect(r.adjusted(-grow + 0.5, -grow + 1.5, grow - 0.5, grow + 0.5), 2 + grow, 2 + grow)
        p.setPen(QPen(tokens.alpha(t.text, 40 if t.dark else 34), 1))
        p.drawRoundedRect(r, 2, 2)
        if picked:
            p.setPen(QPen(t.accent, 2))
            p.drawRoundedRect(r.adjusted(-3, -3, 3, 3), 5, 5)
        p.restore()


class Board(QListWidget):
    """The page pictures. Dragging one moves it; files dropped from outside are added."""

    def __init__(self, page: "PdfPage"):
        super().__init__()
        self.pdf = page
        self.setObjectName("pdfBoard")
        self.setViewMode(QListView.IconMode)
        self.setIconSize(QSize(THUMB, THUMB))
        self.setGridSize(QSize(THUMB + 28, THUMB + 40))
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)  # (with internal moves: dragging reorders the pages)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDragEnabled(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSpacing(6)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideMiddle)
        self.setItemDelegate(PageLook(self))

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.Undo):
            self.pdf.undo()
        elif e.key() == Qt.Key_Delete:
            self.pdf.remove()
        else:
            super().keyPressEvent(e)

    def dragEnterEvent(self, e):
        if e.source() is None and e.mimeData().hasUrls():
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.source() is None and e.mimeData().hasUrls():
            e.acceptProposedAction()
            return
        super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.source() is None and e.mimeData().hasUrls():
            paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
            e.acceptProposedAction()
            self.pdf.add_files(paths)
            return
        super().dropEvent(e)


class PdfPage(QWidget):
    """Corefold PDF in the window: the cards, and the board when PDFs are open."""

    def __init__(self, controller: "Controller", header):
        super().__init__()
        self.c = controller
        self.setAcceptDrops(True)
        self.files: list[Path] = []
        self.passwords: dict[str, str] = {}
        self.counts: dict[int, int] = {}          # file -> its number of pages
        self.pixmaps: dict[tuple[int, int], QPixmap] = {}
        self.pending: list[Path] = []             # files waiting to be read, in the order given
        self.reading = False
        self.jobs: list = []                      # (kept until their answer is in)
        self.stops: list[threading.Event] = []
        # files whose small pictures are still to be drawn, (board round, file, PDF, password): drawn by one job,
        # one file after the other, once every file dropped is read (40 jobs at once, each waiting for the PDF
        # library in turn, kept the next files from even being counted: 40 PDFs took half a minute to show)
        self.thumb_queue: collections.deque = collections.deque()
        self.thumbing = False
        self.board_round = 0
        self.undo_stack: list[list[tuple[int, int, int]]] = []
        self.state: list[tuple[int, int, int]] = []
        self.saved_state: list[tuple[int, int, int]] | None = None
        self.mode = "organize"
        self.restoring = False
        # (with thousands of pages, both are done once for many changes: a selection of 4,000 pages
        # cleared, or 4,000 small pictures arriving, each looked through every page, and froze the window)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self._refresh)
        self.fresh: set[tuple[int, int]] = set()  # small pictures that came, not drawn on the board yet
        self.thumb_timer = QTimer(self)
        self.thumb_timer.setSingleShot(True)
        self.thumb_timer.setInterval(80)
        self.thumb_timer.timeout.connect(self._draw_fresh)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(12)
        lay.addLayout(header)
        self.banner = header.itemAt(0).widget() if header.count() else None
        self.stack = QStackedWidget()
        self.stack.currentChanged.connect(self._shown)
        self.stack.addWidget(self._start())
        self.stack.addWidget(QWidget())  # (the board is made the first time PDFs are opened)
        self._board_made = False
        self.editor = None                        # (made when a PDF is first opened to edit)
        self._edit_from = 0
        lay.addWidget(self.stack, 1)

    # ---- the cards

    def _start(self) -> QWidget:
        inner = QWidget()
        inner.setObjectName("scrollContent")
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        # Every card, in groups: editing and merging first and big, then pages, adding to a PDF, making it
        # smaller or safer, and turning it into something else. (The Converter's own PDF tools are cards here
        # too, so its catalog, which imports every converter, almost half a second, is only made when one is
        # clicked.)
        known = {key: (title, line, icon_name) for key, title, line, icon_name in ACTIONS + MORE}
        self.cards: dict[str, ActionCard] = {}
        self.grids: list[ResponsiveGrid] = []
        for n, (name, keys) in enumerate(GROUPS):
            if name:
                v.addSpacing(8)
                v.addWidget(_label(tr(name), "pdfGroup"))
            grid = ResponsiveGrid(min_width=320 if n == 0 else 230, spacing=10, max_columns=2 if n == 0 else 4)
            for key in keys:
                title, line, icon_name = known[key]
                self.cards[key] = grid.add(ActionCard(key, tr(title), tr(line), icon_name,
                                                      lambda k=key: self.start(k), big=n == 0))
            self.grids.append(grid)
            v.addWidget(grid)
            if n == 0:
                hint = _label(tr("Or drop PDFs or pictures anywhere on this page."), "subtle", wrap=True)
                v.addWidget(hint)
        self.grid = self.grids[0]
        v.addSpacing(8)
        row = QHBoxLayout()
        self.where = _label("", "subtle", wrap=True)
        self._show_where()
        change = QPushButton(tr("Change where"))
        change.setObjectName("small")
        change.setCursor(Qt.PointingHandCursor)
        change.clicked.connect(lambda: self.c.show_window("ConvOptions"))
        row.addWidget(self.where, 1)
        row.addWidget(change, 0, Qt.AlignTop)
        v.addLayout(row)
        v.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(inner)
        scroll.viewport().setAutoFillBackground(False)
        return scroll

    @staticmethod
    def _converter_tools() -> dict:
        from .converters import catalog
        return {t.key: t for t in catalog(False).get("pdf", [])}

    def start(self, key: str):
        """A card was clicked: pick the files, then ask what's needed and make the new file (or open
        the board)."""
        if key == "pictures":
            kinds = " ".join(f"*{ext}" for ext in sorted(IMAGE_EXT | PICTURE_ALIASES))
            names, _ = QFileDialog.getOpenFileNames(self, tr("Pick the pictures"), str(Path.home()),
                                                    f"{tr('Pictures')} ({kinds})")
            if names:
                self.pictures_to_pdf([Path(n) for n in names])
            return
        several = key in ("merge", "organize", "compress", "searchable", "topictures", "lock", "unlock") or \
            key.startswith("pdf:")
        title = tr("Pick the PDFs") if several else tr("Pick a PDF")
        if several:
            names, _ = QFileDialog.getOpenFileNames(self, title, str(Path.home()), PDF_FILTER)
        else:
            name, _ = QFileDialog.getOpenFileName(self, title, str(Path.home()), PDF_FILTER)
            names = [name] if name else []
        files = [Path(n) for n in names if n]
        if files:
            self.run(key, files)

    def run(self, key: str, files: list[Path]):
        from . import pdfdialogs as pd
        icon, dark = self.c.icon, self.c.theme.dark
        if key in ("merge", "organize", "extract"):
            self.open_board(files, key)
            return
        if key == "edit":
            self.open_editor(files[0])
            return
        if key.startswith("pdf:"):
            tool = self._converter_tools().get(key)
            if tool is not None:
                self.c.tool_chosen(tool, files)
            return
        if key == "searchable":
            from . import pdfocr
            why = pdfocr.check(files)
            if why:
                self.c.tell(tr("Make searchable"), why)
                return
        asks = {"split": (pd.SplitDialog, pdftools.SPLIT), "compress": (pd.CompressDialog, pdftools.COMPRESS),
                "searchable": (pd.SearchableDialog, _searchable_tool()),
                "topictures": (pd.ToPicturesDialog, pdftools.TO_PICTURES), "lock": (pd.PdfLockDialog, pdftools.LOCK),
                "unlock": (None, pdftools.UNLOCK)}
        dialog, tool = asks[key]
        if key == "lock":
            already = [f for f in files if pdftools.lock_state(f) == "locked"]
            if already and len(already) == len(files):
                self.c.tell(tr("Lock"), tr("This PDF already has a password"))
                return
            files = [f for f in files if f not in already]
        elif key == "unlock":
            files = [f for f in files if pdftools.lock_state(f) != "open"]
            if not files:
                self.c.tell(tr("Unlock"), tr("This PDF has no password"))
                return
        params = {}
        if dialog is not None:
            dlg = dialog(files, icon, dark)
            try:
                if not sheet.run(dlg):
                    return
                params = dlg.params()
            finally:
                dlg.deleteLater()
        self.c.start_job(tool, files, params)

    def pictures_to_pdf(self, files: list[Path]):
        from .pdfdialogs import PicturesDialog
        files = [f for f in files if is_picture(f)]
        if not files:
            return
        dlg = PicturesDialog(files, self.c.icon, self.c.theme.dark)
        try:
            if not sheet.run(dlg):
                return
            params = dlg.params()
        finally:
            dlg.deleteLater()
        self.c.start_job(pdftools.PICTURES, files, params)

    # ---- the editor

    def edit_picked(self):
        """The board's Edit content: the PDF of the page picked (or the only, or first, PDF on the board)."""
        items = self.board.selectedItems() if hasattr(self, "board") else []
        index = items[0].data(FILE) if items else 0
        if self.files and isinstance(index, int) and 0 <= index < len(self.files):
            self.open_editor(self.files[index])

    def open_editor(self, src: Path):
        """Opens one PDF in the editor (its password is asked for first, if it has one)."""
        if not is_pdf(src):
            return

        def look(_progress):
            return pdftools.lock_state(src), _count(src, self.passwords.get(str(src), ""))
        self._run(look, lambda _: None, lambda result: self._edit(src, result), "corefold-pdf-look")

    def _edit(self, src: Path, result):
        if not work.alive(self):
            return
        password, count = self.passwords.get(str(src), ""), 0
        if isinstance(result, Exception):
            log.warning("couldn't read %s: %s", src, result)
        else:
            state, count = result
            wrong = False
            while state == "locked" and not count:
                password = self.c.ask_password([src], wrong=wrong)
                if password is None:
                    return
                count = _count(src, password)
                wrong = not count
        if not count:
            self.c.tell(tr("Corefold PDF"), tr("{name} couldn't be read. It may be damaged.", name=short_name(src.name)))
            return
        if self.editor is None:
            from .pdfeditor import PdfEditor
            self.editor = PdfEditor(self)
            self.editor.closed.connect(lambda: self.stack.setCurrentIndex(self._edit_from))
            self.stack.addWidget(self.editor)
        if self.stack.currentWidget() is not self.editor:  # (closing it goes back there: the cards or the board)
            self._edit_from = self.stack.currentIndex()
        self.stack.setCurrentWidget(self.editor)
        self.editor.open(src, password)
        self.editor.view.setFocus()

    # ---- the board

    def _board(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        muted = self.c.theme.muted
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.close_btn = IconButton(tr("Close these PDFs"))
        self.close_btn.setIcon(icons.icon("arrow_right" if self.layoutDirection() == Qt.RightToLeft
                                          else "arrow_left", muted))
        self.close_btn.clicked.connect(lambda: self.close_board())
        bar.addWidget(self.close_btn)
        self.add_btn = QPushButton(tr("Add PDFs…"))
        self.add_btn.setIcon(icons.icon("add", muted))
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(self._choose_more)
        bar.addWidget(self.add_btn)
        bar.addSpacing(8)
        self.tool_btns: dict[str, IconButton] = {}
        for key, tip, name, fn in (("left", tr("Turn left"), "arrow_rotate_counterclockwise", lambda: self.turn(-90)),
                                   ("right", tr("Turn right"), "arrow_rotate_clockwise", lambda: self.turn(90)),
                                   ("delete", tr("Delete (Del)"), "delete", self.remove),
                                   ("view", tr("See it big"), "zoom_in", self.preview)):
            b = IconButton(tip)
            b.setIcon(icons.icon(name, muted, tokens.alpha(QColor(muted), 90)))
            b.clicked.connect(fn)
            bar.addWidget(b)
            self.tool_btns[key] = b
        self.edit_btn = QPushButton(tr("Edit content"))
        self.edit_btn.setIcon(icons.icon("text_edit_style", muted))
        self.edit_btn.setCursor(Qt.PointingHandCursor)
        self.edit_btn.setToolTip(tr("Type text, change words, add pictures, highlight, draw, sign and fill in forms"))
        self.edit_btn.clicked.connect(self.edit_picked)
        bar.addWidget(self.edit_btn)
        bar.addSpacing(4)
        undo = QPushButton(tr("Undo"))
        undo.setObjectName("flat")
        undo.setToolTip(tr("Undo (Ctrl + Z)"))
        undo.setCursor(Qt.PointingHandCursor)
        undo.clicked.connect(self.undo)
        bar.addWidget(undo)
        self.tool_btns["undo"] = undo
        bar.addStretch()
        self.pick = QLineEdit()
        self.pick.setObjectName("pdfPick")
        self.pick.setPlaceholderText(tr("Select pages: 1-3, 8"))
        self.pick.setClearButtonEnabled(True)
        self.pick.setMaximumWidth(220)
        self.pick.returnPressed.connect(self.select_typed)
        bar.addWidget(self.pick)
        all_btn = QPushButton(tr("Select all"))
        all_btn.setObjectName("flat")
        all_btn.setCursor(Qt.PointingHandCursor)
        all_btn.clicked.connect(lambda: (self.board.selectAll(), self.board.setFocus()))
        bar.addWidget(all_btn)
        v.addLayout(bar)
        self.tip = _label("", "muted", wrap=True)
        v.addWidget(self.tip)
        self.legend_box = QWidget()
        self.legend = FlowLayout(self.legend_box, 8)
        v.addWidget(self.legend_box)
        self.board = Board(self)
        self.board.itemSelectionChanged.connect(self._selection)
        self.board.itemDoubleClicked.connect(lambda _item: self.preview())
        self.board.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self._changed))
        v.addWidget(self.board, 1)
        foot = QHBoxLayout()
        self.info = _label("", "muted", wrap=True)
        foot.addWidget(self.info, 1)
        self.selected_btn = QPushButton(tr("Save selected pages"))
        self.selected_btn.setCursor(Qt.PointingHandCursor)
        self.selected_btn.clicked.connect(self._selected_menu)
        foot.addWidget(self.selected_btn)
        self.save_btn = QPushButton(tr("Save as a new PDF"))
        self.save_btn.setObjectName("primary")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(lambda: self.save("one"))
        foot.addWidget(self.save_btn)
        v.addLayout(foot)
        for keys, fn in ((QKeySequence("Ctrl+L"), lambda: self.turn(-90)),
                         (QKeySequence("Ctrl+R"), lambda: self.turn(90))):
            sc = QShortcut(keys, box)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        return box

    def _show_where(self):
        """Where the new files go: the Converter's choice (Settings › Converter), the same for every tool."""
        s = self.c.settings
        if getattr(s, "save_mode", "beside") == "folder":
            self.where.setText(tr("Your PDFs are never changed: every result is a new file in {folder}.",
                                  folder=s.output_folder))
        else:
            self.where.setText(tr("Your PDFs are never changed: every result is a new file next to the original."))

    def showEvent(self, e):
        super().showEvent(e)
        if hasattr(self, "where"):
            self._show_where()

    def _shown(self, index: int):
        if self.banner is not None:  # (the board gets the room of the tool's banner)
            self.banner.setVisible(index == 0)

    def _make_board(self):
        if not self._board_made:
            self._board_made = True
            placeholder = self.stack.widget(1)
            self.stack.insertWidget(1, self._board())
            self.stack.removeWidget(placeholder)
            placeholder.deleteLater()

    def open_board(self, files: list[Path], mode: str = "organize"):
        self._make_board()
        self.mode = mode
        self.stack.setCurrentIndex(1)
        self.tip.setText(tr(TIPS.get(mode, TIPS["organize"])))
        self.add_files(files)
        self._refresh()

    def add_files(self, paths: list[Path]):
        """Dropped or picked: PDFs go on the board (in the order given); only pictures? A PDF is made
        from them."""
        pdfs = [p for p in paths if is_pdf(p)]
        if not pdfs:
            pictures = [p for p in paths if is_picture(p)]
            if pictures:
                self.pictures_to_pdf(pictures)
            elif paths:
                self.c.tell(tr("Corefold PDF"), tr("Those aren't PDFs."))
            return
        self._make_board()
        if self.stack.currentIndex() == 0:
            self.mode = "merge" if len(pdfs) > 1 else "organize"
            self.stack.setCurrentIndex(1)
            self.tip.setText(tr(TIPS[self.mode]))
        self.pending += pdfs
        self._next()
        self._refresh()

    def _choose_more(self):
        names, _ = QFileDialog.getOpenFileNames(self, tr("Pick the PDFs"), str(self.files[-1].parent if self.files
                                                                              else Path.home()), PDF_FILTER)
        if names:
            self.add_files([Path(n) for n in names])

    def _next(self):
        """Reads the next waiting file: asks for its password if it has one, then draws its pages."""
        if self.reading or not self.pending:
            return
        src = self.pending.pop(0)
        self.reading = True

        def look(_progress):
            return pdftools.lock_state(src), _count(src, self.passwords.get(str(src), ""))
        self._run(look, lambda _: None, lambda result, s=src: self._looked(s, result), "corefold-pdf-look")

    def _looked(self, src: Path, result, wrong: bool = False):
        if not work.alive(self):
            return
        count = 0
        if isinstance(result, Exception):
            log.warning("couldn't read %s: %s", src, result)
        else:
            state, count = result
            while state == "locked" and not count:
                password = self.c.ask_password([src], wrong=wrong)
                if password is None:
                    break
                count = _count(src, password)
                wrong = not count
                if count:
                    self.passwords[str(src)] = password
        if not count:
            if not isinstance(result, Exception) and result[0] == "locked":
                self._say(tr("{name} needs its password, so it wasn't added.", name=short_name(src.name)))
            else:
                self._say(tr("{name} couldn't be read. It may be damaged.", name=short_name(src.name)))
            self.reading = False
            self._next()
            self._refresh()
            return
        fi = len(self.files)
        self.files.append(src)
        self.counts[fi] = count
        blank = self._blank()
        many = len(self.files) > 1 or self.mode == "merge"
        for i in range(count):
            item = QListWidgetItem(QIcon(blank), self._item_text(fi, i, many))
            item.setData(FILE, fi)
            item.setData(PAGE, i)
            item.setData(TURN, 0)
            item.setTextAlignment(Qt.AlignHCenter)
            item.setToolTip(f"{src.name} · {tr('Page {number}', number=i + 1)}")
            self.board.addItem(item)
        if many:
            for k in range(self.board.count()):
                it = self.board.item(k)
                it.setText(self._item_text(it.data(FILE), it.data(PAGE), True))
        self._add_legend(fi)
        self.thumb_queue.append((self.board_round, fi, src, self.passwords.get(str(src), "")))
        self.reading = False
        self._changed(added=True)
        self._next()
        self._draw_queued()

    def _draw_queued(self):
        """Starts the job that draws the small pictures, once the files dropped are all read."""
        if self.thumbing or self.pending or self.reading or not self.thumb_queue:
            return
        self.thumbing = True
        stop = threading.Event()
        self.stops.append(stop)
        queue, size = self.thumb_queue, round(THUMB * self.devicePixelRatioF())

        def go(progress):
            while queue and not stop.is_set():
                round_, fi, src, password = queue.popleft()
                _thumbnails(src, password, stop, lambda data, r=round_, f=fi: progress((r, f, data)), size)

        def done(_r):
            self.thumbing = False
            self._draw_queued()  # (files added while it finished)
        self._run(go, lambda got: self._thumb(got[1], got[2]) if got[0] == self.board_round else None, done,
                  "corefold-pdf-thumbs")

    def _run(self, fn, on_progress, on_done, name: str):
        """work.run, with the job kept until its answer is in (a job let go of sooner loses the pictures
        it sent that the window hadn't taken in yet)."""
        holder = []

        def done(result):
            if holder and holder[0] in self.jobs:
                self.jobs.remove(holder[0])
            if work.alive(self):
                on_done(result)
        holder.append(work.run(fn, on_progress, done, name=name))
        self.jobs.append(holder[0])

    def _blank(self) -> QPixmap:
        pm = QPixmap(THUMB * 3 // 4, THUMB)
        pm.fill(QColor("#ffffff"))
        return pm

    def _item_text(self, fi: int, pi: int, many: bool) -> str:
        if many:
            return f"{short_name(self.files[fi].stem, 16)} · {pi + 1}"
        return tr("Page {number}", number=pi + 1)

    def _add_legend(self, fi: int):
        chip = QWidget()
        h = QHBoxLayout(chip)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background: {FILE_COLORS[fi % len(FILE_COLORS)]}; border-radius: 5px;")
        h.addWidget(dot)
        # (the name and the count in labels of their own: a Latin file name and Arabic words never get mixed up)
        h.addWidget(_label(short_name(self.files[fi].name, 40), "muted"))
        h.addWidget(_label("·", "muted"))
        h.addWidget(_label(tr("{count} pages", count=self.counts[fi]), "muted"))
        h.addSpacing(6)
        self.legend.addWidget(chip)
        self.legend_box.updateGeometry()

    def _thumb(self, fi: int, data):
        if not work.alive(self) or fi >= len(self.files):
            return
        pi, raw, w, h = data
        pm = QPixmap.fromImage(QImage(raw, w, h, 4 * w, QImage.Format.Format_RGBA8888).copy())
        pm.setDevicePixelRatio(self.devicePixelRatioF())
        self.pixmaps[(fi, pi)] = pm
        self.fresh.add((fi, pi))
        if not self.thumb_timer.isActive():
            self.thumb_timer.start()

    def _draw_fresh(self):
        fresh, self.fresh = self.fresh, set()
        if not fresh or not work.alive(self):
            return
        for k in range(self.board.count()):
            item = self.board.item(k)
            if (item.data(FILE), item.data(PAGE)) in fresh:
                self._draw(item)

    def _draw(self, item: QListWidgetItem):
        fi, pi = item.data(FILE), item.data(PAGE)
        pm = self.pixmaps.get((fi, pi))
        if pm is None:
            return
        turn = item.data(TURN) % 360
        if turn:
            # (a quarter turn moves pixels without changing them: the smooth way only cost time, 4,000 pages 1.5 s)
            pm = pm.transformed(QTransform().rotate(turn), Qt.FastTransformation)
        if len(self.files) > 1:  # a stripe in the file's colour along the bottom
            pm = QPixmap(pm)
            p = QPainter(pm)
            ratio = pm.devicePixelRatio()
            p.fillRect(0, round(pm.height() / ratio) - 5, round(pm.width() / ratio), 5,
                       QColor(FILE_COLORS[fi % len(FILE_COLORS)]))
            p.end()
        item.setIcon(QIcon(pm))

    # ---- changing the pages

    def items(self) -> list[QListWidgetItem]:
        return [self.board.item(r) for r in range(self.board.count())]

    def plan(self) -> list[tuple[int, int, int]]:
        return [(int(i.data(FILE)), int(i.data(PAGE)), int(i.data(TURN)) % 360) for i in self.items()]

    def selected(self) -> list[QListWidgetItem]:
        return sorted(self.board.selectedItems(), key=self.board.row)

    def turn(self, degrees: int):
        chosen = self.selected()
        if not chosen:
            return
        for item in chosen:
            item.setData(TURN, (item.data(TURN) + degrees) % 360)
            self._draw(item)
        self._changed()

    def remove(self):
        rows = sorted(self.board.row(i) for i in self.board.selectedItems())
        if not rows:
            return
        if len(rows) >= self.board.count():
            self._say(tr("A PDF needs at least one page."))
            return
        for r in reversed(rows):
            self.board.takeItem(r)
        self.board.setCurrentRow(min(rows[0], self.board.count() - 1))
        self._changed()

    def undo(self):
        if not self.undo_stack:
            return
        self.restoring = True
        try:
            self._rebuild(self.undo_stack.pop())
        finally:
            self.restoring = False
        self.state = self.plan()
        self._refresh()

    def _rebuild(self, plan: list[tuple[int, int, int]]):
        many = len(self.files) > 1
        self.board.clear()
        blank = self._blank()
        for fi, pi, turn in plan:
            item = QListWidgetItem(QIcon(blank), self._item_text(fi, pi, many))
            item.setData(FILE, fi)
            item.setData(PAGE, pi)
            item.setData(TURN, turn)
            item.setTextAlignment(Qt.AlignHCenter)
            item.setToolTip(f"{self.files[fi].name} · {tr('Page {number}', number=pi + 1)}")
            self.board.addItem(item)
            self._draw(item)

    def select_typed(self):
        """Pages typed in the box ("1-3, 8": their place on the board now) are selected."""
        try:
            parts = pdftools.parse_ranges(self.pick.text(), self.board.count())
        except ValueError as e:
            self._say(str(e))
            return
        self.board.clearSelection()
        for part in parts:
            for r in part:
                self.board.item(r).setSelected(True)
        if parts:
            self.board.scrollToItem(self.board.item(parts[0][0]))
        self.board.setFocus()
        self.info.setStyleSheet("")

    def _changed(self, added: bool = False):
        if self.restoring:
            return
        now = self.plan()
        if now != self.state:
            if not added:  # (adding a PDF isn't undone: its pages can be deleted)
                self.undo_stack = (self.undo_stack + [self.state])[-50:]
            else:
                self.undo_stack = []
            self.state = now
        if added and self.saved_state is None and len(self.files) == 1:
            self.saved_state = now  # (a file as it was opened: nothing to save yet)
        self._refresh()

    def _selection(self):
        self.refresh_timer.start()

    def _refresh(self):
        n = self.board.count()
        chosen = len(self.board.selectedItems())
        for key in ("left", "right", "delete"):
            self.tool_btns[key].setEnabled(chosen > 0)
        self.tool_btns["view"].setEnabled(n > 0)
        self.tool_btns["undo"].setEnabled(bool(self.undo_stack))
        self.selected_btn.setEnabled(chosen > 0)
        self.save_btn.setText(tr("Merge and save") if len(self.files) > 1 else tr("Save as a new PDF"))
        untouched = len(self.files) == 1 and self.plan() == [(0, i, 0) for i in range(self.counts.get(0, 0))]
        self.save_btn.setEnabled(n > 0 and not untouched)
        if self.reading or self.pending:
            text = tr("Reading the pages…")
        elif not n:
            text = ""
        else:
            text = tr("{count} pages", count=n)
            if len(self.files) == 1:  # (which PDF this is: the tool's banner, with its name, is hidden on the board)
                text = f"{ltr(short_name(self.files[0].name, 48))}  ·  {text}"
            if chosen:
                text += "  ·  " + tr("{count} selected", count=chosen)
            if len(self.files) > 1:
                text += "  ·  " + tr("from {count} PDFs", count=len(self.files))
        self.info.setText(text)
        self.legend_box.setVisible(len(self.files) > 1)

    def _say(self, text: str):
        self.info.setText(text)

    # ---- seeing a page big

    def preview(self):
        from .pdfdialogs import PreviewDialog
        if not self.board.count():
            return
        start = self.board.row(self.selected()[0]) if self.selected() else 0
        plan = self.plan()

        def render(k: int) -> QPixmap | None:
            fi, pi, turn = plan[k]
            return _render_big(self.files[fi], self.passwords.get(str(self.files[fi]), ""), pi, turn,
                               self.devicePixelRatioF())
        dlg = PreviewDialog(tr("Corefold PDF"), self.c.icon, self.c.theme.dark, render, start, len(plan))
        self.preview_dialog = dlg
        try:
            sheet.run(dlg)
        finally:
            self.preview_dialog = None
            dlg.deleteLater()

    # ---- saving

    def _selected_menu(self):
        m = QMenu(self)
        round_menu(m)
        muted = self.c.theme.muted
        for text, how, name in ((tr("As one PDF"), "one", "document_pdf"),
                                (tr("Each page as its own PDF"), "each", "document_multiple"),
                                (tr("As pictures…"), "images", "image")):
            act = m.addAction(icons.icon(name, muted), text)
            act.triggered.connect(lambda _=False, h=how: self.save(h, only_selected=True))
        sheet.menu(m, self.selected_btn, above=True)
        m.deleteLater()

    def save(self, how: str, only_selected: bool = False):
        chosen = self.selected() if only_selected else self.items()
        pages = [(int(i.data(FILE)), int(i.data(PAGE)), int(i.data(TURN)) % 360) for i in chosen]
        if not pages:
            return
        params = {"pages": pages, "passwords": dict(self.passwords), "how": how,
                  "name": self._name(pages, only_selected)}
        if how == "images":
            from .pdfdialogs import ToPicturesDialog
            dlg = ToPicturesDialog([self.files[pages[0][0]]], self.c.icon, self.c.theme.dark, ranges=False)
            try:
                if not sheet.run(dlg):
                    return
                params.update(dlg.params())
            finally:
                dlg.deleteLater()
        plan = self.plan()

        def done(ok: bool):  # (the new order counts as saved only once the PDF is really written)
            if ok and not only_selected and work.alive(self):
                self.saved_state = plan
        self.c.start_job(pdftools.ARRANGED, list(self.files), params, then=done)

    def _name(self, pages, only_selected: bool) -> str:
        first = self.files[pages[0][0]]
        if only_selected and len({fi for fi, _, _ in pages}) == 1:
            return f"{first.stem} (pages {pdftools.ranges_text([pi for _, pi, _ in pages])})".replace(", ", ",")
        if only_selected:
            return f"{first.stem} (selected)"
        return f"{first.stem} merged" if len(self.files) > 1 else f"{first.stem} (edited)"

    # ---- closing the board

    def board_unsaved(self) -> bool:
        return self._board_made and self.board.count() > 0 and self.plan() != self.saved_state and \
            not (len(self.files) == 1 and self.plan() == [(0, i, 0) for i in range(self.counts.get(0, 0))])

    def unsaved(self) -> bool:
        """Work that isn't saved yet: a new order of pages on the board, or changes in the editor (asked
        before Corefold quits or remakes its window)."""
        editing = self.editor is not None and self.editor.src is not None and self.editor.unsaved()
        return editing or self.board_unsaved()

    def close_renderer(self):
        """Lets go of the PDFs (when the page goes away): the editor's drawing thread and the page pictures
        being made stop, and the files aren't kept open."""
        for stop in self.stops:
            stop.set()
        if self.editor is not None:
            self.editor.close_renderer()

    def close_board(self, ask: bool = True) -> bool:
        if not self._board_made:
            return True
        if ask and self.board_unsaved():
            box = QMessageBox(QMessageBox.Question, tr("Close these PDFs?"),
                              tr("The new order of the pages isn't saved yet. Your PDFs themselves are fine."),
                              parent=self)
            close = box.addButton(tr("Close"), QMessageBox.DestructiveRole)
            box.addButton(tr("Cancel"), QMessageBox.RejectRole)
            sheet.run(box)
            chosen = box.clickedButton()
            box.deleteLater()
            if chosen is not close:
                return False
        for stop in self.stops:
            stop.set()
        self.stops, self.pending, self.reading = [], [], False
        self.thumb_queue.clear()
        self.board_round += 1
        self.files, self.passwords, self.counts, self.pixmaps = [], {}, {}, {}
        self.undo_stack, self.state, self.saved_state = [], [], None
        self.board.clear()
        while self.legend.count():
            item = self.legend.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.pick.clear()
        self.stack.setCurrentIndex(0)
        return True

    def go_back(self) -> bool:
        """Back (Alt + Left, the mouse's back button, Esc): from the board or the editor to the cards first."""
        if self.editor is not None and self.stack.currentWidget() is self.editor:
            return self.editor.go_back()
        if self.stack.currentIndex() == 1:
            self.close_board()
            return True
        return False

    # ---- files dropped anywhere on the page

    def dragEnterEvent(self, e):
        if any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            if self.editor is not None and self.stack.currentWidget() is self.editor:
                # (dropped on the editor: a picture goes on the page; PDFs aren't put on the hidden board)
                pictures = [p for p in paths if is_picture(p)]
                if pictures:
                    self.editor.add_picture(pictures[0])
                return
            self.add_files(paths)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete and self.stack.currentIndex() == 1:
            self.remove()
            return
        super().keyPressEvent(e)


# ----------------------------------------------------------------------------- off the window's thread

def _searchable_tool():
    from .pdfocr import SEARCHABLE
    return SEARCHABLE


def _count(src: Path, password: str) -> int:
    """Its number of pages, or 0 when it can't be opened (damaged, or the password is missing)."""
    try:
        import pypdfium2 as pdfium
        with pdftools.PDFIUM:
            doc = pdfium.PdfDocument(str(src), password=password or None)
            try:
                return len(doc)
            finally:
                doc.close()
    except Exception:
        log.debug("couldn't open %s", src, exc_info=True)
        return 0


def _thumbnails(src: Path, password: str, stop: threading.Event, progress, size: int):
    """Small pictures of every page, one at a time."""
    with pdftools.PDFIUM:
        doc = pdftools.open_pdfium(src, password)  # (with what's typed in its form)
        count = len(doc)
    try:
        for i in range(count):
            if stop.is_set():
                return
            try:
                with pdftools.PDFIUM:  # (a page at a time: a preview asked for meanwhile isn't kept waiting)
                    page = doc[i]
                    w, h = page.get_size()
                    im = page.render(scale=size / max(w, h, 1)).to_pil().convert("RGBA")
                    page.close()
                progress((i, im.tobytes("raw", "RGBA"), im.width, im.height))
            except Exception:
                log.debug("couldn't draw page %s of %s", i + 1, src, exc_info=True)
    finally:
        with pdftools.PDFIUM:
            doc.close()


def _render_big(src: Path, password: str, index: int, turn: int, dpr: float) -> QPixmap | None:
    try:
        with pdftools.PDFIUM:
            doc = pdftools.open_pdfium(src, password)
            try:
                page = doc[index]
                w, h = page.get_size()
                im = page.render(scale=1300 * dpr / max(w, h, 1), rotation=turn % 360).to_pil().convert("RGBA")
                page.close()
            finally:
                doc.close()
    except Exception:
        log.debug("couldn't draw page %s of %s big", index + 1, src, exc_info=True)
        return None
    pm = QPixmap.fromImage(QImage(im.tobytes("raw", "RGBA"), im.width, im.height, 4 * im.width,
                                  QImage.Format.Format_RGBA8888).copy())
    pm.setDevicePixelRatio(dpr)
    return pm
