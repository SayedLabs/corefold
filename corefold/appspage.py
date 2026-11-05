"""The Apps page: uninstall programs, then clean up what they left behind (with a backup)."""
from __future__ import annotations

import dataclasses
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QFileInfo, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFileIconProvider, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMenu, QMessageBox, QProgressBar, QPushButton, QStackedWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import uninstall as un
from . import i18n, sheet
from .atomic.widgets import Dropdown
from .core import APP_NAME, OLD_APP_NAMES, human_size, short_name
from .i18n import tr, ltr

if TYPE_CHECKING:
    from .app import Controller

log = logging.getLogger(__name__)
APP_ROLE = Qt.UserRole
SORT_ROLE = Qt.UserRole + 1
FILLED_ROLE = Qt.UserRole + 2  # a leftover folder or key whose parts are listed under it
PARTS_SHOWN = 500              # parts listed under one folder or key: the rest are one row, "{count} more items"


def _button(text: str, name: str | None = None, on_click=None) -> QPushButton:
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    if on_click:
        b.clicked.connect(on_click)
    return b


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


class SortItem(QTreeWidgetItem):
    """Sorts sizes and dates by their value, not their text."""
    def __lt__(self, other):
        tree = self.treeWidget()
        col = tree.sortColumn() if tree else 0
        a, b = self.data(col, SORT_ROLE), other.data(col, SORT_ROLE)
        if a is not None and b is not None:
            return a < b
        return self.text(col).strip("\u202a\u202c").lower() < other.text(col).strip("\u202a\u202c").lower()


class _Signals(QObject):
    listed = Signal(object)         # list[App] or an Exception
    sized = Signal(object)          # App
    phase = Signal(str)             # what's still being worked out: "store", "sizes" or "" (nothing)
    icon = Signal(str, object)      # an icon file (or folder) and its picture (a QImage), made on a thread
    state = Signal(str, object)     # what the uninstall dialog is doing


# what the "Show" list can narrow the apps down to (English, shown through tr())
SHOW = {"all": "All apps", "unused": "Not used for 3 months", "big": "Big (1 GB or more)",
        "new": "Installed in the last 30 days", "startup": "Start with Windows",
        "extras": "Extras that came with Windows"}
# "not used for …" (chosen in Settings → Uninstaller): the filter's name and the count in the status line
UNUSED = {3: ("Not used for 3 months", "{count} not used for 3 months"),
          6: ("Not used for 6 months", "{count} not used for 6 months"),
          12: ("Not used for a year", "{count} not used for a year")}


def _months(controller) -> int:
    months = getattr(getattr(controller, "settings", None), "unused_months", 3)
    return months if months in UNUSED else 3


def _depth(controller) -> str:
    depth = getattr(getattr(controller, "settings", None), "uninstall_depth", "normal")
    return depth if depth in un.DEPTHS else "normal"


def _backup_days(controller) -> int:
    return getattr(getattr(controller, "settings", None), "backup_days", un.KEEP_DAYS)


def _add_usage(apps: list[un.App]):
    """When each app was last used, and whether it starts with Windows (quick: a few registry reads)."""
    try:
        used = un.usage()
    except Exception:
        log.debug("couldn't read when apps were used", exc_info=True)
        used = {}
    try:
        programs = un.startup_programs()
    except Exception:
        log.debug("couldn't read what starts with Windows", exc_info=True)
        programs = []
    for app in apps:
        try:
            app.last_used = un.last_used(app, used)
            app.startup = un.starts_with_windows(app, programs)
        except Exception:
            log.debug("usage of %s", app.name, exc_info=True)


def _shown(app: un.App, show: str, months: int = 3) -> bool:
    now = time.time()
    if show == "unused":  # only when Windows knows: an app with no date may still be in use
        return bool(app.last_used) and now - app.last_used > months * 30.5 * 86400
    if show == "big":
        return app.size >= 1024 ** 3
    if show == "new":
        try:
            return now - time.mktime(time.strptime(app.date, "%Y-%m-%d")) < 30 * 86400
        except (ValueError, OverflowError):
            return False
    if show == "startup":
        return app.startup
    if show == "extras":
        return un.is_windows_extra(app)
    return True


def _installed(date: str) -> str:
    """When an app was installed ("2026-01-29"), written the way the person's Windows writes dates."""
    if not date:
        return ""
    from PySide6.QtCore import QDate
    from .timefmt import latin_digits, locale
    day = QDate.fromString(date, "yyyy-MM-dd")
    return latin_digits(locale().toString(day, "d MMM yyyy")) if day.isValid() else date


def _when(ts: float) -> str:
    if not ts:
        return ""
    from .timefmt import time_ago
    return time_ago(ts)


def emit(signal, value) -> bool:
    """Sends from a worker thread. False once the page is gone."""
    try:
        signal.emit(value)
        return True
    except RuntimeError:
        return False


def _icon_path(app: un.App) -> str:
    """The file (or folder) whose icon the app is shown with, or ""."""
    for path in (app.icon, os.path.join(app.location, "") if app.location else ""):
        if path and os.path.exists(path) and (os.path.isdir(path) or path.lower().endswith((".exe", ".ico", ".dll"))):
            return path
    return ""


def _icon_image(path: str):
    """A file's icon as Windows shows it, as a QImage: safe on a thread (Windows can take a while
    to give some, and the list shouldn't stutter meanwhile)."""
    import ctypes
    from ctypes import byref, c_uint, c_void_p, c_wchar_p, wintypes
    from .quicksearch import hicon_image

    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [("hIcon", c_void_p), ("iIcon", ctypes.c_int), ("dwAttributes", wintypes.DWORD),
                    ("szDisplayName", ctypes.c_wchar * 260), ("szTypeName", ctypes.c_wchar * 80)]
    shell32, user32 = ctypes.windll.shell32, ctypes.windll.user32
    shell32.SHGetFileInfoW.argtypes = [c_wchar_p, wintypes.DWORD, ctypes.POINTER(SHFILEINFOW), c_uint, c_uint]
    shell32.SHGetFileInfoW.restype = ctypes.c_size_t
    user32.DestroyIcon.argtypes = [c_void_p]
    info = SHFILEINFOW()
    if not shell32.SHGetFileInfoW(path, 0, byref(info), ctypes.sizeof(info), 0x100) or not info.hIcon:
        return None  # SHGFI_ICON (large)
    try:
        return hicon_image(info.hIcon)
    finally:
        user32.DestroyIcon(info.hIcon)


def _make_icons(paths: list[str], cancel: threading.Event, signal):
    import ctypes
    ctypes.windll.ole32.CoInitializeEx(None, 2)  # (the shell wants it on every thread it's asked from)
    try:
        for path in paths:
            if cancel.is_set():
                return
            try:
                image = _icon_image(path)
            except Exception:
                log.debug("no icon for %s", path, exc_info=True)
                image = None
            if image is not None:
                try:
                    signal.emit(path, image)
                except RuntimeError:  # (the page is gone)
                    return
    finally:
        ctypes.windll.ole32.CoUninitialize()


# ----------------------------------------------------------------------------- the page

class HunterButton(QPushButton):
    """Drag it onto any window: the program that window belongs to is found in the list."""
    found = Signal(str)   # the program file of the window it was let go on ("" for none)

    def __init__(self, theme=None):
        super().__init__()
        self.setAccessibleName(tr("Drag onto a window"))
        self.setToolTip(tr("Drag onto a window") + "\n" +
                        tr("Don't know a program's name? Drag this onto one of its windows."))
        self.setCursor(Qt.OpenHandCursor)
        self.apply_theme(theme)

    def apply_theme(self, theme):
        from . import icons
        self.setIcon(icons.icon("target", theme.text if theme is not None else "#888888"))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.setDown(True)
            self.grabMouse(Qt.CrossCursor)
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(e)
        self.releaseMouse()
        self.setDown(False)
        if self.rect().contains(e.position().toPoint()):
            return  # let go on the button itself: nothing picked
        exe = ""
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes as wt
            pt = wt.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            exe = un.program_at_point(pt.x, pt.y)
        if exe and os.path.normcase(exe) == os.path.normcase(sys.executable):
            exe = ""  # Rakiza Core's own window
        self.found.emit(exe)


