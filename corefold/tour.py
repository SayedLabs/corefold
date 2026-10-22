"""A short welcome tour: what Corefold does, in five small steps."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from . import icons
from .i18n import tr

# (big picture: an icon from icons.py, or "" for the app's own; title, text) - English, shown through tr()
STEPS = [
    ("", "Welcome to Corefold", "Convert, shrink and edit files without opening any app. Here's how, in a "
                                 "few quick steps."),
    ("cursor", "Hold {key} and drag a file", "Start dragging a file in File Explorer or on your desktop while holding "
                                         "{key}. A wheel of options pops up right where you are."),
    ("target", "Drop it on an option", "Let go over \"PNG\", \"Resize\", \"Make smaller\"… The new file appears next to "
                                   "the original, which is never changed. Let go in the middle to cancel."),
    ("cursor_click", "Or right-click it", "Right-click any file or folder and choose Corefold. On Windows 11 it's under \"Show "
                                "more options\"."),
    ("broom", "Clean up apps too", "The Apps page uninstalls programs and then removes what they leave behind, with "
                                "a backup you can put back."),
    ("paint_brush", "Make it yours", "Change the colours, the wheel's shape and size, the sounds and which options show up "
                            "in Settings. Each tool has its own tab there."),
]


class Tour(QDialog):
    def __init__(self, icon: QIcon, key: str, color: QColor | None = None):
        super().__init__(None, Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setWindowTitle(tr("Welcome"))
        self.setWindowIcon(icon)
        self.setFixedWidth(460)
        self.stack = QStackedWidget()
        for pic, title, text in STEPS:
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.setContentsMargins(10, 6, 10, 6)
            big = QLabel()
            dpr = self.devicePixelRatioF()
            big.setPixmap(icons.pixmap(pic, 56, color or QColor("#0a7768"), dpr) if pic else icon.pixmap(64, 64))
            big.setMinimumHeight(72)
            big.setAlignment(Qt.AlignCenter)
            t = QLabel(tr(title, key=key))
            t.setObjectName("h1")
            t.setAlignment(Qt.AlignCenter)
            t.setWordWrap(True)
            d = QLabel(tr(text, key=key))
            d.setObjectName("muted")
            d.setAlignment(Qt.AlignCenter)
            d.setWordWrap(True)
            d.setMinimumHeight(64)
            lay.addWidget(big)
            lay.addWidget(t)
            lay.addWidget(d)
            self.stack.addWidget(w)
        self.dots = QLabel()
        self.dots.setAlignment(Qt.AlignCenter)
        self.back = QPushButton(tr("Back"))
        self.back.clicked.connect(lambda: self.go(-1))
        self.skip = QPushButton(tr("Skip"))
        self.skip.setObjectName("flat")
        self.skip.clicked.connect(self.accept)
        self.next = QPushButton()
        self.next.setObjectName("primary")
        self.next.clicked.connect(lambda: self.go(1))
        for b in (self.back, self.skip, self.next):
            b.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout()
        row.addWidget(self.skip)
        row.addStretch()
        row.addWidget(self.back)
        row.addWidget(self.next)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.addWidget(self.stack)
        lay.addWidget(self.dots)
        lay.addLayout(row)
        self.go(0)

    def go(self, step: int):
        i = self.stack.currentIndex() + step
        if i >= self.stack.count():
            self.accept()
            return
        i = max(0, i)
        self.stack.setCurrentIndex(i)
        last = i == self.stack.count() - 1
        self.next.setText(tr("Start") if last else tr("Next"))
        self.back.setVisible(i > 0)
        self.skip.setVisible(not last)
        self.dots.setText(" ".join("●" if n == i else "○" for n in range(self.stack.count())))
