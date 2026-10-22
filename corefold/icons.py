"""Draws the line icons (see icondata.py) in any colour and size: on the wheel, in the sidebar, on
buttons. They follow the theme's text colour, so they look right in light and dark."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .icondata import EVENODD, PATHS

_renderers: dict[tuple[str, str], QSvgRenderer] = {}


def has(name: str) -> bool:
    return name in PATHS


def svg(name: str, color: str) -> bytes:
    rule = ' fill-rule="evenodd" clip-rule="evenodd"' if name in EVENODD else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
            f'<path d="{PATHS[name]}" fill="{color}"{rule}/></svg>').encode("ascii")


def _renderer(name: str, color: QColor) -> QSvgRenderer | None:
    if name not in PATHS:
        return None
    key = (name, color.name())  # transparency is applied when painting, see paint()
    r = _renderers.get(key)
    if r is None:
        if len(_renderers) > 400:  # a theme or colour change makes new ones; don't keep them all
            _renderers.clear()
        r = QSvgRenderer(QByteArray(svg(name, color.name())))
        _renderers[key] = r
    return r


def paint(p: QPainter, rect: QRectF, name: str, color: QColor):
    """Draws the icon centred in rect (as a square that fits), with the colour's transparency."""
    r = _renderer(name, color)
    if r is None:
        return
    side = min(rect.width(), rect.height())
    box = QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side)
    if color.alpha() < 255:
        p.save()
        p.setOpacity(p.opacity() * color.alphaF())
        r.render(p, box)
        p.restore()
    else:
        r.render(p, box)


def pixmap(name: str, size: int, color: QColor | str, dpr: float = 1.0) -> QPixmap:
    color = QColor(color)
    pm = QPixmap(max(1, round(size * dpr)), max(1, round(size * dpr)))
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    paint(p, QRectF(0, 0, size, size), name, color)
    p.end()
    return pm


def icon(name: str, color: QColor | str, disabled: QColor | str | None = None) -> QIcon:
    """A QIcon (sharp at the usual sizes and on high-DPI screens) for buttons and lists."""
    ic = QIcon()
    for size in (16, 20, 24, 32):
        for dpr in (1.0, 2.0):
            ic.addPixmap(pixmap(name, size, color, dpr), QIcon.Mode.Normal)
            if disabled is not None:
                ic.addPixmap(pixmap(name, size, disabled, dpr), QIcon.Mode.Disabled)
    return ic


ICON_SIZE = QSize(20, 20)
SMALL = QSize(18, 18)
