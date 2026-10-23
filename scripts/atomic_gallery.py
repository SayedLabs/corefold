"""Shows every part of the Atomic design system on one page, in light and dark, and saves a picture of each.

Run: python scripts/atomic_gallery.py <folder>       (a window opens if no folder is given)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit,  # noqa: E402
                               QProgressBar, QPushButton, QSlider, QVBoxLayout, QWidget)

from corefold.atomic import qss, tokens  # noqa: E402
from corefold.atomic.widgets import Dropdown, GlowPanel, Group, SearchButton, SettingRow, Switch, mark_rows  # noqa: E402


def gallery() -> QWidget:
    w = QWidget()
    w.setObjectName("window")
    w.setAttribute(Qt.WA_StyledBackground)
    root = QHBoxLayout(w)
    root.setContentsMargins(8, 8, 8, 8)
    canvas = QFrame()
    canvas.setObjectName("canvas")
    root.addWidget(canvas)
    lay = QVBoxLayout(canvas)
    lay.setContentsMargins(28, 22, 28, 22)
    lay.setSpacing(14)

    def lab(text, name=None):
        x = QLabel(text)
        if name:
            x.setObjectName(name)
        return x

    hero = GlowPanel()
    hl = QVBoxLayout(hero)
    hl.setContentsMargins(24, 20, 24, 20)
    hl.addWidget(lab("Atomic", "display"))
    hl.addWidget(lab("Colours, type, spacing, corners and motion for Atomic Works apps.", "muted"))
    lay.addWidget(hero)

    row = QHBoxLayout()
    for text, name in (("Primary", "primary"), ("Default", None), ("Ghost", "ghost"), ("Delete", "danger"),
                       ("Link", "flat")):
        b = QPushButton(text)
        if name:
            b.setObjectName(name)
        row.addWidget(b)
    row.addStretch()
    row.addWidget(SearchButton("Search"))
    lay.addLayout(row)

    row = QHBoxLayout()
    edit = QLineEdit()
    edit.setPlaceholderText("A text box")
    combo = Dropdown()
    combo.addItems(["A list", "Another"])
    slider = QSlider(Qt.Horizontal)
    slider.setValue(60)
    for x in (edit, combo, slider):
        row.addWidget(x, 1)
    lay.addLayout(row)
    bar = QProgressBar()
    bar.setValue(40)
    bar.setTextVisible(False)
    lay.addWidget(bar)

    chips = QHBoxLayout()
    for text, name in (("A chip", "chip"), ("Ctrl K", "kbd"), ("A badge", "badge")):
        chips.addWidget(lab(text, name))
    chips.addStretch()
    lay.addLayout(chips)

    lay.addWidget(lab("Settings", "eyebrow"))
    group = Group()
    group.add(SettingRow("A setting with a switch", "A line about what it does.", Switch()))
    combo2 = Dropdown()
    combo2.addItems(["Automatic"])
    group.add(SettingRow("A setting with a list", "", combo2))
    lay.addWidget(group)

    card = QFrame()
    card.setObjectName("card")
    cl = QVBoxLayout(card)
    cl.setContentsMargins(18, 10, 18, 10)
    for i, text in enumerate(("Switch rows", "follow each other", "with hairlines between")):
        sw = Switch(text)
        sw.setChecked(i != 1)
        cl.addWidget(sw)
    lay.addWidget(card)
    lay.addStretch()
    mark_rows(w)
    return w


def main():
    app = QApplication(sys.argv)
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    for dark in (True, False):
        pal = tokens.palette(dark, QColor("#3cc7b2" if dark else "#0a7768"))
        tokens.use(pal)
        app.setStyleSheet(qss.base(pal))
        w = gallery()
        w.resize(760, 760)
        w.show()
        for _ in range(20):
            app.processEvents()
        if out:
            out.mkdir(parents=True, exist_ok=True)
            w.grab().save(str(out / f"atomic-{'dark' if dark else 'light'}.png"))
            w.close()
        else:
            app.exec()


if __name__ == "__main__":
    main()
