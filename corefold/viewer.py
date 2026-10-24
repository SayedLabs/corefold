"""Corefold Viewer: a fast window for flipping through the pictures in a folder.

Pictures are read in the background (the next and previous ones too, so the arrow keys feel
instant). Turning and cropping change only what's on screen until you press Save; Save keeps the
photo's details (date taken, camera, colour profile). Deleting sends to the Recycle Bin."""
from __future__ import annotations

import os
import threading
from functools import lru_cache
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QGuiApplication, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut,
                           QTransform)
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem,
                               QMenu, QMessageBox, QPushButton, QSlider, QVBoxLayout, QWidget)

from . import i18n, icons
from .core import ALIASES, APP_NAME, IMAGE_EXT, human_size, norm, short_name
from .i18n import ltr, tr
from .settings import VIEWER_SORTS as SORTS
from .theme import round_menu
from .winutil import open_with, print_file, reveal_in_explorer, set_title_bar_dark

if TYPE_CHECKING:
    from .app import Controller

RAW_EXT = {".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".dng", ".orf", ".rw2", ".raf", ".srw", ".pef",
           ".3fr", ".erf", ".kdc", ".mrw", ".x3f"}   # cameras' own files: their built-in full-size preview is shown
PICTURE_EXT = IMAGE_EXT | {e for e, to in ALIASES.items() if to in IMAGE_EXT} | {".jpe", ".tga", ".psd", ".dib",
                                                                                  ".svg"} | RAW_EXT
CACHE_BYTES = 600 << 20   # decoded pictures kept for quick flipping
QT_READS = {".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".bmp", ".gif", ".webp", ".ico", ".dib"}
ZOOMS = (0.05, 0.1, 0.25, 0.33, 0.5, 0.67, 0.8, 1, 1.25, 1.5, 2, 3, 4, 6, 8, 12, 16)
CROP_RATIOS = {"free": None, "square": (1, 1), "4:3": (4, 3), "3:2": (3, 2), "16:9": (16, 9)}


def is_picture(p: Path) -> bool:
    return p.suffix.lower() in PICTURE_EXT


def natural_key(name: str):
    import re
    return [(0, int(x), "") if x.isdigit() else (1, 0, x) for x in re.split(r"(\d+)", name.lower())]



def pictures_in(folder: Path, sort: str = "name") -> list[Path]:
    """The pictures in a folder: by name the way File Explorer orders them, or by date or size."""
    try:
        found = [e for e in os.scandir(folder) if e.is_file() and is_picture(Path(e.name))]
    except OSError:
        return []
    found.sort(key=lambda e: natural_key(e.name))
    if sort in ("newest", "oldest", "biggest"):
        def stat(e):
            try:
                st = e.stat()
                return st.st_size if sort == "biggest" else st.st_mtime
            except OSError:
                return 0
        found.sort(key=stat, reverse=sort != "oldest")  # (stable: same dates stay in name order)
    return [Path(e.path) for e in found]


def pil_to_qimage(im) -> QImage:
    """A Pillow picture as a QImage (its own copy of the pixels)."""
    has_alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
    im = im.convert("RGBA" if has_alpha else "RGB")
    w, h = im.size
    if has_alpha:
        return QImage(im.tobytes("raw", "RGBA"), w, h, 4 * w, QImage.Format_RGBA8888).copy()
    return QImage(im.tobytes("raw", "RGB"), w, h, 3 * w, QImage.Format_RGB888).copy()


@lru_cache(maxsize=1)
def _qt_reads_tiff() -> bool:
    """Qt's TIFF reader opens a big TIFF several times faster than Pillow (it comes with Qt on Windows)."""
    from PySide6.QtGui import QImageReader
    return b"tiff" in {bytes(f) for f in QImageReader.supportedImageFormats()}


def raw_preview(path: Path):
    """The full-size JPEG a camera keeps inside its RAW file (every camera makes one for its own screen):
    the biggest one found, as a Pillow picture turned the right way up."""
    import io

    from PIL import Image, ImageOps
    data = path.read_bytes()
    best, best_px, start = None, 0, 0
    for _ in range(64):
        i = data.find(b"\xff\xd8\xff", start)
        if i < 0:
            break
        start = i + 3
        try:  # (its start is enough to know its size: no copy of the whole file for each one)
            im = Image.open(io.BytesIO(data[i:i + (1 << 20)]))
            px = im.width * im.height
        except Exception:
            continue
        if im.format == "JPEG" and px > best_px:
            best, best_px = i, px
    if best is None:
        raise RuntimeError(tr("this camera file has no picture Corefold can show"))
    im = Image.open(io.BytesIO(data[best:]))
    im.load()
    try:  # the turn is in the RAW file's own details more often than in the preview's
        with Image.open(path) as raw:
            turn = raw.getexif().get(0x0112, 1)
        if turn and turn != 1 and im.getexif().get(0x0112, 1) in (0, 1):
            exif = im.getexif()
            exif[0x0112] = turn
            im.info["exif"] = exif.tobytes()
    except Exception:
        pass
    try:
        return ImageOps.exif_transpose(im)
    except Exception:
        return im


def _qimage_to_pil(image: QImage):
    from PIL import Image
    img = image.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    return Image.frombuffer("RGBA", (w, h), bytes(img.constBits())[: img.bytesPerLine() * h], "raw", "RGBA",
                            img.bytesPerLine(), 1).copy()


def svg_picture(path: Path, longest: int = 2400) -> QImage:
    """A drawing (SVG) at a good size for the screen, on a see-through background."""
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer

    from .safetext import safe_svg
    try:  # (a picture it names outside itself, on a shared folder say, is never opened)
        r = QSvgRenderer(QByteArray(safe_svg(path.read_bytes())))
    except (OSError, ValueError, EOFError) as e:
        raise RuntimeError(tr("this drawing can't be read")) from e
    if not r.isValid():
        raise RuntimeError(tr("this drawing can't be read"))
    size = r.defaultSize()
    if size.isEmpty():
        size = QSize(1000, 1000)
    side = max(size.width(), size.height())
    if side < longest // 2 or side > longest * 2:  # small ones made sharp; huge ones (99999 px) not drawn at that size
        size = size.scaled(longest, longest, Qt.KeepAspectRatio)
    img = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p)
    p.end()
    return img


def load_picture(path: Path) -> QImage:
    """Reads a picture (Qt's own readers are fastest; Pillow does HEIC, AVIF and the rest).
    Phone photos are turned the right way up."""
    ext = path.suffix.lower()
    if ext == ".svg":
        return svg_picture(path)
    if ext in RAW_EXT:
        return pil_to_qimage(raw_preview(path))
    if ext in QT_READS or ext in (".tif", ".tiff") and _qt_reads_tiff():
        from PySide6.QtGui import QImageReader
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        img = reader.read()
        if not img.isNull():
            return img
    from .converters import load_image
    return pil_to_qimage(load_image(path))


THUMB = 72      # px: the pictures in the strip along the bottom
STRIP_AHEAD = 24  # the strip's small pictures made around the one shown (the rest when scrolled to)


