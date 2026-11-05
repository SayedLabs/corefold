"""Rakiza Core PDF's small windows: how to split, how much to compress, pictures to PDF, PDF to
pictures, a password with what it still allows, and one page shown big."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QRadioButton, QSizePolicy, QSpinBox, QVBoxLayout)

from .atomic.widgets import Dropdown
from .dialogs import Base, describe
from .dialogs2 import LockDialog
from .i18n import is_rtl, ltr, tr
from .pdftools import COMPRESS_LEVELS, DPIS, HIDDEN, IMAGE_FORMATS, MARGINS, PAGE_FORMATS, PAGE_SIZES, SPLIT_MODES


def _note(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    return lab


def _choices(lay, options: dict, chosen, owner) -> QButtonGroup:
    group = QButtonGroup(owner)
    for key, text in options.items():
        rb = QRadioButton(tr(text if isinstance(text, str) else text[0]))
        rb.setProperty("key", key)
        rb.setChecked(key == chosen)
        group.addButton(rb)
        lay.addWidget(rb)
    return group


def _chosen(group: QButtonGroup, default):
    b = group.checkedButton()
    return b.property("key") if b else default


def _combo(options: dict, chosen, translate: bool = True) -> Dropdown:
    box = Dropdown()
    for key, text in options.items():
        text = text if isinstance(text, str) else text[0]
        box.addItem(tr(text) if translate else text, key)
    box.setCurrentIndex(max(0, box.findData(chosen)))
    return box


class SplitDialog(Base):
    def __init__(self, files, icon, dark, pages: int = 0):
        super().__init__(tr("Split a PDF"), describe(files) + (f"  ·  {tr('{count} pages', count=pages)}"
                                                               if pages else ""), icon, dark)
        self.group = _choices(self.lay, SPLIT_MODES, "each", self)
        row = QHBoxLayout()
        self.every = QSpinBox()
        self.every.setRange(1, 9999)
        self.every.setValue(2)
        row.addSpacing(24)
        row.addWidget(QLabel(tr("Pages in each PDF")))
        row.addWidget(self.every)
        row.addStretch()
        self.lay.insertLayout(self.lay.indexOf(self.group.buttons()[1]) + 1, row)
        self.ranges = QLineEdit()
        self.ranges.setPlaceholderText(tr("e.g. 1-3, 4-10, 11-"))
        self.lay.insertWidget(self.lay.indexOf(self.group.buttons()[2]) + 1, self.ranges)
        self.every.valueChanged.connect(lambda _: self.group.buttons()[1].setChecked(True))
        self.ranges.textEdited.connect(lambda _: self.group.buttons()[2].setChecked(True))
        self.lay.addWidget(_note(tr("Each part is saved as its own PDF in a new folder. Your original stays as it "
                                    "is.")))
        self.buttons(tr("Split"))
        self.group.buttonToggled.connect(self._check)
        self.ranges.textChanged.connect(self._check)
        self._check()

    def _check(self, *_):
        self.ok.setEnabled(_chosen(self.group, "each") != "ranges" or bool(self.ranges.text().strip()))

    def params(self):
        return {"mode": _chosen(self.group, "each"), "every": self.every.value(), "ranges": self.ranges.text()}


class CompressDialog(Base):
    def __init__(self, files, icon, dark):
        super().__init__(tr("Compress"), describe(files), icon, dark)
        self.group = _choices(self.lay, COMPRESS_LEVELS, "normal", self)
        self.lay.addWidget(_note(tr("Pictures inside are made smaller; text and drawings stay sharp. "
                                    "Saved as a new file.")))
        self.buttons(tr("Compress"))

    def params(self):
        return {"level": _chosen(self.group, "normal")}


class PageSizeDialog(Base):
    """Page size: which size every page is made, and how much room is left around what's on it."""

    def __init__(self, files, icon, dark):
        super().__init__(tr("Page size"), describe(files), icon, dark)
        self.size = _combo(PAGE_FORMATS, "a4")
        self.size.setAccessibleName(tr("Page size"))
        self.margin = _combo(MARGINS, "none")
        self.margin.setAccessibleName(tr("Margin"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.addWidget(QLabel(tr("Size")), 0, 0)
        grid.addWidget(self.size, 0, 1)
        grid.addWidget(QLabel(tr("Margin")), 1, 0)
        grid.addWidget(self.margin, 1, 1)
        self.lay.addLayout(grid)
        self.lay.addWidget(_note(tr("What's on each page is made bigger or smaller to fit, in the middle, keeping "
                                    "its shape. Wide pages are made landscape. Saved as a new file.")))
        self.buttons(tr("Make them this size"))

    def params(self):
        return {"size": self.size.currentData(), "margin": self.margin.currentData()}


class TrimDialog(Base):
    """Trim edges: how much room is left around what's on each page, and whether every page is cut the same."""

    def __init__(self, files, icon, dark):
        super().__init__(tr("Trim edges"), describe(files), icon, dark)
        self.margin = _combo(MARGINS, "small")
        self.margin.setAccessibleName(tr("Room left around it"))
        row = QGridLayout()
        row.setHorizontalSpacing(12)
        row.addWidget(QLabel(tr("Room left around it")), 0, 0)
        row.addWidget(self.margin, 0, 1)
        self.lay.addLayout(row)
        self.same = QCheckBox(tr("Cut every page the same, so they all still match"))
        self.same.setChecked(True)
        self.same.setToolTip(tr("Pages of the same size are cut around what's on all of them together."))
        self.lay.addWidget(self.same)
        self.lay.addWidget(_note(tr("What's cut away is only hidden, not taken out of the file: to remove "
                                    "something for good, cover it in Edit content. Saved as a new file.")))
        self.buttons(tr("Trim edges"))

    def params(self):
        return {"margin": self.margin.currentData(), "same": self.same.isChecked()}


class SearchableDialog(Base):
    """Make searchable: what happens, and whether pages made searchable before are read again."""

    def __init__(self, files, icon, dark):
        super().__init__(tr("Make searchable"), describe(files), icon, dark)
        self.lay.addWidget(_note(tr("Rakiza Core reads the words on scanned pages with Windows' own reader, Arabic too, "
                                    "and puts them under the picture as hidden text, right where they are. You can "
                                    "then search, select and copy them. The pages look exactly as before.")))
        self.redo = QCheckBox(tr("Read pages made searchable before again"))
        self.redo.setToolTip(tr("Their old hidden text is taken out first, so nothing is there twice."))
        self.lay.addWidget(self.redo)
        self.lay.addWidget(_note(tr("Pages with their own text are left as they are. Saved as a new file.")))
        self.buttons(tr("Make searchable"))

    def params(self):
        return {"redo": self.redo.isChecked()}


class CleanDialog(Base):
    """Remove hidden info: what was found in the PDF(s), ticked to be taken out, and (for one PDF) its title
    and author to keep or change. found: hidden_info() of each file, or None when it couldn't be looked in."""

    def __init__(self, files, icon, dark, found: list):
        super().__init__(tr("Remove hidden info"), describe(files), icon, dark)
        known = [f for f in found if f]
        self.title = self.author = None
        if len(files) == 1 and known:
            grid = QGridLayout()
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(8)
            self.title, self.author = QLineEdit(known[0]["title"]), QLineEdit(known[0]["author"])
            self.title.setPlaceholderText(tr("No title"))
            self.author.setPlaceholderText(tr("No author"))
            for r, (text, w) in enumerate(((tr("Title"), self.title), (tr("Author"), self.author))):
                grid.addWidget(QLabel(text), r, 0)
                grid.addWidget(w, r, 1)
            grid.setColumnStretch(1, 1)
            self.lay.addLayout(grid)
        head = QLabel(tr("Take out"))
        head.setObjectName("cardTitle")
        self.lay.addWidget(head)
        self.boxes = {}
        complete = len(known) == len(found)  # (a locked PDF couldn't be looked in: everything stays ticked)
        for key, text, line in HIDDEN:
            count, what = self._found(key, known)
            box = QCheckBox(tr(text) + (f"  ·  {count}" if count else ""))
            box.setChecked(bool(count) or not complete)
            box.setEnabled(bool(count) or not complete)
            self.boxes[key] = box
            item = QVBoxLayout()
            item.setSpacing(0)
            item.addWidget(box)
            under = QHBoxLayout()  # (in under the box's tick, on whichever side it is)
            under.addSpacing(26)
            under.addWidget(_note(what or (tr(line) if count or not complete else tr("None found"))), 1)
            item.addLayout(under)
            self.lay.addLayout(item)
        self.lay.addWidget(_note(tr("What's on the pages, web links and form boxes stay as they are.") + " " +
                                 tr("Saved as a new file; your original stays as it is.")))
        self.buttons(tr("Save a clean copy"))

    @staticmethod
    def _found(key: str, known: list) -> tuple[int, str]:
        """How many of this kind were found, and (for who made it) what it says."""
        if key != "details":
            return sum(f[key] for f in known), ""
        bits = []
        for f in known:
            for name, value in f["details"]:
                if name != "Title" and value not in bits:
                    bits.append(value)
        text = "  ·  ".join(ltr(b[:60]) for b in bits[:4]) + ("  ·  …" if len(bits) > 4 else "")
        return len(bits), ("\u200f" + text if is_rtl() else text)

    def params(self):
        out = {"remove": [k for k, box in self.boxes.items() if box.isChecked()]}
        if self.title is not None:
            out.update(title=self.title.text(), author=self.author.text())
        return out


class PicturesDialog(Base):
    """Pictures to PDF: the page size, the margin, and the new file's name."""

    def __init__(self, files, icon, dark):
        super().__init__(tr("Pictures to PDF"), describe(files), icon, dark)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.size = _combo(PAGE_SIZES, "picture")
        self.margin = _combo(MARGINS, "none")
        self.name = QLineEdit(files[0].stem if len(files) == 1 else f"{files[0].stem} +{len(files) - 1}")
        for r, (text, w) in enumerate(((tr("Page size"), self.size), (tr("Margin"), self.margin),
                                       (tr("Name"), self.name))):
            grid.addWidget(QLabel(text), r, 0)
            grid.addWidget(w, r, 1)
        grid.setColumnStretch(1, 1)
        self.lay.addLayout(grid)
        self.size.currentIndexChanged.connect(self._sized)
        self.lay.addWidget(_note(tr("One page for each picture, in the order you picked them. Wide pictures get "
                                    "a page on its side. Pictures keep their quality.")))
        self.buttons(tr("Make PDF"))
        self._sized()

    def _sized(self, *_):
        self.margin.setEnabled(self.size.currentData() != "picture")

    def params(self):
        return {"size": self.size.currentData(), "margin": self.margin.currentData(), "name": self.name.text()}


class ToPicturesDialog(Base):
    def __init__(self, files, icon, dark, ranges: bool = True):
        super().__init__(tr("PDF to pictures"), describe(files), icon, dark)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.format = _combo(IMAGE_FORMATS, "png", translate=False)
        self.dpi = _combo(DPIS, 150)
        self.ranges = QLineEdit()
        self.ranges.setPlaceholderText(tr("All pages (or type some, like 1-3, 5)"))
        rows = [(tr("Format"), self.format), (tr("Sharpness"), self.dpi)] + ([(tr("Pages"), self.ranges)] if ranges
                                                                            else [])
        for r, (text, w) in enumerate(rows):
            grid.addWidget(QLabel(text), r, 0)
            grid.addWidget(w, r, 1)
        grid.setColumnStretch(1, 1)
        self.lay.addLayout(grid)
        self.lay.addWidget(_note(tr("PNG keeps every detail; JPG makes smaller files.")))
        self.buttons(tr("Save pictures"))

    def params(self):
        return {"format": self.format.currentData(), "dpi": int(self.dpi.currentData()),
                "ranges": self.ranges.text() if self.ranges.parent() is not None else ""}


class PdfLockDialog(LockDialog):
    """The Converter's password window, plus what people who open the PDF may still do."""

    def __init__(self, files, icon, dark):
        super().__init__(files, icon, dark)
        self.allow_print = QCheckBox(tr("Allow printing"))
        self.allow_copy = QCheckBox(tr("Allow copying text"))
        for i, box in enumerate((self.allow_print, self.allow_copy)):
            box.setChecked(True)
            self.lay.insertWidget(self.lay.indexOf(self.warn) + 1 + i, box)

    def params(self):
        return {**super().params(), "print": self.allow_print.isChecked(), "copy": self.allow_copy.isChecked()}


class PreviewDialog(Base):
    """One page, big. Left and right arrows go through the pages."""

    def __init__(self, title: str, icon, dark, render, index: int, count: int):
        super().__init__(title, "", icon, dark)
        self.render, self.index, self.count = render, index, count
        self.setMinimumSize(480, 560)
        screen = self.screen().availableGeometry() if self.screen() else None
        self.resize(760, min(980, screen.height() - 80) if screen else 900)
        self.page: QPixmap | None = None
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignCenter)
        self.picture.setMinimumSize(200, 200)
        self.picture.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.lay.addWidget(self.picture, 1)
        row = QHBoxLayout()
        self.back = QPushButton(tr("Previous"))
        self.next = QPushButton(tr("Next"))
        self.where = QLabel()
        self.where.setObjectName("muted")
        self.back.clicked.connect(lambda: self.go(-1))
        self.next.clicked.connect(lambda: self.go(1))
        close = QPushButton(tr("Close"))
        close.setObjectName("primary")
        close.clicked.connect(self.accept)
        for w in (self.back, self.where, self.next):
            row.addWidget(w)
        row.addStretch()
        row.addWidget(close)
        self.lay.addLayout(row)
        self.go(0)

    def go(self, step: int):
        self.index = max(0, min(self.count - 1, self.index + step))
        self.page = self.render(self.index)
        if self.page is None:
            self.picture.setPixmap(QPixmap())
            self.picture.setText(tr("This page couldn't be drawn."))
        else:
            self._fit()
        self.where.setText(tr("{number} of {total}", number=self.index + 1, total=self.count))
        self.back.setEnabled(self.index > 0)
        self.next.setEnabled(self.index < self.count - 1)

    def _fit(self):
        """The page as big as the window allows, never stretched."""
        if self.page is None:
            return
        ratio = self.page.devicePixelRatio()
        room = self.picture.size() * ratio
        pm = self.page.scaled(room, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(ratio)
        self.picture.setPixmap(pm)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_PageUp, Qt.Key_PageDown):
            forward = e.key() in (Qt.Key_Right, Qt.Key_PageDown)
            if self.layoutDirection() == Qt.RightToLeft and e.key() in (Qt.Key_Left, Qt.Key_Right):
                forward = not forward
            self.go(1 if forward else -1)
            return
        super().keyPressEvent(e)


class PrintDialog(Base):
    """Print: which printer, how many copies, which pages, both sides, black and white."""

    def __init__(self, files, icon, dark, count: int | None = None):
        from .pdfmore import can_print_both_sides, printers
        super().__init__(tr("Print"), describe(files), icon, dark)
        names, default = printers()
        self.count = count
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.printer = Dropdown()
        self.printer.setAccessibleName(tr("Printer"))
        for n in names:
            self.printer.addItem(n, n)
        self.printer.setCurrentIndex(max(0, self.printer.findData(default)))
        self.copies = QSpinBox()
        self.copies.setRange(1, 99)
        self.copies.setAccessibleName(tr("Copies"))
        self.pages = QLineEdit()
        self.pages.setPlaceholderText(tr("Every page (or type them, like 1-3, 5)"))
        self.pages.setAccessibleName(tr("Pages"))
        self.pages.setEnabled(len(files) == 1)
        for r, (text, w) in enumerate(((tr("Printer"), self.printer), (tr("Copies"), self.copies),
                                       (tr("Pages"), self.pages))):
            grid.addWidget(QLabel(text), r, 0)
            grid.addWidget(w, r, 1)
        grid.setColumnStretch(1, 1)
        self.lay.addLayout(grid)
        self.both = QCheckBox(tr("Print on both sides of the paper"))
        self.gray = QCheckBox(tr("Black and white"))
        self.lay.addWidget(self.both)
        self.lay.addWidget(self.gray)
        self.problem = _note("")
        self.problem.hide()
        self.lay.addWidget(self.problem)

        def printer_changed():
            ok = can_print_both_sides(self.printer.currentData() or "")
            self.both.setEnabled(ok)
            if not ok:
                self.both.setChecked(False)
            self.both.setToolTip("" if ok else tr("This printer can't print on both sides by itself"))
        self.printer.currentIndexChanged.connect(printer_changed)
        printer_changed()
        self.buttons(tr("Print"))
        if not names:
            self.printer.setEnabled(False)
            self.problem.setText(tr("Windows has no printer. Add one in Windows' Settings, under Printers & scanners."))
            self.problem.show()
            self.ok.setEnabled(False)
        self.pages.textChanged.connect(lambda: self._check())

    def _check(self) -> bool:
        from .pdfmore import page_numbers
        if self.count is None or not self.pages.text().strip():
            self.problem.hide()
            self.ok.setEnabled(self.printer.count() > 0)
            return True
        try:
            page_numbers(self.pages.text(), self.count)
        except ValueError as e:
            self.problem.setText(str(e))
            self.problem.show()
            self.ok.setEnabled(False)
            return False
        self.problem.hide()
        self.ok.setEnabled(True)
        return True

    def accept(self):
        if self._check():
            super().accept()

    def params(self):
        return {"printer": self.printer.currentData() or "", "copies": self.copies.value(),
                "pages": self.pages.text() if self.pages.isEnabled() else "", "both_sides": self.both.isChecked(),
                "gray": self.gray.isChecked()}


class BookmarksDialog(Base):
    """The PDF's bookmarks (the list of chapters readers show on the side): add, rename, arrange, take away."""

    def __init__(self, files, icon, dark, marks: list, pages: int, other: int = 0):
        from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem
        super().__init__(tr("Bookmarks"), describe(files), icon, dark)
        self.pages = max(1, pages)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Title"), tr("Page")])
        self.tree.setAccessibleName(tr("Bookmarks"))
        self.tree.setMinimumSize(460, 280)
        self.tree.setDragDropMode(QAbstractItemView.InternalMove)  # (dropped onto another: it goes inside it)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                                  QAbstractItemView.SelectedClicked)
        header = self.tree.header()
        header.setStretchLastSection(False)
        from PySide6.QtWidgets import QHeaderView
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._item_class = QTreeWidgetItem
        parents: list = []
        for level, title, page in marks:
            level = min(level, len(parents))
            item = self._new_item(title, page)
            if level:
                parents[level - 1].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            del parents[level:]
            parents.append(item)
        self.tree.expandAll()
        self.lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        self.add = QPushButton(tr("Add a bookmark"))
        self.add.clicked.connect(self._add)
        self.remove = QPushButton(tr("Remove"))
        self.remove.clicked.connect(self._remove)
        self.page = QSpinBox()
        self.page.setRange(1, self.pages)
        self.page.setAccessibleName(tr("Goes to page"))
        self.page.valueChanged.connect(self._page_changed)
        for b in (self.add, self.remove):
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        row.addStretch()
        row.addWidget(QLabel(tr("Goes to page")))
        row.addWidget(self.page)
        self.lay.addLayout(row)
        self.lay.addWidget(_note(tr("Drag a bookmark to move it; drop it onto another to put it inside that one. "
                                    "Double-click a title to change it. Saved as a new file.")))
        if other:
            self.lay.addWidget(_note(tr("{count} bookmarks that open a web page or another file are left out.",
                                        count=other)))
        self.buttons(tr("Save"))
        self.tree.currentItemChanged.connect(lambda *_: self._selected())
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self._selected()

    def _new_item(self, title: str, page: int):
        item = self._item_class([title, str(page + 1)])
        item.setData(1, Qt.UserRole, page)
        item.setFlags(item.flags() | Qt.ItemIsEditable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
        item.setTextAlignment(1, Qt.AlignCenter)
        return item

    def _selected(self):
        item = self.tree.currentItem()
        self.remove.setEnabled(item is not None)
        self.page.setEnabled(item is not None)
        if item is not None:
            self.page.blockSignals(True)
            self.page.setValue(int(item.data(1, Qt.UserRole)) + 1)
            self.page.blockSignals(False)

    def _page_changed(self, value: int):
        item = self.tree.currentItem()
        if item is not None:
            item.setData(1, Qt.UserRole, value - 1)
            item.setText(1, str(value))

    def _add(self):
        current = self.tree.currentItem()
        page = int(current.data(1, Qt.UserRole)) if current is not None else 0
        item = self._new_item(tr("Page {number}", number=page + 1), page)
        if current is None:
            self.tree.addTopLevelItem(item)
        else:  # (right after the one picked, at the same level)
            parent = current.parent()
            if parent is None:
                self.tree.insertTopLevelItem(self.tree.indexOfTopLevelItem(current) + 1, item)
            else:
                parent.insertChild(parent.indexOfChild(current) + 1, item)
        self.tree.setCurrentItem(item)
        self.tree.editItem(item, 0)

    def _remove(self):
        item = self.tree.currentItem()
        if item is None:
            return
        parent = item.parent()
        if parent is None:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        else:
            parent.removeChild(item)

    def params(self):
        marks: list = []

        def walk(item, level):
            page = item.data(1, Qt.UserRole)
            marks.append([level, item.text(0).strip(), int(page) if page is not None else 0])
            for i in range(item.childCount()):
                walk(item.child(i), level + 1)
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i), 0)
        return {"marks": marks}
