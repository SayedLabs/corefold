"""The base stylesheet: how Qt's own widgets look, plus a few names pages give their widgets.

Names (setObjectName) understood here:
    canvas      the rounded page area           topBar     the strip at the top of the canvas
    display     the biggest text (a greeting)   h1, h2     a page's name, a card's heading
    eyebrow     small heading above a group     muted      second-line text
    subtle      third-line text, hints          section    small heading between cards
    card        a rounded box                   group      a box of setting rows
    chip        a small rounded label           kbd        a key, like Ctrl + K
    primary     the main button (one a screen)  danger     a button that deletes or removes
    ghost       a button with no box            flat       a link-like button
    small       a small ghost button            iconButton a square button with an icon
    pillButton  a round-ended ghost button with an icon and a word or two (the language button)
    tab, subtab tabs and pill tabs              searchBox  the search field in the top strip
    segmented   a strip of segment buttons      segment    one choice in it (checkable)
    line        a 1 px divider                  warning    a box with a warning stripe
    dropdownBox, dropdownList   the open list of a Dropdown (widgets.py)
"""
from __future__ import annotations

import re
from string import Template

from PySide6.QtGui import QColor

from .tokens import FONT_DISPLAY, FONT_TEXT, RADIUS, TYPE, Palette, alpha, mix

