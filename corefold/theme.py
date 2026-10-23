"""Colours and the app-wide stylesheet. Light/dark follows Windows unless the user picks one.

The dark look: near-black surfaces separated by thin dark lines, with the accent colour kept for
the few things that matter (the selected page, the main button, switches, focus)."""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from string import Template

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap

from . import winutil
from .settings import ACCENTS, DEFAULT_ACCENT


@dataclass
class Theme:
    dark: bool
    accent: QColor
    on_accent: QColor    # text on top of the accent colour
    bg: QColor           # window background
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
    sidebar: QColor = None       # type: ignore[assignment]
    line_strong: QColor = None   # type: ignore[assignment]  # borders of things you can click
    hover: QColor = None         # type: ignore[assignment]  # behind something under the mouse


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


def mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


def build_theme(appearance: str, accent_key: str, custom_color: str = "#3cc7b2", opacity: int = 94) -> Theme:
    """Colours from the Corefold design system (dark: near-black with thin dark lines)."""
    dark = appearance == "dark" or (appearance == "system" and system_is_dark())
    if accent_key == "custom" and QColor(custom_color).isValid():
        accent = QColor(custom_color)
    else:
        accent = QColor(ACCENTS.get(accent_key, ACCENTS[DEFAULT_ACCENT])[2 if dark else 1])
    lum = 0.2126 * accent.red() + 0.7152 * accent.green() + 0.0722 * accent.blue()
    on_accent = QColor("#04130f") if lum > 150 else QColor("#ffffff")
    a = round(255 * opacity / 100)
    if dark:
        return Theme(True, accent, on_accent,
                     bg=QColor("#09090a"), panel=QColor("#111113"), field=QColor("#161618"),
                     border=QColor("#1f1f23"), text=QColor("#f1f1f3"), muted=QColor("#8e8e96"),
                     wheel_bg=QColor(17, 17, 19, a), wheel_stripe=QColor(255, 255, 255, 10),
                     wheel_center=QColor(9, 9, 10, 250), wheel_line=QColor(255, 255, 255, 15),
                     danger=QColor("#ff6b78"), sidebar=QColor("#0c0c0d"), line_strong=QColor("#2c2c32"),
                     hover=QColor("#18181b"))
    return Theme(False, accent, on_accent,
                 bg=QColor("#f7f7f8"), panel=QColor("#ffffff"), field=QColor("#ffffff"),
                 border=QColor("#e6e6e9"), text=QColor("#111113"), muted=QColor("#65656d"),
                 wheel_bg=QColor(255, 255, 255, a), wheel_stripe=QColor(17, 17, 19, 10),
                 wheel_center=QColor(255, 255, 255, 252), wheel_line=QColor(17, 17, 19, 26),
                 danger=QColor("#c0263a"), sidebar=QColor("#fbfbfc"), line_strong=QColor("#d4d4d9"),
                 hover=QColor("#f0f0f2"))


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


