"""Corefold PDF's small windows: how to split, how much to compress, pictures to PDF, PDF to
pictures, a password with what it still allows, and one page shown big."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QRadioButton, QSizePolicy, QSpinBox)

from .atomic.widgets import Dropdown
from .dialogs import Base, describe
from .dialogs2 import LockDialog
from .i18n import tr
from .pdftools import COMPRESS_LEVELS, DPIS, IMAGE_FORMATS, MARGINS, PAGE_SIZES, SPLIT_MODES


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
