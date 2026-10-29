"""Draws the line icons (see icondata.py) in any colour and size: on the wheel, in the sidebar, on
buttons. They follow the theme's text colour, so they look right in light and dark."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .icondata import EVENODD, PATHS, STROKES

_renderers: dict[tuple[str, str], QSvgRenderer] = {}


def forget():
    """Lets go of the drawn icons kept to be quick (once the window has been freed)."""
    from PySide6.QtGui import QPixmapCache
    _renderers.clear()
    QPixmapCache.clear()


def has(name: str) -> bool:
    return name in PATHS or name in STROKES


def svg(name: str, color: str) -> bytes:
    if name in STROKES:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
                f'<g fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
                f'{STROKES[name]}</g></svg>').encode("ascii")
    rule = ' fill-rule="evenodd" clip-rule="evenodd"' if name in EVENODD else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
            f'<path d="{PATHS[name]}" fill="{color}"{rule}/></svg>').encode("ascii")


def _renderer(name: str, color: QColor) -> QSvgRenderer | None:
    if not has(name):
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


_icons: dict[tuple, QIcon] = {}


def _ratios() -> tuple[float, ...]:
    """The screens' sharpness (1 on most, 1.25 to 2 on high-DPI ones): an icon is drawn for those only."""
    from PySide6.QtGui import QGuiApplication
    found = {round(s.devicePixelRatio(), 2) for s in QGuiApplication.screens()} or {1.0}
    return tuple(sorted(found))


def icon(name: str, color: QColor | str, disabled: QColor | str | None = None,
         selected: QColor | str | None = None) -> QIcon:
    """A QIcon (sharp at the usual sizes and on high-DPI screens) for buttons and lists. The same icon in
    the same colours is drawn once and shared (a new look or a page made again asks for many of them)."""
    key = (name, QColor(color).name(QColor.HexArgb),
           None if disabled is None else QColor(disabled).name(QColor.HexArgb),
           None if selected is None else QColor(selected).name(QColor.HexArgb), _ratios())
    ic = _icons.get(key)
    if ic is None:
        ic = QIcon()
        for size in (16, 20, 24, 32):
            for dpr in key[-1]:
                ic.addPixmap(pixmap(name, size, color, dpr), QIcon.Mode.Normal)
                if disabled is not None:
                    ic.addPixmap(pixmap(name, size, disabled, dpr), QIcon.Mode.Disabled)
                if selected is not None:
                    ic.addPixmap(pixmap(name, size, selected, dpr), QIcon.Mode.Selected)
        if len(_icons) > 300:  # (a few looks' worth; older ones are let go)
            _icons.clear()
        _icons[key] = ic
    return QIcon(ic)  # (a copy that shares the pictures: changing it leaves the kept one as it is)


