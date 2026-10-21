"""A short welcome tour: what Satsuma does, in five small steps."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from .i18n import tr

# (big picture, title, text) - English, shown through tr()
STEPS = [
    ("🍊", "Welcome to Satsuma", "Convert, shrink and edit files without opening any app. Here's how, in a "
                                 "few quick steps."),
    ("🖱️", "Hold {key} and drag a file", "Start dragging a file in File Explorer or on your desktop while holding "
                                         "{key}. A wheel of options pops up right where you are."),
    ("🎯", "Drop it on an option", "Let go over \"PNG\", \"Resize\", \"Make smaller\"… The new file appears next to "
                                   "the original, which is never changed. Let go in the middle to cancel."),
    ("📋", "Or right-click it", "Right-click any file or folder and choose Satsuma. On Windows 11 it's under \"Show "
                                "more options\"."),
    ("🧹", "Clean up apps too", "The Apps page uninstalls programs and then removes what they leave behind, with "
                                "a backup you can put back."),
    ("🎨", "Make it yours", "Change the colours, the wheel's shape and size, the sounds and which options show up, "
                            "on the Look and Formats pages."),
]


class Tour(QDialog):
    def __init__(self, icon: QIcon, key: str):
        super().__init__(None, Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setWindowTitle(tr("Welcome"))
        self.setWindowIcon(icon)
        self.setFixedWidth(460)
        self.stack = QStackedWidget()
        for pic, title, text in STEPS:
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.setContentsMargins(10, 6, 10, 6)
            big = QLabel(pic)
            f = QFont("Segoe UI Emoji")
            f.setPointSize(40)
            big.setFont(f)
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
