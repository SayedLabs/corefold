"""Corefold PDF's page: what you can do with PDFs, as cards, and a board of page pictures where
pages from one or more PDFs are put in order, turned, deleted and saved as a new PDF.

Nothing here is loaded unless Corefold PDF is added in the Store and its page is opened."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QImage, QKeySequence, QPainter, QPixmap, QShortcut, QTransform
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListView,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea,
                               QStackedWidget, QVBoxLayout, QWidget)

from . import icons, pdftools, work
from .atomic import tokens
from .atomic.widgets import FlowLayout, IconButton, ResponsiveGrid
from .core import IMAGE_EXT, norm, short_name
from .i18n import tr
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
ACTIONS = [("merge", "Merge PDFs", "Put several PDFs together in the order you want", "merge"),
           ("organize", "Arrange pages", "Move, turn and delete pages, and see them as you go", "grid"),
           ("split", "Split", "Cut a PDF into several, page by page or in parts", "split_vertical"),
           ("extract", "Take out pages", "Save the pages you pick as a new PDF", "document_multiple"),
           ("compress", "Compress", "Make a big PDF small enough to send", "arrow_minimize"),
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


class ActionCard(QFrame):
    """One thing Corefold PDF does: click it (or Enter) to start."""

    def __init__(self, key: str, title: str, line: str, icon_name: str, on_click):
        super().__init__()
        self.key = key
        self.setObjectName("toolCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(title)
        self.on_click = on_click
        t = tokens.current()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)
        tile = QLabel()
        tile.setFixedSize(36, 36)
        tile.setAlignment(Qt.AlignCenter)
        tile.setObjectName("pdfCardIcon")
        tile.setPixmap(icons.pixmap(icon_name, 20, t.accent, self.devicePixelRatioF()))
        tile.setStyleSheet(f"QLabel#pdfCardIcon {{ background: {tokens.alpha(t.accent, 40).name(QColor.HexArgb)};"
                           f" border-radius: 10px; }}")
        lay.addWidget(tile, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(2)
        self.title = _label(title, "cardTitle")
        words.addWidget(self.title)
        sub = _label(line, "muted", wrap=True)
        sub.setMinimumWidth(1)
        words.addWidget(sub)
        words.addStretch()
        lay.addLayout(words, 1)
        for w in (tile, self.title, sub):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.on_click()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.on_click()
        else:
            super().keyPressEvent(e)


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
        self.undo_stack: list[list[tuple[int, int, int]]] = []
        self.state: list[tuple[int, int, int]] = []
        self.saved_state: list[tuple[int, int, int]] | None = None
        self.mode = "organize"
        self.restoring = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(12)
        lay.addLayout(header)
        self.banner = header.itemAt(0).widget() if header.count() else None
        self.stack = QStackedWidget()
        self.stack.currentChanged.connect(self._shown)
        self.stack.addWidget(self._start())
        self.stack.addWidget(self._board())
        lay.addWidget(self.stack, 1)

    # ---- the cards

    def _start(self) -> QWidget:
        from .panel import DropZone
        inner = QWidget()
        inner.setObjectName("scrollContent")
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        self.drop = DropZone(None, None, title=tr("Drop PDFs here to see their pages"),
                             sub=tr("Or drop pictures to make a PDF from them. Everything stays on your PC."),
                             hot_title=tr("Let go"),
                             buttons=[(tr("Open PDFs…"), lambda: self.start("organize"), "primary"),
                                      (tr("Pictures to PDF…"), lambda: self.start("pictures"))])
        self.drop.dropped.connect(self.add_files)
        v.addWidget(self.drop)
        v.addWidget(_label(tr("What do you want to do?"), "h2"))
        self.grid = ResponsiveGrid(min_width=250, spacing=10, max_columns=3)
        self.cards: dict[str, ActionCard] = {}
        for key, title, line, icon_name in ACTIONS:
            self.cards[key] = self.grid.add(ActionCard(key, tr(title), tr(line), icon_name,
                                                       lambda k=key: self.start(k)))
        tools = self._converter_tools()
        for key, title, line, icon_name in MORE:
            if key in tools:
                self.cards[key] = self.grid.add(ActionCard(key, tr(title), tr(line), icon_name,
                                                           lambda k=key: self.start(k)))
        v.addWidget(self.grid)
        v.addWidget(_label(tr("Your PDFs are never changed: every result is a new file next to the original."),
                           "subtle", wrap=True))
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
        several = key in ("merge", "organize", "compress", "topictures", "lock", "unlock") or key.startswith("pdf:")
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
        if key.startswith("pdf:"):
            tool = self._converter_tools().get(key)
            if tool is not None:
                self.c.tool_chosen(tool, files)
            return
        asks = {"split": (pd.SplitDialog, pdftools.SPLIT), "compress": (pd.CompressDialog, pdftools.COMPRESS),
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
                if not dlg.exec():
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
            if not dlg.exec():
                return
            params = dlg.params()
        finally:
            dlg.deleteLater()
        self.c.start_job(pdftools.PICTURES, files, params)

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

    def _shown(self, index: int):
        if self.banner is not None:  # (the board gets the room of the tool's banner)
            self.banner.setVisible(index == 0)

    def open_board(self, files: list[Path], mode: str = "organize"):
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
        stop = threading.Event()
        self.stops.append(stop)
        password = self.passwords.get(str(src), "")
        dpr = self.devicePixelRatioF()
        self._run(lambda progress: _thumbnails(src, password, stop, progress, round(THUMB * dpr)),
                  lambda data, f=fi: self._thumb(f, data), lambda _r: None, "corefold-pdf-thumbs")
        self.reading = False
        self._changed(added=True)
        self._next()

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
        for k in range(self.board.count()):
            item = self.board.item(k)
            if item.data(FILE) == fi and item.data(PAGE) == pi:
                self._draw(item)

    def _draw(self, item: QListWidgetItem):
        fi, pi = item.data(FILE), item.data(PAGE)
        pm = self.pixmaps.get((fi, pi))
        if pm is None:
            return
        turn = item.data(TURN) % 360
        if turn:
            pm = pm.transformed(QTransform().rotate(turn), Qt.SmoothTransformation)
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
        self._refresh()

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
            dlg.exec()
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
        m.exec(self.selected_btn.mapToGlobal(self.selected_btn.rect().topLeft()))
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
                if not dlg.exec():
                    return
                params.update(dlg.params())
            finally:
                dlg.deleteLater()
        self.c.start_job(pdftools.ARRANGED, list(self.files), params)
        if not only_selected:
            self.saved_state = self.plan()

    def _name(self, pages, only_selected: bool) -> str:
        first = self.files[pages[0][0]]
        if only_selected and len({fi for fi, _, _ in pages}) == 1:
            return f"{first.stem} (pages {pdftools.ranges_text([pi for _, pi, _ in pages])})".replace(", ", ",")
        if only_selected:
            return f"{first.stem} (selected)"
        return f"{first.stem} merged" if len(self.files) > 1 else f"{first.stem} (edited)"

    # ---- closing the board

    def unsaved(self) -> bool:
        return self.board.count() > 0 and self.plan() != self.saved_state and \
            not (len(self.files) == 1 and self.plan() == [(0, i, 0) for i in range(self.counts.get(0, 0))])

    def close_board(self, ask: bool = True) -> bool:
        if ask and self.unsaved():
            box = QMessageBox(QMessageBox.Question, tr("Close these PDFs?"),
                              tr("The new order of the pages isn't saved yet. Your PDFs themselves are fine."),
                              parent=self)
            close = box.addButton(tr("Close"), QMessageBox.DestructiveRole)
            box.addButton(tr("Cancel"), QMessageBox.RejectRole)
            box.exec()
            chosen = box.clickedButton()
            box.deleteLater()
            if chosen is not close:
                return False
        for stop in self.stops:
            stop.set()
        self.stops, self.pending, self.reading = [], [], False
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
        """Back (Alt + Left, the mouse's back button, Esc): from the board to the cards first."""
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
            self.add_files(paths)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete and self.stack.currentIndex() == 1:
            self.remove()
            return
        super().keyPressEvent(e)


# ----------------------------------------------------------------------------- off the window's thread

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
    import pypdfium2 as pdfium
    with pdftools.PDFIUM:
        doc = pdfium.PdfDocument(str(src), password=password or None)
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
        import pypdfium2 as pdfium
        with pdftools.PDFIUM:
            doc = pdfium.PdfDocument(str(src), password=password or None)
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