# ---- the product tiles (the Corefold mark in each product's colours, on a near-black tile)
_TILE_BODY = "M20 10H33V31H54V44A10 10 0 0 1 44 54H20A10 10 0 0 1 10 44V20A10 10 0 0 1 20 10Z"
_TILE_FOLD = "M37.5 10.6L53.4 26.5A1.6 1.6 0 0 1 52.3 29.2H38.6A1.6 1.6 0 0 1 37 27.6V11.7A1.6 1.6 0 0 1 37.5 10.6Z"
_TILE_GLYPHS = {  # drawn on the old 4..60 grid, moved into the 10..54 body by _GLYPH_FIT
    "corefold": '<circle cx="31" cy="38" r="8.5"/>',
    # the sections: Files (pages on pages), Images & screen (a picture), Computer (a screen), Quick tools (a bolt)
    "files": '<g fill="none" stroke-width="3.8" stroke-linejoin="round" stroke-linecap="round">'
             '<rect x="19" y="31" width="18" height="22" rx="3"/><path d="M26 26.5h14a3 3 0 0 1 3 3V46"/></g>',
    "media": '<g fill="none" stroke-width="3.8" stroke-linejoin="round" stroke-linecap="round">'
             '<rect x="17.5" y="29" width="28" height="23" rx="3.5"/><path d="M19.5 48.5 27 41l6 6 4-4 7 7"/></g>'
             '<circle cx="37.5" cy="35.5" r="2.9"/>',
    "computer": '<g fill="none" stroke-width="3.8" stroke-linejoin="round" stroke-linecap="round">'
                '<rect x="17" y="28" width="29" height="19.5" rx="3"/><path d="M31.5 47.5v5M25 53.5h13"/></g>',
    "quick": '<path d="M35 25.5 21.5 42.5h9.5l-3 12.5L42 37.5h-9.5l2.5-12z"/>',
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
    # Startup: a bolt (switched on as Windows starts)
    "startup": '<path d="M34 26 21.5 42H30.5L28 54 41 37.5H32Z"/>',
    # Scan: a shield, and the line of a scan passing over it
    "scan": '<path fill="none" stroke-width="3.8" stroke-linejoin="round" '
            'd="M31 26.5 42 31V39c0 7-5 11.5-11 14-6-2.5-11-7-11-14V31Z"/>'
            '<path fill="none" stroke-width="3.8" stroke-linecap="round" d="M15.5 40.5H46.5"/>',
    # PDF: a signature on a line
    "pdf": '<path fill="none" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round" '
           'd="M18 44c3.5-7 7.5-11 9.5-8.5s-2.5 9 .5 9 4.5-6.5 7.5-6.5 1.5 5 4.5 4.5 3-2 4.5-2.5M19 51H43"/>',
    # Rename: a text cursor in a name box
    "rename": '<rect x="17" y="33" width="28" height="15" rx="3.5" fill="none" stroke-width="3.8"/>'
              '<path fill="none" stroke-width="3.6" stroke-linecap="round" d="M37 28V53M34 28H40M34 53H40M23 40.5H30"/>',
    # Search: a magnifier
    "search": '<circle cx="28.5" cy="38.5" r="8" fill="none" stroke-width="4.2"/>'
              '<path fill="none" stroke-width="4.6" stroke-linecap="round" d="M34.5 44.5 42 52"/>',
    "colors": '<path d="M31 27c6 7.5 9.5 12.5 9.5 17a9.5 9.5 0 0 1-19 0c0-4.5 3.5-9.5 9.5-17Z"/>',
    "ruler": '<rect x="16" y="34" width="30" height="13" rx="2.5" fill="none" stroke-width="3.6"/><path fill="none" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" d="M22 34V39.5M28 34V41.5M34 34V39.5M40 34V41.5"/>',
    "hash": '<path fill="none" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" d="M31 26 42 30.5V39c0 7-5 11.5-11 14-6-2.5-11-7-11-14V30.5ZM25.5 40 29.5 44 36.5 35.5"/>',
    "shredder": '<rect x="18" y="31" width="26" height="9" rx="2.5"/><path fill="none" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" d="M22 44V53M27.5 44V50M33 44V53M38.5 44V50"/>',
    "fonts": '<path fill="none" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" d="M19 53 27.5 29 36 53M22 45H33M39 41.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9ZM43.5 39V53"/>',
    "network": '<path fill="none" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" d="M17 37a20 20 0 0 1 28 0M21.5 42a13.5 13.5 0 0 1 19 0M26 47a7 7 0 0 1 10 0"/><circle cx="31" cy="51.5" r="2.6"/>',
    "battery": '<rect x="16" y="33" width="26" height="15" rx="3.5" fill="none" stroke-width="3.6"/><rect x="20.5" y="37.5" width="11" height="6" rx="1.2"/><path fill="none" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" d="M46 38.5V42.5"/>',
    "focus": '<circle cx="31" cy="41" r="11" fill="none" stroke-width="3.8"/><path fill="none" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" d="M31 35V41L35 44M28 26H34"/>',
    "layouts": '<rect x="17" y="29" width="12" height="24" rx="2.5"/><rect x="32" y="29" width="13" height="10.5" rx="2.5"/><rect x="32" y="42.5" width="13" height="10.5" rx="2.5"/>',
    "drivers": '<rect x="18" y="32" width="26" height="17" rx="3" fill="none" stroke-width="3.6"/><path fill="none" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" d="M23 49V53M29 49V53M35 49V53M41 49V53M23 32V28M29 32V28M35 32V28M41 32V28"/>',
    "awake": '<path fill="none" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" d="M18 36H40V42a9 9 0 0 1-9 9H27a9 9 0 0 1-9-9ZM40 38.5h2a3.5 3.5 0 0 1 0 7H40M25 27v4M32 27v4"/>',
    "snippets": '<path fill="none" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" d="M24 30c-4 0-4 3-4 5.5s0 4.5-3 5c3 .5 3 2.5 3 5s0 5.5 4 5.5M38 30c4 0 4 3 4 5.5s0 4.5 3 5c-3 .5-3 2.5-3 5s0 5.5-4 5.5"/><circle cx="31" cy="40.5" r="2.6"/>',
    "backup": '<path fill="none" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" d="M41.5 35.5A11 11 0 0 0 21 34M20.5 45.5A11 11 0 0 0 41 47M21 28V34H27M41 53V47H35"/>',
    "notes": '<path fill="none" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" d="M20 29H42V43L35 50H20ZM35 50V43H42M25 36H37M25 41.5H31"/>',
    "system": '<rect x="17" y="29" width="28" height="18" rx="3" fill="none" stroke-width="3.6"/><path fill="none" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" d="M25 53H37M31 47V53"/>',
    "unlock": '<rect x="20" y="38" width="22" height="15" rx="3.5"/><path fill="none" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" d="M25 38V33.5a6 6 0 0 1 11.5-2.5"/>',
    "organizer": '<path fill="none" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" d="M17 33.5c0-2 1.5-3.5 3.5-3.5h6l3 3.5h12c2 0 3.5 1.5 3.5 3.5V49c0 2-1.5 3.5-3.5 3.5h-21C18.5 52.5 17 51 17 49ZM23.5 42H38.5M23.5 47H33"/>',
    "sound": '<path d="M19 37h5l8-7v22l-8-7h-5Z"/><path fill="none" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" d="M37 36a7 7 0 0 1 0 10M41 32a13 13 0 0 1 0 18"/>',
}
_GLYPH_FIT = 'transform="translate(6.857 6.857) scale(0.7857)"'
PRODUCT_COLORS = {  # (light end, deep end) of each product's gradient
    "corefold": ("#6be6d2", "#0f8f7c"), "converter": ("#b4f08c", "#2f9a52"), "player": ("#ff9cb8", "#c22f5c"), "archive": ("#ffd27a", "#a86c00"),
    "cleaner": ("#a6c1ff", "#2b5fd4"), "viewer": ("#c9bcff", "#6b4bd6"), "cloud": ("#9fe3ff", "#1b86c4"),
    "capture": ("#ffb08f", "#d4502c"), "space": ("#f2a8ff", "#a23cc0"), "clipboard": ("#d9e2ef", "#5f6f88"),
    "startup": ("#fff09a", "#c79300"), "scan": ("#7cf3d3", "#2563eb"), "pdf": ("#ffa494", "#cc2f2a"), "rename": ("#9ff0d0", "#13906a"),
    "search": ("#a9d4ff", "#3a63d8"),
    "colors": ("#ffb3e6", "#c4318f"), "ruler": ("#e0e6a8", "#8a9a1f"), "hash": ("#9ee8c0", "#1c8a55"),
    "shredder": ("#c9c9cf", "#55555e"), "fonts": ("#ffd1a6", "#c4621c"), "network": ("#99e6ff", "#127fae"),
    "battery": ("#b8f5a3", "#3b9a1f"), "focus": ("#ffb0a8", "#c4382c"), "layouts": ("#b5c4ff", "#4257c9"),
    "drivers": ("#d4c4a8", "#7a5f34"), "awake": ("#f2cfa0", "#9c6420"), "snippets": ("#c8b5ff", "#6a3fd0"),
    "backup": ("#9fe3dc", "#16867a"), "notes": ("#fff2a0", "#c8a400"), "system": ("#b9d3e8", "#3d6a8f"),
    "unlock": ("#ffc6a0", "#c9661c"), "organizer": ("#a6e3ff", "#2a7cc4"), "sound": ("#ffb8d4", "#bf3f74"),
    "files": ("#a9d2ff", "#2c6fd6"), "media": ("#ffb9a0", "#d1452f"), "computer": ("#d2b8ff", "#7440cf"),
    "quick": ("#ffe28e", "#c38800"),
}


_tiles: dict = {}  # drawn tiles, by (product, size, screen scale): Home draws the same few again on every rebuild


def product_tile(product: str, size: int, dark: bool = True, dpr: float = 1.0) -> QPixmap:
    """The app-icon look: the mark in a gradient on a near-black tile (the same in light and dark)."""
    key = (product, size, round(dpr, 3))
    kept = _tiles.get(key)
    if kept is not None:
        return QPixmap(kept)  # (shared until someone paints on it)
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
    if len(_tiles) > 200:
        _tiles.clear()
    _tiles[key] = pm
    return QPixmap(pm)


def soft_tile(name: str, size: int, color: QColor, dpr: float = 1.0) -> QPixmap:
    """An icon on a soft rounded square of its own colour (a feature in a list, in the quiet glyph colour)."""
    pm = QPixmap(max(1, round(size * dpr)), max(1, round(size * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    soft = QColor(color)
    soft.setAlpha(40)
    p.setBrush(soft)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    glyph = round(size * 0.55)
    p.drawPixmap((size - glyph) // 2, (size - glyph) // 2, pixmap(name, glyph, color, dpr))
    p.end()
    return pm


ICON_SIZE = QSize(20, 20)
SMALL = QSize(18, 18)
