"""More small windows: crop, sticker, speed, PDF password and renaming files."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QRadioButton, QSpinBox, QStackedWidget, QWidget)

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
            text = tr("{speed}× slower", speed=f"{1 / s:g}") if s < 1 else tr("{speed}× faster", speed=f"{s:g}")
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
        self.mode = QComboBox()
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
        self.ratio = QComboBox()
        for key, (text, value) in CROP_RATIOS.items():
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


def register(dialogs: dict):
    from .pdfpages import PagesDialog
    dialogs.update({"pages": PagesDialog, "speed": SpeedDialog, "sticker": StickerDialog, "lock": LockDialog,
                    "rename_files": RenameFilesDialog, "crop": CropDialog})