def small_picture(path: Path, size: int = THUMB) -> QImage:
    """A small copy for the strip, read at that size when the format allows it (much faster)."""
    if path.suffix.lower() in QT_READS:
        from PySide6.QtGui import QImageReader
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        full = reader.size()
        if full.isValid() and full.width() > 0 and full.height() > 0:
            reader.setScaledSize(full.scaled(size * 2, size * 2, Qt.KeepAspectRatio))
        img = reader.read()
        if not img.isNull():
            return img
    img = load_picture(path)
    return img.scaled(size * 2, size * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation) if not img.isNull() else img


# the names of the details shown (English, shown through tr())
DETAIL_LABELS = ("Name", "Folder", "Dimensions", "File size", "Format", "Taken", "Camera", "Lens", "Exposure",
                 "Location", "This photo says where it was taken")


def picture_details(path: Path) -> list[tuple[str, str]]:
    """What there is to know about a photo: size, date taken, camera… (English labels, shown through tr())."""
    rows: list[tuple[str, str]] = []
    try:
        st = path.stat()
        rows.append(("File size", human_size(st.st_size)))
    except OSError:
        pass
    try:
        from PIL import ExifTags, Image
        from .converters import setup_pillow
        setup_pillow()
        with Image.open(path) as im:
            rows.insert(0, ("Dimensions", f"{im.width} × {im.height}"))
            rows.append(("Format", im.format or path.suffix[1:].upper()))
            exif = im.getexif()
            sub = exif.get_ifd(0x8769) if exif else {}
            def get(tag):
                return sub.get(tag) or exif.get(tag)
            taken = get(36867) or get(306)
            if taken:
                rows.append(("Taken", str(taken).replace(":", "-", 2)))
            camera = " ".join(str(x).strip() for x in (exif.get(271), exif.get(272)) if x)
            if camera:
                rows.append(("Camera", camera))
            if get(42036):
                rows.append(("Lens", str(get(42036))))
            bits = []
            if get(33434):
                t = float(get(33434))
                bits.append(f"1/{round(1 / t)} s" if 0 < t < 1 else f"{t:g} s")
            if get(33437):
                bits.append(f"f/{float(get(33437)):g}")
            if get(34855):
                bits.append(f"ISO {get(34855)}")
            if get(37386):
                bits.append(f"{float(get(37386)):g} mm")
            if bits:
                rows.append(("Exposure", " · ".join(bits)))
            if exif.get_ifd(0x8825):
                rows.append(("Location", "This photo says where it was taken"))
            del ExifTags
    except Exception:
        pass
    return rows


def apply_edits(im, edits: list):
    """The window's turns (clockwise degrees) and crops (x, y, w, h in the picture as it looks then),
    done to a Pillow picture."""
    from PIL import Image
    turns = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}
    for op, value in edits:
        if op == "rotate" and value % 360:
            im = im.transpose(turns[value % 360])
        elif op == "flip":
            im = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT if value == "h" else Image.Transpose.FLIP_TOP_BOTTOM)
        elif op == "crop":
            x, y, w, h = value
            im = im.crop((max(0, x), max(0, y), min(im.width, x + w), min(im.height, y + h)))
        elif op == "adjust":
            im = _adjusted_pil(im, value)
    return im


def adjust_array(a, light: int = 0, contrast: int = 0, colour: int = 0, auto: bool = False):
    """Light, contrast and colour (each -100 to 100) and "Auto" (each colour stretched to use the full
    range, like Photos' auto-fix) on an RGB or RGBA numpy picture (height, width, channels). The same
    sums for the preview on screen and for the saved file."""
    import numpy as np
    x = np.arange(256, dtype=np.float32)
    rgb = a[..., :3]
    luts = []
    for ch in range(3):
        lut = x.copy()
        if auto:
            hist = np.bincount(rgb[..., ch].ravel(), minlength=256).cumsum()
            total = hist[-1] or 1
            lo, hi = int(np.searchsorted(hist, total * 0.005)), int(np.searchsorted(hist, total * 0.995))
            if hi - lo > 16:
                lut = (lut - lo) * 255 / (hi - lo)
        lut = lut * (1 + light / 200)
        lut = (lut - 128) * (1 + contrast / 100) + 128
        luts.append(np.clip(lut, 0, 255).astype(np.uint8))
    out = a.copy()
    for ch in range(3):
        out[..., ch] = luts[ch][rgb[..., ch]]
    if colour:
        f = out[..., :3].astype(np.float32)
        gray = (f[..., 0] * 0.299 + f[..., 1] * 0.587 + f[..., 2] * 0.114)[..., None]
        out[..., :3] = np.clip(gray + (f - gray) * (1 + colour / 100), 0, 255).astype(np.uint8)
    return out


def _adjusted_pil(im, value):
    import numpy as np
    from PIL import Image
    mode = im.mode
    work = im.convert("RGBA" if "A" in mode or mode == "P" else "RGB")
    out = Image.fromarray(adjust_array(np.asarray(work), *value), work.mode)
    return out


