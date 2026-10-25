"""The first-start question "Which tools do you want?" (switching them on and off later is on each
section's page, in window.py)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from . import icons
from . import tools as all_tools
from .i18n import tr


class ToolPicker(QDialog):
    """Every available tool, ticked, under its section's name; the ones left unticked stay switched off (they can
    be switched on any time in their section)."""

    def __init__(self, icon: QIcon, dark: bool, chosen: set[str] | None = None):
        super().__init__(None, Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setWindowTitle(tr("Welcome"))
        self.setWindowIcon(icon)
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        title = QLabel(tr("Which tools do you want?"))
        title.setObjectName("h1")
        lay.addWidget(title)
        intro = QLabel(tr("Pick the ones you'll use. The others stay switched off in their section, and you can "
                          "switch them on any time. Tools you don't pick aren't loaded at all, so Corefold stays "
                          "light."))
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        dpr = self.devicePixelRatioF()
        self.boxes: dict[str, QCheckBox] = {}
        for sec in all_tools.SECTIONS:  # each section's tools under its name, two to a row (it fits a small screen)
            tools = [all_tools.BY_ID[tool_id] for tool_id in sec.tools  # (a section resting for now: none)
                     if all_tools.state(tool_id) == all_tools.AVAILABLE]
            if not tools:
                continue
            head = QLabel(tr(sec.name))
            head.setObjectName("h2")
            lay.addWidget(head)
            grid = QGridLayout()
            grid.setSpacing(8)
            for i, tool in enumerate(tools):
                row = QFrame()
                row.setObjectName("card")
                row.setToolTip(tr(tool.line))
                h = QHBoxLayout(row)
                h.setContentsMargins(12, 8, 12, 8)
                h.setSpacing(10)
                tile = QLabel()
                tile.setPixmap(icons.product_tile(tool.product, 30, dark, dpr))
                tile.setFixedSize(32, 32)
                h.addWidget(tile)
                name = QLabel(tr(tool.nav))
                name.setObjectName("cardTitle")
                h.addWidget(name, 1)
                box = QCheckBox()
                box.setCursor(Qt.PointingHandCursor)
                box.setChecked(chosen is None or tool.id in chosen)
                box.setAccessibleName(tr(tool.nav))
                h.addWidget(box, 0, Qt.AlignVCenter)
                self.boxes[tool.id] = box
                grid.addWidget(row, i // 2, i % 2)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            lay.addLayout(grid)
        lay.addSpacing(4)
        buttons = QHBoxLayout()
        buttons.addStretch()
        go = QPushButton(tr("Continue"))
        go.setObjectName("primary")
        go.setCursor(Qt.PointingHandCursor)
        go.clicked.connect(self.accept)
        buttons.addWidget(go)
        lay.addLayout(buttons)

    def chosen(self) -> set[str]:
        return {tool_id for tool_id, box in self.boxes.items() if box.isChecked()}


def ask_tools(icon: QIcon, dark: bool) -> set[str] | None:
    """The tools picked on the first start (None: the window was closed, so everything stays on)."""
    dlg = ToolPicker(icon, dark)
    ok = dlg.exec() == QDialog.Accepted
    picked = dlg.chosen() if ok else None
    dlg.deleteLater()
    return picked
