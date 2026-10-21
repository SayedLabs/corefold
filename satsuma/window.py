"""The Satsuma window: Home, General, Look, Formats and About pages."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QListWidget, QPushButton, QScrollArea, QSpinBox,
                               QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

from . import __version__
from .converters import CATEGORY_NAMES, catalog
from .settings import (ACCENTS, APP_NAME, LOG_DIR, SHORTCUT_LABELS, installed_uninstaller, set_startup,
                       startup_enabled)
from .theme import Theme
from .wheel import WheelPreview
from .winutil import set_title_bar_dark

if TYPE_CHECKING:
    from .app import Controller

PAGES = ["Home", "General", "Look", "Formats", "Updates", "About"]


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


def card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    return f


def page(title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(14)
    lay.addWidget(label(title, "h1"))
    if subtitle:
        lay.addWidget(label(subtitle, "muted", wrap=True))
    return w, lay


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
    changed = Signal(str)

    def __init__(self, value: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        keys = list(ACCENTS)
        for i, key in enumerate(keys):
            name, _ = ACCENTS[key]
            b = button("")
            b.setCheckable(True)
            b.setChecked(key == value)
            b.setFixedSize(32, 32)
            b.setToolTip(name)
            self.group.addButton(b, i)
            self.buttons[key] = b
            lay.addWidget(b)
        lay.addStretch()
        self.group.idClicked.connect(lambda i: self.changed.emit(keys[i]))

    def apply_theme(self, t: Theme):
        for key, b in self.buttons.items():
            color = ACCENTS[key][1]
            b.setStyleSheet(
                f"QPushButton {{ background: {color}; border: 3px solid {t.panel.name()}; border-radius: 16px;"
                f" padding: 0; }}"
                f"QPushButton:hover {{ border-color: {t.border.name()}; }}"
                f"QPushButton:checked {{ background: {color}; border: 3px solid {t.text.name()}; }}")


class DropZone(QFrame):
    """Drag any file over this box to open the wheel, no key needed."""
    entered = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(110)
        lay = QVBoxLayout(self)
        a = label("Try it here: drag any file onto this box", "h2")
        b = label("Images, audio, video and PDFs. Your original file is never changed.", "muted")
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
        self.resize(920, 620)
        self.setMinimumSize(840, 580)

        # sidebar
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

        # pages
        self.stack = QStackedWidget()
        for build in (self._home, self._general, self._look, self._formats, self._updates, self._about):
            self.stack.addWidget(build())
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        footer = QHBoxLayout()
        footer.addWidget(label("Changes are saved automatically.", "muted"))
        footer.addStretch()
        footer.addWidget(button("Done", "primary", self.close))
        content = QVBoxLayout()
        content.setContentsMargins(30, 24, 30, 18)
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

    # ---- pages

    def _home(self) -> QWidget:
        w, lay = page("Convert files with one drag")
        steps = card()
        grid = QGridLayout(steps)
        grid.setContentsMargins(18, 16, 18, 16)
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
        lay.addWidget(steps)

        drop = DropZone()
        drop.entered.connect(self.c.wheel.popup_at_cursor)
        lay.addWidget(drop)

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

        box = card()
        grid = QGridLayout(box)
        grid.setContentsMargins(18, 16, 18, 16)
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

        grid.addWidget(label("Key to hold while dragging"), 0, 0)
        grid.addWidget(self.shortcut, 0, 1)
        grid.addWidget(label("Save new files"), 1, 0)
        grid.addWidget(self.save_mode, 1, 1)
        grid.addWidget(self.folder_row, 2, 1)
        grid.addWidget(label("Image quality"), 3, 0)
        grid.addLayout(q_row, 3, 1)
        grid.setColumnStretch(1, 1)
        lay.addWidget(box)

        checks = card()
        cl = QVBoxLayout(checks)
        cl.setContentsMargins(18, 16, 18, 16)
        cl.setSpacing(12)
        self.show_progress = QCheckBox("Show a popup while converting and when it's done")
        self.show_progress.setChecked(s.show_progress)
        self.show_progress.toggled.connect(self._general_changed)
        self.reveal = QCheckBox("Open the folder with the new file after converting")
        self.reveal.setChecked(s.reveal_after)
        self.reveal.toggled.connect(self._general_changed)
        self.startup = QCheckBox("Start Satsuma when Windows starts")
        self.startup.setChecked(startup_enabled())
        self.startup.toggled.connect(set_startup)
        for cb in (self.show_progress, self.reveal, self.startup):
            cb.setCursor(Qt.PointingHandCursor)
            cl.addWidget(cb)
        lay.addWidget(checks)
        lay.addStretch()
        return w

    def _look(self) -> QWidget:
        s = self.c.settings
        w, lay = page("Look", "Make Satsuma yours. The preview updates as you change things.")

        controls = card()
        cl = QVBoxLayout(controls)
        cl.setContentsMargins(18, 16, 18, 16)
        cl.setSpacing(8)

        appearance = Segmented([("system", "Match Windows"), ("dark", "Dark"), ("light", "Light")],
                               s.appearance)
        appearance.changed.connect(lambda v: self._look_changed("appearance", v))
        self.swatches = Swatches(s.accent)
        self.swatches.changed.connect(lambda v: self._look_changed("accent", v))
        self.accent_name = label("", "muted")
        size = Segmented([("small", "Small"), ("medium", "Medium"), ("large", "Large")], s.wheel_size)
        size.changed.connect(lambda v: self._look_changed("wheel_size", v))
        self.animations = QCheckBox("Animations")
        self.animations.setCursor(Qt.PointingHandCursor)
        self.animations.setChecked(s.animations)
        self.animations.toggled.connect(lambda v: self._look_changed("animations", v))

        for title, widget in (("Appearance", appearance), ("Accent colour", self.swatches),
                              (None, self.accent_name), ("Wheel size", size)):
            if title:
                cl.addSpacing(6)
                cl.addWidget(label(title, "h2"))
            cl.addWidget(widget)
        cl.addSpacing(10)
        cl.addWidget(self.animations)
        cl.addStretch()

        preview_box = card()
        pl = QVBoxLayout(preview_box)
        pl.setContentsMargins(12, 12, 12, 12)
        pl.addWidget(label("Preview", "muted"), 0, Qt.AlignHCenter)
        self.preview = WheelPreview(self.c.settings, self.c.theme)
        pl.addWidget(self.preview, 0, Qt.AlignCenter)
        pl.addStretch()

        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(controls, 1)
        row.addWidget(preview_box)
        lay.addLayout(row)
        lay.addStretch()
        return w

    def _formats(self) -> QWidget:
        w, lay = page("Formats", "Untick anything you don't use. Fewer slices on the wheel = easier to hit.")
        hidden = set(self.c.settings.hidden_tools)
        tools = catalog(multi=False)
        tools["pdf"] = catalog(multi=True)["pdf"]  # include "Merge"
        self.tool_checks: dict[str, QCheckBox] = {}

        content = QWidget()
        content.setObjectName("scrollContent")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 8, 0)
        cl.setSpacing(10)
        for kind, items in tools.items():
            cl.addWidget(label(CATEGORY_NAMES[kind], "h2"))
            box = card()
            grid = QGridLayout(box)
            grid.setContentsMargins(16, 12, 16, 12)
            grid.setHorizontalSpacing(20)
            grid.setVerticalSpacing(9)
            for i, t in enumerate(items):
                note = " (when several are dropped)" if t.multi_only else ""
                cb = QCheckBox(f"{t.label}  ·  {t.description}{note}")
                cb.setCursor(Qt.PointingHandCursor)
                cb.setChecked(t.key not in hidden)
                cb.toggled.connect(lambda on, key=t.key: self._tool_toggled(key, on))
                self.tool_checks[t.key] = cb
                grid.addWidget(cb, i // 2, i % 2)
            cl.addWidget(box)
        cl.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        scroll.viewport().setAutoFillBackground(False)
        lay.addWidget(scroll, 1)
        lay.addWidget(button("Show everything again", on_click=self._show_all_tools), 0, Qt.AlignLeft)
        return w

    def _updates(self) -> QWidget:
        s = self.c.settings
        w, lay = page("Updates", "Satsuma can update itself, so you never have to download it again.")
        box = card()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(18, 16, 18, 16)
        bl.setSpacing(12)
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

        opts = card()
        ol = QVBoxLayout(opts)
        ol.setContentsMargins(18, 16, 18, 16)
        ol.setSpacing(12)
        self.auto_check = QCheckBox("Check for updates automatically")
        self.auto_check.setChecked(s.check_updates)
        self.auto_check.toggled.connect(self._update_options_changed)
        self.auto_install = QCheckBox("Install updates by themselves (Satsuma restarts for a moment)")
        self.auto_install.setChecked(s.auto_update)
        self.auto_install.toggled.connect(self._update_options_changed)
        for cb in (self.auto_check, self.auto_install):
            cb.setCursor(Qt.PointingHandCursor)
            ol.addWidget(cb)
        ol.addWidget(label("Updates come from the official Satsuma release page, are checked for damage "
                           "before installing, and keep all your settings.", "muted", wrap=True))
        lay.addWidget(opts)
        lay.addStretch()
        return w

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

        remove = card()
        rl = QVBoxLayout(remove)
        rl.setContentsMargins(18, 16, 18, 16)
        rl.setSpacing(10)
        rl.addWidget(label("Remove Satsuma", "h2"))
        if installed_uninstaller():
            rl.addWidget(label("Removes the app, its desktop icon and Start menu entry. You'll be asked "
                               "whether to delete your settings too. Files you converted are kept.",
                               "muted", wrap=True))
            rl.addWidget(button("Uninstall Satsuma…", "danger", self.c.uninstall), 0, Qt.AlignLeft)
            rl.addWidget(label("You can also uninstall it from Windows Settings → Apps → Installed apps.",
                               "muted", wrap=True))
        else:
            rl.addWidget(label("This copy wasn't set up with the installer. The button below deletes "
                               "Satsuma's settings, stops it starting with Windows, and closes it. "
                               "Then delete the Satsuma folder to finish.", "muted", wrap=True))
            rl.addWidget(button("Remove settings and quit", "danger", self.c.remove_portable), 0, Qt.AlignLeft)
        lay.addWidget(remove)
        lay.addStretch()
        return w

    # ---- reacting to changes

    def refresh(self):
        s = self.c.settings
        key = s.shortcut_label
        self.step_labels[0].setText(f"Hold <b>{key}</b> and start dragging a file in File Explorer "
                                    f"or on your desktop.")
        self.step_labels[1].setText("A wheel of formats pops up under your cursor.")
        self.step_labels[2].setText("Drop the file on a format. The new file appears "
                                    + ("next to the original." if s.save_mode == "beside"
                                       else "in your Satsuma folder.")
                                    + " Let go in the middle to cancel.")
        self.power.setText("Satsuma is on" if s.enabled else "Paused. Click to turn on")
        self.folder_label.setText(s.output_folder)
        self.folder_row.setVisible(s.save_mode == "folder")
        self.accent_name.setText(ACCENTS[s.accent][0])

    def _general_changed(self, *_):
        s = self.c.settings
        s.shortcut = self.shortcut.currentData()
        s.save_mode = self.save_mode.currentData()
        s.jpg_quality = self.quality.value()
        s.show_progress = self.show_progress.isChecked()
        s.reveal_after = self.reveal.isChecked()
        self.c.settings_changed()

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Save converted files in", self.c.settings.output_folder)
        if d:
            self.c.settings.output_folder = d
            self.c.settings_changed()

    def _look_changed(self, key: str, value):
        setattr(self.c.settings, key, value)
        self.c.settings_changed()
        if key in ("appearance", "accent"):
            self.c.apply_theme()
        self.preview.update()

    def _tool_toggled(self, key: str, on: bool):
        hidden = set(self.c.settings.hidden_tools)
        hidden.discard(key) if on else hidden.add(key)
        self.c.settings.hidden_tools = sorted(hidden)
        self.c.settings_changed()

    def _show_all_tools(self):
        for cb in self.tool_checks.values():
            cb.setChecked(True)

    def apply_theme(self, t: Theme):
        self.swatches.apply_theme(t)
        self.preview.theme = t
        self.preview.update()
        if self.isVisible():
            set_title_bar_dark(self, t.dark)

    def sync(self):
        """Pick up changes made from the tray menu or outside the app."""
        for widget, value in ((self.power, self.c.settings.enabled), (self.startup, startup_enabled())):
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
