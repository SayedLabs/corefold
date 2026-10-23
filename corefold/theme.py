"""Colours and the app-wide stylesheet. Light/dark follows Windows unless the user picks one.

The colours, sizes and Qt's own widgets come from the Atomic design system (atomic/); this file adds
the wheel's colours and the look of Corefold's own parts."""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from string import Template

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap

from . import winutil
from .atomic import qss, tokens
from .atomic.tokens import mix  # noqa: F401 (other modules take it from here)
from .settings import ACCENTS, DEFAULT_ACCENT


@dataclass
class Theme:
    dark: bool
    accent: QColor
    on_accent: QColor    # text on top of the accent colour
    bg: QColor           # the page area (and dialogs)
    panel: QColor        # cards and popups
    field: QColor        # inputs and buttons
    border: QColor       # the thin lines between things
    text: QColor
    muted: QColor
    wheel_bg: QColor
    wheel_stripe: QColor
    wheel_center: QColor
    wheel_line: QColor
    danger: QColor = None        # type: ignore[assignment]  # "Delete", "Uninstall"…
    sidebar: QColor = None       # type: ignore[assignment]  # behind everything (the sidebar sits on it)
    line_strong: QColor = None   # type: ignore[assignment]  # borders of things you can click
    hover: QColor = None         # type: ignore[assignment]  # behind something under the mouse
    tokens: tokens.Palette = None  # type: ignore[assignment]  # the design system's full set of colours


def system_is_dark() -> bool:
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except Exception:
        pass
    return winutil.registry_prefers_dark()


def build_theme(appearance: str, accent_key: str, custom_color: str = "#3cc7b2", opacity: int = 94) -> Theme:
    """Colours from the Atomic design system (atomic/tokens.py), plus the wheel's own."""
    dark = appearance == "dark" or (appearance == "system" and system_is_dark())
    if accent_key == "custom" and QColor(custom_color).isValid():
        accent = QColor(custom_color)
    else:
        accent = QColor(ACCENTS.get(accent_key, ACCENTS[DEFAULT_ACCENT])[2 if dark else 1])
    p = tokens.palette(dark, accent)
    a = round(255 * opacity / 100)
    if dark:
        wheel = dict(wheel_bg=QColor(17, 18, 22, a), wheel_stripe=QColor(255, 255, 255, 10),
                     wheel_center=QColor(10, 11, 13, 250), wheel_line=QColor(255, 255, 255, 15))
    else:
        wheel = dict(wheel_bg=QColor(255, 255, 255, a), wheel_stripe=QColor(17, 17, 19, 10),
                     wheel_center=QColor(255, 255, 255, 252), wheel_line=QColor(17, 17, 19, 26))
    return Theme(dark, p.accent, p.on_accent, bg=p.canvas, panel=p.surface, field=p.field, border=p.border,
                 text=p.text, muted=p.muted, danger=p.danger, sidebar=p.backdrop, line_strong=p.line_strong,
                 hover=p.hover, tokens=p, **wheel)


