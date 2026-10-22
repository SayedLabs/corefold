"""The Corefold window: Home, Apps, General, Look, Formats, History, Updates and About pages."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QDateTime, QEvent, QLocale, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton,
                               QScrollArea, QSlider, QSpinBox, QStackedWidget, QSystemTrayIcon, QVBoxLayout,
                               QWidget)

from . import UPDATE_REPO, __version__, i18n, icons, winutil
from . import settings as settings_mod
from .converters import CATEGORY_NAMES, catalog, human_size
from .core import short_name
from .i18n import LANGUAGES, tr
from .panel import DropZone, FilePanel, choose_files
from .settings import (ACCENTS, APP_NAME, DEFAULT_ACCENT, APPEARANCES, HOVER_STYLES, LABEL_STYLES, LOG_DIR, MENU_STYLES,
                       SHORTCUT_LABELS, SLICE_COLORS, SOUNDS, TEXT_SIZE_NAMES, TOAST_CORNERS, TOAST_TIMES,
                       WHEEL_POSITIONS, WHEEL_SIZE_NAMES, WHEEL_STYLES, WINDOW_TEXT, installed_uninstaller,
                       FORMAT_KEYS, LOOK_KEYS, editable_keys, general_keys, set_startup, startup_enabled)
from .theme import Theme
from .wheel import WheelPreview
from .winutil import memory_use, reveal_in_explorer, set_title_bar_dark

if TYPE_CHECKING:
    from .app import Controller

# every page, in the order they are built (names are English, shown through tr())
PAGES = ["Home", "Converter", "History", "Apps", "Soon", "Archives", "Viewer", "Player", "Capture", "Space",
         "Clipboard", "Cloud", "General", "Look", "Formats", "Updates", "About"]
# tools still being made: page -> product tile (in the order they're planned)
SOON = {"Archives": "archive", "Viewer": "viewer", "Player": "player", "Capture": "capture", "Space": "space",
        "Clipboard": "clipboard", "Cloud": "cloud"}
# the sidebar: pages, with a small heading before each group (None + heading). The tools still being
# made share one "Coming soon" row, so the sidebar stays short.
NAV = ["Home", "History", (None, "Tools"), "Converter", "Apps", "Soon",
       (None, "Settings"), "General", "Look", "Formats", "Updates", "About"]
NAV_LABELS = {"Apps": "Uninstaller", "Soon": "Coming soon"}  # where the sidebar's name differs from the page's
NAV_OWNER = {name: "Soon" for name in SOON}  # pages without a row of their own: the row that's lit
PAGE_ICONS = {"Home": "home", "Converter": "arrow_swap", "Apps": "broom", "General": "settings", "Look": "paint_brush",  # icons.py
              "Formats": "puzzle_piece", "History": "history", "Updates": "arrow_sync", "About": "info",
              "Soon": "star", "Archives": "archive", "Player": "video", "Viewer": "image", "Capture": "screenshot",
              "Space": "data_pie", "Clipboard": "clipboard", "Cloud": "globe"}
# the tools on the Home page: page, product tile, name, one line (English, shown through tr())
TOOLS = [
    ("Converter", "converter", "Corefold Converter",
     "Convert, shrink and edit pictures, videos, music, PDFs and documents with one drag."),
    ("Apps", "cleaner", "Corefold Uninstaller",
     "Remove programs and everything they leave behind, with a backup you can put back."),
]
SOON_TEXT = {  # name, one line, what it will do (English, shown through tr())
    "Cloud": ("Corefold Cloud", "All your cloud storage in one window: OneDrive, Google Drive, Dropbox and more.",
              ["See OneDrive, Google Drive and Dropbox side by side, without switching accounts",
               "Drag files from one cloud to another",
               "Several accounts of the same service at once",
               "Search across all of them",
               "Sign in on Microsoft's and Google's own page: your passwords never reach Corefold"]),
    "Capture": ("Corefold Capture", "Take a picture or a video of your screen, mark it up and share it in "
                "seconds.",
                ["Pick an area, a window or the whole screen with one shortcut",
                 "Draw arrows, boxes and text, and blur anything private",
                 "Copy the text you see in any picture or screen",
                 "Record your screen as a video or a GIF, with sound if you want",
                 "Everything stays on your PC: nothing is uploaded"]),
    "Space": ("Corefold Space", "See what's filling your disk and free up space safely.",
              ["A clear map of the biggest folders and files",
               "Find duplicate files and photos",
               "Clean up safe junk: temporary files, old installers, leftover caches",
               "Never touches Windows or your personal files without asking",
               "Everything removed goes to the Recycle Bin first"]),
    "Clipboard": ("Corefold Clipboard", "Everything you copy, kept and easy to find again.",
                  ["Text, pictures, links and files you copied, in one list",
                   "Pin what you paste often so it's always at the top",
                   "Search your clipboard history",
                   "Paste as plain text, without the formatting",
                   "Stays on your PC, and skips passwords from password managers"]),
    "Archives": ("Corefold Archive", "Open, look inside and make archives right here, faster and tidier than the "
                 "usual archive apps.",
                 ["Open ZIP, RAR, 7z, TAR, ISO and more with a double-click",
                  "Drag files out of an archive into any folder",
                  "Change a file inside an archive and save it back",
                  "Repair damaged ZIP files and rescue what can still be read",
                  "Extract archives inside archives in one go"]),
    "Player": ("Corefold Player", "Plays every video and song, smooth in 4K, with clean controls that get out of "
               "the way.",
               ["Plays every common video and music format",
                "Subtitles you can drag in, resize and shift in time",
                "Picks up where you left off, with a Continue watching list",
                "A mini player that stays on top of other windows",
                "Speed, A–B loop, frame by frame, screenshots and clips"]),
    "Viewer": ("Corefold Viewer", "Flip through a folder of pictures fast, and fix them on the spot.",
               ["Arrow keys through a whole folder, instantly",
                "Zoom, turn and crop",
                "Quick edits, and save as another format",
                "HEIC, WebP, AVIF and more",
                "See the details: size, date and camera"]),
}


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


def tool_header(product: str, title: str, intro: str, dark: bool, dpr: float, extra: QWidget | None = None,
                pill: str = "") -> QHBoxLayout:
    """A tool's own heading: its tile, its name (and a pill like "Coming soon"), one line about it,
    and something on the far side (like the on/off switch)."""
    head = QHBoxLayout()
    head.setSpacing(16)
    tile = QLabel()
    tile.setPixmap(icons.product_tile(product, 60, dark, dpr))
    tile.setFixedSize(62, 62)
    head.addWidget(tile, 0, Qt.AlignTop)
    words = QVBoxLayout()
    words.setSpacing(4)
    top = QHBoxLayout()
    top.setSpacing(10)
    top.addWidget(label(title, "h1"))
    if pill:
        p = label(pill, "soonPill")
        p.setFixedHeight(20)
        top.addWidget(p, 0, Qt.AlignVCenter)
    top.addStretch()
    words.addLayout(top)
    words.addWidget(label(intro, "muted", wrap=True))
    head.addLayout(words, 1)
    if extra is not None:
        head.addWidget(extra, 0, Qt.AlignTop)
    return head


class ToolCard(QFrame):
    """A tool on the Home page: click anywhere on it to open it."""

    def __init__(self, product: str, title: str, text: str, on_click, dark: bool, dpr: float, small: bool = False,
                 pill: str = ""):
        super().__init__()
        self.setObjectName("toolCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)  # Tab reaches it; Enter opens it
        self.on_click = on_click
        lay = QHBoxLayout(self) if small else QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10 if small else 8)
        tile = QLabel()
        side = 34 if small else 44
        tile.setPixmap(icons.product_tile(product, side, dark, dpr))
        tile.setFixedSize(side + 2, side + 2)
        tile.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.title = label(title, "cardTitle")
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents)
        if small:
            lay.addWidget(tile, 0, Qt.AlignVCenter)
            words = QVBoxLayout()
            words.setSpacing(2)
            words.addWidget(self.title)
            if pill:
                p = label(pill, "muted")
                p.setAttribute(Qt.WA_TransparentForMouseEvents)
                words.addWidget(p)
            lay.addLayout(words, 1)
        else:
            lay.addWidget(tile)
            lay.addWidget(self.title)
            line = label(text, "muted", wrap=True)
            line.setMinimumWidth(1)  # wraps inside the card instead of pushing past its edge (long Arabic lines)
            line.setContentsMargins(6, 0, 6, 0)
            line.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(line)
            self.stat = label(pill, "cardStat")
            self.stat.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(self.stat)
            lay.addStretch()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.on_click()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.on_click()
        else:
            super().keyPressEvent(e)


def locale() -> QLocale:
    """Dates and numbers in the app's language (not always Windows' language)."""
    return QLocale({"ar": QLocale.Language.Arabic, "fr": QLocale.Language.French, "es": QLocale.Language.Spanish,
                    "tr": QLocale.Language.Turkish}.get(i18n.language(), QLocale.Language.English))


def date_text(ts: float, long: bool = False) -> str:
    when = QDateTime.fromSecsSinceEpoch(int(ts)).date()
    return locale().toString(when, "d MMMM yyyy" if long else "d MMM")


class _WheelGuard(QObject):
    """Sends the mouse wheel on to the page (so it scrolls) unless the box was clicked first."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel and not obj.hasFocus():
            area = obj.parent()
            while area is not None and not isinstance(area, QScrollArea):
                area = area.parent()
            if area is not None:
                QApplication.sendEvent(area.verticalScrollBar(), event)
            event.ignore()
            return True
        return False


