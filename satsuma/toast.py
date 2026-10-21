"""Small popup in the bottom-right corner showing progress and results."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from .winutil import reveal_in_explorer


class Toast(QWidget):
    cancel_requested = Signal(int)

    def __init__(self, icon: QIcon):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(370)
        self.job_id: int | None = None
        self.running = False
        self.reveal_path: Path | None = None
        self.custom_action = None  # callback for notify() buttons

        panel = QFrame()
        panel.setObjectName("toast")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)

        ico = QLabel()
        ico.setPixmap(icon.pixmap(34, 34))
        ico.setFixedSize(38, 38)
        self.title = QLabel()
        self.title.setObjectName("toastTitle")
        self.detail = QLabel()
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        self.action = QPushButton()
        self.action.setObjectName("primary")
        self.action.setCursor(Qt.PointingHandCursor)
        self.action.clicked.connect(self._on_action)
        close = QPushButton("✕")
        close.setObjectName("close")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.hide)

        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(self.title)
        text.addWidget(self.detail)
        top = QHBoxLayout()
        top.addWidget(ico, 0, Qt.AlignTop)
        top.addLayout(text, 1)
        top.addWidget(close, 0, Qt.AlignTop)
        row = QHBoxLayout()
        row.addWidget(self.bar, 1)
        row.addWidget(self.action)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 12, 12, 12)
        lay.setSpacing(8)
        lay.addLayout(top)
        lay.addLayout(row)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

    def _place(self):
        self.adjustSize()
        g = QGuiApplication.primaryScreen().availableGeometry()
        self.move(g.right() - self.width() - 16, g.bottom() - self.height() - 16)

    def start(self, job_id: int, title: str, detail: str):
        self.job_id, self.running, self.reveal_path, self.custom_action = job_id, True, None, None
        self.title.setText(title)
        self.detail.setText(detail)
        self.bar.setValue(0)
        self.bar.show()
        self.action.setText("Cancel")
        self.action.setEnabled(True)
        self.action.show()
        self.hide_timer.stop()
        self._place()
        self.show()

    def set_progress(self, job_id: int, frac: float):
        if job_id == self.job_id and self.running:
            self.bar.setValue(int(frac * 1000))

    def finish(self, job_id: int, title: str, detail: str, reveal: Path | None, linger_ms: int) -> bool:
        """Show a result, unless a newer job is still running on screen. Returns True if shown."""
        if job_id != self.job_id and self.running:
            return False
        self.job_id, self.running, self.reveal_path, self.custom_action = job_id, False, reveal, None
        self.title.setText(title)
        self.detail.setText(detail)
        self.bar.hide()
        self.action.setText("Show in folder")
        self.action.setEnabled(True)
        self.action.setVisible(reveal is not None)
        self._place()
        self.show()
        self.hide_timer.start(linger_ms)
        return True

    def notify(self, title: str, detail: str, action_text: str | None = None, action=None,
               linger_ms: int = 10000) -> bool:
        """A message with an optional button. Skipped while a conversion is on screen."""
        if self.running and self.isVisible():
            return False
        self.job_id, self.running, self.reveal_path, self.custom_action = None, False, None, action
        self.title.setText(title)
        self.detail.setText(detail)
        self.bar.hide()
        self.action.setText(action_text or "")
        self.action.setEnabled(True)
        self.action.setVisible(bool(action_text and action))
        self._place()
        self.show()
        self.hide_timer.start(linger_ms)
        return True

    def _on_action(self):
        if self.custom_action and not self.running:
            action, self.custom_action = self.custom_action, None
            self.hide()
            action()
        elif self.running and self.job_id is not None:
            self.cancel_requested.emit(self.job_id)
            self.action.setEnabled(False)
        elif self.reveal_path:
            reveal_in_explorer(self.reveal_path)
            self.hide()

    def enterEvent(self, e):
        self.hide_timer.stop()

    def leaveEvent(self, e):
        if not self.running and self.isVisible():
            self.hide_timer.start(2500)
