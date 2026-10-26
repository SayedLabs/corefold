"""Pictures Pillow can't read by itself, read the same way everywhere in Corefold (converting, the PDF tool,
the viewer): a camera's RAW file (the full-size JPEG every camera keeps inside it for its own screen) and a
drawing (SVG, drawn by Qt's own SVG reader, which is LGPL like the rest of Qt).

register() teaches Pillow to open them, so Image.open() works on them like on any picture. Nothing is
decoded until the picture's pixels are needed: opening one only to know its size stays quick."""
from __future__ import annotations

import io
import os

from .i18n import tr

RAW_EXT = {".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".dng", ".orf", ".rw2", ".raf", ".srw", ".pef",
           ".3fr", ".erf", ".kdc", ".mrw", ".x3f"}
SVG_EXT = {".svg", ".svgz"}
SVG_SIZE = 2400            # px: a drawing's longest side when it says no size (or a silly one)
_registered = False


def raw_jpeg_at(data: bytes) -> int | None:
    """Where the biggest JPEG inside a camera file starts (None if there's none)."""
    from PIL import Image
    best, best_px, start = None, 0, 0
    for _ in range(64):
        i = data.find(b"\xff\xd8\xff", start)
        if i < 0:
            break
        start = i + 3
        try:  # (its start is enough to know its size: no copy of the whole file for each one)
            with Image.open(io.BytesIO(data[i:i + (1 << 20)])) as im:
                px = im.width * im.height if im.format == "JPEG" else 0
        except Exception:
            continue
        if px > best_px:
            best, best_px = i, px
    return best


def raw_turn(data: bytes) -> int:
    """Which way up the camera was held, from the RAW file's own details (1: as it is)."""
    from PIL import Image
    try:
        exif = Image.Exif()
        exif.load(data[:1 << 20])
        return int(exif.get(0x0112, 1) or 1)
    except Exception:
        return 1


def svg_image(data: bytes, longest: int = SVG_SIZE):
    """A drawing as a QImage, on a see-through background: at its own size, or made sharp when it's
    small (an icon) and kept sane when it claims to be huge."""
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    from .safetext import safe_svg
    try:  # (a picture it names outside itself, on a shared folder say, is never opened)
        r = QSvgRenderer(QByteArray(safe_svg(data)))
    except (OSError, ValueError, EOFError) as e:
        raise RuntimeError(tr("this drawing can't be read")) from e
    if not r.isValid():
        raise RuntimeError(tr("this drawing can't be read"))
    size = svg_size(r, longest)
    img = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p)
    p.end()
    return img


def svg_size(renderer, longest: int = SVG_SIZE):
    from PySide6.QtCore import QSize, Qt
    size = renderer.defaultSize()
    if size.isEmpty():
        size = QSize(1000, 1000)
    side = max(size.width(), size.height())
    if side < longest // 2 or side > longest * 2:  # small ones made sharp; huge ones (99999 px) not drawn at that size
        size = size.scaled(longest, longest, Qt.KeepAspectRatio)
    return size


def qimage_to_pil(image):
    from PIL import Image
    from PySide6.QtGui import QImage
    img = image.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    return Image.frombuffer("RGBA", (w, h), bytes(img.constBits())[: img.bytesPerLine() * h], "raw", "RGBA",
                            img.bytesPerLine(), 1).copy()


def _suffix(im) -> str:
    return os.path.splitext(str(getattr(im, "filename", "") or ""))[1].lower()


def register():
    """Teaches Pillow to open camera files and drawings (once). They're asked first, but only take a
    file with their own names (.cr2, .svg…): a camera file starts like a TIFF, and is read as one otherwise."""
    global _registered
    if _registered:
        return
    from PIL import Image, ImageFile

    class CameraFile(ImageFile.ImageFile):
        format = "RAW"
        format_description = "Camera RAW (its full-size picture)"

        def _open(self):
            if _suffix(self) not in RAW_EXT:
                raise SyntaxError("not a camera file")
            data = self.fp.read()
            at = raw_jpeg_at(data)
            if at is None:
                raise SyntaxError("no picture inside")
            inner = Image.open(io.BytesIO(data[at:]))
            self._inner = inner
            self._size = inner.size
            self._mode = inner.mode
            self.tile = []
            turn = raw_turn(data)
            exif = inner.getexif()
            if turn != 1 and exif.get(0x0112, 1) in (0, 1):  # (the turn is in the RAW file's details more often)
                exif[0x0112] = turn
            if len(exif):
                self.info["exif"] = exif.tobytes()
            if "icc_profile" in inner.info:
                self.info["icc_profile"] = inner.info["icc_profile"]

        def load(self):
            if self._im is None and getattr(self, "_inner", None) is not None:
                self._inner.load()
                self.im = self._inner.im
                self._inner.close()
                self._inner = None
            return Image.Image.load(self)

    class Drawing(ImageFile.ImageFile):
        format = "SVG"
        format_description = "Drawing (SVG)"

        def _open(self):
            if _suffix(self) not in SVG_EXT:
                raise SyntaxError("not a drawing")
            self._data = self.fp.read()
            from PySide6.QtCore import QByteArray
            from PySide6.QtSvg import QSvgRenderer

            from .safetext import safe_svg
            try:
                r = QSvgRenderer(QByteArray(safe_svg(self._data)))
            except (OSError, ValueError, EOFError) as e:
                raise SyntaxError("not a drawing that can be read") from e
            if not r.isValid():
                raise SyntaxError("not a drawing that can be read")
            size = svg_size(r)
            self._size = (size.width(), size.height())
            self._mode = "RGBA"
            self.tile = []

        def load(self):
            if self._im is None and getattr(self, "_data", None) is not None:
                self.im = qimage_to_pil(svg_image(self._data)).im
                self._data = None
            return Image.Image.load(self)

    def anything(prefix: bytes) -> bool:
        return True  # (the name decides: see _open)

    for plugin, exts in ((CameraFile, RAW_EXT), (Drawing, SVG_EXT)):
        Image.register_open(plugin.format, plugin, anything)
        Image.register_extensions(plugin.format, sorted(exts))
        Image.ID.remove(plugin.format)
        Image.ID.insert(0, plugin.format)  # (before TIFF, which would read a camera file's small preview)
    _registered = True
