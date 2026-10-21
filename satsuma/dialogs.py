"""Small windows that ask for a choice before converting: resize, rotate, trim and new names."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QRadioButton, QSlider, QSpinBox, QVBoxLayout)

from .converters import RESIZE_PRESETS, ROTATIONS, category, media_duration, norm, video_frame
from .winutil import set_title_bar_dark


class Base(QDialog):
    def __init__(self, title: str, subtitle: str, icon: QIcon, dark: bool):
        super().__init__(None, Qt.WindowStaysOnTopHint | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setWindowTitle(title)
        self.setWindowIcon(icon)
        self.dark = dark
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(22, 18, 22, 16)
        self.lay.setSpacing(12)
        h = QLabel(title)
        h.setObjectName("h2")
        self.lay.addWidget(h)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("muted")
            s.setWordWrap(True)
            self.lay.addWidget(s)

    def buttons(self, ok_text: str = "Convert"):
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton(ok_text)
        self.ok.setObjectName("primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.accept)
        for b in (cancel, self.ok):
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        self.lay.addSpacing(4)
        self.lay.addLayout(row)

    def showEvent(self, e):
        super().showEvent(e)
        set_title_bar_dark(self, self.dark)
        self.raise_()
        self.activateWindow()

    def params(self) -> dict:
        return {}


def describe(files: list[Path]) -> str:
    return files[0].name if len(files) == 1 else f"{len(files)} files"


class ResizeDialog(Base):
    def __init__(self, files, icon, dark):
        super().__init__("Resize", describe(files), icon, dark)
        self.group = QButtonGroup(self)
        for key, (text, _, _) in RESIZE_PRESETS.items():
            rb = QRadioButton(text)
            rb.setProperty("key", key)
            rb.setChecked(key == "50")
            self.group.addButton(rb)
            self.lay.addWidget(rb)
        custom = QHBoxLayout()
        self.custom = QRadioButton("Custom width:")
        self.custom.setProperty("key", "custom")
        self.group.addButton(self.custom)
        self.width = QSpinBox()
        self.width.setRange(16, 20000)
        self.width.setValue(1200)
        self.width.setSuffix(" px")
        self.width.valueChanged.connect(lambda _: self.custom.setChecked(True))
        custom.addWidget(self.custom)
        custom.addWidget(self.width)
        custom.addStretch()
        self.lay.addLayout(custom)
        note = QLabel("The height changes to match, so nothing gets stretched. Your original stays as it is.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        self.lay.addWidget(note)
        self.buttons("Resize")

    def params(self):
        key = self.group.checkedButton().property("key")
        return {"preset": key, "width": self.width.value()}


class RotateDialog(Base):
    def __init__(self, files, icon, dark):
        super().__init__("Rotate or flip", describe(files), icon, dark)
        self.choice = None
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, (key, (text, _)) in enumerate(ROTATIONS.items()):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(40)
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            grid.addWidget(b, i // 2, i % 2)
        self.lay.addLayout(grid)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.lay.addLayout(row)

    def _pick(self, key):
        self.choice = key
        self.accept()

    def params(self):
        return {"how": self.choice or "right"}


def fmt_time(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}"


class TrimDialog(Base):
    STEPS = 1000

    def __init__(self, files, icon, dark):
        src = files[0]
        super().__init__("Trim", src.name, icon, dark)
        self.src = src
        self.video = category(src) == "video"
        self.duration = max(0.1, media_duration(src))

        self.start_s = QSlider(Qt.Horizontal)
        self.end_s = QSlider(Qt.Horizontal)
        for s in (self.start_s, self.end_s):
            s.setRange(0, self.STEPS)
            s.setCursor(Qt.PointingHandCursor)
        self.end_s.setValue(self.STEPS)
        self.start_n = QDoubleSpinBox()
        self.end_n = QDoubleSpinBox()
        for n in (self.start_n, self.end_n):
            n.setRange(0, self.duration)
            n.setDecimals(1)
            n.setSingleStep(0.5)
            n.setSuffix(" s")
        self.end_n.setValue(self.duration)

        self.thumbs = [QLabel(), QLabel()]
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        for col, (title, slider, num) in enumerate((("Start", self.start_s, self.start_n),
                                                    ("End", self.end_s, self.end_n))):
            t = QLabel(title)
            t.setObjectName("h2")
            grid.addWidget(t, 0, col)
            if self.video:
                self.thumbs[col].setFixedSize(240, 135)
                self.thumbs[col].setAlignment(Qt.AlignCenter)
                self.thumbs[col].setObjectName("muted")
                grid.addWidget(self.thumbs[col], 1, col)
            grid.addWidget(slider, 2, col)
            grid.addWidget(num, 3, col)
        self.lay.addLayout(grid)
        self.length = QLabel()
        self.length.setObjectName("muted")
        self.lay.addWidget(self.length)
        note = QLabel("Saved as a new file" + (" (MP4)." if self.video else ".") + " Your original stays as it is.")
        note.setObjectName("muted")
        self.lay.addWidget(note)
        self.buttons("Trim")

        self._syncing = False
        self.start_s.valueChanged.connect(lambda v: self._from_slider(0, v))
        self.end_s.valueChanged.connect(lambda v: self._from_slider(1, v))
        self.start_n.valueChanged.connect(lambda v: self._from_number(0, v))
        self.end_n.valueChanged.connect(lambda v: self._from_number(1, v))
        self.thumb_timer = [QTimer(self), QTimer(self)]
        for i, tm in enumerate(self.thumb_timer):
            tm.setSingleShot(True)
            tm.timeout.connect(lambda i=i: self._thumb(i))
        self._update()
        if self.video:
            QTimer.singleShot(50, lambda: (self._thumb(0), self._thumb(1)))

    def _values(self):
        return self.start_n.value(), self.end_n.value()

    def _from_slider(self, which, v):
        if self._syncing:
            return
        t = v / self.STEPS * self.duration
        self._syncing = True
        (self.start_n if which == 0 else self.end_n).setValue(t)
        self._syncing = False
        self._fix(which)

    def _from_number(self, which, t):
        if self._syncing:
            return
        self._syncing = True
        (self.start_s if which == 0 else self.end_s).setValue(round(t / self.duration * self.STEPS))
        self._syncing = False
        self._fix(which)

    def _fix(self, which):
        start, end = self._values()
        if end - start < 0.2:  # keep at least a fraction of a second
            self._syncing = True
            if which == 0:
                self.start_n.setValue(max(0.0, end - 0.2))
                self.start_s.setValue(round(self.start_n.value() / self.duration * self.STEPS))
            else:
                self.end_n.setValue(min(self.duration, start + 0.2))
                self.end_s.setValue(round(self.end_n.value() / self.duration * self.STEPS))
            self._syncing = False
        self._update()
        if self.video:
            self.thumb_timer[which].start(250)

    def _update(self):
        start, end = self._values()
        self.length.setText(f"Keeps {fmt_time(start)} → {fmt_time(end)}  ·  {fmt_time(end - start)} long "
                            f"(was {fmt_time(self.duration)})")

    def _thumb(self, which):
        t = self._values()[which]
        if which == 1:
            t = max(0.0, t - 0.05)
        data = video_frame(self.src, t)
        pm = QPixmap()
        if data and pm.loadFromData(data):
            self.thumbs[which].setPixmap(pm.scaled(240, 135, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.thumbs[which].setText("No preview")

    def params(self):
        start, end = self._values()
        return {"start": start, "end": end}


class RenameDialog(Base):
    def __init__(self, files, icon, dark):
        super().__init__("Name the new files", f"{len(files)} files", icon, dark)
        self.files = files
        self.base = QLineEdit()
        self.base.setPlaceholderText("e.g. vacation")
        self.start = QSpinBox()
        self.start.setRange(0, 99999)
        self.start.setValue(1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Name"))
        row.addWidget(self.base, 1)
        row.addSpacing(10)
        row.addWidget(QLabel("Start at"))
        row.addWidget(self.start)
        self.lay.addLayout(row)
        self.preview = QLabel()
        self.preview.setObjectName("muted")
        self.preview.setWordWrap(True)
        self.lay.addWidget(self.preview)
        self.base.textChanged.connect(self._update)
        self.start.valueChanged.connect(self._update)
        self.buttons("Convert")
        self.keep = QPushButton("Keep original names")
        self.keep.setCursor(Qt.PointingHandCursor)
        self.keep.clicked.connect(self._keep)
        self.lay.itemAt(self.lay.count() - 1).layout().insertWidget(0, self.keep)
        self.keep_names = False
        self._update()

    def _keep(self):
        self.keep_names = True
        self.accept()

    def _update(self):
        base = self.base.text().strip()
        if not base:
            self.preview.setText("Type a name, or keep the original names.")
            self.ok.setEnabled(False)
            return
        self.ok.setEnabled(True)
        n = self.start.value()
        ext = norm(self.files[0].suffix)
        names = [f"{base}-{n + i}{ext}" for i in range(min(3, len(self.files)))]
        more = "…" if len(self.files) > 3 else ""
        self.preview.setText("New files: " + ", ".join(names) + more)

    def params(self):
        if self.keep_names:
            return {}
        return {"rename_base": self.base.text().strip(), "rename_start": self.start.value()}


DIALOGS = {"resize": ResizeDialog, "rotate": RotateDialog, "trim": TrimDialog}


def ask(kind: str, files: list[Path], icon: QIcon, dark: bool) -> dict | None:
    """Shows a dialog; returns the user's choices, or None if they cancelled."""
    dlg = (RenameDialog if kind == "rename" else DIALOGS[kind])(files, icon, dark)
    return dlg.params() if dlg.exec() else None  # 1 = accepted, 0 = cancelled
