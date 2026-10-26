"""A section that's one tool (Corefold Files): one heading over all its pages, with its name and colour,
and a bar of what it can be used for. Picking a use changes only the page under the heading, so moving
between them feels like one tool, not several."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from . import icons
from .i18n import tr


class OneToolHead(QWidget):
    """The heading: the tool's tile and name, a line about the use that's open, ⚙, and the bar of uses.
    Made again only when the section or its uses change (switched on or off, a new look)."""
    opened = Signal(str)     # a page to open
    settings = Signal()      # ⚙ pressed

    def __init__(self):
        super().__init__()
        self.setObjectName("oneToolHead")
        self.section = None
        self.tabs: dict[str, QPushButton] = {}
        self.banner = None
        self.line = None
        self.gear = None
        self.bar = None
        self._icons = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.hide()

    def show_section(self, sec, uses: list, theme, dpr: float):
        """The heading of this section, with a tab for its start page and one for each use switched on (a use
        switched off has no tab: it isn't loaded)."""
        from .atomic.widgets import GlowPanel
        from .window import label
        self.section = sec
        lay = self.layout()
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().hide()  # (not drawn again at its old place before it goes)
                item.widget().deleteLater()
        dark = theme.dark
        color = QColor(icons.PRODUCT_COLORS.get(sec.product, ("#3cc7b2", "#0f8f7c"))[0 if dark else 1])
        self.banner = GlowPanel(self, color=color, rings=False)
        bv = QVBoxLayout(self.banner)
        bv.setContentsMargins(20, 16, 20, 12)
        bv.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(14)
        tile = QLabel()
        tile.setPixmap(icons.product_tile(sec.product, 48, dark, dpr))
        tile.setFixedSize(50, 50)
        top.addWidget(tile, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(label(tr(sec.one_tool), "h1"))
        self.line = label("", "muted", wrap=True)
        self.line.setMinimumWidth(1)  # (wraps: long translations)
        words.addWidget(self.line)
        top.addLayout(words, 1)
        self.gear = QPushButton(tr("Settings"))
        self.gear.setIcon(icons.icon("settings", theme.text))
        self.gear.setCursor(Qt.PointingHandCursor)
        self.gear.clicked.connect(self.settings)
        top.addWidget(self.gear, 0, Qt.AlignVCenter)
        bv.addLayout(top)
        self.bar = QFrame()
        self.bar.setObjectName("useBar")
        row = QHBoxLayout(self.bar)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(2)
        group = QButtonGroup(self.bar)
        self.tabs = {}
        self._icons = {}
        for page, icon, name, tip in [(sec.page, "home", tr("Start"), tr(sec.line))] + \
                [(t.page, t.icon, tr(t.use or t.nav), tr(t.short or t.line)) for t in uses]:
            b = QPushButton(name)
            b.setObjectName("useTab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIconSize(QSize(16, 16))
            b.setProperty("words", name)
            b.setProperty("tip", tip)
            b.setAccessibleName(name)
            b.setAccessibleDescription(tip)
            b.clicked.connect(lambda _=False, p=page: self.opened.emit(p))
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)  # (room to spare is shared; never less than its name)
            group.addButton(b)
            row.addWidget(b)
            self.tabs[page] = b
            self._icons[page] = (icons.icon(icon, theme.muted), icons.icon(icon, theme.text))
        bv.addWidget(self.bar)
        lay.addWidget(self.banner)

    def set_current(self, page: str, line: str, gear: bool):
        """Lights the use that's open and says what it does."""
        for name, b in self.tabs.items():
            b.setChecked(name == page)
            b.setIcon(self._icons[name][1 if name == page else 0])
        if self.line is not None:
            self.line.setText(line)
            self.gear.setVisible(gear)

    def fit(self, room: int):
        """A bar wider than the room shows the other uses as icons only (named when pointed at)."""
        tabs = list(self.tabs.values())
        if not tabs:
            return
        for b in tabs:
            b.setText(b.property("words"))
            b.setToolTip(b.property("tip"))
        spacing = self.bar.layout().spacing()
        need = sum(b.sizeHint().width() + spacing for b in tabs) + 52  # (the banner's and the bar's edges)
        for b in [b for b in tabs if not b.isChecked()]:  # (the use that's open keeps its name)
            if need <= room:
                break
            before = b.sizeHint().width()
            b.setText("")
            b.setToolTip(f"{b.property('words')}\n{b.property('tip')}")
            need -= before - b.sizeHint().width()


class UseCard(QFrame):
    """A use on the tool's start page: its icon, its name and what it's for. Click it (or Enter) to open it."""

    def __init__(self, tool, open_use, theme, dpr: float):
        super().__init__()
        from .window import label
        self.setObjectName("toolCard")
        self.tool = tool
        self.open_use = open_use
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)  # Tab reaches it; Enter opens it
        self.setAccessibleName(tr(tool.use or tool.nav))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)
        tile = QLabel()
        tile.setPixmap(icons.soft_tile(tool.icon, 36, theme.accent, dpr))
        tile.setFixedSize(36, 36)
        lay.addWidget(tile, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(label(tr(tool.use or tool.nav), "cardTitle"))
        line = label(tr(tool.short or tool.line), "subtle", wrap=True)
        line.setMinimumWidth(1)  # (wraps inside the card: long translations)
        words.addWidget(line)
        words.addStretch()
        lay.addLayout(words, 1)
        for w in (tile,) + tuple(self.findChildren(QLabel)):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.open_use()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.open_use()
        else:
            super().keyPressEvent(e)
