"""A section that's one tool (Corefold Files): its features down the side of its pages, each with a few
words under its name. The one picked opens beside them and the others stay one click away; the tool's own
row at the top opens its main page, which tells more about each feature."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from . import i18n, icons
from .i18n import tr

WIDE = 248     # the list's width with the names and their words
NARROW = 60    # icons only (a narrow window): the names and words show when pointed at


class FeatureRow(QFrame):
    """One row of the list: a tile, and the name with a few words under it."""
    picked = Signal(str)

    def __init__(self, page: str, title: str, line: str, tile: QPixmap, head: bool = False):
        super().__init__()
        self.page = page
        self.setObjectName("featureRow")
        self.setProperty("head", head)
        self.setProperty("current", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)  # Tab reaches it; Enter opens it; the arrow keys go up and down
        self.setAccessibleName(title)
        self.setAccessibleDescription(line)
        self.words_tip = f"{title}\n{line}"
        lay = QHBoxLayout(self)
        lay.setSpacing(10)
        self.tile = QLabel()
        self.tile.setPixmap(tile)
        side = round(tile.width() / tile.devicePixelRatio())
        self.tile.setFixedSize(side, side)
        lay.addWidget(self.tile, 0, Qt.AlignTop)
        self.words = QWidget()
        words = QVBoxLayout(self.words)
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(1)
        self.title = QLabel(title)
        self.title.setObjectName("cardTitle" if head else "featureName")
        words.addWidget(self.title)
        self.line = QLabel(line)
        self.line.setObjectName("subtle")
        self.line.setWordWrap(True)
        self.line.setMinimumWidth(1)  # (wraps inside the row: long translations)
        words.addWidget(self.line)
        if i18n.is_rtl():  # (a name in Latin letters, like "PDF", still starts on the right)
            for lab in (self.title, self.line):
                lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.words, 1)
        for w in (self.tile, self.words, self.title, self.line):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.set_narrow(False)

    def set_narrow(self, narrow: bool):
        self.words.setVisible(not narrow)
        self.layout().setContentsMargins(*((8, 8, 8, 8) if narrow else (10, 8, 10, 9)))
        self.setToolTip(self.words_tip if narrow else "")

    def set_current(self, on: bool):
        if self.property("current") == on:
            return
        self.setProperty("current", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.picked.emit(self.page)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.picked.emit(self.page)
        elif e.key() in (Qt.Key_Down, Qt.Key_Up):
            self.focusNextPrevChild(e.key() == Qt.Key_Down)
        else:
            super().keyPressEvent(e)


class FeatureSide(QFrame):
    """The list itself. Made again only when the section or its features change (switched on or off, a new
    look), so moving between features only moves the lit row."""
    opened = Signal(str)  # a page to open

    def __init__(self):
        super().__init__()
        self.setObjectName("featureSide")
        self.section = None
        self.rows: dict[str, FeatureRow] = {}
        self.narrow = False
        self.more = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area = QScrollArea()
        area.setObjectName("featureScroll")
        area.setFrameShape(QFrame.NoFrame)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.inner.setObjectName("featureInner")
        self.list = QVBoxLayout(self.inner)
        self.list.setContentsMargins(0, 0, 4, 0)
        self.list.setSpacing(2)
        area.setWidget(self.inner)
        outer.addWidget(area)
        self.setFixedWidth(WIDE)
        self.hide()

    def show_section(self, sec, tools: list, off: int, accent, dark: bool, dpr: float):
        """The tool's row, then each feature in use (a feature switched off isn't listed: it isn't loaded)."""
        self.section = sec
        while self.list.count():
            item = self.list.takeAt(0)
            if item.widget():
                item.widget().hide()  # (not drawn again at its old place before it goes)
                item.widget().deleteLater()
        self.rows = {}
        head = FeatureRow(sec.page, tr(sec.one_tool), tr(sec.line), icons.product_tile(sec.product, 36, dark, dpr),
                          head=True)
        self.rows[sec.page] = head
        self.list.addWidget(head)
        self.list.addSpacing(8)
        for tool in tools:
            self.rows[tool.page] = FeatureRow(tool.page, tr(tool.nav), tr(tool.short or tool.line),
                                              icons.soft_tile(tool.icon, 32, accent, dpr))
            self.list.addWidget(self.rows[tool.page])
        for row in self.rows.values():
            row.picked.connect(self.opened)
            row.set_narrow(self.narrow)
        self.more = None
        if off:  # (the tool's page is where they're switched on)
            # (made inside the list: a button without a parent is shown as a window of its own for a moment)
            self.more = QPushButton(tr("{count} more you can switch on", count=off), self.inner)
            self.more.setObjectName("flat")
            self.more.setCursor(Qt.PointingHandCursor)
            self.more.clicked.connect(lambda: self.opened.emit(sec.page))
            self.list.addSpacing(4)
            self.list.addWidget(self.more, 0, Qt.AlignLeading)
            self.more.setVisible(not self.narrow)
        self.list.addStretch()

    def set_current(self, page: str):
        for name, row in self.rows.items():
            row.set_current(name == page)

    def set_narrow(self, narrow: bool):
        if narrow == self.narrow:
            return
        self.narrow = narrow
        self.setFixedWidth(NARROW if narrow else WIDE)
        for row in self.rows.values():
            row.set_narrow(narrow)
        if self.more is not None:
            self.more.setVisible(not narrow)

    def sizeHint(self) -> QSize:
        return QSize(self.width(), 200)