QSS = Template("""
* { font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 10pt; color: $text; }
QWidget#window { background: $bg; }
QWidget#sidebar { background: $sidebar; border-$inner_side: 1px solid $border; }
QLabel#brand { font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 13pt; font-weight: 600; }
QListWidget#nav { background: transparent; border: none; outline: none; }
QListWidget#nav::item { padding: 7px 10px; border-radius: 8px; margin: 1px 0; color: $muted;
                        border-$lead_side: 2px solid transparent; }
QListWidget#nav::item:hover { background: $hover; color: $text; }
QListWidget#nav::item:selected { background: $hover; color: $text; border-$lead_side: 2px solid $accent; }
QListWidget#nav::item:disabled { color: $muted; background: transparent; }
QLabel#soonPill { background: transparent; border: 1px solid $line_strong; border-radius: 9px; color: $muted;
                  font-size: 7pt; font-weight: 600; padding: 0 6px; }
QLabel#h1 { font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 19pt; font-weight: 600; }
QLabel#h2 { font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 11pt; font-weight: 600; }
QLabel#muted { color: $muted; }
QLabel#unsaved { color: #e8a33d; font-weight: 600; }
QLabel#warning { color: $text; background: $field; border: 1px solid $line_strong; border-$lead_side: 2px solid #e8a33d;
                 border-radius: 8px; padding: 8px 10px; }
QLabel#step { background: $accent_soft; color: $accent; border-radius: 13px; font-weight: 700; }
QLabel#version { color: $muted; font-size: 8pt; }
QFrame#card { background: $panel; border: 1px solid $border; border-radius: 12px; }
QFrame#toolCard { background: $panel; border: 1px solid $border; border-radius: 14px; }
QFrame#toolCard:hover { border-color: $line_strong; background: $hover; }
QFrame#toolCard:focus { border-color: $accent; }
QLabel#cardTitle { font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 11.5pt; font-weight: 600; }
QLabel#cardStat { color: $accent; font-size: 8.5pt; font-weight: 600; }
QFrame#dropzone { border: 1px dashed $line_strong; border-radius: 14px; background: $panel; }
QFrame#dropzone:hover { border-color: $accent; }
QFrame#dropzone[hot="true"] { border: 1px solid $accent; background: $accent_soft; }
QLabel#section { color: $muted; font-weight: 700; font-size: 8pt; }
QPushButton#action { padding: 7px 13px; border-radius: 8px; }
QPushButton#action:hover { background: $hover; border-color: $line_strong; }
QPushButton#action:disabled { color: $muted; background: transparent; border: 1px dashed $border; }
QPushButton#small { padding: 2px 7px; min-width: 0; background: transparent; border: 1px solid transparent; color: $muted;
                    font-size: 8pt; }
QPushButton#small:hover { background: $hover; border-color: $line_strong; color: $text; }
QPushButton#small:disabled { color: $border; background: transparent; border-color: transparent; }
QTreeWidget#history, QTreeWidget#apps, QListWidget#history { background: $panel; border: 1px solid $border;
        border-radius: 12px; outline: none; padding: 4px; }
QTreeWidget#history::item, QTreeWidget#apps::item { padding: 5px 2px; }
QListWidget#history::item { padding: 8px 10px; border-radius: 7px; }
QTreeWidget#history::item:selected, QTreeWidget#apps::item:selected, QListWidget#history::item:selected {
        background: $accent_soft; color: $text; }
QTreeWidget::item:hover, QListWidget#history::item:hover { background: $hover; }
QLabel#badge { background: $hover; border: 1px solid $border; border-radius: 8px; padding: 2px 8px; font-size: 8pt; }
QHeaderView::section { background: $panel; color: $muted; border: none; border-bottom: 1px solid $border; padding: 6px; }
QFrame#line { background: $border; max-height: 1px; border: none; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: $field; border: 1px solid $line_strong; border-radius: 8px;
        padding: 5px 9px; min-height: 20px; selection-background-color: $accent; selection-color: $on_accent; }
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover { border-color: $muted; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: $accent; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow { image: url($down); width: 12px; height: 12px; }
QSpinBox, QDoubleSpinBox { padding-right: 24px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right;
        width: 22px; border: none; background: transparent; border-top-right-radius: 8px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right;
        width: 22px; border: none; background: transparent; border-bottom-right-radius: 8px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover { background: $hover; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url($up); width: 10px; height: 10px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url($down); width: 10px; height: 10px; }
QComboBox QAbstractItemView { background: $panel; border: 1px solid $line_strong; outline: none; padding: 4px;
        selection-background-color: $hover; selection-color: $text; }
QDialog { background: $bg; }
QRadioButton { spacing: 10px; padding: 3px 0; }
QRadioButton::indicator { width: 16px; height: 16px; border-radius: 9px; border: 1px solid $line_strong;
        background: $field; }
QRadioButton::indicator:hover { border-color: $muted; }
QRadioButton::indicator:checked { border: 1px solid $accent;
        background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                    stop:0 $accent, stop:0.48 $accent, stop:0.56 $field, stop:1 $field); }
QSlider::groove:horizontal { height: 4px; background: $line_strong; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::handle:horizontal { background: $text; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
QPushButton#star { background: transparent; border: none; font-size: 13pt; padding: 0 4px; color: $muted; }
QPushButton#star:checked { color: $accent; background: transparent; }
QLabel#stat { font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 20pt; font-weight: 600; color: $text; }
QPushButton { background: $field; border: 1px solid $line_strong; border-radius: 8px; padding: 7px 16px; }
QPushButton:hover { background: $hover; border-color: $muted; }
QPushButton:pressed { background: $border; }
QPushButton:checked { background: $accent_soft; color: $text; border-color: $accent; }
QPushButton:disabled { color: $muted; border-color: $border; }
QPushButton#primary { background: $accent; color: $on_accent; border: 1px solid $accent; font-weight: 600; }
QPushButton#primary:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#primary:disabled { background: $accent_soft; border-color: $accent_soft; color: $muted; }
QPushButton#danger { color: $danger; }
QPushButton#danger:hover { border-color: $danger; }
QPushButton#flat { background: transparent; border: none; color: $muted; padding: 2px 6px; }
QPushButton#tab { background: transparent; border: none; border-bottom: 2px solid transparent; border-radius: 0;
                  color: $muted; padding: 6px 12px 7px 12px; min-width: 0; font-weight: 600; }
QPushButton#tab:hover { color: $text; }
QPushButton#tab:checked { color: $text; border-bottom: 2px solid $accent; }
QFrame#tabLine { background: $border; border: none; max-height: 1px; min-height: 1px; }
QPushButton#subtab { background: transparent; border: 1px solid transparent; border-radius: 14px; color: $muted;
                     padding: 4px 14px; min-width: 0; }
QPushButton#subtab:hover { color: $text; background: $hover; }
QPushButton#subtab:checked { color: $text; background: $hover; border-color: $line_strong; }
QPushButton#flat:hover { color: $text; }
QPushButton#power { padding: 9px 18px; font-weight: 600; }
QCheckBox { spacing: 10px; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px; border: 1px solid $line_strong;
                       background: $field; }
QCheckBox::indicator:hover { border-color: $muted; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; image: url($check); }
QProgressBar { background: $border; border: none; border-radius: 2px; max-height: 4px; }
QProgressBar::chunk { background: $accent; border-radius: 2px; }
QFrame#toast { background: $panel; border: 1px solid $line_strong; border-radius: 14px; }
QFrame#clipPopup { background: $panel; border: 1px solid $line_strong; border-radius: 14px; }
QListWidget#clips { background: transparent; border: none; outline: none; }
QListWidget#clipsPage { background: $panel; border: 1px solid $border; border-radius: 12px; outline: none; padding: 4px; }
QLabel#toastTitle { font-weight: 600; }
QPushButton#close { background: transparent; border: none; color: $muted; padding: 0 4px; font-size: 11pt; }
QPushButton#close:hover { color: $text; }
QScrollArea, QWidget#scrollContent { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; border: none; }
QScrollBar::handle:vertical { background: $line_strong; border-radius: 3px; min-height: 30px; margin: 0 2px; }
QScrollBar::handle:vertical:hover { background: $muted; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; border: none; }
QScrollBar::handle:horizontal { background: $line_strong; border-radius: 3px; min-width: 30px; margin: 2px 0; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; border: none; background: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QMenu { background: $panel; border: 1px solid $line_strong; padding: 6px; }
QMenu[round="true"] { border-radius: 10px; }
QMenu::item { padding: 7px 26px 7px 12px; border-radius: 6px; }
QMenu::item:selected { background: $hover; color: $text; }
QMenu::item:disabled { color: $muted; }
QMenu::separator { height: 1px; background: $border; margin: 5px 8px; }
QToolTip { background: $panel; color: $text; border: 1px solid $line_strong; padding: 5px 8px; border-radius: 6px; }
QMessageBox { background: $bg; }
""")