def _reset_row(on_click) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addStretch()
    row.addWidget(button(tr("Reset to defaults"), "flat", on_click))
    return row


def _alive_set_text(lab: QLabel, text: str):
    try:
        lab.setText(text)
    except RuntimeError:  # the window was closed meanwhile
        pass


def _html(text: str) -> str:
    import html
    return html.escape(text, quote=False)


def time_ago(ts: float) -> str:
    d = time.time() - ts
    if d < 60:
        return tr("just now")
    if d < 3600:
        return tr("{count} min ago", count=int(d // 60))
    if d < 86400:
        return tr("{count} h ago", count=int(d // 3600))
    if d < 2 * 86400:
        return tr("yesterday")
    if d < 7 * 86400:
        return tr("{count} days ago", count=int(d // 86400))
    return date_text(ts)


class Segmented(QWidget):
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], value: str, per_row: int = 4):
        super().__init__()
        # up to per_row buttons on a line; more go on the next line, so long lists (and long
        # translations) never make the window wider than the screen
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.keys = [k for k, _ in options]
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        cols = min(per_row, len(options)) or 1
        for i, (key, text) in enumerate(options):
            b = button(text)
            b.setCheckable(True)
            b.setChecked(key == value)
            self.group.addButton(b, i)
            lay.addWidget(b, i // cols, i % cols)
        lay.setColumnStretch(cols, 1)
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
            b.setToolTip(tr(ACCENTS[key][0]) if key in ACCENTS else tr("Pick your own colour"))
            self.group.addButton(b, i)
            self.buttons[key] = b
            lay.addWidget(b)
        lay.addStretch()
        self.group.idClicked.connect(lambda i: self._clicked(keys[i]))
        self.theme: Theme | None = None

    def _clicked(self, key):
        if key == "custom":
            c = QColorDialog.getColor(QColor(self.custom), self.window(), tr("Pick a colour"))
            if not c.isValid():  # cancelled: go back to the colour that was selected
                self.buttons.get(self.value, self.buttons[DEFAULT_ACCENT]).setChecked(True)
                return
            self.custom = c.name()
            self.custom_picked.emit(self.custom)
        self.value = key
        self.changed.emit(key)

    def apply_theme(self, t: Theme):
        self.theme = t
        for key, b in self.buttons.items():
            color = ACCENTS[key][2 if t.dark else 1] if key in ACCENTS else self.custom
            text = ""
            if key == "custom":
                b.setText("+")
                text = f"color: {'#000' if QColor(color).lightness() > 150 else '#fff'}; font-size: 14pt;"
            b.setStyleSheet(
                f"QPushButton {{ background: {color}; border: 3px solid {t.panel.name()}; border-radius: 16px;"
                f" padding: 0; {text} }}"
                f"QPushButton:hover {{ border-color: {t.border.name()}; }}"
                f"QPushButton:checked {{ background: {color}; border: 3px solid {t.text.name()}; }}")


# ----------------------------------------------------------------------------- the window

class MainWindow(QWidget):
    def __init__(self, controller: "Controller", icon: QIcon, committed: dict | None = None,
                 pending_startup: bool | None = None):
        super().__init__()
        self.c = controller
        # the settings as last saved (what "Discard" goes back to), and a start-with-Windows choice
        # that isn't saved yet
        self.committed = committed if committed is not None else self._current_settings()
        self.pending_startup = pending_startup
        self.icon = icon
        self._batch = False
        self.setObjectName("window")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icon)
        self.resize(1040, 700)
        self.setMinimumSize(720, 460)  # fits small laptop screens with big text (150%)
        self.setAcceptDrops(True)      # files can be dropped anywhere on the window
        self._placed = False

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setAttribute(Qt.WA_StyledBackground)
        sidebar.setFixedWidth(212)
        logo = QLabel()
        logo.setPixmap(icon.pixmap(30, 30))
        brand = QHBoxLayout()
        brand.addWidget(logo)
        brand.addWidget(label(APP_NAME, "brand"))
        brand.addStretch()
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav_rows: list[str | None] = []   # sidebar row -> page name (None for a heading)
        self.nav.ensurePolished()
        row_h = max(34, self.nav.fontMetrics().height() + 18)  # compact rows, so every page fits
        for entry in NAV:
            item = QListWidgetItem()
            if isinstance(entry, tuple):
                item.setFlags(Qt.NoItemFlags)
                f = item.font()
                f.setPointSizeF(7.5)
                f.setWeight(QFont.Weight.DemiBold)
                f.setCapitalization(QFont.Capitalization.AllUppercase)
                item.setFont(f)
                item.setSizeHint(QSize(0, row_h - 6))
                self.nav_rows.append(None)
            else:
                item.setSizeHint(QSize(0, row_h))
                self.nav_rows.append(entry)
            self.nav.addItem(item)
        for row, name in enumerate(self.nav_rows):
            if name == "Soon":  # how many tools are on the way, in a small pill (clicks go through to the row)
                holder = QWidget()
                holder.setAttribute(Qt.WA_TransparentForMouseEvents)
                hl = QHBoxLayout(holder)
                hl.setContentsMargins(0, 0, 10, 0)
                hl.addStretch()
                pill = label(str(len(SOON)), "soonPill")
                pill.setFixedHeight(18)
                hl.addWidget(pill, 0, Qt.AlignVCenter)
                self.nav.setItemWidget(self.nav.item(row), holder)
        self.nav.setIconSize(icons.ICON_SIZE)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # (it still scrolls on tiny screens)
        self.refresh_nav()
        self.nav.setCursor(Qt.PointingHandCursor)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 18, 14, 14)
        side.setSpacing(14)
        side.addLayout(brand)
        side.addWidget(self.nav, 1)
        side.addWidget(label(tr("Version {version}", version=__version__), "version"))

        self.stack = QStackedWidget()
        builders = {"Home": self._home, "Converter": self._converter, "History": self._history, "Apps": self._apps,
                    "Soon": self._soon_all, "General": self._general,
                    "Look": self._look, "Formats": self._formats, "Updates": self._updates, "About": self._about}
        self._wheel_guard = None
        self._builders = builders
        # pages that are rarely opened are only built the first time they are (a faster, lighter window)
        self._unbuilt = {"Apps", "Soon", *SOON}
        for name in PAGES:
            self.stack.addWidget(QWidget() if name in self._unbuilt else builders[name]())
        self._opening = None  # a page without a sidebar row of its own, while it's being opened
        self.nav.currentRowChanged.connect(self._page_changed)
        self.nav.itemClicked.connect(self._row_clicked)
        last = self.c.settings.last_page
        self.open_page(last if last in PAGES else "Home")

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.saved_hint = label(tr("Changes to settings are kept when you press Save."), "muted")
        self.unsaved = label(tr("You have unsaved changes"), "unsaved")
        footer.addWidget(self.saved_hint)
        footer.addWidget(self.unsaved)
        footer.addStretch()
        self.discard_btn = button(tr("Discard"), None, self.discard_changes)
        self.save_btn = button(tr("Save"), "primary", self.save_changes)
        self.done_btn = button(tr("Done"), "primary", self.close)
        for b in (self.discard_btn, self.save_btn, self.done_btn):
            footer.addWidget(b)
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
        self._no_accidental_scrolling()
        self.update_save_bar()

    # ---- saving settings on purpose

    @staticmethod
    def _current_settings_of(s) -> dict:
        import copy
        return {k: copy.deepcopy(getattr(s, k)) for k in editable_keys()}

    def _current_settings(self) -> dict:
        return self._current_settings_of(self.c.settings)

    def is_dirty(self) -> bool:
        if self.pending_startup is not None and self.pending_startup != startup_enabled():
            return True
        return self._current_settings() != self.committed

    def update_save_bar(self):
        """Shows Save and Discard while settings were changed but not saved yet."""
        import copy
        if not hasattr(self, "save_btn"):
            return
        dirty = self.is_dirty()
        settings_mod.HELD = copy.deepcopy(self.committed) if dirty else None
        self.unsaved.setVisible(dirty)
        self.discard_btn.setVisible(dirty)
        self.save_btn.setVisible(dirty)
        self.done_btn.setObjectName("" if dirty else "primary")
        self.done_btn.style().unpolish(self.done_btn)
        self.done_btn.style().polish(self.done_btn)
        self.saved_hint.setVisible(not dirty)

    def save_changes(self):
        old_language = self.committed.get("language")
        self.committed = self._current_settings()
        settings_mod.HELD = None
        if self.pending_startup is not None:
            set_startup(self.pending_startup)
            self.pending_startup = None
        self.c.settings.save()
        self.update_save_bar()
        self.saved_hint.setText(tr("Saved"))
        QTimer.singleShot(2500, lambda: _alive_set_text(self.saved_hint,
                                                        tr("Changes to settings are kept when you press Save.")))
        if self.c.settings.language != old_language:
            self._ask_restart_for_language()

    def discard_changes(self, rebuild: bool = True):
        """Puts every setting back the way it was last saved."""
        import copy
        s = self.c.settings
        for k, v in self.committed.items():
            setattr(s, k, copy.deepcopy(v))
        settings_mod.HELD = None
        self.pending_startup = None
        self.c.watch_downloads(s.watch_downloads)
        self.c.apply_theme()
        self.c.settings_changed(menu=True)
        if rebuild:
            self.c.rebuild_window(self.current_page())

    def reset_to_defaults(self, keys):
        """Sets these settings to how Corefold comes (still to be saved, so it can be undone)."""
        import copy
        from .settings import Settings
        fresh = Settings()
        for k in keys:
            setattr(self.c.settings, k, copy.deepcopy(getattr(fresh, k)))
        self.c.watch_downloads(self.c.settings.watch_downloads)
        self.c.apply_theme()
        self.c.settings_changed(menu=True)
        self.c.rebuild_window(self.current_page(), committed=self.committed, pending_startup=self.pending_startup)

    def _no_accidental_scrolling(self, root: QWidget | None = None):
        """Scrolling down a page never changes a setting: lists, number boxes and sliders only react
        to the mouse wheel after they were clicked."""
        if self._wheel_guard is None:
            self._wheel_guard = _WheelGuard(self)
        for cls in (QComboBox, QSpinBox, QSlider):
            for w in (root or self).findChildren(cls):
                w.setFocusPolicy(Qt.StrongFocus)
                w.installEventFilter(self._wheel_guard)

    def refresh_nav(self):
        show = self.c.settings.sidebar_icons
        t = self.c.theme
        for row, entry in enumerate(NAV):
            item = self.nav.item(row)
            if isinstance(entry, tuple):
                item.setText(tr(entry[1]))
                item.setForeground(t.muted)
                continue
            item.setText(tr(NAV_LABELS.get(entry, entry)))
            item.setIcon(icons.icon(PAGE_ICONS[entry], t.muted, selected=t.text) if show else QIcon())

    def current_page(self) -> str:
        i = self.stack.currentIndex()
        return PAGES[i] if 0 <= i < len(PAGES) else "Home"

    def _apps(self) -> QWidget:
        from .appspage import AppsPage
        self.apps_page = AppsPage(self.c)
        return self.apps_page

    def _build_page(self, name: str):
        """Builds a page that was left for later, in its place."""
        if name not in self._unbuilt:
            return
        self._unbuilt.discard(name)
        i = PAGES.index(name)
        stand_in = self.stack.widget(i)
        built = self._builders[name]() if name in self._builders else self._soon(name)
        self.stack.insertWidget(i, built)
        self.stack.removeWidget(stand_in)
        stand_in.deleteLater()
        self._no_accidental_scrolling(built)

    def _row_clicked(self, item):
        """The lit row clicked again (from a page that shares it): go to the row's own page."""
        row = self.nav.row(item)
        if 0 <= row < len(self.nav_rows) and self.nav_rows[row] and self.current_page() != self.nav_rows[row]:
            self._page_changed(row)

    def _page_changed(self, row: int):
        name = self.nav_rows[row] if 0 <= row < len(self.nav_rows) else None
        if name is None:
            return
        if self._opening and NAV_OWNER.get(self._opening) == name:
            name = self._opening
        self._build_page(name)
        self.stack.setCurrentIndex(PAGES.index(name))
        winutil.log_memory(f"page {name}")
        QTimer.singleShot(2500, lambda n=name: winutil.log_memory(f"page {n}, 2.5 s later"))
        if name == "Apps":
            self.apps_page.ensure_loaded()
        elif name in ("History", "Home"):
            self.refresh_history()
        elif name == "About":
            self.refresh_memory()

    def _soon_all(self) -> QWidget:
        """Every tool that's on the way, each opening its own page."""
        dark, dpr = self.c.theme.dark, self.devicePixelRatioF()
        w, lay = page(tr("Coming soon"), tr("New tools on the way. Each arrives in a free update."))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for i, (name, product) in enumerate(SOON.items()):
            title, intro, _features = SOON_TEXT[name]
            grid.addWidget(ToolCard(product, tr(title), tr(intro), lambda n=name: self.open_page(n), dark, dpr,
                                    pill=tr("Soon")), i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        lay.addStretch()
        return w

    def _soon(self, name: str) -> QWidget:
        """A page for a tool that's still being made: what it will do."""
        product = SOON[name]
        title, intro, features = SOON_TEXT[name]
        w, lay = page(tr(title))
        lay.itemAt(0).widget().hide()  # the big title is shown next to the tile instead
        lay.addLayout(tool_header(product, tr(title), tr(intro), self.c.theme.dark, self.devicePixelRatioF(),
                                  pill=tr("Coming soon")))
        box, bl = card(10)
        bl.addWidget(label(tr("What it will do"), "h2"))
        for text in features:
            row = QHBoxLayout()
            row.setSpacing(10)
            dot = QLabel()
            dot.setPixmap(icons.pixmap("add", 16, self.c.theme.accent, self.devicePixelRatioF()))
            dot.setFixedWidth(18)
            row.addWidget(dot, 0, Qt.AlignTop)
            row.addWidget(label(tr(text), wrap=True), 1)
            bl.addLayout(row)
        lay.addWidget(box)
        lay.addWidget(label(tr("This page shows what's coming. It isn't ready yet, and it will arrive in a free "
                               "update."), "muted", wrap=True))
        lay.addStretch()
        return w

    # ---- pages

    def _home(self) -> QWidget:
        """The start page: every tool, what's coming, and what happened lately."""
        dark, dpr = self.c.theme.dark, self.devicePixelRatioF()
        w, lay = page(tr("Welcome to Corefold"), tr("Your everyday tools in one place. Pick one to start."))
        lay.addWidget(label(tr("Your tools"), "section"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        self.tool_cards = {}
        for i, (name, product, title, text) in enumerate(TOOLS):
            c = ToolCard(product, tr(title), tr(text), lambda n=name: self.open_page(n), dark, dpr)
            self.tool_cards[name] = c
            grid.addWidget(c, i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        lay.addWidget(label(tr("Coming soon"), "section"))
        soon = QGridLayout()
        soon.setHorizontalSpacing(12)
        soon.setVerticalSpacing(12)
        for i, (name, product) in enumerate(SOON.items()):
            soon.addWidget(ToolCard(product, tr(SOON_TEXT[name][0]), "", lambda n=name: self.open_page(n), dark, dpr,
                                    small=True, pill=tr("Soon")), i // 2, i % 2)
        soon.setColumnStretch(0, 1)
        soon.setColumnStretch(1, 1)
        lay.addLayout(soon)

        head = QHBoxLayout()
        head.addWidget(label(tr("Recent activity"), "section"))
        head.addStretch()
        head.addWidget(button(tr("See all"), "flat", lambda: self.open_page("History")))
        lay.addLayout(head)
        recent, self.recent_lay = card(8)
        lay.addWidget(recent)
        lay.addStretch()
        return w

    def _fill_recent(self):
        """The last few things Corefold did, on the Home page."""
        while self.recent_lay.count():
            item = self.recent_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        entries = self.c.history.items[:5]
        if not entries:
            self.recent_lay.addWidget(label(tr("Nothing yet. What you do with Corefold shows up here."), "muted",
                                            wrap=True))
            return
        arrow = "←" if i18n.is_rtl() else "→"
        for e in entries:
            n_in = max(e.input_count, len(e.inputs))
            n_out = max(e.output_count, len(e.outputs))
            what = e.inputs[0] if n_in == 1 and e.inputs else tr("{count} files", count=n_in)
            result = Path(e.outputs[0]).name if n_out == 1 and e.outputs else \
                tr("{count} new files", count=n_out) if n_out else tr("no new file")
            row = QHBoxLayout()
            row.setSpacing(10)
            text = label(f"<b>{_html(e.tool)}</b>&nbsp;&nbsp;{_html(short_name(what, 40))} {arrow} "
                         f"{_html(short_name(result, 40))}")
            text.setTextFormat(Qt.RichText)
            row.addWidget(text, 1)
            row.addWidget(label(time_ago(e.when), "muted"))
            holder = QWidget()
            holder.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            self.recent_lay.addWidget(holder)

    def _converter(self) -> QWidget:
        w, lay = page(tr("Corefold Converter"))
        lay.itemAt(0).widget().hide()  # shown in the heading with the tile instead
        self.power = button("", "power")
        self.power.setCheckable(True)
        self.power.setChecked(self.c.settings.enabled)
        self.power.toggled.connect(self.c.set_enabled)
        lay.addLayout(tool_header("converter", tr("Corefold Converter"),
                                  tr("Convert, shrink and edit pictures, videos, music, PDFs and documents with one "
                                     "drag."), self.c.theme.dark, self.devicePixelRatioF(), self.power))
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

        self.drop = DropZone(self._choose_files, self.c.paste)
        self.drop.dropped.connect(self.show_files)
        lay.addWidget(self.drop)
        self.panel = FilePanel(self.c)
        lay.addWidget(self.panel)

        again = QHBoxLayout()
        self.again_btn = button("", None, lambda: self.c.do_again())
        self.again_btn.setToolTip(tr("Pick other files and do the same thing to them, with the same choices."))
        again.addWidget(self.again_btn)
        again.addStretch()
        lay.addLayout(again)
        self.home_stats = label("", "muted")
        lay.addWidget(self.home_stats)
        lay.addWidget(label(tr("Corefold keeps running in the system tray (by the clock) after you close this "
                               "window. It uses almost no memory or power while it waits."), "muted", wrap=True))
        lay.addStretch()
        return w

    def _general(self) -> QWidget:
        s = self.c.settings
        w, lay = page(tr("General"))

        box, bl = card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)

        self.language = QComboBox()
        for key, text in LANGUAGES.items():
            self.language.addItem(tr(text) if key == "system" else text, key)
        self.language.setCurrentIndex(max(0, self.language.findData(s.language)))
        self.language.currentIndexChanged.connect(self._language_changed)

        self.shortcut = QComboBox()
        for key, text in SHORTCUT_LABELS.items():
            self.shortcut.addItem(tr(text), key)
        self.shortcut.setCurrentIndex(max(0, self.shortcut.findData(s.shortcut)))
        self.shortcut.currentIndexChanged.connect(self._general_changed)

        self.save_mode = QComboBox()
        self.save_mode.addItem(tr("Next to the original file"), "beside")
        self.save_mode.addItem(tr("Always in one folder"), "folder")
        self.save_mode.setCurrentIndex(max(0, self.save_mode.findData(s.save_mode)))
        self.save_mode.currentIndexChanged.connect(self._general_changed)

        self.folder_row = QWidget()
        fr = QHBoxLayout(self.folder_row)
        fr.setContentsMargins(0, 0, 0, 0)
        self.folder_label = label("", "muted")
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        fr.addWidget(self.folder_label, 1)
        fr.addWidget(button(tr("Open"), on_click=self._open_output_folder))
        fr.addWidget(button(tr("Change…"), on_click=self._pick_folder))

        self.quality = QSpinBox()
        self.quality.setRange(50, 100)
        self.quality.setSuffix(" %")
        self.quality.setValue(s.jpg_quality)
        self.quality.setMinimumWidth(96)
        self.quality.valueChanged.connect(self._general_changed)
        q_row = QHBoxLayout()
        q_row.addWidget(self.quality)
        q_row.addWidget(label(tr("for JPG and WebP. Higher = better quality, bigger file."), "muted"), 1)

        self.sound = QComboBox()
        for key, text in SOUNDS.items():
            self.sound.addItem(tr(text), key)
        self.sound.setCurrentIndex(max(0, self.sound.findData(s.sound)))
        self.sound.currentIndexChanged.connect(self._sound_changed)
        self.sound.setMinimumWidth(160)
        snd_row = QHBoxLayout()
        snd_row.setSpacing(8)
        snd_row.addWidget(self.sound)
        snd_row.addWidget(button(tr("▶ Play"), on_click=self.c.play_done_sound))
        snd_row.addStretch()
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(s.volume)
        self.volume.setCursor(Qt.PointingHandCursor)
        self.volume_label = label(f"{s.volume}%", "muted")
        self.volume_label.setFixedWidth(44)
        self.volume_label.setAlignment(Qt.AlignVCenter | Qt.AlignTrailing)
        self.volume.valueChanged.connect(self._volume_changed)
        self.volume.sliderReleased.connect(self.c.play_done_sound)
        vol_row = QHBoxLayout()
        self.vol_icons = (label(), label())  # quiet / loud speakers, drawn in apply_theme
        vol_row.setSpacing(10)
        for lab in self.vol_icons:
            lab.setFixedSize(20, 20)
        vol_row.addWidget(self.vol_icons[0])
        vol_row.addWidget(self.volume, 1)
        vol_row.addWidget(self.vol_icons[1])
        vol_row.addWidget(self.volume_label)

        self.unavailable = QComboBox()
        self.unavailable.addItem(tr("Show them dimmed (with the reason)"), True)
        self.unavailable.addItem(tr("Hide them"), False)
        self.unavailable.setCurrentIndex(0 if s.show_unavailable else 1)
        self.unavailable.currentIndexChanged.connect(self._general_changed)

        rows = ((tr("Language"), self.language), (tr("Key to hold while dragging"), self.shortcut),
                (tr("Save new files"), self.save_mode), ("", self.folder_row), (tr("Image quality"), q_row),
                (tr("Sound when done"), snd_row), (tr("Volume"), vol_row),
                (tr("Formats that don't fit a file"), self.unavailable))
        for r, (text, widget) in enumerate(rows):
            if text:
                grid.addWidget(label(text), r, 0)
            (grid.addLayout if isinstance(widget, QHBoxLayout) else grid.addWidget)(widget, r, 1)
        grid.setColumnStretch(1, 1)
        bl.addLayout(grid)
        lay.addWidget(box)

        # right-click menu
        menu, ml = card()
        ml.addWidget(label(tr("Right-click menu"), "h2"))
        self.context = checkbox(tr("Show Corefold when I right-click a file or folder"), s.context_menu,
                                self.c.set_context_menu)
        ml.addWidget(self.context)
        self.menu_style = Segmented([(k, tr(v)) for k, v in MENU_STYLES.items()], s.menu_style)
        self.menu_style.changed.connect(self.c.set_menu_style)
        ml.addWidget(self.menu_style)
        self.menu_hint = label("", "muted", wrap=True)
        ml.addWidget(self.menu_hint)
        ml.addWidget(label(tr("On Windows 11, it's under \"Show more options\" (or Shift + right-click)."), "muted",
                           wrap=True))
        lay.addWidget(menu)

        checks, cl = card()
        self.ask_names = checkbox(tr("When converting several files, ask for a new name (like vacation-1, "
                                     "vacation-2…)"), s.ask_names, self._general_changed)
        self.show_progress = checkbox(tr("Show a popup while converting and when it's done"), s.show_progress,
                                      self._general_changed)
        self.reveal = checkbox(tr("Open the folder with the new file after converting"), s.reveal_after,
                               self._general_changed)
        self.startup = checkbox(tr("Start Corefold when Windows starts"),
                                startup_enabled() if self.pending_startup is None else self.pending_startup,
                                self._startup_toggled)
        self.problem_sound = checkbox(tr("Play a soft sound when something goes wrong"), s.sound_problems,
                                      self._general_changed)
        self.tick = checkbox(tr("Play a tiny tick when the wheel opens"), s.sound_tick, self._general_changed)
        self.downloads = checkbox(tr("When an iPhone photo (HEIC) or WebP picture lands in Downloads, offer a "
                                     "JPG copy"), s.watch_downloads, self.c.set_watch_downloads)
        self.pack_everywhere = checkbox(tr("Show ZIP / 7z options on every wheel"), s.pack_on_every_wheel,
                                        self._general_changed)
        for cb in (self.ask_names, self.show_progress, self.reveal, self.problem_sound, self.tick, self.downloads,
                   self.pack_everywhere, self.startup):
            cl.addWidget(cb)
        lay.addWidget(checks)

        # keeping the PC fast
        perf, pl = card()
        pl.addWidget(label(tr("Keep my PC fast"), "h2"))
        self.low_priority = checkbox(tr("Convert in the background, so games and other apps always come first"),
                                     s.low_priority, self._performance_changed)
        self.one_at_a_time = checkbox(tr("Convert one thing at a time (the rest wait in line)"), s.one_at_a_time,
                                      self._performance_changed)
        pl.addWidget(self.low_priority)
        pl.addWidget(self.one_at_a_time)
        pl.addWidget(label(tr("Corefold also lets go of memory it doesn't need when it's idle, and closes this window "
                              "completely a little while after you close it."), "muted", wrap=True))
        lay.addWidget(perf)
        lay.addLayout(_reset_row(lambda: self.reset_to_defaults(general_keys())))
        lay.addStretch()
        return w

    def _look(self) -> QWidget:
        s = self.c.settings
        w, lay = page(tr("Look"), tr("Make Corefold yours. The preview updates as you change things."))

        controls, cl = card(8)
        appearance = Segmented([(k, tr(v)) for k, v in APPEARANCES.items()], s.appearance)
        appearance.changed.connect(lambda v: self._look_changed("appearance", v))
        self.swatches = Swatches(s.accent, s.custom_color)
        self.swatches.changed.connect(lambda v: self._look_changed("accent", v))
        self.swatches.custom_picked.connect(lambda v: setattr(self.c.settings, "custom_color", v))
        self.accent_name = label("", "muted")
        style = Segmented([(k, tr(v)) for k, v in WHEEL_STYLES.items()], s.wheel_style, per_row=3)
        style.changed.connect(lambda v: self._look_changed("wheel_style", v))
        size = Segmented([(k, tr(v)) for k, v in WHEEL_SIZE_NAMES.items()], s.wheel_size)
        size.changed.connect(lambda v: self._look_changed("wheel_size", v))
        labels = Segmented([(k, tr(v)) for k, v in LABEL_STYLES.items()], s.label_style)
        labels.changed.connect(lambda v: self._look_changed("label_style", v))
        hover = Segmented([(k, tr(v)) for k, v in HOVER_STYLES.items()], s.hover_style)
        hover.changed.connect(lambda v: self._look_changed("hover_style", v))
        colors = Segmented([(k, tr(v)) for k, v in SLICE_COLORS.items()], s.slice_colors)
        colors.changed.connect(lambda v: self._look_changed("slice_colors", v))
        text_size = Segmented([(k, tr(v)) for k, v in TEXT_SIZE_NAMES.items()], s.text_size)
        text_size.changed.connect(lambda v: self._look_changed("text_size", v))
        position = Segmented([(k, tr(v)) for k, v in WHEEL_POSITIONS.items()], s.wheel_position)
        position.changed.connect(lambda v: self._look_changed("wheel_position", v))
        self.per_page = QSpinBox()
        self.per_page.setRange(6, 16)
        self.per_page.setValue(s.per_page)
        self.per_page.setSuffix(tr(" slices"))
        self.per_page.valueChanged.connect(lambda v: self._look_changed("per_page", v))
        per_row = QHBoxLayout()
        per_row.addWidget(self.per_page)
        per_row.addWidget(label(tr("then a \"More\" slice shows the rest"), "muted"), 1)
        per_holder = QWidget()
        per_holder.setLayout(per_row)
        per_row.setContentsMargins(0, 0, 0, 0)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(60, 100)
        self.opacity.setValue(s.wheel_opacity)
        self.opacity.setCursor(Qt.PointingHandCursor)
        self.opacity.valueChanged.connect(lambda v: self._look_changed("wheel_opacity", v))
        self.animations = checkbox(tr("Animations"), s.animations, lambda v: self._look_changed("animations", v))

        for title, widget in ((tr("Appearance"), appearance), (tr("Accent colour"), self.swatches),
                              (None, self.accent_name), (tr("Wheel shape"), style), (tr("Wheel size"), size),
                              (tr("Slice labels"), labels), (tr("Text size"), text_size),
                              (tr("Pointing at a slice"), hover), (tr("Slice colours"), colors),
                              (tr("Options per page"), per_holder), (tr("Where the wheel opens"), position),
                              (tr("Wheel see-through"), self.opacity)):
            if title:
                cl.addSpacing(6)
                cl.addWidget(label(title, "h2"))
            cl.addWidget(widget)
        cl.addSpacing(10)
        cl.addWidget(self.animations)
        self.numbers = checkbox(tr("Show numbers on the slices when the wheel is opened from the right-click menu "
                                   "(press 1–9 to pick, 0 for More)"), s.wheel_numbers,
                                lambda v: self._look_changed("wheel_numbers", v))
        cl.addWidget(self.numbers)
        cl.addStretch()

        # the window and the popups
        more, ml = card(8)
        win_text = Segmented([(k, tr(v)) for k, v in WINDOW_TEXT.items()], s.window_text)
        win_text.changed.connect(lambda v: self._look_changed("window_text", v))
        corner = QComboBox()
        for key, text in TOAST_CORNERS.items():
            corner.addItem(tr(text), key)
        corner.setCurrentIndex(max(0, corner.findData(s.toast_corner)))
        corner.currentIndexChanged.connect(lambda _: self._look_changed("toast_corner", corner.currentData()))
        corner.setMaximumWidth(320)
        toast_time = Segmented([(k, tr(v)) for k, v in TOAST_TIMES.items()], s.toast_time)
        toast_time.changed.connect(lambda v: self._look_changed("toast_time", v))
        ml.addWidget(label(tr("Text size in Corefold's windows"), "h2"))
        ml.addWidget(win_text)
        ml.addSpacing(6)
        ml.addWidget(label(tr("Where popups appear"), "h2"))
        prow = QHBoxLayout()
        prow.addWidget(corner)
        prow.addWidget(button(tr("Show an example"), on_click=lambda: self.c.toast.notify(
            tr("Done"), tr("This is where popups will appear."), linger_ms=3000)))
        prow.addStretch()
        ml.addLayout(prow)
        ml.addSpacing(6)
        ml.addWidget(label(tr("How long popups stay"), "h2"))
        ml.addWidget(toast_time)
        ml.addSpacing(6)
        ml.addWidget(checkbox(tr("Icons in the sidebar"), s.sidebar_icons,
                              lambda v: self._look_changed("sidebar_icons", v)))

        preview_box, pl = card()
        pl.setContentsMargins(12, 12, 12, 12)
        pl.addWidget(label(tr("Preview"), "muted"), 0, Qt.AlignHCenter)
        self.preview = WheelPreview(self.c.settings, self.c.theme)
        pl.addWidget(self.preview, 0, Qt.AlignCenter)
        pl.addWidget(label(tr("Dimmed = can't be used for that file") + "\n"
                           + tr("Dots = pages (\"More\" shows the next one)"), "muted"), 0, Qt.AlignHCenter)
        self.legend = label("", "muted")
        self.legend.setWordWrap(True)
        pl.addWidget(self.legend)
        pl.addStretch()

        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(controls, 1)
        row.addWidget(preview_box)
        lay.addLayout(row)
        lay.addWidget(more)
        lay.addLayout(_reset_row(lambda: self.reset_to_defaults(LOOK_KEYS)))
        lay.addStretch()
        return w

    def _formats(self) -> QWidget:
        w, lay = page(tr("Formats"), tr("Untick anything you don't use. ☆ pins an option to the top of its wheel, "
                                        "and ▲ ▼ change the order. The right-click menu follows these choices too."))
        self.tool_checks: dict[str, QCheckBox] = {}
        self.tool_stars: dict[str, QPushButton] = {}
        self.tool_rows: dict[str, list] = {}
        self.format_grids: dict[str, QGridLayout] = {}
        self.format_search = QLineEdit()
        self.format_search.setPlaceholderText(tr("Search options (e.g. PNG, resize, ZIP)"))
        self.format_search.setClearButtonEnabled(True)
        self.format_search.textChanged.connect(self._filter_formats)
        lay.addWidget(self.format_search)
        for kind in catalog():
            lay.addWidget(label(tr(CATEGORY_NAMES[kind]), "h2"))
            box, _ = card()
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(6)
            box.layout().addLayout(grid)
            self.format_grids[kind] = grid
            self._fill_format_grid(kind)
            lay.addWidget(box)
        row = QHBoxLayout()
        row.addWidget(button(tr("Show everything again"), on_click=self._show_all_tools))
        row.addWidget(button(tr("Reset the order"), on_click=self._reset_order))
        row.addStretch()
        row.addWidget(button(tr("Reset to defaults"), on_click=lambda: self.reset_to_defaults(FORMAT_KEYS)))
        lay.addLayout(row)
        lay.addStretch()
        return w

    def _history(self) -> QWidget:
        w, lay = page(tr("History"))
        stats, sl = card()
        row = QHBoxLayout()
        self.stat_files = label("0", "stat")
        self.stat_saved = label("0", "stat")
        self.stat_jobs = label("0", "stat")
        for value, text in ((self.stat_files, tr("files converted")), (self.stat_saved, tr("space saved")),
                            (self.stat_jobs, tr("conversions"))):
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

        head = QHBoxLayout()
        head.addWidget(label(tr("Recent conversions"), "h2"))
        head.addStretch()
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText(tr("Search"))
        self.history_search.setClearButtonEnabled(True)
        self.history_search.setMaximumWidth(240)
        self.history_search.textChanged.connect(self._filter_history)
        head.addWidget(self.history_search)
        lay.addLayout(head)
        self.history_list = QListWidget()
        self.history_list.setObjectName("history")
        self.history_list.setMinimumHeight(220)
        self.history_list.itemDoubleClicked.connect(lambda _: self._history_open())
        self.history_list.currentRowChanged.connect(lambda _: self._history_buttons())
        lay.addWidget(self.history_list, 1)
        self.history_empty = label(tr("Nothing yet. Converted files will show up here."), "muted")
        # takes the free space while the list is hidden, so the page doesn't spread out
        lay.addWidget(self.history_empty, 1, Qt.AlignTop)
        btns = QHBoxLayout()
        self.h_open = button(tr("Open"), on_click=self._history_open)
        self.h_show = button(tr("Show in folder"), on_click=self._history_show)
        btns.addWidget(self.h_open)
        btns.addWidget(self.h_show)
        btns.addStretch()
        btns.addWidget(button(tr("Clear history"), on_click=self._history_clear))
        btns.addWidget(button(tr("Reset stats"), on_click=self._stats_reset))
        lay.addLayout(btns)
        return w

    def _updates(self) -> QWidget:
        s = self.c.settings
        w, lay = page(tr("Updates"), tr("Corefold can update itself, so you never have to download it again."))
        box, bl = card()
        self.update_status = label("", "h2", wrap=True)
        self.update_detail = label("", "muted", wrap=True)
        self.update_now = button(tr("Update now"), "primary", self.c.start_update)
        self.check_now = button(tr("Check now"), on_click=lambda: self.c.check_updates(manual=True))
        row = QHBoxLayout()
        row.addWidget(self.update_now)
        row.addWidget(self.check_now)
        row.addStretch()
        bl.addWidget(self.update_status)
        bl.addWidget(self.update_detail)
        bl.addLayout(row)
        lay.addWidget(box)

        opts, ol = card()
        self.auto_check = checkbox(tr("Check for updates automatically"), s.check_updates,
                                   self._update_options_changed)
        self.auto_install = checkbox(tr("Install updates by themselves (Corefold restarts for a moment)"),
                                     s.auto_update, self._update_options_changed)
        ol.addWidget(self.auto_check)
        ol.addWidget(self.auto_install)
        ol.addWidget(label(tr("Updates come from the official Corefold release page, are checked for damage "
                              "before installing, and keep all your settings."), "muted", wrap=True))
        lay.addWidget(opts)
        lay.addStretch()
        return w

    def _about(self) -> QWidget:
        w, lay = page(tr("About"))
        top = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(self.icon.pixmap(72, 72))
        text = QVBoxLayout()
        text.addWidget(label(APP_NAME, "h2"))
        text.addWidget(label(tr("Version {version}", version=__version__), "muted"))
        text.addWidget(label(tr("Shift-drag file converter for Windows. Everything happens on your PC; "
                                "nothing is uploaded."), "muted", wrap=True))
        self.memory = label("", "muted")
        text.addWidget(self.memory)
        top.addWidget(logo, 0, Qt.AlignTop)
        top.addSpacing(10)
        top.addLayout(text, 1)
        lay.addLayout(top)

        row = QHBoxLayout()
        row.addWidget(button(tr("Show the tour"), on_click=self.c.show_tour))
        row.addWidget(button(tr("Open log folder"), on_click=self._open_logs))
        row.addWidget(button(tr("Quit Corefold"), on_click=self.c.ask_quit))
        row.addStretch()
        lay.addLayout(row)

        if UPDATE_REPO:
            owner, _, name = UPDATE_REPO.partition("/")
            links, ll = card()
            ll.addWidget(label(tr("Help make Corefold better"), "h2"))
            ll.addWidget(label(tr("Found a bug or have an idea? Tell us. It takes a minute and every message is "
                                  "read."), "muted", wrap=True))
            lrow = QHBoxLayout()
            self.link_buttons = [
                (button(tr("Send feedback"), "primary", lambda: QDesktopServices.openUrl(
                    QUrl(f"https://github.com/{UPDATE_REPO}/issues/new"))), "chat"),
                (button(tr("Website"), on_click=lambda: QDesktopServices.openUrl(
                    QUrl(f"https://{owner}.github.io/{name}/"))), "globe"),
                (button(tr("Privacy"), on_click=lambda: QDesktopServices.openUrl(
                    QUrl(f"https://{owner}.github.io/{name}/privacy.html"))), "lock_closed"),
            ]
            for b, _ in self.link_buttons:
                lrow.addWidget(b)
            lrow.addWidget(button(tr("All releases"), on_click=lambda: QDesktopServices.openUrl(
                QUrl(f"https://github.com/{UPDATE_REPO}/releases"))))
            lrow.addStretch()
            ll.addLayout(lrow)
            lay.addWidget(links)

        remove, rl = card()
        rl.addWidget(label(tr("Remove Corefold"), "h2"))
        if installed_uninstaller():
            rl.addWidget(label(tr("Removes the app, its desktop icon, Start menu entry and right-click menu. You'll "
                                  "be asked whether to delete your settings too. Files you converted are kept."),
                               "muted", wrap=True))
            rl.addWidget(button(tr("Uninstall Corefold…"), "danger", self.c.uninstall), 0, Qt.AlignLeading)
            rl.addWidget(label(tr("You can also uninstall it from Windows Settings → Apps → Installed apps."),
                               "muted", wrap=True))
        else:
            rl.addWidget(label(tr("This copy wasn't set up with the installer. The button below deletes "
                                  "Corefold's settings, removes its right-click menu, stops it starting with Windows, "
                                  "and closes it. Then delete the Corefold folder to finish."), "muted", wrap=True))
            rl.addWidget(button(tr("Remove settings and quit"), "danger", self.c.remove_portable), 0,
                         Qt.AlignLeading)
        lay.addWidget(remove)
        lay.addStretch()
        return w

    # ---- reacting to changes

    def refresh(self):
        s = self.c.settings
        key = s.shortcut_label
        self.step_labels[0].setText(tr("Hold <b>{key}</b> and start dragging a file in File Explorer or on your "
                                       "desktop, or right-click it and choose <b>Corefold</b>.", key=key))
        self.step_labels[1].setText(tr("A wheel of options pops up. Dimmed ones don't work for that file (point at "
                                       "one to see why). The dots in the middle show how many pages there are: hold "
                                       "the file over \"More\" to see the next one."))
        where = tr("The new file appears next to the original.") if s.save_mode == "beside" else \
            tr("The new file appears in your Corefold folder.")
        self.step_labels[2].setText(tr("Drop the file on a format (or click it).") + " " + where + " "
                                    + tr("Let go in the middle to cancel."))
        self.power.setText(tr("Corefold is on") if s.enabled else tr("Paused. Click to turn on"))
        self.folder_label.setText(short_name(s.output_folder, 60))
        self.folder_label.setToolTip(s.output_folder)
        self.folder_row.setVisible(s.save_mode == "folder")
        self.accent_name.setText(tr("Your colour") if s.accent == "custom" else tr(ACCENTS[s.accent][0]))
        self.legend.setText(tr("Colours: blue = convert · purple = edit · green = make smaller · pink = read · "
                               "amber = archive") if s.slice_colors == "group" else "")
        self.legend.setVisible(s.slice_colors == "group")
        self.menu_style.setEnabled(s.context_menu)
        self.menu_hint.setText(tr("Right-click a photo → Corefold → Convert to PNG, Resize…, Remove the background… "
                                  "(like 7-Zip's menu).") if s.menu_style == "windows" else
                               tr("Right-click → Convert with Corefold opens the wheel of options."))
        st = self.c.history.stats
        self.home_stats.setText(tr("So far: {count} files converted · {size} saved", count=locale().toString(st.files),
                                   size=human_size(st.bytes_saved)) if st.files else "")
        self.home_stats.setVisible(bool(st.files))
        conv = self.tool_cards["Converter"].stat
        conv.setText(tr("{count} files converted", count=locale().toString(st.files)) if st.files else "")
        conv.setVisible(bool(st.files))
        self.tool_cards["Apps"].stat.setVisible(False)
        self.power.setToolTip(tr("Turns the wheel and the right-click menu on or off."))
        self.refresh_again()
        self.update_save_bar()

    def refresh_again(self):
        text = self.c.again_text()
        self.again_btn.setText("↻  " + text if text else "")
        self.again_btn.setVisible(bool(text))

    def refresh_memory(self):
        used = memory_use()
        self.memory.setText(tr("Memory in use right now: {size}", size=human_size(used)) if used else "")
        self.memory.setVisible(bool(used))

    def refresh_history(self):
        st = self.c.history.stats
        loc = locale()
        self.stat_files.setText(loc.toString(st.files))
        self.stat_saved.setText(human_size(st.bytes_saved))
        self.stat_jobs.setText(loc.toString(st.jobs))
        self.stat_since.setText(tr("Since {date}", date=date_text(st.since, long=True)))
        self.history_list.clear()
        for e in self.c.history.items:
            n_in = max(e.input_count, len(e.inputs))
            n_out = max(e.output_count, len(e.outputs))
            what = e.inputs[0] if n_in == 1 and e.inputs else tr("{count} files", count=n_in)
            if n_out == 1 and e.outputs:
                result = Path(e.outputs[0]).name
            elif n_out:
                result = tr("{count} new files", count=n_out)
            else:
                result = tr("no new file")
            warn = "  ·  " + tr("{count} failed", count=e.errors) if e.errors else ""
            arrow = "←" if i18n.is_rtl() else "→"
            item = QListWidgetItem(f"{e.tool}:  {what}  {arrow}  {result}{warn}      {time_ago(e.when)}")
            item.setData(Qt.UserRole, e.outputs)
            self.history_list.addItem(item)
        empty = not self.c.history.items
        self.history_list.setVisible(not empty)
        self.history_empty.setVisible(empty)
        self._fill_recent()
        self._filter_history()
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

    def _filter_history(self, *_):
        text = self.history_search.text().strip().lower()
        for i in range(self.history_list.count()):
            item = self.history_list.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _confirm(self, title: str, text: str, yes: str) -> bool:
        box = QMessageBox(QMessageBox.Question, title, text, parent=self)
        ok = box.addButton(yes, QMessageBox.DestructiveRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        chosen = box.clickedButton() is ok
        box.deleteLater()
        return chosen

    def _history_clear(self):
        if self._confirm(tr("Clear history"), tr("Remove the list of recent conversions? Your files aren't touched."),
                         tr("Clear history")):
            self.c.history.clear()
            self.refresh_history()

    def _stats_reset(self):
        if self._confirm(tr("Reset stats"), tr("Start counting files converted and space saved from zero?"),
                         tr("Reset stats")):
            self.c.history.reset_stats()
            self.refresh_history()

    def _language_changed(self, *_):
        choice = self.language.currentData()
        if choice == self.c.settings.language:
            return
        self.c.settings.language = choice
        self.c.settings_changed()  # the right-click menu changes language when Corefold restarts
        # (the restart is offered when the change is saved)

    def _ask_restart_for_language(self):
        choice = self.c.settings.language
        if i18n.resolve(choice) == i18n.language():
            return  # e.g. "Match Windows" while Windows is in the language already in use
        new = i18n.resolve(choice)
        # ask in the new language, since that's the one the person picked
        old = i18n.language()
        i18n.set_language(new)
        title, text = tr("Restart Corefold?"), tr("Corefold needs to restart to change the language. It only takes "
                                                  "a moment.")
        now_text, later_text = tr("Restart now"), tr("Later")
        i18n.set_language(old)
        box = QMessageBox(QMessageBox.Question, title, text, parent=self)
        now = box.addButton(now_text, QMessageBox.AcceptRole)
        box.addButton(later_text, QMessageBox.RejectRole)
        box.exec()
        chosen = box.clickedButton() is now
        box.deleteLater()
        if chosen:
            self.c.restart("General")

    def _startup_toggled(self, on: bool):
        self.pending_startup = on
        self.update_save_bar()

    def _general_changed(self, *_):
        s = self.c.settings
        s.shortcut = self.shortcut.currentData()
        s.save_mode = self.save_mode.currentData()
        s.jpg_quality = self.quality.value()
        s.show_unavailable = bool(self.unavailable.currentData())
        s.ask_names = self.ask_names.isChecked()
        s.show_progress = self.show_progress.isChecked()
        s.reveal_after = self.reveal.isChecked()
        s.sound_problems = self.problem_sound.isChecked()
        s.sound_tick = self.tick.isChecked()
        changed = s.pack_on_every_wheel != self.pack_everywhere.isChecked()
        s.pack_on_every_wheel = self.pack_everywhere.isChecked()
        self.c.settings_changed(menu=changed)
        if changed:
            self.panel.refresh()

    def _performance_changed(self, *_):
        s = self.c.settings
        s.low_priority = self.low_priority.isChecked()
        s.one_at_a_time = self.one_at_a_time.isChecked()
        self.c.apply_performance()
        self.c.settings_changed()

    def _sound_changed(self, *_):
        self.c.settings.sound = self.sound.currentData()
        self.c.settings_changed()
        self.c.play_done_sound()

    def _volume_changed(self, v: int):
        self.c.settings.volume = v
        self.volume_label.setText(f"{v}%")
        self.c.settings_changed()

    def _open_logs(self):
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)  # opening a folder that isn't there shows a Windows error
        except OSError:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_DIR)))

    def _open_output_folder(self):
        folder = Path(self.c.settings.output_folder)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, tr("Save converted files in"), self.c.settings.output_folder)
        if d:
            self.c.settings.output_folder = d
            self.c.settings_changed()

    def _look_changed(self, key: str, value):
        setattr(self.c.settings, key, value)
        self.c.settings_changed()
        if key in ("appearance", "accent", "window_text"):
            self.c.apply_theme()
        elif key == "sidebar_icons":
            self.refresh_nav()
        elif key == "wheel_opacity":
            self.c.apply_theme(wheel_only=True)  # dragging the slider stays smooth: the window's look doesn't change
        self.preview.update()

    def _tool_toggled(self, key: str, on: bool):
        hidden = set(self.c.settings.hidden_tools)
        hidden.discard(key) if on else hidden.add(key)
        if sorted(hidden) == self.c.settings.hidden_tools:
            return
        self.c.settings.hidden_tools = sorted(hidden)
        if self._batch:
            return
        self.c.settings_changed(menu=True)
        self.panel.refresh()

    def _star_toggled(self, key: str, on: bool, star: QPushButton):
        favs = [k for k in self.c.settings.favorites if k != key]
        if on:
            favs.append(key)
        self.c.settings.favorites = favs
        star.setText("★" if on else "☆")
        self.c.settings_changed(menu=True)
        self.panel.refresh()

    def _show_all_tools(self):
        self._batch = True  # one save and one refresh, not one per tick box
        try:
            for cb in self.tool_checks.values():
                cb.setChecked(True)
        finally:
            self._batch = False
        self.c.settings.hidden_tools = []
        self.c.settings_changed(menu=True)
        self.panel.refresh()

    def ordered_tools(self, kind: str):
        tools = catalog(multi=True)[kind]
        order = {k: i for i, k in enumerate(self.c.settings.tool_order.get(kind, []))}
        default = {t.key: i for i, t in enumerate(tools)}
        return sorted(tools, key=lambda t: order.get(t.key, len(order) + default[t.key]))

    def _fill_format_grid(self, kind: str):
        from .panel import clear_layout
        grid = self.format_grids[kind]
        clear_layout(grid)
        hidden = set(self.c.settings.hidden_tools)
        favs = set(self.c.settings.favorites)
        tools = self.ordered_tools(kind)
        for i, t in enumerate(tools):
            star = button("★" if t.key in favs else "☆", "star")
            star.setCheckable(True)
            star.setChecked(t.key in favs)
            star.setToolTip(tr("Pin to the top of the wheel"))
            star.toggled.connect(lambda on, key=t.key, b=star: self._star_toggled(key, on, b))
            cb = checkbox(f"{t.label}  ·  {t.description}", t.key not in hidden,
                          lambda on, key=t.key: self._tool_toggled(key, on))
            up = button("▲", "small", lambda _=False, k=kind, key=t.key: self._move_tool(k, key, -1))
            down = button("▼", "small", lambda _=False, k=kind, key=t.key: self._move_tool(k, key, 1))
            up.setEnabled(i > 0)
            down.setEnabled(i < len(tools) - 1)
            up.setToolTip(tr("Move earlier on the wheel"))
            down.setToolTip(tr("Move later on the wheel"))
            self.tool_checks[t.key] = cb
            self.tool_stars[t.key] = star
            self.tool_rows[t.key] = [star, cb, up, down]
            grid.addWidget(star, i, 0)
            grid.addWidget(cb, i, 1)
            grid.addWidget(up, i, 2)
            grid.addWidget(down, i, 3)
        grid.setColumnStretch(1, 1)
        if self.format_search.text():
            self._filter_formats(self.format_search.text())

    def _filter_formats(self, text: str):
        text = text.strip().lower()
        for key, widgets in self.tool_rows.items():
            try:
                show = not text or text in widgets[1].text().lower() or text in key.lower()
                for w in widgets:
                    w.setVisible(show)
            except RuntimeError:
                pass  # a row that was just rebuilt

    def _move_tool(self, kind: str, key: str, step: int):
        keys = [t.key for t in self.ordered_tools(kind)]
        i = keys.index(key)
        j = i + step
        if 0 <= j < len(keys):
            keys[i], keys[j] = keys[j], keys[i]
            self.c.settings.tool_order[kind] = keys
            self.c.settings_changed(menu=True)
            self._fill_format_grid(kind)
            self.panel.refresh()

    def _reset_order(self):
        self.c.settings.tool_order = {}
        self.c.settings_changed(menu=True)
        for kind in self.format_grids:
            self._fill_format_grid(kind)
        self.panel.refresh()

    # ---- home page

    def _choose_files(self):
        files = choose_files(self)
        if files:
            self.show_files(files)

    def show_files(self, files):
        self.open_page("Converter")
        self.panel.set_files(files)

    def keyPressEvent(self, e):
        from PySide6.QtGui import QKeySequence
        if e.matches(QKeySequence.StandardKey.Paste):
            self.c.paste()
            return
        if e.key() == Qt.Key.Key_Escape:  # Esc closes the window, like most settings windows
            self.close()
            return
        super().keyPressEvent(e)

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
            status = tr("You have version {version}", version=current)
            detail = tr("Automatic updates aren't set up for this copy. Copies built by GitHub (see the README) "
                        "update themselves.")
        elif state == "checking":
            status, detail = tr("Checking for updates…"), ""
        elif state == "downloading" and info:
            status = tr("Downloading Corefold {version}…", version=info.version)
            detail = tr("Corefold restarts by itself when it's done.")
        elif state == "available" and info:
            status = tr("Corefold {version} is available", version=info.version)
            detail = tr("You have {version}.", version=current)
            if not c.can_self_install():
                detail += " " + tr("This copy isn't installed, so the button opens the download page.")
        elif state == "uptodate":
            status = tr("You're up to date")
            detail = tr("Corefold {version} is the newest version.", version=current)
        elif state == "error":
            status, detail = tr("Couldn't check for updates"), c.update_error[:1].upper() + c.update_error[1:]
        else:
            status = tr("You have version {version}", version=current)
            detail = tr("Corefold checks for updates shortly after it starts.")
        self.update_status.setText(status)
        self.update_detail.setText(detail)
        self.update_detail.setVisible(bool(detail))
        self.update_now.setVisible(state == "available" and info is not None)
        self.update_now.setText(tr("Update now") if c.can_self_install() else tr("Open download page"))
        self.check_now.setEnabled(c.updates_supported() and state not in ("checking", "downloading"))
        self.auto_check.setEnabled(c.updates_supported())
        self.auto_install.setEnabled(c.updates_supported() and c.can_self_install())

    def apply_theme(self, t: Theme):
        self.refresh_nav()  # the icons follow the text colour
        dpr = self.devicePixelRatioF()
        for lab, name in zip(self.vol_icons, ("speaker_0", "speaker_2")):
            lab.setPixmap(icons.pixmap(name, 18, t.muted, dpr))
        for b, name in getattr(self, "link_buttons", []):
            b.setIcon(icons.icon(name, t.on_accent if b.objectName() == "primary" else t.text))
        self.swatches.apply_theme(t)
        self.preview.theme = t
        self.preview.update()
        if self.isVisible():
            set_title_bar_dark(self, t.dark)

    def sync(self):
        """Pick up changes made from the tray menu or outside the app."""
        startup = startup_enabled() if self.pending_startup is None else self.pending_startup
        for widget, value in ((self.power, self.c.settings.enabled), (self.startup, startup),
                              (self.context, self.c.settings.context_menu),
                              (self.downloads, self.c.settings.watch_downloads)):
            widget.blockSignals(True)
            widget.setChecked(value)
            widget.blockSignals(False)
        self.refresh()

    def open_page(self, name: str):
        owner = NAV_OWNER.get(name, name)
        if owner not in self.nav_rows:
            return
        row = self.nav_rows.index(owner)
        self._opening = name if name != owner else None
        try:
            if self.nav.currentRow() == row:
                self._page_changed(row)
            else:
                self.nav.setCurrentRow(row)
        finally:
            self._opening = None

    def showEvent(self, e):
        if not self._placed:
            self._placed = True
            self._restore_place()
        super().showEvent(e)
        set_title_bar_dark(self, self.c.theme.dark)

    def _restore_place(self):
        """Opens where it was last time (moved back onto the screen if that screen is gone), or
        at a size that fits the screen."""
        from PySide6.QtCore import QByteArray
        saved = self.c.settings.window_geometry
        if saved:
            try:
                if self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii"))):
                    return
            except Exception:
                pass
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            g = screen.availableGeometry()
            w, h = min(1040, g.width() - 40), min(700, g.height() - 40)
            self.resize(max(w, self.minimumWidth()), max(h, self.minimumHeight()))
            self.move(g.left() + (g.width() - self.width()) // 2, g.top() + (g.height() - self.height()) // 2)

    def _remember_place(self):
        try:
            self.c.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
            self.c.settings.last_page = self.current_page()
            self.c.settings_changed()
        except Exception:
            pass

    # ---- drop files anywhere on the window

    def dragEnterEvent(self, e):
        if any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if p.exists()]
        if paths:
            e.acceptProposedAction()
            self.show_files(paths)

    def closeEvent(self, e):
        if self.c.session_ending:  # Windows is shutting down: never hold it up
            e.accept()
            return
        if self.is_dirty():
            box = QMessageBox(QMessageBox.Question, tr("Save your changes?"),
                              tr("You changed some settings. Keep them?"), parent=self)
            save = box.addButton(tr("Save"), QMessageBox.AcceptRole)
            drop = box.addButton(tr("Don't save"), QMessageBox.DestructiveRole)
            box.addButton(tr("Cancel"), QMessageBox.RejectRole)
            box.exec()
            chosen = box.clickedButton()
            box.deleteLater()
            if chosen is save:
                self.save_changes()
            elif chosen is drop:
                self.discard_changes(rebuild=False)
                self._remember_place()
                e.ignore()
                self.hide()
                self.c.rebuild_window(self.current_page())  # the next opening shows the saved settings
                self.c.window_closed()
                return
            else:
                e.ignore()
                return
        self._remember_place()
        e.ignore()
        self.hide()
        self.c.window_closed()
        if not self.c.told_about_tray:
            self.c.told_about_tray = True
            self.c.tray.showMessage(APP_NAME, tr("Still running in the system tray. Hold {key} and drag a file any "
                                                 "time.", key=self.c.settings.shortcut_label),
                                    QSystemTrayIcon.Information, 3500)