class AppsPage(QWidget):
    def __init__(self, controller: "Controller", window=None):
        super().__init__()
        self.c = controller
        self.apps: list[un.App] = []
        self.loaded = False
        self.loading = False
        self.signals = _Signals(self)
        self.signals.listed.connect(self._listed)
        self.signals.sized.connect(self._sized)
        self.signals.phase.connect(self._phase)
        self.signals.icon.connect(self._icon_made)
        self.phase = ""
        self.cancel = threading.Event()
        self.provider = QFileIconProvider()
        self._icon_queue: list[QTreeWidgetItem] = []  # rows still without their icon
        self._icon_paths: list[str] = []               # (off Windows: icon files still to make)
        self._icons: dict[str, QIcon] = {}   # icon file -> its icon (made once, kept while the page is)
        self._icon_items: dict[str, list[QTreeWidgetItem]] = {}  # icon file -> the rows waiting for it
        self._icon_cancel = threading.Event()
        self._items: dict[str, QTreeWidgetItem] = {}
        self.icon_timer = QTimer(self)
        self.icon_timer.timeout.connect(self._load_icons)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(12)
        from .window import tool_header
        lay.addLayout(tool_header("cleaner", tr("Uninstaller"),
                                  tr("Uninstall programs, then clean up the folders, shortcuts and registry entries they "
                                     "leave behind. Everything removed is kept as a backup for {days} days, so you can "
                                     "put it back.", days=_backup_days(controller)),
                                  controller.theme.dark if controller is not None else True, self.devicePixelRatioF(),
                                  window.tool_settings_button("Apps") if window is not None else None))

        top = QHBoxLayout()
        self.show_only = Dropdown()
        self.show_only.setAccessibleName(tr("Which apps to show"))
        for key, text in SHOW.items():
            self.show_only.addItem(tr(UNUSED[_months(controller)][0] if key == "unused" else text), key)
        self.show_only.currentIndexChanged.connect(self._filter)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search apps"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.store = QCheckBox(tr("Store apps"))
        self.store.setChecked(True)
        self.store.toggled.connect(self._filter)
        top.addWidget(self.search, 1)
        top.addWidget(self.show_only)
        top.addWidget(self.store)
        top.addWidget(_button(tr("Refresh"), on_click=self.reload))
        self.b_updates = _button(tr("Updates…"), on_click=self.show_updates)
        self.b_updates.setToolTip(tr("Look for newer versions of your apps, and update them"))
        top.addWidget(self.b_updates)
        self.hunter = HunterButton(controller.theme if controller is not None else None)
        self.hunter.found.connect(self._hunted)
        top.addWidget(self.hunter)
        lay.addLayout(top)

        self.tree = QTreeWidget()
        self.tree.setObjectName("apps")
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels([tr("Name"), tr("Publisher"), tr("Size"), tr("Installed"), tr("Last used")])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        h = self.tree.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        # (the publisher gets a set width, so a long one doesn't squeeze the names: it's cut short with …)
        h.setSectionResizeMode(1, QHeaderView.Interactive)
        h.resizeSection(1, 170)
        for col in (2, 3, 4):
            h.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._selection)
        self.tree.itemDoubleClicked.connect(lambda *_: self.uninstall_selected())
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.setMinimumHeight(60)  # (a small window: the list gives way, not the words above it)
        lay.addWidget(self.tree, 1)

        self.status = _label(tr("Loading the list of apps…"), "muted")
        lay.addWidget(self.status)
        self.show_note = _label(tr("\"Last used\" is when you last opened it from Start, the taskbar, a shortcut or File "
                                   "Explorer. Apps that only run in the background (like drivers) don't show a date, "
                                   "and aren't listed as unused."), "muted", wrap=True)
        self.show_note.hide()
        lay.addWidget(self.show_note)

        row = QHBoxLayout()
        self.b_uninstall = _button(tr("Uninstall"), "primary", self.uninstall_selected)
        self.b_leftovers = _button(tr("Remove leftovers only"), on_click=lambda: self.uninstall_selected(forced=True))
        self.b_leftovers.setToolTip(tr("For apps whose uninstaller is missing or broken: skips it and only offers "
                                       "what the app left behind."))
        self.b_folder = _button(tr("Open its folder"), on_click=self.open_folder)
        row.addWidget(self.b_uninstall)
        row.addWidget(self.b_leftovers)
        row.addWidget(self.b_folder)
        row.addStretch()
        self.b_more = _button(tr("More"))
        more = QMenu(self.b_more)
        from .theme import round_menu
        round_menu(more)
        more.addAction(tr("Browser extensions…"), self.show_extensions)
        more.addAction(tr("Watch an install…"), self.watch_install)
        more.addAction(tr("Folders of apps that are gone…"), self.show_orphans)
        more.addAction(tr("Backups…"), self.show_backups)
        more.addAction(tr("Save the list…"), self.save_list)
        self.b_more.setMenu(more)
        row.addWidget(self.b_more)
        lay.addLayout(row)
        self._selection()

    # ---- loading

    def ensure_loaded(self):
        if not self.loaded and not self.loading:
            self.reload()

    def reload(self):
        if self.loading:
            return
        self.loading = True
        self.cancel.set()
        self.cancel = threading.Event()
        self.status.setText(tr("Loading the list of apps…"))
        signals, cancel = self.signals, self.cancel

        def work():
            # programs first (quick) with the Store apps found last time, then today's Store apps (Windows
            # takes a few seconds to list those), then the sizes apps don't tell (remembered once worked out)
            cache = un.load_cache()
            sizes = cache.get("sizes") if isinstance(cache.get("sizes"), dict) else {}
            try:
                apps = un.list_apps(include_store=False)
                for a in apps:
                    if not a.size and isinstance(sizes.get(un.size_key(a)), int):
                        a.size = sizes[un.size_key(a)]
                old_store = un.cached_store_apps(cache)
                _add_usage(apps + old_store)
            except Exception as e:
                log.exception("couldn't list apps")
                apps, old_store = e, []
            if isinstance(apps, list) and not emit(signals.phase, "store"):
                return
            if not emit(signals.listed, apps if not isinstance(apps, list) else
                        sorted(apps + old_store, key=lambda a: a.name.lower())):
                return
            if not isinstance(apps, list):
                return
            store = None
            if not cancel.is_set():
                try:
                    store = un.store_apps()
                except Exception:
                    log.debug("couldn't list Store apps", exc_info=True)
            if cancel.is_set():
                return
            if store is not None:
                _add_usage(store)
                cache["store"] = [dataclasses.asdict(a) for a in store]
                un.save_cache(cache)
            else:
                store = old_store
            apps = sorted(apps + store, key=lambda a: a.name.lower())
            if store != old_store and not emit(signals.listed, apps):
                return
            missing = sorted([a for a in apps if not a.size and not a.store], key=lambda a: a.name.lower())
            if missing:
                emit(signals.phase, "sizes")

            def sized(a):
                sizes[un.size_key(a)] = a.size
                if not emit(signals.sized, a):
                    cancel.set()
            un.fill_sizes(missing, cancel, sized)
            if missing:
                known = {un.size_key(a) for a in apps}
                cache["sizes"] = {k: v for k, v in sizes.items() if k in known}
                un.save_cache(cache)
            emit(signals.phase, "")
        threading.Thread(target=work, daemon=True).start()

    def _phase(self, phase: str):
        self.phase = phase
        if self.loaded:
            self._update_status()

    def _listed(self, apps):
        self.loading = False
        if isinstance(apps, Exception):
            self.status.setText(tr("Couldn't read the list of apps: {error}", error=str(apps)))
            return
        self.loaded = True
        self.apps = apps
        selected = {i.data(0, APP_ROLE).id for i in self.tree.selectedItems()}
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        self._icon_queue = []
        self._items = {}
        for app in apps:
            item = SortItem([ltr(app.name), ltr(app.publisher), human_size(app.size) if app.size else "",
                             _installed(app.date), _when(app.last_used)])
            if not _icon_path(app):  # (no icon of its own: a plain one, so its name lines up with the others)
                item.setIcon(0, self._plain_icon())
            item.setData(0, APP_ROLE, app)
            item.setData(2, SORT_ROLE, app.size)
            item.setData(3, SORT_ROLE, app.date or "0")
            item.setData(4, SORT_ROLE, app.last_used)
            tip = [app.name]
            if app.version:
                tip.append(tr("Version {version}", version=app.version))
            if app.location:
                tip.append(app.location)
            if app.startup:
                tip.append(tr("Starts with Windows"))
            if app.store:
                tip.append(tr("From the Microsoft Store"))
            elif app.broken:
                tip.append(tr("Its uninstaller is missing: use \"Remove leftovers only\"."))
            item.setToolTip(0, "\n".join(tip))
            if app.broken:
                item.setForeground(0, self.palette().placeholderText())
            self.tree.addTopLevelItem(item)
            self._items[app.id] = item
            if app.id in selected:
                item.setSelected(True)
            self._icon_queue.append(item)
        self.tree.setSortingEnabled(True)
        self._filter()
        self._start_icons()

    def _start_icons(self):
        self._icon_cancel.set()
        self._icon_cancel = threading.Event()
        self._icon_items = {}
        for item in self._icon_queue:
            path = _icon_path(item.data(0, APP_ROLE))
            if path in self._icons:
                item.setIcon(0, self._icons[path])
            elif path:
                self._icon_items.setdefault(path, []).append(item)
        self._icon_queue = []
        wanted = list(self._icon_items)
        if not wanted:
            return
        if sys.platform == "win32":
            threading.Thread(target=_make_icons, args=(wanted, self._icon_cancel, self.signals.icon),
                             daemon=True).start()
        else:  # (off Windows, for tests: Qt's own icons, a few at a time)
            self._icon_paths = wanted
            self.icon_timer.start(15)

    def _plain_icon(self) -> QIcon:
        if getattr(self, "_plain", None) is None:
            from . import icons
            theme = self.c.theme if self.c is not None else None
            self._plain = icons.icon("apps", theme.muted if theme is not None else "#888888")
        return self._plain

    def _icon_made(self, path: str, image):
        self._show_icon(path, QIcon(QPixmap.fromImage(image)))

    def _show_icon(self, path: str, icon: QIcon):
        self._icons[path] = icon
        for item in self._icon_items.pop(path, []):
            try:
                item.setIcon(0, icon)
            except RuntimeError:  # (the list was made again meanwhile)
                pass

    def _load_icons(self):
        end = time.monotonic() + 0.012
        while time.monotonic() < end:
            if not self._icon_paths:
                self.icon_timer.stop()
                return
            path = self._icon_paths.pop(0)
            icon = self.provider.icon(QFileInfo(path))
            if not icon.isNull():
                self._show_icon(path, icon)

    def _sized(self, app):
        item = self._items.get(app.id)
        if item is not None and item.data(0, APP_ROLE) is app:
            item.setText(2, human_size(app.size))
            item.setData(2, SORT_ROLE, app.size)
        self._update_status()

    def _filter(self, *_):
        words = self.search.text().lower().split()
        show = self.show_only.currentData() or "all"
        stores = self.store.isChecked() or show == "extras"  # (Windows' extras are Store apps)
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            app = item.data(0, APP_ROLE)
            text = f"{app.name} {app.publisher}".lower()
            item.setHidden(not all(w in text for w in words) or (bool(app.store) and not stores)
                           or not _shown(app, show, _months(self.c)))
        self.show_note.setVisible(show == "unused")
        self._update_status()

    def _update_status(self):
        shown = [self.tree.topLevelItem(i).data(0, APP_ROLE) for i in range(self.tree.topLevelItemCount())
                 if not self.tree.topLevelItem(i).isHidden()]
        total = sum(a.size for a in shown)
        bits = [tr("{count} apps · {size}", count=len(shown), size=i18n.isolate(human_size(total)))]
        months = _months(self.c)
        unused = sum(1 for a in self.apps if _shown(a, "unused", months) and (not a.store or self.store.isChecked()))
        if unused and (self.show_only.currentData() or "all") == "all":
            bits.append(tr(UNUSED[months][1], count=unused))
        if self.phase == "store":
            bits.append(tr("Loading Store apps…"))
        elif self.phase == "sizes":
            bits.append(tr("Working out sizes…"))
        self.status.setText(i18n.join(bits) if self.loaded else tr("Loading the list of apps…"))

    def _selected(self) -> list[un.App]:
        return [i.data(0, APP_ROLE) for i in self.tree.selectedItems()]

    def _selection(self):
        apps = self._selected()
        self.b_uninstall.setEnabled(bool(apps))
        self.b_leftovers.setEnabled(bool(apps) and not any(a.store for a in apps))
        self.b_folder.setEnabled(len(apps) == 1 and bool(apps[0].location) and os.path.isdir(apps[0].location))

    # ---- actions

    def open_folder(self):
        apps = self._selected()
        if apps and apps[0].location and os.path.isdir(apps[0].location):
            os.startfile(apps[0].location)  # noqa: S606

    def _menu(self, pos):
        """Right-click on an app: what the buttons do, and finding out more about it."""
        item = self.tree.itemAt(pos)
        if item is None:
            return
        if not item.isSelected():
            self.tree.setCurrentItem(item)
        apps = self._selected()
        if not apps:
            return
        from PySide6.QtGui import QGuiApplication
        from .theme import round_menu
        m = QMenu(self)
        round_menu(m)
        m.addAction(tr("Uninstall"), self.uninstall_selected)
        leftovers = m.addAction(tr("Remove leftovers only"), lambda: self.uninstall_selected(forced=True))
        leftovers.setEnabled(self.b_leftovers.isEnabled())
        folder = m.addAction(tr("Open its folder"), self.open_folder)
        folder.setEnabled(self.b_folder.isEnabled())
        m.addSeparator()
        if len(apps) == 1:
            m.addAction(tr("Search the web for “{text}”", text=short_name(apps[0].name, 40)),
                        lambda: self._search(apps[0]))
        m.addAction(tr("Copy the name") if len(apps) == 1 else tr("Copy the names"),
                    lambda: QGuiApplication.clipboard().setText("\n".join(a.name for a in apps)))
        sheet.menu(m, self.tree.viewport().mapToGlobal(pos))
        sheet.forget(m)

    def _search(self, app: un.App):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from .quickfind import search_url
        engine = self.c.settings.quick_search_engine if self.c is not None else "duckduckgo"
        QDesktopServices.openUrl(QUrl(search_url(engine, f"{app.name} {app.publisher}")))

    def save_list(self):
        """Saves the apps shown (as they're sorted) to a file a spreadsheet opens: for a new PC, or for help."""
        from PySide6.QtWidgets import QFileDialog
        start = str(Path.home() / "Desktop" / (tr("Installed apps") + ".csv"))
        path, _ = QFileDialog.getSaveFileName(self, tr("Save the list…"), start, tr("Spreadsheet (*.csv)"))
        if not path:
            return
        import csv
        rows = [[tr("Name"), tr("Publisher"), tr("Version"), tr("Size"), tr("Installed"), tr("Last used")]]
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            app = it.data(0, APP_ROLE)
            if it.isHidden() or app is None:
                continue
            rows.append([app.name, app.publisher, app.version, it.text(2), it.text(3), it.text(4)])
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:  # (the mark lets Excel read Arabic)
                csv.writer(f).writerows(rows)
        except OSError as e:
            from .converters import friendly_error
            sheet.warning(self, tr("Save the list…"), tr("Couldn't save the list: {error}",
                                                              error=friendly_error(e)))
            return
        self.status.setText(tr("Saved the list of {count} apps", count=len(rows) - 1))

    def uninstall_selected(self, forced: bool = False):
        apps = self._selected()
        if not apps:
            return
        if any(_is_self(a) for a in apps):
            sheet.information(self, tr("Apps"), tr("To remove Rakiza Core itself, use \"Uninstall Rakiza Core\" on "
                                                   "the About page."))
            return
        dlg = UninstallDialog(self.c, apps, self.apps, forced, self.window().windowIcon())
        sheet.run(dlg)
        dlg.deleteLater()
        if dlg.changed:
            self.reload()

    def _hunted(self, exe: str):
        """A window was picked with "Drag onto a window": its program is picked in the list."""
        app = un.app_for_program(exe, self.apps) if exe else None
        if app is None:
            sheet.information(self, tr("Drag onto a window"),
                              tr("That window belongs to Windows itself or to a program that isn't installed "
                                 "(it runs on its own).") if exe else tr("No window there."))
            return
        if app.store and not self.store.isChecked():
            self.store.setChecked(True)
        self.search.clear()
        self.show_only.setCurrentIndex(0)
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, APP_ROLE).id == app.id:
                self.tree.clearSelection()
                item.setSelected(True)
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item, QAbstractItemView.PositionAtCenter)
                self.window().raise_()
                self.status.setText(tr("Found: {name}. Press Uninstall to remove it.", name=app.name))
                return

    def apply_theme(self, theme):
        self.hunter.apply_theme(theme)

    def show_extensions(self):
        sheet.run(ExtensionsDialog(self.window().windowIcon()))

    def watch_install(self):
        sheet.run(WatchInstallDialog(self.window().windowIcon()))

    def show_updates(self):
        dlg = UpdatesDialog(self.window().windowIcon())
        sheet.run(dlg)
        dlg.deleteLater()
        if dlg.changed:
            self.reload()

    def show_orphans(self):
        """Program folders no installed app uses any more (an app deleted by hand, or broken)."""
        dlg = OrphansDialog(self.c, self.apps, self.window().windowIcon())
        sheet.run(dlg)
        dlg.deleteLater()
        if dlg.changed:
            self.reload()

    def show_backups(self):
        dlg = BackupsDialog(self.c, self.window().windowIcon())
        sheet.run(dlg)
        dlg.deleteLater()
        if dlg.changed:
            self.reload()


