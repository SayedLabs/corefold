"""Small windows that ask for a choice before converting: resize, rotate, trim, fit size,
watermark, frames, slideshow, archive options, passwords and new names."""
from __future__ import annotations

from pathlib import Path

import io

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QSlider,
                               QSpinBox, QVBoxLayout)

from .converters import RESIZE_PRESETS, ROTATIONS, category, load_image, media_duration, norm, video_frame
from .core import human_size
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


class FitDialog(Base):
    def __init__(self, files, icon, dark, last_mb: float = 10):
        from .extras import FIT_PRESETS
        size = sum(f.stat().st_size for f in files if f.exists())
        super().__init__("Make it fit", f"{describe(files)} · {human_size(size)} now", icon, dark)
        self.group = QButtonGroup(self)
        for mb, text in FIT_PRESETS:
            rb = QRadioButton(f"Under {text}")
            rb.setProperty("mb", float(mb))
            rb.setChecked(abs(mb - last_mb) < 1e-6)
            self.group.addButton(rb)
            self.lay.addWidget(rb)
        row = QHBoxLayout()
        self.custom = QRadioButton("Under")
        self.custom.setProperty("mb", -1.0)
        self.group.addButton(self.custom)
        self.mb = QDoubleSpinBox()
        self.mb.setRange(0.1, 4000)
        self.mb.setDecimals(1)
        self.mb.setSuffix(" MB")
        self.mb.setValue(last_mb if last_mb not in [m for m, _ in FIT_PRESETS] else 15)
        self.mb.valueChanged.connect(lambda _: self.custom.setChecked(True))
        row.addWidget(self.custom)
        row.addWidget(self.mb)
        row.addStretch()
        self.lay.addLayout(row)
        if not self.group.checkedButton():
            self.custom.setChecked(True)
        note = QLabel("Satsuma picks the best quality that still fits. Videos keep their sound; very long videos "
                      "may need a bigger size.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        self.lay.addWidget(note)
        self.buttons("Make it fit")

    def params(self):
        b = self.group.checkedButton()
        mb = b.property("mb") if b else -1.0
        return {"mb": self.mb.value() if mb is None or mb < 0 else float(mb)}


class WatermarkDialog(Base):
    def __init__(self, files, icon, dark, last: dict | None = None):
        from .extras import POSITIONS, WM_SIZES
        super().__init__("Watermark", describe(files), icon, dark)
        last = last or {}
        self.files = files
        self.text = QLineEdit(last.get("text", ""))
        self.text.setPlaceholderText("e.g. © Your Name")
        self.logo = last.get("logo", "") if Path(last.get("logo", "") or "-").exists() else ""
        self.logo_label = QLabel()
        self.logo_label.setObjectName("muted")
        pick_logo = QPushButton("Choose logo…")
        pick_logo.clicked.connect(self._pick_logo)
        clear_logo = QPushButton("No logo")
        clear_logo.clicked.connect(self._clear_logo)
        self.position = QComboBox()
        for k, v in POSITIONS.items():
            self.position.addItem(v, k)
        self.position.setCurrentIndex(max(0, self.position.findData(last.get("position", "br"))))
        self.size = QComboBox()
        for k in WM_SIZES:
            self.size.addItem(k.capitalize(), k)
        self.size.setCurrentIndex(max(0, self.size.findData(last.get("size", "medium"))))
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(10, 100)
        self.opacity.setValue(int(last.get("opacity", 60)))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        logo_row = QHBoxLayout()
        logo_row.addWidget(self.logo_label, 1)
        logo_row.addWidget(pick_logo)
        logo_row.addWidget(clear_logo)
        for r, (label, w) in enumerate((("Text", self.text), ("Logo", logo_row), ("Where", self.position),
                                        ("Size", self.size), ("See-through", self.opacity))):
            grid.addWidget(QLabel(label), r, 0)
            (grid.addLayout if isinstance(w, QHBoxLayout) else grid.addWidget)(w, r, 1)
        self.lay.addLayout(grid)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(320, 180)
        self.lay.addWidget(self.preview)
        self.buttons("Add watermark")
        self._base = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._render)
        for sig in (self.text.textChanged, self.position.currentIndexChanged, self.size.currentIndexChanged,
                    self.opacity.valueChanged):
            sig.connect(lambda *_: self.timer.start(120))
        self._update_logo_label()
        QTimer.singleShot(30, self._render)

    def _pick_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a logo", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self.logo = path
            self._update_logo_label()
            self._render()

    def _clear_logo(self):
        self.logo = ""
        self._update_logo_label()
        self._render()

    def _update_logo_label(self):
        self.logo_label.setText(Path(self.logo).name if self.logo else "None")
        has_any = bool(self.text.text().strip() or self.logo)
        if hasattr(self, "ok"):
            self.ok.setEnabled(has_any)

    def _base_image(self):
        if self._base is None:
            from PIL import Image
            src = self.files[0]
            try:
                if category(src) == "video":
                    data = video_frame(src, min(1.0, media_duration(src) / 2), 640)
                    self._base = Image.open(io.BytesIO(data)).convert("RGBA") if data else None
                else:
                    im = load_image(src).convert("RGBA")
                    im.thumbnail((640, 640))
                    self._base = im
            except Exception:
                self._base = None
            if self._base is None:
                self._base = Image.new("RGBA", (640, 360), (120, 120, 130, 255))
        return self._base

    def _render(self):
        from .extras import make_overlay
        self._update_logo_label()
        try:
            base = self._base_image().copy()
            if self.text.text().strip() or self.logo:
                base.alpha_composite(make_overlay(base.width, base.height, self.params()))
            buf = io.BytesIO()
            base.convert("RGB").save(buf, "PNG")
            pm = QPixmap()
            pm.loadFromData(buf.getvalue())
            self.preview.setPixmap(pm.scaled(420, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception:
            self.preview.setText("No preview")

    def params(self):
        return {"text": self.text.text().strip(), "logo": self.logo, "position": self.position.currentData(),
                "size": self.size.currentData(), "opacity": self.opacity.value()}


class FramesDialog(Base):
    def __init__(self, files, icon, dark):
        dur = media_duration(files[0])
        super().__init__("Save pictures from the video", f"{files[0].name} · {fmt_time(dur)} long", icon, dark)
        self.group = QButtonGroup(self)
        self.every = QRadioButton("One picture every")
        self.every.setChecked(True)
        self.secs = QDoubleSpinBox()
        self.secs.setRange(0.1, 3600)
        self.secs.setValue(1)
        self.secs.setSuffix(" seconds")
        self.count_rb = QRadioButton("This many pictures, spread out:")
        self.count = QSpinBox()
        self.count.setRange(1, 1000)
        self.count.setValue(10)
        self.all = QRadioButton("Every single frame (makes a lot of files)")
        for rb in (self.every, self.count_rb, self.all):
            self.group.addButton(rb)
        for rb, w in ((self.every, self.secs), (self.count_rb, self.count)):
            row = QHBoxLayout()
            row.addWidget(rb)
            row.addWidget(w)
            row.addStretch()
            self.lay.addLayout(row)
        self.secs.valueChanged.connect(lambda _: self.every.setChecked(True))
        self.count.valueChanged.connect(lambda _: self.count_rb.setChecked(True))
        self.lay.addWidget(self.all)
        note = QLabel("Saved as PNG pictures in a new folder.")
        note.setObjectName("muted")
        self.lay.addWidget(note)
        self.buttons("Save pictures")

    def params(self):
        if self.all.isChecked():
            return {"mode": "all"}
        if self.count_rb.isChecked():
            return {"mode": "count", "count": self.count.value()}
        return {"mode": "every", "seconds": self.secs.value()}


class SlideshowDialog(Base):
    def __init__(self, files, icon, dark):
        super().__init__("Make a video", f"{len(files)} pictures, in name order", icon, dark)
        row = QHBoxLayout()
        row.addWidget(QLabel("Show each picture for"))
        self.secs = QDoubleSpinBox()
        self.secs.setRange(0.1, 60)
        self.secs.setValue(2)
        self.secs.setSuffix(" seconds")
        row.addWidget(self.secs)
        row.addStretch()
        self.lay.addLayout(row)
        self.total = QLabel()
        self.total.setObjectName("muted")
        self.lay.addWidget(self.total)
        self.secs.valueChanged.connect(lambda v: self.total.setText(f"The video will be {fmt_time(v * len(files))} long."))
        self.total.setText(f"The video will be {fmt_time(2 * len(files))} long.")
        self.buttons("Make video")

    def params(self):
        return {"seconds": self.secs.value()}


SPLITS = [(0, "Don't split"), (25, "25 MB (email)"), (100, "100 MB"), (700, "700 MB (CD)"),
          (4095, "4 GB (USB stick, FAT32)")]


class PackDialog(Base):
    def __init__(self, files, icon, dark):
        size = 0
        for f in files:
            try:
                size += sum(x.stat().st_size for x in f.rglob("*") if x.is_file()) if f.is_dir() else f.stat().st_size
            except OSError:
                pass
        super().__init__("Make an archive", f"{describe(files)} · {human_size(size)}", icon, dark)
        from .archives import find_7z
        has7z = bool(find_7z())
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name (optional)")
        self.format = QComboBox()
        self.format.addItem("ZIP (opens everywhere)", "zip")
        if has7z:
            self.format.addItem("7z (smaller files)", "7z")
        self.level = QComboBox()
        for k, v in (("fast", "Fast"), ("normal", "Normal"), ("max", "Smallest file (slower)")):
            self.level.addItem(v, k)
        self.level.setCurrentIndex(1)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("No password")
        self.password2 = QLineEdit()
        self.password2.setEchoMode(QLineEdit.Password)
        self.password2.setPlaceholderText("Type it again")
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: [w.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
                                         for w in (self.password, self.password2)])
        self.split = QComboBox()
        for mb, text in SPLITS:
            self.split.addItem(text, mb)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        pw_row = QHBoxLayout()
        pw_row.addWidget(self.password, 1)
        pw_row.addWidget(show)
        rows = [("Name", self.name), ("Type", self.format), ("Compression", self.level), ("Password", pw_row),
                ("", self.password2), ("Split into parts", self.split)]
        for r, (label, w) in enumerate(rows):
            if label:
                grid.addWidget(QLabel(label), r, 0)
            (grid.addLayout if isinstance(w, QHBoxLayout) else grid.addWidget)(w, r, 1)
        self.lay.addLayout(grid)
        self.warn = QLabel()
        self.warn.setObjectName("muted")
        self.warn.setWordWrap(True)
        self.lay.addWidget(self.warn)
        if not has7z:
            self.password.setEnabled(False)
            self.password2.setEnabled(False)
            self.split.setEnabled(False)
        self.buttons("Make archive")
        for sig in (self.password.textChanged, self.password2.textChanged, self.format.currentIndexChanged):
            sig.connect(lambda *_: self._check())
        self._check()

    def _check(self):
        p1, p2 = self.password.text(), self.password2.text()
        if p1 and p1 != p2:
            self.warn.setText("The two passwords don't match yet.")
            self.ok.setEnabled(False)
            return
        self.ok.setEnabled(True)
        if p1:
            what = "The file names and contents are hidden" if self.format.currentData() == "7z" else \
                "The contents are protected (AES-256)"
            self.warn.setText(f"{what}. Don't forget the password: nobody can open the archive without it.")
        else:
            self.warn.setText("")

    def params(self):
        return {"format": self.format.currentData(), "level": self.level.currentData(),
                "password": self.password.text(), "split_mb": self.split.currentData() or 0,
                "rename_base": self.name.text().strip()}


class PasswordDialog(Base):
    def __init__(self, files, icon, dark, wrong: bool = False):
        super().__init__("Password needed", f"{files[0].name} is protected with a password.", icon, dark)
        if wrong:
            w = QLabel("That password didn't work. Try again.")
            w.setStyleSheet("color: #e5484d;")
            self.lay.addWidget(w)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Password")
        show = QCheckBox("Show password")
        show.toggled.connect(lambda on: self.password.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        self.lay.addWidget(self.password)
        self.lay.addWidget(show)
        self.buttons("Open")
        self.password.setFocus()

    def params(self):
        return {"password": self.password.text()}


DIALOGS = {"resize": ResizeDialog, "rotate": RotateDialog, "trim": TrimDialog, "fit": FitDialog,
           "watermark": WatermarkDialog, "frames": FramesDialog, "slideshow": SlideshowDialog, "pack": PackDialog,
           "password": PasswordDialog, "rename": RenameDialog}


def ask(kind: str, files: list[Path], icon: QIcon, dark: bool, **extra) -> dict | None:
    """Shows a dialog; returns the user's choices, or None if they cancelled."""
    dlg = DIALOGS[kind](files, icon, dark, **extra)
    return dlg.params() if dlg.exec() else None  # 1 = accepted, 0 = cancelled
