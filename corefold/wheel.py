"""The format wheel that pops up under the cursor, plus a preview of it for the settings window.

Two ways to use it:
- drag mode: hold Shift while dragging a file; drop it on a slice.
- click mode: right-click a file → "Convert with Corefold"; the wheel appears and you click a slice.

Slices that can't be used for the file are drawn dimmed; pointing at one shows why. When there
are more options than fit, the last slice is "More": click it, or hold the file over it for a
moment while dragging, to see the next page. The middle always says how many options there are,
which page you're on, and how many are dimmed or hidden in Settings.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QLinearGradient, QPainter,
                           QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import QWidget

from . import icons, winutil
from .converters import Pick, Slot, pick, warm_up
from .core import APP_NAME
from .i18n import tr
from .settings import TEXT_SIZES, WHEEL_SIZES, Settings
from .theme import Theme

CENTER = -2  # hover value for the cancel area in the middle
POP = 7      # how far the hovered slice grows
INNER = {"ring": 0.36, "petals": 0.34, "slim": 0.5, "bubbles": 0.37, "neon": 0.36, "glass": 0.36}
OPEN_STYLES = {"petals", "bubbles"}  # styles with gaps between the options (see the base disc in paint_wheel)
MORE_DWELL_MS = 650  # how long to hold a dragged file over "More" to turn the page
DWELL_STEP_MS = 30

ICONS = {  # action -> line icon (see icons.py)
    "resize": "resize_image", "rotate": "arrow_rotate_clockwise", "fit": "target_arrow", "cutout": "wand",
    "ocr": "scan_text", "small": "arrow_minimize", "watermark": "drop", "scan": "scan_dash", "video": "video_clip",
    "qr": "qr_code", "clean": "broom", "trim": "cut", "frames": "filmstrip_image", "mp3": "music_note_2",
    "merge": "merge", "compress": "folder_zip", "split": "split_vertical", "png": "image", "text": "document_text",
    "extract": "arrow_download", "folder": "folder_open", "browse": "search", "test": "stethoscope", "zip": "archive",
    "7z": "archive", "options": "lock_closed_key", "pdf": "document_pdf", "more": "add", "crop": "crop",
    "sticker": "sticker", "mute": "speaker_mute", "volume": "speaker_2", "speed": "fast_forward",
    "pages": "document_multiple", "images": "image_multiple", "lock": "lock_closed", "unlock": "lock_open",
    "rename": "rename",
}
FORMAT_ACTIONS = {"png", "jpg", "webp", "ico", "bmp", "mp3", "m4a", "wav", "flac", "ogg", "opus", "aiff", "mp4",
                  "mov", "webm", "mkv", "gif", "pdf", "text"}
GROUPS = {  # action -> group, for "colour by kind of action"
    **{a: "convert" for a in FORMAT_ACTIONS},
    **{a: "edit" for a in ("resize", "rotate", "trim", "watermark", "cutout", "clean", "frames", "video", "scan",
                           "crop", "sticker", "mute", "volume", "speed", "pages", "rename")},
    **{a: "shrink" for a in ("fit", "small", "compress")},
    **{a: "archive" for a in ("zip", "7z", "options", "extract", "folder", "browse", "test", "merge", "split")},
    **{a: "read" for a in ("ocr", "qr", "images")},
    **{a: "archive" for a in ("lock", "unlock")},
}
GROUP_COLORS = {"convert": "#3b82f6", "edit": "#a855f7", "shrink": "#22c55e", "archive": "#f59e0b",
                "read": "#ec4899", "more": "#94a3b8"}
GROUP_NAMES = {"convert": "Convert", "edit": "Edit", "shrink": "Make smaller", "archive": "Archive",
               "read": "Read"}


def action_of(key: str) -> str:
    return key.split(":", 1)[-1]


@dataclass
class Item:
    label: str
    ok: bool = True
    favorite: bool = False
    action: str = ""       # "resize", "jpg", ... (decides the icon and group colour)
    more: bool = False     # the "More" slice
    number: str = ""       # the key that picks it in click mode ("1"…"9", "0" for More)


def item_for(slot: Slot, favorite: bool) -> Item:
    return Item(slot.tool.label, slot.ok, favorite, action_of(slot.tool.key))


def inner_radius(R: float, style: str) -> float:
    return round(R * INNER.get(style, 0.36))


def slice_path(c: QPointF, r_in: float, r_out: float, start: float, span: float) -> QPainterPath:
    """Ring segment; angles in degrees, 0 = top, clockwise."""
    outer = QRectF(c.x() - r_out, c.y() - r_out, 2 * r_out, 2 * r_out)
    inner = QRectF(c.x() - r_in, c.y() - r_in, 2 * r_in, 2 * r_in)
    a0 = 90 - start  # Qt: 0 = 3 o'clock, counter-clockwise
    path = QPainterPath()
    path.arcMoveTo(outer, a0)
    path.arcTo(outer, a0, -span)
    path.arcTo(inner, a0 - span, span)
    path.closeSubpath()
    return path


@dataclass
class Look:
    style: str = "ring"
    labels: str = "both"      # both, text, icon
    hover: str = "fill"       # fill, outline, grow
    colors: str = "plain"     # plain, group
    text_scale: float = 1.0

    @classmethod
    def of(cls, s: Settings) -> "Look":
        return cls(s.wheel_style, s.label_style, s.hover_style, s.slice_colors, TEXT_SIZES.get(s.text_size, 1.0))


def slice_tint(look: Look, item: Item, i: int, n: int) -> QColor | None:
    """The colour a slice is tinted with ("by kind of action" or "rainbow"), or None."""
    if look.colors == "group":
        group = "more" if item.more else GROUPS.get(item.action, "")
        return QColor(GROUP_COLORS[group]) if group else None
    if look.colors == "rainbow":
        if item.more:
            return QColor(GROUP_COLORS["more"])
        return QColor.fromHsv(round(i * 360 / max(n, 1)) % 360, 150, 235)
    return None


def paint_wheel(p: QPainter, c: QPointF, R: float, items: list[Item], hover: int,
                line1: str, line2: str, t: Theme, look: Look | None = None, line3: str = "",
                more_progress: float = 0.0, page: tuple[int, int] = (0, 1)):
    look = look or Look()
    style = look.style
    r = inner_radius(R, style)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    petals = style == "petals"
    bubbles = style == "bubbles"
    neon = style == "neon"
    glass = style == "glass"
    open_style = style in OPEN_STYLES

    # Base disc. Never fully transparent, so Windows sends drops to it (even in the gaps).
    base = QColor(t.wheel_bg)
    if open_style:
        base.setAlpha(3)
    elif neon:  # deeper, so the glowing lines stand out
        base = _over(base, QColor(0, 0, 0, 90 if t.dark else 30))
        base.setAlpha(max(base.alpha(), 200))
    p.setPen(Qt.NoPen)
    p.setBrush(base)
    if open_style:
        p.drawEllipse(c, R + POP, R + POP)
    else:
        p.drawEllipse(c, R, R)

    n = len(items)
    size = max(8, round(R / 17 * look.text_scale))
    if n > 9:
        size = max(7, round(size * 9 / n + 1))
    label_font = QFont("Segoe UI", size)
    icon_font = QFont("Segoe UI", max(9, round(size * (1.55 if look.labels == "icon" else 1.15))))  # sizes the icons
    label_width = max(70, round(R * 0.61))
    if n:
        seg = 360 / n
        # petals and neon: a gap at the inner edge, widening outwards
        gap_px = 9 if petals else 6 if neon else 0
        gap = math.degrees(gap_px / max(r + 3, 1)) if gap_px and n > 1 else 0
        for i, item in enumerate(items):
            hovered = i == hover
            active = hovered and item.ok
            grow = active or (hovered and item.more)
            rr = R + (POP if grow else 0)
            mid = i * seg
            a = math.radians(mid)
            tint = slice_tint(look, item, i, n)
            filled = active and look.hover == "fill"
            m = (rr + r) / 2 + (2 if style == "slim" else 3)
            pos = QPointF(c.x() + m * math.sin(a), c.y() - m * math.cos(a))
            badge = None  # where the key number goes

            if bubbles:
                ring_w = (R - r) / 2 - 3
                chord = (R + r) / 2 * math.sin(math.radians(min(seg, 180) / 2)) - 4 if n > 1 else ring_w
                b = max(12.0, min(ring_w, chord)) + (POP / 2 if grow else 0)
                bc = QPointF(c.x() + (R + r) / 2 * math.sin(a), c.y() - (R + r) / 2 * math.cos(a))
                pos = bc
                if filled:
                    fill = QColor(t.accent)
                else:
                    fill = QColor(t.wheel_bg)
                    fill.setAlpha(max(fill.alpha(), 235))
                    if hovered:
                        fill = fill.lighter(118) if t.dark else fill.darker(107)
                    if tint is not None:
                        tint.setAlpha(80 if hovered else 50)
                        fill = _over(fill, tint)
                p.setBrush(fill)
                p.setPen(QPen(t.accent if active and look.hover == "outline" else t.wheel_line,
                              3 if active and look.hover == "outline" else 1))
                p.drawEllipse(bc, b, b)
                if item.more and more_progress > 0:
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(t.accent, 4, Qt.SolidLine, Qt.RoundCap))
                    p.drawArc(QRectF(bc.x() - b + 3, bc.y() - b + 3, 2 * b - 6, 2 * b - 6), 90 * 16,
                              int(-360 * more_progress * 16))
                badge = QPointF(bc.x() + b * 0.56, bc.y() - b * 0.56)  # top-right of the bubble, clear of the text
            else:
                inset_in = 3 if (petals or neon) else 0
                inset_out = 2 if (petals or neon) else 0
                path = slice_path(c, r + inset_in, rr - inset_out, mid - seg / 2 + gap / 2, seg - gap)
                line = QColor(tint) if tint is not None else QColor(t.accent)
                if neon:
                    if not (item.ok or item.more):
                        line = QColor(t.muted)
                    fill = QColor(line)
                    fill.setAlpha(150 if filled else 70 if hovered else 16)
                    p.setBrush(fill)
                    p.setPen(Qt.NoPen)
                    p.drawPath(path)
                    p.setBrush(Qt.NoBrush)
                    for width, alpha in ((9, 28), (5, 60), (2, 230 if hovered else 170)):  # the glow
                        g = QColor(line)
                        g.setAlpha(alpha if (item.ok or item.more) else alpha // 3)
                        p.setPen(QPen(g, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                        p.drawPath(path)
                elif glass:
                    if filled:
                        top, bottom = QColor(t.accent).lighter(130), QColor(t.accent)
                    elif tint is not None:  # coloured glass: the tint, clearer near the middle
                        top, bottom = QColor(tint), QColor(tint)
                        top.setAlpha(45 if t.dark else 40)
                        bottom.setAlpha((120 if t.dark else 110) + (40 if hovered else 0))
                    else:
                        shade = QColor(255, 255, 255) if t.dark else QColor(40, 60, 90)
                        top, bottom = QColor(shade), QColor(shade)
                        top.setAlpha(12 if t.dark else 8)
                        bottom.setAlpha((46 if t.dark else 34) + (24 if hovered else 0))
                    grad = QRadialGradient(c, rr)
                    grad.setColorAt(max(0.0, min(1.0, r / rr)), top)
                    grad.setColorAt(1.0, bottom)
                    p.setBrush(grad)
                    p.setPen(QPen(QColor(255, 255, 255, 70) if t.dark else QColor(40, 60, 90, 45), 1))
                    p.drawPath(path)
                else:
                    if filled:
                        fill = QColor(t.accent)
                    elif petals:
                        fill = QColor(t.wheel_bg)
                        if hovered:
                            fill = fill.lighter(115) if t.dark else fill.darker(106)
                    else:
                        fill = QColor(t.wheel_stripe)
                        if i % 2 == 0:
                            fill.setAlpha(fill.alpha() // 2)
                        if hovered:
                            fill.setAlpha(fill.alpha() + 18)
                    if tint is not None and not filled:
                        tint.setAlpha(70 if hovered else 42)
                        fill = tint if not petals else _over(fill, tint)
                    p.setBrush(fill)
                    if petals:
                        p.setPen(QPen(fill, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    else:
                        p.setPen(QPen(t.wheel_line, 1))
                    p.drawPath(path)
                if active and look.hover == "outline" and not neon:
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(t.accent, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    p.drawPath(slice_path(c, r + 2, rr - 2, mid - seg / 2 + gap / 2 + 0.8, seg - gap - 1.6))
                if item.more and more_progress > 0:  # dwell timer while dragging over "More"
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(t.accent, 4, Qt.SolidLine, Qt.RoundCap))
                    arc_r = rr - 5
                    p.drawArc(QRectF(c.x() - arc_r, c.y() - arc_r, 2 * arc_r, 2 * arc_r),
                              int((90 - (mid - seg / 2 + 2)) * 16), int(-(seg - 4) * more_progress * 16))
                nr = rr - max(9.0, R / 14)
                badge = QPointF(c.x() + nr * math.sin(a), c.y() - nr * math.cos(a))

            if filled:
                color = QColor(t.on_accent)
            elif active:
                color = QColor(t.accent) if not neon else QColor(t.text)
            elif item.ok or item.more:
                color = QColor(t.text)
            else:
                color = QColor(t.muted)
                color.setAlpha(150 if hovered else 95)
            _draw_label(p, pos, item, look, label_font, icon_font, color, active, label_width)
            if item.number and badge is not None:  # small key number near the outer edge
                nf = QFont("Segoe UI", max(6, round(size * 0.75)))
                nf.setWeight(QFont.Weight.Bold)
                p.setFont(nf)
                ncol = QColor(color)
                ncol.setAlpha(min(ncol.alpha(), 150))
                p.setPen(ncol)
                p.drawText(QRectF(badge.x() - 10, badge.y() - 8, 20, 16), Qt.AlignCenter, item.number)

    if glass:  # a soft shine across the top half
        ring = QPainterPath()
        ring.addEllipse(c, R, R)
        hole = QPainterPath()
        hole.addEllipse(c, r, r)
        shine = QLinearGradient(QPointF(c.x(), c.y() - R), QPointF(c.x(), c.y()))
        shine.setColorAt(0.0, QColor(255, 255, 255, 70 if t.dark else 150))
        shine.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(shine)
        p.drawPath(ring.subtracted(hole))
    if neon:
        for width, alpha in ((8, 30), (2, 200)):
            g = QColor(t.accent)
            g.setAlpha(alpha)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(g, width))
            p.drawEllipse(c, R - 1, R - 1)
    elif not open_style:
        rim = QColor(t.accent) if not glass else QColor(255, 255, 255) if t.dark else QColor(40, 60, 90)
        rim.setAlpha(150 if not glass else (90 if t.dark else 60))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(rim, 2))
        p.drawEllipse(c, R - 1, R - 1)

    # middle: what's being dropped, what will happen, and how many options there are
    p.setPen(QPen(t.accent if neon else t.wheel_line, 1.5 if neon else 1))
    center_bg = QColor(t.wheel_center)
    if hover == CENTER and n:
        center_bg = center_bg.lighter(125) if t.dark else center_bg.darker(104)
    p.setBrush(center_bg)
    p.drawEllipse(c, r - 4, r - 4)
    width = int(2 * r - 18)
    f1 = QFont("Segoe UI", max(7, round(R / 21 * look.text_scale)))
    f1.setWeight(QFont.Weight.DemiBold)
    f2 = QFont("Segoe UI", max(7, round(R / 22 * look.text_scale)))
    f3 = QFont("Segoe UI", max(6, round(R / 26)))
    p.setFont(f1)
    p.setPen(t.text)
    p.drawText(QRectF(c.x() - r, c.y() - 21, 2 * r, 17), Qt.AlignCenter,
               QFontMetrics(f1).elidedText(line1, Qt.ElideMiddle, width))
    p.setFont(f2)
    unusable = 0 <= hover < n and not items[hover].ok and not items[hover].more
    p.setPen(t.accent if hover >= 0 and not unusable else t.muted)
    p.drawText(QRectF(c.x() - r + 8, c.y() - 3, 2 * r - 16, r * 0.5),
               Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, line2)
    current, total = page
    if total > 1:  # page dots: ● ○ ○
        dot, gap_px = max(2.0, r / 22), max(7.0, r / 7)
        x0 = c.x() - gap_px * (total - 1) / 2
        p.setPen(Qt.NoPen)
        for i in range(total):
            col = QColor(t.accent) if i == current else QColor(t.muted)
            if i != current:
                col.setAlpha(120)
            p.setBrush(col)
            p.drawEllipse(QPointF(x0 + i * gap_px, c.y() + r * 0.5), dot, dot)
    if line3:
        p.setFont(f3)
        p.setPen(t.muted)
        chord = 2 * math.sqrt(max(1.0, (r - 4) ** 2 - (r * 0.7) ** 2))
        p.drawText(QRectF(c.x() - r, c.y() + r * 0.7 - 7, 2 * r, 14), Qt.AlignCenter,
                   QFontMetrics(f3).elidedText(line3, Qt.ElideRight, int(chord)))


def _over(a: QColor, b: QColor) -> QColor:
    """b drawn over a."""
    k = b.alpha() / 255
    return QColor(round(a.red() + (b.red() - a.red()) * k), round(a.green() + (b.green() - a.green()) * k),
                  round(a.blue() + (b.blue() - a.blue()) * k), max(a.alpha(), b.alpha()))


def _draw_label(p: QPainter, pos: QPointF, item: Item, look: Look, font: QFont, icon_font: QFont,
                color: QColor, active: bool, max_width: int = 102):
    icon = ICONS.get("more" if item.more else item.action, "")
    is_format = item.action in FORMAT_ACTIONS and not item.more
    text = ("★ " if item.favorite and item.ok else "") + item.label
    mode = look.labels
    if is_format and mode == "icon":
        mode = "text"  # format names are clearer than any icon
    if is_format and mode == "both":
        mode = "text"
    font.setWeight(QFont.Weight.Bold if active else QFont.Weight.DemiBold)
    # long names (they're often longer in Arabic) end in "…" instead of being cut off mid-letter
    text = QFontMetrics(font).elidedText(text, Qt.ElideRight, max_width)
    half = max_width // 2 + 1
    p.setPen(color)
    # line icons look smaller than emoji at the same size, so they're drawn a little bigger
    side = round(icon_font.pointSizeF() * (1.3 if mode == "icon" else 1.55))
    if mode == "icon" and icon:
        icons.paint(p, QRectF(pos.x() - side / 2, pos.y() - side / 2, side, side), icon, color)
        return
    if mode == "both" and icon:
        icons.paint(p, QRectF(pos.x() - side / 2, pos.y() - 2 - side, side, side), icon, color)
        p.setPen(color)
        p.setFont(font)
        p.drawText(QRectF(pos.x() - half, pos.y() - 1, 2 * half, 22), Qt.AlignCenter, text)
        return
    p.setFont(font)
    p.drawText(QRectF(pos.x() - half, pos.y() - 13, 2 * half, 26), Qt.AlignCenter, text)


def ease_out_back(x: float) -> float:
    c1 = 1.70158
    return 1 + (c1 + 1) * (x - 1) ** 3 + c1 * (x - 1) ** 2


def paginate(slots: list, per_page: int) -> list[list]:
    """Splits the slices into pages; every page but a single one keeps its last spot for "More"."""
    per_page = max(3, per_page)
    if len(slots) <= per_page:
        return [list(slots)]
    size = per_page - 1
    return [slots[i:i + size] for i in range(0, len(slots), size)]


def counts_line(pk: Pick) -> str:
    return tr("{count} options", count=sum(1 for s in pk.slots if s.ok))


def hint_text(pk: Pick, click_mode: bool, pages: int, numbers: bool = False) -> str:
    """What the middle says while you're not pointing at anything (the dots show the pages)."""
    text = tr("Click an option") if click_mode else tr("Drop on an option")
    if click_mode and numbers and pk.slots:
        text = tr("Click an option or press its number")
    if pk.hidden:
        text += "\n" + tr("{count} hidden", count=pk.hidden)
    return text