def _is_self(app: un.App) -> bool:
    if app.name.lower().startswith(tuple(n.lower() for n in (APP_NAME, *OLD_APP_NAMES))):
        return True
    if getattr(sys, "frozen", False) and app.location:
        # (Rakiza Core's folder or one that holds it: not any folder whose name only starts the same, "...\\Core")
        return un._inside(os.path.dirname(sys.executable), app.location)
    return False


# ----------------------------------------------------------------------------- uninstalling

class UninstallDialog(QDialog):
    """Confirm → the app's own uninstaller → looking for leftovers → choose → removed (with undo)."""

    def __init__(self, controller, apps: list[un.App], everything: list[un.App], forced: bool, icon: QIcon):
        super().__init__(None, Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.c = controller
        self.apps, self.everything, self.forced = apps, everything, forced
        self.changed = False
        self.cancel = threading.Event()
        self.leftovers: list[un.Leftover] = []
        self.backup: Path | None = None
        self.busy = False
        self.setWindowIcon(icon)
        self.setWindowTitle(tr("Uninstall") if not forced else tr("Remove leftovers"))
        self.setMinimumSize(620, 460)
        self.signals = _Signals(self)
        self.signals.state.connect(self._state)

        self.stack = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.addWidget(self.stack, 1)

        # 1. confirm
        p1 = QWidget()
        l1 = QVBoxLayout(p1)
        l1.setContentsMargins(0, 0, 0, 0)
        names = ", ".join(short_name(a.name, 40) for a in apps[:3]) + ("…" if len(apps) > 3 else "")
        title = tr("Uninstall {name}?", name=names) if not forced else tr("Remove what {name} left behind?", name=names)
        self.confirm_title = _label(title, "h2", wrap=True)
        l1.addWidget(self.confirm_title)
        if forced:
            self.confirm_note = _label(tr("The app's uninstaller won't run. Rakiza Core only looks for its folders, "
                                          "shortcuts and registry entries, and you choose what goes."), "muted", wrap=True)
            l1.addWidget(self.confirm_note)
            if any(not a.broken for a in apps):
                warn = _label(tr("This app still looks installed (its uninstaller is still there). Removing its "
                                 "files would break it, so nothing will be ticked: use Uninstall instead if you can."),
                              wrap=True)
                warn.setObjectName("warning")
                l1.addWidget(warn)
        else:
            l1.addWidget(_label(tr("First the app's own uninstaller runs (follow its steps). Then Rakiza Core looks for "
                                   "anything it left behind, and you choose what goes."), "muted", wrap=True))
        self.quiet = QCheckBox(tr("Uninstall quietly where the app allows it (no questions)"))
        self.quiet.setChecked(len(apps) > 1)
        self.restore_point = QCheckBox(tr("Make a System Restore point first (asks for administrator)"))
        self.restore_point.setChecked(bool(getattr(getattr(controller, "settings", None), "restore_point", False)))
        l1.addSpacing(6)
        l1.addWidget(self.quiet)
        l1.addWidget(self.restore_point)
        # shown or hidden once they're in the dialog (before, showing one would open it as its own window)
        self.quiet.setVisible(not forced and any(a.quiet or a.msi or a.store for a in apps))
        self.restore_point.setVisible(not forced)
        l1.addStretch()
        r1 = QHBoxLayout()
        r1.addStretch()
        r1.addWidget(_button(tr("Cancel"), on_click=self.reject))
        self.go = _button(tr("Uninstall") if not forced else tr("Look for leftovers"), "primary", self.start)
        r1.addWidget(self.go)
        l1.addLayout(r1)
        self.stack.addWidget(p1)

        # 2. working (uninstaller running, scanning, removing)
        p2 = QWidget()
        l2 = QVBoxLayout(p2)
        l2.setContentsMargins(0, 0, 0, 0)
        self.work_title = _label("", "h2", wrap=True)
        self.work_detail = _label("", "muted", wrap=True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.clock = _label("", "muted")
        self.stuck_note = _label("", "warning", wrap=True)
        self.stuck_note.hide()
        l2.addStretch()
        l2.addWidget(self.work_title)
        l2.addWidget(self.work_detail)
        l2.addWidget(self.bar)
        l2.addWidget(self.clock)
        l2.addWidget(self.stuck_note)
        l2.addStretch()
        r2 = QHBoxLayout()
        self.show_btn = _button(tr("Show its window"), on_click=lambda: self.control.front.set())
        self.show_btn.setToolTip(tr("Brings the uninstaller's window to the front, in case it's behind other windows."))
        self.kill_btn = _button(tr("Stop the uninstaller"), "danger", on_click=lambda: self.control.stop.set())
        r2.addWidget(self.show_btn)
        r2.addWidget(self.kill_btn)
        r2.addStretch()
        self.skip = _button(tr("The uninstaller is done"), on_click=self._skip_wait)
        self.skip.setToolTip(tr("If the uninstaller has finished but Rakiza Core is still waiting, click here."))
        self.stop = _button(tr("Cancel"), on_click=self._stop)
        r2.addWidget(self.skip)
        r2.addWidget(self.stop)
        l2.addLayout(r2)
        self.stack.addWidget(p2)

        # 3. leftovers
        p3 = QWidget()
        l3 = QVBoxLayout(p3)
        l3.setContentsMargins(0, 0, 0, 0)
        self.left_title = _label("", "h2", wrap=True)
        l3.addWidget(self.left_title)
        self.left_note = _label(tr("Ticked items clearly belong to the app. Unticked ones only might, so check them "
                                   "first. Open a folder or a registry key to choose what goes from inside it. "
                                   "Everything removed is kept as a backup."), "muted", wrap=True)
        l3.addWidget(self.left_note)
        depth_row = QHBoxLayout()
        self.depth_label = _label(tr("How deep to clean up:"))
        depth_row.addWidget(self.depth_label)
        self.depth = Dropdown()
        for key, text in un.DEPTHS.items():
            self.depth.addItem(tr(text), key)
        self.depth.setCurrentIndex(max(0, self.depth.findData(_depth(controller))))
        self.depth.currentIndexChanged.connect(self._depth_changed)
        depth_row.addWidget(self.depth)
        depth_row.addStretch()
        l3.addLayout(depth_row)
        self.depth_help = _label("", "muted", wrap=True)
        l3.addWidget(self.depth_help)
        picks = QHBoxLayout()
        picks.setSpacing(6)
        self.pick_all = _button(tr("Select all"), "chipButton", lambda: self._tick_all(True))
        self.pick_none = _button(tr("Select none"), "chipButton", lambda: self._tick_all(False))
        self.pick_suggested = _button(tr("Only what's suggested"), "chipButton", self._tick_suggested)
        self.pick_suggested.setToolTip(tr("Ticks again only what Rakiza Core suggests removing."))
        for b in (self.pick_all, self.pick_none, self.pick_suggested):
            picks.addWidget(b)
        picks.addStretch()
        l3.addLayout(picks)
        self.left_tree = QTreeWidget()
        self.left_tree.setObjectName("apps")
        self.left_tree.setHeaderLabels([tr("What"), tr("How sure"), tr("Size")])
        self.left_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.left_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.left_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.left_tree.header().setStretchLastSection(False)
        self.left_tree.itemChanged.connect(self._ticks_changed)
        self.left_tree.itemExpanded.connect(self._fill_parts)
        self.left_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.left_tree.customContextMenuRequested.connect(self._left_menu)
        l3.addWidget(self.left_tree, 1)
        self.left_problems = _label("", "warning", wrap=True)
        self.left_problems.hide()
        l3.addWidget(self.left_problems)
        self.close_running = QCheckBox()
        self.close_running.setChecked(True)
        l3.addWidget(self.close_running)
        self.close_running.setVisible(False)
        r3 = QHBoxLayout()
        self.left_info = _label("", "muted")
        r3.addWidget(self.left_info, 1)
        r3.addWidget(_button(tr("Keep everything"), on_click=self._keep))
        self.remove_btn = _button(tr("Remove selected"), "primary", self.remove_selected)
        r3.addWidget(self.remove_btn)
        l3.addLayout(r3)
        self.stack.addWidget(p3)

        # 4. done
        p4 = QWidget()
        l4 = QVBoxLayout(p4)
        l4.setContentsMargins(0, 0, 0, 0)
        self.done_title = _label("", "h2", wrap=True)
        self.done_detail = _label("", "muted", wrap=True)
        l4.addStretch()
        l4.addWidget(self.done_title)
        l4.addWidget(self.done_detail)
        l4.addStretch()
        r4 = QHBoxLayout()
        self.undo_btn = _button(tr("Undo"), on_click=self.undo)
        r4.addWidget(self.undo_btn)
        r4.addStretch()
        r4.addWidget(_button(tr("Close"), "primary", self.accept))
        l4.addLayout(r4)
        self.stack.addWidget(p4)
        self.control = un.Control(self.cancel)
        self.removed_apps: list[un.App] = []
        self.no_ticks: set[int] = set()
        self._shown_list = 0  # counts the lists of leftovers shown (sizes worked out for an older one are dropped)
        self.running: list[tuple[int, str]] = []
        self.problems: list[tuple[un.App, un.Outcome]] = []
        self.started = 0.0
        self.ticker = QTimer(self)
        self.ticker.timeout.connect(self._tick)
        self._working_buttons()

    # ---- running

    def _emit(self, state: str, data=None):
        try:
            self.signals.state.emit(state, data)
        except RuntimeError:
            self.cancel.set()

    def start(self):
        self.busy = True
        self.stack.setCurrentIndex(1)
        quiet, point = self.quiet.isChecked(), self.restore_point.isChecked() and not self.restore_point.isHidden()
        apps, everything, forced = list(self.apps), list(self.everything), self.forced
        cancel, control = self.cancel, self.control
        command = _app_command()

        def work():
            if point:
                self._emit("restore_point")
                un.run_elevated(command, "restore-point",
                                {"description": tr("Before uninstalling {name}", name=apps[0].name)}, cancel)
            removed, problems = [], []
            for n, app in enumerate(apps):
                if cancel.is_set():
                    break
                if forced:
                    removed.append(app)
                    continue
                self._emit("running", (app, n, len(apps)))
                control.skip.clear()
                control.stop.clear()
                try:
                    outcome = un.run_uninstaller(app, quiet, control, lambda s: self._emit("progress", s))
                except Exception:
                    log.exception("uninstalling %s failed", app.name)
                    outcome = un.Outcome("not_started", 0)
                if outcome is None:  # the window was closed
                    break
                if outcome.result == "removed":
                    removed.append(app)
                    self.changed = True
                else:  # still installed: its files are left alone
                    problems.append((app, outcome))
            if cancel.is_set() or not removed:
                self._emit("stopped", problems)
                return
            self._emit("scanning", None)
            installed = [a for a in everything if a not in removed]
            found, no_ticks = [], set()
            for app in removed:
                try:
                    items = un.find_leftovers(app, installed, forced=forced or app.broken)
                    if forced and not app.broken:  # still installed: nothing is ticked for you
                        no_ticks |= {id(i) for i in items}
                    found += items
                except Exception:
                    log.exception("looking for leftovers of %s failed", app.name)
            try:
                running = un.programs_in([i.path for i in found if i.kind == "folder"])
            except Exception:
                log.debug("couldn't see what's running", exc_info=True)
                running = []
            self.no_ticks, self.running = no_ticks, running
            self._emit("found", (removed, found, problems))
        threading.Thread(target=work, daemon=True).start()

    def _skip_wait(self):
        self.control.skip.set()

    def _stop(self):
        self.cancel.set()
        self.reject()

    def _tick(self):
        secs = int(time.monotonic() - self.started)
        self.clock.setText(tr("Time so far: {time}", time=f"{secs // 60}:{secs % 60:02d}"))

    def _clock(self, on: bool):
        if on:
            self.started = time.monotonic()
            self._tick()
            self.ticker.start(1000)
        else:
            self.ticker.stop()
            self.clock.setText("")

    def _working_buttons(self, window: bool = False, stuck: bool = False, skip: bool = False):
        self.show_btn.setVisible(window)
        self.kill_btn.setVisible(stuck)
        self.skip.setVisible(skip)
        self.stuck_note.setVisible(stuck)

    def _state(self, state: str, data):
        if state == "restore_point":
            self.work_title.setText(tr("Making a restore point…"))
            self.work_detail.setText(tr("This can take a minute. If Windows asks for permission, click Yes."))
            self._working_buttons()
            self._clock(True)
        elif state == "running":
            app, n, total = data
            self.work_title.setText(tr("Uninstalling {name}…", name=app.name) +
                                    (f"  ({n + 1}/{total})" if total > 1 else ""))
            self.work_detail.setText(tr("Starting its uninstaller…"))
            self.bar.setRange(0, 0)
            self._working_buttons()
            self._clock(True)
        elif state == "progress":
            self._progress(data)
        elif state == "scanning":
            self._working_buttons()
            self._clock(False)
            self.work_title.setText(tr("Looking for leftovers…"))
            self.work_detail.setText(tr("Checking program folders, app data, shortcuts and the registry."))
        elif state == "stopped":
            self.busy = False
            self._clock(False)
            if data:
                title = tr("Nothing was removed") if len(data) > 1 else \
                    tr("{name} wasn't uninstalled", name=data[0][0].name)
                self._finish(title, _problems_text(data, len(data) > 1), undo=False)
            else:
                self._finish(tr("Nothing was removed"), tr("The uninstall was cancelled, so Rakiza Core didn't look "
                                                           "for leftovers."), undo=False)
        elif state == "found":
            removed, found, problems = data
            self.busy = False
            self._clock(False)
            self.leftovers = found
            self.problems = problems
            if not found:
                detail = tr("{name} left nothing behind.", name=removed[0].name) if len(removed) == 1 \
                    else tr("These apps left nothing behind.")
                if problems:
                    detail += "\n\n" + _problems_text(problems, True)
                self._finish(tr("All clean"), detail, undo=False)
                return
            self._show_leftovers(removed, found)
        elif state == "admin":
            self.work_detail.setText(tr("Some items need administrator permission. If Windows asks, click Yes."))
        elif state == "removing":
            n, total, item = data
            self.bar.setRange(0, total)
            self.bar.setValue(n)
            self.work_detail.setText(tr("{number} of {count}: {what}", number=n + 1, count=total,
                                        what=short_name(item.label(), 70)))
        elif state == "removed":
            self._removed(data)

    def _progress(self, state: str):
        """What the app's uninstaller is doing (see un.run_uninstaller)."""
        if state == "starting":
            self.work_detail.setText(tr("Starting its uninstaller… If Windows asks for permission, click Yes."))
            self._working_buttons()
        elif state == "window":
            self.work_detail.setText(tr("Its uninstaller window is open: follow its steps there. Rakiza Core waits "
                                        "until it's finished."))
            self._working_buttons(window=True, skip=True)
        elif state == "working":
            self.work_detail.setText(tr("Its uninstaller is working (it has no window to follow)…"))
            self._working_buttons(skip=True)
        elif state == "stuck":
            self.work_detail.setText(tr("Its uninstaller is still running, but it has no window."))
            self.stuck_note.setText(tr("It hasn't done anything for a minute, so it may be stuck (or waiting for "
                                       "something it can't show). You can keep waiting, or stop it."))
            self._working_buttons(stuck=True, skip=True)
        elif state == "cant_stop":
            self.stuck_note.setText(tr("Rakiza Core couldn't stop it, because it runs as administrator. Close it "
                                       "from Task Manager, or keep waiting."))
            self.stuck_note.show()
        elif state == "checking":
            self.work_detail.setText(tr("Its uninstaller has closed. Checking that the app is gone…"))
            self._working_buttons()

    # ---- choosing leftovers

    def _depth_changed(self, *_):
        depth = self.depth.currentData() or "normal"
        settings = getattr(self.c, "settings", None)
        if settings is not None and getattr(settings, "uninstall_depth", None) != depth:
            self.c.keep_now("uninstall_depth", depth)  # (kept now, never held back with Settings' unsaved changes)
            settings.save()
        if self.removed_apps:
            self._show_leftovers(self.removed_apps, self.leftovers)

    def _suggested(self, item: un.Leftover) -> bool:
        """Ticked to start with (and again with "Suggested")."""
        depth = self.depth.currentData() or "normal"
        return un.ticked_at(item, depth) and id(item) not in self.no_ticks

    def _sure_cell(self, row: QTreeWidgetItem, level: int):
        word, meaning = un.SURE.get(level, un.SURE[1])
        row.setText(1, tr(word))
        row.setToolTip(1, tr(meaning))
        colors = getattr(getattr(self.c, "theme", None), "tokens", None)
        if colors is not None:
            from PySide6.QtGui import QBrush
            row.setForeground(1, QBrush(colors.success if level >= 3 else colors.warning if level == 2
                                        else colors.danger))

    def _leftover_row(self, item: un.Leftover, ticked: bool) -> QTreeWidgetItem:
        """One leftover: a folder or a registry key opens to show what's in it, to keep some of that."""
        row = QTreeWidgetItem([item.label(), "", human_size(item.size) if item.size else ""])
        flags = row.flags() | Qt.ItemIsUserCheckable
        if item.kind in ("folder", "key"):
            flags |= Qt.ItemIsAutoTristate  # (ticked, unticked, or partly: some of what's inside)
            row.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        row.setFlags(flags)
        row.setCheckState(0, Qt.Checked if ticked else Qt.Unchecked)
        row.setData(0, APP_ROLE, item)
        row.setToolTip(0, item.label())
        self._sure_cell(row, item.level)
        return row

    def _show_leftovers(self, removed, found):
        self.removed_apps = removed
        self._shown_list += 1
        depth = self.depth.currentData() or "normal"
        self.depth_help.setText(tr(un.DEPTH_HELP[depth]))
        shown = [i for i in found if un.shown_at(i, depth)]
        self.left_tree.blockSignals(True)
        self.left_tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for item in shown:
            group = groups.get(item.reason)
            if group is None:
                group = QTreeWidgetItem([tr(un.REASONS.get(item.reason, item.reason)), "", ""])
                group.setFlags(group.flags() | Qt.ItemIsAutoTristate | Qt.ItemIsUserCheckable)
                f = group.font(0)
                f.setBold(True)
                group.setFont(0, f)
                self.left_tree.addTopLevelItem(group)
                groups[item.reason] = group
            group.addChild(self._leftover_row(item, self._suggested(item)))
        self.left_tree.expandToDepth(0)  # (the groups: what's inside a folder or key is shown when it's opened)
        self.left_tree.blockSignals(False)
        name = removed[0].name if len(removed) == 1 else tr("{count} apps", count=len(removed))
        self.left_title.setText(tr("{name} left {count} things behind", name=name, count=len(shown)))
        self.left_problems.setText(_problems_text(self.problems, True))
        self.left_problems.setVisible(bool(self.problems))
        names = sorted({os.path.basename(exe) for _, exe in self.running})
        self.close_running.setText(tr("Close what's still running from its folders first: {names}",
                                      names=", ".join(names[:5]) + ("…" if len(names) > 5 else "")))
        self.close_running.setVisible(bool(names))
        self.stack.setCurrentIndex(2)
        self._ticks_changed()

    def _fill_parts(self, row: QTreeWidgetItem):
        """A leftover folder or key was opened: what's right inside it, each ticked like it, to untick
        what should stay. (Listed once, when it's first opened.)"""
        item = row.data(0, APP_ROLE)
        if not isinstance(item, un.Leftover) or item.kind not in ("folder", "key") or row.data(0, FILLED_ROLE):
            return
        row.setData(0, FILLED_ROLE, True)
        try:
            parts = un.children_of(item)
        except Exception:
            log.debug("couldn't list what's in %s", item.path, exc_info=True)
            parts = []
        state = row.checkState(0)
        self.left_tree.blockSignals(True)
        rows = []
        for part in parts[:PARTS_SHOWN]:
            child = QTreeWidgetItem([_part_text(part), "", human_size(part.size) if part.size else ""])
            flags = child.flags() | Qt.ItemIsUserCheckable
            if part.kind in ("folder", "key"):
                flags |= Qt.ItemIsAutoTristate
                child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            child.setFlags(flags)
            child.setCheckState(0, state)  # (before it's added: an unticked one would untick the folder)
            child.setData(0, APP_ROLE, part)
            child.setToolTip(0, part.label())
            row.addChild(child)
            rows.append((child, part))
        rest = parts[PARTS_SHOWN:]
        if rest:  # one row for all the rest: ticked, they go; unticked, they stay
            more = QTreeWidgetItem([tr("{count} more items", count=len(rest)), "",
                                    human_size(sum(p.size for p in rest)) if any(p.size for p in rest) else ""])
            more.setFlags(more.flags() | Qt.ItemIsUserCheckable)
            more.setCheckState(0, state)
            more.setData(0, APP_ROLE, rest)
            row.addChild(more)
        if not parts:
            row.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
        self.left_tree.blockSignals(False)
        self._ticks_changed()
        folders = [(child, part) for child, part in rows if part.kind == "folder"]
        if folders:
            self._measure(folders)

    def _measure(self, rows):
        """The sizes of the folders just listed, worked out away from the window's thread."""
        from . import work
        shown, paths = self._shown_list, [part.path for _, part in rows]

        def measure(_progress):
            sizes, end = [], time.monotonic() + 15  # (at most 15 s for them all: the rest show no size)
            for path in paths:
                sizes.append(un.folder_size(Path(path), limit_s=1) if time.monotonic() < end else 0)
            return sizes

        def measured(sizes):
            if not work.alive(self) or shown != self._shown_list or isinstance(sizes, Exception):
                return
            self.left_tree.blockSignals(True)
            for (child, part), size in zip(rows, sizes):
                part.size = size
                child.setText(2, human_size(size) if size else "")
            self.left_tree.blockSignals(False)
            self._ticks_changed()
        # (kept while the sheet is open: its answer comes through the job's own signals)
        self._sizing = getattr(self, "_sizing", [])
        self._sizing.append(work.run(measure, lambda _x: None, measured))

    def _chosen(self) -> list[un.Leftover]:
        """What's ticked: a whole leftover, or (when only some of what's inside it is ticked) those parts."""
        out: list[un.Leftover] = []

        def walk(node: QTreeWidgetItem):
            for c in range(node.childCount()):
                row = node.child(c)
                data, state = row.data(0, APP_ROLE), row.checkState(0)
                if data is None or state == Qt.PartiallyChecked:  # a group, or a folder or key partly ticked
                    walk(row)
                elif state == Qt.Checked:
                    out.extend(data if isinstance(data, list) else [data])
        walk(self.left_tree.invisibleRootItem())
        return out

    def _tick_all(self, on: bool):
        for g in range(self.left_tree.topLevelItemCount()):
            self.left_tree.topLevelItem(g).setCheckState(0, Qt.Checked if on else Qt.Unchecked)

    def _tick_suggested(self):
        self.left_tree.blockSignals(True)
        for g in range(self.left_tree.topLevelItemCount()):
            group = self.left_tree.topLevelItem(g)
            for c in range(group.childCount()):
                row = group.child(c)
                row.setCheckState(0, Qt.Checked if self._suggested(row.data(0, APP_ROLE)) else Qt.Unchecked)
        self.left_tree.blockSignals(False)
        self._ticks_changed()

    def _left_menu(self, pos):
        """Right-click on a leftover: see it where it is before choosing."""
        row = self.left_tree.itemAt(pos)
        item = row.data(0, APP_ROLE) if row is not None else None
        if not isinstance(item, un.Leftover):
            return
        from PySide6.QtGui import QGuiApplication
        from .theme import round_menu
        from .winutil import reveal_in_explorer
        m = QMenu(self)
        round_menu(m)
        if item.kind in ("folder", "file", "shortcut"):
            m.addAction(tr("Show in folder"), lambda: reveal_in_explorer(Path(item.path)))
            path = item.path
        elif item.is_registry:
            m.addAction(tr("Open in Registry Editor"), lambda: un.open_in_regedit(item.path, item.view))
            path = un.registry_place(item.path, item.view)
        else:  # a scheduled task
            path = item.label()
        m.addAction(tr("Copy the path"), lambda: QGuiApplication.clipboard().setText(path))
        if item.kind in ("folder", "key") and row.childIndicatorPolicy() == QTreeWidgetItem.ShowIndicator:
            m.addSeparator()
            m.addAction(tr("Hide what's inside") if row.isExpanded() else tr("Show what's inside"),
                        lambda: row.setExpanded(not row.isExpanded()))
        sheet.menu(m, self.left_tree.viewport().mapToGlobal(pos))
        sheet.forget(m)

    def _ticks_changed(self, *_):
        chosen = self._chosen()
        size = sum(i.size for i in chosen)
        self.left_info.setText(tr("{count} selected · {size}", count=len(chosen), size=human_size(size)))
        self.remove_btn.setEnabled(bool(chosen))

    def _keep(self):
        self.accept()

    def remove_selected(self):
        chosen = self._chosen()
        if not chosen:
            return
        self.busy = True
        self.stack.setCurrentIndex(1)
        self._working_buttons()
        self.stop.setEnabled(False)
        self.work_title.setText(tr("Removing leftovers…"))
        self.work_detail.setText(tr("Making a backup of everything first."))
        self._clock(True)
        apps = sorted({a.name for a in self.apps})
        command = _app_command()
        close = self.close_running.isVisible() and self.close_running.isChecked()

        self._hold(True)

        def work():
            folder = None
            try:
                folder = un.new_backup(apps)
                result = un.remove(chosen, folder, apps, on_item=lambda n, total, item: self._emit(
                    "removing", (n, total, item)), close_running=close)
                failed = un.items_from(result["failed"])
                needs_admin = [i for i in failed if i.needs_admin]
                if needs_admin:
                    self._emit("admin", None)
                    more = un.run_elevated(command, "cleanup", {"items": result["failed"], "folder": str(folder),
                                                                "apps": apps, "close": close})
                    if more and "done" in more:
                        result["done"] += more["done"]
                        result["freed"] += more.get("freed", 0)
                        result["failed"] = more.get("failed", [])
            except Exception:  # never leave the window stuck on "Removing…"
                log.exception("removing leftovers failed")
                result = {"done": [], "failed": [dict(i.__dict__) for i in chosen], "freed": 0}
            self._emit("removed", (folder, result))
        threading.Thread(target=work, daemon=True).start()

    def _hold(self, on: bool):
        """Tells Rakiza Core not to quit or update itself while files are being moved."""
        if self.c is not None and on != getattr(self, "_holding", False):
            self.c.uninstall_busy = max(0, getattr(self.c, "uninstall_busy", 0) + (1 if on else -1))
            self._holding = on

    def _removed(self, data):
        self._hold(False)
        self.busy = False
        self.stop.setEnabled(True)
        self._clock(False)
        folder, result = data
        # (files only scheduled for deletion at restart count too: undoing takes that back)
        self.backup = folder if folder is not None and any(d.get("stored") or d.get("pending")
                                                           for d in result["done"]) else None
        self.changed = True
        # (a folder taken in two goes, partly, then the rest as administrator, is one leftover)
        done = len({(d.get("kind"), str(d.get("path", "")).lower(), d.get("value")) for d in result["done"]})
        failed = len(result["failed"])
        detail = tr("{size} freed. Everything is kept as a backup for {days} days: undo it here or from "
                    "\"Backups…\" on the Apps page.", size=human_size(result["freed"]), days=_backup_days(self.c))
        later = sum(int(d.get("on_restart") or 0) for d in result["done"])
        if later:
            detail += "\n" + tr("{count} files were still in use: Windows removes them when you restart the PC.",
                                count=later)
        if failed:
            detail += "\n" + tr("{count} items couldn't be removed (in use, or administrator permission was "
                                "refused). Restarting the PC and trying again usually helps.", count=failed)
        self._finish(tr("Removed {count} leftovers", count=done), detail, undo=self.backup is not None)
        if self.c is not None:
            self.c.play_done_sound()

    def _finish(self, title: str, detail: str, undo: bool):
        self.done_title.setText(title)
        self.done_detail.setText(detail)
        self.undo_btn.setVisible(undo)
        self.stack.setCurrentIndex(3)

    def undo(self):
        if self.backup is None or getattr(self, "_undoing", None) is not None:
            return
        from . import work
        backup = self.backup
        self.undo_btn.setEnabled(False)
        # (off the window's thread: putting a big folder back, or waiting for the administrator copy, takes a while)
        self._undoing = work.run(lambda _p: restore_backup(backup), lambda _: None, self._undone, busy=True)

    def _undone(self, result):
        from . import work
        if not work.alive(self):
            return
        self._undoing = None
        self.undo_btn.setEnabled(True)
        if isinstance(result, Exception):
            log.warning("putting back %s failed", self.backup, exc_info=result)
            self.done_detail.setText(str(result))
            return
        if result["failed"]:
            self.done_detail.setText(tr("{count} items couldn't be put back.", count=len(result["failed"])))
        else:
            self.done_title.setText(tr("Put back"))
            self.done_detail.setText(tr("Everything that was removed is back where it was. (The app itself stays "
                                        "uninstalled: install it again to use it.)"))
        if result.get("recycled"):
            self.done_detail.setText(self.done_detail.text() + "\n" + _recycled_text(result["recycled"]))
        self.undo_btn.hide()

    def reject(self):
        if self.busy and self.stack.currentIndex() == 1 and not self.stop.isEnabled():
            return  # removing: don't close half-way
        self.cancel.set()
        super().reject()

    def closeEvent(self, e):
        if self.busy and not self.stop.isEnabled():
            e.ignore()
            return
        self.cancel.set()
        super().closeEvent(e)


def _part_text(item: un.Leftover) -> str:
    """A part of a leftover folder or key, under it: just its own name."""
    if item.kind == "value":
        return item.value
    return item.path.rstrip("\\/").replace("/", "\\").rsplit("\\", 1)[-1] or item.path


def _problems_text(problems: list[tuple[un.App, un.Outcome]], names: bool) -> str:
    """One line for each app that is still installed, with why (and its name, when there were several)."""
    lines = []
    for app, outcome in problems:
        why = tr(un.OUTCOMES.get(outcome.result, un.OUTCOMES["still"]), code=outcome.code, error=outcome.error)
        lines.append(f"{ltr(app.name)}: {why}" if names else why)
    return "\n".join(lines)


def _app_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return [sys.executable, str(main_py)]


def _recycled_text(count: int) -> str:
    return tr("{count} items from another drive went to the Recycle Bin: put them back from there.", count=count)


def restore_backup(folder: Path) -> dict:
    result = un.restore(folder)
    if result["failed"]:
        more = un.run_elevated(_app_command(), "restore", {"folder": str(folder)})
        if more and "failed" in more:
            result = {"done": result["done"] + more.get("done", 0), "failed": more["failed"],
                      "recycled": result.get("recycled", 0)}
    return result


# ----------------------------------------------------------------------------- backups

class OrphansDialog(UninstallDialog):
    """Program folders no installed app uses any more: looked for → choose → moved into a backup (with undo),
    the same way an app's leftovers go."""

    def __init__(self, controller, everything: list[un.App], icon: QIcon):
        super().__init__(controller, [], everything, True, icon)
        # (the backup is named after it, and Backups… shows it under this name)
        self.apps = [un.App(id="orphans", name=tr("Folders of apps that are gone"))]
        self.setWindowTitle(tr("Folders of apps that are gone"))
        self.confirm_title.setText(tr("Look for folders of apps that are gone?"))
        self.confirm_note.setText(tr("Rakiza Core looks in Program Files for folders that no installed app, service, "
                                     "scheduled task, shortcut or start-up entry uses, named after no installed "
                                     "app or maker, and unchanged for two months: what an app deleted by hand or a "
                                     "broken uninstall left behind. Nothing is ticked, and you choose what goes."))
        self.go.setText(tr("Look for them"))
        self.left_note.setText(tr("Nothing uses these any more. A portable app or a game that a launcher installed "
                                  "can look the same, so check each one first. Everything removed is kept as a "
                                  "backup, and can be put back from Backups."))
        for w in (self.depth_label, self.depth, self.depth_help):
            w.hide()

    def start(self):
        self.busy = True
        self.stack.setCurrentIndex(1)
        everything, cancel, me = [a for a in self.everything if not a.store], self.cancel, self.apps
        self._emit("scanning", None)

        def work():
            try:
                found = un.orphan_folders(everything, cancel)
            except Exception:
                log.exception("looking for folders of apps that are gone failed")
                found = []
            if cancel.is_set():
                return
            self._emit("found", (me, found, []))
        threading.Thread(target=work, daemon=True).start()

    def _suggested(self, item: un.Leftover) -> bool:
        return False  # (nothing is ticked for you here: see the note)

    def _state(self, state: str, data):
        if state == "scanning":
            super()._state(state, data)
            self.work_detail.setText(tr("Checking Program Files against every app, service, task, shortcut and "
                                        "start-up entry."))
        elif state == "found" and not data[1]:
            self.busy = False
            self._clock(False)
            self._finish(tr("All clean"), tr("No program folders are left from apps that are gone."), undo=False)
        else:
            super()._state(state, data)

    def _show_leftovers(self, removed, found):
        self.removed_apps = removed
        self.left_tree.blockSignals(True)
        self.left_tree.clear()
        self._shown_list += 1
        group = QTreeWidgetItem([tr(un.REASONS["orphan"]), "", ""])
        group.setFlags(group.flags() | Qt.ItemIsAutoTristate | Qt.ItemIsUserCheckable)
        f = group.font(0)
        f.setBold(True)
        group.setFont(0, f)
        self.left_tree.addTopLevelItem(group)
        for item in found:
            group.addChild(self._leftover_row(item, False))
        self.left_tree.expandToDepth(0)
        self.left_tree.blockSignals(False)
        self.left_title.setText(tr("{count} program folders no installed app uses", count=len(found)))
        self.left_problems.setVisible(False)
        self.close_running.setVisible(False)
        self.stack.setCurrentIndex(2)
        self._ticks_changed()


class BackupsDialog(QDialog):
    def __init__(self, controller, icon: QIcon):
        super().__init__(None, Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.c = controller
        self.changed = False
        self.setWindowIcon(icon)
        self.setWindowTitle(tr("Backups"))
        self.setMinimumSize(560, 380)
        self._job = None  # (putting back or deleting, off the window's thread)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.addWidget(_label(tr("Backups"), "h2"))
        lay.addWidget(_label(tr("What Rakiza Core removed after uninstalling apps. Put it back, or delete the backup to "
                                "free the space. Backups are deleted by themselves after {days} days.",
                                days=_backup_days(self.c)), "muted", wrap=True))
        self.tree = QTreeWidget()
        self.tree.setObjectName("apps")
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels([tr("Apps"), tr("When"), tr("Items")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.itemSelectionChanged.connect(self._selection)
        lay.addWidget(self.tree, 1)
        self.empty = _label(tr("No backups."), "muted")
        lay.addWidget(self.empty)
        row = QHBoxLayout()
        self.b_restore = _button(tr("Put back"), "primary", self.restore)
        self.b_delete = _button(tr("Delete backup"), "danger", self.delete)
        row.addWidget(self.b_restore)
        row.addWidget(self.b_delete)
        row.addStretch()
        row.addWidget(_button(tr("Close"), on_click=self.accept))
        lay.addLayout(row)
        self.fill()

    def fill(self):
        self.tree.clear()
        backups = un.list_backups()
        for folder, data in backups:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(data.get("time", 0)))
            item = QTreeWidgetItem([", ".join(data.get("apps", [])) or folder.name, when,
                                    str(len(data.get("items", [])))])
            item.setData(0, APP_ROLE, folder)
            item.setToolTip(0, "\n".join(i.get("path", "") for i in data.get("items", [])[:25]))
            self.tree.addTopLevelItem(item)
        self.empty.setVisible(not backups)
        self._selection()

    def _folder(self) -> Path | None:
        items = self.tree.selectedItems()
        return items[0].data(0, APP_ROLE) if items else None

    def _selection(self):
        has = self._folder() is not None and self._job is None
        self.b_restore.setEnabled(has)
        self.b_delete.setEnabled(has)

    def _run(self, fn, done):
        """fn() off the window's thread (a backup can be big, and putting it back may wait for the
        administrator copy), then done(its result) here."""
        from . import work

        def finished(result):
            if not work.alive(self):
                return
            self._job = None
            if isinstance(result, Exception):
                log.warning("backup job failed", exc_info=result)
                sheet.warning(self, tr("Backups"), str(result))
            else:
                done(result)
            self.fill()
        self._job = work.run(lambda _p: fn(), lambda _: None, finished, busy=True)
        self._selection()

    def restore(self):
        folder = self._folder()
        if folder is None or self._job is not None:
            return
        self.changed = True

        def done(result):
            if result["failed"]:
                sheet.warning(self, tr("Backups"), tr("{count} items couldn't be put back.",
                                                      count=len(result["failed"])))
            elif result.get("recycled"):
                sheet.information(self, tr("Backups"), _recycled_text(result["recycled"]))
        self._run(lambda: restore_backup(folder), done)

    def delete(self):
        folder = self._folder()
        if folder is None or self._job is not None:
            return
        if sheet.question(self, tr("Backups"), tr("Delete this backup for good? It can't be put back after "
                                                 "that.")) != QMessageBox.Yes:
            return

        def done(ok):
            if not ok:
                sheet.warning(self, tr("Backups"), tr("Some files in the backup couldn't be deleted."))
        self._run(lambda: un.delete_backup(folder), done)


class UpdatesDialog(QDialog):
    """Newer versions of the installed apps (Windows' own winget finds them), updated one by one."""

    def __init__(self, icon: QIcon):
        super().__init__(None, Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setWindowIcon(icon)
        self.setWindowTitle(tr("App updates"))
        self.setMinimumSize(640, 420)
        self.changed = False
        self.busy = False
        self.stop = threading.Event()
        self.jobs: list = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.addWidget(_label(tr("App updates"), "h2"))
        lay.addWidget(_label(tr("Newer versions of your apps, found by Windows' own package manager. Each one is "
                                "downloaded from where its maker publishes it, and installed quietly where the app "
                                "allows it."), "muted", wrap=True))
        self.tree = QTreeWidget()
        self.tree.setObjectName("apps")
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels([tr("App"), tr("Installed"), tr("New version"), ""])
        h = self.tree.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3):
            h.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.tree.itemChanged.connect(lambda *_: self._selection())
        lay.addWidget(self.tree, 1)
        self.status = _label("", "muted", wrap=True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.b_update = _button(tr("Update ticked"), "primary", self.update_ticked)
        self.b_store = _button(tr("Get it from the Microsoft Store"), on_click=self._store)
        self.b_store.hide()
        self.b_again = _button(tr("Check again"), on_click=self.check)
        row.addWidget(self.b_update)
        row.addWidget(self.b_store)
        row.addWidget(self.b_again)
        row.addStretch()
        self.b_close = _button(tr("Close"), on_click=self.reject)
        row.addWidget(self.b_close)
        lay.addLayout(row)
        self.check()

    def _run(self, fn, done, progress=lambda _x: None):
        from . import work
        self.jobs = [j for j in self.jobs if j.is_alive()]
        self.jobs.append(work.run(fn, progress, done, name="corefold-app-updates"))

    def _busy(self, on: bool, text: str = ""):
        self.busy = on
        self.b_again.setEnabled(not on)
        if text:
            self.status.setText(text)
        self._selection()

    def check(self):
        if self.busy:
            return
        from . import appupdates
        self.tree.clear()
        self.b_store.hide()
        self._busy(True, tr("Looking for newer versions… (this can take a minute)"))
        self._run(lambda _p: appupdates.check(), self._checked)

    def _checked(self, result):
        from . import work
        if not work.alive(self):
            return
        self._busy(False)
        if isinstance(result, FileNotFoundError):
            self.status.setText(tr("Windows' package manager (winget) isn't on this PC. It comes with "
                                   "“App Installer” from the Microsoft Store."))
            self.b_store.show()
            return
        if isinstance(result, Exception):
            log.warning("couldn't check for app updates: %s", result)
            self.status.setText(tr("Couldn't look for updates: {error}", error=str(result) or type(result).__name__))
            return
        self.fill(result)

    def fill(self, updates):
        self.tree.blockSignals(True)
        self.tree.clear()
        for u in updates:
            item = QTreeWidgetItem([u.name, ltr(u.version), ltr(u.available), ""])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked)
            item.setData(0, APP_ROLE, u)
            item.setToolTip(0, u.id)
            self.tree.addTopLevelItem(item)
        self.tree.blockSignals(False)
        n = len(updates)
        self.status.setText(tr("All your apps are up to date.") if not n else tr("1 app has a newer version.")
                            if n == 1 else tr("{count} apps have a newer version.", count=n))
        self._selection()

    def _ticked(self) -> list[QTreeWidgetItem]:
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())
                if self.tree.topLevelItem(i).checkState(0) == Qt.Checked
                and self.tree.topLevelItem(i).text(3) != tr("Updated")]

    def _selection(self):
        self.b_update.setEnabled(not self.busy and bool(self._ticked()))
        self.b_update.setVisible(self.tree.topLevelItemCount() > 0)

    def update_ticked(self):
        items = self._ticked()
        if self.busy or not items:
            return
        from . import appupdates
        todo = [(i, item.data(0, APP_ROLE)) for i, item in enumerate(items)]
        self.stop.clear()
        self._busy(True)
        self.b_close.setText(tr("Stop after this one"))

        def go(progress):
            done = []
            for n, (i, u) in enumerate(todo):
                if self.stop.is_set():
                    break
                progress(("start", i, u, n, len(todo)))
                try:
                    ok, said = appupdates.update(u)
                except Exception as e:
                    from .converters import friendly_error
                    ok, said = False, friendly_error(e)
                progress(("done", i, u, ok, said))
                done.append(ok)
            return done

        def step(info):
            if not work_alive():
                return
            if info[0] == "start":
                _, i, u, n, total = info
                items[i].setText(3, tr("Updating…"))
                self.status.setText(tr("Updating {name} ({n} of {total})…", name=u.name, n=n + 1, total=total))
            else:
                _, i, u, ok, said = info
                items[i].setText(3, tr("Updated") if ok else tr("Couldn't update"))
                if not ok and said:
                    items[i].setToolTip(3, said)
                self.changed = self.changed or ok

        def work_alive():
            from . import work
            return work.alive(self)
        self._run(go, self._updated, step)

    def _updated(self, result):
        from . import work
        if not work.alive(self):
            return
        self._busy(False)
        self.b_close.setText(tr("Close"))
        if isinstance(result, Exception):
            self.status.setText(tr("Couldn't update: {error}", error=str(result)))
            return
        ok = sum(1 for x in result if x)
        failed = len(result) - ok
        said = tr("Updated 1 app.") if ok == 1 else tr("Updated {count} apps.", count=ok)
        if failed:
            said += " " + tr("{failed} couldn't be updated: point at them to see why.", failed=failed)
        self.status.setText(said)

    def _store(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from . import appupdates
        QDesktopServices.openUrl(QUrl(appupdates.STORE_PAGE))

    def reject(self):
        if self.busy:
            self.stop.set()  # (the one being installed is never cut off halfway)
            self.status.setText(tr("Stopping after this one…"))
            return
        super().reject()


class WatchInstallDialog(QDialog):
    """Watching an install: a look before, the person installs the app, a look after. What's new is kept, and
    offered with the app's leftovers when it's uninstalled one day."""

    def __init__(self, icon: QIcon):
        from . import installtrack
        super().__init__(None, Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setWindowIcon(icon)
        self.setWindowTitle(tr("Watch an install"))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        lay.addWidget(_label(tr("Watch an install"), "h2"))
        lay.addWidget(_label(tr("Rakiza Core looks at where apps put things before you install one, and again once it's "
                                "installed. When you uninstall that app one day, everything it put in is offered "
                                "too, even what isn't named after it."), "muted", wrap=True))
        self.status = _label("", wrap=True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.b_start = _button(tr("Start watching"), "primary", self.begin)
        self.b_open = _button(tr("Open an installer…"), on_click=self.open_installer)
        self.b_done = _button(tr("I've finished installing"), "primary", self.end)
        self.b_cancel = _button(tr("Stop watching"), on_click=self.stop_watching)
        for b in (self.b_start, self.b_open, self.b_done, self.b_cancel):
            row.addWidget(b)
        row.addStretch()
        self.b_close = _button(tr("Close"), on_click=self.accept)
        row.addWidget(self.b_close)
        lay.addLayout(row)
        self._show(waiting=bool(installtrack.waiting()))

    def _show(self, waiting: bool, text: str = ""):
        self.b_start.setVisible(not waiting)
        for b in (self.b_open, self.b_done, self.b_cancel):
            b.setVisible(waiting)
        if text:
            self.status.setText(text)
        elif waiting:
            self.status.setText(tr("Watching. Install the app now (from here or anywhere), then press I've finished "
                                   "installing. You can close this and come back: Rakiza Core keeps watching."))
        else:
            self.status.setText(tr("Press Start watching before you open the app's installer."))

    def _busy(self, text: str):
        for b in (self.b_start, self.b_open, self.b_done, self.b_cancel):
            b.setEnabled(False)
        self.status.setText(text)

    def _ready(self):
        for b in (self.b_start, self.b_open, self.b_done, self.b_cancel):
            b.setEnabled(True)

    def begin(self):
        from . import installtrack, work
        self._busy(tr("Looking at where apps put things…"))

        def done(result):
            if not work.alive(self):
                return
            self._ready()
            if isinstance(result, Exception):
                self._show(False, tr("Couldn't look: {error}", error=str(result)))
            else:
                self._show(True)
        work.run(lambda _p: installtrack.start(), lambda _: None, done, name="corefold-watch-install")

    def open_installer(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, tr("Open an installer"), str(Path.home() / "Downloads"),
                                              tr("Installers") + " (*.exe *.msi)")
        if path:
            try:
                os.startfile(path)  # (Windows asks for an administrator when the installer needs one)
            except (OSError, AttributeError) as e:
                self.status.setText(tr("Couldn't open it: {error}", error=str(e)))

    def end(self):
        from . import installtrack, work
        self._busy(tr("Looking at what changed…"))

        def name_of(hive, view, key):
            return str(un._values(hive, un.UNINSTALL + "\\" + key, view).get("DisplayName") or "")

        def done(result):
            if not work.alive(self):
                return
            self._ready()
            if isinstance(result, Exception):
                self._show(True, tr("Couldn't look: {error}", error=str(result)))
            elif not result or not result.get("items"):
                self._show(False, tr("Nothing new was found. Was the app installed while Rakiza Core watched?"))
            elif result.get("apps"):
                self._show(False, tr("Done. Rakiza Core saw {name} put in {count} things, and offers them all when you "
                                     "uninstall it.", name=", ".join(result["apps"]), count=len(result["items"])))
            else:
                self._show(False, tr("Done. {count} new things were seen, but no new app appeared in Windows' list, "
                                     "so they can't be offered with one.", count=len(result["items"])))
        work.run(lambda _p: installtrack.finish(name_of), lambda _: None, done, name="corefold-watch-install")

    def stop_watching(self):
        from . import installtrack
        installtrack.cancel()
        self._show(False)


class ExtensionsDialog(QDialog):
    """The browsers' extensions: each one is removed in its browser, which is opened on it."""

    def __init__(self, icon: QIcon):
        super().__init__(None, Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setWindowIcon(icon)
        self.setWindowTitle(tr("Browser extensions"))
        self.setMinimumSize(640, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.addWidget(_label(tr("Browser extensions"), "h2"))
        lay.addWidget(_label(tr("What's been added to your browsers. Ones that see every website can read what you "
                                "type on them, so keep only the ones you know."), "muted", wrap=True))
        self.tree = QTreeWidget()
        self.tree.setObjectName("apps")
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.setHeaderLabels([tr("Extension"), tr("Browser"), tr("Sees every website"), tr("Size")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.itemSelectionChanged.connect(self._selection)
        self.tree.itemDoubleClicked.connect(lambda *_: self.remove())
        lay.addWidget(self.tree, 1)
        self.status = _label("", "muted", wrap=True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.b_remove = _button(tr("Remove in the browser"), "primary", self.remove)
        self.b_remove.setToolTip(tr("Opens the browser on it: press Remove there. (A browser puts back an "
                                    "extension deleted behind its back.)"))
        row.addWidget(self.b_remove)
        self.b_unforce = _button(tr("Stop a program forcing it"), on_click=self.unforce)
        self.b_unforce.setToolTip(tr("For an extension a program put in through Windows' registry (the browser "
                                     "says it's installed by your administrator, or puts it back each time): "
                                     "removes that entry, and the browser takes the extension out."))
        row.addWidget(self.b_unforce)
        row.addWidget(_button(tr("Refresh"), on_click=self.fill))
        row.addStretch()
        row.addWidget(_button(tr("Close"), on_click=self.accept))
        lay.addLayout(row)
        self.fill()

    def fill(self):
        from . import extensions
        self.tree.clear()
        found = extensions.find()
        try:
            forced = extensions.find_forced()
        except Exception:
            log.debug("couldn't read the forced extensions", exc_info=True)
            forced = []
        self.forced = forced
        # the profile is only named for a browser with several of them
        several = {b for b in {e.browser for e in found} if len({e.profile_dir for e in found if e.browser == b}) > 1}
        for ext in found:
            where = f"{ext.browser} ({ext.profile})" if ext.browser in several and ext.profile else ext.browser
            name = ext.name + ("" if ext.on else "  " + tr("(turned off)"))
            mine = [f for f in forced if f.id == ext.id and f.browser == ext.browser]
            if mine:
                name += "  " + tr("(forced by a program)")
            item = SortItem([name, where, tr("Yes") if ext.all_sites else "", human_size(ext.size) if ext.size else ""])
            item.setData(0, APP_ROLE, ext)
            item.setData(1, APP_ROLE, mine)
            item.setData(3, SORT_ROLE, ext.size)
            item.setToolTip(0, f"{ext.name} {ext.version}\n{ext.id}")
            self.tree.addTopLevelItem(item)
        shown = {(e.browser, e.id) for e in found}
        for f in forced:  # (forced, but not in a profile yet: the browser puts it in when it next starts)
            if (f.browser, f.id) in shown and f.id:
                continue
            shown.add((f.browser, f.id))
            item = SortItem([(f.id or f.data) + "  " + tr("(forced by a program)"), f.browser, "", ""])
            item.setData(0, APP_ROLE, None)
            item.setData(1, APP_ROLE, [x for x in forced if (x.browser, x.id) == (f.browser, f.id)])
            item.setToolTip(0, f"{f.hive}\\{f.key}" + (f"\n{f.by}" if f.by else ""))
            self.tree.addTopLevelItem(item)
        self.tree.sortItems(1, Qt.AscendingOrder)
        self.status.setText(tr("{count} extensions, {sites} of them see every website.", count=len(found),
                               sites=sum(1 for e in found if e.all_sites)) if found else
                            tr("No browser extensions found."))
        self._selection()

    def _ext(self):
        items = self.tree.selectedItems()
        return items[0].data(0, APP_ROLE) if items else None

    def _forced(self) -> list:
        items = self.tree.selectedItems()
        return list(items[0].data(1, APP_ROLE) or []) if items else []

    def _selection(self):
        self.b_remove.setEnabled(self._ext() is not None and not self._forced())
        self.b_unforce.setEnabled(bool(self._forced()))

    def unforce(self):
        """Removes the registry entries a program put in to force this extension (asks first)."""
        from . import extensions, work
        items = self._forced()
        if not items:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("Stop a program forcing it?"))
        box.setText(tr("Rakiza Core removes the entry that forces this extension into {browser}. Close the browser and "
                       "open it again: it takes the extension out by itself.", browser=items[0].browser))
        if any(f.needs_admin for f in items):
            box.setInformativeText(tr("Windows asks for an administrator."))
        yes = box.addButton(tr("Remove the entry"), QMessageBox.AcceptRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        sheet.run(box)
        if box.clickedButton() is not yes:
            return
        command = _app_command()

        def go(_progress):
            return extensions.remove_forced(items, lambda payload: un.run_elevated(command, "browser-ext", payload))

        def done(result):
            if not work.alive(self):
                return
            self.b_unforce.setEnabled(True)
            if isinstance(result, Exception):
                self.status.setText(tr("Couldn't remove it: {error}", error=str(result)))
                return
            removed, failed = result
            self.fill()
            self.status.setText(tr("Done. Close {browser} and open it again to see the extension go.",
                                   browser=items[0].browser) if removed and not failed else
                                tr("Windows didn't allow it, or the administrator question was answered no."))
        self.b_unforce.setEnabled(False)
        work.run(go, lambda _: None, done, name="corefold-unforce")

    def remove(self):
        from . import extensions
        ext = self._ext()
        if ext is None:
            return
        if extensions.open_page(ext):
            self.status.setText(tr("{browser} is open on {name}: press Remove there, then Refresh here.",
                                   browser=ext.browser, name=ext.name))
        else:
            self.status.setText(tr("{browser} wasn't found on this PC.", browser=ext.browser))
