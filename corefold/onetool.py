"""A section that's one tool (Rakiza Core Files): over each of its tools' pages, a line back to its workspace,
"Files › Rename files", with what the tool does and its settings. The workspace itself (fileworkspace.py) has its
own heading, so this shows only on the tools' pages."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from . import icons
from .i18n import is_rtl, tr


class OneToolHead(QWidget):
    """The line over a tool's page: ← Files › the tool's name, a line about it, and its settings. Made again only
    when the section changes (or a new look)."""
    opened = Signal(str)     # a page to open (the workspace)
    settings = Signal()      # Settings pressed

    def __init__(self):
        super().__init__()
        self.setObjectName("oneToolHead")
        self.section = None
        self.tabs: dict[str, QPushButton] = {}  # (Tab goes through these after the top bar)
        self.crumb = None
        self.name = None
        self.line = None
        self.gear = None
        self.bar = None  # (no bar of uses any more: the workspace has every tool)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 4)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.hide()

    def show_section(self, sec, uses: list, theme, dpr: float):
        from .window import label
        self.section = sec
        lay = self.layout()
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
            elif item.layout():
                _clear(item.layout())
        row = QHBoxLayout()
        row.setSpacing(6)
        self.crumb = QPushButton(tr(sec.name))
        self.crumb.setObjectName("crumb")
        self.crumb.setCursor(Qt.PointingHandCursor)
        self.crumb.setIcon(icons.icon("arrow_right" if is_rtl() else "arrow_left", theme.muted))
        self.crumb.setIconSize(QSize(16, 16))
        self.crumb.setToolTip(tr("Back to {name}", name=tr(sec.name)))
        self.crumb.setAccessibleName(tr("Back to {name}", name=tr(sec.name)))
        self.crumb.clicked.connect(lambda _=False: self.opened.emit(sec.page))
        row.addWidget(self.crumb, 0, Qt.AlignVCenter)
        sep = label("›" if not is_rtl() else "‹", "muted")
        row.addWidget(sep, 0, Qt.AlignVCenter)
        self.name = label("", "cardTitle")
        row.addWidget(self.name, 0, Qt.AlignVCenter)
        self.line = label("", "subtle")
        self.line.setMinimumWidth(1)
        self.line.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.line, 1, Qt.AlignVCenter)
        self.gear = QPushButton(tr("Settings"))
        self.gear.setObjectName("small")
        self.gear.setIcon(icons.icon("settings", theme.text))
        self.gear.setCursor(Qt.PointingHandCursor)
        self.gear.clicked.connect(self.settings)
        row.addWidget(self.gear, 0, Qt.AlignVCenter)
        lay.addLayout(row)
        self.tabs = {sec.page: self.crumb}

    def set_current(self, page: str, line: str, gear: bool, name: str = ""):
        if self.name is not None:
            self.name.setText(name)
            self.line.setText(f"· {line}" if line else "")
            self.line.setToolTip(line)
            self.gear.setVisible(gear)

    def fit(self, room: int):
        """(Nothing to fold: the line about the tool is cut short by itself.)"""


def _clear(lay):
    while lay.count():
        item = lay.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())
