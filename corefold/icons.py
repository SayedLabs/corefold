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


def icon(name: str, color: QColor | str, disabled: QColor | str | None = None,
         selected: QColor | str | None = None) -> QIcon:
    """A QIcon (sharp at the usual sizes and on high-DPI screens) for buttons and lists."""
    ic = QIcon()
    for size in (16, 20, 24, 32):
        for dpr in (1.0, 2.0):
            ic.addPixmap(pixmap(name, size, color, dpr), QIcon.Mode.Normal)
            if disabled is not None:
                ic.addPixmap(pixmap(name, size, disabled, dpr), QIcon.Mode.Disabled)
            if selected is not None:
                ic.addPixmap(pixmap(name, size, selected, dpr), QIcon.Mode.Selected)
    return ic


# ---- the product tiles (the Corefold mark in each product's colours, on a near-black tile)
_TILE_BODY = "M20 10H33V31H54V44A10 10 0 0 1 44 54H20A10 10 0 0 1 10 44V20A10 10 0 0 1 20 10Z"
_TILE_FOLD = "M37.5 10.6L53.4 26.5A1.6 1.6 0 0 1 52.3 29.2H38.6A1.6 1.6 0 0 1 37 27.6V11.7A1.6 1.6 0 0 1 37.5 10.6Z"
_TILE_GLYPHS = {  # drawn on the old 4..60 grid, moved into the 10..54 body by _GLYPH_FIT
    "corefold": '<circle cx="31" cy="38" r="8.5"/>',
    # the Converter: one thing in, another out (two arrows turning around each other)
    "converter": '<g fill="none" stroke-width="4.2" stroke-linecap="round" stroke-linejoin="round">'
                 '<path d="M17 36.5H40.5M34.5 30.5 40.5 36.5 34.5 42.5"/>'
                 '<path d="M45 48H21.5M27.5 42 21.5 48 27.5 54"/></g>',
    "player": '<path d="M25 28.5 40 38 25 47.5Z"/>',
    "archive": '<rect x="20" y="28" width="22" height="5" rx="2"/><rect x="20" y="36" width="22" height="5" rx="2"/>'
               '<rect x="20" y="44" width="22" height="5" rx="2"/>',
    "cleaner": '<path d="M31 25c1.2 7.3 5.7 11.8 13 13-7.3 1.2-11.8 5.7-13 13-1.2-7.3-5.7-11.8-13-13 7.3-1.2 '
               '11.8-5.7 13-13Z"/>',
    "viewer": '<path d="M17 50 26.5 37l6 8 4-5L46 50Z"/><circle cx="23" cy="29" r="3.5"/>',
    "cloud": '<path d="M22 50a7.5 7.5 0 0 1-1-14.9A10 10 0 0 1 40.2 33a8.5 8.5 0 0 1 2.3 17Z"/>',
    # Capture: the corners of a picked area, around a lens
    "capture": '<path fill="none" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" '
               'd="M18.5 35V29.5H24M38 29.5H43.5V35M43.5 45V50.5H38M24 50.5H18.5V45"/><circle cx="31" cy="40" r="5"/>',
    # Space: a disk shown as a pie, one slice pulled out
    "space": '<path d="M29 32.5a10.5 10.5 0 1 0 10.5 10.5H29Z"/><path d="M31.5 30.5a10.5 10.5 0 0 1 10.5 10.5H31.5Z"/>',
    # Clipboard: a board with its clip
    "clipboard": '<rect x="21" y="32" width="20" height="21" rx="3.5" fill="none" stroke-width="4"/>'
                 '<rect x="25.5" y="29" width="11" height="6.5" rx="2.2"/>'
                 '<path fill="none" stroke-width="3.4" stroke-linecap="round" d="M26.5 42.5H35.5M26.5 47.5H32"/>',
}
_GLYPH_FIT = 'transform="translate(6.857 6.857) scale(0.7857)"'
PRODUCT_COLORS = {  # (light end, deep end) of each product's gradient
    "corefold": ("#6be6d2", "#0f8f7c"), "converter": ("#b4f08c", "#2f9a52"), "player": ("#ff9cb8", "#c22f5c"), "archive": ("#ffd27a", "#a86c00"),
    "cleaner": ("#a6c1ff", "#2b5fd4"), "viewer": ("#c9bcff", "#6b4bd6"), "cloud": ("#9fe3ff", "#1b86c4"),
    "capture": ("#ffb08f", "#d4502c"), "space": ("#f2a8ff", "#a23cc0"), "clipboard": ("#d9e2ef", "#5f6f88"),
}


def product_tile(product: str, size: int, dark: bool = True, dpr: float = 1.0) -> QPixmap:
    """The app-icon look: the mark in a gradient on a near-black tile (the same in light and dark)."""
    light, deep = PRODUCT_COLORS.get(product, PRODUCT_COLORS["corefold"])
    svg_text = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"><defs>'
        f'<linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{light}"/>'
        f'<stop offset="1" stop-color="{deep}"/></linearGradient>'
        f'<radialGradient id="glow" cx="50%" cy="40%" r="60%"><stop offset="0" stop-color="{light}" '
        f'stop-opacity=".28"/><stop offset="1" stop-color="{light}" stop-opacity="0"/></radialGradient></defs>'
        f'<rect x="2.5" y="2.5" width="59" height="59" rx="15" fill="#0b0b0c" stroke="#26282c"/>'
        f'<rect x="2.5" y="2.5" width="59" height="59" rx="15" fill="url(#glow)"/>'
        f'<path d="{_TILE_BODY}" fill="url(#g)"/><path d="{_TILE_FOLD}" fill="{light}" opacity=".62"/>'
        f'<g fill="#0b0b0c" stroke="#0b0b0c" stroke-width="0" {_GLYPH_FIT}>'
        f'{_TILE_GLYPHS.get(product, _TILE_GLYPHS["corefold"])}</g></svg>')
    pm = QPixmap(max(1, round(size * dpr)), max(1, round(size * dpr)))
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg_text.encode("ascii"))).render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


ICON_SIZE = QSize(20, 20)
SMALL = QSize(18, 18)
