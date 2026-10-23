"""Small windows that ask for a choice before converting: resize, rotate, trim, fit size,
watermark, frames, slideshow, archive options, passwords and new names."""
from __future__ import annotations

import io
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QSlider,
                               QSpinBox, QVBoxLayout)

from .converters import RESIZE_PRESETS, ROTATIONS, category, load_image, media_duration, norm, video_frame
from .core import human_size, quick_size, safe_name, short_name
from .i18n import tr
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

    def buttons(self, ok_text: str = ""):
        ok_text = ok_text or tr("Convert")
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton(tr("Cancel"))
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
    return short_name(files[0].name) if len(files) == 1 else tr("{count} files", count=len(files))


class ResizeDialog(Base):
    def __init__(self, files, icon, dark):
        super().__init__(tr("Resize"), describe(files), icon, dark)
        self.group = QButtonGroup(self)
        for key, (text, _, _) in RESIZE_PRESETS.items():
            rb = QRadioButton(tr(text))
            rb.setProperty("key", key)
            rb.setChecked(key == "50")
            self.group.addButton(rb)
            self.lay.addWidget(rb)
        custom = QHBoxLayout()
        self.custom = QRadioButton(tr("Custom width:"))
        self.custom.setProperty("key", "custom")
        self.group.addButton(self.custom)
        self.width = QSpinBox()
        self.width.setRange(16, 20000)
        self.width.setValue(1200)
        self.width.setSuffix(tr(" px"))
        self.width.valueChanged.connect(lambda _: self.custom.setChecked(True))
        custom.addWidget(self.custom)
        custom.addWidget(self.width)
        custom.addStretch()
        self.lay.addLayout(custom)
        note = QLabel(tr("The height changes to match, so nothing gets stretched. Your original stays as it is."))
        note.setObjectName("muted")
        note.setWordWrap(True)
        self.lay.addWidget(note)
        self.buttons(tr("Resize"))

    def params(self):
        key = self.group.checkedButton().property("key")
        return {"preset": key, "width": self.width.value()}


class RotateDialog(Base):
    def __init__(self, files, icon, dark):
        super().__init__(tr("Rotate or flip"), describe(files), icon, dark)
        self.choice = None
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, (key, (text, _)) in enumerate(ROTATIONS.items()):
            b = QPushButton(tr(text))
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(40)
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            grid.addWidget(b, i // 2, i % 2)
        self.lay.addLayout(grid)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton(tr("Cancel"))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.lay.addLayout(row)

    def _pick(self, key):
        self.choice = key
        self.accept()

    def params(self):
        return {"how": self.choice or "right"}


def fmt_time(t: float) -> str:
    m, s = divmod(round(max(0.0, t), 1), 60)  # rounded first, so 1:59.97 shows as 2:00.0, not 1:60.0
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}"


class _Frames(QObject):
    ready = Signal(int, int, object)  # which end, request number, PNG bytes


