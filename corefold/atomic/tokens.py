"""Design tokens: the few numbers and colours every screen is built from.

Surfaces stack from the back of the window to the front:
    backdrop (the sidebar) → canvas (the page area, flat beside it) → surface (cards and groups, filled,
    no edge) → raised (popups, menus, the command palette).
Lines are hairlines (`border`) or, around things you can click, a little stronger (`line_strong`).

The look is quiet: greys do the work and the accent colour is kept for what it means. It goes on the main
button of a screen (`primary`), switches and ticks that are on, progress, the focus ring and a drop target.
Everything else that's "picked" (the page you're on, a chosen option, a selected row) is a grey `select`
fill, and decorative icons are `glyph` (ink), never the accent.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor

# ---- spacing (a 4 px grid) and corners

SPACE = {"xxs": 2, "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 20, "xxl": 24, "x3": 32, "x4": 40}
RADIUS = {"xs": 4, "sm": 6, "md": 8, "lg": 12, "xl": 16, "canvas": 14, "pill": 999}

# ---- type: Segoe UI Variable on Windows 11 (Segoe UI on 10), sizes in points at 100 % text size

def _has_variable_segoe() -> bool:
    import os
    return os.path.exists(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "SegUIVar.ttf"))


# One family each, never a list: with a list ("Segoe UI Variable Text", "Segoe UI") Qt looks through every font
# on the PC the first time text is measured, which froze the window for seconds when it first opened.
_VARIABLE = _has_variable_segoe()
FONT_TEXT = '"Segoe UI Variable Text"' if _VARIABLE else '"Segoe UI"'
FONT_DISPLAY = '"Segoe UI Variable Display"' if _VARIABLE else '"Segoe UI"'
TEXT_FAMILY = FONT_TEXT.strip('"')  # (for QFont)
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
    subtle: QColor        # third line, placeholders (still easy to read: 4.5 to 1 on cards and the page)
    danger: QColor
    success: QColor
    warning: QColor
    scrim: QColor         # dims the window behind a palette or dialog
    select: QColor = None       # type: ignore[assignment]  # behind what's picked: the page you're on, a row
    faint: QColor = None        # type: ignore[assignment]  # what can't be used now, and edges under the mouse
    glyph: QColor = None        # type: ignore[assignment]  # decorative icons (feature tiles, list marks)
    primary: QColor = None      # type: ignore[assignment]  # the main button of a screen
    on_primary: QColor = None   # type: ignore[assignment]
    primary_hover: QColor = None  # type: ignore[assignment]


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
    """The colours for one appearance and accent: soft neutral greys (close to Apple's), so any accent sits well
    and shows only where it means something."""
    a = QColor(accent)
    if dark:
        surface = QColor("#1e1e21")
        p = Palette(
            dark=True, accent=a, on_accent=on_color(a), accent_soft=mix(surface, a, 0.16),
            accent_hover=a.lighter(110), backdrop=QColor("#19191b"), canvas=QColor("#131315"),
            surface=surface, raised=QColor("#26262a"), field=QColor("#2a2a2e"), hover=QColor("#2a2a2f"),
            pressed=QColor("#323237"), border=QColor("#26262a"), line_strong=QColor("#3a3a40"),
            text=QColor("#f5f5f7"), muted=QColor("#a1a1a8"), subtle=QColor("#8e8e95"),
            danger=QColor("#ff6961"), success=QColor("#32d74b"), warning=QColor("#ffb340"),
            scrim=QColor(0, 0, 0, 150), select=QColor("#2e2e33"), faint=QColor("#6e6e76"))
    else:
        surface = QColor("#f5f5f7")
        p = Palette(
            dark=False, accent=a, on_accent=on_color(a), accent_soft=mix(QColor("#ffffff"), a, 0.10),
            accent_hover=a.darker(110), backdrop=QColor("#ececef"), canvas=QColor("#ffffff"),
            surface=surface, raised=QColor("#ffffff"), field=QColor("#ffffff"), hover=QColor("#ececf0"),
            pressed=QColor("#e1e1e6"), border=QColor("#e6e6ea"), line_strong=QColor("#d2d2d7"),
            text=QColor("#1d1d1f"), muted=QColor("#5e5e63"), subtle=QColor("#6e6e73"),
            danger=QColor("#d70015"), success=QColor("#1e7b34"), warning=QColor("#b25000"),
            scrim=QColor(20, 20, 24, 90), select=QColor("#dcdce1"), faint=QColor("#a1a1a6"))
    p.glyph = p.text
    p.primary, p.on_primary, p.primary_hover = p.accent, p.on_accent, p.accent_hover
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
        _current = palette(True, QColor("#3fb6a2"))
    return _current
