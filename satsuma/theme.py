"""Colours and the app-wide stylesheet. Light/dark follows Windows unless the user picks one."""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from string import Template

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap

from . import winutil
from .settings import ACCENTS


@dataclass
class Theme:
    dark: bool
    accent: QColor
    on_accent: QColor    # text on top of the accent colour
    bg: QColor           # window background
    panel: QColor        # cards, sidebar, popups
    field: QColor        # inputs and buttons
    border: QColor
    text: QColor
    muted: QColor
    wheel_bg: QColor
    wheel_stripe: QColor
    wheel_center: QColor
    wheel_line: QColor


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


def build_theme(appearance: str, accent_key: str, custom_color: str = "#ff8c1a", opacity: int = 94) -> Theme:
    dark = appearance == "dark" or (appearance == "system" and system_is_dark())
    if accent_key == "custom" and QColor(custom_color).isValid():
        accent = QColor(custom_color)
    else:
        accent = QColor(ACCENTS.get(accent_key, ACCENTS["tangerine"])[1])
    lum = 0.2126 * accent.red() + 0.7152 * accent.green() + 0.0722 * accent.blue()
    on_accent = QColor("#141417") if lum > 150 else QColor("#ffffff")
    if dark:
        return Theme(True, accent, on_accent,
                     bg=QColor("#151517"), panel=QColor("#1d1d21"), field=QColor("#26262b"),
                     border=QColor("#34343b"), text=QColor("#ececf0"), muted=QColor("#9a9aa6"),
                     wheel_bg=QColor(22, 22, 25, round(255 * opacity / 100)), wheel_stripe=QColor(255, 255, 255, 14),
                     wheel_center=QColor(12, 12, 14, 250), wheel_line=QColor(255, 255, 255, 28))
    return Theme(False, accent, on_accent,
                 bg=QColor("#f4f4f7"), panel=QColor("#ffffff"), field=QColor("#ffffff"),
                 border=QColor("#dcdce3"), text=QColor("#1b1b20"), muted=QColor("#6b6b78"),
                 wheel_bg=QColor(252, 252, 253, round(255 * opacity / 100)), wheel_stripe=QColor(0, 0, 0, 12),
                 wheel_center=QColor(255, 255, 255, 252), wheel_line=QColor(0, 0, 0, 30))


def _check_image(color: QColor) -> str:
    """Checkbox tick drawn in the given colour, saved as a PNG for the stylesheet."""
    path = Path(tempfile.gettempdir()) / f"satsuma-check-{color.name()[1:]}.png"
    if not path.exists():
        pm = QPixmap(36, 36)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(color, 4.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        tick = QPainterPath(QPointF(9, 19))
        tick.lineTo(15.5, 25.5)
        tick.lineTo(27, 11)
        p.drawPath(tick)
        p.end()
        pm.save(str(path), "PNG")
    return path.as_posix()


QSS = Template("""
* { font-family: "Segoe UI"; font-size: 10pt; color: $text; }
QWidget#window { background: $bg; }
QWidget#sidebar { background: $panel; border-right: 1px solid $border; }
QLabel#brand { font-size: 13pt; font-weight: 700; }
QListWidget#nav { background: transparent; border: none; outline: none; }
QListWidget#nav::item { padding: 9px 12px; border-radius: 8px; margin: 1px 0; color: $muted; }
QListWidget#nav::item:hover { background: $field; color: $text; }
QListWidget#nav::item:selected { background: $accent_soft; color: $text; }
QLabel#h1 { font-size: 18pt; font-weight: 600; }
QLabel#h2 { font-size: 11pt; font-weight: 600; }
QLabel#muted { color: $muted; }
QLabel#step { background: $accent; color: $on_accent; border-radius: 13px; font-weight: 700; }
QLabel#version { color: $muted; font-size: 9pt; }
QFrame#card { background: $panel; border: 1px solid $border; border-radius: 12px; }
QFrame#dropzone { border: 2px dashed $border; border-radius: 14px; background: $panel; }
QFrame#dropzone:hover { border-color: $accent; }
QFrame#line { background: $border; max-height: 1px; border: none; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: $field; border: 1px solid $border; border-radius: 7px;
                      padding: 5px 9px; min-height: 20px; }
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover, QLineEdit:focus { border-color: $accent; }
QDialog { background: $bg; }
QRadioButton { spacing: 10px; padding: 3px 0; }
QRadioButton::indicator { width: 16px; height: 16px; border-radius: 9px; border: 1px solid $border; background: $field; }
QRadioButton::indicator:checked { border: 5px solid $accent; background: $on_accent; }
QSlider::groove:horizontal { height: 6px; background: $field; border: 1px solid $border; border-radius: 3px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 3px; }
QSlider::handle:horizontal { background: $accent; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }
QListWidget#history { background: $panel; border: 1px solid $border; border-radius: 12px; outline: none; padding: 4px; }
QListWidget#history::item { padding: 8px 10px; border-radius: 7px; }
QListWidget#history::item:selected { background: $accent_soft; color: $text; }
QPushButton#star { background: transparent; border: none; font-size: 13pt; padding: 0 4px; color: $muted; }
QPushButton#star:checked { color: $accent; background: transparent; }
QLabel#stat { font-size: 20pt; font-weight: 700; color: $accent; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: $panel; border: 1px solid $border; outline: none;
                              selection-background-color: $accent; selection-color: $on_accent; }
QPushButton { background: $field; border: 1px solid $border; border-radius: 7px; padding: 7px 16px; }
QPushButton:hover { border-color: $accent; }
QPushButton:checked { background: $accent; color: $on_accent; border-color: $accent; font-weight: 600; }
QPushButton:disabled { color: $muted; }
QPushButton#primary { background: $accent; color: $on_accent; border: none; font-weight: 600; }
QPushButton#primary:hover { background: $accent_hover; }
QPushButton#danger { color: #e5484d; }
QPushButton#danger:hover { border-color: #e5484d; }
QPushButton#flat { background: transparent; border: none; color: $muted; padding: 2px 6px; }
QPushButton#flat:hover { color: $text; }
QPushButton#power { padding: 10px 18px; font-weight: 600; }
QCheckBox { spacing: 10px; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px; border: 1px solid $border;
                       background: $field; }
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; image: url($check); }
QProgressBar { background: $field; border: none; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: $accent; border-radius: 3px; }
QFrame#toast { background: $panel; border: 1px solid $border; border-radius: 12px; }
QLabel#toastTitle { font-weight: 600; }
QPushButton#close { background: transparent; border: none; color: $muted; padding: 0 4px; font-size: 11pt; }
QPushButton#close:hover { color: $text; }
QScrollArea, QWidget#scrollContent { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $border; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QMenu { background: $panel; border: 1px solid $border; padding: 6px; }
QMenu::item { padding: 6px 24px 6px 12px; border-radius: 5px; }
QMenu::item:selected { background: $accent; color: $on_accent; }
QMenu::separator { height: 1px; background: $border; margin: 5px 8px; }
QToolTip { background: $panel; color: $text; border: 1px solid $border; padding: 4px; }
QMessageBox { background: $bg; }
""")


def stylesheet(t: Theme) -> str:
    return QSS.substitute(
        text=t.text.name(), bg=t.bg.name(), panel=t.panel.name(), field=t.field.name(),
        border=t.border.name(), muted=t.muted.name(), accent=t.accent.name(),
        on_accent=t.on_accent.name(), accent_hover=t.accent.lighter(112).name(),
        accent_soft=mix(t.panel, t.accent, 0.22).name(), check=_check_image(t.on_accent),
    )