class Wheel(QWidget):
    chosen = Signal(object, list)  # Tool, files
    opened = Signal()
    refined = Signal(int)          # background checks finished for this load (its number)

    def __init__(self, settings: Settings, theme: Theme):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.settings, self.theme = settings, theme
        self.R = WHEEL_SIZES["medium"]
        self.scale = 1.0
        self.click_mode = False
        self._down_at_open = False
        self._keys_down: set[str] = set()
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(170)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.valueChanged.connect(self._animate)
        self.watch = QTimer(self)  # click mode: close on Esc or a click outside
        self.watch.timeout.connect(self._watch)
        self.dwell = QTimer(self)  # drag mode: hold over "More" to turn the page
        self.dwell.setInterval(DWELL_STEP_MS)
        self.dwell.timeout.connect(self._dwell_tick)
        self.dwell_ms = 0
        self.load_id = 0
        self.paths: list[Path] = []
        self.refined.connect(self._refine)
        self.reset()

    def reset(self):
        self.load_id += 1  # any background check still running is for an old file
        self.paths = []
        self.files: list[Path] = []
        self.pick = Pick([], [])
        self.pages: list[list[Slot]] = [[]]
        self.page = 0
        self.hover = CENTER
        self.title = ""
        self.empty_reason = ""
        self.dwell_ms = 0
        self.dwell.stop()

    # ---- what's on the wheel

    @property
    def slots(self) -> list[Slot]:
        return self.pages[self.page] if self.pages else []

    @property
    def has_more(self) -> bool:
        return len(self.pages) > 1

    def count(self) -> int:
        return len(self.slots) + (1 if self.has_more else 0)

    def is_more(self, idx: int) -> bool:
        return self.has_more and idx == len(self.slots)

    def slot_at(self, idx: int) -> Slot | None:
        return self.slots[idx] if 0 <= idx < len(self.slots) else None

    def next_page(self):
        if self.has_more:
            self.page = (self.page + 1) % len(self.pages)
            self.hover = -1
            self.dwell_ms = 0
            self.update()

    def _animate(self, v):
        v = float(v)
        self.scale = 0.82 + 0.18 * ease_out_back(v)
        self.setWindowOpacity(min(1.0, v * 1.8))
        self.update()

    def _load(self, paths: list[Path], quick: bool = True):
        """Works out the slices. With quick=True, checks that have to read the file (like "does this
        video have sound?") run in the background, so the wheel appears at once."""
        s = self.settings
        self.paths = list(paths)
        self.pick = pick(paths, set(s.hidden_tools), s.favorites, s.show_unavailable, s.tool_order,
                         s.pack_on_every_wheel, quick=quick)
        self.files = self.pick.files
        page = self.page if not quick else 0
        self.pages = paginate(self.pick.slots, s.per_page)
        self.page = min(page, len(self.pages) - 1)
        if not self.pick.slots:
            self.title = paths[0].name if paths else ""
            if self.files and self.pick.dimmed and not self.pick.hidden:
                self.empty_reason = tr("Nothing here works for this file")  # all greyed out and then hidden
            elif self.files:
                self.empty_reason = tr("Nothing available. Check Formats in Settings")
            else:
                self.empty_reason = tr("This file can't be found")
        elif len(self.files) == 1:
            self.title = self.files[0].name
        else:
            self.title = tr("{count} items", count=len(self.files))
        if quick and self.pick.pending:
            self.load_id += 1
            load_id, files = self.load_id, list(self.files)

            def work():
                warm_up(files)
                self.refined.emit(load_id)
            threading.Thread(target=work, daemon=True).start()

    def _refine(self, load_id: int):
        if load_id == self.load_id and self.isVisible() and self.paths:
            self._load(self.paths, quick=False)
            self.update()

    def popup_at_cursor(self, paths: list[Path] | None = None):
        """Drag mode when paths is None (files arrive with the drag); click mode otherwise."""
        if self.isVisible() and not paths:
            return
        self.hide()
        self.reset()
        self.click_mode = paths is not None
        if paths:
            self._load(paths)
        self.R = WHEEL_SIZES.get(self.settings.wheel_size, WHEEL_SIZES["medium"])
        side = 2 * (self.R + POP) + 14
        self.setFixedSize(side, side)
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        if self.settings.wheel_position == "center":
            cx, cy = g.left() + g.width() // 2, g.top() + g.height() // 2
        else:
            cx, cy = pos.x(), pos.y()
        x = min(max(cx - side // 2, g.left()), g.right() - side)
        y = min(max(cy - side // 2, g.top()), g.bottom() - side)
        self.move(x, y)
        if self.settings.animations:
            self.scale = 0.82
            self.setWindowOpacity(0.0)
            self.show()
            self.anim.stop()
            self.anim.start()
        else:
            self.scale = 1.0
            self.setWindowOpacity(1.0)
            self.show()
        self.raise_()
        self.opened.emit()
        if self.click_mode:
            self._down_at_open = winutil.any_button_down()
            self._keys_down = set()
            self._key_pressed()  # keys already held when the wheel opened don't count
            self.watch.start(30)

    def hideEvent(self, e):
        self.watch.stop()
        self.dwell.stop()
        self.click_mode = False
        super().hideEvent(e)

    def _numbers_on(self) -> bool:
        return self.click_mode and getattr(self.settings, "wheel_numbers", True)

    def _key_pressed(self) -> str:
        """A number key that has just gone down ("" if none). Top row and number pad both work."""
        now = set()
        for d in range(10):
            if winutil.key_down(0x30 + d) or winutil.key_down(0x60 + d):
                now.add(str(d))
        new = now - self._keys_down
        self._keys_down = now
        return min(new) if new else ""

    def press_number(self, key: str) -> bool:
        """Picks the slice with this number (0 = More). Returns True if something happened."""
        if key == "0":
            if self.has_more:
                self.next_page()
                return True
            return False
        idx = int(key) - 1
        if idx >= len(self.slots):
            return False
        slot = self.slots[idx]
        if not slot.ok:
            self._set_hover(idx)  # shows why it can't be used
            return False
        files = list(self.files)
        self.hide()
        self.chosen.emit(slot.tool, files)
        return True

    def _watch(self):
        if winutil.key_down(winutil.VK_ESCAPE):
            self.hide()
            return
        if self._numbers_on():
            key = self._key_pressed()
            if key and self.press_number(key):
                return
        down = winutil.any_button_down()
        if self._down_at_open:  # wait for the click that opened the menu to finish
            self._down_at_open = down
            return
        if down and not self.geometry().contains(QCursor.pos()):
            self.hide()

    def hit(self, pos: QPointF) -> int:
        dx, dy = pos.x() - self.width() / 2, pos.y() - self.height() / 2
        dist = math.hypot(dx, dy) / max(self.scale, 0.01)
        if dist < inner_radius(self.R, self.settings.wheel_style):
            return CENTER
        n = self.count()
        if dist > self.R + POP or not n:
            return -1
        seg = 360 / n
        angle = math.degrees(math.atan2(dx, -dy)) % 360  # 0 = top, clockwise
        return int(((angle + seg / 2) % 360) // seg) % n

    def _usable(self, idx: int) -> bool:
        slot = self.slot_at(idx)
        return bool(slot and slot.ok)

    def _set_hover(self, idx: int):
        if idx != self.hover:
            self.hover = idx
            self.dwell_ms = 0
            if self.is_more(idx) and not self.click_mode:
                self.dwell.start()
            else:
                self.dwell.stop()
            self.update()

    def _dwell_tick(self):
        if not self.is_more(self.hover):
            self.dwell.stop()
            return
        self.dwell_ms += DWELL_STEP_MS
        if self.dwell_ms >= MORE_DWELL_MS:
            self.dwell.stop()
            self.next_page()
            self.hover = len(self.slots) if self.has_more else -1  # still over "More"
        self.update()

    # ---- drag & drop
    def dragEnterEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if not paths:
            e.ignore()
            return
        if paths != self.paths:  # dragging out and back in keeps the page you were on
            self._load(paths)
        e.setDropAction(Qt.CopyAction)  # Shift normally means "move"; Corefold always copies
        e.accept()
        self.update()

    def dragMoveEvent(self, e):
        self._set_hover(self.hit(e.position()))
        if self._usable(self.hover):
            e.setDropAction(Qt.CopyAction)
            e.accept()
        else:
            e.ignore()  # middle, outside, "More" or a dimmed slice = no drop

    def dragLeaveEvent(self, e):
        self._set_hover(-1)

    def dropEvent(self, e):
        idx = self.hit(e.position())
        slot = self.slot_at(idx)
        if slot and slot.ok:
            e.setDropAction(Qt.CopyAction)
            e.accept()
            self.chosen.emit(slot.tool, list(self.files))
        else:
            e.ignore()
        self.hide()

    # ---- click mode
    def mouseMoveEvent(self, e):
        if self.click_mode:
            self._set_hover(self.hit(e.position()))

    def mouseReleaseEvent(self, e):
        if not self.click_mode:
            return
        idx = self.hit(e.position())
        if self.is_more(idx):
            self.next_page()
            self._set_hover(idx if self.has_more else -1)
            return
        slot = self.slot_at(idx)
        if slot and slot.ok:
            files = list(self.files)
            self.hide()
            self.chosen.emit(slot.tool, files)
        elif idx in (CENTER, -1):
            self.hide()
        # clicking a dimmed slice keeps the wheel open; its reason is already shown

    def wheelEvent(self, e):
        if self.click_mode and self.has_more and e.angleDelta().y():
            if e.angleDelta().y() < 0:
                self.next_page()
            else:
                self.page = (self.page - 1) % len(self.pages)
                self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self.scale != 1.0:
            p.translate(c)
            p.scale(self.scale, self.scale)
            p.translate(-c)
        hint = hint_text(self.pick, self.click_mode, len(self.pages), self._numbers_on())
        line3 = ""
        if not self.title:
            line1, line2 = APP_NAME, hint
        elif not self.pick.slots:
            line1, line2 = self.title, self.empty_reason
        elif self.is_more(self.hover):
            nxt = (self.page + 1) % len(self.pages) + 1
            line1 = self.title
            line2 = tr("Click for page {page}", page=nxt) if self.click_mode else tr("Hold for page {page}", page=nxt)
        elif self.slot_at(self.hover):
            slot = self.slot_at(self.hover)
            line1, line2 = self.title, slot.reason or slot.tool.description
        elif self.hover == CENTER:
            line1, line2 = self.title, tr("Click here to cancel") if self.click_mode else tr("Let go here to cancel")
        else:
            line1, line2 = self.title, hint
        if self.pick.slots:
            line3 = counts_line(self.pick)
        favs = set(self.settings.favorites)
        items = [item_for(s, s.tool.key in favs) for s in self.slots]
        if self.has_more:
            items.append(Item(tr("More"), ok=False, more=True))
        if self._numbers_on():
            for i, item in enumerate(items):
                item.number = "0" if item.more else (str(i + 1) if i < 9 else "")
        progress = self.dwell_ms / MORE_DWELL_MS if self.is_more(self.hover) and not self.click_mode else 0.0
        paint_wheel(p, c, self.R, items, self.hover, line1, line2, self.theme, Look.of(self.settings), line3,
                    progress, (self.page, len(self.pages)))
        p.end()


class WheelPreview(QWidget):
    """A live, non-interactive wheel for the Look settings page."""
    def __init__(self, settings: Settings, theme: Theme):
        super().__init__()
        self.settings, self.theme = settings, theme
        self.hover = 0
        self.demo = [Item("PNG", action="png"), Item("JPG", ok=False, action="jpg"), Item("WEBP", action="webp"),
                     Item(tr("Resize"), favorite=True, action="resize"), Item(tr("Cut out"), action="cutout"),
                     Item(tr("Fit size"), action="fit"), Item(tr("Copy text"), action="ocr"),
                     Item("ZIP", action="zip"), Item(tr("More"), ok=False, more=True)]
        self.text = [tr("Convert to {format}", format="PNG"), tr("Already {format}", format="JPG"),
                     tr("Convert to {format}", format="WebP"), tr("Make it bigger or smaller"),
                     tr("Remove the background"), tr("Make it fit a size limit"), tr("Copy the text in the picture"),
                     tr("Put in a ZIP file"), tr("Hold for page {page}", page=2)]
        side = int(2 * (WHEEL_SIZES["huge"] + POP) * 0.58) + 16
        self.setFixedSize(side, side)
        self.timer = QTimer(self)  # only runs while the preview can be seen
        self.timer.setInterval(1100)
        self.timer.timeout.connect(self._step)

    def showEvent(self, e):
        super().showEvent(e)
        self.timer.start()

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    def _step(self):
        if self.settings.animations:
            self.hover = (self.hover + 1) % len(self.demo)
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        R = WHEEL_SIZES.get(self.settings.wheel_size, WHEEL_SIZES["medium"]) * 0.58
        c = QPointF(self.width() / 2, self.height() / 2)
        paint_wheel(p, c, R, self.demo, self.hover, "photo.jpg", self.text[self.hover], self.theme,
                    Look.of(self.settings), tr("{count} options", count=16), page=(0, 2))
        p.end()