class TrimDialog(Base):
    STEPS = 1000

    def __init__(self, files, icon, dark):
        src = files[0]
        super().__init__(tr("Trim"), short_name(src.name), icon, dark)
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
            n.setSuffix(tr(" s"))
        self.end_n.setValue(self.duration)

        self.thumbs = [QLabel(), QLabel()]
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        for col, (title, slider, num) in enumerate(((tr("Start"), self.start_s, self.start_n),
                                                    (tr("End"), self.end_s, self.end_n))):
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
        note = QLabel(tr("Saved as a new MP4 file. Your original stays as it is.") if self.video
                      else tr("Saved as a new file. Your original stays as it is."))
        note.setObjectName("muted")
        self.lay.addWidget(note)
        self.buttons(tr("Trim"))

        self._syncing = False
        self.start_s.valueChanged.connect(lambda v: self._from_slider(0, v))
        self.end_s.valueChanged.connect(lambda v: self._from_slider(1, v))
        self.start_n.valueChanged.connect(lambda v: self._from_number(0, v))
        self.end_n.valueChanged.connect(lambda v: self._from_number(1, v))
        self.wanted = [0, 0]
        self.frames = _Frames(self)
        self.frames.ready.connect(self._show_thumb)
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
        self.length.setText(tr("Keeps {start} → {end}  ·  {length} long (was {was})", start=fmt_time(start),
                               end=fmt_time(end), length=fmt_time(end - start), was=fmt_time(self.duration)))

    def _thumb(self, which):
        """Reads the frame in the background, so dragging the sliders stays smooth."""
        t = self._values()[which]
        if which == 1:
            t = max(0.0, t - 0.05)
        self.wanted[which] += 1
        ticket, src, frames = self.wanted[which], self.src, self.frames

        def work():
            data = video_frame(src, t)
            try:
                frames.ready.emit(which, ticket, data)
            except RuntimeError:
                pass  # the window was closed meanwhile
        threading.Thread(target=work, daemon=True).start()

    def _show_thumb(self, which, ticket, data):
        if ticket != self.wanted[which]:
            return  # an older frame; a newer one is on its way
        pm = QPixmap()
        if data and pm.loadFromData(data):
            self.thumbs[which].setPixmap(pm.scaled(240, 135, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.thumbs[which].setPixmap(QPixmap())
            self.thumbs[which].setText(tr("No preview"))

    def params(self):
        start, end = self._values()
        return {"start": start, "end": end}


class RenameDialog(Base):
    def __init__(self, files, icon, dark, ext: str = "", single: bool = False):
        super().__init__(tr("Name the new file") if single else tr("Name the new files"),
                         describe(files), icon, dark)
        self.files = files
        self.ext = ext  # the format being made (".png"), or "" to keep each file's own
        self.single = single  # all the files go into one new file (a PDF, a ZIP…): no numbers
        self.base = QLineEdit()
        self.base.setPlaceholderText(tr("e.g. vacation"))
        self.start = QSpinBox()
        self.start.setRange(0, 99999)
        self.start.setValue(1)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Name")))
        row.addWidget(self.base, 1)
        row.addSpacing(10)
        start_label = QLabel(tr("Start at"))
        row.addWidget(start_label)
        row.addWidget(self.start)
        start_label.setVisible(not single)
        self.start.setVisible(not single)
        self.lay.addLayout(row)
        self.preview = QLabel()
        self.preview.setObjectName("muted")
        self.preview.setWordWrap(True)
        self.lay.addWidget(self.preview)
        self.base.textChanged.connect(self._update)
        self.start.valueChanged.connect(self._update)
        self.buttons(tr("Convert"))
        self.keep = QPushButton(tr("Keep original names"))
        self.keep.setCursor(Qt.PointingHandCursor)
        self.keep.clicked.connect(self._keep)
        self.lay.itemAt(self.lay.count() - 1).layout().insertWidget(0, self.keep)
        self.keep_names = False
        self._update()

    def _keep(self):
        self.keep_names = True
        self.accept()

    def _update(self):
        base = safe_name(self.base.text())  # what the file will really be called (no \ / : * ? " < > |)
        if not base:
            self.preview.setText(tr("Type a name, or keep the original names."))
            self.ok.setEnabled(False)
            return
        self.ok.setEnabled(True)
        n = self.start.value()
        ext = self.ext or norm(self.files[0].suffix)
        if self.single:
            self.preview.setText(tr("New file: {name}", name=base + ext))
            return
        names = [f"{base}-{n + i}{ext}" for i in range(min(3, len(self.files)))]
        more = "…" if len(self.files) > 3 else ""
        self.preview.setText(tr("New files: {names}", names=", ".join(names) + more))

    def params(self):
        if self.keep_names:
            return {}
        return {"rename_base": self.base.text().strip(), "rename_start": self.start.value()}


class FitDialog(Base):
    def __init__(self, files, icon, dark, last_mb: float = 10):
        from .extras import FIT_PRESETS
        size = sum(quick_size(f, 0.1)[0] for f in files)
        super().__init__(tr("Make it fit"), tr("{what} · {size} now", what=describe(files), size=human_size(size)),
                         icon, dark)
        self.group = QButtonGroup(self)
        for mb, text in FIT_PRESETS:
            rb = QRadioButton(tr("Under {size} MB ({use})", size=f"{mb:g}", use=tr(text)) if text
                              else tr("Under {size} MB", size=f"{mb:g}"))
            rb.setProperty("mb", float(mb))
            rb.setChecked(abs(mb - last_mb) < 1e-6)
            self.group.addButton(rb)
            self.lay.addWidget(rb)
        row = QHBoxLayout()
        self.custom = QRadioButton(tr("Under"))
        self.custom.setProperty("mb", -1.0)
        self.group.addButton(self.custom)
        self.mb = QDoubleSpinBox()
        self.mb.setRange(0.1, 4000)
        self.mb.setDecimals(1)
        self.mb.setSuffix(tr(" MB"))
        self.mb.setValue(last_mb if last_mb not in [m for m, _ in FIT_PRESETS] else 15)
        self.mb.valueChanged.connect(lambda _: self.custom.setChecked(True))
        row.addWidget(self.custom)
        row.addWidget(self.mb)
        row.addStretch()
        self.lay.addLayout(row)
        if not self.group.checkedButton():
            self.custom.setChecked(True)
        note = QLabel(tr("Corefold picks the best quality that still fits. Videos keep their sound; very long "
                         "videos may need a bigger size."))
        note.setObjectName("muted")
        note.setWordWrap(True)
        self.lay.addWidget(note)
        self.buttons(tr("Make it fit"))

    def params(self):
        b = self.group.checkedButton()
        mb = b.property("mb") if b else -1.0
        return {"mb": self.mb.value() if mb is None or mb < 0 else float(mb)}


def _preview_base(src: Path):
    """A small copy of the picture (or a frame of the video) to show the watermark on."""
    from PIL import Image
    base = None
    try:
        if category(src) == "video":
            data = video_frame(src, min(1.0, media_duration(src) / 2), 640)
            base = Image.open(io.BytesIO(data)).convert("RGBA") if data else None
        else:
            base = load_image(src).convert("RGBA")
            base.thumbnail((640, 640))
    except Exception:
        base = None
    return base or Image.new("RGBA", (640, 360), (120, 120, 130, 255))


class WatermarkDialog(Base):
    def __init__(self, files, icon, dark, last: dict | None = None):
        from .extras import POSITIONS, WM_SIZES
        super().__init__(tr("Watermark"), describe(files), icon, dark)
        last = last or {}
        self.files = files
        self.text = QLineEdit(last.get("text", ""))
        self.text.setPlaceholderText(tr("e.g. © Your Name"))
        self.logo = last.get("logo", "") if Path(last.get("logo", "") or "-").exists() else ""
        self.logo_label = QLabel()
        self.logo_label.setObjectName("muted")
        pick_logo = QPushButton(tr("Choose logo…"))
        pick_logo.clicked.connect(self._pick_logo)
        clear_logo = QPushButton(tr("No logo"))
        clear_logo.clicked.connect(self._clear_logo)
        self.position = QComboBox()
        for k, v in POSITIONS.items():
            self.position.addItem(tr(v), k)
        self.position.setCurrentIndex(max(0, self.position.findData(last.get("position", "br"))))
        self.size = QComboBox()
        for k in WM_SIZES:
            self.size.addItem(tr(k.capitalize()), k)
        self.size.setCurrentIndex(max(0, self.size.findData(last.get("size", "medium"))))
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(10, 100)
        self.opacity.setValue(int(last.get("opacity", 60)))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        logo_row = QHBoxLayout()
        logo_row.setSpacing(8)
        logo_row.addWidget(self.logo_label, 1)
        logo_row.addWidget(pick_logo)
        logo_row.addWidget(clear_logo)
        for r, (label, w) in enumerate(((tr("Text"), self.text), (tr("Logo"), logo_row),
                                        (tr("Where"), self.position), (tr("Size"), self.size),
                                        (tr("See-through"), self.opacity))):
            grid.addWidget(QLabel(label), r, 0)
            (grid.addLayout if isinstance(w, QHBoxLayout) else grid.addWidget)(w, r, 1)
        self.lay.addLayout(grid)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(320, 180)
        self.lay.addWidget(self.preview)
        self.buttons(tr("Add watermark"))
        self._base = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._render)
        for sig in (self.text.textChanged, self.position.currentIndexChanged, self.size.currentIndexChanged,
                    self.opacity.valueChanged):
            sig.connect(lambda *_: self.timer.start(120))
        self._update_logo_label()
        # the picture behind the preview is read in the background (a video frame can take a moment)
        self.loaded = _Frames(self)
        self.loaded.ready.connect(lambda *_: self._render())
        self.preview.setText("…")
        files, loaded = self.files, self.loaded

        def work():
            base = _preview_base(files[0])
            self._base = base
            try:
                loaded.ready.emit(0, 0, None)
            except RuntimeError:
                pass  # the window was closed meanwhile
        threading.Thread(target=work, daemon=True).start()

    def _pick_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Choose a logo"), "",
                                              tr("Images") + " (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self.logo = path
            self._update_logo_label()
            self._render()

    def _clear_logo(self):
        self.logo = ""
        self._update_logo_label()
        self._render()

    def _update_logo_label(self):
        self.logo_label.setText(Path(self.logo).name if self.logo else tr("None"))
        has_any = bool(self.text.text().strip() or self.logo)
        if hasattr(self, "ok"):
            self.ok.setEnabled(has_any)

    def _render(self):
        from .extras import make_overlay
        self._update_logo_label()
        if self._base is None:
            return  # still reading the picture; it renders when it's ready
        try:
            base = self._base.copy()
            if self.text.text().strip() or self.logo:
                base.alpha_composite(make_overlay(base.width, base.height, self.params()))
            buf = io.BytesIO()
            base.convert("RGB").save(buf, "PNG")
            pm = QPixmap()
            pm.loadFromData(buf.getvalue())
            self.preview.setPixmap(pm.scaled(420, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception:
            self.preview.setText(tr("No preview"))

    def params(self):
        return {"text": self.text.text().strip(), "logo": self.logo, "position": self.position.currentData(),
                "size": self.size.currentData(), "opacity": self.opacity.value()}


class FramesDialog(Base):
    def __init__(self, files, icon, dark):
        dur = media_duration(files[0])
        super().__init__(tr("Save pictures from the video"),
                         tr("{name} · {length} long", name=short_name(files[0].name), length=fmt_time(dur)), icon,
                         dark)
        self.group = QButtonGroup(self)
        self.every = QRadioButton(tr("One picture every"))
        self.every.setChecked(True)
        self.secs = QDoubleSpinBox()
        self.secs.setRange(0.1, 3600)
        self.secs.setValue(1)
        self.secs.setSuffix(tr(" seconds"))
        self.count_rb = QRadioButton(tr("This many pictures, spread out:"))
        self.count = QSpinBox()
        self.count.setRange(1, 1000)
        self.count.setValue(10)
        self.all = QRadioButton(tr("Every single frame (makes a lot of files)"))
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
        note = QLabel(tr("Saved as PNG pictures in a new folder."))
        note.setObjectName("muted")
        self.lay.addWidget(note)
        self.buttons(tr("Save pictures"))

    def params(self):
        if self.all.isChecked():
            return {"mode": "all"}
        if self.count_rb.isChecked():
            return {"mode": "count", "count": self.count.value()}
        return {"mode": "every", "seconds": self.secs.value()}


class SlideshowDialog(Base):
    def __init__(self, files, icon, dark):
        super().__init__(tr("Make a video"), tr("{count} pictures, in name order", count=len(files)), icon, dark)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Show each picture for")))
        self.secs = QDoubleSpinBox()
        self.secs.setRange(0.1, 60)
        self.secs.setValue(2)
        self.secs.setSuffix(tr(" seconds"))
        row.addWidget(self.secs)
        row.addStretch()
        self.lay.addLayout(row)
        self.total = QLabel()
        self.total.setObjectName("muted")
        self.lay.addWidget(self.total)
        self.secs.valueChanged.connect(
            lambda v: self.total.setText(tr("The video will be {length} long.", length=fmt_time(v * len(files)))))
        self.total.setText(tr("The video will be {length} long.", length=fmt_time(2 * len(files))))
        self.buttons(tr("Make video"))

    def params(self):
        return {"seconds": self.secs.value()}


SPLITS = [(0, "Don't split"), (25, "25 MB (email)"), (100, "100 MB"), (700, "700 MB (CD)"),
          (4095, "4 GB (USB stick, FAT32)")]


class PackDialog(Base):
    def __init__(self, files, icon, dark):
        sizes = [quick_size(f, 0.3 / max(1, len(files))) for f in files]
        size = human_size(sum(b for b, _ in sizes)) + ("+" if not all(done for _, done in sizes) else "")
        super().__init__(tr("Make an archive"), f"{describe(files)} · {size}", icon, dark)
        from .archives import find_7z
        has7z = bool(find_7z())
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("Name (optional)"))
        self.format = QComboBox()
        self.format.addItem(tr("ZIP (opens everywhere)"), "zip")
        if has7z:
            self.format.addItem(tr("7z (smaller files)"), "7z")
        self.level = QComboBox()
        for k, v in (("fast", "Fast"), ("normal", "Normal"), ("max", "Smallest file (slower)")):
            self.level.addItem(tr(v), k)
        self.level.setCurrentIndex(1)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText(tr("No password"))
        self.password2 = QLineEdit()
        self.password2.setEchoMode(QLineEdit.Password)
        self.password2.setPlaceholderText(tr("Type it again"))
        show = QCheckBox(tr("Show"))
        show.toggled.connect(lambda on: [w.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
                                         for w in (self.password, self.password2)])
        self.split = QComboBox()
        for mb, text in SPLITS:
            self.split.addItem(tr(text), mb)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        pw_row = QHBoxLayout()
        pw_row.addWidget(self.password, 1)
        pw_row.addWidget(show)
        rows = [(tr("Name"), self.name), (tr("Type"), self.format), (tr("Compression"), self.level),
                (tr("Password"), pw_row), ("", self.password2), (tr("Split into parts"), self.split)]
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
        self.buttons(tr("Make archive"))
        for sig in (self.password.textChanged, self.password2.textChanged, self.format.currentIndexChanged):
            sig.connect(lambda *_: self._check())
        self._check()

    def _check(self):
        p1, p2 = self.password.text(), self.password2.text()
        if p1 and p1 != p2:
            self.warn.setText(tr("The two passwords don't match yet."))
            self.ok.setEnabled(False)
            return
        self.ok.setEnabled(True)
        if p1:
            what = tr("The file names and contents are hidden.") if self.format.currentData() == "7z" else \
                tr("The contents are protected (AES-256).")
            self.warn.setText(what + " " + tr("Don't forget the password: nobody can open the archive without it."))
        else:
            self.warn.setText("")

    def params(self):
        return {"format": self.format.currentData(), "level": self.level.currentData(),
                "password": self.password.text(), "split_mb": self.split.currentData() or 0,
                "rename_base": self.name.text().strip()}


class PasswordDialog(Base):
    def __init__(self, files, icon, dark, wrong: bool = False):
        super().__init__(tr("Password needed"), tr("{name} is protected with a password.",
                                                   name=short_name(files[0].name)),
                         icon, dark)
        if wrong:
            w = QLabel(tr("That password didn't work. Try again."))
            w.setStyleSheet("color: #e5484d;")
            self.lay.addWidget(w)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText(tr("Password"))
        show = QCheckBox(tr("Show password"))
        show.toggled.connect(lambda on: self.password.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        self.lay.addWidget(self.password)
        self.lay.addWidget(show)
        self.buttons(tr("Open"))
        self.ok.setEnabled(False)
        self.password.textChanged.connect(lambda t: self.ok.setEnabled(bool(t)))
        self.password.setFocus()

    def params(self):
        return {"password": self.password.text()}


DIALOGS = {"resize": ResizeDialog, "rotate": RotateDialog, "trim": TrimDialog, "fit": FitDialog,
           "watermark": WatermarkDialog, "frames": FramesDialog, "slideshow": SlideshowDialog, "pack": PackDialog,
           "password": PasswordDialog, "rename": RenameDialog}


def ask(kind: str, files: list[Path], icon: QIcon, dark: bool, **extra) -> dict | None:
    """Shows a dialog; returns the user's choices, or None if they cancelled."""
    if kind not in DIALOGS:
        from .dialogs2 import register
        register(DIALOGS)
    dlg = DIALOGS[kind](files, icon, dark, **extra)
    try:
        return dlg.params() if dlg.exec() else None  # 1 = accepted, 0 = cancelled
    finally:
        dlg.deleteLater()  # free the window (and any preview pictures) straight away