def stylesheet(t: Theme, scale: float = 1.0) -> str:
    rtl = QGuiApplication.layoutDirection() == Qt.RightToLeft
    accent_hover = t.accent.lighter(112) if t.dark else t.accent.darker(112)
    css = QSS.substitute(
        text=t.text.name(), bg=t.bg.name(), panel=t.panel.name(), field=t.field.name(),
        border=t.border.name(), muted=t.muted.name(), accent=t.accent.name(),
        on_accent=t.on_accent.name(), accent_hover=accent_hover.name(),
        accent_soft=mix(t.panel, t.accent, 0.14 if t.dark else 0.12).name(),
        danger=(t.danger or QColor("#c0263a")).name(),
        sidebar=(t.sidebar or t.panel).name(), line_strong=(t.line_strong or t.border).name(),
        hover=(t.hover or t.field).name(),
        check=_check_image(t.on_accent), up=_chevron_image(t.muted, True), down=_chevron_image(t.muted, False),
        # the sidebar's edge faces the pages: on the right in English, on the left in Arabic
        inner_side="left" if rtl else "right",
        lead_side="right" if rtl else "left",
    )
    if scale != 1.0:  # bigger or smaller text everywhere ("Text size in this window")
        css = re.sub(r"(\d+(?:\.\d+)?)pt", lambda m: f"{float(m.group(1)) * scale:.1f}pt", css)
    return css


def round_menu(menu) -> None:
    """Rounded corners for a menu (Windows draws menus square unless the window is see-through)."""
    menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WA_TranslucentBackground)
    menu.setProperty("round", True)
