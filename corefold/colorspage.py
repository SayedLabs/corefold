"""Corefold Colors' page: pick a colour, how it's copied, its shortcut, and the colours picked lately."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, \
    QScrollArea, QVBoxLayout, QWidget

from . import colors, sheet
from .atomic.widgets import Dropdown
from .i18n import ltr, tr
from .settings import COLORS_FORMATS, COLORS_SHORTCUTS
from .window import Segmented, _alive, button, card, label

if TYPE_CHECKING:
    from .app import Controller

PER_ROW = 12   # swatches on a line


class ColorsPage(QScrollArea):
    def __init__(self, controller: "Controller", header):
        super().__init__()
        self.c = controller
        self.selected = ""   # the colour shown big ("#RRGGBB")
        self._newest = ""    # (a colour just picked is shown big)
        self.swatches: list[QPushButton] = []
        inner = QWidget()
        inner.setObjectName("scrollContent")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(14)
        lay.addLayout(header)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setWidget(inner)
        self.viewport().setAutoFillBackground(False)
        s = self.c.settings

        # picking, and how
        box, bl = card()
        row = QHBoxLayout()
        row.setSpacing(12)
        self.pick_btn = button(tr("Pick a colour"), "primary", self.pick)
        row.addWidget(self.pick_btn)
        self.key_hint = label("", "muted", wrap=True)
        row.addWidget(self.key_hint, 1)
        bl.addLayout(row)
        bl.addWidget(label(tr("Click or press Enter to pick. The arrow keys move one pixel, the mouse wheel zooms. "
                              "Esc cancels."), "muted", wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        self.format = Segmented([(k, v) for k, v in COLORS_FORMATS.items()], s.colors_format, per_row=7)
        self.format.changed.connect(self._format_changed)
        self.shortcut = Dropdown()
        for key, text in COLORS_SHORTCUTS.items():
            self.shortcut.addItem(tr(text) if key == "off" else text, key)
        self.shortcut.setCurrentIndex(max(0, self.shortcut.findData(s.colors_shortcut)))
        self.shortcut.currentIndexChanged.connect(self._shortcut_changed)
        grid.addWidget(label(tr("Copy as")), 0, 0)
        grid.addWidget(self.format, 0, 1)
        grid.addWidget(label(tr("Shortcut")), 1, 0)
        grid.addWidget(self.shortcut, 1, 1, Qt.AlignLeft)
        grid.setColumnStretch(2, 1)
        bl.addLayout(grid)
        self.key_note = label(tr("Another app already uses this shortcut. Pick another one."), "warning", wrap=True)
        bl.addWidget(self.key_note)
        lay.addWidget(box)

        # the colours picked lately
        box, bl = card(10)
        head = QHBoxLayout()
        head.addWidget(label(tr("Your colours"), "h2"))
        head.addStretch()
        self.clear_btn = button(tr("Clear all"), "flat", self._clear)
        head.addWidget(self.clear_btn)
        bl.addLayout(head)
        self.grid_box = QWidget()
        self.grid = QGridLayout(self.grid_box)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self.grid.setColumnStretch(PER_ROW, 1)
        bl.addWidget(self.grid_box)
        self.empty = label(tr("Colours you pick show up here."), "muted", wrap=True)
        bl.addWidget(self.empty)
        # a colour of one's own: typed or pasted, in any of the usual ways
        self.typed = QLineEdit()
        self.typed.setPlaceholderText(tr("Add a colour: #1E90FF, rgb(30, 144, 255), hsl(…) or a name"))
        self.typed.setClearButtonEnabled(True)
        self.typed.setMaximumWidth(420)
        self.typed.returnPressed.connect(self._add_typed)
        self.typed.textChanged.connect(lambda _t: self.typed_note.hide())
        bl.addWidget(self.typed)
        self.typed_note = label(tr("That isn't a colour Corefold knows. Try #1E90FF or rgb(30, 144, 255)."),
                                "warning", wrap=True)
        self.typed_note.hide()
        bl.addWidget(self.typed_note)
        self.detail = QWidget()
        outer = QVBoxLayout(self.detail)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(14)
        dl = QHBoxLayout()
        dl.setSpacing(18)
        outer.addLayout(dl)
        self.big = QLabel()
        self.big.setFixedSize(112, 112)
        dl.addWidget(self.big, 0, Qt.AlignTop)
        rows = QGridLayout()
        rows.setHorizontalSpacing(14)
        rows.setVerticalSpacing(8)
        self.values: dict[str, QLabel] = {}
        self.copy_buttons: dict[str, QPushButton] = {}
        for i, (fmt, name) in enumerate(COLORS_FORMATS.items()):
            rows.addWidget(label(name, "muted"), i, 0)
            value = label("")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.values[fmt] = value
            rows.addWidget(value, i, 1)
            b = button(tr("Copy"), None, lambda _=False, f=fmt: self._copy(f))
            self.copy_buttons[fmt] = b
            rows.addWidget(b, i, 2)
        n = len(COLORS_FORMATS)
        # how readable text is on it (WCAG: 4.5:1 for text, 3:1 for large text)
        rows.addWidget(label(tr("Text on it"), "muted"), n, 0)
        samples = QHBoxLayout()
        samples.setSpacing(8)
        self.samples: dict[str, QLabel] = {}
        for ink in ("#FFFFFF", "#000000"):
            lab = QLabel()
            lab.setContentsMargins(10, 4, 10, 4)
            self.samples[ink] = lab
            samples.addWidget(lab)
        samples.addStretch()
        rows.addLayout(samples, n, 1)
        self.remove_btn = button(tr("Remove"), "flat", self._remove)
        rows.addWidget(self.remove_btn, n + 1, 2)
        rows.setColumnStretch(1, 1)
        dl.addLayout(rows, 1)
        # darker and lighter versions of it (a click shows that one, ready to copy)
        strip = QHBoxLayout()
        strip.setSpacing(4)
        strip.addWidget(label(tr("Shades"), "muted"))
        strip.addSpacing(10)
        self.shade_buttons: list[QPushButton] = []
        for _ in range(len(colors.shades("#808080"))):
            b = QPushButton()
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(30, 30)
            b.clicked.connect(lambda _=False, b=b: self.show_color(b.toolTip()))
            strip.addWidget(b)
            self.shade_buttons.append(b)
        strip.addStretch()
        outer.addLayout(strip)
        bl.addWidget(self.detail)
        lay.addWidget(box)
        lay.addStretch()

        tool = self.c.colors
        if tool is not None:
            tool.changed.connect(self.refresh)
        self.refresh()

    @property
    def history(self) -> list[str]:
        return list(self.c.settings.colors_history)

    # ---- picking

    def pick(self):
        if self.c.colors is not None:
            self.c.colors.pick()

    def _format_changed(self, fmt: str):
        self.c.settings.colors_format = fmt
        self.c.settings_changed()

    def _shortcut_changed(self, *_):
        self.c.settings.colors_shortcut = self.shortcut.currentData()
        self.c.settings_changed()
        self.refresh_key()

    def refresh_key(self):
        from .hotkey import display
        s, tool = self.c.settings, self.c.colors
        if s.colors_shortcut == "off":
            self.key_hint.setText(tr("Pick a shortcut below to pick colours from any app."))
        else:
            key = COLORS_SHORTCUTS.get(s.colors_shortcut) or display(s.colors_shortcut)
            self.key_hint.setText(tr("Or press {key} in any app.", key=key))
        self.key_note.setVisible(tool is not None and s.colors_shortcut != "off" and not tool.key.ok)

    # ---- the colours picked lately

    def refresh(self):
        self.refresh_key()
        history = self.history
        newest = history[0] if history else ""
        if self.selected not in history or newest != self._newest:
            self.selected = newest
        self._newest = newest
        t = self.c.theme
        for b in self.swatches:
            b.hide()
            b.deleteLater()
        self.swatches = []
        for i, hex_ in enumerate(history):
            b = QPushButton()
            b.setCursor(Qt.PointingHandCursor)
            b.setCheckable(True)
            b.setChecked(hex_ == self.selected)
            b.setFixedSize(34, 34)
            b.setToolTip(hex_)
            b.setAccessibleName(hex_)
            b.setStyleSheet(
                f"QPushButton {{ background: {hex_}; border: 1px solid {t.border.name()}; border-radius: 17px;"
                f" padding: 0; min-width: 0; }}"   # (the line shows white on a light page, black on a dark one)
                f"QPushButton:hover {{ border: 2px solid {t.muted.name()}; }}"
                f"QPushButton:checked {{ border: 3px solid {t.text.name()}; }}")
            b.clicked.connect(lambda _=False, h=hex_: self.show_color(h))
            self.grid.addWidget(b, i // PER_ROW, i % PER_ROW)
            self.swatches.append(b)
        self.grid_box.setVisible(bool(history))
        self.empty.setVisible(not history)
        self.clear_btn.setEnabled(bool(history))
        self._fill_detail()

    def show_color(self, hex_: str):
        self.selected = hex_
        for b in self.swatches:
            b.setChecked(b.toolTip() == hex_)
        self._fill_detail()

    def _fill_detail(self):
        hex_ = self.selected
        self.detail.setVisible(bool(hex_))
        if not hex_:
            return
        t = self.c.theme
        self.big.setStyleSheet(f"background: {hex_}; border: 1px solid {t.border.name()}; border-radius: 16px;")
        for fmt, value in self.values.items():
            value.setText(ltr(colors.as_text(hex_, fmt)))
        for b in self.copy_buttons.values():
            b.setText(tr("Copy"))
        for ink, lab in self.samples.items():
            ratio = colors.contrast(hex_, ink)
            grade = "AAA" if ratio >= 7 else "AA" if ratio >= 4.5 else tr("Large text only") if ratio >= 3 \
                else tr("Hard to read")
            lab.setText(ltr(f"Aa  {ratio:.1f}:1") + "  ·  " + grade)
            lab.setToolTip(tr("Contrast {ratio}:1 with {which} text. 4.5:1 is enough for text, 3:1 for large text.",
                              ratio=f"{ratio:.1f}", which=tr("white") if ink == "#FFFFFF" else tr("black")))
            lab.setStyleSheet(f"background: {hex_}; color: {ink}; border: 1px solid {t.border.name()};"
                              f" border-radius: 6px; font-weight: 600;")
        seen = set()
        for b, shade in zip(self.shade_buttons, colors.shades(hex_)):
            b.setVisible(shade not in seen)  # (black and white have fewer)
            seen.add(shade)
            b.setToolTip(shade)
            b.setAccessibleName(shade)
            edge = f"3px solid {t.text.name()}" if shade == hex_.upper() else f"1px solid {t.border.name()}"
            b.setStyleSheet(f"QPushButton {{ background: {shade}; border: {edge}; border-radius: 6px; padding: 0;"
                            f" min-width: 0; }} QPushButton:hover {{ border: 2px solid {t.muted.name()}; }}")

    def _add_typed(self):
        hex_ = colors.parse(self.typed.text())
        if not hex_:
            self.typed_note.show()
            return
        self.typed.clear()
        if self.c.colors is not None:
            self.c.colors.remember(hex_)
        self.refresh()
        self.show_color(hex_)

    def _copy(self, fmt: str):
        if not self.selected:
            return
        if self.c.colors is not None:
            self.c.colors.copy(self.selected, fmt)
        b = self.copy_buttons[fmt]
        b.setText(tr("Copied"))
        QTimer.singleShot(1500, lambda: _alive(b) and b.setText(tr("Copy")))

    def _remove(self):
        if self.selected and self.c.colors is not None:
            self.c.colors.forget(self.selected)

    def _clear(self):
        if not self.history or self.c.colors is None:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("Clear all your colours?"))
        box.setText(tr("The colours you picked are forgotten. This can't be undone."))
        yes = box.addButton(tr("Clear"), QMessageBox.DestructiveRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        sheet.run(box)
        if box.clickedButton() is yes:
            self.c.colors.clear()

    def apply_theme(self, theme):
        self.refresh()
