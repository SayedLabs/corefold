"""The Corefold window: Home, Apps, General, Look, Formats, History, Updates and About pages."""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton,
                               QScrollArea, QSizePolicy, QSlider, QSpinBox, QStackedWidget, QSystemTrayIcon, QVBoxLayout,
                               QWidget)

from . import UPDATE_REPO, __version__, i18n, icons, winutil
from . import tools as all_tools
from . import settings as settings_mod
from .converters import CATEGORY_NAMES, catalog, human_size
from .core import short_name
from .i18n import LANGUAGES, tr
from .panel import DropZone, FilePanel, choose_files
from .settings import (ACCENTS, APP_NAME, DEFAULT_ACCENT, APPEARANCES, HOVER_STYLES, LABEL_STYLES, LOG_DIR, MENU_STYLES,
                       SHORTCUT_LABELS, SLICE_COLORS, SOUNDS, TEXT_SIZE_NAMES, TOAST_CORNERS, TOAST_TIMES,
                       WHEEL_POSITIONS, WHEEL_SIZE_NAMES, WHEEL_STYLES, WINDOW_TEXT, installed_uninstaller,
                       ARCHIVE_KEYS, BACKUP_DAYS, CONVERTER_KEYS, EXTRACT_MODES, FORMAT_KEYS, GENERAL_KEYS,
                       SLIDESHOW_SECONDS, VIEWER_KEYS, VIEWER_WHEEL, CLIP_DAYS, CLIP_MAX, CLIP_SHORTCUTS, CLIPBOARD_KEYS,
                       CAP_AFTER, CAP_FPS, CAP_RECORD_SHORTCUTS, CAP_SHORTCUTS, CAPTURE_KEYS, PLAYER_KEYS, PLAYER_SKIP,
                       PLAYER_WHEEL,
                       LOOK_KEYS, UNINSTALLER_KEYS, UNUSED_MONTHS, WHEEL_KEYS, editable_keys, set_startup,
                       startup_enabled)
from .atomic.widgets import Switch, mark_rows
from .theme import Theme
from .timefmt import date_text, locale, time_ago  # noqa: F401 (used by other pages)
from .navbar import ICON_ROLE
from .wheel import WheelPreview
from .winutil import memory_use, reveal_in_explorer, set_title_bar_dark

if TYPE_CHECKING:
    from .app import Controller

# every page, in the order they are built (names are English, shown through tr()); each tool's
# pages come from tools.py
_TOOL_SETTINGS_PAGES = [p for tool in all_tools.TOOLS for p in tool.settings_pages]
PAGES = ["Home", "History", *[tool.page for tool in all_tools.TOOLS], "Store", "General", "Look",
         *_TOOL_SETTINGS_PAGES, "Updates", "About"]
# tools still being made: page -> product tile (in the order they're planned)
SOON = {tool.page: tool.product for tool in all_tools.coming()}
# the sidebar's fixed rows (the tools a person uses go between the two headings) and its names
NAV = ["Home", "History", (None, "Tools"), "Store", (None, "App"), "General", "Updates", "About"]
NAV_LABELS = {"General": "Settings", **{tool.page: tool.nav for tool in all_tools.TOOLS}}
# Settings: a tab for the whole app and one for each tool someone uses. A tab with several pages
# shows them as smaller tabs underneath.
BASE_SETTINGS_TABS = [("General", "General", ["General"]), ("Look", "Appearance", ["Look"])]
SETTINGS_TABS = BASE_SETTINGS_TABS + [(tool.settings, tool.nav, list(tool.settings_pages))
                                      for tool in all_tools.TOOLS if tool.settings]
SETTINGS_SUBTABS = {"ConvOptions": "Options", "WheelLook": "Wheel", "Formats": "Formats"}
SETTINGS_PAGES = ["General", "Look", *_TOOL_SETTINGS_PAGES]
TOOL_SETTINGS = {tool.page: tool.settings for tool in all_tools.TOOLS if tool.settings}
# pages without a sidebar row of their own: the row that's lit
NAV_OWNER = {**{name: "Store" for name in SOON}, **{name: "General" for name in SETTINGS_PAGES if name != "General"}}
PAGE_ICONS = {"Home": "home", "General": "settings", "Look": "paint_brush", "Formats": "puzzle_piece",  # icons.py
              "History": "history", "Updates": "arrow_sync", "About": "info", "Store": "apps",
              **{tool.page: tool.icon for tool in all_tools.TOOLS}}


COMPACT_NAV = 60   # the sidebar's width with icons only

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
    """An on/off setting: a whole row with the words on one side and a switch on the other."""
    cb = Switch(text)
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
    """A tool's own heading, on a banner washed with the tool's colour: its tile, its name (and a pill like
    "Coming soon"), one line about it, and something on the far side (like the on/off switch)."""
    from .atomic.widgets import GlowPanel
    banner = GlowPanel(color=QColor(icons.PRODUCT_COLORS.get(product, ("#3cc7b2", "#0f8f7c"))[0 if dark else 1]),
                       rings=False)
    head = QHBoxLayout(banner)
    head.setContentsMargins(20, 18, 20, 18)
    head.setSpacing(16)
    tile = QLabel()
    tile.setPixmap(icons.product_tile(product, 56, dark, dpr))
    tile.setFixedSize(58, 58)
    head.addWidget(tile, 0, Qt.AlignTop)
    words = QVBoxLayout()
    words.setSpacing(3)
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
        head.addWidget(extra, 0, Qt.AlignVCenter)
    outer = QHBoxLayout()
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(banner)
    return outer


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
        lay.setContentsMargins(16, 14, 16, 14) if small else lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10 if small else 4)
        tile = QLabel()
        side = 40
        tile.setPixmap(icons.product_tile(product, side, dark, dpr))
        tile.setFixedSize(side + 2, side + 2)
        tile.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.title = label(title, "cardTitle")
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents)
        if small:
            lay.addWidget(tile, 0, Qt.AlignTop)
            words = QVBoxLayout()
            words.setSpacing(2)
            words.addWidget(self.title)
            if pill:
                p = label(pill, "muted", wrap=True)
                p.setMinimumWidth(1)  # wraps inside the card (long lines, long translations)
                p.setAttribute(Qt.WA_TransparentForMouseEvents)
                words.addWidget(p)
            self.stat = label("", "cardStat")  # (a small figure, like how many files it converted)
            self.stat.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.stat.hide()
            words.addWidget(self.stat)
            words.addStretch()
            lay.addLayout(words, 1)
        else:
            top = QHBoxLayout()
            top.setSpacing(8)
            top.addWidget(tile)
            top.addStretch()
            self.arrow = QLabel()  # (shows when the mouse is over the card)
            self.arrow.setPixmap(icons.pixmap("arrow_left" if i18n.is_rtl() else "arrow_right", 18,
                                              icons_color(dark), dpr))
            self.arrow.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.arrow.hide()
            top.addWidget(self.arrow, 0, Qt.AlignTop)
            lay.addLayout(top)
            lay.addSpacing(4)
            lay.addWidget(self.title)
            line = label(text, "muted", wrap=True)
            line.setMinimumWidth(1)  # wraps inside the card instead of pushing past its edge (long Arabic lines)
            line.setContentsMargins(0, 0, 2, 0)
            line.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(line)
            self.stat = label(pill, "cardStat")
            self.stat.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(self.stat)
            lay.addStretch()

    def enterEvent(self, e):
        if hasattr(self, "arrow"):
            self.arrow.show()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if hasattr(self, "arrow"):
            self.arrow.hide()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.on_click()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.on_click()
        else:
            super().keyPressEvent(e)


class _BackButton(QObject):
    """The mouse's back button anywhere in the window goes back a page, like in a browser."""

    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self.w = window

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.BackButton and \
                obj.isWidgetType() and obj.window() is self.w:
            self.w.go_back()
            return True
        return False


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


def _alive(widget) -> bool:
    try:
        from shiboken6 import isValid
        return isValid(widget)
    except ImportError:
        return True


def icons_color(dark: bool) -> QColor:
    from .atomic import tokens
    return tokens.current().muted


def _greeting() -> str:
    from datetime import datetime
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return tr("Good morning")
    if 12 <= hour < 18:
        return tr("Good afternoon")
    return tr("Good evening")


