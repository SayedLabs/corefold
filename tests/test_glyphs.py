"""Marks Windows' UI font hasn't got (★ ✓ ▶ ...) are drawn as icons: drawing one made Windows look through
every font on the PC first (scripts/glyphcheck.py lists any that come back)."""
from unittest import mock

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QFont, QImage, QPainter

from corefold import wheel


def _label(item, rtl=False):
    img = QImage(160, 60, QImage.Format_ARGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    try:
        with mock.patch.object(wheel, "is_rtl", return_value=rtl):
            wheel._draw_label(p, QPointF(80, 30), item, wheel.Look(labels="text"), QFont(), 16, QColor("black"), False)
    finally:
        p.end()
    xs = [x for x in range(img.width()) for y in range(img.height()) if img.pixelColor(x, y).lightness() < 128]
    return min(xs), max(xs)


def test_a_favourite_on_the_wheel_gets_a_drawn_star_on_its_leading_side(qapp):
    plain = _label(wheel.Item("PNG"))
    starred = _label(wheel.Item("PNG", favorite=True))
    assert starred[0] < plain[0] - 4 and starred[1] > plain[1]  # the star leads, and the name moves over for it
    rtl = _label(wheel.Item("PNG", favorite=True), rtl=True)
    assert rtl[1] > plain[1] + 4 and rtl[0] < plain[0]  # in Arabic it leads from the right

