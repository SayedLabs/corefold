"""Text and drawings from other people never make Windows reach out to another computer.

Qt shows a label, a tooltip or an email as rich text (HTML) when it looks like HTML, and rich text can
name pictures by a file path. A path like \\\\some-server\\share\\x.png makes Windows connect to that
computer and sign in to it with this person's Windows account (it can then try to crack the password).
The same goes for a picture inside a drawing (SVG). So:
- guard(): labels show their text as plain text unless the code asks for rich text, and tooltips with
  something that looks like HTML show it as it's written;
- blocked(): what a text view gives back for any picture or style it's asked to load (nothing);
- safe_svg(): a drawing with every picture or link that points outside it taken out."""
from __future__ import annotations

import gzip
import html
import io
import logging
import re
import zlib

log = logging.getLogger("corefold")

_guarded = False
SVG_MAX = 64 << 20   # a drawing bigger than this (unpacked) isn't read


def plain_tip(text):
    """A tooltip that shows text as written, even when it looks like HTML."""
    if isinstance(text, str) and "<" in text and ">" in text:
        return "<p style='white-space:pre'>" + html.escape(text, quote=False) + "</p>"
    return text


def guard():
    """Labels show plain text unless the code asked for rich text (setTextFormat(Qt.RichText)), and
    tooltips never turn text that looks like HTML into HTML. Called once, before any window is made."""
    global _guarded
    if _guarded:
        return
    _guarded = True
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QAction, QStandardItem
    from PySide6.QtWidgets import QLabel, QListWidgetItem, QMessageBox, QTableWidgetItem, QTreeWidgetItem, QWidget

    label_init = QLabel.__init__

    def init(self, *args, **kwargs):
        label_init(self, *args, **kwargs)
        if self.textFormat() == Qt.AutoText:
            self.setTextFormat(Qt.PlainText)
    QLabel.__init__ = init

    # a message box's text too (its label is made inside Qt, past the one above): a file's name in it stays a name
    box_init = QMessageBox.__init__

    def box(self, *args, **kwargs):
        box_init(self, *args, **kwargs)
        if self.textFormat() == Qt.AutoText:
            self.setTextFormat(Qt.PlainText)
    QMessageBox.__init__ = box

    for cls in (QWidget, QAction, QListWidgetItem, QTableWidgetItem, QStandardItem):
        original = cls.setToolTip

        def set_tip(self, text, _original=original):
            _original(self, plain_tip(text))
        cls.setToolTip = set_tip

    tree_tip = QTreeWidgetItem.setToolTip

    def set_tree_tip(self, column, text):
        tree_tip(self, column, plain_tip(text))
    QTreeWidgetItem.setToolTip = set_tree_tip


def blocked(kind):
    """What a text view's loadResource() gives back so that nothing is loaded: an empty picture or an
    empty style. (Giving back None isn't enough: Qt then opens the file itself.)"""
    from PySide6.QtGui import QImage, QTextDocument
    return QImage() if kind == QTextDocument.ImageResource else ""


def raster_picture(data: bytes):
    """A QImage from a picture's bytes, never a drawing (SVG): one can name pictures outside itself."""
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QImage
    img = QImage()
    head = bytes(data[:512]).lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"<") or head[:2] == b"\x1f\x8b" or head[:4] in (b"\xff\xfe<\x00", b"\xfe\xff\x00<"):
        return img
    img.loadFromData(QByteArray(bytes(data)))
    return img


def offline_document():
    """A QTextDocument that loads no picture or style from anywhere (for HTML from other people)."""
    from PySide6.QtGui import QTextDocument

    class OfflineDocument(QTextDocument):
        def loadResource(self, kind, url):
            return blocked(kind)
    return OfflineDocument()


# ----------------------------------------------------------------------------- drawings (SVG)

XLINK = "http://www.w3.org/1999/xlink"
SVG_NS = "http://www.w3.org/2000/svg"
_IMPORT = re.compile(r"@import[^;]*;?", re.I)
_URL = re.compile(r"url\(\s*(['\"]?)(?!\s*#)[^)]*\)", re.I)


def _inside(value: str) -> bool:
    v = value.strip().lower()
    return v.startswith("#") or v.startswith("data:")


def safe_svg(data: bytes) -> bytes:
    """The drawing with every reference to something outside it (a file, a shared folder, a web address)
    taken out, and its XML entities written out. Raises ValueError if it isn't a drawing that can be read
    safely."""
    import xml.etree.ElementTree as ET
    if data[:2] == b"\x1f\x8b":   # .svgz
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
                data = f.read(SVG_MAX + 1)
        except (OSError, EOFError, zlib.error) as e:
            raise ValueError("damaged") from e
    if len(data) > SVG_MAX:
        raise ValueError("too big")
    try:   # (ElementTree never reads an outside DTD or entity, and expat stops entities that blow up)
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ValueError(str(e)) from e
    for el in root.iter():
        for name in list(el.attrib):
            local = name.rsplit("}", 1)[-1].lower()
            value = el.attrib[name]
            if local in ("href", "src") and not _inside(value):
                del el.attrib[name]
            elif local == "style" or (isinstance(value, str) and "url(" in value.lower()):
                el.attrib[name] = _URL.sub("none", _IMPORT.sub("", value))
        tag = el.tag.rsplit("}", 1)[-1].lower() if isinstance(el.tag, str) else ""
        if tag == "style" and el.text:
            el.text = _URL.sub("none", _IMPORT.sub("", el.text))
        if tag in ("script", "foreignobject"):
            el.clear()
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK)
    return ET.tostring(root, encoding="utf-8")