_BASE = Template("""
* { font-family: $font_text; font-size: ${body}pt; color: $text; }
QWidget#window { background: $backdrop; }
QFrame#canvas { background: $canvas; border: none; border-radius: 0; }
QWidget#topBar { background: transparent; }
QLabel#display { font-family: $font_display; font-size: ${display}pt; font-weight: 600; }
QLabel#h1 { font-family: $font_display; font-size: ${h1}pt; font-weight: 600; }
QLabel#h2 { font-family: $font_display; font-size: ${subtitle}pt; font-weight: 600; }
QLabel#title { font-family: $font_display; font-size: ${title}pt; font-weight: 600; }
QLabel#eyebrow { color: $muted; font-size: ${caption}pt; font-weight: 600; padding: 6px 2px 0 2px; }
QLabel#section { color: $muted; font-weight: 600; font-size: ${caption_plus}pt; padding-top: 8px; }
QLabel#muted { color: $muted; }
QLabel#subtle { color: $subtle; font-size: ${caption_plus}pt; }
QLabel#version { color: $subtle; font-size: ${caption}pt; }
QLabel#unsaved { color: $warning; font-weight: 600; }
QLabel#warning { color: $text; background: $surface; border: 1px solid $line_strong; border-$lead_side: 3px solid $warning;
                 border-radius: ${r_md}px; padding: 9px 12px; }
QLabel#chip { background: $select; color: $text; border-radius: 10px; padding: 2px 10px;
              font-size: ${caption_plus}pt; font-weight: 600; }
QLabel#kbd { background: $field; color: $muted; border: 1px solid $line_strong; border-bottom-width: 2px;
             border-radius: 5px; padding: 0 5px; font-size: ${caption}pt; font-weight: 600; }
QLabel#badge { background: $hover; border: 1px solid $border; border-radius: ${r_md}px; padding: 2px 8px;
               font-size: ${caption}pt; }
QLabel#step { background: $select; color: $text; border-radius: 13px; font-weight: 700; }
QLabel#stat { font-family: $font_display; font-size: ${h1}pt; font-weight: 600; color: $text; }
QFrame#card, QFrame#group { background: $surface; border: 1px solid $surface; border-radius: ${r_lg}px; }
QFrame#line { background: $border; max-height: 1px; min-height: 1px; border: none; }
QFrame#tabLine { background: $border; border: none; max-height: 1px; min-height: 1px; }
QFrame#toast { background: $raised; border: 1px solid $line_strong; border-radius: ${r_xl}px; }

QPushButton { background: $field; border: 1px solid $line_strong; border-bottom-color: $line_bottom;
              border-radius: ${r_md}px; padding: 7px 15px; text-align: center; }
QPushButton:hover { background: $hover; }
QPushButton:pressed { background: $pressed; border-bottom-color: $line_strong; }
QPushButton:checked { background: $select; color: $text; border-color: $line_strong; }
QPushButton:disabled { color: $subtle; border-color: $border; background: transparent; }
QPushButton:focus { outline: none; }
/* ("text-align" makes Qt lay out an icon and its words itself, with a real gap between them: without it the
   words touched the icon, and overlapped it in Arabic) */
/* a button with a menu: the arrow on its trailing side (Qt's own one lands outside the button in Arabic) */
QPushButton::menu-indicator { image: url($down); width: 10px; height: 10px; subcontrol-origin: padding;
        subcontrol-position: center right; right: 7px; }
QPushButton#primary { background: $primary; color: $on_primary; border: 1px solid $primary; border-bottom-color: $primary_edge;
                      font-weight: 600; }
QPushButton#primary:hover { background: $primary_hover; border-color: $primary_hover; }
QPushButton#primary:pressed { background: $primary_edge; }
QPushButton#primary:disabled { background: $select; border-color: $select; color: $subtle; }
QPushButton#danger { color: $danger; }
QPushButton#danger:hover { border-color: $danger; background: $danger_soft; }
QPushButton#ghost { background: transparent; border: 1px solid transparent; }
QPushButton#ghost:hover { background: $hover; }
QPushButton#ghost:pressed { background: $pressed; }
QPushButton#flat { background: transparent; border: none; color: $text; padding: 3px 6px; font-weight: 600; }
QPushButton#flat:hover { color: $accent_text; }
QPushButton#flat:disabled { color: $subtle; font-weight: 600; }
QPushButton#danger:disabled { color: $subtle; }
QPushButton#small { padding: 2px 8px; min-width: 0; background: transparent; border: 1px solid transparent;
                    color: $muted; font-size: ${caption}pt; }
QPushButton#small:hover { background: $hover; border-color: $line_strong; color: $text; }
QPushButton#small:disabled { color: $border; background: transparent; border-color: transparent; }
QPushButton#iconButton { padding: 0; min-width: 0; border-radius: ${r_md}px; background: transparent;
                         border: 1px solid transparent; }
QPushButton#iconButton:hover { background: $hover; }
QPushButton#iconButton:pressed { background: $pressed; }
QPushButton#iconButton:disabled { background: transparent; }
QPushButton#pillButton { padding: 0 12px; min-width: 0; border-radius: 17px; background: transparent;
        border: 1px solid transparent; color: $muted; font-weight: 600; }
QPushButton#pillButton:hover { background: $hover; color: $text; border-color: $line_strong; }
QPushButton#pillButton:pressed { background: $pressed; }
QPushButton#tab { background: transparent; border: none; border-bottom: 2px solid transparent; border-radius: 0;
                  color: $muted; padding: 6px 12px 7px 12px; min-width: 0; font-weight: 600; }
QPushButton#tab:hover { color: $text; }
QPushButton#tab:checked { color: $text; border-bottom: 2px solid $text; }
QPushButton#subtab { background: transparent; border: 1px solid transparent; border-radius: 12px; color: $muted;
                     padding: 4px 14px; min-width: 0; font-weight: 600; }
QPushButton#subtab:hover { color: $text; background: $hover; }
QPushButton#subtab:checked { color: $text; background: $surface; border-color: $line_strong; }
QFrame#segmented { background: $segmented; border: 1px solid $border; border-radius: 10px; }
QPushButton#segment { background: transparent; border: 1px solid transparent; border-radius: 7px; color: $muted;
                      padding: 5px 14px; min-width: 0; font-weight: 600; }
QPushButton#segment:hover { color: $text; }
QPushButton#segment:checked { color: $text; background: $raised; border-color: $line_strong; }

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {
        background: $field; border: 1px solid $line_strong; border-bottom-color: $line_bottom; border-radius: ${r_md}px;
        padding: 5px 10px; min-height: 20px; selection-background-color: $accent; selection-color: $on_accent; }
QTextEdit, QPlainTextEdit { padding: 6px; }
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { background: $hover; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {
        border-color: $accent; background: $field; }
QComboBox:focus { background: $field; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { color: $subtle; background: transparent; }
QLineEdit#searchBox { border-radius: 16px; padding: 6px 12px 6px 34px; background: $field; }
QLineEdit#searchBox:focus { border: 1px solid $accent; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox::down-arrow { image: url($down); width: 12px; height: 12px; }
QComboBox QAbstractItemView { background: $raised; border: 1px solid $line_strong; outline: none; padding: 4px;
        selection-background-color: $hover; selection-color: $text; }
QComboBox { combobox-popup: 0; }
QFrame#dropdownBox { background: transparent; border: none; margin: 0; padding: 0; }
QListView#dropdownList { background: $raised; border: 1px solid $line_strong; border-radius: ${r_lg}px; padding: 4px 0;
        outline: none; }
QSpinBox, QDoubleSpinBox { padding-right: 24px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right;
        width: 22px; border: none; background: transparent; border-top-right-radius: ${r_md}px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right;
        width: 22px; border: none; background: transparent; border-bottom-right-radius: ${r_md}px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover { background: $hover; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url($up); width: 10px; height: 10px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url($down); width: 10px; height: 10px; }

QCheckBox { spacing: 10px; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px; border: 1px solid $line_strong; background: $field; }
QCheckBox::indicator:hover { border-color: $muted; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; image: url($check); }
QCheckBox::indicator:disabled { background: transparent; border-color: $border; }
QRadioButton { spacing: 10px; padding: 3px 0; }
QRadioButton::indicator { width: 16px; height: 16px; border-radius: 9px; border: 1px solid $line_strong; background: $field; }
QRadioButton::indicator:hover { border-color: $muted; }
QRadioButton::indicator:checked { border: 1px solid $accent;
        background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                    stop:0 $accent, stop:0.45 $accent, stop:0.55 $field, stop:1 $field); }
QSlider::groove:horizontal { height: 4px; background: $line_strong; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::handle:horizontal { background: $knob; border: 4px solid $knob_ring; width: 10px; height: 10px; margin: -7px 0;
                             border-radius: 9px; }
QSlider::handle:horizontal:hover { border-width: 3px; }
QProgressBar { background: $line_strong; border: none; border-radius: 2px; max-height: 4px; }
QProgressBar::chunk { background: $accent; border-radius: 2px; }

QTreeView, QListView, QTableView { background: $surface; border: 1px solid $border; border-radius: ${r_lg}px; outline: none;
        alternate-background-color: $canvas; }
QTreeView::item, QListView::item { padding: 5px 2px; }
QTreeView::item:hover, QListView::item:hover { background: $hover; }
QTreeView::item:selected, QListView::item:selected { background: $select; color: $text; }
QTreeView::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid $line_strong; background: $field; }
QTreeView::indicator:hover { border-color: $muted; }
QTreeView::indicator:checked { background: $accent; border-color: $accent; image: url($check); }
QTreeView::indicator:indeterminate { background: $select; border-color: $muted; }
QHeaderView::section { background: $surface; color: $muted; border: none; border-bottom: 1px solid $border; padding: 7px 6px;
        font-weight: 600; font-size: ${caption_plus}pt; }
QHeaderView::section:hover { color: $text; }
/* the sort mark sits beside the column's name, small: ^ for A to Z, as in File Explorer */
QHeaderView::down-arrow, QHeaderView::up-arrow { width: 9px; height: 9px; subcontrol-origin: padding;
        subcontrol-position: center right; right: 6px; }
QHeaderView::up-arrow { image: url($up); }
QHeaderView::down-arrow { image: url($down); }
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 8px; }
QSplitter::handle:vertical { height: 8px; }
QSplitter::handle:hover { background: $border; }

QScrollArea, QWidget#scrollContent { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; border: none; }
QScrollBar::handle:vertical { background: $line_strong; border-radius: 3px; min-height: 30px; margin: 0 2px; }
QScrollBar::handle:vertical:hover { background: $subtle; margin: 0 1px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; border: none; }
QScrollBar::handle:horizontal { background: $line_strong; border-radius: 3px; min-width: 30px; margin: 2px 0; }
QScrollBar::handle:horizontal:hover { background: $subtle; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; border: none; background: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

QMenu { background: $raised; border: 1px solid $line_strong; padding: 6px; }
QMenu[round="true"] { border-radius: ${r_lg}px; }
QMenu::item { padding: 7px 28px 7px 12px; border-radius: ${r_sm}px; }
QMenu::item:selected { background: $hover; color: $text; }
QMenu::item:disabled { color: $subtle; }
QMenu::separator { height: 1px; background: $border; margin: 5px 8px; }
QToolTip { background: $raised; color: $text; border: 1px solid $line_strong; padding: 5px 9px; border-radius: ${r_sm}px; }
QDialog, QMessageBox { background: $canvas; }
""")


