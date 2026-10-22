"""The "Pages" window: turn, delete and reorder the pages of a PDF, then save a new copy."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap, QTransform
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem, \
    QPushButton

from . import icons
from .dialogs import Base, describe
from .i18n import tr

log = logging.getLogger(__name__)

THUMB = 150
INDEX, TURN = Qt.UserRole, Qt.UserRole + 1


class _Rendered(QObject):
    page = Signal(int, object)   # page index, (raw RGBA bytes, width, height)
    count = Signal(int)


def render_thumbnails(src: Path, signals: _Rendered, stop: threading.Event, size: int = THUMB):
    """Draws small pictures of every page, one by one, in the background."""
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(src))
    except Exception:
        log.debug("couldn't open %s for thumbnails", src, exc_info=True)
        signals.count.emit(0)
        return
    try:
        n = len(pdf)
        signals.count.emit(n)
        for i in range(n):
            if stop.is_set():
                return
            try:
                page = pdf[i]
                w, h = page.get_size()
                scale = size / max(w, h, 1)
                im = page.render(scale=scale).to_pil().convert("RGBA")
                page.close()
                signals.page.emit(i, (im.tobytes("raw", "RGBA"), im.width, im.height))
            except RuntimeError:
                return  # the window was closed
            except Exception:
                log.debug("couldn't draw page %s", i + 1, exc_info=True)
    finally:
        pdf.close()


class PagesDialog(Base):
    def __init__(self, files, icon, dark):
        super().__init__(tr("Pages"), describe(files), icon, dark)
        self.src = files[0]
        self.setMinimumSize(640, 520)
        self.pixmaps: dict[int, QPixmap] = {}
        self.total = 0

        self.list = QListWidget()
        self.list.setViewMode(QListView.IconMode)
        self.list.setIconSize(QSize(THUMB, THUMB))
        self.list.setGridSize(QSize(THUMB + 24, THUMB + 34))
        self.list.setResizeMode(QListView.Adjust)
        # "Static" + internal moves = dragging a page reorders the pages (any other movement in icon
        # mode would just park the picture somewhere else on the grid)
        self.list.setMovement(QListView.Static)
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDragEnabled(True)
        self.list.viewport().setAcceptDrops(True)
        self.list.setDropIndicatorShown(True)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setSpacing(6)
        self.list.itemSelectionChanged.connect(self._selection)
        self.list.model().rowsMoved.connect(lambda *_: self._changed())

        self.left = QPushButton(tr("Turn left"))
        self.right = QPushButton(tr("Turn right"))
        self.delete = QPushButton(tr("Delete"))
        ink, faint = ("#e6f0ee", "#5e7470") if dark else ("#11201e", "#7e8c89")  # text colours of the theme
        for b, name in ((self.left, "arrow_rotate_counterclockwise"), (self.right, "arrow_rotate_clockwise"),
                        (self.delete, "delete")):
            b.setIcon(icons.icon(name, ink, faint))
            b.setIconSize(icons.SMALL)
        self.up = QPushButton("◀  " + tr("Move back"))
        self.down = QPushButton(tr("Move forward") + "  ▶")
        self.left.clicked.connect(lambda: self.turn(-90))
        self.right.clicked.connect(lambda: self.turn(90))
        self.delete.clicked.connect(self.remove)
        self.up.clicked.connect(lambda: self.move(-1))
        self.down.clicked.connect(lambda: self.move(1))
        row = QHBoxLayout()
        for b in (self.left, self.right, self.delete, self.up, self.down):
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        row.addStretch()
        self.lay.addLayout(row)
        self.lay.addWidget(self.list, 1)
        self.info = QLabel(tr("Loading pages…"))
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        self.lay.addWidget(self.info)
        self.buttons(tr("Save as a new PDF"))

        self.rendered = _Rendered(self)
        self.rendered.count.connect(self._count)
        self.rendered.page.connect(self._page)
        self.stop = threading.Event()
        threading.Thread(target=render_thumbnails, args=(self.src, self.rendered, self.stop), daemon=True).start()
        self._selection()
        self.ok.setEnabled(False)

    # ---- filling in

    def _count(self, n: int):
        if not n:
            self.info.setText(tr("This PDF couldn't be read."))
            self.ok.setEnabled(False)
            return
        self.total = n
        blank = QPixmap(THUMB * 3 // 4, THUMB)
        blank.fill(Qt.white)
        for i in range(n):
            item = QListWidgetItem(QIcon(blank), tr("Page {number}", number=i + 1))
            item.setData(INDEX, i)
            item.setData(TURN, 0)
            item.setTextAlignment(Qt.AlignHCenter)
            self.list.addItem(item)
        self._changed()

    def _page(self, i: int, data):
        raw, w, h = data
        pm = QPixmap.fromImage(QImage(raw, w, h, 4 * w, QImage.Format.Format_RGBA8888).copy())
        self.pixmaps[i] = pm
        for item in self._items():
            if item.data(INDEX) == i:
                self._draw(item)

    def _draw(self, item: QListWidgetItem):
        pm = self.pixmaps.get(item.data(INDEX))
        if pm is None:
            return
        turn = item.data(TURN) % 360
        if turn:
            pm = pm.transformed(QTransform().rotate(turn), Qt.SmoothTransformation)
        item.setIcon(QIcon(pm))

    # ---- editing

    def _items(self) -> list[QListWidgetItem]:
        return [self.list.item(r) for r in range(self.list.count())]

    def _selected_rows(self) -> list[int]:
        return sorted(self.list.row(i) for i in self.list.selectedItems())

    def turn(self, degrees: int):
        for item in self.list.selectedItems():
            item.setData(TURN, (item.data(TURN) + degrees) % 360)
            self._draw(item)
        self._changed()

    def remove(self):
        rows = self._selected_rows()
        if not rows or len(rows) >= self.list.count():
            if rows:
                self.info.setText(tr("A PDF needs at least one page."))
            return
        for r in reversed(rows):
            self.list.takeItem(r)
        self.list.setCurrentRow(min(rows[0], self.list.count() - 1))
        self._changed()

    def move(self, step: int):
        rows = self._selected_rows()
        if not rows:
            return
        if (step < 0 and rows[0] == 0) or (step > 0 and rows[-1] == self.list.count() - 1):
            return
        items = [self.list.takeItem(r) for r in reversed(rows)][::-1]
        start = rows[0] + step
        for k, item in enumerate(items):
            self.list.insertItem(start + k, item)
        self.list.clearSelection()
        for item in items:
            item.setSelected(True)
        self._changed()

    def _selection(self):
        has = bool(self.list.selectedItems())
        for b in (self.left, self.right, self.delete, self.up, self.down):
            b.setEnabled(has)

    def _changed(self):
        pages = self.params()["pages"]
        n = len(pages)
        untouched = pages == [(i, 0) for i in range(self.total)]
        self.info.setText(tr("{count} pages · drag them to change the order", count=n) if n else
                          tr("Loading pages…"))
        self.ok.setEnabled(n > 0 and not untouched)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete:  # the Delete key removes the selected pages
            self.remove()
            return
        super().keyPressEvent(e)

    def done(self, r):
        self.stop.set()
        super().done(r)

    def params(self) -> dict:
        return {"pages": [(int(i.data(INDEX)), int(i.data(TURN)) % 360) for i in self._items()]}
