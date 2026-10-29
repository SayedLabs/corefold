"""A window that shows what's inside an archive and lets you take out, open, add or remove files."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (QFileSystemWatcher, QItemSelectionModel, QMimeData, QModelIndex, QObject, Qt, QTimer, QUrl,
                            Signal)
from PySide6.QtGui import QDrag, QGuiApplication, QIcon, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QFileIconProvider, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
                               QSplitter, QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import archives as ar
from . import sheet
from .core import APP_NAME, Cancelled, Tool, human_size, short_name
from .i18n import ltr, tr
from .theme import round_menu
from .winutil import reveal_in_explorer, set_title_bar_dark

if TYPE_CHECKING:
    from .app import Controller

PATH_ROLE = Qt.UserRole
DIR_ROLE = Qt.UserRole + 1
EAGER_LIMIT = 3000  # archives with more items than this fill their folders in only when opened
FIRST_ROWS = 1500  # a folder with more files than this shows these at once, and the rest a moment later
MORE_ROWS = 1500
SEARCH_FILLS = 2000  # a search opens the folders of this many matches at most (one letter can match 200,000)
LOOK_STEP = 250  # rows a search goes through before it looks at the clock
DRAG_LIMIT = 256 << 20  # bigger than this (unpacked): use Extract, which shows progress and can be cancelled
DRAG_KEEP_MS = 5 * 60_000  # the temporary copies made for a drag are removed this long after the drop


def _inside_any(path: str, folders: set) -> bool:
    """True if path is inside one of these folders (looks at its parents, not at every folder)."""
    parent = path.rpartition("/")[0]
    while parent:
        if parent in folders:
            return True
        parent = parent.rpartition("/")[0]
    return False


_DIGITS = re.compile(r"(\d+)")


def _natural(text: str) -> list:
    """Sort key where "report-2" comes before "report-10", like in File Explorer."""
    parts = _DIGITS.split(text.lower())
    return [(0, int(x), "") if x.isdigit() else (1, 0, x) for x in parts]


class Contents:
    """What's in an archive, folder by folder. For a big archive it's worked out in the background, with
    the listing (100,000 files in one folder held the window 0.33 s when it was done on opening)."""

    def __init__(self, entries: list[ar.Entry]):
        self.entries = entries
        self.dirs: dict[str, list] = {"": []}    # folder -> the folders in it
        self.files: dict[str, list] = {"": []}   # folder -> (path, entry) of the files in it
        self.dir_time: dict[str, str] = {}
        self.count = self.size = 0
        self.locked = False                      # a file in it is locked with a password
        dirs, files = self.dirs, self.files

        def ensure(path: str):
            if path in dirs:
                return
            parent = path.rpartition("/")[0]
            ensure(parent)
            dirs[path] = []
            files[path] = []
            dirs[parent].append(path)

        for e in entries:
            path = e.path.strip("/")
            if not path:
                continue
            if e.is_dir:
                ensure(path)
                if e.modified:
                    self.dir_time[path] = e.modified[:16]
                continue
            parent = path.rpartition("/")[0]
            ensure(parent)
            files[parent].append((path, e))
            self.count += 1
            self.size += e.size
            self.locked = self.locked or e.encrypted
        self.nested = ar.holds_archives(entries)
        self.top = keyed_rows(self, "", 0, True)  # (the first folder in the usual order, ready to show)

    def keyed(self, folder: str, col: int, ascending: bool) -> list:
        if folder == "" and (col, ascending) == (0, True) and self.top is not None:
            return self.top
        return keyed_rows(self, folder, col, ascending)


def keyed_rows(contents: Contents, folder: str, col: int, ascending: bool) -> list:
    """The rows inside a folder as (sort key, (is_dir, path, entry)), in that order (folders first)."""
    specs = [(True, path, None) for path in contents.dirs[folder]] + \
        [(False, p, e) for p, e in contents.files[folder]]
    if col == 0:
        keyed = [(_natural(spec[1].rpartition("/")[2]), spec) for spec in specs]
    elif col == 3:
        dir_time = contents.dir_time
        keyed = [(dir_time.get(path, "") if is_dir else e.modified[:16], (is_dir, path, e))
                 for is_dir, path, e in specs]
    else:
        keyed = [(0 if is_dir else (e.size if col == 1 else e.packed), (is_dir, path, e))
                 for is_dir, path, e in specs]
    keyed.sort(key=lambda k: k[0], reverse=not ascending)
    keyed.sort(key=lambda k: not k[1][0])  # folders first (the sort keeps the order above)
    return keyed


class Row(QTreeWidgetItem):
    """One file or folder. What it's sorted by is kept on the item itself (asking Qt for it on each of
    the million comparisons a big archive needs would take seconds); folders always come first."""

    def setup(self, view, is_dir: bool, name: str, size: int = 0, packed: int = 0, modified: str = "",
              natural: list | None = None):
        self.view, self.is_dir = view, is_dir
        self.keys = (natural if natural is not None else _natural(name), size, packed, modified)

    def __lt__(self, other):
        col, ascending = self.view.sort_state
        if self.is_dir != other.is_dir:
            return self.is_dir if ascending else other.is_dir
        return self.keys[col] < other.keys[col]


PICTURE_EXT = {".png", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic", ".heif", ".avif",
               ".ico", ".svg"}
TEXT_EXT = {".txt", ".md", ".log", ".csv", ".tsv", ".json", ".xml", ".ini", ".cfg", ".conf", ".nfo", ".srt", ".vtt",
            ".yml", ".yaml", ".toml", ".html", ".htm", ".css", ".js", ".ts", ".py", ".bat", ".cmd", ".ps1", ".sh",
            ".c", ".h", ".cpp", ".hpp", ".cs", ".java", ".kt", ".go", ".rs", ".php", ".rb", ".lua", ".sql", ".reg",
            ".gitignore", ".diz"}
TEXT_SHOWN = 256 << 10     # this much of a text file is shown
SNIFF_MAX = 2 << 20        # a file with an unknown kind is looked at for text when it's this small
PREVIEW_PX = 1600          # pictures are made this small (at most) for the preview
PREVIEW_PIXELS = 64_000_000  # bigger pictures (a "picture bomb" of a few KB) are never decoded for it


def preview_of(name: str, data: bytes) -> tuple[str, object]:
    """("picture", QImage), ("text", str) or ("none", why) for a file's first bytes."""
    ext = os.path.splitext(name.lower())[1] or name.lower()
    if ext in PICTURE_EXT:
        img = QImage()
        if ext == ".svg":
            from PySide6.QtCore import QByteArray
            from PySide6.QtGui import QPainter
            from PySide6.QtSvg import QSvgRenderer

            from .safetext import safe_svg
            try:
                r = QSvgRenderer(QByteArray(safe_svg(data)))
            except (ValueError, EOFError, OSError):
                r = QSvgRenderer()
            if r.isValid():
                size = r.defaultSize()
                size.scale(min(PREVIEW_PX, max(64, size.width() * 2)), min(PREVIEW_PX, max(64, size.height() * 2)),
                           Qt.KeepAspectRatio)
                img = QImage(size, QImage.Format_ARGB32_Premultiplied)
                img.fill(0)
                p = QPainter(img)
                r.render(p)
                p.end()
        else:
            img.loadFromData(data)
        if img.isNull():  # HEIC, AVIF…: through Pillow
            try:
                import io

                from PIL import Image, ImageOps
                try:
                    import pi_heif
                    pi_heif.register_heif_opener()
                except Exception:
                    pass
                with Image.open(io.BytesIO(data)) as im:
                    if im.width * im.height > PREVIEW_PIXELS:  # (Qt refused it as too big: decoding it whole
                        im.draft("RGB", (PREVIEW_PX, PREVIEW_PX))  # could take GBs; a JPEG can be read small)
                        if im.width * im.height > PREVIEW_PIXELS:
                            return "none", tr("This picture can't be shown.")
                    im = ImageOps.exif_transpose(im)
                    im.thumbnail((PREVIEW_PX, PREVIEW_PX))
                    im = im.convert("RGBA")
                    img = QImage(im.tobytes(), im.width, im.height, im.width * 4, QImage.Format_RGBA8888).copy()
            except Exception:
                img = QImage()
        if img.isNull():
            return "none", tr("This picture can't be shown.")
        if max(img.width(), img.height()) > PREVIEW_PX:
            img = img.scaled(PREVIEW_PX, PREVIEW_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return "picture", img
    head = data[:TEXT_SHOWN]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "text", head.decode("utf-16", "replace")
    if b"\0" in head[:8192]:
        return "none", tr("No preview for this kind of file. Double-click to open it.")
    text = head.decode("utf-8-sig", "replace")
    if ext not in TEXT_EXT and text.count("\ufffd") > max(8, len(text) // 50):
        return "none", tr("No preview for this kind of file. Double-click to open it.")
    return "text", text


class PreviewPane(QStackedWidget):
    """Beside the list: the picked file's picture or text."""

    def __init__(self):
        super().__init__()
        self.setObjectName("archivePreview")
        self.setMinimumWidth(180)
        self.message = QLabel()
        self.message.setObjectName("muted")
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setWordWrap(True)
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignCenter)
        self.picture.setMinimumSize(1, 1)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        for w in (self.message, self.picture, self.text):
            self.addWidget(w)
        self.image = QImage()
        self.show_message(tr("Pick a file to see it here."))

    def show_message(self, text: str):
        self.image = QImage()
        self.message.setText(text)
        self.setCurrentWidget(self.message)

    def show_picture(self, img: QImage):
        self.image = img
        self.setCurrentWidget(self.picture)
        self._fit()

    def show_text(self, text: str):
        self.image = QImage()
        self.text.setPlainText(text)
        self.setCurrentWidget(self.text)

    def _fit(self):
        if self.image.isNull():
            return
        ratio = self.devicePixelRatioF()
        pix = QPixmap.fromImage(self.image)
        size = self.picture.size() * ratio
        if pix.width() > size.width() or pix.height() > size.height():
            pix = pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pix.setDevicePixelRatio(ratio)
        self.picture.setPixmap(pix)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()


class _Previewed(QObject):
    done = Signal(object)  # (number of the request, kind, what to show)


class _Listed(QObject):
    """Carries a fresh listing from the background back to the window."""
    done = Signal(object)  # (entries or None, their Contents or None, error or None)


class ArchiveTree(QTreeWidget):
    """The archive's contents; selected files can be dragged out into any folder."""

    def __init__(self, browser: "ArchiveBrowser"):
        super().__init__()
        self.browser = browser

    def startDrag(self, actions):
        self.browser._drag_out()

    def selectAll(self):
        self.browser.finish_rows()  # (Ctrl + A, too: every file, not just the rows already shown)
        self.browser.finish_look()
        super().selectAll()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # narrow: the Packed column steps aside so the names aren't cut short; it's back when there's room
        widths = getattr(self, "widths", None)
        if widths:
            self.setColumnHidden(2, self.viewport().width() - sum(widths) < 220)


class ArchiveBrowser(QWidget):
    def __init__(self, controller: "Controller", src: Path, password: str, entries: list[ar.Entry], icon: QIcon,
                 contents: Contents | None = None):
        super().__init__()
        self.c, self.src, self.password = controller, src, password
        self.setObjectName("window")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"{src.name} - {APP_NAME}")
        self.setWindowIcon(icon)
        screen = QGuiApplication.primaryScreen()
        room = screen.availableGeometry() if screen else None  # (roomy enough for long names, never off a small screen)
        self.resize(min(960, room.width() - 40) if room else 960, min(600, room.height() - 40) if room else 600)
        self.icons = QFileIconProvider()
        self._lock_icon = None  # made the first time a locked file shows up

        title = QLabel(short_name(src.name, 70))
        title.setToolTip(str(src))
        title.setObjectName("h2")
        from .i18n import is_rtl
        if is_rtl():  # a Latin file name would otherwise sit on the far side from its tile
            title.setAlignment(Qt.AlignRight | Qt.AlignAbsolute | Qt.AlignVCenter)
        from . import icons as tiles
        tile = QLabel()
        tile.setPixmap(tiles.product_tile("archive", 34, controller.theme.dark, self.devicePixelRatioF()))
        tile.setFixedSize(36, 36)
        self.info = QLabel()
        self.info.setObjectName("muted")
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search inside the archive"))
        self.search.setClearButtonEnabled(True)
        self.search_timer = QTimer(self)  # (typing a word searches once, not once for each letter)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(220)
        self.search_timer.timeout.connect(lambda: self._filter(self.search.text()))
        self.search.textChanged.connect(lambda _t: self.search_timer.start())

        self.tree = ArchiveTree(self)
        self.tree.setObjectName("history")
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragOnly)
        self.tree.setHeaderLabels([tr("Name"), tr("Size"), tr("Packed"), tr("Modified")])
        for col in (1, 2):  # (sizes line up on the right, under their headings)
            self.tree.headerItem().setTextAlignment(col, Qt.AlignRight | Qt.AlignVCenter)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.tree.setUniformRowHeights(True)
        self.tree.setTextElideMode(Qt.ElideMiddle)  # (a long name keeps its end: report-20…-final.pdf)
        # sorted by Corefold (in Python, before the rows go in): Qt's own sorting would be slow for
        # 100,000 files. Clicking a column heading sorts by it.
        self.tree.sort_state = (0, True)
        header = self.tree.header()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(0, Qt.AscendingOrder)
        header.sectionClicked.connect(self._sort_by)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        header.setStretchLastSection(False)  # (all the room left goes to the names, not to Modified)
        # the other columns just as wide as what's in them, so the names get the room (measured once: sizing
        # them to their rows would read every row of a folder of 100,000 files)
        fm = self.tree.fontMetrics()
        sizes = max(fm.horizontalAdvance(human_size(n)) for n in (1000, 999 * 1024 ** 2 + 943718))
        self.tree.widths = []
        for col, wide in ((1, sizes), (2, sizes), (3, fm.horizontalAdvance("2026-10-25 08:12"))):
            self.tree.widths.append(max(wide, fm.horizontalAdvance(self.tree.headerItem().text(col))) + 26)
            header.resizeSection(col, self.tree.widths[-1])
        self.tree.itemDoubleClicked.connect(lambda item, _col: self._open(item))
        self.tree.itemSelectionChanged.connect(self._buttons)
        self.tree.itemExpanded.connect(self._expanded)
        self._folder_icon = self.icons.icon(QFileIconProvider.Folder)
        self._file_icon = self.icons.icon(QFileIconProvider.File)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        # keys: Enter opens, Delete removes, Ctrl+F searches
        QShortcut(QKeySequence(Qt.Key_Return), self.tree, lambda: self._open(self.tree.currentItem()),
                  context=Qt.WidgetShortcut)
        QShortcut(QKeySequence(Qt.Key_Enter), self.tree, lambda: self._open(self.tree.currentItem()),
                  context=Qt.WidgetShortcut)
        QShortcut(QKeySequence.StandardKey.Delete, self.tree,
                  lambda: self._delete() if ar.can_edit(self.src) else None, context=Qt.WidgetShortcut)
        QShortcut(QKeySequence.StandardKey.Find, self, self.search.setFocus)

        def btn(text, fn, name=None):
            b = QPushButton(text)
            if name:
                b.setObjectName(name)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            return b

        self.b_here = btn(tr("Extract here"), self._extract_here, "primary")
        self.b_here.setToolTip(tr("Next to the archive (or in your results folder), without a folder inside a folder")
                               if getattr(controller.settings, "extract_mode", "smart") == "smart" else
                               tr("Next to the archive (or in your results folder), in a new folder"))
        self.b_to = btn(tr("Extract to…"), self._extract_to)
        self.b_open = btn(tr("Open"), lambda: self._open(self.tree.currentItem()))
        self.b_more = btn(tr("More"), lambda: None)
        more = QMenu(self.b_more)
        round_menu(more)
        more.addAction(tr("Test"), self._test)
        rescue = more.addAction(tr("Rescue files…"), self._repair)
        rescue.setToolTip(tr("For a damaged archive: makes a new ZIP with every file that can still be read. "
                             "The archive itself isn't changed."))
        more.setToolTipsVisible(True)
        more.addSeparator()
        self.a_preview = more.addAction(tr("Show a preview (P)"))
        self.a_preview.setCheckable(True)
        self.a_preview.triggered.connect(self.set_preview)
        more.addAction(tr("Select all"), self.tree.selectAll)
        more.addAction(tr("Show in folder"), lambda: reveal_in_explorer(self.src))
        if ar.has_legacy_names(src):  # an old ZIP whose names don't say how they're written
            more.addSeparator()
            more.addMenu(self._names_menu(more))
        self.b_more.setMenu(more)
        self.nested = QCheckBox(tr("Also unpack archives inside"))
        self.nested.setToolTip(tr("Archives found inside are unpacked too, each into its own folder."))
        self.nested.setChecked(bool(getattr(controller.settings, "extract_nested", False)))
        self.b_add = btn(tr("Add files…"), self._add)
        self.b_del = btn(tr("Remove"), self._delete, "danger")
        row = QHBoxLayout()
        for b in (self.b_here, self.b_to, self.b_open, self.b_more):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(self.b_add)
        row.addWidget(self.b_del)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(tile, 0, Qt.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(title)
        words.addWidget(self.info)
        head.addLayout(words, 1)
        lay.addLayout(head)
        lay.addWidget(self.search)
        self.preview = PreviewPane()
        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.tree)
        self.split.addWidget(self.preview)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        self.split.setSizes([640, 280])
        lay.addWidget(self.split, 1)
        self._preview_asked = 0
        self._preview_stop = None  # stops the preview being read (another file was picked)
        self._previewed = _Previewed(self)
        self._previewed.done.connect(self._preview_ready)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(150)
        self.preview_timer.timeout.connect(self._load_preview)
        self.tree.currentItemChanged.connect(lambda *_: self.preview_timer.start() if self.preview.isVisibleTo(self)
                                             else None)
        QShortcut(QKeySequence(Qt.Key_P), self.tree, lambda: self.set_preview(self.preview.isHidden()),
                  context=Qt.WidgetShortcut)
        shown = bool(getattr(controller.settings, "archive_preview", True))
        self.preview.setVisible(shown)
        self.a_preview.setChecked(shown)
        lay.addWidget(self.nested)
        lay.addWidget(self._changes_bar())
        lay.addLayout(row)
        editable = ar.can_edit(src)  # (only once they're in the window: before, each would open as a window)
        self.b_add.setVisible(editable)
        self.b_del.setVisible(editable)

        # files opened from the archive and changed: offered to be saved back into it
        self.watched: dict[str, tuple[str, tuple]] = {}  # temp file -> (path inside, how it was)
        self.changed: dict[str, Path] = {}               # path inside -> changed file, waiting for an answer
        self.watcher: QFileSystemWatcher | None = None
        self._lister: _Listed | None = None
        self.check_timer = QTimer(self)
        self.check_timer.setSingleShot(True)
        self.check_timer.timeout.connect(self._check_changes)
        self.set_entries(entries, contents)

    # ---- names of an old ZIP

    def _names_menu(self, parent: QMenu) -> QMenu:
        """File names: worked out by Corefold, or one the person picks if they still look wrong."""
        from PySide6.QtGui import QActionGroup
        menu = QMenu(tr("File names"), parent)
        round_menu(menu)
        menu.setToolTipsVisible(True)
        group = QActionGroup(menu)
        choices = ((tr("Arabic (DOS, 720)"), 720), (tr("Arabic (Windows, 1256)"), 1256), (tr("Arabic (864)"), 864),
                   ("UTF-8", ar.UTF8_CP), (tr("As this PC reads them"), 0))
        label = {cp: name for name, cp in choices}.get(ar.found_zip_codepage(self.src))
        auto = menu.addAction(tr("Worked out by Corefold ({name})", name=label) if label
                              else tr("Worked out by Corefold"))
        auto.setToolTip(tr("Old ZIP files don't say which letters their names use. If they look wrong, pick "
                           "another one here."))
        auto.setData(None)
        menu.addSeparator()
        actions = [auto]
        for name, cp in choices:
            act = menu.addAction(name)
            act.setData(cp)
            actions.append(act)
        chosen = ar.picked_zip_codepage(self.src)
        for act in actions:
            act.setCheckable(True)
            act.setChecked(act.data() == chosen)
            group.addAction(act)
            act.triggered.connect(lambda _on=False, a=act: self._pick_names(a.data()))
        return menu

    def _pick_names(self, cp):
        if cp == ar.picked_zip_codepage(self.src):
            return
        ar.set_zip_codepage(self.src, cp)
        self.reload()

    # ---- contents

    def set_entries(self, entries: list[ar.Entry], contents: Contents | None = None):
        """Shows what's in the archive. Folders of a big archive are only filled in when they're
        opened (or searched), so even 100,000 files show up at once."""
        self.entries = entries
        if contents is None or contents.entries is not entries:
            contents = Contents(entries)
        self.contents = contents
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        self._dirs, self._files, self._dir_time = contents.dirs, contents.files, contents.dir_time
        self._items: dict[str, Row] = {}          # folder -> its row, once made
        self._filled: set[str] = set()
        self._later: list = []                    # (folder, rows still to make in it), a big folder's rest
        self._round = getattr(self, "_round", 0) + 1
        self._looking = None                      # a search still going through the rows (see _filter)
        self._looked_for = ""
        self._many = False                        # the last search matched more than SEARCH_FILLS
        files, size = contents.count, contents.size
        self.tree.addTopLevelItems(self._rows_for(""))
        self._filled.add("")
        if len(entries) <= EAGER_LIMIT:  # small: everything at once
            for path in list(self._dirs):
                self._fill(path)
            if len(self._dirs) < 30:
                self.tree.expandAll()
        self.tree.setUpdatesEnabled(True)
        try:
            packed = self.src.stat().st_size
        except OSError:
            packed = 0
        text = tr("Files: {count} · {size} unpacked · {packed} archive", count=files, size=human_size(size),
                  packed=human_size(packed))
        if size and packed < size:
            text += " · " + tr("{percent}% smaller packed", percent=100 - round(packed * 100 / size))
        self._info_text = text + " · " + tr("Drag files out to any folder")
        self.info.setText(self._info_text)
        self.nested.setVisible(contents.nested)
        self._buttons()

    def _rows_for(self, folder: str) -> list:
        """The rows inside a folder, in the current order (folders first). A folder with thousands of files
        gets its first rows now and the rest a few at a time, so the window keeps answering."""
        col, ascending = self.tree.sort_state
        keyed = self.contents.keyed(folder, col, ascending)
        if len(keyed) > FIRST_ROWS:
            self._later.append((folder, keyed[FIRST_ROWS:]))
            if len(self._later) == 1:
                QTimer.singleShot(0, lambda r=self._round: self._more_rows(r))
            keyed = keyed[:FIRST_ROWS]
        return [self._row(k, spec, col) for k, spec in keyed]

    def _row(self, key, spec, col: int) -> Row:
        view = self.tree
        is_dir, path, e = spec
        name = path.rpartition("/")[2]
        natural = key if col == 0 else None
        if is_dir:
            item = Row([ltr(name), "", "", self._dir_time.get(path, "")])
            item.setup(view, True, name, modified=self._dir_time.get(path, ""), natural=natural)
            item.setIcon(0, self._folder_icon)
            item.setData(0, PATH_ROLE, path)
            item.setData(0, DIR_ROLE, True)
            if self._dirs[path] or self._files[path]:
                item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            self._items[path] = item
            return item
        right = Qt.AlignRight | Qt.AlignVCenter
        modified = e.modified[:16]
        item = Row([ltr(name), human_size(e.size), human_size(e.packed) if e.packed else "", modified])
        item.setup(view, False, name, e.size, e.packed, modified, natural=natural)
        if e.encrypted:  # locked with a password
            if self._lock_icon is None:
                from . import icons as line_icons
                self._lock_icon = line_icons.icon("lock_closed", "#7e8c89")
            item.setIcon(0, self._lock_icon)
        else:
            item.setIcon(0, self._file_icon)
        item.setData(0, PATH_ROLE, path)
        item.setData(0, DIR_ROLE, False)
        item.setTextAlignment(1, right)
        item.setTextAlignment(2, right)
        return item

    def _more_rows(self, round_: int, count: int = MORE_ROWS):
        """Makes the next rows of a big folder (see _rows_for)."""
        if round_ != self._round:  # (the archive was read again meanwhile)
            return
        col = self.tree.sort_state[0]
        text = self.search.text().strip().lower()
        while self._later and count > 0:
            folder, keyed = self._later[0]
            part, rest = keyed[:count], keyed[count:]
            rows = [self._row(k, spec, col) for k, spec in part]
            if folder == "":
                parent, first = QModelIndex(), self.tree.topLevelItemCount()
                self.tree.addTopLevelItems(rows)
            else:
                parent, first = self.tree.indexFromItem(self._items[folder]), self._items[folder].childCount()
                self._items[folder].addChildren(rows)
            if text:  # (searching meanwhile: only what matches shows; by place, as in _look)
                for n, r in enumerate(rows):
                    if text not in r.text(0).lower():
                        self.tree.setRowHidden(first + n, parent, True)
            count -= len(part)
            if rest:
                self._later[0] = (folder, rest)
            else:
                self._later.pop(0)
        if self._later:
            QTimer.singleShot(0, lambda: self._more_rows(round_))
        elif text:  # (folders that came meanwhile may hold matches)
            self.search_timer.start()

    def finish_rows(self):
        """Every row of the folders opened so far (before sorting or selecting all)."""
        if self._later:
            self._more_rows(self._round, 1 << 62)

    def finish_look(self):
        """A search still going through the rows ends now (before selecting all: rows it hasn't shown again
        yet would be left out). What was typed a moment ago is looked for first."""
        if self.search_timer.isActive():
            self._filter(self.search.text())
        looking, self._looking = self._looking, None
        for _ in looking or ():
            pass

    def _fill(self, folder: str):
        """Makes the rows of a folder the first time it's needed."""
        if folder in self._filled or folder not in self._items:
            return
        self._filled.add(folder)
        self._items[folder].addChildren(self._rows_for(folder))

    def _expanded(self, item):
        if item.data(0, DIR_ROLE):
            self._fill(item.data(0, PATH_ROLE))

    def _sort_by(self, col: int):
        """A column heading was clicked: sort by it (again: the other way round)."""
        old_col, ascending = self.tree.sort_state
        ascending = not ascending if col == old_col else col == 0
        self.tree.sort_state = (col, ascending)
        order = Qt.AscendingOrder if ascending else Qt.DescendingOrder
        self.tree.header().setSortIndicator(col, order)
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if self._later:  # (a big folder is still showing its rows: shown again, in the new order)
                self.set_entries(self.entries, self.contents)
                if self.search.text().strip():
                    self._filter(self.search.text())
                return
            self._looking = None  # (a search going through the rows stops: they move, and it starts again)
            self._resort(col, ascending)
            if self._looked_for:
                self._filter(self._looked_for)
        finally:
            QGuiApplication.restoreOverrideCursor()

    def _resort(self, col: int, ascending: bool):
        """Puts every row shown in the new order. (Qt's own sort asks Python about each of the million
        comparisons 100,000 rows need, which took seconds; here each row's key is looked at once.)"""
        tree = self.tree
        opened = [p for p, item in self._items.items() if item.isExpanded()]
        chosen = tree.selectedItems()
        current = tree.currentItem()
        tree.setUpdatesEnabled(False)
        try:
            levels = [tree.invisibleRootItem()] + [self._items[p] for p in self._filled if p and p in self._items]
            for parent in levels:
                kids = parent.takeChildren()
                kids.sort(key=lambda r: r.keys[col], reverse=not ascending)
                kids.sort(key=lambda r: not r.is_dir)
                parent.addChildren(kids)
            for p in opened:
                self._items[p].setExpanded(True)
            if current is not None:
                tree.setCurrentItem(current, 0, QItemSelectionModel.NoUpdate)
            if len(chosen) <= 5000:
                for item in chosen:
                    item.setSelected(True)
        finally:
            tree.setUpdatesEnabled(True)

    def reload(self, wait: bool = False):
        """Reads the archive again (after a change). Big archives take a moment, so it's read in the
        background and the window keeps responding; wait=True reads it right here."""
        if wait:
            try:
                self.set_entries(ar.list_entries(self.src, self.password))
            except Exception as e:
                self.info.setText(tr("Couldn't read the archive: {error}", error=e))
            return
        import threading
        if self._lister is None:
            self._lister = _Listed(self)
            self._lister.done.connect(self._listed)
        lister, src, password = self._lister, self.src, self.password

        def work():
            entries, contents, error = None, None, None
            try:
                entries = ar.list_entries(src, password)
                contents = Contents(entries)
            except Exception as e:
                error = e
            try:
                lister.done.emit((entries, contents, error))
            except RuntimeError:  # the window was closed meanwhile
                pass
        threading.Thread(target=work, daemon=True).start()

    def _listed(self, payload):
        entries, contents, error = payload
        if error is not None:
            self.info.setText(tr("Couldn't read the archive: {error}", error=error))
        else:
            self.set_entries(entries, contents)

    def _filter(self, text: str):
        self.search_timer.stop()
        text = text.strip().lower()
        many = False
        if text:  # fill in the folders that hold a match (they may not have been opened yet)
            matches = [p for p in self._dirs if text in p.rpartition("/")[2].lower()] + \
                [p for rows in self._files.values() for p, _e in rows if text in p.rpartition("/")[2].lower()]
            many = len(matches) > SEARCH_FILLS
            for path in matches[:SEARCH_FILLS]:
                parts = path.split("/")
                for i in range(1, len(parts)):
                    self._fill("/".join(parts[:i]))
        if many != self._many:
            self._many = many
            self.info.setText(self._info_text + (" · " + tr("Many files match: type more of the name to find the one "
                                                            "you want.") if many else ""))

        self._looked_for = text
        self._looking = self._look(text)
        self._look_on(self._looking)

    def _look(self, text: str):
        """Shows only the rows that match (with the folders they're in), going through the rows a part at a
        time: 100,000 rows in one go held the window 0.4 s. Rows are hidden by their place in the folder:
        Qt finds an item's place by walking the folder from its end the first time it's asked, which made a
        first search of 100,000 rows take seconds."""
        tree, model = self.tree, self.tree.model()
        seen = 0

        def visit(item: QTreeWidgetItem, row: int, parent: QModelIndex, inside_match: bool = False):
            # a folder that matches shows everything in it
            nonlocal seen
            match = not text or inside_match or text in item.text(0).lower()
            child_match = False
            count = item.childCount()
            if count:
                here = model.index(row, 0, parent)
                for i in range(count):
                    child_match = (yield from visit(item.child(i), i, here, match and bool(text))) or child_match
                if text and child_match:
                    tree.setExpanded(here, True)
            show = match or child_match
            if tree.isRowHidden(row, parent) == show:  # (only the rows that change)
                tree.setRowHidden(row, parent, not show)
            seen += 1
            if seen % LOOK_STEP == 0:
                yield
            return show
        top, i = QModelIndex(), 0
        while i < tree.topLevelItemCount():  # (rows of a big folder may come in meanwhile)
            yield from visit(tree.topLevelItem(i), i, top)
            i += 1

    def _look_on(self, looking):
        if looking is not self._looking:  # (a new search, or the archive was read again or sorted)
            return
        end = time.perf_counter() + 0.02
        for _ in looking:
            if time.perf_counter() > end:
                QTimer.singleShot(0, self, lambda: self._look_on(looking))
                return
        self._looking = None

    def _selected_paths(self) -> list[str]:
        """Selected files, plus everything inside selected folders."""
        chosen = {item.data(0, PATH_ROLE) for item in self.tree.selectedItems()}
        folders = {item.data(0, PATH_ROLE) for item in self.tree.selectedItems() if item.data(0, DIR_ROLE)}
        paths = set(chosen)
        if folders:  # one pass over the archive (fast even with 100,000 files and everything selected)
            for e in self.entries:
                path = e.path.strip("/")
                if path and _inside_any(path, folders):
                    paths.add(path)
        return sorted(paths)

    def _buttons(self):
        sel = self.tree.selectedItems()
        self.b_to.setText(tr("Extract selected to…") if sel else tr("Extract to…"))
        self.b_del.setEnabled(bool(sel))
        current = self.tree.currentItem()
        self.b_open.setEnabled(bool(current and not current.data(0, DIR_ROLE)))

    # ---- the preview

    def set_preview(self, on: bool):
        self.preview.setVisible(on)
        self.a_preview.setChecked(on)
        self.c.settings.archive_preview = on
        self.c.settings.save()
        if on:
            self._load_preview()

    def _load_preview(self):
        item = self.tree.currentItem()
        self._preview_asked += 1
        asked = self._preview_asked
        if self._preview_stop is not None:
            self._preview_stop.set()
            self._preview_stop = None
        if item is None:
            self.preview.show_message(tr("Pick a file to see it here."))
            return
        if item.data(0, DIR_ROLE):
            self.preview.show_message(item.text(0))
            return
        member, size = item.data(0, PATH_ROLE), item.keys[1]
        name = member.rpartition("/")[2]
        ext = os.path.splitext(name.lower())[1]
        if size > ar.PREVIEW_MAX or (ext not in PICTURE_EXT and ext not in TEXT_EXT and size > SNIFF_MAX):
            self.preview.show_message(tr("No preview for this kind of file. Double-click to open it.") if
                                      size <= ar.PREVIEW_MAX else tr("Too big to show here. Double-click to open it."))
            return
        self.preview.show_message(tr("Loading…"))
        import threading
        signals, src, password = self._previewed, self.src, self.password
        limit = ar.PREVIEW_MAX if ext in PICTURE_EXT else TEXT_SHOWN
        stop = self._preview_stop = threading.Event()

        def work():
            try:
                kind, what = preview_of(name, ar.read_member(src, member, password, limit, stop))
            except Cancelled:
                return
            except ar.NeedPassword:
                kind, what = "none", tr("This file is locked with a password.")
            except Exception as e:
                kind, what = "none", tr("Couldn't read it: {error}", error=e)
            try:
                signals.done.emit((asked, kind, what))
            except RuntimeError:  # the window was closed meanwhile
                pass
        threading.Thread(target=work, daemon=True).start()

    def _preview_ready(self, payload):
        asked, kind, what = payload
        if asked != self._preview_asked:
            return  # another file was picked meanwhile
        if kind == "picture":
            self.preview.show_picture(what)
        elif kind == "text":
            self.preview.show_text(what)
        else:
            self.preview.show_message(what)

    # ---- actions (run in the background with the usual progress popup)

    def _run(self, key: str, desc: str, fn, items=None, after=None, params: dict | None = None):
        tool = Tool(key, desc, desc, fn, batch=True, renames=False, password=True)
        self.c.start_job(tool, items or [self.src], {"password": self.password, **(params or {})}, after=after)

    def _pick_folder(self) -> str:
        return QFileDialog.getExistingDirectory(self, tr("Extract to"), str(self.src.parent))

    def _nested(self) -> bool:
        return self.nested.isVisible() and self.nested.isChecked()

    def _extract_here(self):
        """Everything, the smart way (one item: straight in; several: into a folder named after the archive)."""
        nested = self._nested()
        mode = getattr(self.c.settings, "extract_mode", "smart")
        self._run("archive:extract", tr("Extract"), lambda items, ctx: ar.extract(
            self.src, _with(ctx, nested=nested), mode=mode))

    def _extract_to(self):
        """Into a folder you pick: the selected items, or everything if nothing is selected."""
        members = self._selected_paths() if self.tree.selectedItems() else None
        dest = self._pick_folder()
        if not dest:
            return
        nested = self._nested()
        if members:
            self._run("archive:extract", tr("Extract"), lambda items, ctx: ar.extract(
                self.src, _with(ctx, dest=dest, members=members, nested=nested)))
        else:
            self._run("archive:extract", tr("Extract"), lambda items, ctx: ar.extract(
                self.src, _with(ctx, dest=dest, nested=nested), mode="folder"))

    def _tree_menu(self, pos):
        """Right-click on the list."""
        item = self.tree.itemAt(pos)
        if item is not None and not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
            self.tree.setCurrentItem(item)
        menu = QMenu(self)
        round_menu(menu)
        if item is not None and not item.data(0, DIR_ROLE):
            menu.addAction(tr("Open"), lambda: self._open(item))
            menu.addAction(tr("Open with…"), lambda: self._open(item, "open_with"))
        if self.tree.selectedItems():
            menu.addAction(tr("Extract selected to…"), self._extract_to)
            if ar.can_edit(self.src):
                menu.addAction(tr("Remove"), self._delete)
            menu.addSeparator()
        menu.addAction(tr("Extract here"), self._extract_here)
        menu.addAction(tr("Select all"), self.tree.selectAll)
        sheet.menu(menu, self.tree.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def _open(self, item, after: str = "open"):
        if not item or item.data(0, DIR_ROLE):
            return
        member = item.data(0, PATH_ROLE)
        self._run("archive:open", tr("Open"), lambda items, ctx: ar.extract_for_opening(self.src, member, ctx),
                  after=after, params={"member": member})

    def _test(self):
        self._run("archive:test", tr("Test"), lambda items, ctx: ar.test_archive(self.src, ctx))

    def _repair(self):
        self.c.repair_archive(self.src, self.password)

    # ---- dragging files out

    def _top_selected(self) -> list[str]:
        """Selected items that aren't inside another selected folder."""
        chosen = {i.data(0, PATH_ROLE) for i in self.tree.selectedItems()}
        return sorted(p for p in chosen if not _inside_any(p, chosen))

    def _drag_out(self):
        members = self._selected_paths()
        tops = self._top_selected()
        if not members or not tops:
            return
        wanted = set(members)
        total = sum(e.size for e in self.entries if e.path.strip("/") in wanted)
        if total > DRAG_LIMIT:
            sheet.information(self, tr("Extract"), tr("That's a lot to drag ({size}). Use Extract instead: it "
                                                       "shows the progress.", size=human_size(total)))
            return
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        # (taken out on a thread of its own: up to 256 MB can take a while, and the window, drawn on this one,
        # must not turn white and "Not responding" meanwhile; clicks and keys wait until it's done)
        from PySide6.QtCore import QEventLoop
        from . import work
        loop, got = QEventLoop(), {}

        def done(result):
            got["result"] = result
            loop.quit()
        job = work.run(lambda _p: ar.extract_for_drag(self.src, members, self.password), lambda _: None, done,
                       name="corefold-drag-out")
        if "result" not in got:
            loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        del job
        QGuiApplication.restoreOverrideCursor()
        folder = got.get("result")
        if isinstance(folder, Exception) or folder is None:
            e = folder
            why = tr("wrong or missing password") if isinstance(e, ar.NeedPassword) else \
                str(e or "") or tr("the archive is damaged or incomplete")
            sheet.warning(self, tr("Couldn't take the files out"), why)
            return
        urls = []
        for p in tops:
            path = folder / p.replace("/", os.sep)
            try:
                if path.exists() and path.resolve().is_relative_to(folder.resolve()):
                    urls.append(QUrl.fromLocalFile(str(path)))
            except OSError:
                pass
        if urls:
            mime = QMimeData()
            mime.setUrls(urls)
            drag = QDrag(self.tree)
            drag.setMimeData(mime)
            drag.exec(Qt.CopyAction)  # Explorer has copied them by the time this returns
        # the temporary copies go away a little later (an app the files were dropped on may still be reading)
        QTimer.singleShot(DRAG_KEEP_MS, lambda f=folder: ar.remove_drag_folder(f))

    # ---- a file opened from the archive was changed: put it back?

    def _changes_bar(self) -> QFrame:
        self.bar = QFrame()
        self.bar.setObjectName("card")
        bl = QHBoxLayout(self.bar)
        bl.setContentsMargins(12, 8, 12, 8)
        self.bar_text = QLabel()
        self.bar_text.setWordWrap(True)
        self.bar_save = QPushButton(tr("Save into the archive"))
        self.bar_save.setObjectName("primary")
        self.bar_save.clicked.connect(self._save_back)
        self.bar_skip = QPushButton(tr("Not now"))
        self.bar_skip.clicked.connect(self._skip_changes)
        for b in (self.bar_save, self.bar_skip):
            b.setCursor(Qt.PointingHandCursor)
        bl.addWidget(self.bar_text, 1)
        bl.addWidget(self.bar_save)
        bl.addWidget(self.bar_skip)
        self.bar.hide()
        return self.bar

    @staticmethod
    def _stamp(path: Path) -> tuple:
        try:
            st = path.stat()
            return st.st_mtime_ns, st.st_size
        except OSError:
            return ()

    def opened(self, member: str, path: Path):
        """A file from the archive was opened in its app: notice when it's saved there."""
        self.watched[str(path)] = (member, self._stamp(path))
        if self.watcher is None:
            self.watcher = QFileSystemWatcher(self)
            self.watcher.fileChanged.connect(lambda _p: self.check_timer.start(800))
            self.watcher.directoryChanged.connect(lambda _p: self.check_timer.start(800))
        # the folder too: many apps save by writing a new file and swapping it in
        self.watcher.addPath(str(path))
        self.watcher.addPath(str(path.parent))

    def _check_changes(self):
        for name, (member, before) in list(self.watched.items()):
            path = Path(name)
            now = self._stamp(path)
            if not now or now == before:
                continue
            self.watched[name] = (member, now)
            if self.watcher is not None and name not in self.watcher.files():
                self.watcher.addPath(name)
            self.changed[member] = path
        self._show_bar()

    def _show_bar(self):
        if not self.changed:
            self.bar.hide()
            return
        names = ", ".join(short_name(m.rpartition("/")[2], 40) for m in self.changed)
        if ar.can_edit(self.src):
            self.bar_text.setText(tr("You changed {name}. Save the new version into the archive?", name=names))
            self.bar_save.show()
        else:
            self.bar_text.setText(tr("You changed {name}, but this kind of archive can't be changed. Save the file "
                                     "somewhere else from its app to keep your changes.", name=names))
            self.bar_save.hide()
        self.bar_skip.setText(tr("Not now") if ar.can_edit(self.src) else tr("OK"))
        self.bar.show()

    def _save_back(self):
        changed, self.changed = self.changed, {}
        self.bar.hide()
        if not changed:
            return

        src = self.src  # (the window may be closed while this runs)

        def save_all(items, ctx):  # one after the other: they all change the same archive
            result = None
            for member, path in changed.items():
                ctx.check()
                result = ar.update_member(src, member, path, ctx)
            return result
        self._run("archive:update", tr("Save into {name}", name=short_name(self.src.name, 30)), save_all,
                  after="reload")

    def _skip_changes(self):
        self.changed = {}
        self.bar.hide()

    def _add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Add to {name}", name=self.src.name), str(self.src.parent))
        if paths:
            self._run("archive:add", tr("Add to {name}", name=short_name(self.src.name, 30)),
                      lambda items, ctx: ar.add_files(self.src, [Path(p) for p in paths], ctx), after="reload")

    def _delete(self):
        members = self._selected_paths()
        if not members:
            return
        box = QMessageBox(QMessageBox.Question, tr("Remove from archive"),
                          tr("Remove these items from {name}? ({count})", name=self.src.name, count=len(members))
                          + "\n" + tr("This changes the archive and can't be undone."), parent=self)
        remove = box.addButton(tr("Remove"), QMessageBox.DestructiveRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        sheet.run(box)
        chosen = box.clickedButton() is remove
        box.deleteLater()
        if chosen:
            self._run("archive:delete", tr("Remove from {name}", name=short_name(self.src.name, 30)),
                      lambda items, ctx: ar.delete_entries(self.src, members, ctx), after="reload")

    # ---- drag files in to add them

    def dragEnterEvent(self, e):
        # files from Explorer or another archive window (not ones dragged out of this one) are added
        if ar.can_edit(self.src) and e.mimeData().hasUrls() and e.source() is not self.tree:
            e.acceptProposedAction()

    def closeEvent(self, e):
        self._check_changes()
        if self.changed and ar.can_edit(self.src):
            names = ", ".join(short_name(m.rpartition("/")[2], 40) for m in self.changed)
            box = QMessageBox(QMessageBox.Question, tr("Save into the archive"),
                              tr("You changed {name}. Save the new version into the archive?", name=names), parent=self)
            save = box.addButton(tr("Save into the archive"), QMessageBox.AcceptRole)
            box.addButton(tr("Don't save"), QMessageBox.DestructiveRole)
            cancel = box.addButton(tr("Cancel"), QMessageBox.RejectRole)
            sheet.run(box)
            chosen = box.clickedButton()
            box.deleteLater()
            if chosen is cancel:
                e.ignore()
                return
            if chosen is save:
                self._save_back()
        if self._preview_stop is not None:
            self._preview_stop.set()
        ar.set_zip_codepage(self.src, None)  # (the pick lasts while it's open)
        ar.forget_list(self.src)
        super().closeEvent(e)

    def dropEvent(self, e):
        if e.source() is self.tree:  # files dragged out of this window and let go over it again
            return
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if p.exists() and p.resolve() != self.src.resolve()]
        if paths:
            self._run("archive:add", tr("Add to {name}", name=short_name(self.src.name, 30)),
                      lambda items, ctx: ar.add_files(self.src, paths, ctx),
                      after="reload")

    def showEvent(self, e):
        super().showEvent(e)
        self.setAcceptDrops(ar.can_edit(self.src))
        set_title_bar_dark(self, self.c.theme.dark)


def _with(ctx, **params):
    """Same job context with extra settings."""
    from .core import Ctx
    return Ctx(ctx.options, ctx._progress, ctx.cancel_event, dict(ctx.params, **params), ctx.index, ctx.total)