def base(p: Palette, rtl: bool = False, check: str = "", up: str = "", down: str = "") -> str:
    """The stylesheet for palette p. check/up/down are image paths for the tick and the arrows."""
    return _BASE.substitute(
        font_text=FONT_TEXT, font_display=FONT_DISPLAY,
        body=TYPE["body"], caption=TYPE["caption"], caption_plus=TYPE["caption"] + 0.5, subtitle=TYPE["subtitle"],
        title=TYPE["title"], h1=TYPE["h1"], display=TYPE["display"],
        r_sm=RADIUS["sm"], r_md=RADIUS["md"], r_lg=RADIUS["lg"], r_xl=RADIUS["xl"], r_canvas=RADIUS["canvas"],
        text=p.text.name(), muted=p.muted.name(), subtle=p.subtle.name(),
        backdrop=p.backdrop.name(), canvas=p.canvas.name(), surface=p.surface.name(), raised=p.raised.name(),
        field=p.field.name(), hover=p.hover.name(), pressed=p.pressed.name(),
        segmented=(p.backdrop if p.dark else p.pressed).name(),
        border=p.border.name(), line_strong=p.line_strong.name(),
        line_bottom=(p.line_strong if p.dark else mix(p.line_strong, p.text, 0.12)).name(),
        accent=p.accent.name(), on_accent=p.on_accent.name(), accent_soft=p.accent_soft.name(),
        select=p.select.name(), primary=p.primary.name(), on_primary=p.on_primary.name(),
        primary_hover=p.primary_hover.name(), primary_edge=p.primary.darker(118 if not p.dark else 110).name(),
        accent_text=(p.accent if p.dark else p.accent.darker(108)).name(),
        danger=p.danger.name(), danger_soft=mix(p.surface, p.danger, 0.1).name(), warning=p.warning.name(),
        knob=(p.text if p.dark else QColor("#ffffff")).name(), knob_ring=p.accent.name(),
        check=check, up=up, down=down,
        lead_side="right" if rtl else "left",
    )


def scaled(css: str, scale: float) -> str:
    """Every point size times scale (bigger or smaller text everywhere)."""
    if scale == 1.0:
        return css
    return re.sub(r"(\d+(?:\.\d+)?)pt", lambda m: f"{float(m.group(1)) * scale:.1f}pt", css)


__all__ = ["base", "scaled", "alpha"]
