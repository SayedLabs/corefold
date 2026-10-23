"""Design tokens: the few numbers and colours every screen is built from.

Surfaces stack from the back of the window to the front:
    backdrop (behind everything: the sidebar sits on it) → canvas (the page area, a big rounded sheet)
    → surface (cards and groups) → raised (popups, menus, the command palette).
Lines are hairlines (`border`) or, around things you can click, a little stronger (`line_strong`).
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor

# ---- spacing (a 4 px grid) and corners

SPACE = {"xxs": 2, "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 20, "xxl": 24, "x3": 32, "x4": 40}
RADIUS = {"xs": 4, "sm": 6, "md": 8, "lg": 12, "xl": 16, "canvas": 14, "pill": 999}

# ---- type: Segoe UI Variable on Windows 11 (Segoe UI on 10), sizes in points at 100 % text size

FONT_TEXT = '"Segoe UI Variable Text", "Segoe UI"'
FONT_DISPLAY = '"Segoe UI Variable Display", "Segoe UI"'
TYPE = {
    "caption": 8.0,     # small print, pills, keyboard hints
    "body": 10.0,       # everything by default
    "strong": 10.5,     # row titles
    "subtitle": 11.5,   # card titles
    "title": 14.0,      # group headings on big pages
    "h1": 20.0,         # a page's name
    "display": 26.0,    # the greeting on the start page
}

# ---- motion (milliseconds). Small things move fast; nothing waits on an animation.

MOTION = {"instant": 90, "fast": 140, "normal": 200, "slow": 280}


@dataclass
class Palette:
    dark: bool
    accent: QColor
    on_accent: QColor     # text and icons on top of the accent
    accent_soft: QColor   # a wash of the accent: selected rows, chips
    accent_hover: QColor
    backdrop: QColor      # behind everything (the sidebar)
    canvas: QColor        # the page area
    surface: QColor       # cards and groups
    raised: QColor        # popups and menus
    field: QColor         # text boxes and buttons
    hover: QColor         # behind something under the mouse
    pressed: QColor
    border: QColor        # hairlines
    line_strong: QColor   # edges of things you can click
    text: QColor
    muted: QColor         # second line of text
    subtle: QColor        # third line, placeholders
    danger: QColor
    success: QColor
    warning: QColor
    scrim: QColor         # dims the window behind a palette or dialog


def mix(a: QColor, b: QColor, t: float) -> QColor:
    """a, moved t of the way towards b."""
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


def alpha(c: QColor, a: int) -> QColor:
    out = QColor(c)
    out.setAlpha(a)
    return out


def on_color(c: QColor) -> QColor:
    """Readable text on top of c."""
    lum = 0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()
    return QColor("#06110f") if lum > 150 else QColor("#ffffff")


def palette(dark: bool, accent: QColor) -> Palette:
    """The colours for one appearance and accent (cool graphite neutrals, so any accent sits well)."""
    if dark:
        canvas = QColor("#121317")
        surface = QColor("#18191e")
        p = Palette(
            dark=True, accent=QColor(accent), on_accent=on_color(accent),
            accent_soft=mix(surface, accent, 0.16), accent_hover=QColor(accent).lighter(112),
            backdrop=QColor("#0b0c0f"), canvas=canvas, surface=surface, raised=QColor("#1d1e24"),
            field=QColor("#1e1f25"), hover=QColor("#24252c"), pressed=QColor("#2a2b33"),
            border=QColor("#25262d"), line_strong=QColor("#33343d"),
            text=QColor("#f3f4f7"), muted=QColor("#9b9da8"), subtle=QColor("#6c6e79"),
            danger=QColor("#ff6b78"), success=QColor("#4fd18b"), warning=QColor("#f2b44a"),
            scrim=QColor(0, 0, 0, 150))
    else:
        canvas = QColor("#f8f9fb")
        surface = QColor("#ffffff")
        p = Palette(
            dark=False, accent=QColor(accent), on_accent=on_color(accent),
            accent_soft=mix(surface, accent, 0.11), accent_hover=QColor(accent).darker(112),
            backdrop=QColor("#eceef2"), canvas=canvas, surface=surface, raised=QColor("#ffffff"),
            field=QColor("#ffffff"), hover=QColor("#f0f1f4"), pressed=QColor("#e6e8ec"),
            border=QColor("#e4e6eb"), line_strong=QColor("#d2d5dc"),
            text=QColor("#121318"), muted=QColor("#5d606b"), subtle=QColor("#8e919b"),
            danger=QColor("#c0263a"), success=QColor("#1d8a4e"), warning=QColor("#a86a00"),
            scrim=QColor(20, 22, 28, 90))
    return p


# ---- the palette in use (widgets that paint themselves read it; the app sets it when the theme changes)

_current: Palette | None = None
animations = True   # the app turns this off when the person asks for no animations


def use(p: Palette, animate: bool = True) -> None:
    global _current, animations
    _current = p
    animations = animate


def current() -> Palette:
    global _current
    if _current is None:
        _current = palette(True, QColor("#3cc7b2"))
    return _current
