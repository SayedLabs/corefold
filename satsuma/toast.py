"""Small popup in the bottom corner showing progress and results."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QIcon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from .core import os_open
from .i18n import tr
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
        self.open_path: Path | None = None
        self.custom_action = None  # callback for notify() buttons
        self.extra_action = None   # callback for the second button ("Undo", "Compare")
        self.corner = "auto"       # where it shows up (see settings.TOAST_CORNERS)
        self.time_scale = 1.0      # how long it stays (short, normal, long)

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
        self.percent = QLabel()
        self.percent.setObjectName("muted")
        self.open_btn = QPushButton(tr("Open"))
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(self._open)
        self.extra_btn = QPushButton()
        self.extra_btn.setCursor(Qt.PointingHandCursor)
        self.extra_btn.clicked.connect(self._on_extra)
        self.extra_btn.hide()
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
        row.addWidget(self.percent)
        row.addStretch(0)
        row.addWidget(self.extra_btn)
        row.addWidget(self.open_btn)
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
        """A corner of the screen you're working on (by default bottom right, bottom left in Arabic)."""
        self.adjustSize()
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        corner = self.corner
        if corner not in ("bottom-right", "bottom-left", "top-right", "top-left"):
            rtl = QGuiApplication.layoutDirection() == Qt.RightToLeft
            corner = "bottom-left" if rtl else "bottom-right"
        x = g.left() + 16 if corner.endswith("left") else g.right() - self.width() - 16
        y = g.top() + 16 if corner.startswith("top") else g.bottom() - self.height() - 16
        self.move(x, y)

    def _linger(self, ms: int):
        self.hide_timer.start(max(1500, int(ms * self.time_scale)))

    def _set_extra(self, text: str = "", action=None):
        self.extra_action = action if text else None
        self.extra_btn.setText(text)
        self.extra_btn.setVisible(bool(text and action))

    def _on_extra(self):
        action, self.extra_action = self.extra_action, None
        self.hide()
        if action:
            action()

    def _show_buttons(self, action_text: str = "", open_path: Path | None = None):
        self.open_path = open_path
        self.open_btn.setVisible(open_path is not None)
        self.action.setText(action_text)
        self.action.setEnabled(True)
        self.action.setVisible(bool(action_text))

    def start(self, job_id: int, title: str, detail: str):
        self.job_id, self.running, self.reveal_path, self.custom_action = job_id, True, None, None
        self.title.setText(title)
        self.detail.setText(detail)
        self.bar.setValue(0)
        self.bar.show()
        self.percent.setText("")
        self.percent.show()
        self._set_extra()
        self._show_buttons(tr("Cancel"))
        self.hide_timer.stop()
        self._place()
        self.show()

    def set_detail(self, job_id: int, text: str):
        if job_id == self.job_id and self.running:
            self.detail.setText(text)

    def set_progress(self, job_id: int, frac: float):
        if job_id == self.job_id and self.running:
            self.bar.setValue(int(frac * 1000))
            self.percent.setText(f"{int(frac * 100)}%")

    def finish(self, job_id: int, title: str, detail: str, reveal: Path | None, linger_ms: int,
               single: bool = True, extra_text: str = "", extra=None) -> bool:
        """Show a result, unless a newer job is still running on screen. Returns True if shown."""
        if job_id != self.job_id and self.running:
            return False
        self.job_id, self.running, self.reveal_path, self.custom_action = job_id, False, reveal, None
        self.title.setText(title)
        self.detail.setText(detail)
        self.bar.hide()
        self.percent.hide()
        # "Open" for a single new file (not for folders: "Show in folder" does that)
        can_open = single and reveal is not None and reveal.is_file()
        self._show_buttons(tr("Show in folder") if reveal is not None else "", reveal if can_open else None)
        self._set_extra(extra_text, extra)
        self._place()
        self.show()
        self._linger(linger_ms + (3000 if extra else 0))
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
        self.percent.hide()
        self._show_buttons(action_text if (action_text and action) else "")
        self._set_extra()
        self._place()
        self.show()
        self._linger(linger_ms)
        return True

    def _open(self):
        if self.open_path is not None and self.open_path.exists():
            try:
                os_open(self.open_path)
            except OSError:
                pass
        self.hide()

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

