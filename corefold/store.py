"""The first-start question "Which tools do you want?" (the Store page itself is in window.py)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from . import icons
from . import tools as all_tools
from .i18n import tr


class ToolPicker(QDialog):
    """Every ready tool, ticked; the ones left unticked go to the Store (they can be added any time)."""

    def __init__(self, icon: QIcon, dark: bool, chosen: set[str] | None = None):
        super().__init__(None, Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setWindowTitle(tr("Welcome"))
        self.setWindowIcon(icon)
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        title = QLabel(tr("Which tools do you want?"))
        title.setObjectName("h1")
        lay.addWidget(title)
        intro = QLabel(tr("Pick the ones you'll use. The others stay in the Store, and you can add them any time. "
                          "Tools you don't pick aren't loaded at all, so Corefold stays light."))
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        dpr = self.devicePixelRatioF()
        self.boxes: dict[str, QCheckBox] = {}
        for tool in all_tools.ready():
            row = QFrame()
            row.setObjectName("card")
            h = QHBoxLayout(row)
            h.setContentsMargins(14, 10, 14, 10)
            h.setSpacing(12)
            tile = QLabel()
            tile.setPixmap(icons.product_tile(tool.product, 38, dark, dpr))
            tile.setFixedSize(40, 40)
            h.addWidget(tile, 0, Qt.AlignTop)
            words = QVBoxLayout()
            words.setSpacing(2)
            name = QLabel(tr(tool.name))
            name.setObjectName("cardTitle")
            line = QLabel(tr(tool.line))
            line.setObjectName("muted")
            line.setWordWrap(True)
            line.setMinimumWidth(1)
            words.addWidget(name)
            words.addWidget(line)
            h.addLayout(words, 1)
            box = QCheckBox()
            box.setCursor(Qt.PointingHandCursor)
            box.setChecked(chosen is None or tool.id in chosen)
            h.addWidget(box, 0, Qt.AlignVCenter)
            self.boxes[tool.id] = box
            lay.addWidget(row)
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
