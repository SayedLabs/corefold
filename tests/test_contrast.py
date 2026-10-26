"""Every kind of text is easy to read on what it sits on, in both looks and with every accent colour
(4.5 to 1, the WCAG AA rule for normal text)."""
import pytest
from PySide6.QtGui import QColor

from corefold.atomic import tokens
from corefold.settings import ACCENTS


def _lum(c: QColor) -> float:
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def ratio(a: QColor, b: QColor) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("dark", [False, True])
@pytest.mark.parametrize("accent", sorted(ACCENTS))
def test_text_is_easy_to_read_everywhere_it_sits(accent, dark):
    p = tokens.palette(dark, QColor(ACCENTS[accent][2 if dark else 1]))
    accent_text = p.accent if dark else p.accent.darker(108)  # (as theme.py and qss.py draw links)
    pairs = {
        "text": (p.text, ["canvas", "surface", "raised", "field", "backdrop", "hover", "select"]),
        "muted": (p.muted, ["canvas", "surface", "raised", "field", "backdrop", "select"]),
        "subtle": (p.subtle, ["canvas", "surface", "raised"]),
        "accent_text": (accent_text, ["canvas", "surface"]),
        "danger": (p.danger, ["canvas", "surface"]),
        "success": (p.success, ["canvas", "surface"]),
        "warning": (p.warning, ["canvas", "surface"]),
        "on_primary": (p.on_primary, ["primary"]),
        "on_accent": (p.on_accent, ["accent"]),
    }
    low = [f"{name} on {bg}: {ratio(fg, getattr(p, bg)):.2f}"
           for name, (fg, bgs) in pairs.items() for bg in bgs if ratio(fg, getattr(p, bg)) < 4.5]
    assert not low, low


def test_what_cant_be_used_now_still_looks_lighter_than_readable_text():
    for dark in (False, True):
        p = tokens.palette(dark, QColor("#3fb6a2"))
        assert ratio(p.faint, p.surface) < ratio(p.subtle, p.surface) < ratio(p.muted, p.surface) < ratio(p.text, p.surface)