def _adjusted_qimage(image: QImage, value) -> QImage:
    import numpy as np
    img = image.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    a = np.frombuffer(img.constBits(), dtype=np.uint8, count=bpl * h).reshape(h, bpl)[:, :w * 4].reshape(h, w, 4)
    out = np.ascontiguousarray(adjust_array(a, *value))
    return QImage(out.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()


def _flipped(image: QImage, way: str) -> QImage:
    o = Qt.Orientation.Horizontal if way == "h" else Qt.Orientation.Vertical
    try:
        return image.flipped(o)  # (Qt 6.9 and later)
    except AttributeError:
        return image.mirrored(way == "h", way == "v")


class _Loaded(QObject):
    done = Signal(object, object)  # (path, QImage or Exception)
    small = Signal(object, object)  # (path, small QImage) for the strip


class PictureView(QWidget):
    """The picture, zoomed to fit or to any size, moved with the mouse; and the crop frame."""
    zoom_changed = Signal()
    crop_changed = Signal()

    def __init__(self, dark: bool):
        super().__init__()
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.image = QImage()
        self.pixmap = QPixmap()
        self.fit = True
        self.zoom = 1.0
        self.center = QPointF(0, 0)   # the picture point in the middle of the view (when not fitting)
        self.dark = dark
        self.wheel_zooms = True
        self._drag = None
        self.crop: QRectF | None = None   # in picture pixels, while cropping
        self.crop_ratio = None
        self._crop_drag = None
        self.movie = None

    # ---- what's shown

    def set_image(self, image: QImage, keep_view: bool = False):
        self.image = image
        self.pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        if not keep_view:
            self.fit = True
            self.center = QPointF(image.width() / 2, image.height() / 2)
        self.crop = None
        self.update()
        self.zoom_changed.emit()

    def scale(self) -> float:
        if self.image.isNull():
            return 1.0
        if self.fit:
            w, h = max(1, self.width() - 24), max(1, self.height() - 24)
            return min(1.0, w / self.image.width(), h / self.image.height())
        return self.zoom

    def target(self) -> QRectF:
        s = self.scale()
        w, h = self.image.width() * s, self.image.height() * s
        if self.fit:
            return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        return QRectF(self.width() / 2 - self.center.x() * s, self.height() / 2 - self.center.y() * s, w, h)

    def set_zoom(self, z: float, around: QPointF | None = None):
        if self.image.isNull():
            return
        z = max(ZOOMS[0], min(ZOOMS[-1], z))
        around = around or QPointF(self.width() / 2, self.height() / 2)
        before = self.to_image(around)
        self.fit = False
        self.zoom = z
        # keep the point under the mouse where it was
        s = z
        self.center = QPointF(before.x() - (around.x() - self.width() / 2) / s,
                              before.y() - (around.y() - self.height() / 2) / s)
        self._clamp()
        self.update()
        self.zoom_changed.emit()

    def step_zoom(self, up: bool, around: QPointF | None = None):
        now = self.scale()
        options = [z for z in ZOOMS if (z > now * 1.01 if up else z < now * 0.99)]
        if options:
            self.set_zoom(options[0] if up else options[-1], around)

    def fit_view(self):
        self.fit = True
        self.update()
        self.zoom_changed.emit()

    def actual_size(self):
        self.set_zoom(1.0)

    def to_image(self, p: QPointF) -> QPointF:
        r = self.target()
        s = self.scale()
        return QPointF((p.x() - r.x()) / s, (p.y() - r.y()) / s)

    def _clamp(self):
        if self.image.isNull():
            return
        s = self.scale()
        half_w, half_h = self.width() / 2 / s, self.height() / 2 / s
        iw, ih = self.image.width(), self.image.height()
        x = iw / 2 if iw <= 2 * half_w else min(max(self.center.x(), half_w), iw - half_w)
        y = ih / 2 if ih <= 2 * half_h else min(max(self.center.y(), half_h), ih - half_h)
        self.center = QPointF(x, y)

    # ---- drawing

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0b0b0c") if self.dark else QColor("#ececec"))
        if self.pixmap.isNull():
            p.end()
            return
        r = self.target()
        if self.image.hasAlphaChannel():  # a soft checkerboard behind see-through pictures
            p.save()
            p.setClipRect(r)
            size = 10
            light, dark = (QColor("#2a2b2e"), QColor("#202124")) if self.dark else (QColor("#ffffff"), QColor("#e4e4e4"))
            for yy in range(int(r.top()) // size, int(r.bottom()) // size + 1):
                for xx in range(int(r.left()) // size, int(r.right()) // size + 1):
                    p.fillRect(xx * size, yy * size, size, size, light if (xx + yy) % 2 else dark)
            p.restore()
        p.setRenderHint(QPainter.SmoothPixmapTransform, self.scale() < 2)
        p.drawPixmap(r, self.pixmap, QRectF(self.pixmap.rect()))
        if self.crop is not None:
            s = self.scale()
            c = QRectF(r.x() + self.crop.x() * s, r.y() + self.crop.y() * s, self.crop.width() * s,
                       self.crop.height() * s)
            shade = QColor(0, 0, 0, 150)
            p.fillRect(QRectF(r.left(), r.top(), r.width(), c.top() - r.top()), shade)
            p.fillRect(QRectF(r.left(), c.bottom(), r.width(), r.bottom() - c.bottom()), shade)
            p.fillRect(QRectF(r.left(), c.top(), c.left() - r.left(), c.height()), shade)
            p.fillRect(QRectF(c.right(), c.top(), r.right() - c.right(), c.height()), shade)
            p.setPen(QPen(QColor("#ffffff"), 1.5))
            p.drawRect(c)
            p.setPen(QPen(QColor(255, 255, 255, 90), 1))
            for i in (1, 2):  # thirds
                p.drawLine(QPointF(c.left() + c.width() * i / 3, c.top()), QPointF(c.left() + c.width() * i / 3, c.bottom()))
                p.drawLine(QPointF(c.left(), c.top() + c.height() * i / 3), QPointF(c.right(), c.top() + c.height() * i / 3))
            p.setBrush(QColor("#ffffff"))
            p.setPen(Qt.NoPen)
            for pt in (c.topLeft(), c.topRight(), c.bottomLeft(), c.bottomRight()):
                p.drawEllipse(pt, 5, 5)
        p.end()

    def resizeEvent(self, e):
        self._clamp()
        super().resizeEvent(e)
        self.zoom_changed.emit()

    # ---- mouse

    def wheelEvent(self, e):
        if self.wheel_zooms or e.modifiers() & Qt.ControlModifier:
            self.step_zoom(e.angleDelta().y() > 0, e.position())
        else:
            win = self.window()
            if hasattr(win, "go"):
                win.go(-1 if e.angleDelta().y() > 0 else 1)

    def mouseDoubleClickEvent(self, e):
        if self.crop is not None:
            return
        if self.fit and self.scale() < 1:
            self.set_zoom(1.0, e.position())
        else:
            self.fit_view()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self.crop is not None:
            self._crop_drag = (self._crop_handle(e.position()), self.to_image(e.position()), QRectF(self.crop))
            return
        if not self.fit:
            self._drag = (e.position(), QPointF(self.center))
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self.crop is not None:
            if self._crop_drag:
                self._move_crop(e.position())
            else:
                handle = self._crop_handle(e.position())
                self.setCursor({"move": Qt.SizeAllCursor, "": Qt.CrossCursor}.get(handle, Qt.SizeFDiagCursor
                                                                                    if handle in ("tl", "br") else
                                                                                    Qt.SizeBDiagCursor))
            return
        if self._drag:
            start, center = self._drag
            s = self.scale()
            d = e.position() - start
            self.center = QPointF(center.x() - d.x() / s, center.y() - d.y() / s)
            self._clamp()
            self.update()
        else:
            self.setCursor(Qt.OpenHandCursor if not self.fit else Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        self._drag = None
        self._crop_drag = None
        if not self.fit and self.crop is None:
            self.setCursor(Qt.OpenHandCursor)

    # ---- cropping

    def start_crop(self):
        if self.image.isNull():
            return
        self.fit_view()
        w, h = self.image.width(), self.image.height()
        self.crop = QRectF(w * 0.1, h * 0.1, w * 0.8, h * 0.8)
        self.set_crop_ratio(self.crop_ratio)

    def set_crop_ratio(self, ratio):
        self.crop_ratio = ratio
        if self.crop is None:
            return
        if ratio:
            w, h = self.image.width(), self.image.height()
            rw, rh = ratio
            if w * rh > h * rw:  # wider than the ratio: full height
                ch, cw = h * 0.9, h * 0.9 * rw / rh
            else:
                cw, ch = w * 0.9, w * 0.9 * rh / rw
            self.crop = QRectF((w - cw) / 2, (h - ch) / 2, cw, ch)
        self.update()
        self.crop_changed.emit()

    def _crop_handle(self, pos: QPointF) -> str:
        r = self.target()
        s = self.scale()
        c = self.crop
        corners = {"tl": (c.left(), c.top()), "tr": (c.right(), c.top()), "bl": (c.left(), c.bottom()),
                   "br": (c.right(), c.bottom())}
        for name, (x, y) in corners.items():
            if abs(r.x() + x * s - pos.x()) < 12 and abs(r.y() + y * s - pos.y()) < 12:
                return name
        pt = self.to_image(pos)
        return "move" if c.contains(pt) else ""

    def _move_crop(self, pos: QPointF):
        handle, start, rect = self._crop_drag
        pt = self.to_image(pos)
        w, h = self.image.width(), self.image.height()
        dx, dy = pt.x() - start.x(), pt.y() - start.y()
        c = QRectF(rect)
        if handle == "move":
            c.translate(dx, dy)
            c.moveLeft(min(max(0, c.left()), w - c.width()))
            c.moveTop(min(max(0, c.top()), h - c.height()))
        elif handle:
            left, top, right, bottom = c.left(), c.top(), c.right(), c.bottom()
            if "l" in handle:
                left = min(max(0, left + dx), right - 8)
            if "r" in handle:
                right = max(min(w, right + dx), left + 8)
            if "t" in handle:
                top = min(max(0, top + dy), bottom - 8)
            if "b" in handle:
                bottom = max(min(h, bottom + dy), top + 8)
            c = QRectF(left, top, right - left, bottom - top)
            if self.crop_ratio:  # keep the shape: the height follows the width
                rw, rh = self.crop_ratio
                new_h = c.width() * rh / rw
                if "t" in handle:
                    c.setTop(c.bottom() - new_h)
                else:
                    c.setHeight(new_h)
                if c.top() < 0 or c.bottom() > h:
                    return
        else:  # a new frame
            c = QRectF(start, pt).normalized().intersected(QRectF(0, 0, w, h))
        self.crop = c
        self.update()
        self.crop_changed.emit()


class ViewerWindow(QWidget):
    """One window: the picture, a bar of buttons, and the photo's details on the side."""

    def __init__(self, controller: "Controller", path: Path, icon):
        super().__init__()
        self.c = controller
        self.setObjectName("window")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowIcon(icon)
        self.resize(1180, 780)
        s = controller.settings
        self.files: list[Path] = [path]
        self.index = 0
        self.cache: OrderedDict[str, QImage] = OrderedDict()
        self.loading: set[str] = set()
        self.edits: list = []          # turns and crops not saved yet
        self.loader = _Loaded(self)
        self.loader.done.connect(self._loaded)
        self.slideshow = QTimer(self)
        self.slideshow.timeout.connect(lambda: self.go(1, wrap=True))

        self.view = PictureView(controller.theme.dark)
        self.view.wheel_zooms = getattr(s, "viewer_wheel", "zoom") == "zoom"
        self.view.zoom_changed.connect(self._zoom_text)

        # the bar
        self.title = QLabel()
        self.title.setObjectName("h2")
        self.counter = QLabel()
        self.counter.setObjectName("muted")
        self.zoom_label = QPushButton("100%")
        self.zoom_label.setObjectName("small")
        self.zoom_label.setToolTip(tr("Fit to the window / actual size"))
        self.zoom_label.clicked.connect(lambda: self.view.fit_view() if not self.view.fit else self.view.actual_size())
        self.icon_buttons: list[tuple[QPushButton, str]] = []

        def tool(name, tip, fn, text=""):
            b = QPushButton(text)
            b.setObjectName("small")
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setIconSize(QSize(18, 18))
            b.clicked.connect(fn)
            self.icon_buttons.append((b, name))
            return b

        back, ahead = ("chevron_right", "chevron_left") if i18n.is_rtl() else ("chevron_left", "chevron_right")
        self.b_prev = tool(back, tr("Previous (Left arrow)"), lambda: self.go(-1))
        self.b_next = tool(ahead, tr("Next (Right arrow)"), lambda: self.go(1))
        self.b_out = tool("zoom_out", tr("Zoom out (−)"), lambda: self.view.step_zoom(False))
        self.b_in = tool("zoom_in", tr("Zoom in (+)"), lambda: self.view.step_zoom(True))
        self.b_left = tool("arrow_rotate_counterclockwise", tr("Turn left (Shift+R)"), lambda: self.rotate(-90))
        self.b_right = tool("arrow_rotate_clockwise", tr("Turn right (R)"), lambda: self.rotate(90))
        self.b_crop = tool("crop", tr("Crop (C)"), self.toggle_crop)
        self.b_delete = tool("delete", tr("Delete (Del): goes to the Recycle Bin"), self.delete)
        self.b_info = tool("info", tr("Details (I)"), self.toggle_info)
        self.b_show = tool("play", tr("Slideshow (F5)"), self.toggle_slideshow)
        self.b_full = tool("full_screen_maximize", tr("Full screen (F11)"), self.toggle_full)
        self.b_save = QPushButton(tr("Save"))
        self.b_save.setObjectName("primary")
        self.b_save.setCursor(Qt.PointingHandCursor)
        self.b_save.clicked.connect(self.save)
        self.b_more = QPushButton(tr("More"))
        self.b_more.setCursor(Qt.PointingHandCursor)
        more = QMenu(self.b_more)
        round_menu(more)
        more.addAction(tr("Save as…"), self.save_as)
        more.addAction(tr("Copy the picture"), self.copy)
        more.addAction(tr("Show in folder"), lambda: reveal_in_explorer(self.current()))
        more.addAction(tr("Open with…"), lambda: self._with_saved(open_with))
        more.addAction(tr("Print…"), lambda: self._with_saved(print_file))
        more.addAction(tr("More options in the Converter…"), self.to_converter)
        more.addSeparator()
        more.addAction(tr("Rename… (F2)"), self.rename)
        more.addAction(tr("Move to a folder… (M)"), lambda: self.to_folder(move=True))
        more.addAction(tr("Copy to a folder…"), lambda: self.to_folder(move=False))
        more.addAction(tr("Flip left to right (H)"), lambda: self.flip("h"))
        more.addAction(tr("Flip upside down (V)"), lambda: self.flip("v"))
        more.addAction(tr("Light and colour… (A)"), self.toggle_adjust)
        more.addAction(tr("Compare with the next one (X)"), self.toggle_compare)
        more.addAction(tr("Show or hide the small pictures (T)"), self.toggle_strip)
        order = more.addMenu(tr("Sort by"))
        round_menu(order)
        self.sort_actions = {}
        for key, text in SORTS.items():
            a = order.addAction(tr(text), lambda k=key: self.set_sort(k))
            a.setCheckable(True)
            self.sort_actions[key] = a
        more.addSeparator()
        more.addAction(tr("Set as desktop background"), self.set_wallpaper)
        self.b_more.setMenu(more)

        bar = QHBoxLayout()
        bar.setContentsMargins(14, 8, 14, 8)
        bar.setSpacing(6)
        words = QVBoxLayout()
        words.setSpacing(0)
        words.addWidget(self.title)
        words.addWidget(self.counter)
        bar.addWidget(self.b_prev)
        bar.addWidget(self.b_next)
        bar.addSpacing(8)
        bar.addLayout(words, 1)
        for b in (self.b_out, self.zoom_label, self.b_in):
            bar.addWidget(b)
        bar.addSpacing(10)
        for b in (self.b_left, self.b_right, self.b_crop, self.b_delete, self.b_info, self.b_show, self.b_full):
            bar.addWidget(b)
        bar.addSpacing(10)
        bar.addWidget(self.b_save)
        bar.addWidget(self.b_more)
        self.bar = QFrame()
        self.bar.setObjectName("viewerBar")
        self.bar.setLayout(bar)

        # cropping: the shapes and Apply
        self.crop_bar = QFrame()
        self.crop_bar.setObjectName("viewerBar")
        cb = QHBoxLayout(self.crop_bar)
        cb.setContentsMargins(14, 6, 14, 6)
        cb.setSpacing(6)
        cb.addWidget(QLabel(tr("Crop")))
        self.crop_buttons = {}
        for key, text in (("free", tr("Free")), ("square", tr("Square")), ("4:3", "4:3"), ("3:2", "3:2"),
                          ("16:9", "16:9")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(key == "free")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._crop_ratio(k))
            self.crop_buttons[key] = b
            cb.addWidget(b)
        cb.addStretch()
        self.crop_size = QLabel()
        self.crop_size.setObjectName("muted")
        cb.addWidget(self.crop_size)
        cancel = QPushButton(tr("Cancel"))
        cancel.clicked.connect(self.toggle_crop)
        apply = QPushButton(tr("Crop"))
        apply.setObjectName("primary")
        apply.clicked.connect(self.apply_crop)
        cb.addWidget(cancel)
        cb.addWidget(apply)
        self.crop_bar.hide()
        self.view.crop_changed.connect(self._crop_text)

        # light and colour
        self.adjust_bar = QFrame()
        self.adjust_bar.setObjectName("viewerBar")
        ab = QHBoxLayout(self.adjust_bar)
        ab.setContentsMargins(14, 6, 14, 6)
        ab.setSpacing(8)
        self.sliders = {}
        for key, text in (("light", tr("Brightness")), ("contrast", tr("Contrast")), ("colour", tr("Colour"))):
            ab.addWidget(QLabel(text))
            sl = QSlider(Qt.Horizontal)
            sl.setRange(-100, 100)
            sl.setFixedWidth(120)
            sl.valueChanged.connect(lambda _v: self._adjust_timer.start(90))
            sl.setToolTip(text)
            self.sliders[key] = sl
            ab.addWidget(sl)
        self.auto_btn = QPushButton(tr("Auto"))
        self.auto_btn.setCheckable(True)
        self.auto_btn.setCursor(Qt.PointingHandCursor)
        self.auto_btn.setToolTip(tr("Fixes dull or dark photos by itself"))
        self.auto_btn.toggled.connect(lambda _on: self._adjust_timer.start(10))
        ab.addWidget(self.auto_btn)
        ab.addStretch()
        cancel = QPushButton(tr("Cancel"))
        cancel.clicked.connect(lambda: self._end_adjust(keep=False))
        done = QPushButton(tr("Apply"))
        done.setObjectName("primary")
        done.clicked.connect(self.apply_adjust)
        ab.addWidget(cancel)
        ab.addWidget(done)
        self.adjust_bar.hide()
        self._adjust_base: QImage | None = None
        self._adjust_timer = QTimer(self)
        self._adjust_timer.setSingleShot(True)
        self._adjust_timer.timeout.connect(self._adjust_preview)

        # the strip of small pictures along the bottom
        self.strip = QListWidget()
        self.strip.setObjectName("viewerStrip")
        self.strip.setViewMode(QListView.IconMode)
        self.strip.setFlow(QListView.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setMovement(QListView.Static)
        self.strip.setIconSize(QSize(THUMB, THUMB))
        self.strip.setGridSize(QSize(THUMB + 10, THUMB + 10))
        self.strip.setFixedHeight(THUMB + 28)
        self.strip.setHorizontalScrollMode(QListView.ScrollPerPixel)
        self.strip.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.strip.setFocusPolicy(Qt.NoFocus)
        self.strip.itemClicked.connect(lambda it: self.go_to(self.strip.row(it)))
        self.strip.horizontalScrollBar().valueChanged.connect(lambda _v: self._strip_timer.start(120))
        self._strip_timer = QTimer(self)
        self._strip_timer.setSingleShot(True)
        self._strip_timer.timeout.connect(self._strip_visible)
        self._strip_files: list[Path] = []
        self._strip_asked: set[str] = set()
        self.loader.small.connect(self._small_loaded)
        self.strip.setVisible(bool(getattr(s, "viewer_strip", True)))

        # details
        self.info = QFrame()
        self.info.setObjectName("card")
        self.info.setFixedWidth(270)
        self.info_lay = QVBoxLayout(self.info)
        self.info_lay.setContentsMargins(16, 14, 16, 14)
        self.info.setVisible(bool(getattr(s, "viewer_info", False)))

        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(0)
        middle.addWidget(self.view, 1)
        # comparing: the next picture beside this one (X), to keep the better of two alike
        self.view2 = PictureView(controller.theme.dark)
        self.view2.wheel_zooms = self.view.wheel_zooms
        self.view2.hide()
        middle.addWidget(self.view2, 1)
        middle.addWidget(self.info)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.bar)
        lay.addWidget(self.crop_bar)
        lay.addWidget(self.adjust_bar)
        lay.addLayout(middle, 1)
        lay.addWidget(self.strip)

        keys = ((Qt.Key_Right, lambda: self.go(1)), (Qt.Key_Left, lambda: self.go(-1)),
                (Qt.Key_PageDown, lambda: self.go(1)), (Qt.Key_PageUp, lambda: self.go(-1)),
                (Qt.Key_Space, lambda: self.go(1)), (Qt.Key_Backspace, lambda: self.go(-1)),
                (Qt.Key_Home, lambda: self.go_to(0)), (Qt.Key_End, lambda: self.go_to(len(self.files) - 1)),
                (Qt.Key_Plus, lambda: self.view.step_zoom(True)), (Qt.Key_Equal, lambda: self.view.step_zoom(True)),
                (Qt.Key_Minus, lambda: self.view.step_zoom(False)), (Qt.Key_0, self.view.fit_view),
                (Qt.Key_1, self.view.actual_size), (Qt.Key_R, lambda: self.rotate(90)),
                (Qt.Key_C, self.toggle_crop), (Qt.Key_I, self.toggle_info), (Qt.Key_Delete, self.delete),
                (Qt.Key_F5, self.toggle_slideshow), (Qt.Key_F11, self.toggle_full), (Qt.Key_F, self.toggle_full),
                (Qt.Key_F2, self.rename), (Qt.Key_M, lambda: self.to_folder(move=True)),
                (Qt.Key_H, lambda: self.flip("h")), (Qt.Key_V, lambda: self.flip("v")),
                (Qt.Key_T, self.toggle_strip), (Qt.Key_A, self.toggle_adjust), (Qt.Key_X, self.toggle_compare),
                (Qt.Key_Escape, self._escape), (Qt.Key_Return, self._enter), (Qt.Key_Enter, self._enter))
        for key, fn in keys:
            QShortcut(QKeySequence(key), self, fn)
        QShortcut(QKeySequence("Shift+R"), self, lambda: self.rotate(-90))
        QShortcut(QKeySequence.StandardKey.Copy, self, self.copy)
        QShortcut(QKeySequence.StandardKey.Save, self, self.save)

        self.apply_theme(controller.theme)
        self.open(path)

    # ---- files

    def open(self, path: Path):
        """Shows this picture, with the others in its folder a key press away."""
        sort = getattr(self.c.settings, "viewer_sort", "name")
        for k, a in getattr(self, "sort_actions", {}).items():
            a.setChecked(k == sort)
        folder_files = pictures_in(path.parent, sort)
        key = os.path.normcase(str(path))
        self.files = folder_files if any(os.path.normcase(str(f)) == key for f in folder_files) else [path]
        self.index = next((i for i, f in enumerate(self.files) if os.path.normcase(str(f)) == key), 0)
        self.show_current()

    def _with_saved(self, action):
        """Another app gets the picture as it is on disk: turns and crops not saved yet are asked about first."""
        if self.files and self._leave_ok():
            if not action(self.current()):
                self.c.tell(tr("Windows couldn't do that"), short_name(self.current().name))

    def current(self) -> Path:
        return self.files[self.index] if self.files else Path()

    def go(self, step: int, wrap: bool = False):
        if not self.files or not self._leave_ok():
            return
        new = self.index + step
        if wrap or getattr(self.c.settings, "viewer_loop", False):
            new %= len(self.files)
        if 0 <= new < len(self.files):
            self.index = new
            self.show_current()

    def go_to(self, index: int):
        if self.files and 0 <= index < len(self.files) and self._leave_ok():
            self.index = index
            self.show_current()

    def show_current(self):
        path = self.current()
        self.edits = []
        self._end_crop()
        self._end_adjust(keep=True)
        self.title.setText(ltr(short_name(path.name, 60)))
        self.title.setToolTip(str(path))
        self.setWindowTitle(f"{path.name} - {APP_NAME}")
        self.counter.setText(tr("{number} of {total}", number=self.index + 1, total=len(self.files)))
        self._buttons()
        key = str(path)
        if key in self.cache:
            self.cache.move_to_end(key)
            self._show_image(path, self.cache[key])
        else:
            self.view.set_image(QImage())
            self.counter.setText(self.counter.text() + " · " + tr("Opening…"))
            self._load(path)
        for step in (1, -1):  # the next and the previous one, so the arrow keys are instant
            i = self.index + step
            if 0 <= i < len(self.files):
                self._load(self.files[i])
        self._fill_info()
        self._fill_strip()
        self._fill_compare()
        self._movie(path)

    def _movie(self, path: Path):
        """Animated GIFs and WebPs play."""
        if self.view.movie is not None:
            self.view.movie.stop()
            self.view.movie.deleteLater()
            self.view.movie = None
        if path.suffix.lower() not in (".gif", ".webp"):
            return
        from PySide6.QtGui import QMovie
        movie = QMovie(str(path))
        if movie.isValid() and movie.frameCount() != 1:
            movie.frameChanged.connect(lambda _n: self.view.set_image(movie.currentImage(), keep_view=True))
            self.view.movie = movie
            movie.start()

    def _load(self, path: Path):
        key = str(path)
        if key in self.cache or key in self.loading:
            return
        self.loading.add(key)
        loader = self.loader

        def work():
            try:
                result = load_picture(path)
            except Exception as e:
                result = e
            try:
                loader.done.emit(path, result)
            except RuntimeError:  # the window was closed meanwhile
                pass
        threading.Thread(target=work, daemon=True).start()

    def _loaded(self, path: Path, result):
        key = str(path)
        self.loading.discard(key)
        if isinstance(result, QImage) and not result.isNull():
            self.cache[key] = result
            self.cache.move_to_end(key)
            total = sum(img.sizeInBytes() for img in self.cache.values())
            while total > CACHE_BYTES and len(self.cache) > 1:
                _old, img = self.cache.popitem(last=False)
                total -= img.sizeInBytes()
        if self.view2.isVisible() and path == self._compared():
            self.view2.set_image(result if isinstance(result, QImage) else QImage())
        if path == self.current():
            if isinstance(result, QImage) and not result.isNull():
                self._show_image(path, result)
                self.counter.setText(tr("{number} of {total}", number=self.index + 1, total=len(self.files)))
                if self.view2.isVisible():
                    self._fill_compare()
            else:
                self.view.set_image(QImage())
                from .converters import friendly_error
                why = friendly_error(result) if isinstance(result, Exception) else ""
                self.counter.setText(tr("Couldn't open this picture") + (f": {why}" if why else ""))

    def _show_image(self, path: Path, image: QImage):
        self.view.set_image(self._edited(image))

    def _edited(self, image: QImage) -> QImage:
        for op, value in self.edits:
            if op == "rotate":
                image = image.transformed(QTransform().rotate(value), Qt.SmoothTransformation)
            elif op == "flip":
                image = _flipped(image, value)
            elif op == "crop":
                x, y, w, h = value
                image = image.copy(x, y, w, h)
            elif op == "adjust":
                image = _adjusted_qimage(image, value)
        return image

    # ---- changes

    def rotate(self, angle: int):
        if self.view.image.isNull() or self.view.movie is not None:
            return
        self._end_crop()
        self._end_adjust(keep=False)
        self.edits.append(("rotate", angle))
        self.view.set_image(self.view.image.transformed(QTransform().rotate(angle), Qt.SmoothTransformation))
        self._buttons()

    def flip(self, way: str):
        """Mirrors the picture: "h" left to right, "v" upside down (saved with Save, like turning)."""
        if self.view.image.isNull() or self.view.movie is not None:
            return
        self._end_crop()
        self._end_adjust(keep=False)
        self.edits.append(("flip", way))
        self.view.set_image(_flipped(self.view.image, way))
        self._buttons()

    def set_sort(self, key: str):
        self.c.settings.viewer_sort = key
        self.c.settings_changed()
        if self.files:
            self.open(self.current())

    def rename(self):
        """F2: a new name for the picture (the extension stays)."""
        from PySide6.QtWidgets import QInputDialog
        src = self.current()
        if not src.name or not src.exists() or not self._leave_ok():
            return
        name, ok = QInputDialog.getText(self, tr("Rename"), tr("New name:"), text=src.stem)
        name = (name or "").strip().rstrip(".")
        if not ok or not name or name == src.stem:
            return
        from .core import safe_name
        out = src.with_name(safe_name(name) + src.suffix)
        if out.exists() and os.path.normcase(str(out)) != os.path.normcase(str(src)):
            QMessageBox.warning(self, tr("Couldn't rename"), tr("There's already a file called {name}.", name=out.name))
            return
        try:
            src.rename(out)
        except OSError as e:
            QMessageBox.warning(self, tr("Couldn't rename"), str(e))
            return
        image = self.cache.pop(str(src), None)
        if image is not None:
            self.cache[str(out)] = image
        self.files[self.index] = out
        self.show_current()

    def to_folder(self, move: bool):
        """Moves (or copies) the picture to another folder: handy for sorting a pile of photos."""
        import shutil
        src = self.current()
        if not src.name or not src.exists() or not self._leave_ok():
            return
        start = getattr(self, "_last_folder", "") or str(src.parent)
        folder = QFileDialog.getExistingDirectory(self, tr("Move to a folder") if move else tr("Copy to a folder"),
                                                  start)
        if not folder:
            return
        self._last_folder = folder
        from .core import unique_path
        out = unique_path(Path(folder) / src.name)
        try:
            if move:
                shutil.move(str(src), str(out))
            else:
                shutil.copy2(src, out)
        except OSError as e:
            QMessageBox.warning(self, tr("Couldn't move it") if move else tr("Couldn't copy it"), str(e))
            return
        if not move:
            self.c.toast.notify(tr("Copied"), out.name, linger_ms=2000)
            return
        self.cache.pop(str(src), None)
        del self.files[self.index]
        self.c.toast.notify(tr("Moved"), tr("{name} is now in {folder}.", name=out.name, folder=Path(folder).name),
                            linger_ms=2500)
        if not self.files:
            self.close()
            return
        self.index = min(self.index, len(self.files) - 1)
        self.show_current()

    def toggle_crop(self):
        if self.view.crop is not None:
            self._end_crop()
            return
        if self.view.image.isNull() or self.view.movie is not None:
            return
        self._end_adjust(keep=False)
        self.view.start_crop()
        self.crop_bar.show()
        self._crop_ratio("free")
        self._crop_text()

    def _crop_ratio(self, key: str):
        for k, b in self.crop_buttons.items():
            b.setChecked(k == key)
        self.view.set_crop_ratio(CROP_RATIOS[key])

    def _crop_text(self):
        c = self.view.crop
        self.crop_size.setText(f"{round(c.width())} × {round(c.height())}" if c is not None else "")

    def apply_crop(self):
        c = self.view.crop
        if c is None:
            return
        rect = (round(c.x()), round(c.y()), max(1, round(c.width())), max(1, round(c.height())))
        self.edits.append(("crop", rect))
        image = self.view.image.copy(*rect)
        self._end_crop()
        self.view.set_image(image)
        self._buttons()

    # ---- comparing two

    def _compared(self) -> Path | None:
        i = self.index + 1
        return self.files[i] if 0 <= i < len(self.files) else None

    def toggle_compare(self):
        if self.view2.isHidden() and len(self.files) < 2:
            return
        self.view2.setVisible(self.view2.isHidden())
        self._fill_compare()

    def _fill_compare(self):
        if self.view2.isHidden():
            return
        other = self._compared()
        if other is None:  # the last picture: compare it with the one before
            if self.index > 0:
                self.index -= 1
                self.show_current()
            else:
                self.view2.hide()
            return
        key = str(other)
        self.view2.set_image(self.cache[key] if key in self.cache else QImage())
        if key not in self.cache:
            self._load(other)
        self.counter.setText(tr("{number} of {total}", number=self.index + 1, total=len(self.files)) + " · " +
                             tr("beside it: {name}", name=ltr(short_name(other.name, 40))))

    # ---- light and colour

    def _adjust_value(self):
        return (self.sliders["light"].value(), self.sliders["contrast"].value(), self.sliders["colour"].value(),
                self.auto_btn.isChecked())

    def toggle_adjust(self):
        if self._adjust_base is not None:
            self._end_adjust(keep=False)
            return
        if self.view.image.isNull() or self.view.movie is not None:
            return
        self._end_crop()
        self._adjust_base = self.view.image
        for sl in self.sliders.values():
            sl.blockSignals(True)
            sl.setValue(0)
            sl.blockSignals(False)
        self.auto_btn.blockSignals(True)
        self.auto_btn.setChecked(False)
        self.auto_btn.blockSignals(False)
        self.adjust_bar.show()

    def _adjust_preview(self):
        base = self._adjust_base
        if base is None:
            return
        value = self._adjust_value()
        img = base if not any(value) else _adjusted_qimage(base, value)
        self.view.set_image(img, keep_view=True)

    def apply_adjust(self):
        value = self._adjust_value()
        if self._adjust_base is not None and any(value):
            self._adjust_timer.stop()
            self._adjust_preview()
            self.edits.append(("adjust", value))
        self._end_adjust(keep=True)
        self._buttons()

    def _end_adjust(self, keep: bool):
        base, self._adjust_base = self._adjust_base, None
        self._adjust_timer.stop()
        self.adjust_bar.hide()
        if base is not None and not keep:
            self.view.set_image(base, keep_view=True)

    def _end_crop(self):
        self.view.crop = None
        self.crop_bar.hide()
        self.view.setCursor(Qt.ArrowCursor)
        self.view.update()

    def _buttons(self):
        self.b_save.setVisible(bool(self.edits))
        many = len(self.files) > 1
        loop = getattr(self.c.settings, "viewer_loop", False)
        self.b_prev.setEnabled(many and (loop or self.index > 0))
        self.b_next.setEnabled(many and (loop or self.index < len(self.files) - 1))
        self.b_show.setEnabled(many)

    def _leave_ok(self) -> bool:
        """Before moving on from a picture with changes that weren't saved: save them?"""
        if not self.edits:
            return True
        box = QMessageBox(QMessageBox.Question, tr("Save the changes?"),
                          tr("You changed {name}. Save the changes?", name=self.current().name), parent=self)
        save = box.addButton(tr("Save"), QMessageBox.AcceptRole)
        skip = box.addButton(tr("Don't save"), QMessageBox.DestructiveRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        chosen = box.clickedButton()
        box.deleteLater()
        if chosen is save:
            return self.save()
        if chosen is skip:
            self.edits = []
            return True
        return False

    def save(self) -> bool:
        """Writes the turned or cropped picture over the original (its details are kept)."""
        if not self.edits:
            return True
        src = self.current()
        ext = norm(src.suffix)
        if ext not in (".jpg", ".png", ".webp", ".bmp", ".tiff", ".avif"):
            return self.save_as()
        return self._write(src, ext)

    def save_as(self) -> bool:
        src = self.current()
        if not src.name:
            return False
        filters = {".jpg": "JPG (*.jpg)", ".png": "PNG (*.png)", ".webp": "WebP (*.webp)", ".avif": "AVIF (*.avif)",
                   ".bmp": "BMP (*.bmp)", ".tiff": "TIFF (*.tiff)"}
        stem = src.stem + (" (edited)" if self.edits else "")
        guess = norm(src.suffix) if norm(src.suffix) in filters else ".jpg"
        name, chosen = QFileDialog.getSaveFileName(self, tr("Save as"), str(src.with_name(stem + guess)),
                                                   ";;".join(filters.values()), filters[guess])
        if not name:
            return False
        out = Path(name)
        ext = norm(out.suffix) or next((e for e, f in filters.items() if f == chosen), ".jpg")
        if not out.suffix:
            out = out.with_suffix(ext)
        return self._write(out, ext)

    def _write(self, out: Path, ext: str) -> bool:
        from .converters import load_image, save_image
        from .core import Ctx
        src = self.current()
        try:
            ext_in = src.suffix.lower()
            if ext_in in RAW_EXT:
                base = raw_preview(src)
            elif ext_in == ".svg":
                base = _qimage_to_pil(svg_picture(src))
            else:
                base = load_image(src)
            im = apply_edits(base, self.edits)
            tmp = out.with_name(f"{out.stem}.part{out.suffix}")  # (Pillow picks the format from the end of the name)
            ctx = Ctx(self.c.settings.options("", 1))
            ctx.options.jpg_quality = max(ctx.options.jpg_quality, 95)
            save_image(im, tmp, ext, ctx)
            os.replace(tmp, out)
        except Exception as e:
            try:
                tmp.unlink(missing_ok=True)
            except (OSError, UnboundLocalError):
                pass
            QMessageBox.warning(self, tr("Couldn't save"), str(e))
            return False
        self.edits = []
        self.cache.pop(str(out), None)
        if out == src:
            self.cache.pop(str(src), None)
            self.show_current()
        else:
            self.open(out)
        self.c.toast.notify(tr("Saved"), out.name, linger_ms=2500)
        return True

    def delete(self):
        path = self.current()
        if not path.name or not path.exists():
            return
        if getattr(self.c.settings, "viewer_confirm_delete", True):
            box = QMessageBox(QMessageBox.Question, tr("Delete this picture?"),
                              tr("{name} goes to the Recycle Bin, where you can put it back.", name=path.name),
                              parent=self)
            yes = box.addButton(tr("Delete"), QMessageBox.DestructiveRole)
            box.addButton(tr("Cancel"), QMessageBox.RejectRole)
            box.exec()
            chosen = box.clickedButton() is yes
            box.deleteLater()
            if not chosen:
                return
        from .uninstall import recycle
        try:
            ok = recycle(path)
        except Exception:
            ok = False
        if not ok:
            QMessageBox.warning(self, tr("Couldn't delete"), tr("{name} couldn't be moved to the Recycle Bin.",
                                                                name=path.name))
            return
        self.cache.pop(str(path), None)
        self.edits = []
        del self.files[self.index]
        if not self.files:
            self.close()
            return
        self.index = min(self.index, len(self.files) - 1)
        self.show_current()

    def copy(self):
        if not self.view.image.isNull():
            QGuiApplication.clipboard().setImage(self.view.image)
            self.c.toast.notify(tr("Copied"), tr("The picture is on the clipboard."), linger_ms=2000)

    def to_converter(self):
        self.c.show_window("Converter")
        self.c.window.show_files([self.current()])

    def set_wallpaper(self):
        import ctypes
        path = self.current()
        try:
            ok = ctypes.windll.user32.SystemParametersInfoW(20, 0, str(path), 3)  # SPI_SETDESKWALLPAPER
        except Exception:
            ok = False
        if not ok:
            QMessageBox.warning(self, tr("Couldn't change the background"), path.name)

    # ---- view

    # ---- the strip

    def toggle_strip(self):
        if self.isFullScreen():
            return
        self.strip.setVisible(self.strip.isHidden())
        self.c.settings.viewer_strip = not self.strip.isHidden()
        self.c.settings.save()
        self._fill_strip()

    def _fill_strip(self):
        if self.strip.isHidden():
            return
        if self._strip_files != self.files:  # opened, sorted, or a file deleted, renamed or moved
            self._strip_files = list(self.files)
            self._strip_asked.clear()
            self.strip.clear()
            blank = QPixmap(THUMB, THUMB)
            blank.fill(Qt.transparent)
            for f in self.files:
                it = QListWidgetItem(QIcon(blank), "")
                it.setToolTip(f.name)
                it.setSizeHint(QSize(THUMB + 10, THUMB + 10))
                self.strip.addItem(it)
        if 0 <= self.index < self.strip.count():
            self.strip.setCurrentRow(self.index)
            self.strip.scrollToItem(self.strip.item(self.index), QListView.PositionAtCenter)
        lo, hi = max(0, self.index - STRIP_AHEAD), min(len(self.files), self.index + STRIP_AHEAD + 1)
        self._make_small(self.files[lo:hi])

    def _strip_visible(self):
        """Small pictures for the part of the strip scrolled to."""
        if self.strip.isHidden() or not self.strip.count():
            return
        first = self.strip.indexAt(self.strip.viewport().rect().topLeft() + QPoint(4, 4)).row()
        first = max(first, 0)
        self._make_small(self.files[first:first + STRIP_AHEAD])

    def _make_small(self, files: list[Path]):
        wanted = [f for f in files if str(f) not in self._strip_asked]
        if not wanted:
            return
        self._strip_asked.update(str(f) for f in wanted)
        loader = self.loader

        def work():
            for f in wanted:
                try:
                    img = small_picture(f)
                except Exception:
                    continue
                try:
                    loader.small.emit(f, img)
                except RuntimeError:  # the window was closed
                    return
        threading.Thread(target=work, daemon=True, name="corefold-viewer-strip").start()

    def _small_loaded(self, path: Path, img: QImage):
        if img.isNull():
            return
        try:
            row = self._strip_files.index(path)
        except ValueError:
            return
        if row < self.strip.count():
            self.strip.item(row).setIcon(QIcon(QPixmap.fromImage(img)))

    def toggle_info(self):
        self.info.setVisible(not self.info.isVisible())
        self.c.settings.viewer_info = self.info.isVisible()
        self.c.settings.save()
        self._fill_info()

    def _fill_info(self):
        if not self.info.isVisible():
            return
        lay = self.info_lay
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().hide()  # (not drawn again at its old place before it goes)
                item.widget().deleteLater()
        head = QLabel(tr("Details"))
        head.setObjectName("h2")
        lay.addWidget(head)
        path = self.current()
        for name, value in [("Name", path.name), ("Folder", str(path.parent))] + picture_details(path):
            key = QLabel(tr(name))
            key.setObjectName("muted")
            if name == "Folder":  # a long path breaks after a \ (not in the middle of a name, or cut off)
                value = value.replace("\\", "\\\u200b")
            val = QLabel(ltr(tr(value) if name == "Location" else value))
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(key)
            lay.addWidget(val)
        lay.addStretch()

    def toggle_slideshow(self):
        if self.slideshow.isActive():
            self.slideshow.stop()
            return
        if len(self.files) < 2:
            return
        seconds = int(getattr(self.c.settings, "slideshow_seconds", 4) or 4)
        self.slideshow.start(seconds * 1000)
        if not self.isFullScreen():
            self.toggle_full()

    def toggle_full(self):
        if self.isFullScreen():
            self.showNormal()
            self.bar.show()
            self.strip.setVisible(bool(getattr(self.c.settings, "viewer_strip", True)))
            self._fill_strip()
        else:
            self.showFullScreen()
            self.bar.hide()
            self.strip.hide()

    def _escape(self):
        if self.view.crop is not None:
            self._end_crop()
        elif self._adjust_base is not None:
            self._end_adjust(keep=False)
        elif self.view2.isVisible():
            self.toggle_compare()
        elif self.slideshow.isActive() or self.isFullScreen():
            self.slideshow.stop()
            if self.isFullScreen():
                self.toggle_full()
        else:
            self.close()

    def _enter(self):
        if self.view.crop is not None:
            self.apply_crop()
        elif self._adjust_base is not None:
            self.apply_adjust()

    def _zoom_text(self):
        self.zoom_label.setText(f"{round(self.view.scale() * 100)}%")

    def apply_theme(self, theme):
        self.view.dark = self.view2.dark = theme.dark
        for b, name in self.icon_buttons:
            if name:
                b.setIcon(icons.icon(name, theme.text))
        self.view.update()

    def closeEvent(self, e):
        if not self._leave_ok():
            e.ignore()
            return
        self.slideshow.stop()
        if self.view.movie is not None:
            self.view.movie.stop()
        self.cache.clear()
        super().closeEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        set_title_bar_dark(self, self.c.theme.dark)