def _image(name: str, color: QColor, draw) -> str:
    """A small picture for the stylesheet (ticks, arrows), drawn once and kept in the temp folder."""
    path = Path(tempfile.gettempdir()) / f"corefold-{name}-{color.name()[1:]}.png"
    if not path.exists():
        pm = QPixmap(36, 36)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(color, 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        draw(p)
        p.end()
        pm.save(str(path), "PNG")
    return path.as_posix()


def _check_image(color: QColor) -> str:
    def draw(p):
        tick = QPainterPath(QPointF(9, 19))
        tick.lineTo(15.5, 25.5)
        tick.lineTo(27, 11)
        p.drawPath(tick)
    return _image("check", color, draw)


def _chevron_image(color: QColor, up: bool) -> str:
    def draw(p):
        path = QPainterPath(QPointF(10, 22 if up else 14))
        path.lineTo(18, 14 if up else 22)
        path.lineTo(26, 22 if up else 14)
        p.drawPath(path)
    return _image("up" if up else "down", color, draw)


# Corefold's own parts (Qt's widgets and the shared names come from atomic/qss.py)
QSS = Template("""
QWidget#sidebar { background: transparent; }
QLabel#brand { font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 12.5pt; font-weight: 700; }
QListWidget#nav { background: transparent; border: none; outline: none; }
QPushButton#navToggle { background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 0;
                        min-width: 0; }
QPushButton#navToggle:hover { background: $nav_hover; }
QPushButton#navToggle:pressed { background: $nav_press; }
QFrame#palettePanel { background: transparent; border: none; }
QLineEdit#paletteSearch { background: transparent; border: none; font-size: 12.5pt; padding: 8px 10px; min-height: 26px; }
QLineEdit#paletteSearch:focus { border: none; background: transparent; }
QListWidget#paletteList { background: transparent; border: none; padding: 0; }
QListWidget#settingsNav { background: transparent; border: none; outline: none; }
QLabel#soonPill { background: transparent; border: 1px solid $line_strong; border-radius: 9px; color: $muted;
                  font-size: 7pt; font-weight: 600; padding: 0 7px; }
QLabel#newPill { background: $accent; color: $on_accent; border-radius: 9px; font-size: 7pt; font-weight: 700;
                 padding: 0 7px; }
QFrame#toolCard { background: $panel; border: 1px solid $border; border-radius: 14px; }
QFrame#toolCard:hover { border-color: $accent_line; background: $tool_hover; }
QFrame#toolCard:focus { border-color: $accent; }
QLabel#cardTitle { font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 11pt; font-weight: 600; }
QLabel#cardStat { color: $accent_text; font-size: 8.5pt; font-weight: 600; }
QLabel#heroStat { font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 17pt; font-weight: 700; }
QFrame#dropzone { border: 1px dashed $line_strong; border-radius: 14px; background: $panel; }
QFrame#dropzone:hover { border-color: $accent; }
QFrame#dropzone[hot="true"] { border: 1px solid $accent; background: $accent_soft; }
QPushButton#action { padding: 7px 13px; border-radius: 8px; }
QPushButton#action:hover { background: $hover; border-color: $line_strong; }
QPushButton#action:disabled { color: $muted; background: transparent; border: 1px dashed $border; }
QPushButton#quick { background: $panel; border: 1px solid $border; border-radius: 12px; padding: 10px 14px;
                    text-align: $lead_side; font-weight: 600; }
QPushButton#quick:hover { background: $tool_hover; border-color: $accent_line; }
QTreeWidget#history, QTreeWidget#apps, QListWidget#history { background: $panel; border: 1px solid $border;
        border-radius: 12px; outline: none; padding: 4px; }
QTreeWidget#history::item, QTreeWidget#apps::item { padding: 6px 2px; }
QListWidget#history::item { padding: 8px 10px; border-radius: 8px; }
QTreeWidget#history::item:selected, QTreeWidget#apps::item:selected, QListWidget#history::item:selected {
        background: $accent_soft; color: $text; }
QPushButton#star { background: transparent; border: none; font-size: 13pt; padding: 0 4px; color: $muted; }
QPushButton#star:checked { color: $accent; background: transparent; }
QPushButton#power { padding: 8px 18px; font-weight: 600; border-radius: 17px; }
QPushButton#power:checked { background: $accent_soft; border-color: $accent; color: $text; }
QPushButton#driveCard { background: $panel; border: 1px solid $border; border-radius: 14px; padding: 0; text-align: left; }
QPushButton#driveCard:hover { border-color: $accent_line; background: $tool_hover; }
QProgressBar#low::chunk { background: $danger; }
QWidget#playerWindow { background: #000000; }
QFrame#playerBar, QWidget#playerNoteBox { background: #121314; }
QFrame#playerBar QLabel, QWidget#playerNoteBox QLabel { color: #f2f2f2; background: transparent; }
QFrame#playerBar QLabel#muted { color: #a3a6a8; }
QFrame#playerBar QPushButton#small, QWidget#playerNoteBox QPushButton { color: #e8e8e8; background: transparent;
        border: 1px solid transparent; font-size: 9pt; }
QFrame#playerBar QPushButton#small:hover, QWidget#playerNoteBox QPushButton:hover { background: #26282a;
        border-color: #3a3c3e; }
QFrame#playerBar QPushButton#small:checked { background: #1f3330; border-color: $accent; }
QFrame#playerBar QPushButton#small:disabled { color: #55585a; }
QFrame#playerBar QSlider::groove:horizontal { background: #3a3c3e; }
QFrame#playerBar QSlider::handle:horizontal { background: #f2f2f2; border-color: #f2f2f2; }
QSlider#seek::groove:horizontal { height: 6px; border-radius: 3px; }
QSlider#seek::sub-page:horizontal { border-radius: 3px; }
QLabel#playerCover { background: #0b0b0c; }
QFrame#clipPopup { background: $raised; border: 1px solid $line_strong; border-radius: 16px; }
QListWidget#clips { background: transparent; border: none; outline: none; }
QListWidget#clipsPage { background: $panel; border: 1px solid $border; border-radius: 12px; outline: none; padding: 4px; }
QLabel#toastTitle { font-weight: 600; }
QPushButton#close { background: transparent; border: none; color: $muted; padding: 0 4px; font-size: 11pt; }
QPushButton#close:hover { color: $text; }
""")


def stylesheet(t: Theme, scale: float = 1.0) -> str:
    rtl = QGuiApplication.layoutDirection() == Qt.RightToLeft
    p = t.tokens
    lead = "right" if rtl else "left"
    base = qss.base(p, rtl, check=_check_image(p.on_accent), up=_chevron_image(p.muted, True),
                    down=_chevron_image(p.muted, False))
    own = QSS.substitute(
        text=p.text.name(), panel=p.surface.name(), field=p.field.name(), raised=p.raised.name(),
        border=p.border.name(), muted=p.muted.name(), subtle=p.subtle.name(), accent=p.accent.name(),
        on_accent=p.on_accent.name(), accent_soft=p.accent_soft.name(), danger=p.danger.name(),
        line_strong=p.line_strong.name(), hover=p.hover.name(),
        accent_line=mix(p.line_strong, p.accent, 0.45).name(),
        accent_text=(p.accent if p.dark else p.accent.darker(108)).name(),
        tool_hover=mix(p.surface, p.accent, 0.05 if p.dark else 0.035).name(),
        nav_hover=mix(p.backdrop, p.text, 0.06).name(), nav_press=mix(p.backdrop, p.text, 0.1).name(),
        lead_side=lead,
    )
    return qss.scaled(base + own, scale)


def round_menu(menu) -> None:
    """Rounded corners for a menu (Windows draws menus square unless the window is see-through)."""
    menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WA_TranslucentBackground)
    menu.setProperty("round", True)
