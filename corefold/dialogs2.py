"""More small windows: crop, sticker, speed, PDF password and renaming files."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QRadioButton, QSpinBox, QStackedWidget, QWidget)

from .atomic.widgets import Dropdown
from .core import short_name
from .dialogs import Base, describe
from .i18n import tr
from .moretools import CROP_RATIOS, SPEEDS, STICKERS_IMAGE, STICKERS_VIDEO, new_names, rename_conflicts


# ----------------------------------------------------------------------------- speed

class SpeedDialog(Base):
    def __init__(self, files, icon, dark):
        super().__init__(tr("Speed up or slow down"), describe(files), icon, dark)
        self.group = QButtonGroup(self)
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, s in enumerate(SPEEDS):
            text = tr("{speed}× slower", speed=f"{1 / s:.3g}") if s < 1 else tr("{speed}× faster", speed=f"{s:g}")
            rb = QRadioButton(text)
            rb.setProperty("speed", s)
            rb.setChecked(s == 2.0)
            self.group.addButton(rb)
            grid.addWidget(rb, i // 4, i % 4)
        self.lay.addLayout(grid)
        note = QLabel(tr("The sound keeps its normal pitch. Saved as a new file; your original stays as it is."))
        note.setObjectName("muted")
        note.setWordWrap(True)
        self.lay.addWidget(note)
        self.buttons(tr("Change speed"))

    def params(self):
        b = self.group.checkedButton()
        return {"speed": float(b.property("speed")) if b else 2.0}


# ----------------------------------------------------------------------------- stickers

class StickerDialog(Base):
    def __init__(self, files, icon, dark):
        from .converters import category, is_animated
        super().__init__(tr("Make a sticker"), describe(files), icon, dark)
        moving = category(files[0]) == "video" or is_animated(files[0])
        presets = STICKERS_VIDEO if moving else STICKERS_IMAGE
        self.group = QButtonGroup(self)
        for i, (key, text) in enumerate(presets.items()):
            rb = QRadioButton(tr(text))
            rb.setProperty("key", key)
            rb.setChecked(i == 0)
            self.group.addButton(rb)
            self.lay.addWidget(rb)
        note = QLabel(tr("Made to the sizes the apps ask for, so you can add it straight away."))
        note.setObjectName("muted")
        note.setWordWrap(True)
        self.lay.addWidget(note)
        self.buttons(tr("Make sticker"))

    def params(self):
        b = self.group.checkedButton()
        return {"preset": b.property("key") if b else "whatsapp"}


# ----------------------------------------------------------------------------- PDF password

class LockDialog(Base):
    def __init__(self, files, icon, dark):
        super().__init__(tr("Protect with a password"), describe(files), icon, dark)
        self.p1 = QLineEdit()
        self.p1.setEchoMode(QLineEdit.Password)
        self.p1.setPlaceholderText(tr("Password"))
        self.p2 = QLineEdit()
        self.p2.setEchoMode(QLineEdit.Password)
        self.p2.setPlaceholderText(tr("Type it again"))
        show = QCheckBox(tr("Show password"))
        show.toggled.connect(lambda on: [w.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
                                         for w in (self.p1, self.p2)])
        self.warn = QLabel()
        self.warn.setObjectName("muted")
        self.warn.setWordWrap(True)
        for w in (self.p1, self.p2, show, self.warn):
            self.lay.addWidget(w)
        self.buttons(tr("Lock"))
        for w in (self.p1, self.p2):
            w.textChanged.connect(self._check)
        self._check()

    def _check(self, *_):
        a, b = self.p1.text(), self.p2.text()
        ok = bool(a) and a == b
        self.ok.setEnabled(ok)
        if a and b and a != b:
            self.warn.setText(tr("The two passwords don't match yet."))
        elif ok:
            self.warn.setText(tr("Anyone opening the PDF will need this password (AES-256). "
                                 "Don't forget it: it can't be recovered."))
        else:
            self.warn.setText("")

    def params(self):
        return {"new_password": self.p1.text()}


# ----------------------------------------------------------------------------- renaming

class RenameFilesDialog(Base):
    MODES = (("number", "New name with numbers"), ("replace", "Replace text"), ("add", "Add text"))

    def __init__(self, files, icon, dark):
        super().__init__(tr("Rename"), tr("{count} files", count=len(files)) if len(files) != 1
                         else short_name(files[0].name), icon, dark)
        self.files = files
        self.mode = Dropdown()
        for key, text in self.MODES:
            self.mode.addItem(tr(text), key)
        self.lay.addWidget(self.mode)
        self.pages = QStackedWidget()
        # numbers
        p1 = QWidget()
        g1 = QGridLayout(p1)
        g1.setContentsMargins(0, 0, 0, 0)
        self.base = QLineEdit()
        self.base.setPlaceholderText(tr("e.g. vacation"))
        self.start = QSpinBox()
        self.start.setRange(0, 999999)
        self.start.setValue(1)
        g1.addWidget(QLabel(tr("Name")), 0, 0)
        g1.addWidget(self.base, 0, 1)
        g1.addWidget(QLabel(tr("Start at")), 1, 0)
        g1.addWidget(self.start, 1, 1)
        # replace
        p2 = QWidget()
        g2 = QGridLayout(p2)
        g2.setContentsMargins(0, 0, 0, 0)
        self.find = QLineEdit()
        self.repl = QLineEdit()
        g2.addWidget(QLabel(tr("Find")), 0, 0)
        g2.addWidget(self.find, 0, 1)
        g2.addWidget(QLabel(tr("Replace with")), 1, 0)
        g2.addWidget(self.repl, 1, 1)
        # add
        p3 = QWidget()
        g3 = QGridLayout(p3)
        g3.setContentsMargins(0, 0, 0, 0)
        self.before = QLineEdit()
        self.after = QLineEdit()
        g3.addWidget(QLabel(tr("Before the name")), 0, 0)
        g3.addWidget(self.before, 0, 1)
        g3.addWidget(QLabel(tr("After the name")), 1, 0)
        g3.addWidget(self.after, 1, 1)
        for p in (p1, p2, p3):
            self.pages.addWidget(p)
        self.lay.addWidget(self.pages)
        self.preview = QLabel()
        self.preview.setObjectName("muted")
        self.preview.setWordWrap(True)
        self.lay.addWidget(self.preview)
        note = QLabel(tr("This renames the files themselves (no copies). You can undo it from the popup."))
        note.setObjectName("muted")
        note.setWordWrap(True)
        self.lay.addWidget(note)
        self.buttons(tr("Rename"))
        self.mode.currentIndexChanged.connect(self._mode_changed)
        for w in (self.base, self.find, self.repl, self.before, self.after):
            w.textChanged.connect(self._update)
        self.start.valueChanged.connect(self._update)
        self._update()

    def _mode_changed(self, i):
        self.pages.setCurrentIndex(i)
        self._update()

    def params(self) -> dict:
        return {"mode": self.mode.currentData(), "base": self.base.text().strip(), "start": self.start.value(),
                "find": self.find.text(), "replace": self.repl.text(), "before": self.before.text(),
                "after": self.after.text()}

    def _update(self, *_):
        p = self.params()
        empty = (p["mode"] == "number" and not p["base"]) or (p["mode"] == "replace" and not p["find"]) or \
            (p["mode"] == "add" and not (p["before"] or p["after"]))
        if empty:
            self.preview.setText(tr("Type something to see the new names."))
            self.ok.setEnabled(False)
            return
        pairs = new_names(self.files, p)
        lines = [f"{short_name(o.name, 30)}  →  {short_name(n.name, 30)}" for o, n in pairs[:4]]
        if len(pairs) > 4:
            lines.append("…")
        bad = rename_conflicts([(o, n) for o, n in pairs if o != n])
        if bad:
            lines.append(tr("Some new names are already used by other files."))
        self.preview.setText("\n".join(lines))
        self.ok.setEnabled(not bad)


# ----------------------------------------------------------------------------- crop

class _Loaded(QObject):
    ready = Signal(object)


class CropArea(QWidget):
    """The picture with a box you drag to choose what to keep."""
    changed = Signal()
    HANDLE = 12

    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 300)
        self.setMouseTracking(True)
        self.pixmap: QPixmap | None = None
        self.box = [0.0, 0.0, 1.0, 1.0]   # left, top, right, bottom (0 to 1)
        self.ratio: float | None = None
        self.size_px = (1, 1)             # the real picture's size, for the ratio
        self._drag = None                 # ("move" | corner name | "new", start point, start box)

    def set_image(self, pm: QPixmap, size: tuple[int, int]):
        self.pixmap, self.size_px = pm, size
        self.update()

    def image_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        iw, ih = self.size_px
        scale = min(w / max(iw, 1), h / max(ih, 1))
        dw, dh = iw * scale, ih * scale
        return QRectF((w - dw) / 2, (h - dh) / 2, dw, dh)

    def set_ratio(self, ratio: float | None):
        self.ratio = ratio
        if ratio:
            self.box = self._fit_ratio(self.box, ratio)
        self.update()
        self.changed.emit()

    def _fit_ratio(self, box, ratio):
        """The biggest box with this shape, centred on the current one."""
        iw, ih = self.size_px
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        w_px, h_px = (box[2] - box[0]) * iw, (box[3] - box[1]) * ih
        if w_px / max(h_px, 1e-6) > ratio:
            w_px = h_px * ratio
        else:
            h_px = w_px / ratio
        w, h = w_px / iw, h_px / ih
        if w > 1:
            w, h = 1.0, h / w
        if h > 1:
            w, h = w / h, 1.0
        x0 = min(max(0.0, cx - w / 2), 1 - w)
        y0 = min(max(0.0, cy - h / 2), 1 - h)
        return [x0, y0, x0 + w, y0 + h]

    def _to_rel(self, p: QPointF) -> tuple[float, float]:
        r = self.image_rect()
        return ((p.x() - r.x()) / max(r.width(), 1), (p.y() - r.y()) / max(r.height(), 1))

    def _corner_at(self, p: QPointF):
        r = self.image_rect()
        x0, y0, x1, y1 = self.box
        corners = {"tl": (x0, y0), "tr": (x1, y0), "bl": (x0, y1), "br": (x1, y1)}
        for name, (cx, cy) in corners.items():
            px, py = r.x() + cx * r.width(), r.y() + cy * r.height()
            if abs(p.x() - px) <= self.HANDLE and abs(p.y() - py) <= self.HANDLE:
                return name
        rx, ry = self._to_rel(p)
        if x0 <= rx <= x1 and y0 <= ry <= y1:
            return "move"
        return "new"

    def mousePressEvent(self, e):
        p = e.position()
        self._drag = (self._corner_at(p), self._to_rel(p), list(self.box))

    CURSORS = {"tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor,
               "bl": Qt.SizeBDiagCursor, "move": Qt.SizeAllCursor, "new": Qt.CrossCursor}

    def mouseMoveEvent(self, e):
        if not self._drag:
            self.setCursor(self.CURSORS.get(self._corner_at(e.position()), Qt.CrossCursor))
            return
        kind, (sx, sy), start = self._drag
        x, y = self._to_rel(e.position())
        x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
        b = list(start)
        if kind == "move":
            w, h = start[2] - start[0], start[3] - start[1]
            nx = min(max(start[0] + x - sx, 0.0), 1 - w)
            ny = min(max(start[1] + y - sy, 0.0), 1 - h)
            b = [nx, ny, nx + w, ny + h]
        elif kind == "new":
            b = [min(sx, x), min(sy, y), max(sx, x), max(sy, y)]
        else:
            if "l" in kind:
                b[0] = x
            if "r" in kind:
                b[2] = x
            if "t" in kind:
                b[1] = y
            if "b" in kind[1:] or kind in ("bl", "br"):
                b[3] = y
            b = [min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3])]
        if b[2] - b[0] < 0.02 or b[3] - b[1] < 0.02:
            return  # too small to be meant
        if self.ratio:
            b = self._fit_ratio(b, self.ratio)
        self.box = b
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.image_rect()
        if self.pixmap is not None:
            p.drawPixmap(r.toRect(), self.pixmap)
        x0, y0, x1, y1 = self.box
        sel = QRectF(r.x() + x0 * r.width(), r.y() + y0 * r.height(), (x1 - x0) * r.width(), (y1 - y0) * r.height())
        shade = QColor(0, 0, 0, 120)
        p.fillRect(QRectF(r.x(), r.y(), r.width(), sel.y() - r.y()), shade)
        p.fillRect(QRectF(r.x(), sel.bottom(), r.width(), r.bottom() - sel.bottom()), shade)
        p.fillRect(QRectF(r.x(), sel.y(), sel.x() - r.x(), sel.height()), shade)
        p.fillRect(QRectF(sel.right(), sel.y(), r.right() - sel.right(), sel.height()), shade)
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(sel)
        for cx, cy in ((sel.left(), sel.top()), (sel.right(), sel.top()), (sel.left(), sel.bottom()),
                       (sel.right(), sel.bottom())):
            p.fillRect(QRectF(cx - 5, cy - 5, 10, 10), QColor(255, 255, 255))
        p.end()


class CropDialog(Base):
    def __init__(self, files, icon, dark):
        super().__init__(tr("Crop"), describe(files), icon, dark)
        self.area = CropArea()
        self.ratio = Dropdown()
        for key, (text, _value) in CROP_RATIOS.items():
            self.ratio.addItem(tr(text), key)
        self.ratio.currentIndexChanged.connect(lambda _: self.area.set_ratio(CROP_RATIOS[self.ratio.currentData()][1]))
        self.info = QLabel()
        self.info.setObjectName("muted")
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Shape")))
        row.addWidget(self.ratio)
        row.addStretch()
        row.addWidget(self.info)
        self.lay.addLayout(row)
        self.lay.addWidget(self.area, 1)
        if len(files) > 1:
            n = QLabel(tr("The same part is cut from every picture."))
            n.setObjectName("muted")
            self.lay.addWidget(n)
        self.buttons(tr("Crop"))
        self.area.changed.connect(self._info)
        self.loaded = _Loaded(self)
        self.loaded.ready.connect(self._show)
        src, loaded = files[0], self.loaded

        def work():
            data = None
            try:
                from .compare import preview_rgba
                raw, w, h, size = preview_rgba(src, 900)
                data = (raw, (w, h), size)
            except Exception:
                data = None
            try:
                loaded.ready.emit(data)
            except RuntimeError:
                pass
        threading.Thread(target=work, daemon=True).start()
        self._info()

    def _show(self, data):
        if not data:
            self.info.setText(tr("No preview"))
            return
        raw, (w, h), size = data
        img = QImage(raw, w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()
        self.area.set_image(QPixmap.fromImage(img), size)
        self._info()

    def _info(self):
        w, h = self.area.size_px
        x0, y0, x1, y1 = self.area.box
        if w > 1:
            self.info.setText(f"{round((x1 - x0) * w)} × {round((y1 - y0) * h)}")
        # nothing to crop while the box still covers the whole picture
        self.ok.setEnabled(w > 1 and (x0 > 0.001 or y0 > 0.001 or x1 < 0.999 or y1 < 0.999))

    def params(self):
        return {"box": tuple(self.area.box)}


# ----------------------------------------------------------------------------- video: crop, subtitles, sound

class _Choices(Base):
    """A title, a few choices (one is picked), a note and a button."""

    def __init__(self, files, icon, dark, title: str, choices: dict, note: str, ok: str, key: str,
                 translate: bool = True):
        super().__init__(title, describe(files), icon, dark)
        self.key = key
        self.group = QButtonGroup(self)
        for i, (value, text) in enumerate(choices.items()):
            rb = QRadioButton(tr(text) if translate else text)
            rb.setProperty("value", value)
            rb.setChecked(i == 0)
            self.group.addButton(rb)
            self.lay.addWidget(rb)
        if note:
            n = QLabel(note)
            n.setObjectName("muted")
            n.setWordWrap(True)
            self.lay.addWidget(n)
        self.buttons(ok)

    def params(self):
        b = self.group.checkedButton()
        return {self.key: b.property("value") if b else None}


class VideoCropDialog(_Choices):
    def __init__(self, files, icon, dark):
        from .videotools import CROPS
        super().__init__(files, icon, dark, tr("Crop the video"), CROPS,
                         tr("Shapes are cut from the middle of the picture. Saved as a new file; your original "
                            "stays as it is."), tr("Crop"), "how")


class SoundDialog(_Choices):
    def __init__(self, files, icon, dark):
        from .videotools import SOUND_FORMATS
        super().__init__(files, icon, dark, tr("Save the sound"), SOUND_FORMATS,
                         tr("WAV and FLAC keep every detail but are big; MP3 and M4A play everywhere."), tr("Save"),
                         "format", translate=False)


class SubtitlesDialog(Base):
    def __init__(self, files, icon, dark):
        from .videotools import subtitles_next_to
        super().__init__(tr("Put subtitles in the video"), describe(files), icon, dark)
        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText(tr("A subtitle file (.srt, .ass, .vtt)"))
        found = subtitles_next_to(files[0])
        if found is not None:
            self.path.setText(str(found))
        pick = QPushButton(tr("Choose…"))
        pick.clicked.connect(self._pick)
        row.addWidget(self.path, 1)
        row.addWidget(pick)
        self.lay.addLayout(row)
        self.burn = QRadioButton(tr("Draw them into the picture (plays on phones and TVs too)"))
        self.track = QRadioButton(tr("Add them as subtitles you can turn off (MKV, no quality lost, fast)"))
        self.burn.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.burn)
        group.addButton(self.track)
        self.lay.addWidget(self.burn)
        self.lay.addWidget(self.track)
        self.buttons(tr("Put them in"))
        self.ok.setEnabled(bool(self.path.text()))
        self.path.textChanged.connect(lambda t: self.ok.setEnabled(bool(t.strip())))

    def _pick(self):
        from PySide6.QtWidgets import QFileDialog
        name, _ = QFileDialog.getOpenFileName(self, tr("Choose…"), self.path.text(),
                                              tr("Subtitles") + " (*.srt *.ass *.ssa *.vtt)")
        if name:
            self.path.setText(name)

    def params(self):
        return {"subtitles": self.path.text().strip().strip('"'), "how": "burn" if self.burn.isChecked() else "track"}


class PageNumbersDialog(Base):
    def __init__(self, files, icon, dark):
        from .pdfstamp import NUMBER_PLACES
        super().__init__(tr("Number the pages"), describe(files), icon, dark)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self.place = Dropdown()
        for k, v in NUMBER_PLACES.items():
            self.place.addItem(tr(v), k)
        self.style = Dropdown()
        self.style.addItem("1, 2, 3…", "n")
        self.style.addItem("1 / 12, 2 / 12…", "of")
        grid.addWidget(QLabel(tr("Where")), 0, 0)
        grid.addWidget(self.place, 0, 1)
        grid.addWidget(QLabel(tr("Style")), 1, 0)
        grid.addWidget(self.style, 1, 1)
        self.lay.addLayout(grid)
        self.skip = QCheckBox(tr("No number on the first page (a cover)"))
        self.lay.addWidget(self.skip)
        note = QLabel(tr("Saved as a new file; your original stays as it is."))
        note.setObjectName("muted")
        note.setWordWrap(True)
        self.lay.addWidget(note)
        self.buttons(tr("Number them"))

    def params(self):
        return {"place": self.place.currentData(), "style": self.style.currentData(),
                "skip": 1 if self.skip.isChecked() else 0}


class SignaturePad(QWidget):
    """Draw a signature with the mouse, a pen or a finger."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 150)
        self.setCursor(Qt.CrossCursor)
        self.strokes: list[list[QPointF]] = []
        self.picture = QImage()   # a picture of a signature picked instead

    def clear(self):
        self.strokes, self.picture = [], QImage()
        self.update()

    def empty(self) -> bool:
        return not self.strokes and self.picture.isNull()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            if not self.picture.isNull():
                self.picture = QImage()
            self.strokes.append([e.position()])
            self.update()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton and self.strokes:
            self.strokes[-1].append(e.position())
            self.update()

    def mouseReleaseEvent(self, e):
        self.changed()

    def changed(self):
        pass  # (set by the dialog)

    def _draw(self, p: QPainter, scale: float = 1.0):
        pen = QPen(QColor("#1a2a6c"), 2.6 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        for stroke in self.strokes:
            if len(stroke) == 1:
                p.drawPoint(stroke[0] * scale)
            for a, b in zip(stroke, stroke[1:]):
                p.drawLine(a * scale, b * scale)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#ffffff"))
        p.setPen(QPen(QColor("#c9ccd1"), 1, Qt.DashLine))
        p.drawLine(16, self.height() - 34, self.width() - 16, self.height() - 34)
        if not self.picture.isNull():
            pm = QPixmap.fromImage(self.picture).scaled(self.size() * 0.9, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap((self.width() - pm.width()) // 2, (self.height() - pm.height()) // 2, pm)
        self._draw(p)
        p.end()

    def save(self, path) -> bool:
        if not self.picture.isNull():
            return self.picture.save(str(path), "PNG")
        img = QImage(self.size() * 3, QImage.Format.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        self._draw(p, 3.0)
        p.end()
        return img.save(str(path), "PNG")


class SignDialog(Base):
    def __init__(self, files, icon, dark):
        from .pdfstamp import SIGN_PAGES, SIGN_PLACES
        from .settings import CONFIG_DIR
        super().__init__(tr("Sign the PDF"), describe(files), icon, dark)
        self.kept = CONFIG_DIR / "signature.png"
        self.lay.addWidget(QLabel(tr("Draw your signature:")))
        self.pad = SignaturePad()
        self.pad.changed = self._changed
        self.lay.addWidget(self.pad)
        row = QHBoxLayout()
        clear = QPushButton(tr("Clear"))
        clear.clicked.connect(lambda: (self.pad.clear(), self._changed()))
        pick = QPushButton(tr("Use a picture of it…"))
        pick.clicked.connect(self._pick)
        row.addWidget(clear)
        row.addWidget(pick)
        row.addStretch()
        self.lay.addLayout(row)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self.pages = Dropdown()
        for k, v in SIGN_PAGES.items():
            self.pages.addItem(tr(v), k)
        self.place = Dropdown()
        for k, v in SIGN_PLACES.items():
            self.place.addItem(tr(v), k)
        self.size = Dropdown()
        for k in ("small", "medium", "large"):
            self.size.addItem(tr(k.capitalize()), k)
        self.size.setCurrentIndex(1)
        for r, (text, w) in enumerate(((tr("Pages"), self.pages), (tr("Where"), self.place), (tr("Size"), self.size))):
            grid.addWidget(QLabel(text), r, 0)
            grid.addWidget(w, r, 1)
        self.lay.addLayout(grid)
        self.remember = QCheckBox(tr("Remember my signature on this PC"))
        self.lay.addWidget(self.remember)
        if self.kept.exists():
            self.pad.picture = QImage(str(self.kept))
            self.remember.setChecked(True)
        self.buttons(tr("Sign"))
        self._changed()

    def _changed(self):
        self.ok.setEnabled(not self.pad.empty()) if hasattr(self, "ok") else None

    def _pick(self):
        from PySide6.QtWidgets import QFileDialog
        name, _ = QFileDialog.getOpenFileName(self, tr("Use a picture of it…").rstrip("…"), "",
                                              tr("Images") + " (*.png *.jpg *.jpeg *.webp *.bmp)")
        if name:
            img = QImage(name)
            if not img.isNull():
                self.pad.strokes, self.pad.picture = [], img
                self.pad.update()
                self._changed()

    def params(self):
        import tempfile
        from pathlib import Path
        out = Path(tempfile.mkdtemp(prefix="corefold-sign-")) / "signature.png"
        self.pad.save(out)
        try:
            if self.remember.isChecked():
                self.kept.parent.mkdir(parents=True, exist_ok=True)
                self.pad.save(self.kept)
            else:
                self.kept.unlink(missing_ok=True)
        except OSError:
            pass
        return {"signature": str(out), "pages": self.pages.currentData(), "place": self.place.currentData(),
                "size": self.size.currentData()}


def register(dialogs: dict):
    from .pdfpages import PagesDialog
    dialogs.update({"pages": PagesDialog, "speed": SpeedDialog, "sticker": StickerDialog, "lock": LockDialog,
                    "rename_files": RenameFilesDialog, "crop": CropDialog, "video_crop": VideoCropDialog,
                    "sound": SoundDialog, "subtitles": SubtitlesDialog, "page_numbers": PageNumbersDialog,
                    "sign": SignDialog})
