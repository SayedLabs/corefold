"""Before / after: drag the line to compare the original picture with the new one."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .core import human_size, short_name
from .history import size_of
from .i18n import tr

log = logging.getLogger(__name__)
MAX_SIDE = 1600


class _Loaded(QObject):
    ready = Signal(int, object)  # 0 = before, 1 = after; (raw, w, h, real size) or None


def preview_rgba(path: Path, max_side: int):
    """A small RGBA copy of a picture for showing on screen: (raw bytes, width, height, real size).
    Made smaller first, so a huge photo doesn't need gigabytes of memory just to be shown."""
    from .converters import load_image
    im = load_image(path)
    size = im.size
    if im.mode not in ("RGB", "RGBA", "L", "LA"):
        im = im.convert("RGBA")
    im.thumbnail((max_side, max_side))
    im = im.convert("RGBA")
    return im.tobytes("raw", "RGBA"), im.width, im.height, size


def _load(path: Path):
    return preview_rgba(path, MAX_SIDE)


class CompareView(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(520, 360)
        self.setCursor(Qt.SplitHCursor)
        self.images: list[QPixmap | None] = [None, None]
        self.split = 0.5
        self.zoom = False   # True: show the middle at the real size, to see details
        self.center = QPointF(0.5, 0.5)
        self.labels = ("", "")

    def set_image(self, which: int, pm: QPixmap):
        self.images[which] = pm
        self.update()

    def _rect(self, pm: QPixmap) -> QRectF:
        w, h = self.width(), self.height()
        # both pictures fill the same frame, even if one was made smaller, so the quality shows
        scale = min(w / pm.width(), h / pm.height()) * (3 if self.zoom else 1)
        dw, dh = pm.width() * scale, pm.height() * scale
        x = w / 2 - dw * (self.center.x() if self.zoom else 0.5)
        y = h / 2 - dh * (self.center.y() if self.zoom else 0.5)
        if self.zoom:
            x = min(0, max(w - dw, x)) if dw > w else (w - dw) / 2
            y = min(0, max(h - dh, y)) if dh > h else (h - dh) / 2
        return QRectF(x, y, dw, dh)

    def mousePressEvent(self, e):
        self._move(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons():
            self._move(e.position())

    def _move(self, p: QPointF):
        self.split = min(max(p.x() / max(self.width(), 1), 0.0), 1.0)
        if self.zoom:
            self.center = QPointF(self.split, min(max(p.y() / max(self.height(), 1), 0.0), 1.0))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # a light checkerboard, so see-through parts are visible
        tile = 12
        for yy in range(0, h, tile):
            for xx in range(0, w, tile):
                p.fillRect(xx, yy, tile, tile, QColor(64, 64, 64) if (xx // tile + yy // tile) % 2 else
                           QColor(56, 56, 56))
        x_split = w * self.split
        for which in (0, 1):
            pm = self.images[which]
            if pm is None:
                continue
            p.save()
            clip = QPainterPath()
            clip.addRect(QRectF(0, 0, x_split, h) if which == 0 else QRectF(x_split, 0, w - x_split, h))
            p.setClipPath(clip)
            p.drawPixmap(self._rect(pm), pm, QRectF(pm.rect()))
            p.restore()
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawLine(QPointF(x_split, 0), QPointF(x_split, h))
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(QPointF(x_split, h / 2), 10, 10)
        p.setPen(QColor(40, 40, 40))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(QRectF(x_split - 10, h / 2 - 10, 20, 20), Qt.AlignCenter, "↔")
        f = QFont("Segoe UI", 9)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        for which, text in enumerate(self.labels):
            if not text:
                continue
            tw = p.fontMetrics().horizontalAdvance(text) + 16
            r = QRectF(10 if which == 0 else w - tw - 10, 10, tw, 24)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(r, 8, 8)
            p.setPen(QColor(255, 255, 255))
            p.drawText(r, Qt.AlignCenter, text)
        p.end()


class CompareWindow(QDialog):
    def __init__(self, before: Path, after: Path, icon: QIcon):
        super().__init__(None, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(tr("Compare") + " · " + short_name(after.name, 50))
        self.setWindowIcon(icon)
        self.resize(900, 640)
        self.view = CompareView()
        self.info = QLabel(tr("Loading…"))
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        zoom = QCheckBox(tr("Zoom in"))
        zoom.toggled.connect(self._zoom)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.close)
        row = QHBoxLayout()
        row.addWidget(self.info, 1)
        row.addWidget(zoom)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view, 1)
        lay.addLayout(row)
        self.paths = (before, after)
        self.sizes = [None, None]
        self.loaded = _Loaded(self)
        self.loaded.ready.connect(self._ready)
        loaded = self.loaded

        def work():
            for which, path in enumerate((before, after)):
                try:
                    data = _load(path)
                except Exception:
                    log.debug("couldn't load %s for comparing", path, exc_info=True)
                    data = None
                try:
                    loaded.ready.emit(which, data)
                except RuntimeError:
                    return  # window closed
        threading.Thread(target=work, daemon=True).start()

    def _zoom(self, on: bool):
        self.view.zoom = on
        self.view.update()

    def _ready(self, which: int, data):
        if data is None:
            self.info.setText(tr("This picture couldn't be opened."))
            return
        raw, w, h, size = data
        self.view.set_image(which, QPixmap.fromImage(QImage(raw, w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()))
        self.sizes[which] = size
        if all(self.sizes):
            parts = []
            for path, (pw, ph) in zip(self.paths, self.sizes):
                parts.append(f"{pw} × {ph} · {human_size(size_of(path))}")
            self.view.labels = (tr("Before"), tr("After"))
            self.info.setText(tr("Before: {before}   ·   After: {after}", before=parts[0], after=parts[1]) + "\n"
                              + tr("Drag across the picture to compare."))
            self.view.update()