def _html(text: str) -> str:
    import html
    return html.escape(text, quote=False)


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
                f"QPushButton {{ background: {color}; border: 3px solid {t.panel.name()}; border-radius: 15px;"
                f" padding: 0; min-height: 0; {text} }}"
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
        self.sidebar = sidebar
        # the menu button (icons only, or names too), the logo and the name
        self.menu_btn = QPushButton()
        self.menu_btn.setObjectName("navToggle")
        self.menu_btn.setCursor(Qt.PointingHandCursor)
        self.menu_btn.setFixedSize(38, 36)
        self.menu_btn.setIconSize(QSize(18, 18))
        self.menu_btn.clicked.connect(self.toggle_nav)
        self.logo = QLabel()
        self.logo.setPixmap(icon.pixmap(28, 28))
        self.brand = label(APP_NAME, "brand")
        brand = QHBoxLayout()
        brand.setSpacing(8)
        brand.setContentsMargins(0, 0, 0, 0)
        brand.addWidget(self.menu_btn)
        brand.addWidget(self.logo)
        brand.addWidget(self.brand)
        brand.addStretch()
        # the tools this person uses (the Store adds and removes them; nothing of the others is built),
        # in the order they dragged them into
        self.tools_on = all_tools.enabled(self.c.settings)
        on_pages = {tool.page for tool in self.tools_on}
        self.nav_entries = NAV[:3] + [tool.page for tool in self.tools_on] + NAV[3:]
        self.settings_tab_list = BASE_SETTINGS_TABS + [(tool.settings, tool.nav, list(tool.settings_pages))
                                                       for tool in self.tools_on if tool.settings]
        from .navbar import BADGE_ROLE, EdgeHandle, NavList
        self.nav = NavList(lambda: self.c.theme, self.c.settings.sidebar_icons, self.c.settings.animations)
        self.nav_rows: list[str | None] = []   # sidebar row -> page name (None for a heading)
        self.nav.ensurePolished()
        row_h = max(38, self.nav.fontMetrics().height() + 20)  # rows a finger could hit, and every page fits
        for entry in self.nav_entries:
            item = QListWidgetItem()
            if isinstance(entry, tuple):
                item.setFlags(Qt.NoItemFlags)
                item.setSizeHint(QSize(0, row_h - 6))
                self.nav_rows.append(None)
            else:
                item.setSizeHint(QSize(0, row_h))
                item.setData(Qt.UserRole, entry)
                self.nav_rows.append(entry)
            self.nav.addItem(item)
        first_tool = 3
        self.nav.movable = (first_tool, first_tool + len(self.tools_on) - 1)
        addable = len([tool for tool in all_tools.ready() if tool.page not in on_pages])
        if addable:  # tools you can add, in a small pill on the Store's row
            self.nav.item(self.nav_rows.index("Store")).setData(BADGE_ROLE, addable)
        self.nav.setIconSize(icons.ICON_SIZE)
        self.nav.moved.connect(self._nav_moved)
        self.nav.menu.connect(self._nav_menu)
        self.refresh_nav()
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(10, 14, 8, 14)
        side.setSpacing(14)
        side.addLayout(brand)
        side.addWidget(self.nav, 1)
        self.version_label = label(tr("Version {version}", version=__version__), "version")
        self.version_label.setContentsMargins(8, 0, 8, 0)
        side.addWidget(self.version_label)
        self.edge = EdgeHandle(sidebar)
        self.edge.dragged.connect(self._nav_dragged)
        self.edge.released.connect(self._nav_released)
        self.edge.toggled.connect(self.toggle_nav)
        self._nav_anim = None
        self._narrow = False
        self._auto_compact = False   # icons only because the window is narrow (not the person's choice)
        self._apply_nav_width(COMPACT_NAV if self.c.settings.nav_compact else self.c.settings.nav_width)

        self.stack = QStackedWidget()
        self.stack.currentChanged.connect(self._fade_in)
        builders = {"Home": self._home, "Converter": self._converter, "History": self._history, "Apps": self._apps,
                    "Archives": self._archives, "Store": self._store, "General": self._general,
                    "Look": self._look, "ConvOptions": self._conv_options, "WheelLook": self._wheel_look,
                    "Formats": self._formats, "AppsSettings": self._apps_settings,
                    "ArchiveSettings": self._archive_settings, "Viewer": self._viewer,
                    "ViewerSettings": self._viewer_settings, "Clipboard": self._clipboard,
                    "ClipboardSettings": self._clipboard_settings, "Space": self._space, "Capture": self._capture,
                    "CaptureSettings": self._capture_settings, "Cloud": self._cloud, "Player": self._player,
                    "PlayerSettings": self._player_settings, "Updates": self._updates, "About": self._about}
        self._wheel_guard = None
        self._settings_head()  # (made before the pages: opening one shows or hides it)
        self._dots: list[QLabel] = []
        self._gears: list[QPushButton] = []  # small "+" marks in the accent colour (recoloured with the theme)
        self._builders = builders
        # pages that are rarely opened are only built the first time they are (a faster, lighter window)
        # (the Converter's page and every Settings page are always made: other parts of the window use them)
        self.clip_page = None
        self.space_page = None
        self.cloud_page = None
        self._unbuilt = {"Apps", "Store", "Viewer", "Clipboard", "Space", "Capture", "Cloud", "Player", *SOON, *[tool.page for tool in all_tools.TOOLS
                                                    if tool.page not in on_pages and tool.page != "Converter"]}
        for name in PAGES:
            built = QWidget() if name in self._unbuilt else builders[name]()
            if name in SETTINGS_PAGES:  # the "Settings" heading and the tabs above say what this is
                built.widget().layout().itemAt(0).widget().hide()
            self.stack.addWidget(built)
        self._opening = None  # a page without a sidebar row of its own, while it's being opened
        self.nav.currentRowChanged.connect(self._page_changed)
        self.nav.itemClicked.connect(self._row_clicked)
        last = self.c.settings.last_page
        self.open_page(last if last in PAGES else "Home")

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.saved_hint = label(tr("Changes to settings are kept when you press Save."), "subtle")
        self.unsaved = label(tr("You have unsaved changes"), "unsaved")
        footer.addWidget(self.saved_hint)
        footer.addWidget(self.unsaved)
        footer.addStretch()
        self.discard_btn = button(tr("Discard"), None, self.discard_changes)
        self.save_btn = button(tr("Save"), "primary", self.save_changes)
        self.done_btn = button(tr("Done"), "primary", self.close)
        for b in (self.discard_btn, self.save_btn, self.done_btn):
            footer.addWidget(b)
        # the pages sit on a big rounded sheet (the canvas) in front of the window's backdrop
        self.canvas = QFrame()
        self.canvas.setObjectName("canvas")
        cl = QVBoxLayout(self.canvas)
        cl.setContentsMargins(28, 12, 16, 16)
        cl.setSpacing(12)
        cl.addWidget(self._top_bar())
        body = QHBoxLayout()
        body.setSpacing(24)
        body.addWidget(self.settings_side)
        column = QVBoxLayout()
        column.setSpacing(12)
        column.addWidget(self.settings_head)
        column.addWidget(self.stack, 1)
        body.addLayout(column, 1)
        cl.addLayout(body, 1)
        cl.addLayout(footer)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 8)
        root.setSpacing(0)
        root.addWidget(sidebar)
        root.addWidget(self.edge)
        root.addWidget(self.canvas, 1)
        root.addSpacing(8)

        self.refresh()
        self.refresh_updates()
        self.refresh_history()
        self._no_accidental_scrolling()
        self.update_save_bar()
        self._went: list[str] = []  # pages opened before this one (Back, Alt + Left, the mouse's back button)
        self._going_back = False
        QShortcut(QKeySequence(QKeySequence.StandardKey.Back), self, self.go_back)
        QShortcut(QKeySequence("Ctrl+K"), self, self.open_palette)
        self._back_button = _BackButton(self)  # (only listens while the window is showing)

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
        on_settings = self.current_page() in SETTINGS_PAGES
        self.saved_hint.setVisible(not dirty and on_settings)
        self.done_btn.setVisible(on_settings or dirty)

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
        mark_rows(root or self)  # (hairlines between settings rows that follow each other)

    def refresh_nav(self):
        t = self.c.theme
        self.nav.show_icons = self.c.settings.sidebar_icons
        compact = self._compact_now()
        for row, entry in enumerate(self.nav_entries):  # (headings never move)
            if isinstance(entry, tuple):
                self.nav.item(row).setText(tr(entry[1]))
        for row in range(self.nav.count()):  # (tools can have been dragged: go by each row's own page)
            item = self.nav.item(row)
            name = item.data(Qt.UserRole)
            if name:
                text = tr(NAV_LABELS.get(name, name))
                item.setText(text)
                item.setData(ICON_ROLE, PAGE_ICONS[name])
                item.setToolTip(text if compact else "")
        self.menu_btn.setIcon(icons.icon("navigation", t.text))
        self.menu_btn.setToolTip(tr("Show the names") if compact else tr("Icons only"))
        self.nav.viewport().update()

    # ---- the sidebar: narrow or wide, and the tools in your order

    def _apply_nav_width(self, width: int):
        self.sidebar.setFixedWidth(width)
        compact = width < COMPACT_NAV + 40
        for w in (self.logo, self.brand, self.version_label):
            w.setVisible(not compact)

    def _fade_in(self, i: int):
        """A page that's opened fades in (quickly), so moving between pages feels smooth."""
        w = self.stack.widget(i)
        if w is None or not (self.c.settings.animations and self.isVisible()) or PAGES[i] == "Player":
            return  # (the Player's video draws itself: it's left alone)
        from PySide6.QtCore import QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        effect = QGraphicsOpacityEffect(w)
        effect.setOpacity(0.0)
        w.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", w)
        anim.setDuration(150)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.finished.connect(lambda: _alive(w) and w.setGraphicsEffect(None))
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def _compact_now(self) -> bool:
        return self.c.settings.nav_compact or getattr(self, "_auto_compact", False)

    def toggle_nav(self):
        self.set_nav_compact(not self._compact_now())

    def set_nav_compact(self, compact: bool, remember: bool = True):
        """Icons only (narrow) or icons and names, sliding between the two."""
        if remember:
            self.c.settings.nav_compact = compact
            self._auto_compact = False
            self.c.settings_changed()
        compact = self._compact_now()
        target = COMPACT_NAV if compact else self.c.settings.nav_width
        for row in range(self.nav.count()):
            item = self.nav.item(row)
            if item.data(Qt.UserRole):
                item.setToolTip(item.text() if compact else "")
        self.menu_btn.setToolTip(tr("Show the names") if compact else tr("Icons only"))
        if self._nav_anim is not None:
            self._nav_anim.stop()
        if not (self.c.settings.animations and self.isVisible()):
            self._apply_nav_width(target)
            return
        from PySide6.QtCore import QEasingCurve, QVariantAnimation
        anim = QVariantAnimation(self)
        anim.setDuration(180)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(self.sidebar.width())
        anim.setEndValue(target)
        anim.valueChanged.connect(lambda v: self._apply_nav_width(int(v)))
        self._nav_anim = anim
        anim.start()

    def _nav_dragged(self, width: int):
        """The sidebar's edge being dragged: wider, narrower, or (far enough) icons only."""
        s = self.c.settings
        if width < COMPACT_NAV + 50:
            if not self._compact_now():
                self.set_nav_compact(True)
            return
        if self._compact_now():
            s.nav_width = max(190, min(340, width))
            self.set_nav_compact(False)
            return
        s.nav_width = max(190, min(340, width))
        if self._nav_anim is not None:
            self._nav_anim.stop()
        self._apply_nav_width(s.nav_width)

    def _nav_released(self):
        self.c.settings_changed()

    def _nav_moved(self, pages: list):
        """Tools dragged into a new order: remembered, and the Home page and Settings follow it."""
        by_page = {tool.page: tool for tool in all_tools.TOOLS}
        self.c.settings.nav_order = [by_page[p].id for p in pages if p in by_page]
        self.c.settings_changed()
        self.nav_rows = [self.nav.item(r).data(Qt.UserRole) for r in range(self.nav.count())]
        self.tools_on = all_tools.enabled(self.c.settings)
        self._rebuild_home()

    def _rebuild_home(self):
        i = PAGES.index("Home")
        old = self.stack.widget(i)
        current = self.stack.currentIndex()
        new = self._home()
        self.stack.insertWidget(i, new)
        self.stack.removeWidget(old)
        old.deleteLater()
        self.stack.setCurrentIndex(current)
        self.refresh()
        self.refresh_history()
        self.apply_theme(self.c.theme)

    def _nav_menu(self, row: int, pos):
        """Right-click on the sidebar: move a tool, put them back in order, or narrow the sidebar."""
        from PySide6.QtWidgets import QMenu
        from .theme import round_menu
        menu = QMenu(self)
        round_menu(menu)
        if self.nav.can_drag(row):
            first, last = self.nav.movable
            up = menu.addAction(tr("Move up"), lambda: self.nav.move_by(row, -1))
            up.setEnabled(row > first)
            down = menu.addAction(tr("Move down"), lambda: self.nav.move_by(row, 1))
            down.setEnabled(row < last)
            menu.addAction(tr("Put the tools back in order"), self._reset_nav_order)
            menu.addSeparator()
            page = self.nav.item(row).data(Qt.UserRole)
            tool = all_tools.BY_PAGE.get(page)
            if tool is not None:
                menu.addAction(tr("Remove from Corefold…"), lambda: self._remove_tool(tool))
                menu.addSeparator()
        menu.addAction(tr("Show the names") if self._compact_now() else tr("Icons only"), self.toggle_nav)
        menu.exec(pos)

    def _reset_nav_order(self):
        self.c.settings.nav_order = []
        self.c.settings_changed()
        self.c.rebuild_window(self.current_page(), committed=self.committed, pending_startup=self.pending_startup)

    def resizeEvent(self, e):
        """A narrow window shows the sidebar's icons only (and names again when it's wide enough)."""
        super().resizeEvent(e)
        if not hasattr(self, "sidebar"):
            return
        narrow = self.width() < 860
        if narrow != self._narrow:
            self._narrow = narrow
            self._auto_compact = narrow
            self.set_nav_compact(narrow, remember=False)

    def current_page(self) -> str:
        i = self.stack.currentIndex()
        return PAGES[i] if 0 <= i < len(PAGES) else "Home"

    def _apps(self) -> QWidget:
        from .appspage import AppsPage
        self.apps_page = AppsPage(self.c, self)
        return self.apps_page

    def _build_page(self, name: str):
        """Builds a page that was left for later, in its place."""
        if name not in self._unbuilt:
            return
        self._unbuilt.discard(name)
        i = PAGES.index(name)
        stand_in = self.stack.widget(i)
        # (a tool that isn't ready only shows what it will do, even if parts of it are already written)
        built = self._builders[name]() if name in self._builders and name not in SOON else self._soon(name)
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
        went = getattr(self, "_went", None)
        before = self.current_page()
        if went is not None and not self._going_back and before != name:
            went.append(before)
            del went[:-30]
        self._build_page(name)
        self.stack.setCurrentIndex(PAGES.index(name))
        self._sync_settings_head(name)
        if os.environ.get("COREFOLD_MEMLOG"):
            winutil.log_memory(f"page {name}")
            QTimer.singleShot(2500, lambda n=name: winutil.log_memory(f"page {n}, 2.5 s later"))
        if name == "Apps":
            self.apps_page.ensure_loaded()
        elif name in ("History", "Home"):
            self.refresh_history()
        elif name == "About":
            self.refresh_memory()
        elif name == "Archives":
            self.refresh_archives()
        elif name == "Viewer":
            self.refresh_views()
        elif name == "Clipboard" and self.clip_page is not None:
            self.clip_page.refresh()
        elif name == "Space" and self.space_page is not None:
            self.space_page.refresh()
        elif name == "Capture":
            self.refresh_captures()
        elif name == "Player":
            self.refresh_watching()
        elif name == "CaptureSettings":
            self._find_microphones()

    def _archives(self) -> QWidget:
        """Corefold Archive: open archives, make new ones, and the ones opened lately."""
        dark, dpr = self.c.theme.dark, self.devicePixelRatioF()
        w, lay = page(tr("Corefold Archive"))
        lay.itemAt(0).widget().hide()
        lay.addLayout(tool_header("archive", tr("Corefold Archive"),
                                  tr("Open, extract and make archives: ZIP, RAR, 7z, TAR, ISO and more."), dark, dpr,
                                  self.tool_settings_button("Archives")))
        self.archive_drop = DropZone(None, None, title=tr("Drop an archive to open it"),
                                     sub=tr("Or drop files and folders to pack them into a ZIP or 7z."),
                                     hot_title=tr("Let go"),
                                     buttons=[(tr("Open an archive…"), self._open_archive, "primary"),
                                              (tr("Make an archive…"), self._make_archive)])
        self.archive_drop.dropped.connect(self._archive_dropped)
        lay.addWidget(self.archive_drop)

        box, bl = card(8)
        head = QHBoxLayout()
        head.addWidget(label(tr("Recent archives"), "h2"))
        head.addStretch()
        self.archive_clear = button(tr("Clear"), "flat", self._clear_archives)
        head.addWidget(self.archive_clear)
        bl.addLayout(head)
        self.archive_recent_lay = QVBoxLayout()
        self.archive_recent_lay.setSpacing(2)
        bl.addLayout(self.archive_recent_lay)
        lay.addWidget(box)

        box, bl = card(8)
        bl.addWidget(label(tr("Good to know"), "h2"))
        for text in (tr("Drag files out of an open archive straight into any folder."),
                     tr("Open a file inside a ZIP or 7z, change it, and save it back into the archive."),
                     tr("Archives inside archives can be unpacked in one go."),
                     tr("A damaged archive? Corefold rescues every file that can still be read."),
                     tr("Files from the internet keep Windows' warning after you unpack them.")):
            row = QHBoxLayout()
            row.setSpacing(10)
            dot = self._accent_dot()
            row.addWidget(dot, 0, Qt.AlignTop)
            row.addWidget(label(text, wrap=True), 1)
            bl.addLayout(row)
        lay.addWidget(box)

        lay.addStretch()
        return w

    def refresh_archives(self):
        """The archives opened lately (ones that were moved or deleted are left out)."""
        if "Archives" in self._unbuilt:
            return
        lay = self.archive_recent_lay
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        shown = 0
        for name in self.c.settings.recent_archives:
            path = Path(name)
            try:
                there = path.is_file()
            except OSError:
                there = False
            if not there:
                continue
            row = QPushButton(f"{short_name(path.name, 60)}   ·   {short_name(str(path.parent), 60)}")
            row.setObjectName("flat")
            row.setToolTip(str(path))
            row.setCursor(Qt.PointingHandCursor)
            row.setIcon(icons.icon("archive", self.c.theme.muted))
            row.setStyleSheet("text-align: left;" if not i18n.is_rtl() else "text-align: right;")
            row.clicked.connect(lambda _=False, p=path: self.c.open_browser(p))
            lay.addWidget(row)
            shown += 1
        if not shown:
            lay.addWidget(label(tr("Archives you open show up here.") if self.c.settings.remember_archives else
                                tr("Turned off in Settings."), "muted", wrap=True))
        self.archive_clear.setVisible(bool(shown))

    def _clear_archives(self):
        self.c.forget_archives()
        self.refresh_archives()

    def _open_archive(self):
        self.c.choose_archive(self)

    def _make_archive(self):
        files = choose_files(self)
        if files:
            self._pack(files)

    def _pack(self, files: list[Path]):
        tool = next((t for tools in catalog(len(files) > 1).values() for t in tools if t.key == "pack:options"), None)
        if tool is not None:
            self.c.tool_chosen(tool, files)

    def _archive_dropped(self, paths: list[Path]):
        from .archives import is_archive_file
        archives = [p for p in paths if is_archive_file(p)]
        if archives and len(archives) == len(paths):
            for p in archives[:5]:
                self.c.open_browser(p)
        else:
            self._pack(paths)

    def _default_for_archives(self):
        from . import assoc
        if not self.open_archives_setting.isChecked():
            self.open_archives_setting.setChecked(True)  # registers first, so Windows lists Corefold
        assoc.open_default_apps()

    def _settings_head(self) -> QWidget:
        """Settings: a list on the side (the app, then each tool you use), and above the page its name
        and, for a tool with several pages, small pill tabs."""
        from .navbar import NavList
        side = QWidget()
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 2, 0, 0)
        sv.setSpacing(10)
        sv.addWidget(label(tr("Settings"), "h1"))
        self.settings_list = NavList(lambda: self.c.theme, True, self.c.settings.animations)
        self.settings_list.setObjectName("settingsNav")
        self.settings_list.on_canvas = True
        tool_of = {tool.settings: tool.page for tool in self.tools_on if tool.settings}
        icon_of = {"General": "settings", "Look": "paint_brush"}
        self.settings_tabs: dict[str, int] = {}  # the tab's first page -> its row
        self.settings_list.ensurePolished()
        row_h = max(36, self.settings_list.fontMetrics().height() + 18)
        for first, text, _pages in self.settings_tab_list:
            item = QListWidgetItem(tr(text))
            item.setSizeHint(QSize(0, row_h))
            item.setData(Qt.UserRole, first)
            item.setData(ICON_ROLE, icon_of.get(first) or PAGE_ICONS.get(tool_of.get(first, ""), "settings"))
            self.settings_tabs[first] = self.settings_list.count()
            self.settings_list.addItem(item)
        self.settings_list.setFixedHeight(row_h * len(self.settings_tab_list) + 6)
        self.settings_list.itemClicked.connect(lambda item: self.open_page(item.data(Qt.UserRole)))
        self.settings_list.currentRowChanged.connect(self._settings_row_changed)
        sv.addWidget(self.settings_list)
        sv.addStretch()
        side.setFixedWidth(212)
        side.hide()
        self.settings_side = side

        head = QWidget()
        v = QVBoxLayout(head)
        v.setContentsMargins(0, 2, 10, 0)
        v.setSpacing(8)
        self.settings_title = label("", "h1")
        v.addWidget(self.settings_title)
        self.settings_sub = QWidget()
        sub = QHBoxLayout(self.settings_sub)
        sub.setContentsMargins(0, 0, 0, 2)
        sub.setSpacing(4)
        self.settings_subtabs: dict[str, QPushButton] = {}
        for page_name, text in SETTINGS_SUBTABS.items():
            b = button(tr(text), "subtab", lambda _=False, n=page_name: self.open_page(n))
            b.setCheckable(True)
            sub.addWidget(b)
            self.settings_subtabs[page_name] = b
        sub.addStretch()
        v.addWidget(self.settings_sub)
        head.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.settings_head = head
        head.hide()
        return head

    def _settings_row_changed(self, row: int):
        item = self.settings_list.item(row)
        if item is not None and not self._syncing_settings:
            self.open_page(item.data(Qt.UserRole))

    _syncing_settings = False

    def _sync_settings_head(self, name: str):
        on = name in SETTINGS_PAGES
        self.settings_head.setVisible(on)
        self.settings_side.setVisible(on)
        if hasattr(self, "saved_hint"):  # (the hint about Save only means something on the settings)
            self.saved_hint.setVisible(on and self.unsaved.isHidden())
            self.done_btn.setVisible(on or not self.unsaved.isHidden())
        self._sync_top_bar(name)
        if not on:
            return
        for first, text, pages in self.settings_tab_list:
            if name in pages:
                self.settings_title.setText(tr(text))
                self._syncing_settings = True
                try:
                    self.settings_list.setCurrentRow(self.settings_tabs[first])
                finally:
                    self._syncing_settings = False
        self.settings_sub.setVisible(name in self.settings_subtabs)
        for page_name, b in self.settings_subtabs.items():
            b.setChecked(page_name == name)

    # ---- the strip at the top: Back, where you are, search, light or dark

    def _top_bar(self) -> QWidget:
        from .atomic.widgets import IconButton, SearchButton
        bar = QWidget()
        bar.setObjectName("topBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.back_btn = IconButton(tr("Back (Alt + Left)"))
        self.back_btn.clicked.connect(self.go_back)
        row.addWidget(self.back_btn)
        self.crumb = label("", "muted")
        row.addWidget(self.crumb)
        row.addStretch()
        self.search_btn = SearchButton(tr("Search Corefold"))
        self.search_btn.setMinimumWidth(240)
        self.search_btn.setToolTip(tr("Find any page, setting or action (Ctrl + K)"))
        self.search_btn.clicked.connect(self.open_palette)
        row.addWidget(self.search_btn)
        self.top_actions = QHBoxLayout()  # (room for more buttons, like the Quick menu's)
        self.top_actions.setSpacing(4)
        row.addLayout(self.top_actions)
        self.theme_btn = IconButton()
        self.theme_btn.clicked.connect(self.toggle_dark)
        row.addWidget(self.theme_btn)
        self.palette = None
        return bar

    def _sync_top_bar(self, name: str):
        if not hasattr(self, "crumb"):
            return
        if name in SETTINGS_PAGES:
            where = tr("Settings")
        elif name in all_tools.BY_PAGE and name not in SOON:
            where = tr("Tools")
        elif name in SOON:
            where = tr("Store")
        else:
            where = ""
        here = tr(NAV_LABELS.get(name, name)) if name not in SETTINGS_PAGES else ""
        sep = " ‹ " if i18n.is_rtl() else " › "
        self.crumb.setText(sep.join(x for x in (where, here) if x) if where else "")
        self.back_btn.setEnabled(bool(getattr(self, "_went", None)))

    def toggle_dark(self):
        """The sun/moon button: light or dark right away (and kept, without pressing Save)."""
        s = self.c.settings
        unsaved = self.is_dirty()
        s.appearance = "light" if self.c.theme.dark else "dark"
        seg = getattr(self, "appearance_seg", None)
        if seg is not None:
            for i, k in enumerate(seg.keys):
                seg.group.button(i).setChecked(k == s.appearance)
        self.c.settings_changed()
        self.c.apply_theme()
        if not unsaved:  # nothing else waiting for Save: this is kept straight away
            self.save_changes()
        self.update_save_bar()

    # ---- the command palette (Ctrl + K): pages, settings and actions, found by typing

    def open_palette(self):
        from .atomic.palette import CommandPalette
        if self.palette is None:
            self.palette = CommandPalette(self, tr("Search pages, settings and actions"),
                                          tr("Nothing matches. Try another word."), heading=lambda where: where)
        self.palette.open(self._commands())

    def _commands(self) -> list:
        from .atomic.palette import Command
        t = self.c.theme
        commands: list[Command] = []
        go, actions, in_settings = tr("Go to"), tr("Actions"), tr("Settings")

        def ic(name):
            return icons.icon(name, t.muted)

        # things to do
        if "Converter" in self.nav_rows:
            commands.append(Command(tr("Convert files…"), actions, ic("arrow_swap"), self._choose_files,
                                    keywords="convert pick choose open files"))
        if self.c.capture is not None:
            commands.append(Command(tr("Take a screenshot"), actions, ic("screenshot"),
                                    lambda: QTimer.singleShot(250, self.c.capture.screenshot), keywords="capture"))
        if self.c.clip is not None:
            commands.append(Command(tr("Show the clipboard history"), actions, ic("clipboard"),
                                    self.c.show_clipboard, hint="Win Alt V", keywords="clipboard paste"))
        commands.append(Command(tr("Switch to light mode") if t.dark else tr("Switch to dark mode"), actions,
                                ic("weather_sunny" if t.dark else "weather_moon"), self.toggle_dark,
                                keywords="theme dark light appearance"))
        if self.c.updates_supported():
            commands.append(Command(tr("Check for updates"), actions, ic("arrow_sync"),
                                    lambda: (self.open_page("Updates"), self.c.check_updates(manual=True)),
                                    keywords="update version"))
        commands.append(Command(tr("Show the tour again"), actions, ic("sparkle"), self.c.show_tour,
                                keywords="help start tips"))
        # pages
        for name in self.nav_rows:
            if name:
                commands.append(Command(tr(NAV_LABELS.get(name, name)), go, ic(PAGE_ICONS.get(name, "apps")),
                                        lambda n=name: self.open_page(n)))
        for tool in all_tools.coming():
            commands.append(Command(tr(tool.name), tr("Coming soon"), ic(tool.icon),
                                    lambda n=tool.page: self.open_page(n), keywords=tr(tool.line)))
        # settings: each tab and page, then each setting on them, found by its words
        tab_of = {}
        for first, text, pages in self.settings_tab_list:
            for page_name in pages:
                tab_of[page_name] = tr(text)
            commands.append(Command(tr(text), in_settings, ic("settings"), lambda f=first: self.open_page(f)))
        sep = " ‹ " if i18n.is_rtl() else " › "
        seen = set()
        setting_icon = ic("text_description")
        for page_name, tab in tab_of.items():
            i = PAGES.index(page_name)
            w = self.stack.widget(i)
            if w is None or page_name in self._unbuilt:
                continue
            where = in_settings + sep + tab
            if page_name in SETTINGS_SUBTABS:
                where += sep + tr(SETTINGS_SUBTABS[page_name])
            for widget in w.findChildren(QWidget):
                text = ""
                if isinstance(widget, QCheckBox):
                    text = widget.text()
                elif isinstance(widget, QLabel) and widget.objectName() in ("", "h2") and not widget.pixmap():
                    text = widget.text()
                text = text.replace("&", "").strip().rstrip(":")
                if not text or len(text) > 90 or "<" in text or (page_name, text) in seen:
                    continue
                seen.add((page_name, text))
                commands.append(Command(text, where, setting_icon,
                                        lambda n=page_name, wd=widget: self._show_setting(n, wd), listed=False))
        return commands

    def _show_setting(self, page_name: str, widget: QWidget):
        """Opens the page with this setting, scrolls to it and makes it glow for a moment."""
        self.open_page(page_name)

        def reveal():
            if not _alive(widget):
                return
            area = widget.parent()
            while area is not None and not isinstance(area, QScrollArea):
                area = area.parent()
            if area is not None:
                area.ensureWidgetVisible(widget, 40, 80)
            from .atomic.widgets import flash
            flash(widget)
            if widget.focusPolicy() != Qt.NoFocus:
                widget.setFocus(Qt.ShortcutFocusReason)
        QTimer.singleShot(60, reveal)

    def open_tool_settings(self, tool_page: str):
        self.open_page(TOOL_SETTINGS.get(tool_page, "General"))

    def tool_settings_button(self, tool_page: str, *more: QWidget) -> QWidget:
        """The ⚙ button at the top of a tool's page (next to anything else that goes there)."""
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for w in more:
            row.addWidget(w)
        gear = button(tr("Settings"), None, lambda: self.open_tool_settings(tool_page))
        gear.setIcon(icons.icon("settings", self.c.theme.text))
        gear.setToolTip(tr("Settings for this tool"))
        self._gears.append(gear)
        row.addWidget(gear)
        return box

    def _accent_dot(self) -> QLabel:
        dot = QLabel()
        dot.setPixmap(icons.pixmap("add", 16, self.c.theme.accent, self.devicePixelRatioF()))
        dot.setFixedWidth(18)
        self._dots.append(dot)
        return dot

    def _store(self) -> QWidget:
        """The Store: the tools you use, the ones you can add, and the ones on the way."""
        dark, dpr = self.c.theme.dark, self.devicePixelRatioF()
        w, lay = page(tr("Store"), tr("Add the tools you use and remove the ones you don't. Everything is free, and "
                                      "a tool you remove isn't loaded at all, so Corefold stays light."))
        on = {tool.id for tool in self.tools_on}
        fresh = {tool.id for tool in all_tools.new_tools(self.c.settings)}
        mine = [tool for tool in all_tools.ready() if tool.id in on]
        more = [tool for tool in all_tools.ready() if tool.id not in on]

        from .atomic.widgets import ResponsiveGrid

        def section(title: str, tools: list, kind: str):
            if not tools:
                return
            head = QHBoxLayout()
            head.setSpacing(8)
            head.addWidget(label(title, "title"))
            count = label(str(len(tools)), "chip")
            head.addWidget(count, 0, Qt.AlignVCenter)
            head.addStretch()
            lay.addLayout(head)
            grid = ResponsiveGrid(300, 12, 3)
            for tool in tools:
                grid.add(self._store_card(tool, kind, tool.id in fresh, dark, dpr))
            lay.addWidget(grid)

        section(tr("Your tools"), mine, "remove")
        if not mine:
            lay.addWidget(label(tr("You haven't added any tools yet. Pick the ones you want below."), "muted",
                                wrap=True))
        section(tr("Add more"), more, "add")
        coming = all_tools.coming()
        if coming:  # many ideas: a tidy grid, the first row shown until "Show all" is pressed
            head = QHBoxLayout()
            head.setSpacing(8)
            head.addWidget(label(tr("Coming soon"), "title"))
            count = label(str(len(coming)), "chip")
            head.addWidget(count, 0, Qt.AlignVCenter)
            head.addStretch()
            lay.addLayout(head)
            lay.addWidget(label(tr("Tools we're making or thinking about. Each one comes in a free update when it's ready."), "muted",
                                wrap=True))
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(10)
            cols, first = 3, 6
            hidden: list[QWidget] = []
            for i, tool in enumerate(coming):
                tile = ToolCard(tool.product, tr(tool.nav), "", lambda n=tool.page: self.open_page(n), dark, dpr,
                                small=True, pill=tr(tool.line))
                grid.addWidget(tile, i // cols, i % cols)
                if i >= first:
                    tile.hide()
                    hidden.append(tile)
            for col in range(cols):
                grid.setColumnStretch(col, 1)
            lay.addLayout(grid)
            if hidden:
                more_btn = button(tr("Show all {count}", count=len(coming)), "flat")

                def toggle():
                    show = hidden[0].isHidden()
                    for tile in hidden:
                        tile.setVisible(show)
                    more_btn.setText(tr("Show less") if show else tr("Show all {count}", count=len(coming)))
                more_btn.clicked.connect(toggle)
                row = QHBoxLayout()
                row.addStretch()
                row.addWidget(more_btn)
                row.addStretch()
                lay.addLayout(row)
        lay.addStretch()
        return w

    def _store_card(self, tool, kind: str, fresh: bool, dark: bool, dpr: float) -> QFrame:
        box = QFrame()
        box.setObjectName("toolCard")
        v = QVBoxLayout(box)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(12)
        tile = QLabel()
        tile.setPixmap(icons.product_tile(tool.product, 46, dark, dpr))
        tile.setFixedSize(48, 48)
        top.addWidget(tile, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(2)
        name = QHBoxLayout()
        name.setSpacing(8)
        name.addWidget(label(tr(tool.name), "cardTitle"))
        if fresh or kind == "soon":
            pill = label(tr("New") if fresh else tr("Soon"), "newPill" if fresh else "soonPill")
            pill.setFixedHeight(18)
            name.addWidget(pill, 0, Qt.AlignVCenter)
        name.addStretch()
        words.addLayout(name)
        state = label(tr("In use") if kind == "remove" else tr("Free"), "subtle")
        words.addWidget(state)
        top.addLayout(words, 1)
        v.addLayout(top)
        line = label(tr(tool.line), "muted", wrap=True)
        line.setMinimumWidth(1)
        v.addWidget(line)
        v.addStretch()
        row = QHBoxLayout()
        row.setSpacing(8)
        if kind == "remove":
            row.addWidget(button(tr("Open"), None, lambda: self.open_page(tool.page)))
            row.addStretch()
            row.addWidget(button(tr("Remove"), "danger", lambda: self._remove_tool(tool)))
        elif kind == "add":
            row.addWidget(button(tr("Add"), "primary", lambda: self.c.set_tool(tool.id, True)))
            row.addStretch()
        else:
            row.addWidget(button(tr("See what it will do"), None, lambda: self.open_page(tool.page)))
            row.addStretch()
        v.addLayout(row)
        return box

    def _remove_tool(self, tool):
        text = tr("{name} leaves the sidebar and stops running. Its settings are kept, so you can add it back "
                  "any time from the Store.", name=tr(tool.name))
        if tool.id == "converter":
            text += "\n\n" + tr("The wheel and Corefold's right-click menu go away with it.")
        elif tool.id == "archive":
            text += "\n\n" + tr("Archives won't open in Corefold with a double-click any more.")
        box = QMessageBox(QMessageBox.Question, tr("Remove {name}?", name=tr(tool.name)), text, parent=self)
        remove = box.addButton(tr("Remove"), QMessageBox.DestructiveRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        chosen = box.clickedButton() is remove
        box.deleteLater()
        if chosen:
            self.c.set_tool(tool.id, False)

    def _soon(self, name: str) -> QWidget:
        """A page for a tool that's still being made: what it will do."""
        tool = all_tools.BY_PAGE[name]
        w, lay = page(tr(tool.name))
        lay.itemAt(0).widget().hide()  # the big title is shown next to the tile instead
        lay.addLayout(tool_header(tool.product, tr(tool.name), tr(tool.line), self.c.theme.dark,
                                  self.devicePixelRatioF(), pill=tr("Coming soon")))
        box, bl = card(10)
        bl.addWidget(label(tr("What it will do"), "h2"))
        for text in tool.features:
            row = QHBoxLayout()
            row.setSpacing(10)
            dot = self._accent_dot()
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
        """The start page: a greeting with what Corefold did so far and quick actions, the tools you use,
        more in the Store, and what happened lately."""
        from .atomic.widgets import FlowLayout, GlowPanel, ResponsiveGrid
        dark, dpr = self.c.theme.dark, self.devicePixelRatioF()
        w, lay = page("")
        lay.itemAt(0).widget().hide()
        lay.setSpacing(16)

        hero = GlowPanel()
        hl = QVBoxLayout(hero)
        hl.setContentsMargins(28, 24, 28, 22)
        hl.setSpacing(6)
        self.hello = label(_greeting(), "display")
        hl.addWidget(self.hello)
        hl.addWidget(label(tr("Your everyday tools in one place. Drop files anywhere in this window, or press "
                              "Ctrl + K to find any page, setting or action."), "muted", wrap=True))
        hl.addSpacing(10)
        figures = QHBoxLayout()
        figures.setSpacing(28)
        self.hero_figures: dict[str, QLabel] = {}
        for key, text in (("files", tr("files converted")), ("saved", tr("space saved")),
                          ("tools", tr("tools in use"))):
            box = QVBoxLayout()
            box.setSpacing(0)
            value = label("0", "heroStat")
            box.addWidget(value)
            box.addWidget(label(text, "subtle"))
            figures.addLayout(box)
            self.hero_figures[key] = value
        figures.addStretch()
        hl.addLayout(figures)
        hl.addSpacing(8)
        quick = FlowLayout(spacing=8)
        self.quick_buttons = []
        on = {tool.id for tool in self.tools_on}
        for tool_id, text, name, run in (
                ("converter", tr("Convert files…"), "arrow_swap", self._choose_files),
                ("capture", tr("Take a screenshot"), "screenshot",
                 lambda: self.c.capture and QTimer.singleShot(250, self.c.capture.screenshot)),
                ("clipboard", tr("Clipboard history"), "clipboard", lambda: self.c.show_clipboard()),
                ("space", tr("See what fills your disk"), "data_pie", lambda: self.open_page("Space"))):
            if tool_id in on and len(self.quick_buttons) < 3:
                b = button(text, "quick", run)
                b.setIconSize(QSize(18, 18))
                self.quick_buttons.append((b, name))
                quick.addWidget(b)
        store = button(tr("Add tools"), "quick", lambda: self.open_page("Store"))
        store.setIconSize(QSize(18, 18))
        self.quick_buttons.append((store, "apps"))
        quick.addWidget(store)
        hl.addLayout(quick)
        lay.addWidget(hero)

        head = QHBoxLayout()
        head.addWidget(label(tr("Your tools"), "title"))
        head.addStretch()
        head.addWidget(button(tr("Open the Store"), "flat", lambda: self.open_page("Store")))
        lay.addLayout(head)
        grid = ResponsiveGrid(230, 12, 3)
        self.tool_cards = {}
        mine = self.tools_on
        for tool in mine:
            c = ToolCard(tool.product, tr(tool.nav), tr(tool.line), lambda n=tool.page: self.open_page(n), dark, dpr)
            self.tool_cards[tool.page] = c
            grid.add(c)
        lay.addWidget(grid)
        if not mine:
            lay.addWidget(label(tr("You haven't added any tools yet. Pick the ones you want below."), "muted",
                                wrap=True))

        others = [tool for tool in all_tools.ready() if tool.id not in on]
        coming = len(all_tools.coming())
        if others or coming:  # one line about the Store (the ideas are there, so Home stays short)
            more, ml = card(0)
            ml.setContentsMargins(18, 14, 14, 14)
            row = QHBoxLayout()
            row.setSpacing(12)
            tiles = QHBoxLayout()
            tiles.setSpacing(-8)
            for tool in (others or all_tools.coming())[:4]:
                t = QLabel()
                t.setPixmap(icons.product_tile(tool.product, 30, dark, dpr))
                tiles.addWidget(t)
            row.addLayout(tiles)
            words = []
            if others:
                words.append(tr("{count} more ready to add", count=len(others)))
            if coming:
                words.append(tr("{count} more on the way", count=coming))
            row.addWidget(label(" · ".join(words), "muted"), 1)
            row.addWidget(button(tr("Open the Store"), None, lambda: self.open_page("Store")))
            ml.addLayout(row)
            lay.addWidget(more)

        head = QHBoxLayout()
        head.addWidget(label(tr("Recent activity"), "title"))
        head.addStretch()
        head.addWidget(button(tr("See all"), "flat", lambda: self.open_page("History")))
        lay.addLayout(head)
        recent, self.recent_lay = card(0)
        self.recent_lay.setContentsMargins(6, 6, 6, 6)
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
            empty = label(tr("Nothing yet. What you do with Corefold shows up here."), "muted", wrap=True)
            empty.setContentsMargins(12, 10, 12, 10)
            self.recent_lay.addWidget(empty)
            return
        arrow = "←" if i18n.is_rtl() else "→"
        dark, dpr = self.c.theme.dark, self.devicePixelRatioF()
        for n, e in enumerate(entries):
            n_in = max(e.input_count, len(e.inputs))
            n_out = max(e.output_count, len(e.outputs))
            what = e.inputs[0] if n_in == 1 and e.inputs else tr("{count} files", count=n_in)
            result = Path(e.outputs[0]).name if n_out == 1 and e.outputs else \
                tr("{count} new files", count=n_out) if n_out else tr("no new file")
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(10, 8, 12, 8)
            row.setSpacing(12)
            tile = QLabel()
            tile.setPixmap(icons.product_tile("converter", 28, dark, dpr))
            row.addWidget(tile)
            words = QVBoxLayout()
            words.setSpacing(0)
            words.addWidget(label(e.tool, "cardTitle"))
            text = label(f"{_html(short_name(what, 44))} {arrow} {_html(short_name(result, 44))}", "muted")
            text.setTextFormat(Qt.RichText)
            words.addWidget(text)
            row.addLayout(words, 1)
            row.addWidget(label(time_ago(e.when), "subtle"))
            if n:
                line = QFrame()
                line.setObjectName("line")
                self.recent_lay.addWidget(line)
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
                                     "drag."), self.c.theme.dark, self.devicePixelRatioF(),
                                  self.tool_settings_button("Converter", self.power)))
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

    # ---- Settings: one row in the sidebar, a tab for the app and one for each tool

    def _general(self) -> QWidget:
        """Settings that are the same for every tool."""
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

        rows = ((tr("Language"), self.language), (tr("Sound when done"), snd_row), (tr("Volume"), vol_row))
        for r, (text, widget) in enumerate(rows):
            grid.addWidget(label(text), r, 0)
            (grid.addLayout if isinstance(widget, QHBoxLayout) else grid.addWidget)(widget, r, 1)
        grid.setColumnStretch(1, 1)
        bl.addLayout(grid)
        lay.addWidget(box)

        checks, cl = card()
        self.startup = checkbox(tr("Start Corefold when Windows starts"),
                                startup_enabled() if self.pending_startup is None else self.pending_startup,
                                self._startup_toggled)
        self.show_progress = checkbox(tr("Show a popup while converting and when it's done"), s.show_progress,
                                      self._general_changed)
        self.problem_sound = checkbox(tr("Play a soft sound when something goes wrong"), s.sound_problems,
                                      self._general_changed)
        for cb in (self.startup, self.show_progress, self.problem_sound):
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
        lay.addLayout(_reset_row(lambda: self.reset_to_defaults(GENERAL_KEYS)))
        lay.addStretch()
        return w

    def _conv_options(self) -> QWidget:
        """The Converter: dragging, where new files go, the right-click menu."""
        s = self.c.settings
        w, lay = page(tr("Options"))

        box, bl = card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
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

        self.unavailable = QComboBox()
        self.unavailable.addItem(tr("Show them dimmed (with the reason)"), True)
        self.unavailable.addItem(tr("Hide them"), False)
        self.unavailable.setCurrentIndex(0 if s.show_unavailable else 1)
        self.unavailable.currentIndexChanged.connect(self._general_changed)

        rows = ((tr("Key to hold while dragging"), self.shortcut), (tr("Save new files"), self.save_mode),
                ("", self.folder_row), (tr("Image quality"), q_row), (tr("Formats that don't fit a file"),
                                                                      self.unavailable))
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
        self.reveal = checkbox(tr("Open the folder with the new file after converting"), s.reveal_after,
                               self._general_changed)
        self.tick = checkbox(tr("Play a tiny tick when the wheel opens"), s.sound_tick, self._general_changed)
        self.downloads = checkbox(tr("When an iPhone photo (HEIC) or WebP picture lands in Downloads, offer a "
                                     "JPG copy"), s.watch_downloads, self.c.set_watch_downloads)
        self.pack_everywhere = checkbox(tr("Show ZIP / 7z options on every wheel"), s.pack_on_every_wheel,
                                        self._general_changed)
        for cb in (self.ask_names, self.reveal, self.tick, self.downloads, self.pack_everywhere):
            cl.addWidget(cb)
        lay.addWidget(checks)
        lay.addLayout(_reset_row(lambda: self.reset_to_defaults(CONVERTER_KEYS)))
        lay.addStretch()
        return w

    def _look(self) -> QWidget:
        """How Corefold looks: light or dark, the accent colour, text size and popups."""
        s = self.c.settings
        w, lay = page(tr("Look"))

        controls, cl = card(8)
        appearance = Segmented([(k, tr(v)) for k, v in APPEARANCES.items()], s.appearance)
        self.appearance_seg = appearance
        appearance.changed.connect(lambda v: self._look_changed("appearance", v))
        self.swatches = Swatches(s.accent, s.custom_color)
        self.swatches.changed.connect(lambda v: self._look_changed("accent", v))
        self.swatches.custom_picked.connect(lambda v: setattr(self.c.settings, "custom_color", v))
        self.accent_name = label("", "muted")
        win_text = Segmented([(k, tr(v)) for k, v in WINDOW_TEXT.items()], s.window_text)
        win_text.changed.connect(lambda v: self._look_changed("window_text", v))
        for title, widget in ((tr("Appearance"), appearance), (tr("Accent colour"), self.swatches),
                              (None, self.accent_name), (tr("Text size in Corefold's windows"), win_text)):
            if title:
                cl.addSpacing(6)
                cl.addWidget(label(title, "h2"))
            cl.addWidget(widget)
        cl.addSpacing(10)
        self.animations = checkbox(tr("Animations"), s.animations, lambda v: self._look_changed("animations", v))
        cl.addWidget(self.animations)
        cl.addWidget(checkbox(tr("Icons in the sidebar"), s.sidebar_icons,
                              lambda v: self._look_changed("sidebar_icons", v)))
        lay.addWidget(controls)

        # the popups
        more, ml = card(8)
        corner = QComboBox()
        for key, text in TOAST_CORNERS.items():
            corner.addItem(tr(text), key)
        corner.setCurrentIndex(max(0, corner.findData(s.toast_corner)))
        corner.currentIndexChanged.connect(lambda _: self._look_changed("toast_corner", corner.currentData()))
        corner.setMaximumWidth(320)
        toast_time = Segmented([(k, tr(v)) for k, v in TOAST_TIMES.items()], s.toast_time)
        toast_time.changed.connect(lambda v: self._look_changed("toast_time", v))
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
        lay.addWidget(more)
        lay.addLayout(_reset_row(lambda: self.reset_to_defaults(LOOK_KEYS)))
        lay.addStretch()
        return w

    def _wheel_look(self) -> QWidget:
        """The Converter's wheel: shape, size, labels, colours, with a live preview."""
        s = self.c.settings
        w, lay = page(tr("Wheel"), tr("The preview updates as you change things."))
        controls, cl = card(8)
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
        for title, widget in ((tr("Wheel shape"), style), (tr("Wheel size"), size),
                              (tr("Slice labels"), labels), (tr("Text size"), text_size),
                              (tr("Pointing at a slice"), hover), (tr("Slice colours"), colors),
                              (tr("Options per page"), per_holder), (tr("Where the wheel opens"), position),
                              (tr("Wheel see-through"), self.opacity)):
            cl.addSpacing(6)
            cl.addWidget(label(title, "h2"))
            cl.addWidget(widget)
        cl.addSpacing(10)
        self.numbers = checkbox(tr("Show numbers on the slices when the wheel is opened from the right-click menu "
                                   "(press 1–9 to pick, 0 for More)"), s.wheel_numbers,
                                lambda v: self._look_changed("wheel_numbers", v))
        cl.addWidget(self.numbers)
        cl.addStretch()

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
        lay.addLayout(_reset_row(lambda: self.reset_to_defaults(WHEEL_KEYS)))
        lay.addStretch()
        return w

    def _apps_settings(self) -> QWidget:
        """The Uninstaller."""
        s = self.c.settings
        w, lay = page(tr("Uninstaller"))
        box, bl = card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        self.unused_months = QComboBox()
        for key, text in UNUSED_MONTHS.items():
            self.unused_months.addItem(tr(text), key)
        self.unused_months.setCurrentIndex(max(0, self.unused_months.findData(s.unused_months)))
        self.unused_months.currentIndexChanged.connect(self._tool_setting_changed)
        self.backup_days = QComboBox()
        for key, text in BACKUP_DAYS.items():
            self.backup_days.addItem(tr(text), key)
        self.backup_days.setCurrentIndex(max(0, self.backup_days.findData(s.backup_days)))
        self.backup_days.currentIndexChanged.connect(self._tool_setting_changed)
        grid.addWidget(label(tr("Count a program as unused after")), 0, 0)
        grid.addWidget(self.unused_months, 0, 1)
        grid.addWidget(label(tr("Keep backups of removed leftovers for")), 1, 0)
        grid.addWidget(self.backup_days, 1, 1)
        grid.setColumnStretch(2, 1)
        bl.addLayout(grid)
        bl.addWidget(label(tr("Backups let you put back anything Corefold removed. Older ones are deleted by "
                              "themselves to free up space."), "muted", wrap=True))
        lay.addWidget(box)
        checks, cl = card()
        self.restore_point = checkbox(tr("Make a System Restore point before uninstalling (ticked to begin with)"),
                                      s.restore_point, self._tool_setting_changed)
        cl.addWidget(self.restore_point)
        lay.addWidget(checks)
        lay.addLayout(_reset_row(lambda: self.reset_to_defaults(UNINSTALLER_KEYS)))
        lay.addStretch()
        return w

    def _viewer(self) -> QWidget:
        """Corefold Viewer: open pictures and email files, and the ones opened lately."""
        dark, dpr = self.c.theme.dark, self.devicePixelRatioF()
        w, lay = page(tr("Corefold Viewer"))
        lay.itemAt(0).widget().hide()
        lay.addLayout(tool_header("viewer", tr("Corefold Viewer"),
                                  tr("Flip through a folder of pictures fast, fix them on the spot, and open email "
                                     "files."), dark, dpr, self.tool_settings_button("Viewer")))
        self.viewer_drop = DropZone(None, None, title=tr("Drop a picture or an email file"),
                                    sub=tr("Pictures open with the rest of their folder: use the arrow keys to flip "
                                           "through. Outlook (.msg) and .eml emails open without Outlook."),
                                    hot_title=tr("Let go"),
                                    buttons=[(tr("Open a picture…"), self._open_picture, "primary"),
                                             (tr("Open an email…"), self._open_email)])
        self.viewer_drop.dropped.connect(lambda paths: [self.c.open_viewer(p) for p in paths[:3]])
        lay.addWidget(self.viewer_drop)

        box, bl = card(8)
        head = QHBoxLayout()
        head.addWidget(label(tr("Opened lately"), "h2"))
        head.addStretch()
        self.views_clear = button(tr("Clear"), "flat", self._clear_views)
        head.addWidget(self.views_clear)
        bl.addLayout(head)
        self.views_lay = QVBoxLayout()
        self.views_lay.setSpacing(2)
        bl.addLayout(self.views_lay)
        lay.addWidget(box)

        box, bl = card(8)
        bl.addWidget(label(tr("Good to know"), "h2"))
        for text in (tr("Arrow keys flip through the folder; the mouse wheel zooms, a double-click shows the "
                        "actual size."),
                     tr("R turns the picture, C crops it, Delete sends it to the Recycle Bin, F11 is full screen."),
                     tr("Turning and cropping only change the file when you press Save, and the photo's details "
                        "are kept."),
                     tr("Emails never load anything from the internet, and links show where they really go "
                        "first.")):
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(self._accent_dot(), 0, Qt.AlignTop)
            row.addWidget(label(text, wrap=True), 1)
            bl.addLayout(row)
        lay.addWidget(box)
        lay.addStretch()
        return w

    def refresh_views(self):
        """The pictures and emails opened lately (ones that were moved or deleted are left out)."""
        if "Viewer" in self._unbuilt:
            return
        lay = self.views_lay
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        shown = 0
        for name in self.c.settings.recent_views:
            path = Path(name)
            try:
                there = path.is_file()
            except OSError:
                there = False
            if not there:
                continue
            email = path.suffix.lower() in (".eml", ".msg")
            row = QPushButton(f"{short_name(path.name, 60)}   ·   {short_name(str(path.parent), 60)}")
            row.setObjectName("flat")
            row.setToolTip(str(path))
            row.setCursor(Qt.PointingHandCursor)
            row.setIcon(icons.icon("chat" if email else "image", self.c.theme.muted))
            row.setStyleSheet("text-align: left;" if not i18n.is_rtl() else "text-align: right;")
            row.clicked.connect(lambda _=False, p=path: self.c.open_viewer(p))
            lay.addWidget(row)
            shown += 1
        if not shown:
            lay.addWidget(label(tr("Pictures and emails you open show up here."), "muted", wrap=True))
        self.views_clear.setVisible(bool(shown))

    def _clear_views(self):
        self.c.settings.recent_views = []
        self.c.settings.save()
        self.refresh_views()

    def _open_picture(self):
        from .viewer import PICTURE_EXT
        pattern = " ".join(f"*{e}" for e in sorted(PICTURE_EXT))
        name, _ = QFileDialog.getOpenFileName(self, tr("Open a picture"), str(Path.home() / "Pictures"),
                                              f"{tr('Pictures')} ({pattern});;{tr('All files')} (*)")
        if name:
            self.c.open_viewer(Path(name))

    def _open_email(self):
        name, _ = QFileDialog.getOpenFileName(self, tr("Open an email"), str(Path.home()),
                                              f"{tr('Emails')} (*.msg *.eml);;{tr('All files')} (*)")
        if name:
            self.c.open_viewer(Path(name))

    def _viewer_settings(self) -> QWidget:
        """Corefold Viewer."""
        s = self.c.settings
        w, lay = page(tr("Viewer"))
        box, bl = card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        self.viewer_wheel = QComboBox()
        for key, text in VIEWER_WHEEL.items():
            self.viewer_wheel.addItem(tr(text), key)
        self.viewer_wheel.setCurrentIndex(max(0, self.viewer_wheel.findData(s.viewer_wheel)))
        self.viewer_wheel.currentIndexChanged.connect(self._viewer_setting_changed)
        self.slideshow_seconds = QComboBox()
        for key, text in SLIDESHOW_SECONDS.items():
            self.slideshow_seconds.addItem(tr(text), key)
        self.slideshow_seconds.setCurrentIndex(max(0, self.slideshow_seconds.findData(s.slideshow_seconds)))
        self.slideshow_seconds.currentIndexChanged.connect(self._viewer_setting_changed)
        grid.addWidget(label(tr("The mouse wheel")), 0, 0)
        grid.addWidget(self.viewer_wheel, 0, 1)
        grid.addWidget(label(tr("Slideshow: each picture for")), 1, 0)
        grid.addWidget(self.slideshow_seconds, 1, 1)
        grid.setColumnStretch(2, 1)
        bl.addLayout(grid)
        self.viewer_confirm_delete = checkbox(tr("Ask before sending a picture to the Recycle Bin"),
                                              s.viewer_confirm_delete, self._viewer_setting_changed)
        bl.addWidget(self.viewer_confirm_delete)
        lay.addWidget(box)
        box, bl = card()
        bl.addWidget(label(tr("Double-click to open"), "h2"))
        self.open_images_setting = checkbox(tr("Open pictures with Corefold (it shows up under \"Open with\")"),
                                            s.open_images, lambda on: self.c.set_open_files("open_images", on))
        self.open_emails_setting = checkbox(tr("Open email files (.msg, .eml) with Corefold"), s.open_emails,
                                            lambda on: self.c.set_open_files("open_emails", on))
        bl.addWidget(self.open_images_setting)
        bl.addWidget(self.open_emails_setting)
        row = QHBoxLayout()
        row.addWidget(button(tr("Make Corefold the default…"), None, self._default_for_viewer))
        row.addStretch()
        bl.addLayout(row)
        bl.addWidget(label(tr("Windows asks you to pick the default app yourself. This opens the right place in "
                              "Settings."), "muted", wrap=True))
        lay.addWidget(box)
        lay.addLayout(_reset_row(lambda: self.reset_to_defaults(VIEWER_KEYS)))
        lay.addStretch()
        return w

    def _viewer_setting_changed(self, *_):
        s = self.c.settings
        s.viewer_wheel = self.viewer_wheel.currentData()
        s.slideshow_seconds = self.slideshow_seconds.currentData()
        s.viewer_confirm_delete = self.viewer_confirm_delete.isChecked()
        self.c.settings_changed()

    def _default_for_viewer(self):
        from . import assoc
        if not self.open_images_setting.isChecked():
            self.open_images_setting.setChecked(True)
        assoc.open_default_apps()

    def _clipboard(self) -> QWidget:
        """Corefold Clipboard: everything copied, searchable, with pins."""
        from .clipview import ClipboardPage
        dark, dpr = self.c.theme.dark, self.devicePixelRatioF()
        header = tool_header("clipboard", tr("Corefold Clipboard"),
                             tr("Everything you copy, kept on this PC and easy to find again."), dark, dpr,
                             self.tool_settings_button("Clipboard"))
        self.clip_page = ClipboardPage(self.c, header)
        return self.clip_page

    def _space(self) -> QWidget:
        """Corefold Space: what's filling the disk, junk to clean up, duplicates."""
        from .spacepage import SpacePage
        dark, dpr = self.c.theme.dark, self.devicePixelRatioF()
        header = tool_header("space", tr("Corefold Space"), tr("See what's filling your disk and free up space safely."),
                             dark, dpr)
        self.space_page = SpacePage(self.c, header)
        return self.space_page

    def _player(self) -> QWidget:
        """Corefold Player: open videos and music, and pick up where you left off."""
        dark, dpr = self.c.theme.dark, self.devicePixelRatioF()
        w, lay = page(tr("Corefold Player"))
        lay.itemAt(0).widget().hide()
        lay.addLayout(tool_header("player", tr("Corefold Player"),
                                  tr("Plays every video and song, smooth in 4K, with clean controls that get out of "
                                     "the way."), dark, dpr, self.tool_settings_button("Player")))
        drop = DropZone(None, None, title=tr("Drop a video or a song"),
                        sub=tr("The rest of its folder plays after it. Drop a subtitle file on the player to add it."),
                        hot_title=tr("Let go"), buttons=[(tr("Open a video or song…"), self._open_media, "primary")])
        drop.dropped.connect(lambda paths: self.c.open_player(paths[0]) if paths else None)
        lay.addWidget(drop)
        box, bl = card(8)
        head = QHBoxLayout()
        head.addWidget(label(tr("Continue watching"), "h2"))
        head.addStretch()
        self.watching_clear = button(tr("Clear"), "flat", self._clear_watching)
        head.addWidget(self.watching_clear)
        bl.addLayout(head)
        self.watching_lay = QVBoxLayout()
        self.watching_lay.setSpacing(2)
        bl.addLayout(self.watching_lay)
        lay.addWidget(box)
        box, bl = card(8)
        bl.addWidget(label(tr("Good to know"), "h2"))
        for text in (tr("Space pauses, the arrow keys jump back and forward, F is full screen, I the mini player."),
                     tr("[ and ] change the speed; , and . go one picture at a time while paused."),
                     tr("L marks a loop: once for the start, again for the end. More saves that part as a clip."),
                     tr("Subtitles a bit early or late? G shows them earlier, H later.")):
            r = QHBoxLayout()
            r.setSpacing(10)
            r.addWidget(self._accent_dot(), 0, Qt.AlignTop)
            r.addWidget(label(text, wrap=True), 1)
            bl.addLayout(r)
        lay.addWidget(box)
        lay.addStretch()
        return w

    def refresh_watching(self):
        if "Player" in self._unbuilt:
            return
        from .player import clock, continue_watching
        lay = self.watching_lay
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        items = continue_watching(8)
        for path, pos, dur, when in items:
            row = QPushButton(f"{short_name(path.name, 60)}   ·   {clock(pos)} / {clock(dur)}   ·   {time_ago(when)}")
            row.setObjectName("flat")
            row.setToolTip(str(path))
            row.setCursor(Qt.PointingHandCursor)
            row.setIcon(icons.icon("video", self.c.theme.muted))
            row.setStyleSheet("text-align: left;" if not i18n.is_rtl() else "text-align: right;")
            row.clicked.connect(lambda _=False, p=path: self.c.open_player(p))
            lay.addWidget(row)
        if not items:
            lay.addWidget(label(tr("Videos you stop part way show up here, to pick up where you left off."), "muted",
                                wrap=True))
        self.watching_clear.setVisible(bool(items))

    def _clear_watching(self):
        from .player import forget_positions
        forget_positions()
        self.refresh_watching()

    def _open_media(self):
        from .player import MEDIA_EXT
        pattern = " ".join(f"*{e}" for e in sorted(MEDIA_EXT))
        name, _ = QFileDialog.getOpenFileName(self, tr("Open a video or song"), str(Path.home() / "Videos"),
                                              f"{tr('Videos and music')} ({pattern});;{tr('All files')} (*)")
        if name:
            self.c.open_player(Path(name))

    def _player_settings(self) -> QWidget:
        """Corefold Player."""
        s = self.c.settings
        w, lay = page(tr("Player"))
        box, bl = card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        self.player_skip = QComboBox()
        for key, text in PLAYER_SKIP.items():
            self.player_skip.addItem(tr(text), key)
        self.player_skip.setCurrentIndex(max(0, self.player_skip.findData(s.player_skip)))
        self.player_wheel = QComboBox()
        for key, text in PLAYER_WHEEL.items():
            self.player_wheel.addItem(tr(text), key)
        self.player_wheel.setCurrentIndex(max(0, self.player_wheel.findData(s.player_wheel)))
        for combo in (self.player_skip, self.player_wheel):
            combo.currentIndexChanged.connect(self._player_setting_changed)
        grid.addWidget(label(tr("The arrow keys jump")), 0, 0)
        grid.addWidget(self.player_skip, 0, 1)
        grid.addWidget(label(tr("The mouse wheel over a video")), 1, 0)
        grid.addWidget(self.player_wheel, 1, 1)
        grid.setColumnStretch(2, 1)
        bl.addLayout(grid)
        self.player_resume = checkbox(tr("Pick up where I left off"), s.player_resume, self._player_setting_changed)
        self.player_next = checkbox(tr("When a file ends, play the next one in its folder"), s.player_next,
                                    self._player_setting_changed)
        self.player_subtitles = checkbox(tr("Turn on a video's own subtitles"), s.player_subtitles,
                                         self._player_setting_changed)
        for cb in (self.player_resume, self.player_next, self.player_subtitles):
            bl.addWidget(cb)
        lay.addWidget(box)
        box, bl = card()
        bl.addWidget(label(tr("Double-click to open"), "h2"))
        self.open_media_setting = checkbox(tr("Open videos and music with Corefold (it shows up under \"Open with\")"),
                                           s.open_media, lambda on: self.c.set_open_files("open_media", on))
        bl.addWidget(self.open_media_setting)
        row = QHBoxLayout()
        row.addWidget(button(tr("Make Corefold the default…"), None, self._default_for_player))
        row.addStretch()
        bl.addLayout(row)
        bl.addWidget(label(tr("Windows asks you to pick the default app yourself. This opens the right place in "
                              "Settings."), "muted", wrap=True))
        lay.addWidget(box)
        lay.addLayout(_reset_row(lambda: self.reset_to_defaults(PLAYER_KEYS)))
        lay.addStretch()
        return w

    def _player_setting_changed(self, *_):
        s = self.c.settings
        s.player_skip = self.player_skip.currentData()
        s.player_wheel = self.player_wheel.currentData()
        s.player_resume = self.player_resume.isChecked()
        s.player_next = self.player_next.isChecked()
        s.player_subtitles = self.player_subtitles.isChecked()
        self.c.settings_changed()

    def _default_for_player(self):
        from . import assoc
        if not self.open_media_setting.isChecked():
            self.open_media_setting.setChecked(True)
        assoc.open_default_apps()

    def _cloud(self) -> QWidget:
        """Corefold Cloud: OneDrive and Dropbox side by side."""
        from .cloudpage import CloudPage
        dark, dpr = self.c.theme.dark, self.devicePixelRatioF()
        header = tool_header("cloud", tr("Corefold Cloud"),
                             tr("Your OneDrive and Dropbox in one window. Drag files between them."), dark, dpr)
        self.cloud_page = CloudPage(self.c, header)
        return self.cloud_page

    def _capture(self) -> QWidget:
        """Corefold Capture: screenshots and recordings."""
        dark, dpr = self.c.theme.dark, self.devicePixelRatioF()
        w, lay = page(tr("Corefold Capture"))
        lay.itemAt(0).widget().hide()
        lay.addLayout(tool_header("capture", tr("Corefold Capture"),
                                  tr("Take a picture or a video of your screen, mark it up and share it in seconds."),
                                  dark, dpr, self.tool_settings_button("Capture")))
        box, bl = card()
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(button(tr("Take a screenshot"), "primary", lambda: self.c.capture and self.c.capture.screenshot()))
        row.addWidget(button(tr("Record the screen"), None, lambda: self.c.capture and self.c.capture.record()))
        row.addStretch()
        bl.addLayout(row)
        self.capture_keys = label("", "muted", wrap=True)
        bl.addWidget(self.capture_keys)
        lay.addWidget(box)
        box, bl = card(8)
        head = QHBoxLayout()
        head.addWidget(label(tr("Lately"), "h2"))
        head.addStretch()
        head.addWidget(button(tr("Open the folder"), "flat", self._open_capture_folder))
        bl.addLayout(head)
        self.captures_lay = QVBoxLayout()
        self.captures_lay.setSpacing(2)
        bl.addLayout(self.captures_lay)
        lay.addWidget(box)
        box, bl = card(8)
        bl.addWidget(label(tr("Good to know"), "h2"))
        for text in (tr("Drag over an area, click a window, or press Enter for the whole screen."),
                     tr("Every screenshot is copied straight away: paste it anywhere with Ctrl+V."),
                     tr("Blur hides passwords and names for good: the picture saved has no way back to them."),
                     tr("Recordings are saved as MP4 videos in Videos\\Captures. Nothing is uploaded.")):
            r = QHBoxLayout()
            r.setSpacing(10)
            r.addWidget(self._accent_dot(), 0, Qt.AlignTop)
            r.addWidget(label(text, wrap=True), 1)
            bl.addLayout(r)
        lay.addWidget(box)
        lay.addStretch()
        return w

    def refresh_captures(self):
        if "Capture" in self._unbuilt:
            return
        from .capture import recent_captures
        from .hotkey import SHORTCUT_NAMES
        s = self.c.settings
        bits = []
        if s.cap_shortcut != "off":
            bits.append(tr("Screenshot: {key}", key=SHORTCUT_NAMES.get(s.cap_shortcut, "")))
        if s.cap_record_shortcut != "off":
            bits.append(tr("Start or stop recording: {key}", key=SHORTCUT_NAMES.get(s.cap_record_shortcut, "")))
        self.capture_keys.setText("   ·   ".join(bits) or tr("Pick shortcuts in Settings to capture from any app."))
        lay = self.captures_lay
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        files = recent_captures(8)
        for path in files:
            video = path.suffix.lower() == ".mp4"
            row = QPushButton(f"{short_name(path.name, 60)}   ·   {time_ago(path.stat().st_mtime)}")
            row.setObjectName("flat")
            row.setToolTip(str(path))
            row.setCursor(Qt.PointingHandCursor)
            row.setIcon(icons.icon("video" if video else "image", self.c.theme.muted))
            row.setStyleSheet("text-align: left;" if not i18n.is_rtl() else "text-align: right;")
            row.clicked.connect(lambda _=False, p=path, v=video: QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
                                if v else self.c.open_viewer(p))
            lay.addWidget(row)
        if not files:
            lay.addWidget(label(tr("Your screenshots and recordings show up here."), "muted", wrap=True))

    def _open_capture_folder(self):
        from .capture import screenshots_folder
        folder = screenshots_folder()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _capture_settings(self) -> QWidget:
        """Corefold Capture."""
        s = self.c.settings
        w, lay = page(tr("Capture"))
        box, bl = card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        self.cap_shortcut = QComboBox()
        for key, text in CAP_SHORTCUTS.items():
            self.cap_shortcut.addItem(tr(text) if key == "off" else text, key)
        self.cap_shortcut.setCurrentIndex(max(0, self.cap_shortcut.findData(s.cap_shortcut)))
        self.cap_record_shortcut = QComboBox()
        for key, text in CAP_RECORD_SHORTCUTS.items():
            self.cap_record_shortcut.addItem(tr(text) if key == "off" else text, key)
        self.cap_record_shortcut.setCurrentIndex(max(0, self.cap_record_shortcut.findData(s.cap_record_shortcut)))
        self.cap_after = QComboBox()
        for key, text in CAP_AFTER.items():
            self.cap_after.addItem(tr(text), key)
        self.cap_after.setCurrentIndex(max(0, self.cap_after.findData(s.cap_after)))
        grid.addWidget(label(tr("Take a screenshot with")), 0, 0)
        grid.addWidget(self.cap_shortcut, 0, 1)
        grid.addWidget(label(tr("Start or stop recording with")), 1, 0)
        grid.addWidget(self.cap_record_shortcut, 1, 1)
        grid.addWidget(label(tr("After a screenshot")), 2, 0)
        grid.addWidget(self.cap_after, 2, 1)
        grid.setColumnStretch(2, 1)
        bl.addLayout(grid)
        self.cap_key_note = label(tr("Another app already uses one of these shortcuts. Pick another one."), "warning",
                                  wrap=True)
        bl.addWidget(self.cap_key_note)
        self.refresh_capture_note()
        self.cap_save = checkbox(tr("Also save every screenshot in Pictures\\Screenshots"), s.cap_save,
                                 self._cap_setting_changed)
        bl.addWidget(self.cap_save)
        bl.addWidget(label(tr("Print Screen on its own may open Windows' Snipping Tool: Windows lets you turn that off "
                              "in Settings, Accessibility, Keyboard."), "muted", wrap=True))
        lay.addWidget(box)
        box, bl = card()
        bl.addWidget(label(tr("Recording"), "h2"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        self.cap_fps = QComboBox()
        for key, text in CAP_FPS.items():
            self.cap_fps.addItem(tr(text), key)
        self.cap_fps.setCurrentIndex(max(0, self.cap_fps.findData(s.cap_fps)))
        self.cap_mic = QComboBox()
        self.cap_mic.addItem(tr("No sound"), "")
        if s.cap_mic:
            self.cap_mic.addItem(s.cap_mic, s.cap_mic)
            self.cap_mic.setCurrentIndex(1)
        self._mics_found = False
        grid.addWidget(label(tr("Smoothness")), 0, 0)
        grid.addWidget(self.cap_fps, 0, 1)
        grid.addWidget(label(tr("Microphone")), 1, 0)
        grid.addWidget(self.cap_mic, 1, 1)
        grid.setColumnStretch(2, 1)
        bl.addLayout(grid)
        self.cap_cursor = checkbox(tr("Show the mouse pointer in recordings"), s.cap_cursor, self._cap_setting_changed)
        self.cap_countdown = checkbox(tr("Count down 3, 2, 1 before recording starts"), s.cap_countdown,
                                      self._cap_setting_changed)
        bl.addWidget(self.cap_cursor)
        bl.addWidget(self.cap_countdown)
        lay.addWidget(box)
        for combo in (self.cap_shortcut, self.cap_record_shortcut, self.cap_after, self.cap_fps, self.cap_mic):
            combo.currentIndexChanged.connect(self._cap_setting_changed)
        lay.addLayout(_reset_row(lambda: self.reset_to_defaults(CAPTURE_KEYS)))
        lay.addStretch()
        return w

    def _find_microphones(self):
        """Asks FFmpeg which microphones there are (only when the page is opened: it takes a moment)."""
        if self._mics_found or not hasattr(self, "cap_mic"):
            return
        self._mics_found = True
        import threading

        from .capture import microphones
        holder = {}

        def work():
            holder["mics"] = microphones()
        t = threading.Thread(target=work, daemon=True)
        t.start()

        def fill():
            if t.is_alive():
                QTimer.singleShot(200, fill)
                return
            if not _alive(self.cap_mic):
                return
            current = self.c.settings.cap_mic
            self.cap_mic.blockSignals(True)
            for mic in holder.get("mics", []):
                if self.cap_mic.findData(mic) < 0:
                    self.cap_mic.addItem(mic, mic)
            self.cap_mic.setCurrentIndex(max(0, self.cap_mic.findData(current)))
            self.cap_mic.blockSignals(False)
        QTimer.singleShot(200, fill)

    def _cap_setting_changed(self, *_):
        s = self.c.settings
        s.cap_shortcut = self.cap_shortcut.currentData()
        s.cap_record_shortcut = self.cap_record_shortcut.currentData()
        s.cap_after = self.cap_after.currentData()
        s.cap_save = self.cap_save.isChecked()
        s.cap_fps = self.cap_fps.currentData()
        s.cap_mic = self.cap_mic.currentData() or ""
        s.cap_cursor = self.cap_cursor.isChecked()
        s.cap_countdown = self.cap_countdown.isChecked()
        self.c.settings_changed()

    def refresh_capture_note(self):
        if hasattr(self, "cap_key_note"):
            cap = self.c.capture
            bad = cap is not None and not (cap.shot_key.ok and cap.rec_key.ok)
            self.cap_key_note.setVisible(bool(bad))

    def _clipboard_settings(self) -> QWidget:
        """Corefold Clipboard."""
        s = self.c.settings
        w, lay = page(tr("Clipboard"))
        box, bl = card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        self.clip_shortcut = QComboBox()
        for key, text in CLIP_SHORTCUTS.items():
            self.clip_shortcut.addItem(tr(text) if key == "off" else text, key)
        self.clip_shortcut.setCurrentIndex(max(0, self.clip_shortcut.findData(s.clip_shortcut)))
        self.clip_max = QComboBox()
        for n in CLIP_MAX:
            self.clip_max.addItem(tr("{count} items", count=locale().toString(n)), n)
        self.clip_max.setCurrentIndex(max(0, self.clip_max.findData(s.clip_max)))
        self.clip_days = QComboBox()
        for key, text in CLIP_DAYS.items():
            self.clip_days.addItem(tr(text), key)
        self.clip_days.setCurrentIndex(max(0, self.clip_days.findData(s.clip_days)))
        for combo in (self.clip_shortcut, self.clip_max, self.clip_days):
            combo.currentIndexChanged.connect(self._clip_setting_changed)
        grid.addWidget(label(tr("Open your clipboard with")), 0, 0)
        grid.addWidget(self.clip_shortcut, 0, 1)
        grid.addWidget(label(tr("Keep up to")), 1, 0)
        grid.addWidget(self.clip_max, 1, 1)
        grid.addWidget(label(tr("Forget copies older than")), 2, 0)
        grid.addWidget(self.clip_days, 2, 1)
        grid.setColumnStretch(2, 1)
        bl.addLayout(grid)
        self.clip_key_note = label(tr("Another app already uses this shortcut, so it can't open the clipboard. Pick "
                                      "another one."), "warning", wrap=True)
        bl.addWidget(self.clip_key_note)
        self.clip_key_note.setVisible(bool(self.c.clip is not None and not self.c.clip.hotkey.ok))
        bl.addWidget(label(tr("Pinned copies are always kept."), "muted", wrap=True))
        lay.addWidget(box)
        box, bl = card()
        self.clip_paste = checkbox(tr("Paste straight away when you pick something (otherwise it's only copied)"),
                                   s.clip_paste, self._clip_setting_changed)
        self.clip_images = checkbox(tr("Keep pictures you copy"), s.clip_images, self._clip_setting_changed)
        bl.addWidget(self.clip_paste)
        bl.addWidget(self.clip_images)
        lay.addWidget(box)
        box, bl = card()
        bl.addWidget(label(tr("Privacy"), "h2"))
        bl.addWidget(label(tr("Your clipboard history stays on this PC and is never uploaded. Passwords copied from "
                              "password managers are never kept."), "muted", wrap=True))
        row = QHBoxLayout()
        row.addWidget(button(tr("Clear the history…"), None, self._clear_clipboard))
        row.addStretch()
        bl.addLayout(row)
        lay.addWidget(box)
        lay.addLayout(_reset_row(lambda: self.reset_to_defaults(CLIPBOARD_KEYS)))
        lay.addStretch()
        return w

    def _clip_setting_changed(self, *_):
        s = self.c.settings
        s.clip_shortcut = self.clip_shortcut.currentData()
        s.clip_max = self.clip_max.currentData()
        s.clip_days = self.clip_days.currentData()
        s.clip_paste = self.clip_paste.isChecked()
        s.clip_images = self.clip_images.isChecked()
        self.c.settings_changed()

    def refresh_clipboard_note(self):
        if hasattr(self, "clip_key_note"):
            self.clip_key_note.setVisible(bool(self.c.clip is not None and not self.c.clip.hotkey.ok))

    def _clear_clipboard(self):
        if self.c.clip is None:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("Clear the clipboard history?"))
        box.setText(tr("Everything kept is forgotten, except what you pinned."))
        yes = box.addButton(tr("Clear"), QMessageBox.DestructiveRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            self.c.clip.clear()

    def _archive_settings(self) -> QWidget:
        """Corefold Archive."""
        s = self.c.settings
        w, lay = page(tr("Archives"))
        box, bl = card()
        bl.addWidget(label(tr("Extract here"), "h2"))
        self.extract_mode = QComboBox()
        for key, text in EXTRACT_MODES.items():
            self.extract_mode.addItem(tr(text), key)
        self.extract_mode.setCurrentIndex(max(0, self.extract_mode.findData(s.extract_mode)))
        self.extract_mode.currentIndexChanged.connect(self._tool_setting_changed)
        self.extract_mode.setMaximumWidth(360)
        bl.addWidget(self.extract_mode)
        bl.addWidget(label(tr("Smart: an archive with one folder or file inside is unpacked straight into place; "
                              "anything else goes into a new folder named after the archive."), "muted", wrap=True))
        self.extract_nested = checkbox(tr("Also unpack archives found inside (ticked to begin with)"),
                                       s.extract_nested, self._tool_setting_changed)
        bl.addWidget(self.extract_nested)
        lay.addWidget(box)
        box, bl = card()
        bl.addWidget(label(tr("Double-click to open"), "h2"))
        self.open_archives_setting = checkbox(tr("Open archives with Corefold (it shows up under \"Open with\")"),
                                              s.open_archives, self.c.set_open_archives)
        bl.addWidget(self.open_archives_setting)
        row = QHBoxLayout()
        row.addWidget(button(tr("Make Corefold the default…"), None, self._default_for_archives))
        row.addStretch()
        bl.addLayout(row)
        bl.addWidget(label(tr("Windows asks you to pick the default app yourself. This opens the right place in "
                              "Settings."), "muted", wrap=True))
        self.remember_archives = checkbox(tr("List the archives I open on the Archive page"), s.remember_archives,
                                          self._tool_setting_changed)
        bl.addWidget(self.remember_archives)
        lay.addWidget(box)
        lay.addLayout(_reset_row(lambda: self.reset_to_defaults(ARCHIVE_KEYS)))
        lay.addStretch()
        return w

    def _tool_setting_changed(self, *_):
        """A setting on the Uninstaller or Archive tab."""
        s = self.c.settings
        s.unused_months = self.unused_months.currentData()
        s.backup_days = self.backup_days.currentData()
        s.restore_point = self.restore_point.isChecked()
        s.extract_mode = self.extract_mode.currentData()
        s.extract_nested = self.extract_nested.isChecked()
        s.remember_archives = self.remember_archives.isChecked()
        self.c.settings_changed()

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
        text.addWidget(label(tr("Everyday tools for Windows: convert files, remove programs, open and make "
                                "archives. Everything happens on your PC; nothing is uploaded."), "muted", wrap=True))
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
        self.hero_figures["files"].setText(locale().toString(st.files))
        self.hero_figures["saved"].setText(human_size(st.bytes_saved) if st.bytes_saved else "0")
        self.hero_figures["tools"].setText(locale().toString(len(self.tools_on)))
        self.hello.setText(_greeting())
        for page_name, tool_card in self.tool_cards.items():  # a small figure under a tool, where there is one
            text = tr("{count} files converted", count=locale().toString(st.files)) \
                if page_name == "Converter" and st.files else ""
            tool_card.stat.setText(text)
            tool_card.stat.setVisible(bool(text))
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
        if e.key() == Qt.Key.Key_Escape:  # Esc steps back inside a page (Space), else closes the window
            here = self.stack.currentWidget()
            if not (callable(getattr(here, "go_back", None)) and here.go_back()):
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
        self.back_btn.setIcon(icons.icon("arrow_right" if i18n.is_rtl() else "arrow_left", t.text, disabled=t.tokens.subtle))
        self.search_btn.glyph = icons.icon("search", t.muted)
        self.search_btn.update()
        self.theme_btn.setIcon(icons.icon("weather_sunny" if t.dark else "weather_moon", t.text))
        self.theme_btn.setToolTip(tr("Switch to light mode") if t.dark else tr("Switch to dark mode"))
        for b, name in self.quick_buttons:
            b.setIcon(icons.icon(name, t.accent if not t.dark else t.text))
        self.settings_list.viewport().update()
        dpr = self.devicePixelRatioF()
        for lab, name in zip(self.vol_icons, ("speaker_0", "speaker_2")):
            lab.setPixmap(icons.pixmap(name, 18, t.muted, dpr))
        for b, name in getattr(self, "link_buttons", []):
            b.setIcon(icons.icon(name, t.on_accent if b.objectName() == "primary" else t.text))
        self.swatches.apply_theme(t)
        self.preview.theme = t
        self.preview.update()
        for dot in self._dots:
            dot.setPixmap(icons.pixmap("add", 16, t.accent, dpr))
        for gear in self._gears:
            gear.setIcon(icons.icon("settings", t.text))
        self.refresh_archives()
        self.refresh_views()
        if self.clip_page is not None:
            self.clip_page.apply_theme(t)
        if self.space_page is not None:
            self.space_page.apply_theme(t)
        if self.cloud_page is not None:
            self.cloud_page.apply_theme(t)
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
        if owner not in self.nav_rows:  # (a tool removed since): nothing changes, or Home on opening
            if self.nav.currentRow() < 0 and name != "Home":
                self.open_page("Home")
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

    def go_back(self) -> bool:
        """Back: first inside the page (a page with steps of its own, like a folder in Space), then to the
        page opened before it."""
        here = self.stack.currentWidget()
        if callable(getattr(here, "go_back", None)) and here.go_back():
            return True
        while self._went:
            name = self._went.pop()
            if NAV_OWNER.get(name, name) in self.nav_rows and name != self.current_page():
                self._going_back = True
                try:
                    self.open_page(name)
                finally:
                    self._going_back = False
                return True
        return False

    def hideEvent(self, e):
        QApplication.instance().removeEventFilter(self._back_button)
        super().hideEvent(e)

    def showEvent(self, e):
        QApplication.instance().installEventFilter(self._back_button)
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
