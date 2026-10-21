"""The Satsuma window: Home, General, Look, Formats, History, Updates and About pages."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QComboBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QScrollArea, QSlider, QSpinBox, QStackedWidget, QSystemTrayIcon, QVBoxLayout,
                               QWidget)

from . import __version__
from .converters import CATEGORY_NAMES, catalog, human_size
from .settings import (ACCENTS, APP_NAME, LOG_DIR, SHORTCUT_LABELS, SOUNDS, WHEEL_STYLES, installed_uninstaller,
                       set_startup, startup_enabled)
from .theme import Theme
from .wheel import WheelPreview
from .winutil import reveal_in_explorer, set_title_bar_dark

if TYPE_CHECKING:
    from .app import Controller

PAGES = ["Home", "General", "Look", "Formats", "History", "Updates", "About"]


# ----------------------------------------------------------------------------- small building blocks

def label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def button(text: str, name: str | None = None, on_click=None) -> QPushButton:
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    if on_click:
        b.clicked.connect(on_click)
    return b


def checkbox(text: str, checked: bool, on_toggle=None) -> QCheckBox:
    cb = QCheckBox(text)
    cb.setCursor(Qt.PointingHandCursor)
    cb.setChecked(checked)
    if on_toggle:
        cb.toggled.connect(on_toggle)
    return cb


def card(spacing: int = 12) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(spacing)
    return f, lay


def page(title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
    """A scrollable page with a big title."""
    inner = QWidget()
    inner.setObjectName("scrollContent")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(0, 0, 10, 0)
    lay.setSpacing(14)
    lay.addWidget(label(title, "h1"))
    if subtitle:
        lay.addWidget(label(subtitle, "muted", wrap=True))
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(inner)
    scroll.viewport().setAutoFillBackground(False)
    return scroll, lay


def time_ago(ts: float) -> str:
    d = time.time() - ts
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    if d < 7 * 86400:
        return f"{int(d // 86400)} days ago" if d >= 2 * 86400 else "yesterday"
    return time.strftime("%b %d", time.localtime(ts))


class Segmented(QWidget):
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], value: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.keys = [k for k, _ in options]
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for i, (key, text) in enumerate(options):
            b = button(text)
            b.setCheckable(True)
            b.setChecked(key == value)
            self.group.addButton(b, i)
            lay.addWidget(b)
        lay.addStretch()
        self.group.idClicked.connect(lambda i: self.changed.emit(self.keys[i]))


class Swatches(QWidget):
    """Accent colour picker: six presets plus a custom colour."""
    changed = Signal(str)          # accent key
    custom_picked = Signal(str)    # "#rrggbb"

    def __init__(self, value: str, custom: str):
        super().__init__()
        self.custom = custom
        self.value = value
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        keys = list(ACCENTS) + ["custom"]
        for i, key in enumerate(keys):
            b = button("")
            b.setCheckable(True)
            b.setChecked(key == value)
            b.setFixedSize(32, 32)
            b.setToolTip(ACCENTS[key][0] if key in ACCENTS else "Pick your own colour")
            self.group.addButton(b, i)
            self.buttons[key] = b
            lay.addWidget(b)
        lay.addStretch()
        self.group.idClicked.connect(lambda i: self._clicked(keys[i]))
        self.theme: Theme | None = None

    def _clicked(self, key):
        if key == "custom":
            c = QColorDialog.getColor(QColor(self.custom), self.window(), "Pick a colour")
            if not c.isValid():  # cancelled: go back to the colour that was selected
                self.buttons.get(self.value, self.buttons["tangerine"]).setChecked(True)
                return
            self.custom = c.name()
            self.custom_picked.emit(self.custom)
        self.value = key
        self.changed.emit(key)

    def apply_theme(self, t: Theme):
        self.theme = t
        for key, b in self.buttons.items():
            color = ACCENTS[key][1] if key in ACCENTS else self.custom
            text = ""
            if key == "custom":
                b.setText("+")
                text = f"color: {'#000' if QColor(color).lightness() > 150 else '#fff'}; font-size: 14pt;"
            b.setStyleSheet(
                f"QPushButton {{ background: {color}; border: 3px solid {t.panel.name()}; border-radius: 16px;"
                f" padding: 0; {text} }}"
                f"QPushButton:hover {{ border-color: {t.border.name()}; }}"
                f"QPushButton:checked {{ background: {color}; border: 3px solid {t.text.name()}; }}")


class DropZone(QFrame):
    """Drag any file over this box to open the wheel, no key needed."""
    entered = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(104)
        lay = QVBoxLayout(self)
        a = label("Try it here: drag any file onto this box", "h2")
        b = label("Images, audio, video, PDFs and Office files. Your original file is never changed.", "muted")
        for w in (a, b):
            w.setAlignment(Qt.AlignCenter)
        lay.addStretch()
        lay.addWidget(a)
        lay.addWidget(b)
        lay.addStretch()

    def dragEnterEvent(self, e):
        if any(u.isLocalFile() for u in e.mimeData().urls()):
            self.entered.emit()  # the wheel pops up under the cursor and takes over the drag
        e.ignore()


# ----------------------------------------------------------------------------- the window

class MainWindow(QWidget):
    def __init__(self, controller: "Controller", icon: QIcon):
        super().__init__()
        self.c = controller
        self.icon = icon
        self.told_about_tray = False
        self.setObjectName("window")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icon)
        self.resize(960, 660)
        self.setMinimumSize(860, 560)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setAttribute(Qt.WA_StyledBackground)
        sidebar.setFixedWidth(200)
        logo = QLabel()
        logo.setPixmap(icon.pixmap(30, 30))
        brand = QHBoxLayout()
        brand.addWidget(logo)
        brand.addWidget(label(APP_NAME, "brand"))
        brand.addStretch()
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.addItems(PAGES)
        self.nav.setCursor(Qt.PointingHandCursor)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 18, 14, 14)
        side.setSpacing(14)
        side.addLayout(brand)
        side.addWidget(self.nav, 1)
        side.addWidget(label(f"Version {__version__}", "version"))

        self.stack = QStackedWidget()
        for build in (self._home, self._general, self._look, self._formats, self._history, self._updates,
                      self._about):
            self.stack.addWidget(build())
        self.nav.currentRowChanged.connect(self._page_changed)
        self.nav.setCurrentRow(0)

        footer = QHBoxLayout()
        footer.addWidget(label("Changes are saved automatically.", "muted"))
        footer.addStretch()
        footer.addWidget(button("Done", "primary", self.close))
        content = QVBoxLayout()
        content.setContentsMargins(30, 24, 20, 18)
        content.setSpacing(12)
        content.addWidget(self.stack, 1)
        content.addLayout(footer)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(sidebar)
        root.addLayout(content, 1)

        self.refresh()
        self.refresh_updates()
        self.refresh_history()

    def _page_changed(self, i: int):
        self.stack.setCurrentIndex(i)
        if PAGES[i] == "History":
            self.refresh_history()

    # ---- pages

    def _home(self) -> QWidget:
        w, lay = page("Convert files with one drag")
        steps, sl = card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        self.step_labels = []
        for i in range(3):
            num = label(str(i + 1), "step")
            num.setFixedSize(26, 26)
            num.setAlignment(Qt.AlignCenter)
            text = label(wrap=True)
            self.step_labels.append(text)
            grid.addWidget(num, i, 0, Qt.AlignTop)
            grid.addWidget(text, i, 1)
        grid.setColumnStretch(1, 1)
        sl.addLayout(grid)
        lay.addWidget(steps)

        drop = DropZone()
        drop.entered.connect(lambda: self.c.wheel.popup_at_cursor())
        lay.addWidget(drop)

        self.home_stats = label("", "muted")
        lay.addWidget(self.home_stats)

        self.power = button("", "power")
        self.power.setCheckable(True)
        self.power.setChecked(self.c.settings.enabled)
        self.power.toggled.connect(self.c.set_enabled)
        row = QHBoxLayout()
        row.addWidget(self.power)
        row.addSpacing(10)
        row.addWidget(label("Satsuma keeps running in the system tray (bottom-right, by the clock) "
                            "after you close this window.", "muted", wrap=True), 1)
        lay.addLayout(row)
        lay.addStretch()
        return w

    def _general(self) -> QWidget:
        s = self.c.settings
        w, lay = page("General")

        box, bl = card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)

        self.shortcut = QComboBox()
        for key, text in SHORTCUT_LABELS.items():
            self.shortcut.addItem(text, key)
        self.shortcut.setCurrentIndex(max(0, self.shortcut.findData(s.shortcut)))
        self.shortcut.currentIndexChanged.connect(self._general_changed)

        self.save_mode = QComboBox()
        self.save_mode.addItem("Next to the original file", "beside")
        self.save_mode.addItem("Always in one folder", "folder")
        self.save_mode.setCurrentIndex(max(0, self.save_mode.findData(s.save_mode)))
        self.save_mode.currentIndexChanged.connect(self._general_changed)

        self.folder_row = QWidget()
        fr = QHBoxLayout(self.folder_row)
        fr.setContentsMargins(0, 0, 0, 0)
        self.folder_label = label("", "muted")
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        fr.addWidget(self.folder_label, 1)
        fr.addWidget(button("Change…", on_click=self._pick_folder))

        self.quality = QSpinBox()
        self.quality.setRange(50, 100)
        self.quality.setSuffix(" %")
        self.quality.setValue(s.jpg_quality)
        self.quality.valueChanged.connect(self._general_changed)
        q_row = QHBoxLayout()
        q_row.addWidget(self.quality)
        q_row.addWidget(label("for JPG and WebP. Higher = better quality, bigger file.", "muted"), 1)

        self.sound = QComboBox()
        for key, text in SOUNDS.items():
            self.sound.addItem(text, key)
        self.sound.setCurrentIndex(max(0, self.sound.findData(s.sound)))
        self.sound.currentIndexChanged.connect(self._sound_changed)
        snd_row = QHBoxLayout()
        snd_row.addWidget(self.sound)
        snd_row.addWidget(button("▶ Play", on_click=self.c.play_done_sound))
        snd_row.addStretch()

        self.unavailable = QComboBox()
        self.unavailable.addItem("Show them dimmed (with the reason)", True)
        self.unavailable.addItem("Hide them", False)
        self.unavailable.setCurrentIndex(0 if s.show_unavailable else 1)
        self.unavailable.currentIndexChanged.connect(self._general_changed)

        rows = (("Key to hold while dragging", self.shortcut), ("Save new files", self.save_mode),
                ("", self.folder_row), ("Image quality", q_row), ("Sound when done", snd_row),
                ("Formats that don't fit a file", self.unavailable))
        for r, (text, widget) in enumerate(rows):
            if text:
                grid.addWidget(label(text), r, 0)
            (grid.addLayout if isinstance(widget, QHBoxLayout) else grid.addWidget)(widget, r, 1)
        grid.setColumnStretch(1, 1)
        bl.addLayout(grid)
        lay.addWidget(box)

        checks, cl = card()
        self.ask_names = checkbox("When converting several files, ask for a new name (like vacation-1, vacation-2…)",
                                  s.ask_names, self._general_changed)
        self.show_progress = checkbox("Show a popup while converting and when it's done", s.show_progress,
                                      self._general_changed)
        self.reveal = checkbox("Open the folder with the new file after converting", s.reveal_after,
                               self._general_changed)
        self.context = checkbox("Add \"Convert with Satsuma\" to the right-click menu", s.context_menu,
                                self.c.set_context_menu)
        self.startup = checkbox("Start Satsuma when Windows starts", startup_enabled(), set_startup)
        for cb in (self.ask_names, self.show_progress, self.reveal, self.context, self.startup):
            cl.addWidget(cb)
        cl.addWidget(label("On Windows 11, the right-click entry is under \"Show more options\".", "muted"))
        lay.addWidget(checks)
        lay.addStretch()
        return w

    def _look(self) -> QWidget:
        s = self.c.settings
        w, lay = page("Look", "Make Satsuma yours. The preview updates as you change things.")

        controls, cl = card(8)
        appearance = Segmented([("system", "Match Windows"), ("dark", "Dark"), ("light", "Light")], s.appearance)
        appearance.changed.connect(lambda v: self._look_changed("appearance", v))
        self.swatches = Swatches(s.accent, s.custom_color)
        self.swatches.changed.connect(lambda v: self._look_changed("accent", v))
        self.swatches.custom_picked.connect(lambda v: setattr(self.c.settings, "custom_color", v))
        self.accent_name = label("", "muted")
        style = Segmented(list(WHEEL_STYLES.items()), s.wheel_style)
        style.changed.connect(lambda v: self._look_changed("wheel_style", v))
        size = Segmented([("small", "Small"), ("medium", "Medium"), ("large", "Large")], s.wheel_size)
        size.changed.connect(lambda v: self._look_changed("wheel_size", v))
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(60, 100)
        self.opacity.setValue(s.wheel_opacity)
        self.opacity.setCursor(Qt.PointingHandCursor)
        self.opacity.valueChanged.connect(lambda v: self._look_changed("wheel_opacity", v))
        self.animations = checkbox("Animations", s.animations, lambda v: self._look_changed("animations", v))

        for title, widget in (("Appearance", appearance), ("Accent colour", self.swatches),
                              (None, self.accent_name), ("Wheel shape", style), ("Wheel size", size),
                              ("Wheel see-through", self.opacity)):
            if title:
                cl.addSpacing(6)
                cl.addWidget(label(title, "h2"))
            cl.addWidget(widget)
        cl.addSpacing(10)
        cl.addWidget(self.animations)
        cl.addStretch()

        preview_box, pl = card()
        pl.setContentsMargins(12, 12, 12, 12)
        pl.addWidget(label("Preview", "muted"), 0, Qt.AlignHCenter)
        self.preview = WheelPreview(self.c.settings, self.c.theme)
        pl.addWidget(self.preview, 0, Qt.AlignCenter)
        pl.addWidget(label("Dimmed = can't be used for that file", "muted"), 0, Qt.AlignHCenter)
        pl.addStretch()

        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(controls, 1)
        row.addWidget(preview_box)
        lay.addLayout(row)
        lay.addStretch()
        return w

    def _formats(self) -> QWidget:
        w, lay = page("Formats", "Untick anything you don't use. Click ☆ to pin a format to the top of the wheel.")
        hidden = set(self.c.settings.hidden_tools)
        favs = set(self.c.settings.favorites)
        tools = catalog(multi=False)
        self.tool_checks: dict[str, QCheckBox] = {}
        self.tool_stars: dict[str, QPushButton] = {}
        for kind, items in tools.items():
            lay.addWidget(label(CATEGORY_NAMES[kind], "h2"))
            box, _ = card()
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(6)
            box.layout().addLayout(grid)
            for i, t in enumerate(items):
                star = button("★" if t.key in favs else "☆", "star")
                star.setCheckable(True)
                star.setChecked(t.key in favs)
                star.setToolTip("Pin to the top of the wheel")
                star.toggled.connect(lambda on, key=t.key, b=star: self._star_toggled(key, on, b))
                cb = checkbox(f"{t.label}  ·  {t.description}", t.key not in hidden,
                              lambda on, key=t.key: self._tool_toggled(key, on))
                self.tool_checks[t.key] = cb
                self.tool_stars[t.key] = star
                row, col = i // 2, (i % 2) * 2
                grid.addWidget(star, row, col)
                grid.addWidget(cb, row, col + 1)
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(3, 1)
            lay.addWidget(box)
        lay.addWidget(button("Show everything again", on_click=self._show_all_tools), 0, Qt.AlignLeft)
        lay.addStretch()
        return w

    def _history(self) -> QWidget:
        w, lay = page("History")
        stats, sl = card()
        row = QHBoxLayout()
        self.stat_files = label("0", "stat")
        self.stat_saved = label("0", "stat")
        self.stat_jobs = label("0", "stat")
        for value, text in ((self.stat_files, "files converted"), (self.stat_saved, "space saved"),
                            (self.stat_jobs, "conversions")):
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(value)
            col.addWidget(label(text, "muted"))
            row.addLayout(col)
            row.addSpacing(30)
        row.addStretch()
        sl.addLayout(row)
        self.stat_since = label("", "muted")
        sl.addWidget(self.stat_since)
        lay.addWidget(stats)

        lay.addWidget(label("Recent conversions", "h2"))
        self.history_list = QListWidget()
        self.history_list.setObjectName("history")
        self.history_list.setMinimumHeight(220)
        self.history_list.itemDoubleClicked.connect(lambda _: self._history_open())
        self.history_list.currentRowChanged.connect(lambda _: self._history_buttons())
        lay.addWidget(self.history_list, 1)
        self.history_empty = label("Nothing yet. Converted files will show up here.", "muted")
        lay.addWidget(self.history_empty)
        btns = QHBoxLayout()
        self.h_open = button("Open", on_click=self._history_open)
        self.h_show = button("Show in folder", on_click=self._history_show)
        btns.addWidget(self.h_open)
        btns.addWidget(self.h_show)
        btns.addStretch()
        btns.addWidget(button("Clear history", on_click=self._history_clear))
        btns.addWidget(button("Reset stats", on_click=self._stats_reset))
        lay.addLayout(btns)
        return w

    def _updates(self) -> QWidget:
        s = self.c.settings
        w, lay = page("Updates", "Satsuma can update itself, so you never have to download it again.")
        box, bl = card()
        self.update_status = label("", "h2", wrap=True)
        self.update_detail = label("", "muted", wrap=True)
        self.update_now = button("Update now", "primary", self.c.start_update)
        self.check_now = button("Check now", on_click=lambda: self.c.check_updates(manual=True))
        row = QHBoxLayout()
        row.addWidget(self.update_now)
        row.addWidget(self.check_now)
        row.addStretch()
        bl.addWidget(self.update_status)
        bl.addWidget(self.update_detail)
        bl.addLayout(row)
        lay.addWidget(box)

        opts, ol = card()
        self.auto_check = checkbox("Check for updates automatically", s.check_updates, self._update_options_changed)
        self.auto_install = checkbox("Install updates by themselves (Satsuma restarts for a moment)",
                                     s.auto_update, self._update_options_changed)
        ol.addWidget(self.auto_check)
        ol.addWidget(self.auto_install)
        ol.addWidget(label("Updates come from the official Satsuma release page, are checked for damage "
                           "before installing, and keep all your settings.", "muted", wrap=True))
        lay.addWidget(opts)
        lay.addStretch()
        return w

    def _about(self) -> QWidget:
        w, lay = page("About")
        top = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(self.icon.pixmap(72, 72))
        text = QVBoxLayout()
        text.addWidget(label(APP_NAME, "h2"))
        text.addWidget(label(f"Version {__version__}", "muted"))
        text.addWidget(label("Shift-drag file converter for Windows. Everything happens on your PC; "
                             "nothing is uploaded.", "muted", wrap=True))
        top.addWidget(logo, 0, Qt.AlignTop)
        top.addSpacing(10)
        top.addLayout(text, 1)
        lay.addLayout(top)

        row = QHBoxLayout()
        row.addWidget(button("Open log folder", on_click=lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(LOG_DIR)))))
        row.addWidget(button("Quit Satsuma", on_click=self.c.quit))
        row.addStretch()
        lay.addLayout(row)

        remove, rl = card()
        rl.addWidget(label("Remove Satsuma", "h2"))
        if installed_uninstaller():
            rl.addWidget(label("Removes the app, its desktop icon, Start menu entry and right-click menu. You'll be "
                               "asked whether to delete your settings too. Files you converted are kept.",
                               "muted", wrap=True))
            rl.addWidget(button("Uninstall Satsuma…", "danger", self.c.uninstall), 0, Qt.AlignLeft)
            rl.addWidget(label("You can also uninstall it from Windows Settings → Apps → Installed apps.",
                               "muted", wrap=True))
        else:
            rl.addWidget(label("This copy wasn't set up with the installer. The button below deletes "
                               "Satsuma's settings, removes its right-click menu, stops it starting with Windows, "
                               "and closes it. Then delete the Satsuma folder to finish.", "muted", wrap=True))
            rl.addWidget(button("Remove settings and quit", "danger", self.c.remove_portable), 0, Qt.AlignLeft)
        lay.addWidget(remove)
        lay.addStretch()
        return w

    # ---- reacting to changes

    def refresh(self):
        s = self.c.settings
        key = s.shortcut_label
        self.step_labels[0].setText(f"Hold <b>{key}</b> and start dragging a file in File Explorer "
                                    f"or on your desktop, or right-click it and choose <b>Convert with Satsuma</b>.")
        self.step_labels[1].setText("A wheel of formats pops up. Dimmed ones don't work for that file; "
                                    "point at one to see why.")
        self.step_labels[2].setText("Drop the file on a format (or click it). The new file appears "
                                    + ("next to the original." if s.save_mode == "beside"
                                       else "in your Satsuma folder.")
                                    + " Let go in the middle to cancel.")
        self.power.setText("Satsuma is on" if s.enabled else "Paused. Click to turn on")
        self.folder_label.setText(s.output_folder)
        self.folder_row.setVisible(s.save_mode == "folder")
        self.accent_name.setText("Your colour" if s.accent == "custom" else ACCENTS[s.accent][0])
        st = self.c.history.stats
        self.home_stats.setText(f"So far: {st.files} files converted · {human_size(st.bytes_saved)} saved"
                                if st.files else "")
        self.home_stats.setVisible(bool(st.files))

    def refresh_history(self):
        st = self.c.history.stats
        self.stat_files.setText(f"{st.files:,}")
        self.stat_saved.setText(human_size(st.bytes_saved))
        self.stat_jobs.setText(f"{st.jobs:,}")
        self.stat_since.setText("Since " + time.strftime("%B %d, %Y", time.localtime(st.since)))
        self.history_list.clear()
        for e in self.c.history.items:
            what = e.inputs[0] if len(e.inputs) == 1 else f"{len(e.inputs)} files"
            result = Path(e.outputs[0]).name if len(e.outputs) == 1 else f"{len(e.outputs)} new files" \
                if e.outputs else "no new file"
            warn = f"  ·  {e.errors} failed" if e.errors else ""
            item = QListWidgetItem(f"{e.tool}:  {what}  →  {result}{warn}      {time_ago(e.when)}")
            item.setData(Qt.UserRole, e.outputs)
            self.history_list.addItem(item)
        empty = not self.c.history.items
        self.history_list.setVisible(not empty)
        self.history_empty.setVisible(empty)
        self._history_buttons()
        self.refresh()

    def _history_selected(self) -> list[Path]:
        item = self.history_list.currentItem()
        return [Path(p) for p in (item.data(Qt.UserRole) or [])] if item else []

    def _history_buttons(self):
        has = any(p.exists() for p in self._history_selected())
        self.h_open.setEnabled(has)
        self.h_show.setEnabled(has)

    def _history_open(self):
        for p in self._history_selected()[:5]:
            if p.exists():
                os.startfile(str(p))  # noqa: S606 (opens with the user's default app)

    def _history_show(self):
        files = [p for p in self._history_selected() if p.exists()]
        if files:
            reveal_in_explorer(files[0])

    def _history_clear(self):
        self.c.history.clear()
        self.refresh_history()

    def _stats_reset(self):
        self.c.history.reset_stats()
        self.refresh_history()

    def _general_changed(self, *_):
        s = self.c.settings
        s.shortcut = self.shortcut.currentData()
        s.save_mode = self.save_mode.currentData()
        s.jpg_quality = self.quality.value()
        s.show_unavailable = bool(self.unavailable.currentData())
        s.ask_names = self.ask_names.isChecked()
        s.show_progress = self.show_progress.isChecked()
        s.reveal_after = self.reveal.isChecked()
        self.c.settings_changed()

    def _sound_changed(self, *_):
        self.c.settings.sound = self.sound.currentData()
        self.c.settings_changed()
        self.c.play_done_sound()

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Save converted files in", self.c.settings.output_folder)
        if d:
            self.c.settings.output_folder = d
            self.c.settings_changed()

    def _look_changed(self, key: str, value):
        setattr(self.c.settings, key, value)
        self.c.settings_changed()
        if key in ("appearance", "accent", "wheel_opacity"):
            self.c.apply_theme()
        self.preview.update()

    def _tool_toggled(self, key: str, on: bool):
        hidden = set(self.c.settings.hidden_tools)
        hidden.discard(key) if on else hidden.add(key)
        self.c.settings.hidden_tools = sorted(hidden)
        self.c.settings_changed()

    def _star_toggled(self, key: str, on: bool, star: QPushButton):
        favs = [k for k in self.c.settings.favorites if k != key]
        if on:
            favs.append(key)
        self.c.settings.favorites = favs
        star.setText("★" if on else "☆")
        self.c.settings_changed()

    def _show_all_tools(self):
        for cb in self.tool_checks.values():
            cb.setChecked(True)

    def _update_options_changed(self, *_):
        self.c.settings.check_updates = self.auto_check.isChecked()
        self.c.settings.auto_update = self.auto_install.isChecked()
        self.c.settings_changed()
        self.refresh_updates()

    def refresh_updates(self):
        c = self.c
        current = __version__
        info = c.update_info
        state = c.update_state
        if not c.updates_supported():
            status = f"You have version {current}"
            detail = ("Automatic updates aren't set up for this copy. Copies built by GitHub "
                      "(see the README) update themselves.")
        elif state == "checking":
            status, detail = "Checking for updates…", ""
        elif state == "downloading" and info:
            status, detail = f"Downloading Satsuma {info.version}…", "Satsuma restarts by itself when it's done."
        elif state == "available" and info:
            status = f"Satsuma {info.version} is available"
            detail = f"You have {current}." + ("" if c.can_self_install() else
                                               " This copy isn't installed, so the button opens the download page.")
        elif state == "uptodate":
            status, detail = "You're up to date", f"Satsuma {current} is the newest version."
        elif state == "error":
            status, detail = "Couldn't check for updates", c.update_error.capitalize()
        else:
            status, detail = f"You have version {current}", "Satsuma checks for updates shortly after it starts."
        self.update_status.setText(status)
        self.update_detail.setText(detail)
        self.update_detail.setVisible(bool(detail))
        self.update_now.setVisible(state == "available" and info is not None)
        self.update_now.setText("Update now" if c.can_self_install() else "Open download page")
        self.check_now.setEnabled(c.updates_supported() and state not in ("checking", "downloading"))
        self.auto_check.setEnabled(c.updates_supported())
        self.auto_install.setEnabled(c.updates_supported() and c.can_self_install())

    def apply_theme(self, t: Theme):
        self.swatches.apply_theme(t)
        self.preview.theme = t
        self.preview.update()
        if self.isVisible():
            set_title_bar_dark(self, t.dark)

    def sync(self):
        """Pick up changes made from the tray menu or outside the app."""
        for widget, value in ((self.power, self.c.settings.enabled), (self.startup, startup_enabled()),
                              (self.context, self.c.settings.context_menu)):
            widget.blockSignals(True)
            widget.setChecked(value)
            widget.blockSignals(False)
        self.refresh()

    def open_page(self, name: str):
        self.nav.setCurrentRow(PAGES.index(name))

    def showEvent(self, e):
        super().showEvent(e)
        set_title_bar_dark(self, self.c.theme.dark)

    def closeEvent(self, e):
        e.ignore()
        self.hide()
        if not self.told_about_tray:
            self.told_about_tray = True
            self.c.tray.showMessage(APP_NAME, "Still running in the system tray. "
                                              f"Hold {self.c.settings.shortcut_label} and drag a file any time.",
                                    QSystemTrayIcon.Information, 3500)
