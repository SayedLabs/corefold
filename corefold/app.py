"""
Rakiza Core - everyday tools for Windows (Converter, Uninstaller, Archive).

Runs in the system tray. Hold Shift while dragging a file in File Explorer or on the
desktop (or right-click it → Rakiza Core) and a wheel of formats appears at the cursor;
pick one to convert.

Kept light on purpose: the main window is only built when it's opened and is freed again a
while after it's closed, conversions run at low priority one at a time, and memory that isn't
needed any more is handed back to Windows when Rakiza Core is idle.
"""
from __future__ import annotations

import copy
import gc
import itertools
import logging
import logging.handlers
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication, QIcon, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import UPDATE_REPO, __version__, i18n, icons, ipc, sheet, shellmenu, sounds, updater, winutil
from . import work as page_work
from . import tools as all_tools
from . import settings as settings_mod
from .converters import PASSWORD_ERROR, Options, Result, Tool, pick, release_memory, run_job, warm_up
from .core import (KEEP_ID, LEGACY_NAME, NO_WINDOW, OLD_APP_NAMES, category, fallback_folder, human_size,
                   is_link, lower_this_thread, norm, os_open, set_background_priority, set_downloads_folder,
                   short_name, system_tool, unique_path)
from .history import History, size_of
from .i18n import tr
from .settings import (APP_NAME, LOG_DIR, TOAST_TIME_SCALE, WINDOW_TEXT_PT, Settings, app_command, app_icon,
                       installed_uninstaller, remove_user_data)
from .atomic import tokens as atomic_tokens
from .theme import build_theme, round_menu, stylesheet
from .toast import Toast
from .wheel import Wheel

class _OnFirstUse:
    """Stands in for one of our modules and only loads it when it's first used, so the copy that
    waits in the tray doesn't carry windows and tools nobody has opened yet."""

    def __init__(self, name: str):
        object.__setattr__(self, "_name", name)

    def _module(self):
        import importlib
        return importlib.import_module(f"{__package__}.{self._name}")

    def __getattr__(self, attr):
        return getattr(self._module(), attr)

    def __setattr__(self, attr, value):
        setattr(self._module(), attr, value)


archives = _OnFirstUse("archives")  # opening and listing archives
dialogs = _OnFirstUse("dialogs")    # the "Resize…", "Password…" windows

APP_ID = f"AtomicWorks.{KEEP_ID}"  # (the installer gives its shortcuts the same id)
# A key-drag only opens the wheel if it starts in one of these windows:
# File Explorer and the desktop. ("#32770" would add Open/Save dialogs.)
SOURCE_WINDOW_CLASSES = {"CabinetWClass", "ExploreWClass", "Progman", "WorkerW"}
DRAG_THRESHOLD_PX = 14
UPDATE_JOB_ID = -1  # the update download shows in the same popup as conversions
UPDATE_CHECK_EVERY_MS = 6 * 3600 * 1000
UPDATE_RETRY_MS = (2, 10, 30, 60, 180)  # minutes: an automatic check that failed (offline) is tried again soon
UPDATE_RETELL_MS = 30 * 60_000  # full screen when a new version was found: say it this much later
LIGHT_TIDY_MS = 4_000          # memory a finished job used goes back this soon
IDLE_TIDY_MS = 45_000          # hand memory back to Windows this long after the last job
FREE_WINDOW_AFTER_MS = 90_000  # free the main window this long after it's closed
AWAY_TIDY_MS = 120_000         # the window left open behind other programs: memory goes back to Windows after this
OPENING_NOTED = 0.25           # opening the window this slow is written in the log, in parts
FREE_POPUPS_AFTER_MS = 60_000  # ... and the quick menu, quick wheel and clipboard popup after they close
MENU_WRITE_DELAY_MS = 1500     # wait for more changes before rewriting the right-click menu
MENU_BATCH_MS = 700            # right-clicked files arrive one process at a time; collect them
GC_EVERY_MS = 2_000            # Python's garbage collector runs on the window's thread this often (see Controller)
log = logging.getLogger("corefold")


def resource(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


# ----------------------------------------------------------------------------- background jobs

class JobSignals(QObject):
    started = Signal(int)
    item = Signal(int, int, int, str)  # job id, file number, how many, name
    progress = Signal(int, float)
    finished = Signal(int, object, list, bool)  # job id, Result, errors, cancelled


class Job(QRunnable):
    def __init__(self, job_id: int, tool: Tool, files: list[Path], options: Options,
                 signals: JobSignals, cancel: threading.Event, params: dict, job: dict | None = None):
        super().__init__()
        self.job_id, self.tool, self.files, self.options = job_id, tool, files, options
        self.signals, self.cancel, self.params = signals, cancel, params
        self.job = job if job is not None else {}  # (what's found out here for the window: the files' sizes)
        self._last = -1.0

    def _progress(self, frac: float):
        if frac - self._last >= 0.005 or frac >= 1.0:
            self._last = frac
            self.signals.progress.emit(self.job_id, frac)

    def run(self):
        lower_this_thread()
        self.signals.started.emit(self.job_id)
        if self.cancel.is_set():  # cancelled while waiting in line
            self.signals.finished.emit(self.job_id, Result(), [], True)
            return
        # (here, not as the job starts: a drive that's slow to answer, or a file being scanned, held the window)
        self.job["bytes_in"] = sum(size_of(f) for f in self.files)
        try:
            result, errors = run_job(self.tool, self.files, self.options, self._progress, self.cancel,
                                     self.params, lambda i, n, name: self.signals.item.emit(self.job_id, i, n, name))
        except Exception as e:
            log.exception("job crashed")
            from .converters import friendly_error
            result, errors = Result(), [friendly_error(e)]
        self.signals.finished.emit(self.job_id, result, errors, self.cancel.is_set())


class UpdateSignals(QObject):
    checked = Signal(object, str, bool)  # UpdateInfo or None, error text, asked by the user?
    progress = Signal(int, float)
    downloaded = Signal(str)
    failed = Signal(str)


class BackgroundSignals(QObject):
    menu_written = Signal(str, str)    # signatures of what's now in the right-click menu (the Converter's, Unlock's)
    menu_files_ready = Signal(object)  # [(tool key, files)] read and ready to run
    archive_listed = Signal(object)    # (archive, password, entries or None, their Contents or None, error or None)


# ----------------------------------------------------------------------------- key-drag detector

class DragWatcher(QObject):
    """Notices a Shift-drag (or the chosen key) that starts in File Explorer or on the desktop.

    Checks the mouse often only while a button is held; otherwise it looks rarely (a drag lasts far
    longer than that), so Rakiza Core uses next to no CPU while it waits in the tray. With the Converter
    paused or removed it doesn't look at all."""
    started = Signal()
    released = Signal()
    FAST_MS, IDLE_MS = 15, 90

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.pressed = self.armed = self.active = False
        self.press_pos = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.refresh()

    def wanted(self) -> bool:
        return self.settings.enabled and "converter" not in getattr(self.settings, "tools_off", ())

    def refresh(self):
        """Starts or stops watching, to match the settings."""
        if self.wanted() or self.pressed:
            if not self.timer.isActive():
                self.timer.start(self.IDLE_MS)
        else:
            self.timer.stop()

    def _interval(self, ms: int):
        if self.timer.interval() != ms:
            self.timer.setInterval(ms)

    def poll(self):
        if not self.wanted() and not self.pressed:
            self.timer.stop()
            return
        down = winutil.key_down(winutil.primary_button())
        if down and not self.pressed:
            self.pressed = True
            self.press_pos = QCursor.pos()
            self.armed = self.settings.enabled and \
                winutil.window_class_under_cursor() in SOURCE_WINDOW_CLASSES
            self._interval(self.FAST_MS if self.armed else self.IDLE_MS)
        elif not down and self.pressed:
            was_active = self.active
            self.pressed = self.armed = self.active = False
            self._interval(self.IDLE_MS)
            if was_active:
                self.released.emit()
            return
        if self.pressed and self.armed and not self.active and winutil.shortcut_held(self.settings.shortcut):
            if (QCursor.pos() - self.press_pos).manhattanLength() > DRAG_THRESHOLD_PX:
                self.active = True
                self.started.emit()


# ----------------------------------------------------------------------------- talking to helpers

class Inbox(QObject):
    """Reads one message from a helper process (see ipc.py), however fast it hangs up."""
    message = Signal(str)

    def __init__(self, conn: QLocalSocket):
        super().__init__(conn)
        self.conn = conn
        self.buf = bytearray()
        self.done = False
        conn.readyRead.connect(self._read)
        conn.disconnected.connect(self._finish)
        QTimer.singleShot(3000, self._finish)  # never wait forever on a stuck helper
        # The helper may have written and left already, before the signals above were connected.
        QTimer.singleShot(0, self._check)

    MAX_BYTES = 4 * 1024 * 1024  # thousands of file paths; anything bigger isn't from Rakiza Core

    def _read(self):
        if not self.done:
            try:
                self.buf.extend(bytes(self.conn.readAll()))
            except RuntimeError:  # (the connection is gone already: Rakiza Core is closing, for an update say)
                return
            if len(self.buf) > self.MAX_BYTES:
                self.buf.clear()
                self._finish()

    def _check(self):
        if self.done:
            return
        self._read()
        try:
            gone = self.conn.state() == QLocalSocket.LocalSocketState.UnconnectedState
        except RuntimeError:
            gone = True
        if gone:
            self._finish()

    def _finish(self):
        if self.done:
            return
        self._read()
        self.done = True
        try:
            self.message.emit(self.buf.decode("utf-8", "replace"))
            self.conn.deleteLater()
        except RuntimeError:
            pass  # (closing: the connection and this reader have gone with the window's objects)


# ----------------------------------------------------------------------------- controller


def _log_opening(new: bool, w, started: float, built: float, filled: float, shown: float):
    """How long opening the window took, in parts, when it was long enough to be felt (to find what to speed up)."""
    drawn = time.perf_counter()
    if drawn - started < OPENING_NOTED:
        return
    parts = [f"{part} {took:.2f}" for part, took in (getattr(w, "made_in", None) or []) if took >= 0.01] if new else []
    log.info("window opened in %.2f s: %s %.2f%s, brought up to date %.2f, shown %.2f, drawn %.2f",
             drawn - started, "made" if new else "kept", built - started,
             f" ({', '.join(parts)})" if parts else "", filled - built, shown - filled, drawn - shown)

class Controller(QObject):
    def __init__(self, app: QApplication, icon: QIcon):
        super().__init__()
        self.app = app
        self.server: QLocalServer | None = None
        self.icon = icon
        self.settings = Settings.load()
        settings_mod.ACTIVE = self.settings
        set_background_priority(self.settings.low_priority)
        self.history = History()
        self.theme = self._make_theme()
        winutil.log_memory("Qt started")
        app.setStyleSheet(self._css())
        winutil.log_memory("look applied")
        self._window = None
        sheet.install(app, self._sheet_window)  # (questions, menus and tips show inside the window)
        self.told_about_tray = False
        self.session_ending = False

        self.pool = QThreadPool(self)
        self.quick_pool = QThreadPool(self)  # opening one file from an archive never waits in line
        self.quick_pool.setMaxThreadCount(2)
        self.apply_performance()
        self.ids = itertools.count(1)
        self.cancels: dict[int, threading.Event] = {}
        self.uninstall_busy = 0  # an app's uninstall or leftover removal is running (Apps page)
        self.quit_pending = False  # the installer asked Rakiza Core to close (see quit_for_installer)
        self.jobs: dict[int, dict] = {}  # id -> tool, files, bytes_in, params, rename, after
        self.browsers: list = []         # open archive windows
        self.viewers: list = []          # open picture and email windows
        self.pending: list[Path] = []    # files arriving from the right-click menu for the wheel
        self.pending_open: list[Path] = []  # ... for the window ("All options…")
        self.pending_runs: dict[str, list[Path]] = {}  # tool key -> files ("Convert to PNG" in the menu)
        self.pending_unlock: list[Path] = []  # "What's using this?" (Rakiza Core Unlock)
        self.pending_timer = QTimer(self)
        self.pending_timer.setSingleShot(True)
        self.pending_timer.timeout.connect(self._open_pending)

        self.wheel = Wheel(self.settings, self.theme)
        self.wheel.chosen.connect(self.tool_chosen)
        self.wheel.opened.connect(self._wheel_opened)
        self.watcher = DragWatcher(self.settings)
        self.watcher.started.connect(lambda: self.wheel.popup_at_cursor())
        self.watcher.released.connect(lambda: QTimer.singleShot(150, self._hide_drag_wheel))

        self.signals = JobSignals()
        self.toast = Toast(icon, self.window_if_open)
        self.signals.started.connect(self._job_started)
        self.signals.item.connect(self._job_item)
        self.signals.progress.connect(self.toast.set_progress)
        self.signals.finished.connect(self.job_done)
        # what the Files workspace is working on (its list and the operations it shows), kept while its page is made
        # again or the window is closed
        from .filework import WorkState
        self.files_work = WorkState(self)
        self.signals.started.connect(self.files_work.job_running)
        self.signals.progress.connect(self.files_work.job_progress)
        self.toast.cancel_requested.connect(self.cancel_job)
        self._apply_toast_settings()
        self.compares: list = []  # open before/after windows
        self.bg = BackgroundSignals()
        self.bg.menu_written.connect(self._menu_written)
        self.bg.menu_files_ready.connect(self._run_menu_files)
        self.bg.archive_listed.connect(self._archive_listed)
        self.listing: set[str] = set()       # archives being read for the archive window
        self.recent_outputs: list[str] = []  # files Rakiza Core just made (not offered as "downloads")

        self.tray = QSystemTrayIcon(icon)
        self.tray.activated.connect(self._tray_clicked)
        self.tray.messageClicked.connect(self._tray_message_clicked)
        self.update_message_at = 0.0  # when the Windows notice about a new version was shown
        self.clip = None     # Rakiza Core Clipboard, while it's in use
        self.capture = None  # Rakiza Core Capture, while it's in use
        self.quick = None    # the quick menu and wheel, while one of them (or the on-top shortcut) is on
        self.awake = None    # Rakiza Core Awake (and its "Keep awake" tray menu), while it's in use
        self.colors = None   # Rakiza Core Colors, while it's in use
        self.peek = None     # the Viewer's Quick look (its shortcut in File Explorer), while it's in use
        self.menu = None
        self._build_tray_menu()

        # updates
        self.update_state = "idle"  # idle, checking, uptodate, available, downloading, error
        self.update_info: updater.UpdateInfo | None = None
        self.update_error = ""
        self.update_cancel: threading.Event | None = None
        self.usignals = UpdateSignals()
        self.usignals.checked.connect(self._update_checked)
        self.usignals.progress.connect(self.toast.set_progress)
        self.usignals.downloaded.connect(self._update_downloaded)
        self.usignals.failed.connect(self._update_failed)
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.check_updates)
        self.update_timer.start(UPDATE_CHECK_EVERY_MS)
        self.update_retry = QTimer(self)  # (started at login before the internet is up: not 6 hours later)
        self.update_retry.setSingleShot(True)
        self.update_retry.timeout.connect(self.check_updates)
        self.update_retries = 0
        # (full screen when a version was found: check, or say it, once later. One timer each, so the checks that
        # find it again while the game goes on don't add a chain of their own that never ends)
        self.update_busy_wait = QTimer(self)
        self.update_busy_wait.setSingleShot(True)
        self.update_busy_wait.timeout.connect(self.check_updates)
        self.update_retell = QTimer(self)
        self.update_retell.setSingleShot(True)
        self.update_retell.timeout.connect(self._tell_update)
        QTimer.singleShot(10_000, self.check_updates)

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.settings.save)

        # the right-click menu is rewritten only when what it shows has changed
        self.menu_timer = QTimer(self)
        self.menu_timer.setSingleShot(True)
        self.menu_timer.timeout.connect(self._write_menu)
        self.menu_busy = False

        # keeping memory low while idle
        self.tidy_timer = QTimer(self)
        self.tidy_timer.setSingleShot(True)
        self.tidy_timer.timeout.connect(self._tidy_when_free)
        self._tidy_waits = 0
        self._light_waits = 0
        self.light_tidy_timer = QTimer(self)  # soon after a job: hand back the memory it used
        self.light_tidy_timer.setSingleShot(True)
        self.light_tidy_timer.timeout.connect(self.light_tidy)
        self.free_window_timer = QTimer(self)
        self.free_window_timer.setSingleShot(True)
        self.free_window_timer.timeout.connect(self._free_window)
        self.free_popups_timer = QTimer(self)
        self.free_popups_timer.setSingleShot(True)
        self.free_popups_timer.timeout.connect(self._free_popups)
        # Python's garbage collector only runs here, on the window's thread: run by itself it can start in
        # any thread (writing the right-click menu, a conversion), and freeing a closed window's widgets from
        # there crashes Qt
        gc.disable()
        _converting[0] = lambda: bool(self.cancels)
        self.gc_timer = QTimer(self)
        self.gc_timer.timeout.connect(collect_garbage)
        self.gc_timer.start(GC_EVERY_MS)
        app.applicationStateChanged.connect(self._app_state_changed)

        self._update_tooltip()
        self.tray.show()
        winutil.log_memory("tray icon shown")

        # "Convert to JPG?" for photos that land in Downloads
        self.downloads = downloads_folder()
        set_downloads_folder(self.downloads)
        default_out = str(Path.home() / "Downloads" / APP_NAME)
        if self.settings.output_folder == default_out and self.downloads != Path.home() / "Downloads":
            self.settings.output_folder = str(self.downloads / APP_NAME)  # Downloads was moved: follow it
        self.seen_downloads: set[str] = set()
        self.dl_watcher = QFileSystemWatcher(self)
        self.dl_watcher.directoryChanged.connect(lambda _: self.dl_timer.start(1500))
        self.dl_timer = QTimer(self)
        self.dl_timer.setSingleShot(True)
        self.dl_timer.timeout.connect(self._check_downloads)
        self.watch_downloads(self.settings.watch_downloads)

        try:  # follow Windows light/dark changes while running
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._system_theme_changed)
        except AttributeError:
            pass

        if not self.settings.first_run:  # (once main() has switched off tools that are new in this version)
            QTimer.singleShot(0, self._background_tools)

    def _build_tray_menu(self):
        """The tray icon's right-click menu (made again when the language changes)."""
        menu = QMenu()
        self.act_update = menu.addAction("", self.start_update)  # shown while a new version is waiting
        f = self.act_update.font()
        f.setBold(True)
        self.act_update.setFont(f)
        self.act_update_sep = menu.addSeparator()
        self.act_update.setVisible(False)
        self.act_update_sep.setVisible(False)
        # grouped as the window's sidebar is: open it; the Files tools; the screen and quick tools; what's
        # running; the app itself
        act_open = menu.addAction(tr("Open Rakiza Core"), self.show_window)
        f = act_open.font()
        f.setBold(True)
        act_open.setFont(f)
        menu.setDefaultAction(act_open)
        self.tray_groups = [menu.addSeparator()]
        self.act_paste = menu.addAction(tr("Convert what's copied"), self.paste)
        self.act_archive = menu.addAction(tr("Open an archive…"), self.choose_archive)
        self.act_again = menu.addAction(tr("Do it again…"), self.do_again)
        menu.aboutToShow.connect(self._update_again_action)
        self.recent_menu = menu.addMenu(tr("Recent files"))
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self.tray_groups.append(menu.addSeparator())
        self.act_clip = menu.addAction(tr("Clipboard history"), lambda: self.show_clipboard(from_tray=True))
        self.act_shot = menu.addAction(tr("Take a screenshot"), lambda: self.capture and self.capture.screenshot())
        self.act_shot_later = menu.addAction(tr("Screenshot with a timer"),
                                             lambda: self.capture and self.capture.screenshot_later())
        self.act_color = menu.addAction(tr("Pick a colour"), lambda: self.colors and self.colors.pick())
        self.act_quick = menu.addAction(tr("Quick menu"), lambda: self.quick and self.quick.toggle_menu())
        self.tray_groups.append(menu.addSeparator())
        self.act_pause = menu.addAction(tr("Pause the Converter"))
        self.act_pause.setCheckable(True)
        self.act_pause.setChecked(not self.settings.enabled)
        self.act_pause.toggled.connect(lambda paused: self.set_enabled(not paused))
        self.act_cancel = menu.addAction(tr("Stop all conversions"), self.cancel_all)
        self.act_cancel.setVisible(False)
        self.tray_groups.append(menu.addSeparator())
        act_settings = menu.addAction(tr("Settings"), lambda: self.show_window("General"))
        self.act_settings = act_settings
        act_check = menu.addAction(tr("Check for updates"), lambda: self.check_updates(manual=True))
        menu.addSeparator()
        act_quit = menu.addAction(tr("Quit"), self.ask_quit)
        self.menu_icons = [(act_open, "home"), (self.act_paste, "arrow_swap"), (self.act_archive, "archive"),
                           (self.act_again, "arrow_sync"), (self.recent_menu.menuAction(), "history"),
                           (self.act_clip, "clipboard"), (self.act_shot, "screenshot"), (self.act_shot_later, "timer"),
                           (self.act_color, "color"), (self.act_quick, "layout"), (self.act_pause, "pause"),
                           (self.act_cancel, "dismiss"), (act_settings, "settings"), (act_check, "arrow_download"),
                           (self.act_update, "arrow_download"), (act_quit, "power")]
        menu.aboutToShow.connect(self._tray_tidy)  # (after "Do it again" knows whether it shows)
        old, self.menu = self.menu, menu
        for m in (menu, self.recent_menu):
            round_menu(m)
        if self.awake is not None:
            self.awake.add_tray_menu()  # (its "Keep awake" menu, in the new language)
        self._tray_tools()
        self._color_menu_icons()
        self.tray.setContextMenu(menu)
        if old is not None:
            old.deleteLater()
        if hasattr(self, "update_state"):  # (made again for a new language: a waiting version keeps its line)
            self._show_update_marks()

    # ---- the main window is built when needed and freed when it's been closed a while

    @property
    def window(self):
        if self._window is None:
            winutil.log_memory("before the window")
            from .window import MainWindow
            self._window = MainWindow(self, self.icon)
            self._window.apply_theme(self.theme)
            winutil.log_memory("window built")
        return self._window

    def rebuild_window(self, page: str = "Home", **keep):
        """Builds the window again from the settings (after Discard or Reset to defaults), in the same
        place, on the same page."""
        old = self._window
        if old is None:
            return
        if not self.pdf_work_ok(old):  # (the new window starts with an empty PDF page: Cancel keeps this one)
            old.setUpdatesEnabled(True)  # (a language change turned drawing off for the swap)
            return
        from .window import MainWindow
        geometry, shown = old.saveGeometry(), old.isVisible()
        new = MainWindow(self, self.icon, **keep)
        new.apply_theme(self.theme)
        new.restoreGeometry(geometry)
        new._placed = True
        new.open_page(page)
        self._window = new
        # the same place on the page (switching a tool on or off doesn't jump back to the top)
        bar = getattr(old.stack.currentWidget(), "verticalScrollBar", None)
        scrolled = bar().value() if bar is not None and page == old.current_page() else 0
        if not shown:
            old.hide()
            old.deleteLater()
            return
        # the new window takes the old one's place only once it's drawn: Windows shows a new window
        # white until it's painted, which flashed a white window over the old one every time
        new.setWindowOpacity(0.0)
        new.show()
        if scrolled:
            QApplication.sendPostedEvents()  # (laid out, so the page can scroll that far)
            _keep_scroll(new, scrolled)
        new.repaint()

        picture, self._fade_from = getattr(self, "_fade_from", None), None

        def swap():
            if _alive(new):
                if picture is not None:
                    from .atomic import motion
                    motion.fade_from(new, picture, atomic_tokens.MOTION["slow"])
                new.setWindowOpacity(1.0)
                new.raise_()
                new.activateWindow()
            if _alive(old):
                old.hide()
                old.deleteLater()
        QTimer.singleShot(60, swap)

    def _sheet_window(self):
        """Where a question goes that has no window of its own to show in (one from the tray, or from the
        right-click menu in Explorer): Rakiza Core's window, opened."""
        self.show_window()
        return self.window

    def window_if_open(self):
        """The window if it exists (and nothing is built just to update it)."""
        return self._window

    def window_closed(self):
        self.free_window_timer.start(FREE_WINDOW_AFTER_MS)

    def _free_window(self):
        w = self._window
        if w is None or w.isVisible():
            return
        self._window = None
        w.deleteLater()
        settings_mod.HELD = None  # (nothing waits for Save without its window: later saves keep every change)
        self.tidy_timer.start(2000)

    def _app_state_changed(self, state):
        """The window left open behind other programs (or minimised): a while later its memory goes back to
        Windows like the tray's does (it comes back by itself, in a blink, when the window is used again)."""
        if state == Qt.ApplicationActive or self._window is None:
            return
        if not self.tidy_timer.isActive() or self.tidy_timer.remainingTime() > AWAY_TIDY_MS:
            self.tidy_timer.start(AWAY_TIDY_MS)

    def popup_closed(self):
        self.free_popups_timer.start(FREE_POPUPS_AFTER_MS)

    def _free_popups(self):
        """The quick menu covers the whole screen, so even hidden it holds a picture of it in memory
        (about 8 MB on a Full HD screen, 33 MB on a 4K one): it's made again the next time it opens."""
        held = []
        if self.quick is not None:
            held += [(self.quick, "menu"), (self.quick, "wheel")]
        if self.clip is not None:
            held.append((self.clip, "popup"))
        if self.peek is not None:
            held.append((self.peek, "window"))
        freed = False
        for owner, name in held:
            w = getattr(owner, name)
            if w is not None and not w.isVisible():
                setattr(owner, name, None)
                w.deleteLater()
                freed = True
        if freed:
            self.tidy_timer.start(2000)

    def show_window(self, page_name: str | None = None):
        self.free_window_timer.stop()
        started = time.perf_counter()
        new = self._window is None
        w = self.window
        built = time.perf_counter()
        w.sync()
        w.refresh_history()  # conversions may have finished while the window was hidden
        if w.panel.paths and not w.isVisible():
            w.panel.refresh()  # files on the Home page may have been moved or deleted meanwhile
        if page_name:
            w.open_page(page_name)
        if getattr(self, "whats_new_waiting", False):  # (the first time the window opens after an update)
            self.whats_new_waiting = False
            QTimer.singleShot(500, self.show_whats_new)
        if not w.isVisible():  # (drawn before it's seen, so it never shows up white first)
            w.setWindowOpacity(0.0)
            filled = time.perf_counter()
            w.show()
            shown = time.perf_counter()
            w.repaint()
            QTimer.singleShot(60, lambda: w.setWindowOpacity(1.0) if _alive(w) else None)
            _log_opening(new, w, started, built, filled, shown)
        w.show()
        if w.isMinimized():  # (back from the taskbar; a maximised window stays maximised)
            w.setWindowState(w.windowState() & ~Qt.WindowMinimized)
        w.raise_()
        w.activateWindow()

    def _tray_clicked(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def _fill_recent(self):
        """The tray's "Recent files": the last few files Rakiza Core made; click one to see it."""
        self.recent_menu.clear()
        shown = 0
        for entry in self.history.items:
            for out in entry.outputs[:3]:
                p = Path(out)
                if shown >= 8:
                    break
                if p.exists():
                    self.recent_menu.addAction(short_name(p.name, 40), lambda p=p: winutil.reveal_in_explorer(p))
                    shown += 1
        if not shown:
            act = self.recent_menu.addAction(tr("Nothing yet"))
            act.setEnabled(False)

    def _update_tooltip(self):
        busy = len(self.cancels)
        self.act_cancel.setVisible(busy > 0)
        if busy:
            self.tray.setToolTip(tr("{app}: converting ({count})", app=APP_NAME, count=busy))
        elif not self.settings.enabled:
            self.tray.setToolTip(tr("{app}: paused", app=APP_NAME))
        else:
            self.tray.setToolTip(tr("{app}: hold {key} while dragging a file", app=APP_NAME,
                                    key=self.settings.shortcut_label))
        if self.awake is not None and self.awake.on:
            self.tray.setToolTip(self.tray.toolTip() + "\n" + self.awake.status())
        if self.update_waiting():
            self.tray.setToolTip(self.tray.toolTip() + "\n" + tr("Rakiza Core {version} is available",
                                                                    version=self.update_info.version))

    def _css(self) -> str:
        return stylesheet(self.theme, WINDOW_TEXT_PT.get(self.settings.window_text, 10) / 10)

    def _make_theme(self):
        s = self.settings
        theme = build_theme(s.appearance, s.accent, s.custom_color, s.wheel_opacity)
        atomic_tokens.use(theme.tokens, s.animations)  # (the parts that draw themselves read it)
        return theme

    def _hide_drag_wheel(self):
        if not self.wheel.click_mode:
            self.wheel.hide()

    def play_sound(self, name: str):
        if name and name != "off":
            volume = self.settings.volume
            winutil.play_sound(lambda: sounds.prepared(name, volume))

    def play_done_sound(self):
        self.play_sound(self.settings.sound)

    def play_problem_sound(self):
        if self.settings.sound_problems and self.settings.sound != "off":
            self.play_sound("problem")

    def _wheel_opened(self):
        self.tidy_timer.stop()
        if self.settings.sound_tick:
            self.play_sound("tick")

    # ---- keeping Rakiza Core light

    def apply_performance(self):
        s = self.settings
        set_background_priority(s.low_priority)
        self.pool.setMaxThreadCount(1 if s.one_at_a_time else 2)

    def light_tidy(self):
        """A few seconds after the last job: memory it used goes back to Windows straight away
        (the background remover stays ready a little longer, in case more pictures follow)."""
        if self.cancels:
            return
        # (handing memory back stops everything for a moment, up to a quarter of a second: not while
        # someone's using one of Rakiza Core's windows, but as soon as they've stopped for a few seconds)
        if QApplication.activeWindow() is not None and winutil.idle_ms() < 3000 and self._light_waits < 40:
            self._light_waits += 1
            self.light_tidy_timer.start(3000)
            return
        self._light_waits = 0
        # (with a window in use, only the younger objects are looked through: going through every object takes
        # up to half a second, and the full look comes once the window isn't in use: see _tidy_when_free)
        gc.collect(1 if QApplication.activeWindow() is not None else 2)
        winutil.trim_memory()

    def _tidy_when_free(self):
        """Looking through every object takes up to half a second with a big Space map kept: not while
        someone is using one of Rakiza Core's windows (unless they've been at it for five minutes)."""
        if QApplication.activeWindow() is not None and self._tidy_waits < 30:
            self._tidy_waits += 1
            self.tidy_timer.start(10_000)
            return
        self._tidy_waits = 0
        self.tidy_up()

    def tidy_up(self):
        """Lets go of memory that's only needed while converting (the background remover alone
        is about 200 MB), then hands unused memory back to Windows."""
        if self.cancels or self.wheel.isVisible():
            self.tidy_timer.start(IDLE_TIDY_MS)
            return
        if self._window is None and not self.browsers and not self.viewers:
            icons.forget()
        release_memory()  # also forgets cached file facts and collects garbage
        winutil.trim_memory()
        log.info("tidied up memory")
        winutil.log_memory("tidied")

    # ---- paste: convert what's on the clipboard

    def clipboard_files(self) -> list[Path]:
        """Files copied in Explorer, or a copied picture saved as a PNG."""
        data = QGuiApplication.clipboard().mimeData()
        if data is None:
            return []
        if data.hasUrls():
            paths = [Path(u.toLocalFile()) for u in data.urls() if u.isLocalFile()]
            paths = [p for p in paths if p.exists()]
            if paths:
                return paths
        if data.hasImage():
            image = QGuiApplication.clipboard().image()
            if not image.isNull():
                folder = Path(self.settings.output_folder)
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                except OSError:
                    folder = Path(tempfile.gettempdir())
                out = unique_path(folder / (tr("Pasted picture") + time.strftime(" %Y-%m-%d %H.%M.%S.png")))
                if image.save(str(out), "PNG"):
                    return [out]
        return []

    def paste(self):
        files = self.clipboard_files()
        if files:
            self.show_window()
            self.window.show_files(files)
        else:
            self.toast.notify(tr("Nothing to convert"),
                              tr("Copy a picture or some files first (Ctrl + C), then try again."), linger_ms=4500)

    # ---- downloads helper

    def set_watch_downloads(self, on: bool):
        self.watch_downloads(on)
        self.settings_changed()

    def watch_downloads(self, on: bool):
        self.settings.watch_downloads = on
        on = on and all_tools.is_on(self.settings, "converter")  # a Converter feature
        folder = str(self.downloads)
        if on and self.downloads.is_dir():
            try:
                self.seen_downloads = {p.name for p in self.downloads.iterdir()}
            except OSError:
                self.seen_downloads = set()
            if folder not in self.dl_watcher.directories():
                self.dl_watcher.addPath(folder)
        elif folder in self.dl_watcher.directories():
            self.dl_watcher.removePath(folder)

    def _check_downloads(self):
        if not self.settings.watch_downloads or not self.downloads.is_dir():
            return
        try:
            names = {p.name for p in self.downloads.iterdir()}
        except OSError:
            return
        new = names - self.seen_downloads
        self.seen_downloads = names
        made = {os.path.normcase(p) for p in self.recent_outputs}  # (once: an Undo can bring back 30,000 names)
        for name in sorted(new):
            path = self.downloads / name
            if os.path.normcase(str(path)) in made:
                continue  # made by Rakiza Core itself
            if path.suffix.lower() in (".heic", ".heif", ".webp", ".avif") and path.is_file():
                self._offer_jpg(path)
                break

    def _offer_jpg(self, path: Path, tries: int = 0, waits: int = 0):
        # wait until the browser has finished writing the file
        try:
            st = path.stat()
        except OSError:
            return
        # (Firefox makes the file empty at once and fills a ".part" beside it: a slow download takes a while)
        writing = st.st_size == 0 or path.with_name(path.name + ".part").exists()
        if (writing and tries < 300) or (tries < 10 and time.time() - st.st_mtime < 1.5):
            QTimer.singleShot(1000 if tries < 10 else 3000, lambda: self._offer_jpg(path, tries + 1))
            return
        if writing:  # (never finished: nothing to convert)
            return
        if winutil.user_busy():  # a game or presentation is full screen: ask later (for up to 10 minutes)
            if waits < 30:
                QTimer.singleShot(20_000, lambda: self._offer_jpg(path, max(tries, 10), waits + 1))
            return
        from .converters import catalog
        tool = next(t for t in catalog()["image"] if t.key == "image:jpg")
        self.toast.notify(tr("{name} downloaded", name=short_name(path.name, 34)),
                          tr("Many apps can't open this kind of picture. Make a JPG copy?"),
                          tr("Convert to JPG"), lambda: self.start_job(tool, [path]), 12000)

    # ---- archives

    def ask_password(self, files: list[Path], wrong: bool = False) -> str | None:
        """The archive's password: one remembered earlier that opens it, or what the person types."""
        from . import passwords
        if not wrong and files and files[0].suffix.lower() != ".pdf" and passwords.load():
            # each try reads the archive with 7-Zip (a big one, 30 passwords: a long while), so it's done off the
            # window's thread, which goes on answering meanwhile
            from PySide6.QtCore import QEventLoop
            loop, got = QEventLoop(), {}

            def tried(result):
                got["found"] = result
                loop.quit()
            job = page_work.run(lambda _p: passwords.find(files[0]), lambda _: None, tried, name="corefold-passwords")
            if "found" not in got:
                loop.exec()
            del job
            found = got.get("found")
            if isinstance(found, Exception):
                log.debug("remembered passwords couldn't be tried: %s", found)
            if isinstance(found, str) and found:
                log.info("a remembered password opened the archive")
                return found
        answer = dialogs.ask("password", files, self.icon, self.theme.dark, wrong=wrong,
                             remember=self.settings.remember_password)
        if answer is None:
            return None
        if answer.get("remember") is not None and answer["remember"] != self.settings.remember_password:
            self.settings.remember_password = answer["remember"]
            self.settings_changed()
        if answer.get("remember"):
            passwords.add(answer["password"])
        return answer["password"]

    def _let_go_when_closed(self, win):
        """A closed Archive or picture window is let go of at once, not when the next one opens: what it
        showed (300,000 entries of a big archive) stayed in memory, and every full look of Python's collector
        went through it (0.45 s with the window in use)."""
        win.destroyed.connect(lambda *_: QTimer.singleShot(0, self, self._forget_closed))

    def _forget_closed(self):
        self.browsers = [b for b in self.browsers if _alive(b)]
        self.viewers = [v for v in self.viewers if _alive(v)]

    def open_browser(self, src: Path, password: str = ""):
        """Reads what's in the archive in the background (big archives take a moment), then opens
        the archive window."""
        for b in self.browsers:  # already open: bring it to the front
            if _alive(b) and b.src == src and not password:
                b.showNormal()
                b.raise_()
                b.activateWindow()
                return
        key = str(src).lower()
        if key in self.listing:
            return
        self.listing.add(key)
        from .browser import Contents  # (loaded here, not on the thread)

        def work():
            entries, contents, error = None, None, None
            try:
                entries = archives.list_entries(src, password)
                contents = Contents(entries)  # (what's in which folder, worked out here, not on the window)
            except Exception as e:
                error = e
            try:
                self.bg.archive_listed.emit((src, password, entries, contents, error))
            except RuntimeError:
                pass
        threading.Thread(target=work, daemon=True).start()

        def still_reading():  # (a big archive: something on screen says it's coming)
            if key in self.listing:
                title = tr("Opening {name}…", name=src.name)
                if self.toast.notify(title, tr("A big archive takes a moment."), linger_ms=30000):
                    self._opening_toast = title
        QTimer.singleShot(700, still_reading)

    def _archive_listed(self, payload):
        from .browser import ArchiveBrowser
        src, password, entries, contents, error = payload
        self.listing.discard(str(src).lower())
        if getattr(self, "_opening_toast", None):
            if self.toast.isVisible() and not self.toast.running and self.toast.title.text() == self._opening_toast:
                self.toast.hide()
            self._opening_toast = None
        if isinstance(error, archives.NeedPassword):  # names are hidden too (7z with a password)
            new = self.ask_password([src], wrong=bool(password))
            if new:
                self.open_browser(src, new)
            return
        if isinstance(error, PermissionError):  # (another program holds it: nothing to rescue, it's fine)
            self.toast.notify(tr("Couldn't open the archive"), f"{short_name(src.name)}: {error}")
            return
        if error is not None:
            self.offer_repair(src, error)
            return
        if not password and contents.locked:
            password = self.ask_password([src])
            if password is None:
                return
        win = ArchiveBrowser(self, src, password, entries, self.icon, contents)
        log.info("archive window opened (%d items)", len(entries))
        self.browsers = [b for b in self.browsers if _alive(b)] + [win]
        self._let_go_when_closed(win)
        self.remember_archive(src)
        win.show()
        win.raise_()
        win.activateWindow()

    def choose_archive(self, parent=None):
        """Pick an archive and open it (from the tray menu or the Archive page)."""
        from PySide6.QtWidgets import QFileDialog
        from .core import ARCHIVE_EXT
        pattern = " ".join(f"*{e}" for e in sorted(ARCHIVE_EXT))
        name, _ = QFileDialog.getOpenFileName(parent, tr("Open an archive"), str(Path.home()),
                                              f"{tr('Archives')} ({pattern});;{tr('All files')} (*)")
        if name:
            self.open_browser(Path(name))

    # ---- the tools this person uses (switched on and off in their sections)

    def set_tool(self, tool_id: str, on: bool):
        """Switches a tool on or off (on its section's page): the window follows right away."""
        if all_tools.state(tool_id) != all_tools.AVAILABLE:  # (resting or still being made: kept as it is)
            return
        s = self.settings
        off = set(s.tools_off)
        if on:
            off.discard(tool_id)
        else:
            off.add(tool_id)
        s.tools_off = sorted(off)
        s.known_tools = sorted(set(s.known_tools) | {tool_id})
        s.save()
        self.apply_tools()
        w = self._window
        if w is not None:
            from .atomic import motion  # (the pages made again come in softly, not all at once)
            motion.cross_fade(w, w.tools_changed, animate=s.animations)
        QTimer.singleShot(400, lambda: self._tool_added_tip(tool_id) if on else None)  # (after the window is drawn)

    def _tool_added_tip(self, tool_id: str):
        s = self.settings
        if tool_id == "capture" and self.capture is not None:
            from .hotkey import SHORTCUT_NAMES
            key = SHORTCUT_NAMES.get(s.cap_shortcut)
            if key and self.capture.shot_key.ok:
                self.toast.notify(tr("Rakiza Core Capture"), tr("Press {key} to take a screenshot.", key=key),
                                  linger_ms=7000)
        if tool_id == "clipboard" and self.clip is not None:
            from .clipboard import HOTKEY_NAMES
            key = HOTKEY_NAMES.get(s.clip_shortcut)
            if key and self.clip.hotkey.ok:
                self.toast.notify(tr("Rakiza Core Clipboard"), tr("Press {key} in any app to open your clipboard, then "
                                                               "pick what to paste.", key=key), linger_ms=7000)
        if tool_id == "colors" and self.colors is not None and s.colors_shortcut != "off" and self.colors.key.ok:
            from .settings import COLORS_SHORTCUTS
            self.toast.notify(tr("Rakiza Core Colors"), tr("Press {key} in any app to pick a colour.",
                                                        key=COLORS_SHORTCUTS.get(s.colors_shortcut, s.colors_shortcut)),
                              linger_ms=7000)

    def apply_tools(self):
        """Starts or stops what each tool does in the background, to match the tools in use."""
        self.watch_downloads(self.settings.watch_downloads)
        runner = getattr(self, "scan_runner", None)
        if runner is not None and not all_tools.is_on(self.settings, "scan"):
            runner.stop()  # (switched off: its scan stops too, and nothing of it is kept)
            self.scan_runner = None
        self.update_shell_menu(500)
        self.sync_archive_opening()
        self._background_tools()
        self._tray_tools()
        self.watcher.refresh()
        log.info("tools in use: %s", ", ".join(t.id for t in all_tools.enabled(self.settings)) or "none")

    def _tray_tools(self):
        """The tray menu only offers what the tools in use can do."""
        converter = all_tools.is_on(self.settings, "converter")
        for act in (self.act_paste, self.act_again, self.act_pause):
            act.setVisible(converter)
        self.act_archive.setVisible(all_tools.is_on(self.settings, "archive"))
        self.act_clip.setVisible(self.clip is not None)
        self.act_shot.setVisible(self.capture is not None)
        self.act_shot_later.setVisible(self.capture is not None)
        self.act_color.setVisible(self.colors is not None)
        self.act_quick.setVisible(self.quick is not None and self.settings.quick_menu)
        self._tray_tidy()

    def _tray_tidy(self):
        """No line between two groups with nothing to show between them (a group whose tools are all off)."""
        menu = getattr(self, "menu", None)
        if menu is None or not getattr(self, "tray_groups", None):
            return
        acts = menu.actions()

        def shown(a) -> bool:
            return not a.isSeparator() and a.isVisible() and a is not self.act_update
        for sep in self.tray_groups:
            if sep not in acts:
                continue
            i = acts.index(sep)
            after = []
            for a in acts[i + 1:]:
                if a.isSeparator():
                    break
                after.append(a)
            sep.setVisible(any(shown(a) for a in acts[:i]) and any(shown(a) for a in after))

    def _background_tools(self):
        self._rest_paused_tools()
        self._clipboard_tool()
        self._capture_tool()
        self._colors_tool()
        self._quick_tool()
        self._awake_tool()
        self._peek_tool()
        self.keep_tidy()
        self.keep_clean()
        self.keep_backups()
        self.keep_drive_backup()
        self._tray_tools()
        running = [name for name, on in (("clipboard", self.clip), ("capture", self.capture),
                                         ("quick access", self.quick), ("awake", self.awake),
                                         ("colors", self.colors), ("quick look", self.peek)) if on is not None]
        if running:
            log.info("running in the background: %s", ", ".join(running))
            winutil.log_memory("background tools started")

    def _rest_paused_tools(self):
        """Tools resting for now (tools.PAUSED_SECTIONS) don't run, but one thing of theirs would go on without
        them: a shutdown planned in Awake is Windows' own timer, and nothing would be left to see or cancel it.
        So it's cancelled, once, and said. (Programs put off by Rakiza Core Startup still start a minute after
        sign-in: they were taken out of Windows' own list, so without that they'd never start.)"""
        s = self.settings
        if all_tools.state("awake") != all_tools.PAUSED or not s.shutdown_at or sys.platform != "win32":
            return
        from datetime import datetime
        try:
            planned = datetime.fromisoformat(s.shutdown_at) > datetime.now()
        except ValueError:
            planned = False
        if planned:
            from .awake import ERROR_NO_SHUTDOWN_IN_PROGRESS, run_shutdown
            code, said = run_shutdown("/a")
            log.info("awake is resting: its planned shutdown cancelled: code %d %s", code, said)
            if code not in (0, ERROR_NO_SHUTDOWN_IN_PROGRESS):
                return  # (still planned: tried again next time)
            QTimer.singleShot(3000, lambda: self.toast.notify(
                tr("Planned shutdown cancelled"),
                tr("Rakiza Core Awake is resting while it's being made better, so the shutdown it planned won't "
                   "happen. Your PC stays on."), linger_ms=12000))
        s.shutdown_at = s.shutdown_planned = ""
        s.save()

    def start_later(self, items: list | None = None):
        """Starts the programs put off until a minute after sign-in (Rakiza Core Startup), one by one."""
        from . import startup
        items = list(self.settings.startup_later) if items is None else items
        if not items:
            return
        ok = startup.start_now(items[0])
        log.info("started later: %s%s", items[0].get("name"), "" if ok else " (couldn't)")
        if len(items) > 1:
            QTimer.singleShot(startup.LATER_GAP_S * 1000, lambda: self.start_later(items[1:]))

    def keep_tidy(self):
        """File Organizer's "keep this folder tidy by itself": a look every hour, a sort once a day."""
        on = bool(self.settings.organize_auto_folder) and all_tools.is_on(self.settings, "organizer")
        timer = getattr(self, "organize_timer", None)
        if on and timer is None:
            self.organize_timer = timer = QTimer(self)
            timer.setInterval(3600 * 1000)
            timer.timeout.connect(self.tidy_folder)
            timer.start()
            QTimer.singleShot(10 * 60 * 1000, self.tidy_folder)  # (not while Rakiza Core itself is starting)
        elif not on and timer is not None:
            timer.stop()
            timer.deleteLater()
            self.organize_timer = None

    def keep_clean(self):
        """Space's "clean up by itself": a look every hour, a clean-up once a week or month (only while Space is
        switched on: otherwise nothing of it runs)."""
        from .space import AUTO_EVERY
        on = AUTO_EVERY.get(self.settings.space_auto_clean, 0) > 0 and all_tools.is_on(self.settings, "space")
        timer = getattr(self, "clean_timer", None)
        if on and timer is None:
            self.clean_timer = timer = QTimer(self)
            timer.setInterval(3600 * 1000)
            timer.timeout.connect(self.clean_up_now)
            timer.start()
            QTimer.singleShot(12 * 60 * 1000, self.clean_up_now)  # (not while Rakiza Core itself is starting)
        elif not on and timer is not None:
            timer.stop()
            timer.deleteLater()
            self.clean_timer = None

    def clean_up_now(self, now: bool = False):
        """The clean-up by itself, if its week or month has gone by (or now)."""
        from . import work
        from .space import AUTO_EVERY
        s = self.settings
        every = AUTO_EVERY.get(s.space_auto_clean, 0)
        if not every or not all_tools.is_on(s, "space") or getattr(self, "_clean_job", None) is not None:
            return
        if not now and time.time() - s.space_auto_last < every:
            return

        def go(_progress):
            from . import space
            return space.auto_clean()
        self._clean_job = work.run(go, lambda _: None, self._cleaned_up, name="corefold-clean", busy=True)

    def _cleaned_up(self, result):
        self._clean_job = None
        self.settings.space_auto_last = time.time()
        self.settings.save()
        if isinstance(result, Exception):
            log.warning("the clean-up by itself failed: %s", result)
            return
        log.info("cleaned up by itself: %s", human_size(result))
        if result >= 50 << 20:  # (a toast for a few megabytes would only be noise)
            self.toast.notify(tr("Cleaned up"), tr("Space freed {size} of temporary files and caches by itself.",
                                                   size=human_size(result)), linger_ms=6000)

    def keep_backups(self):
        """Cloud's folder backups: a look every hour for one whose day or week has gone by (only while Cloud is
        switched on and there's a backup that runs by itself)."""
        try:
            from .cloudbackup import EVERY, load
            on = all_tools.is_on(self.settings, "cloud") and any(EVERY.get(b.every) for b in load())
        except Exception:
            log.debug("backups couldn't be read", exc_info=True)
            on = False
        timer = getattr(self, "backup_timer", None)
        if on and timer is None:
            self.backup_timer = timer = QTimer(self)
            timer.setInterval(3600 * 1000)
            timer.timeout.connect(self.back_up_now)
            timer.start()
            QTimer.singleShot(15 * 60 * 1000, self.back_up_now)  # (not while Rakiza Core itself is starting)
        elif not on and timer is not None:
            timer.stop()
            timer.deleteLater()
            self.backup_timer = None

    def back_up_now(self):
        """The backups whose time has come, one after the other, in the background."""
        from . import work
        if not all_tools.is_on(self.settings, "cloud") or getattr(self, "_backup_job", None) is not None:
            return

        def go(_progress):
            from .cloudbackup import run_due
            return run_due()
        self._backup_job = work.run(go, lambda _: None, self._backed_up, name="corefold-backup")

    def _backed_up(self, result):
        self._backup_job = None
        if isinstance(result, Exception):
            log.warning("the backups by themselves failed: %s", result)
            return
        for b, r in result:
            log.info("backup %s: %s sent, %s the same, %s failed", b.id, r.get("sent"), r.get("same"),
                     len(r.get("failed", [])))
            if r.get("error") is not None or r.get("failed"):  # (only what needs the person: no toast every day)
                self.toast.notify(tr("Backup"), tr("The backup of {name} couldn't finish. Open Cloud to see why.",
                                                   name=b.name), linger_ms=8000)

    # ---- Backup (folders kept safe on another drive: backup.py)

    def keep_drive_backup(self):
        """Backup by itself: a look every half hour for a backup whose day has gone by and whose drive is there
        (only while Backup is switched on and set to run by itself)."""
        from .backuppage import plan_of
        plan = plan_of(self.settings)
        on = all_tools.is_on(self.settings, "backup") and plan.auto and bool(plan.sources) and bool(plan.dest)
        timer = getattr(self, "drive_backup_timer", None)
        if on and timer is None:
            self.drive_backup_timer = timer = QTimer(self)
            timer.setInterval(30 * 60 * 1000)
            timer.timeout.connect(self.run_drive_backup)
            timer.start()
            QTimer.singleShot(10 * 60 * 1000, self.run_drive_backup)  # (not while Rakiza Core itself is starting)
        elif not on and timer is not None:
            timer.stop()
            timer.deleteLater()
            self.drive_backup_timer = None

    def drive_backup_running(self) -> bool:
        return getattr(self, "_drive_backup_job", None) is not None

    def run_drive_backup(self, now: bool = False):
        """The backup, if its day has gone by and its drive is there (or now, when asked on its page)."""
        import threading

        from . import backup, work
        from .backuppage import plan_of
        if not all_tools.is_on(self.settings, "backup") or self.drive_backup_running():
            return
        plan = plan_of(self.settings)
        if backup.problem(plan.sources, plan.dest) or not (now or backup.due(plan)):
            return
        self._drive_backup_cancel = cancel = threading.Event()

        def go(progress):
            return backup.run(plan, lambda files, size, name: progress((files, size, name)), cancel)
        self._drive_backup_job = work.run(go, self._drive_backup_step, lambda r, p=plan, n=now: self._drive_backed_up(
            p, r, n), name="corefold-drive-backup")

    def stop_drive_backup(self):
        cancel = getattr(self, "_drive_backup_cancel", None)
        if cancel is not None:
            cancel.set()

    def _drive_backup_page(self):
        from . import work
        page = getattr(self._window, "backup_page", None) if self._window is not None else None
        return page if page is not None and work.alive(page) else None

    def _drive_backup_step(self, step):
        page = self._drive_backup_page()
        if page is not None and isinstance(step, tuple):
            page.backup_progress(*step)

    def _drive_backed_up(self, plan, summary, asked: bool):
        from . import backup
        from .backuppage import plan_of, save_plan
        self._drive_backup_job = None
        if isinstance(summary, Exception):
            log.warning("the backup failed: %s", summary)
            summary = backup.Summary(problem="Stopped")
        log.info("backup: %d copied (%s), %d older kept, %d failed, %s missing, %s", summary.copied,
                 human_size(summary.bytes), summary.kept_old, len(summary.failed), summary.missing, summary.problem)
        fresh = plan_of(self.settings)
        if fresh.id == plan.id:  # (the page may have changed the folders meanwhile: only the result is kept)
            backup.finished(fresh, summary)
            save_plan(self.settings, fresh)
        page = self._drive_backup_page()
        if page is not None:
            page.backup_done()
        if summary.problem == "The backup drive is full":
            self.toast.notify(tr("Backup"), tr("The backup drive is full: the backup couldn't finish."),
                              linger_ms=8000)
        elif asked and not summary.problem:
            self.toast.notify(tr("Backup done"), tr("{count} files copied ({size})", count=f"{summary.copied:,}",
                                                    size=human_size(summary.bytes)), linger_ms=5000)

    def tidy_folder(self, now: bool = False):
        """Sorts the folder kept tidy, if a day has gone by since the last time (or now)."""
        from . import work
        s = self.settings
        folder = s.organize_auto_folder
        if not folder or not all_tools.is_on(s, "organizer") or getattr(self, "_tidy_job", None) is not None:
            return
        if not now and time.time() - s.organize_auto_last < 23 * 3600:
            return

        from PySide6.QtCore import QStandardPaths
        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
        known = any(place and os.path.normcase(os.path.normpath(str(place))) == os.path.normcase(os.path.normpath(folder))
                    for place in (downloads_folder(), desktop))  # (as the page allowed it: see organizer.refused)

        def go(_progress):
            from . import organizer
            return organizer.auto_sort(Path(folder), known=known)
        self._tidy_job = work.run(go, lambda _: None, lambda r, f=folder: self._tidied(f, r), name="corefold-tidy",
                                  busy=True)

    def _tidied(self, folder: str, result):
        self._tidy_job = None
        self.settings.organize_auto_last = time.time()
        self.settings.save()
        if isinstance(result, Exception):
            log.warning("couldn't tidy %s: %s", folder, result)
            return
        log.info("tidied %s: %d files", folder, result.moved)
        if not result.moved:
            return
        page = getattr(self._window, "organizer_page", None) if self._window is not None else None
        if page is not None:
            page.ensure_loaded()
        detail = tr("1 file went into its folder.") if result.moved == 1 else \
            tr("{count} files went into their folders.", count=result.moved)
        self.toast.notify(tr("Tidied {folder}", folder=Path(folder).name), detail, tr("Undo"), self._untidy,
                          linger_ms=12000)

    def _untidy(self):
        from . import organizer, work
        page = getattr(self._window, "organizer_page", None) if self._window is not None else None

        def done(result):
            self._untidy_job = None
            if page is not None and work.alive(page):
                page.ensure_loaded()
            if not isinstance(result, Exception) and result.back:
                self.toast.notify(tr("Put back"), tr("Put 1 file back where it was.") if result.back == 1 else
                                  tr("Put {count} files back where they were.", count=result.back), linger_ms=4000)
        self._untidy_job = work.run(lambda p: organizer.undo(p), lambda _: None, done, name="corefold-untidy",
                                    busy=True)

    def _capture_tool(self):
        """Rakiza Core Capture's shortcuts are only there while it's in use."""
        on = all_tools.is_on(self.settings, "capture")
        if on and self.capture is None:
            from .capture import CaptureTool
            try:
                self.capture = CaptureTool(self)
            except Exception:
                log.exception("couldn't start Capture")
                self.capture = None
                return
            self._cap_applied = self._cap_settings()
        elif not on and self.capture is not None:
            self.capture.stop()
            self.capture.deleteLater()
            self.capture = None

    def _cap_settings(self) -> tuple:
        return (self.settings.cap_shortcut, self.settings.cap_text_shortcut)

    def _awake_tool(self):
        """Rakiza Core Awake's tray menu is only there while it's in use (and removing it lets the PC sleep)."""
        on = all_tools.is_on(self.settings, "awake")
        if on and self.awake is None:
            from .awake import AwakeTool
            try:
                self.awake = AwakeTool(self)
            except Exception:
                log.exception("couldn't start Awake")
                self.awake = None
        elif not on and self.awake is not None:
            self.awake.stop()
            self.awake.deleteLater()
            self.awake = None
            self._update_tooltip()

    def _colors_tool(self):
        """Rakiza Core Colors' shortcut is only there while it's in use."""
        on = all_tools.is_on(self.settings, "colors")
        if on and self.colors is None:
            from .colors import ColorsTool
            try:
                self.colors = ColorsTool(self)
            except Exception:
                log.exception("couldn't start Colors")
                self.colors = None
                return
            self._colors_applied = self.settings.colors_shortcut
        elif not on and self.colors is not None:
            self.colors.stop()
            self.colors.deleteLater()
            self.colors = None

    def _peek_tool(self):
        """The Viewer's Quick look: only while the Viewer is in use and its shortcut isn't switched off."""
        on = all_tools.is_on(self.settings, "viewer") and self.settings.peek_key != "off"
        if on and self.peek is None:
            from .peek import PeekTool
            try:
                self.peek = PeekTool(self)
            except Exception:
                log.exception("couldn't start Quick look")
                self.peek = None
        elif not on and self.peek is not None:
            self.peek.stop()
            self.peek.deleteLater()
            self.peek = None
        elif on:
            self.peek.apply_settings()

    # ---- Quick access

    def _quick_tool(self):
        """The quick menu's and wheel's shortcuts are only there while one of them is switched on. The
        "Keep a window on top" shortcut lives there too, so it keeps quick access running (only its
        shortcuts, nothing is built) even with the menu and the wheel switched off."""
        s = self.settings
        on = (s.quick_menu or s.quick_wheel or s.ontop_key != "off") and not all_tools.section_paused("quick")
        if on and self.quick is None:
            from .quick import QuickAccess
            try:
                self.quick = QuickAccess(self)
            except Exception:
                log.exception("couldn't start quick access")
                self.quick = None
                return
            self._quick_applied = self._quick_settings()
            winutil.log_memory("quick access on")
            if not s.quick_told and s.quick_menu and self.quick.menu_key.ok:
                s.quick_told = True
                self.save_timer.start(400)
                from .hotkey import display
                QTimer.singleShot(4000, lambda: self.toast.notify(
                    tr("Quick menu"), tr("Press {key} in any app for your tools and shortcuts.",
                                         key=display(s.quick_menu_key)), linger_ms=7000))
                QTimer.singleShot(5000, lambda: winutil.log_memory("quick menu tip shown"))
        elif not on and self.quick is not None:
            self.quick.stop()
            self.quick.deleteLater()
            self.quick = None

    def _quick_settings(self) -> tuple:
        s = self.settings
        return s.quick_menu, s.quick_menu_key, s.quick_wheel, s.quick_wheel_key, s.quick_button, s.ontop_key

    # ---- Rakiza Core Clipboard

    def _clipboard_tool(self):
        """Starts listening for copies when the Clipboard is in use, and stops completely when it isn't."""
        on = all_tools.is_on(self.settings, "clipboard")
        if on and self.clip is None:
            from .clipboard import Clipboard
            try:
                self.clip = Clipboard(self.settings)
            except Exception:
                log.exception("couldn't start the clipboard history")
                self.clip = None
                return
            self.clip.on_hotkey = self.show_clipboard
            self._clip_applied = self._clip_settings()
        elif not on and self.clip is not None:
            self.clip.stop()
            self.clip.deleteLater()
            self.clip = None

    def _clip_settings(self) -> tuple:
        s = self.settings
        return s.clip_shortcut, s.clip_plain_key, s.clip_max, s.clip_days

    def show_clipboard(self, from_tray: bool = False):
        """The clipboard popup, near where you're typing (what you pick is pasted there)."""
        if self.clip is None:
            return
        from .clipboard import foreground_window
        target = 0 if from_tray else foreground_window()
        if self.clip.popup is not None and self.clip.popup.isVisible():
            target = self.clip.popup.target  # (pressed again while it's open: still pasted where you were)
        if self.clip.popup is None:
            from .clipview import QuickPaste
            self.clip.popup = QuickPaste(self, self.clip)
        self.clip.popup.open(target)

    def set_clipboard_paused(self, paused: bool):
        self.settings.clip_paused = paused
        self.settings_changed()
        if self.clip is not None:
            self.clip.changed.emit()

    def remember_archive(self, src: Path):
        """Keeps the list of recently opened archives (shown on the Archive page)."""
        from .settings import RECENT_ARCHIVES
        if not self.settings.remember_archives:
            return
        try:  # one opened from inside another archive lives in the temp folder: not worth remembering
            if src.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
                return
        except OSError:
            return
        key = os.path.normcase(str(src))
        recent = [p for p in self.settings.recent_archives if os.path.normcase(p) != key]
        self.settings.recent_archives = [str(src), *recent][:RECENT_ARCHIVES]
        self.settings.save()
        w = self._window
        if w is not None and hasattr(w, "refresh_archives"):
            w.refresh_archives()

    def forget_archives(self):
        self.settings.recent_archives = []
        self.settings.save()

    def offer_repair(self, src: Path, error):
        """An archive that can't be opened: say why, and offer to rescue what can still be read."""
        box = QMessageBox(QMessageBox.Warning, tr("Couldn't open the archive"),
                          f"{short_name(src.name)}: {error}\n\n"
                          + tr("If it's damaged, Rakiza Core can try to rescue the files that can still be read, "
                               "into a new ZIP next to it. The archive itself isn't changed."))
        box.setWindowIcon(self.icon)
        rescue = box.addButton(tr("Rescue files"), QMessageBox.AcceptRole)
        box.addButton(tr("Close"), QMessageBox.RejectRole)
        sheet.run(box)
        chosen = box.clickedButton() is rescue
        box.deleteLater()
        if chosen:
            self.repair_archive(src)

    def repair_archive(self, src: Path, password: str = ""):
        from . import archives as ar
        desc = tr("Rescue files from {name}", name=short_name(src.name, 30))
        tool = Tool("archive:repair", desc, desc, lambda items, ctx: ar.repair_archive(items[0], ctx), batch=True,
                    renames=False, password=True)
        self.start_job(tool, [src], {"password": password} if password else {})

    def set_open_archives(self, on: bool):
        self.settings.open_archives = on
        self.settings.save()
        self.sync_archive_opening()

    def set_open_files(self, key: str, on: bool):
        """open_images / open_emails (Settings → Viewer), open_pdfs (the PDF reader): applied at once, like opening
        archives."""
        setattr(self.settings, key, on)
        self.settings.save()
        self.sync_archive_opening()

    def sync_archive_opening(self):
        """Double-clicking archives, pictures and emails opens them in Rakiza Core, as chosen in Settings
        (and only for the tools in use)."""
        from . import assoc
        s = self.settings
        viewer = all_tools.is_on(s, "viewer")
        wanted = {"archive": (s.open_archives and all_tools.is_on(s, "archive"), tr("Rakiza Core Archive")),
                  "image": (s.open_images and viewer, tr("Rakiza Core Viewer")),
                  "email": (s.open_emails and viewer, tr("Rakiza Core Viewer")),
                  "pdf": (s.open_pdfs and all_tools.is_on(s, "pdf"), tr("Rakiza Core PDF")),
                  # (the Player has left Rakiza Core: its old "Open with" entry for videos and music is taken out)
                  "media": (False, "")}
        assoc.sync_in_background(wanted, app_command, app_icon())

    # ---- right-click menu

    def set_context_menu(self, on: bool):
        self.settings.context_menu = on
        self.settings_changed()
        self.update_shell_menu()

    def set_menu_style(self, style: str):
        self.settings.menu_style = style
        self.settings_changed()
        self.update_shell_menu()

    def update_shell_menu(self, delay_ms: int = MENU_WRITE_DELAY_MS):
        """Rewrites the right-click menu soon (after more changes have had a chance to arrive)."""
        self.menu_timer.start(delay_ms)

    def _write_menu(self):
        """Works out the menu and writes it if it changed, all in the background (it's a few
        thousand registry entries the first time, and nothing at all when nothing changed)."""
        if self.menu_busy:
            self.menu_timer.start(MENU_WRITE_DELAY_MS)
            return
        s = copy.deepcopy(self.settings)  # a snapshot: the settings may change while this runs
        # the Converter's menu (when it's wanted), and Scan's "Scan with Rakiza Core"
        menu_on = (s.context_menu and (all_tools.is_on(s, "converter") or all_tools.is_on(s, "space"))) or \
            all_tools.is_on(s, "scan")
        unlock_on = all_tools.is_on(s, "unlock")  # "What's using this?" is Rakiza Core Unlock's own
        if not menu_on and s.menu_signature == "off" and not unlock_on and s.unlock_menu_signature == "off":
            return
        self.menu_busy = True

        def work():
            signature = ""
            try:
                if not menu_on:
                    if s.menu_signature != "off" or shellmenu.strays([]):  # (one another copy wrote, too)
                        shellmenu.remove(unlock=False)
                    signature = "off"
                else:
                    command, icon = app_command, app_icon()
                    verbs = shellmenu.plan(s, command)
                    signature = shellmenu.signature(verbs, command, icon)
                    if signature != s.menu_signature or not shellmenu.in_registry(verbs, command, others=True):
                        shellmenu.write(verbs, command, icon)
                        log.info("right-click menu updated")
            except Exception:
                log.exception("could not update the right-click menu")
                signature = ""
            try:
                unlock = shellmenu.sync_unlock(unlock_on, s.unlock_menu_signature, app_command, app_icon())
            except Exception:
                log.exception("could not update Rakiza Core Unlock's right-click entry")
                unlock = ""
            try:
                self.bg.menu_written.emit(signature, unlock)
            except RuntimeError:
                pass  # quitting
        threading.Thread(target=work, daemon=True).start()

    def _menu_written(self, signature: str, unlock: str = ""):
        self.menu_busy = False
        changed = False
        if signature and signature != self.settings.menu_signature:
            self.settings.menu_signature = signature
            changed = True
        if unlock and unlock != self.settings.unlock_menu_signature:
            self.settings.unlock_menu_signature = unlock
            changed = True
        if changed:
            self.settings.save()

    # ---- requests from the right-click menu (each file arrives from its own helper process)

    def handle_message(self, text: str):
        word, _, rest = text.partition("\n")
        word = word.strip()
        lines = [line.strip() for line in rest.split("\n") if line.strip()]
        if word == "quit":  # the installer, before it updates or removes Rakiza Core
            self.quit_for_installer()
            return
        if self.quit_pending:  # (closing for the installer: nothing new is started)
            return
        if word == "show":
            self.show_window(lines[0] if lines and lines[0] in PAGE_NAMES else None)
        elif word == "convert":
            self.files_from_menu(lines)
        elif word == "open":
            self.files_from_menu(lines, into="window")
        elif word == "browse" and lines:
            self.browse_from_menu(lines[0])
        elif word == "view" and lines:
            self.open_viewer(Path(lines[0]))
        elif word == "read" and lines:
            self.read_pdfs(lines)
        elif word == "play" and lines:
            self.played_elsewhere(Path(lines[0]))
        elif word == "uninstall" and lines:
            self.uninstall_from_shortcut(Path(lines[0]))
        elif word == "run" and len(lines) >= 2:
            self.run_from_menu(lines[0], lines[1:])
        elif word == "unlock" and lines:
            self.unlock_from_menu(lines)
        elif word == "scan" and lines:
            self.scan_from_menu(lines)
        elif word == "space" and lines:
            self.space_from_menu(lines[0])

    def space_from_menu(self, path: str):
        """"See what fills this" on a folder or drive: Space's map of it (a map kept from before opens at once)."""
        if not all_tools.is_on(self.settings, "space") or not os.path.isdir(path):
            return
        self.show_window("Space")
        page = getattr(self.window, "space_page", None)
        if page is not None:
            page.look_at(Path(path))

    def scan_from_menu(self, paths: list[str]):
        """"Scan with Rakiza Core" on files, folders or drives: each arrives from its own helper process, so
        they're collected for a moment and scanned together."""
        if not all_tools.is_on(self.settings, "scan"):
            return
        waiting = getattr(self, "pending_scan", None)
        if waiting is None:
            waiting = self.pending_scan = []
            self.scan_timer = QTimer(self, singleShot=True, interval=MENU_BATCH_MS)
            self.scan_timer.timeout.connect(self._scan_pending)
        for p in paths:
            if os.path.exists(p) and p not in waiting:
                waiting.append(p)
        self.scan_timer.start()

    def _scan_pending(self):
        paths, self.pending_scan = self.pending_scan, []
        if not paths:
            return
        from .scanpage import runner
        r = runner(self)
        if r.running:
            self.tell(tr("A scan is already running"), tr("Scan these again when it's done."))
            self.show_window("Scan")
            return
        r.start("custom", paths)
        self.show_window("Scan")

    def uninstall_from_shortcut(self, path: Path):
        """"Uninstall with Rakiza Core" on a shortcut (the desktop, the Start menu): the Uninstaller's own steps
        for the program it opens, leftovers and backup included."""
        if not all_tools.is_on(self.settings, "uninstaller"):
            return
        from . import uninstall as un
        try:
            apps = un.list_apps(include_store=False)
        except Exception:
            log.warning("couldn't list the installed programs", exc_info=True)
            apps = []
        program = un.app_for_shortcut(path, apps)
        if program is None:
            self.tell(tr("Couldn't tell which program this is"),
                      tr("{name} doesn't point to an installed program. Find it in the Uninstaller instead.",
                         name=path.stem))
            self.show_window("Apps")
            return
        from .appspage import UninstallDialog
        dlg = UninstallDialog(self, [program], apps, False, self.icon)
        dlg.setWindowFlag(Qt.WindowStaysOnTopHint)
        sheet.run(dlg)
        dlg.deleteLater()

    def files_from_menu(self, paths: list[str], into: str = "wheel"):
        target = self.pending if into == "wheel" else self.pending_open
        for p in paths:
            path = Path(p)
            if path.exists() and path not in target:
                target.append(path)
        self.pending_timer.start(MENU_BATCH_MS)

    def run_from_menu(self, key: str, paths: list[str]):
        files = self.pending_runs.setdefault(key, [])
        for p in paths:
            path = Path(p)
            if path.exists() and path not in files:
                files.append(path)
        self.pending_timer.start(MENU_BATCH_MS)

    def unlock_from_menu(self, paths: list[str]):
        """From "What's using this?" (Rakiza Core Unlock): each selected file arrives on its own, so they're
        gathered first."""
        for p in paths:
            path = Path(p)
            if path.exists() and path not in self.pending_unlock:
                self.pending_unlock.append(path)
        self.pending_timer.start(MENU_BATCH_MS)

    def _open_pending(self):
        unlock, self.pending_unlock = self.pending_unlock, []
        if unlock and all_tools.is_on(self.settings, "unlock"):
            self.show_window("Unlock")
            page = getattr(self.window, "unlock_page", None)
            if page is not None:
                page.check(unlock)
        files, self.pending = self.pending, []
        if files:
            self.wheel.popup_at_cursor(files)
        files, self.pending_open = self.pending_open, []
        if files:
            self.show_window()
            self.window.show_files(files)
        runs, self.pending_runs = self.pending_runs, {}
        runs = [(k, v) for k, v in runs.items() if v]
        if runs:
            def work():  # reading videos and PDFs can take a moment: not on the window's thread
                for _key, files in runs:
                    try:
                        warm_up(files)
                    except Exception:
                        log.debug("warm-up failed", exc_info=True)
                self.bg.menu_files_ready.emit(runs)
            threading.Thread(target=work, daemon=True).start()

    def _run_menu_files(self, runs):
        for key, files in runs:
            self._run_menu_tool(key, files)

    def _run_menu_tool(self, key: str, files: list[Path]):
        kind = key.split(":", 1)[0]
        if kind not in ("pack", "files"):  # "Convert to PNG" picked with videos selected too: only the pictures
            files = [f for f in files if category(f) == kind] or files
        tool_now = None
        for t in _all_tools(len(files) > 1):
            if t.key == key:
                tool_now = t
                break
        if tool_now and tool_now.target:  # skip files that are already in that format
            files = [f for f in files if norm(f.suffix) != tool_now.target] or files
        pk = pick(files, frozenset(), (), True, None, True)
        slot = next((sl for sl in pk.slots if sl.tool.key == key), None)
        if slot is None:
            self.tell(tr("Can't do that here"), tr("This option doesn't work for these files."))
            return
        if not slot.ok:
            self.tell(slot.tool.description, slot.reason or "")
            return
        self.tool_chosen(slot.tool, pk.files)

    def tell(self, title: str, detail: str):
        """A short problem message: in the popup, or from the tray icon if a conversion is showing there."""
        if not self.toast.notify(title, detail, linger_ms=6000):
            self.tray.showMessage(title, detail, QSystemTrayIcon.Warning, 6000)
        self.play_problem_sound()

    def say(self, title: str, detail: str, done: bool = False):
        """A short good-news message (no problem sound; the done sound if a job finished)."""
        if not self.toast.notify(title, detail, linger_ms=4000):
            self.tray.showMessage(title, detail, QSystemTrayIcon.Information, 4000)
        if done:
            self.play_done_sound()

    def read_pdfs(self, paths: list[str]):
        """PDFs double-clicked (or sent with --read): Rakiza Core PDF's reader, each in a tab."""
        files = [Path(p) for p in paths if os.path.isfile(p) and p.lower().endswith(".pdf")]
        if not files or not self._tool_here("pdf"):
            return
        self.show_window("PDF")
        page = getattr(self.window, "pdf_page", None)
        if page is not None:
            page.open_reader(files)

    def open_viewer(self, path: Path):
        """Opens a picture (with the rest of its folder) or an email file in Rakiza Core Viewer."""
        from .viewer import is_picture
        if not path.is_file() or not self._tool_here("viewer"):
            return
        self.viewers = [v for v in self.viewers if _alive(v)]
        if path.suffix.lower() in (".eml", ".msg"):
            from . import mail
            from .mailview import MailWindow
            try:
                win = MailWindow(self, path, self.icon)
            except (mail.MailError, OSError) as e:
                from .converters import friendly_error
                self.tell(tr("Couldn't open the email"), f"{short_name(path.name)}: {friendly_error(e)}")
                return
        elif is_picture(path):
            for v in self.viewers:  # a picture window is open already: show it there
                if hasattr(v, "open") and not v.isFullScreen() and v.isVisible():
                    if v._leave_ok():
                        v.open(path)
                        v.showNormal()
                        v.raise_()
                        v.activateWindow()
                        self.remember_view(path)
                    return
            from .viewer import ViewerWindow
            win = ViewerWindow(self, path, self.icon)
        else:
            self.tell(tr("Can't open this here"), short_name(path.name))
            return
        self.viewers.append(win)
        self._let_go_when_closed(win)
        self.remember_view(path)
        win.show()
        win.raise_()
        win.activateWindow()

    def _tool_here(self, tool_id: str) -> bool:
        """A tool asked for from outside its own page (a file opened with it, another part of Rakiza Core): True if
        it's in use. If it's resting for now, that's said (once in a while) instead."""
        if all_tools.is_on(self.settings, tool_id):
            return True
        tool = all_tools.BY_ID[tool_id]
        log.info("%s asked for, but it's %s", tool_id, all_tools.state(tool_id))
        if all_tools.state(tool_id) == all_tools.PAUSED and time.time() - getattr(self, "_rest_told", 0) > 30:
            self._rest_told = time.time()
            self.toast.notify(tr(tool.name), tr("Resting for now while it's being made better. It comes back in a "
                                                "future update, with your settings kept."), linger_ms=7000)
        return False

    def played_elsewhere(self, path: Path):
        """A video or song sent with --play: Rakiza Core's Player has left Rakiza Core (it moves to Rakiza Play), so an
        old double-click that still points here asks Windows which app should play it instead."""
        if not path.is_file():
            return
        log.info("--play arrived, but the Player isn't part of Rakiza Core any more")
        self.sync_archive_opening()  # (takes out the old "Open with" entry, if it's still there)
        try:
            subprocess.Popen([system_tool("rundll32.exe"), "shell32.dll,OpenAs_RunDLL", str(path)],
                             creationflags=NO_WINDOW)
        except OSError:
            log.exception("couldn't ask Windows which app plays %s", path)

    def remember_view(self, path: Path):
        from .settings import RECENT_ARCHIVES
        try:
            if path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
                return
        except OSError:
            return
        key = os.path.normcase(str(path))
        rest = [p for p in self.settings.recent_views if os.path.normcase(p) != key]
        self.settings.recent_views = [str(path), *rest][:RECENT_ARCHIVES]
        self.settings.save()
        w = self._window
        if w is not None and hasattr(w, "refresh_views"):
            w.refresh_views()

    def browse_from_menu(self, path: str):
        p = Path(path)
        if p.exists():
            self.open_browser(p)

    # ---- settings

    def apply_theme(self, wheel_only: bool = False):
        if wheel_only:
            self._apply_theme(True)
            return
        from .atomic import motion  # (the window goes softly from the old look to the new one)
        motion.cross_fade(self._window, self._apply_theme, animate=self.settings.animations)

    def _apply_theme(self, wheel_only: bool = False):
        self.theme = self._make_theme()
        if not wheel_only:
            if self._window is not None:  # (restyling goes through every page made: the hidden ones go first)
                self._window.drop_hidden_pages()
            # restyles every window: only when it changes, and drawn once at the end instead of widget by widget
            shown = [w for w in self.app.topLevelWidgets() if w.isVisible()]
            for w in shown:
                w.setUpdatesEnabled(False)
            try:
                self._restyle(self._css())
            finally:
                for w in shown:
                    if _alive(w):
                        w.setUpdatesEnabled(True)
        self.wheel.theme = self.theme
        if not wheel_only:
            self._color_menu_icons()
        if self._window is not None:
            self._window.apply_theme(self.theme)
        if self.clip is not None and self.clip.popup is not None:
            self.clip.popup.apply_theme(self.theme)
        if self.quick is not None:
            self.quick.apply_theme(self.theme)
        if self.peek is not None:
            self.peek.apply_theme(self.theme)

    def _restyle(self, css: str):
        """A new look for every widget. Going from one look straight to another costs Qt about three
        times as long as going through none (each widget is undone and redone against both looks),
        so it goes through none: no picture is drawn in between."""
        with sheet.quiet():
            self.app.setStyleSheet("")
            self.app.setStyleSheet(css)

    def _color_menu_icons(self):
        for action, name in self.menu_icons:
            action.setIcon(icons.icon(name, self.theme.muted, selected=self.theme.text))

    def _system_theme_changed(self, *_):
        if self.settings.appearance == "system":
            self.apply_theme()

    def _apply_toast_settings(self):
        self.toast.corner = self.settings.toast_corner
        self.toast.time_scale = TOAST_TIME_SCALE.get(self.settings.toast_time, 1.0)

    def settings_changed(self, menu: bool = False):
        """Saves the settings (a moment later, so dragging a slider doesn't write the file 50 times).
        menu=True when the change also shows in the right-click menu."""
        self.save_timer.start(400)
        self._apply_toast_settings()
        if self.capture is not None and self._cap_settings() != getattr(self, "_cap_applied", None):
            self._cap_applied = self._cap_settings()
            self.capture.apply_settings()
            if self._window is not None:
                self._window.refresh_capture_note()
                self._window.refresh_captures()
        if self.colors is not None and self.settings.colors_shortcut != getattr(self, "_colors_applied", None):
            self._colors_applied = self.settings.colors_shortcut
            self.colors.apply_settings()
        if self.clip is not None and self._clip_settings() != getattr(self, "_clip_applied", None):
            self._clip_applied = self._clip_settings()
            self.clip.apply_settings()
            if self._window is not None:
                self._window.refresh_clipboard_note()
        if self._quick_settings() != getattr(self, "_quick_applied", None):
            self._quick_applied = self._quick_settings()
            self._quick_tool()
            if self.quick is not None:
                self.quick.apply_settings()
            self._tray_tools()
            btn = getattr(self._window, "quick_btn", None)
            if btn is not None:
                btn.setVisible(bool(self.settings.quick_menu))
            notes = getattr(self._window, "quick_notes", None)
            if notes is not None:
                notes()
        self._peek_tool()  # (nothing happens unless its shortcut changed)
        self.watcher.refresh()
        if self._window is not None:
            self._window.refresh()
        self._update_tooltip()
        if menu:
            self.update_shell_menu()

    def keep_now(self, key: str, value):
        """A setting chosen outside Settings (the Viewer's sort menu, the quick button's own menu): kept
        straight away, never held back with the window's changes that wait for Save."""
        setattr(self.settings, key, value)
        if settings_mod.HELD is not None:
            settings_mod.HELD[key] = copy.deepcopy(value)
        if self._window is not None:
            self._window.committed[key] = copy.deepcopy(value)
        self.settings_changed()

    def set_enabled(self, on: bool):
        self.settings.enabled = on
        self.act_pause.blockSignals(True)
        self.act_pause.setChecked(not on)
        self.act_pause.blockSignals(False)
        if self._window is not None:
            self._window.sync()
        self.settings_changed()

    def switch_language(self, choice: str):
        """Puts Rakiza Core in another language straight away, with no restart: the window, the tray menu,
        popups and the right-click menu. (Windows like the Viewer that are open keep their words until
        they're opened again.)"""
        s = self.settings
        s.language = choice
        if settings_mod.HELD is not None:  # other changes may still wait for Save: this one doesn't
            settings_mod.HELD["language"] = choice
        w = self._window
        if w is not None:
            w.committed["language"] = choice
        s.save()
        if i18n.resolve(choice) == i18n.language():  # e.g. "Match Windows" while Windows is in this language
            if w is not None:
                w.show_language()
            return
        lang = i18n.set_language(choice)
        log.info("language changed to %s", lang)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        # (the new window starts out showing how the old one looked, and fades into the new words)
        self._fade_from = w.grab() if w is not None and w.isVisible() and self.settings.animations else None
        try:
            self._show_language(lang, w)
        finally:
            QApplication.restoreOverrideCursor()

    def _show_language(self, lang: str, w):
        if w is not None:
            w.setUpdatesEnabled(False)  # (the old window isn't drawn half changed before the new one shows)
        self.app.setLayoutDirection(Qt.RightToLeft if i18n.is_rtl() else Qt.LeftToRight)
        load_qt_translations(self.app, lang)
        css = self._css()
        if css != self.app.styleSheet():  # (only right-to-left changes it: restyling everything is slow)
            self._restyle(css)
        self._build_tray_menu()
        self._update_tooltip()
        self.toast.retranslate()
        self._free_popups()   # (the quick menu, wheel and clipboard popup are made again in the new language)
        self.update_shell_menu(500)
        if w is not None:
            self.rebuild_window(w.current_page(), committed=w.committed, pending_startup=w.pending_startup)

    def restart(self, page: str = "General"):
        """Starts a fresh copy of Rakiza Core."""
        if not self.unsaved_work_ok():  # (Cancel: the settings are saved, the next start uses them)
            return
        if self.busy_with_files():  # (a sort or a move stopped half-way would leave files half-done)
            self.toast.notify(tr("Restart later"), tr("Rakiza Core restarts when the conversion is done."),
                              linger_ms=4000)
            QTimer.singleShot(2000, lambda: self.restart(page))
            return
        self.settings.save()
        if self.server:
            self.server.close()
        winutil.release_mutex()
        try:
            subprocess.Popen(app_command("--restarted", "--page", page))
        except OSError:
            log.exception("could not restart")
            winutil.create_mutex()
            if self.server:
                self.server.listen(ipc.SERVER_NAME)  # (right-clicked files reach this copy again)
            return
        self.quit()

    # ---- jobs

    def tool_chosen(self, tool: Tool, files: list[Path]):
        """Called when a slice is picked. Waits until the drop has finished (Explorer is
        still in its drag loop during dropEvent), then asks any questions and converts."""
        QTimer.singleShot(0, lambda: self._ask_and_start(tool, files))

    def _ask_and_start(self, tool: Tool, files: list[Path]):
        dark = self.theme.dark
        if tool.opens == "browse":
            self.open_browser(files[0])
            return
        if tool.target and len(files) > 1:
            # "Convert to PNG" with some PNGs among the files: leave those alone instead of copying them
            others = [f for f in files if norm(f.suffix) != tool.target]
            files = others or files
        params: dict = {}
        # No password question up front (that meant reading every archive first, which froze the
        # window on big ones): if one is needed, the job says so and job_done() asks.
        if tool.ask == "folder":  # "Extract to…": where (as in 7-Zip and WinRAR)
            from PySide6.QtWidgets import QFileDialog
            chosen = QFileDialog.getExistingDirectory(None, tool.description, str(files[0].parent))
            if not chosen:
                return
            params["dest"] = chosen
        elif tool.ask:
            extra = {}
            if tool.ask == "fit":
                extra["last_mb"] = self.settings.fit_mb
            elif tool.ask == "watermark":
                extra["last"] = self.settings.watermark
            answer = dialogs.ask(tool.ask, files, self.icon, dark, **extra)
            if answer is None:
                return
            params.update(answer)
            if tool.ask == "fit":
                self.settings.fit_mb = float(answer.get("mb", 10))
                self.settings.save()
            elif tool.ask == "watermark":
                self.settings.watermark = dict(answer)
                self.settings.save()
        rename: dict = {}
        archive_name = params.pop("rename_base", "")  # the "Make an archive" window has its own name box
        if archive_name:
            rename = {"rename_base": archive_name}
        elif self.settings.ask_names and tool.renames and len(files) > 1:
            # tools that put all the files into one new file get one name, without numbers
            answer = dialogs.ask("rename", files, self.icon, dark, ext=OUTPUT_EXT.get(tool.key, tool.target or ""),
                                 single=tool.batch)
            if answer is None:
                return
            rename = answer
        self.start_job(tool, files, params, rename)

    def start_job(self, tool: Tool, files: list[Path], params: dict | None = None, rename: dict | None = None,
                  after: str | None = None, carry: dict | None = None, then=None):
        """then(ok) is called once the job is over: ok when it finished, made something and nothing failed (the
        PDF editor counts its changes as saved only then)."""
        if self.quit_pending:
            self.toast.notify(tr("Rakiza Core is closing for an update"), tr("Try again in a moment, when it's back."),
                              linger_ms=4000)
            if then is not None:
                then(False)
            return
        self.tidy_timer.stop()
        job_id = next(self.ids)
        cancel = threading.Event()
        quick = tool.key.startswith("archive:open")
        waiting = not quick and any(not j["quick"] for j in self.jobs.values()) and self.settings.one_at_a_time
        self.cancels[job_id] = cancel
        params = params or {}
        what = short_name(files[0].name) if len(files) == 1 else tr("{count} items", count=len(files))
        self.jobs[job_id] = {"tool": tool, "files": files, "bytes_in": 0,
                             "params": params, "rename": rename or {}, "after": after, "quick": quick,
                             "what": what, "carry": carry or {"outputs": [], "errors": []}, "then": then}
        save_to = self.files_work.save_to  # (Files' "Save to", for what's started from its side panel)
        if not quick and after not in ("open", "open_with", "reload"):  # (opening a file from an archive isn't one)
            self.files_work.job_added(job_id, tool, (carry or {}).get("files") or files, params, rename or {}, what)
        if self.settings.show_progress:
            self.toast.start(job_id, tool.description,
                             tr("{what} · waiting for the one before it", what=what) if waiting else what)
        options = self.settings.options(rename.get("rename_base", "") if rename else "",
                                        rename.get("rename_start", 1) if rename else 1)
        if save_to is not None and not quick:
            options.output_folder = Path(save_to) if save_to else None
        (self.quick_pool if quick else self.pool).start(Job(job_id, tool, files, options, self.signals, cancel,
                                                            params, self.jobs[job_id]))
        self._update_tooltip()
        log.info("job %s: %s on %d item(s)", job_id, tool.key, len(files))

    def _job_started(self, job_id: int):
        job = self.jobs.get(job_id)
        if job:
            self.toast.set_detail(job_id, job["what"])

    def _job_item(self, job_id: int, number: int, total: int, name: str):
        text = tr("{number} of {total} · {name}", number=number, total=total, name=short_name(name))
        self.toast.set_detail(job_id, text)
        self.files_work.job_item(job_id, text)

    def cancel_job(self, job_id: int):
        if job_id == UPDATE_JOB_ID:
            if self.update_cancel:
                self.update_cancel.set()
            return
        ev = self.cancels.get(job_id)
        if ev:
            ev.set()

    def cancel_all(self):
        for ev in self.cancels.values():
            ev.set()

    def job_done(self, job_id: int, result: Result, errors: list[str], cancelled: bool):
        self.cancels.pop(job_id, None)
        job = self.jobs.pop(job_id, None) or {"tool": None, "files": [], "bytes_in": 0, "params": {}, "rename": {},
                                               "after": None}
        tool, files = job["tool"], job["files"]
        self._update_tooltip()
        if not self.cancels:
            self.tidy_timer.start(IDLE_TIDY_MS)
            self.light_tidy_timer.start(LIGHT_TIDY_MS)

        # a password is needed (or was wrong): ask, then try again with only the files that need it,
        # so files that already worked aren't done twice
        password_error = tr(PASSWORD_ERROR)
        carry = job.get("carry") or {"outputs": [], "errors": []}
        if tool and tool.password and result.locked and not cancelled:
            retry = list(files) if tool.batch else [f for f in result.locked if isinstance(f, Path)]
            self.toast.hide()
            password = self.ask_password(retry or files, wrong=bool(job["params"].get("password")))
            if password is not None:
                for b in self.browsers:
                    if _alive(b) and retry and b.src == retry[0]:
                        b.password = password
                keep = {"outputs": carry["outputs"] + result.outputs,
                        "errors": carry["errors"] + [e for e in errors if password_error not in e],
                        "files": carry.get("files") or files, "bytes_in": carry.get("bytes_in") or job["bytes_in"]}
                self.files_work.job_retried(job_id)
                self.start_job(tool, retry or files, dict(job["params"], password=password), job["rename"],
                               job["after"], carry=keep, then=job.get("then"))
                return
        then = job.get("then")
        if then is not None:
            try:
                then(not cancelled and not errors and bool(result.outputs or carry["outputs"]))
            except RuntimeError:  # (the window that asked has been closed meanwhile)
                pass
        if carry["outputs"] or carry["errors"]:  # put together with the tries before this one
            result.outputs = carry["outputs"] + result.outputs
            errors = carry["errors"] + errors
        if carry.get("files"):  # the history counts every file of the job, not just the retried ones
            files = carry["files"]
            job["bytes_in"] = carry.get("bytes_in", job["bytes_in"])
        self.recent_outputs = (self.recent_outputs + [str(p) for p in result.outputs])[-200:]
        self.files_work.job_finished(job_id, result.outputs, errors, cancelled, result.note)

        after = job["after"]
        if after in ("open", "open_with") and result.outputs and not errors:
            self.toast.hide()
            try:
                if after == "open_with":
                    winutil.open_with(result.outputs[0])
                else:
                    os_open(result.outputs[0])
            except OSError as e:
                from .converters import friendly_error
                self.toast.notify(tr("Couldn't open the file"), friendly_error(e))
            member = job["params"].get("member")
            for b in self.browsers:  # it watches the file, to offer saving changes back into the archive
                if member and _alive(b) and files and b.src == files[0]:
                    b.opened(member, result.outputs[0])
            return
        if after == "reload":
            for b in self.browsers:
                if _alive(b) and files and b.src == files[0]:
                    b.reload()
        record = tool and not cancelled and not tool.key.startswith(("archive:open", "archive:test"))
        if record:
            self.history.record(tool.description, files, result.outputs, len(errors), job["bytes_in"])
            if result.outputs or (result.note and not errors):
                self.remember_action(tool, job["params"], job["rename"])
            if self._window is not None and self._window.isVisible():
                self._window.refresh_history()
        if result.clipboard:
            QGuiApplication.clipboard().setText(result.clipboard)
        first = result.outputs[0] if result.outputs else None
        # a whole archive came out without a problem: what Settings › Archives says happens next
        extracted = bool(tool and tool.key in ("archive:extract", "archive:folder", "archive:to") and not cancelled
                         and not errors and result.outputs and not job["params"].get("members"))
        recycled = self._recycle_extracted(files) if extracted and self.settings.extract_recycle else 0
        # converted to another format: the originals can go (Settings › Converter); each only when it came out
        originals = 0
        if tool and tool.target and not tool.key.startswith("archive:") and self.settings.convert_recycle \
                and not cancelled and result.sources:
            originals = self._recycle_converted(result.sources, result.outputs)

        if cancelled:
            title, detail, linger = tr("Cancelled"), tr("Nothing was saved for the unfinished file."), 3000
        elif errors:
            title = tr("Couldn't convert") if not result.outputs else tr("Some files couldn't be converted")
            more = "\n" + tr("…and {count} more", count=len(errors) - 3) if len(errors) > 3 else ""
            detail, linger = "\n".join(errors[:3]) + more, 9000
        elif result.note:
            title, detail, linger = tr("Done"), result.note, 5000
        elif result.outputs:
            title = tr("Done")
            if len(result.outputs) == 1:
                detail = tr("Saved {name}", name=short_name(first.name))
                after_size = size_of(first)
                if len(files) == 1 and after_size and job["bytes_in"] and first.is_file():
                    detail += "\n" + tr("{before} → {after}", before=human_size(job["bytes_in"]),
                                         after=human_size(after_size))
            else:
                detail = tr("Saved {count} items", count=len(result.outputs))
            linger = 4500
        else:
            title, detail, linger = tr("Nothing to do"), "", 3000
        elsewhere = "" if cancelled else self._saved_elsewhere(files, result.outputs)
        if elsewhere:  # (looked for beside the original, it wouldn't be found)
            detail += "\n" + tr("The folder it was meant for can't be saved in, so it's in {folder}", folder=elsewhere)
        if recycled:
            detail += "\n" + tr("The archive went to the Recycle Bin") if recycled == 1 else \
                "\n" + tr("{count} files went to the Recycle Bin", count=recycled)
        if originals:
            detail += "\n" + (tr("The original went to the Recycle Bin") if originals == 1 else
                              tr("{count} originals went to the Recycle Bin", count=originals))

        extra_text, extra = "", None
        if result.undo and not cancelled:
            done = list(result.undo)
            extra_text, extra = tr("Undo"), lambda: self.undo(done)
        elif self._can_compare(files, result.outputs, errors, cancelled):
            before, after_file = files[0], result.outputs[0]
            extra_text, extra = tr("Compare"), lambda: self.compare(before, after_file)
        elif result.scans and not cancelled and all_tools.is_on(self.settings, "pdf"):
            scans = list(result.scans)  # (no text to copy: reading the words on the pages is one click away)
            extra_text, extra = tr("Make searchable"), lambda: self.make_searchable(scans)
        important = bool(errors or result.note or result.undo or elsewhere)
        if self.settings.show_progress or important:
            shown = self.toast.finish(job_id, title, detail, first, linger, single=len(result.outputs) == 1,
                                      extra_text=extra_text, extra=extra)
            if not shown and important:
                icon = QSystemTrayIcon.Warning if errors else QSystemTrayIcon.Information
                self.tray.showMessage(title, detail, icon)
        if not cancelled and (result.outputs or result.note) and not (errors and not result.outputs):
            self.play_done_sound()
        elif errors and not cancelled:
            self.play_problem_sound()
        if first and (self.settings.reveal_after or (extracted and self.settings.extract_reveal)) \
                and not errors and not cancelled:
            winutil.reveal_in_explorer(first)

    def make_searchable(self, files: list[Path]):
        """Rakiza Core PDF's Make searchable for these PDFs (offered when there was no text to copy)."""
        self.show_window("PDF")
        page = getattr(self.window, "pdf_page", None)
        if page is not None:
            page.run("searchable", [Path(f) for f in files if Path(f).exists()])

    def _saved_elsewhere(self, files: list, outputs: list) -> str:
        """'Downloads › Rakiza Core' when what was made went there because the folder it was meant for can't be
        saved in (a CD, a read-only or protected folder); '' when everything is where it was meant to go."""
        spare = os.path.normcase(str(fallback_folder()))
        if self.settings.save_mode == "folder":
            meant = [os.path.normcase(self.settings.output_folder)]
        else:
            meant = [os.path.normcase(str(Path(f).parent)) for f in files if isinstance(f, (str, Path))]

        def inside(path: str, top: str) -> bool:
            return path == top or path.startswith(top.rstrip(os.sep) + os.sep)
        for o in outputs:
            where = os.path.normcase(str(Path(o).parent))
            if inside(where, spare) and not any(inside(where, m) for m in meant):
                return f"{fallback_folder().parent.name} › {fallback_folder().name}"
        return ""

    def _recycle_extracted(self, archives: list[Path]) -> int:
        """Sends the extracted archives, each with all its parts, to the Recycle Bin (closing the
        Archive windows that show them). Returns how many files went."""
        from . import archives as ar
        from .space import recycle
        kept = set()
        for b in list(self.browsers):
            if _alive(b) and b.src in archives and not b.close():  # (it asked about unsaved changes: Cancel)
                kept.add(b.src)
        parts = [p for a in archives if a not in kept for p in ar.archive_parts(Path(a))]
        try:
            return recycle(parts)
        except Exception:
            log.exception("couldn't send the extracted archives to the Recycle Bin")
            return 0

    def _recycle_converted(self, sources: list[Path], outputs: list[Path]) -> int:
        """Sends the converted originals to the Recycle Bin (never one that is also a result, or whose
        result isn't there). Returns how many went."""
        from .space import recycle
        made = {os.path.normcase(str(p)) for p in outputs if Path(p).exists()}
        if not made:
            return 0
        go = [Path(p) for p in sources if os.path.normcase(str(p)) not in made and Path(p).is_file()]
        try:
            return recycle(go)
        except Exception:
            log.exception("couldn't send the converted originals to the Recycle Bin")
            return 0

    def first_start(self):
        """The very first start: which tools this person wants, then the short tour."""
        from .store import ask_tools
        picked = ask_tools(self.icon, self.theme.dark)
        s = self.settings
        # (tools resting for now weren't offered: they're offered, switched off, when their section is back)
        ready = {tool.id for tool in all_tools.available()}
        s.known_tools = sorted(set(s.known_tools) | ready)
        if picked is not None:
            s.tools_off = sorted((set(s.tools_off) | ready) - picked)
        s.save()
        self.apply_tools()
        if self._window is not None:
            self.rebuild_window("Home")
        if not s.tour_done:
            QTimer.singleShot(300, self.show_tour)

    def show_tour(self):
        from .tour import Tour
        dlg = Tour(self.icon, self.settings.shortcut_label, self.theme.accent)
        sheet.run(dlg)
        dlg.deleteLater()
        if not self.settings.tour_done:
            self.settings.tour_done = True
            self.settings_changed()

    # ---- after a job: undo, compare, do it again

    def undo(self, done: list):
        from .moretools import undo_rename
        back = undo_rename(done)
        if back:
            self.toast.notify(tr("Undone"), tr("{count} files have their old names back", count=back),
                              linger_ms=3500)
        else:
            self.tell(tr("Couldn't undo"), tr("The files were moved or renamed again since."))

    @staticmethod
    def _can_compare(files: list[Path], outputs: list[Path], errors: list, cancelled: bool) -> bool:
        if cancelled or errors or len(files) != 1 or len(outputs) != 1:
            return False
        a, b = files[0], outputs[0]
        try:
            return a.is_file() and b.is_file() and category(a) == "image" and category(b) == "image"
        except OSError:
            return False

    def compare(self, before: Path, after: Path):
        from .compare import CompareWindow
        if not (before.exists() and after.exists()):
            self.tell(tr("Can't compare"), tr("One of the files isn't there any more."))
            return
        win = CompareWindow(before, after, self.icon)
        self.compares = [w for w in self.compares if _alive(w)] + [win]
        win.show()
        win.raise_()
        win.activateWindow()

    # params that only make sense for the files they were chosen for (or are secret) aren't kept
    NOT_REPEATED = {"password", "new_password", "pages", "passwords"}

    def remember_action(self, tool: Tool, params: dict, rename: dict):
        if tool.key.startswith("pdftool:"):  # (Rakiza Core PDF's page: "Do it again" stays the Converter's)
            return
        kept = {k: v for k, v in params.items() if k not in self.NOT_REPEATED}
        try:
            import json
            json.dumps(kept)
        except (TypeError, ValueError):
            kept = {}
        self.settings.last_action = {"key": tool.key, "params": kept,
                                     "rename": {k: v for k, v in (rename or {}).items()
                                                if isinstance(v, (str, int))}}
        self.save_timer.start(400)
        if self._window is not None:
            self._window.refresh_again()

    def again_text(self) -> str:
        """ "Do it again: Convert to PNG…", or "" when there's nothing to repeat yet."""
        key = self.settings.last_action.get("key")
        if not key:
            return ""
        for multi in (False, True):
            for t in _all_tools(multi):
                if t.key == key:
                    return tr("Do it again: {what}…", what=t.description)
        return ""

    def _update_again_action(self):
        text = self.again_text()
        self.act_again.setVisible(bool(text) and all_tools.is_on(self.settings, "converter"))
        if text:
            self.act_again.setText(text)

    def do_again(self, files: list[Path] | None = None):
        """Runs the last action again, with the same choices, on other files."""
        la = self.settings.last_action
        if not la.get("key"):
            return
        if not files:
            from PySide6.QtWidgets import QFileDialog
            title = self.again_text()
            if la["key"].startswith("folder:"):
                folder = QFileDialog.getExistingDirectory(None, title)
                files = [Path(folder)] if folder else []
            else:
                names, _ = QFileDialog.getOpenFileNames(None, title)
                files = [Path(n) for n in names]
            if not files:
                return
        pk = pick(files, frozenset(), (), True, None, True)
        slot = next((sl for sl in pk.slots if sl.tool.key == la["key"]), None)
        if slot is None:
            self.tell(tr("Can't do that here"), tr("This option doesn't work for these files."))
            return
        if not slot.ok:
            self.tell(slot.tool.description, slot.reason or "")
            return
        tool, params = slot.tool, dict(la.get("params") or {})
        if tool.ask and (tool.ask in ("lock", "pages") or not params):
            self.tool_chosen(tool, pk.files)  # these need fresh answers
            return
        self.start_job(tool, pk.files, params, dict(la.get("rename") or {}))

    # ---- updates

    def updates_supported(self) -> bool:
        return bool(UPDATE_REPO) and __version__ != "dev"

    def can_self_install(self) -> bool:
        return installed_uninstaller() is not None

    def _refresh_updates_page(self):
        if self._window is not None:
            self._window.refresh_updates()

    def update_waiting(self) -> bool:
        """A newer version was found and isn't being installed yet."""
        return self.update_state == "available" and self.update_info is not None

    def _show_update_marks(self):
        """While a new version waits: a line at the top of the tray menu, in the tray tip, on the Home page
        and on the Updates row of the sidebar (so a missed message isn't the only sign)."""
        waiting = self.update_waiting()
        if hasattr(self, "act_update"):
            if waiting:
                self.act_update.setText(tr("Update to Rakiza Core {version}", version=self.update_info.version)
                                        if self.can_self_install() else
                                        tr("Get Rakiza Core {version}", version=self.update_info.version))
            self.act_update.setVisible(waiting)
            self.act_update_sep.setVisible(waiting)
        if hasattr(self, "act_cancel"):
            self._update_tooltip()
        if self._window is not None:
            self._window.show_update_marks()

    def _tell_update(self):
        """Say once a day that a new version is out (not while a game or presentation is full screen):
        in Rakiza Core's own corner message, or as a Windows notification if that one is busy."""
        info = self.update_info
        if not self.update_waiting() or not self.settings.check_updates:
            return
        told = f"{info.version} {time.strftime('%Y-%m-%d')}"
        if self.settings.update_told == told:
            return
        if winutil.user_busy():
            self.update_retell.start(UPDATE_RETELL_MS)
            return
        title = tr("Rakiza Core {version} is available", version=info.version)
        detail = tr("One click to update. Your settings are kept.")
        if not self.toast.notify(title, detail, tr("Update now") if self.can_self_install() else tr("Get it"),
                                 self.start_update, 15000):
            self.tray.showMessage(title, detail, QSystemTrayIcon.Information, 15000)
            self.update_message_at = time.monotonic()
        self.settings.update_told = told
        self.save_timer.start(400)

    def _tray_message_clicked(self):
        if self.update_waiting() and time.monotonic() - self.update_message_at < 120:
            self.show_window("Updates")

    def check_updates(self, manual: bool = False):
        manual = manual is True  # timer/menu signals may pass other values
        if not self.updates_supported():
            if manual:
                self.toast.notify(tr("Updates aren't set up"), tr("This copy wasn't built with an update source."))
            return
        if (not manual and not self.settings.check_updates) or self.update_state in ("checking", "downloading"):
            return
        self.update_state = "checking"
        self._refresh_updates_page()

        def work():
            info, err = None, ""
            try:
                info = updater.check(UPDATE_REPO, __version__)
            except updater.UpdateError as e:
                err = str(e)
            except Exception:
                log.exception("update check failed")
                err = tr("something went wrong")
            self.usignals.checked.emit(info, err, manual)
        threading.Thread(target=work, daemon=True).start()

    def _update_checked(self, info, err: str, manual: bool):
        if err:
            # (offline later on: the version found before still waits)
            self.update_state = "available" if self.update_info else "error"
            self.update_error = err
            log.info("update check: %s", err)
            if self.settings.check_updates and not self.update_retry.isActive():
                wait = UPDATE_RETRY_MS[min(self.update_retries, len(UPDATE_RETRY_MS) - 1)]
                self.update_retries += 1
                self.update_retry.start(wait * 60_000)
                log.info("update check again in %d min", wait)
            if manual:
                self.toast.notify(tr("Couldn't check for updates"), err)
        elif info:
            self.update_retries = 0
            if not (self.update_info and self.update_info.version == info.version):
                log.info("update available: %s", info.version)
            self.update_state, self.update_info = "available", info
            if self.settings.auto_update and self.can_self_install():
                if not manual and winutil.user_busy():  # a game or presentation is full screen: install a bit later
                    self.update_busy_wait.start(15 * 60_000)
                    self._refresh_updates_page()
                    self._show_update_marks()
                    return
                self._refresh_updates_page()
                self.start_update()
                return
            if manual:
                self.settings.update_told = ""
            self._tell_update()
        else:
            self.update_retries = 0
            self.update_state, self.update_info = "uptodate", None
            if manual:
                self.toast.notify(tr("You're up to date"),
                                  tr("Rakiza Core {version} is the newest version.", version=__version__),
                                  linger_ms=4000)
        self._refresh_updates_page()
        self._show_update_marks()

    def update_options_changed(self, checks_were_on: bool, auto_was_on: bool):
        """The Updates page's switches. Turned on, they act now: a version that's already waiting is installed
        (or a check is made) instead of only at the next check, up to 6 hours later."""
        s = self.settings
        if not s.check_updates:
            self.update_retry.stop()
            return
        if s.auto_update and not auto_was_on and self.update_waiting() and self.can_self_install():
            log.info("installing updates by themselves was turned on: updating to %s", self.update_info.version)
            self.start_update()
        elif (s.auto_update and not auto_was_on) or not checks_were_on:
            self.check_updates()

    def start_update(self):
        info = self.update_info
        if not info or self.update_state == "downloading":
            return
        if not self.can_self_install():  # portable copy: open the download page instead
            QDesktopServices.openUrl(QUrl(info.page_url))
            return
        self.update_state = "downloading"
        self.update_cancel = threading.Event()
        self.toast.start(UPDATE_JOB_ID, tr("Downloading Rakiza Core {version}", version=info.version),
                         tr("Rakiza Core restarts by itself when it's done."))
        self._refresh_updates_page()
        self._show_update_marks()
        if not getattr(self, "_silent_failed", False) and updater.start_silent_update():
            # the update task (installed with Rakiza Core) downloads and installs it: Windows doesn't ask
            log.info("update: the update task was started")
            self._silent_since = time.time() - 1
            self._silent_seen = ("", time.monotonic())
            self._silent_timer = QTimer(self)
            self._silent_timer.timeout.connect(self._watch_silent_update)
            self._silent_timer.start(500)
            return
        self._download_update()

    def _download_update(self):
        """The update the way it always went: Rakiza Core downloads the installer, and Windows asks for permission."""
        info = self.update_info
        cancel = self.update_cancel

        def work():
            try:
                path = updater.download(info, __version__,
                                        lambda f: self.usignals.progress.emit(UPDATE_JOB_ID, f), cancel)
                self.usignals.downloaded.emit(str(path))
            except updater.UpdateError as e:
                self.usignals.failed.emit(str(e))
            except Exception:
                log.exception("update download failed")
                self.usignals.failed.emit(tr("something went wrong"))
        threading.Thread(target=work, daemon=True).start()

    def _watch_silent_update(self):
        """What the update task says: its download in the popup; "ready": Rakiza Core closes (once what it's doing
        is done) and the task installs the update and starts it again. A task that doesn't run, or a problem before
        it's ready: the update goes the usual way (Windows asks)."""
        status = updater.silent_status(self._silent_since) or ""
        cancel = self.update_cancel
        if status != self._silent_seen[0]:
            self._silent_seen = (status, time.monotonic())
            log.info("update task: %s", status)
        quiet = time.monotonic() - self._silent_seen[1]
        if cancel is not None and cancel.is_set():  # (the task gives up by itself if Rakiza Core doesn't close)
            self._silent_timer.stop()
            self._update_failed(updater.CANCELLED)
            return
        progress = updater.silent_progress(status)
        if progress is not None:
            self.toast.set_progress(UPDATE_JOB_ID, progress)
        if status == "ready":
            self._silent_timer.stop()
            self._silent_ready()
        elif status.startswith(("failed", "done")) or (not status and quiet > 30) or quiet > 300:
            # (it didn't run, it ran into a problem, or it went quiet: the usual way, once, until Rakiza Core restarts)
            self._silent_timer.stop()
            log.warning("update task didn't install it (%s): the usual way now", status or "it didn't start")
            self._silent_failed = True
            self._download_update()

    def _silent_ready(self):
        if self.update_cancel is not None and self.update_cancel.is_set():
            self._update_failed(updater.CANCELLED)
            return
        if self.busy_with_files():  # let running conversions (or an uninstall, a sort…) finish first
            QTimer.singleShot(2000, self._silent_ready)
            return
        self.toast.finish(UPDATE_JOB_ID, tr("Installing the update…"), tr("Rakiza Core will be back in a moment."),
                          None, 5000)
        if self.update_info is not None:  # (checked at the next start: did it really update?)
            self.settings.update_to = self.update_info.version
            self.settings.save()
        from . import settings_backup
        settings_backup.keep_copy(__version__)  # the settings as they were, in case the new version loses them
        log.info("closing for the update task")
        QTimer.singleShot(1500, self.quit)  # (the popup is seen first)

    def busy_with_files(self) -> bool:
        """Something is converting, sorting, moving or changing files: stopped now, it'd be left half-done."""
        return bool(self.cancels) or bool(self.uninstall_busy) or page_work.busy()

    def quit_for_installer(self):
        """The installer asks Rakiza Core to close (to update or remove it). What's running finishes first and
        nothing new starts; the installer waits for it (and only stops Rakiza Core itself if it never closes)."""
        if not self.quit_pending:
            self.quit_pending = True
            log.info("the installer asked Rakiza Core to close")
            try:  # (tells the installer it was heard: older versions don't answer, so it stops those itself)
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                (LOG_DIR / "closing-for-installer").write_text(str(os.getpid()), encoding="utf-8")
            except OSError:
                pass
            if self.busy_with_files():
                self.toast.notify(tr("Closing for the update"),
                                  tr("Rakiza Core closes as soon as what it's doing is finished."), linger_ms=6000)
        if self.busy_with_files():
            QTimer.singleShot(500, self.quit_for_installer)
            return
        self.quit()

    def _update_downloaded(self, path: str):
        if self.busy_with_files():  # let running conversions (or an uninstall, a sort…) finish first
            QTimer.singleShot(2000, lambda: self._update_downloaded(path))
            return
        self.toast.finish(UPDATE_JOB_ID, tr("Installing the update…"), tr("Rakiza Core will be back in a moment."),
                          None, 5000)
        winutil.release_mutex()
        if self.update_info is not None:  # (checked at the next start: did it really update?)
            self.settings.update_to = self.update_info.version
            self.settings.save()
        try:
            installer = updater.run_installer(Path(path))
        except OSError:
            log.exception("could not start installer")
            winutil.create_mutex()
            self._update_failed(tr("couldn't start the installer"))
            return
        # Rakiza Core stays until the installer (once Windows gave it permission) asks it to close; if the installer
        # ends first, nothing was installed: Rakiza Core keeps running and the update waits
        self._installer = installer
        self._installer_timer = QTimer(self)
        self._installer_timer.timeout.connect(self._watch_installer)
        self._installer_timer.start(1000)

    def _watch_installer(self):
        installer = getattr(self, "_installer", None)
        if installer is None or self.quit_pending:
            self._installer_timer.stop()
            return
        code = installer.poll()
        if code is None:
            return
        self._installer_timer.stop()
        self._installer = None
        log.info("the installer ended (code %s) without asking Rakiza Core to close", code)
        winutil.create_mutex()
        self.settings.update_to = ""
        self.settings.save()
        if code in updater.NOT_ALLOWED:
            self.update_state = "available" if self.update_info else "error"
            self.toast.finish(UPDATE_JOB_ID, tr("Update cancelled"),
                              tr("Nothing was changed: Windows' permission wasn't given."), None, 6000)
            self._refresh_updates_page()
            self._show_update_marks()
        else:
            self._update_failed(tr("couldn't start the installer"))

    def updated_from(self, old: str, renamed: bool = False):
        """The first start after an update: said in a toast, and "What's new" is shown once, the first time the
        window opens (now, or later from the tray). renamed: the first start after the rename (the toast says
        what happened to the name too)."""
        from .releasenotes import kept
        notes = kept(__version__, i18n.language())
        fresh = notes is not None and self.settings.notes_shown != __version__
        self.whats_new_waiting = fresh
        log.info("updated from %s to %s%s", old, __version__, " (what's new waits)" if fresh else "")
        QTimer.singleShot(1500, lambda: self.toast.notify(
            tr("Welcome to the new Rakiza Core") if renamed else tr("Rakiza Core was updated"),
            tr("A new name and a new look. Your settings, history and backups were kept.") if renamed else
            tr("You're now on version {version}. Your settings were kept.", version=__version__),
            tr("What's new") if fresh else None, self.show_whats_new if fresh else None, linger_ms=9000))

    def show_whats_new(self):
        """The "What's new in Rakiza Core <version>" window, over the main window (once per version)."""
        from .notesview import WhatsNew
        from .releasenotes import kept
        self.whats_new_waiting = False
        notes = kept(__version__, i18n.language())
        if notes is None:
            return
        self.settings.notes_shown = __version__
        self.save_timer.start(400)
        w = self.window
        if not w.isVisible():
            self.show_window()
        old = getattr(self, "whats_new", None)
        if old is not None and _alive(old) and old.isVisible():
            return
        dlg = WhatsNew(notes, self.theme.accent, w)
        dlg.finished.connect(dlg.deleteLater)
        self.whats_new = dlg
        sheet.show(dlg)  # (a page that rises inside the window, as everything does)

    def check_update_finished(self):
        """An update was started last time: if this isn't the version it went to, it didn't finish (the old
        version is still here, whole). Said once, with a way to try again."""
        s = self.settings
        wanted, s.update_to = s.update_to, ""
        if not wanted or __version__ == "dev":
            return
        if updater.is_newer(wanted, __version__):
            log.warning("the update to %s didn't finish: still %s", wanted, __version__)
            QTimer.singleShot(2500, lambda: self.toast.notify(
                tr("The update didn't finish"),
                tr("Rakiza Core {version} wasn't installed, so you still have {current}. Nothing was lost.",
                   version=wanted, current=__version__),
                tr("Try again"), lambda: (self.show_window("Updates"), self.check_updates(manual=True)),
                linger_ms=15000))
        else:
            log.info("the update to %s finished (now %s)", wanted, __version__)

    def _update_failed(self, msg: str):
        self.update_state = "available" if self.update_info else "error"
        if msg == updater.CANCELLED:
            self.toast.finish(UPDATE_JOB_ID, tr("Update cancelled"), tr("You can update later from the Updates page."),
                              None, 4000)
        else:
            self.update_error = msg
            self.toast.finish(UPDATE_JOB_ID, tr("Update failed"), msg, None, 9000)
        self._refresh_updates_page()
        self._show_update_marks()

    # ---- quitting / removing

    def _tell_busy(self) -> bool:
        """True (and says so) while files are being moved or changed: stopped now, they'd be left half-done."""
        if self.uninstall_busy:  # moving an app's leftovers into its backup: never stop half-way
            self.toast.notify(tr("Rakiza Core is busy"), tr("It's removing an app's leftovers. Quit when that's done."),
                              linger_ms=5000)
            return True
        if page_work.busy():  # sorting, putting back or changing files: never stop half-way
            self.toast.notify(tr("Rakiza Core is busy"), tr("It's sorting or changing files. Quit when that's done."),
                              linger_ms=5000)
            return True
        return False

    def ask_quit(self):
        """Quit, but check first if a conversion would be stopped half-way."""
        if self._tell_busy():
            return
        if self.cancels and not self._stop_conversions_ok():
            return
        before = set(self.cancels)
        if not self.unsaved_work_ok():
            return
        if set(self.cancels) - before and not self._stop_conversions_ok():  # (saving a PDF's changes started one)
            return
        self.quit()

    def _stop_conversions_ok(self) -> bool:
        box = QMessageBox(QMessageBox.Question, tr("Quit Rakiza Core?"),
                          tr("A conversion is still running. Quitting stops it, and the unfinished file isn't "
                             "saved."), parent=self.window_if_open())
        yes = box.addButton(tr("Quit"), QMessageBox.DestructiveRole)
        box.addButton(tr("Keep converting"), QMessageBox.RejectRole)
        sheet.run(box)
        chosen = box.clickedButton() is yes
        box.deleteLater()
        return chosen

    def unsaved_work_ok(self) -> bool:
        """Before quitting or restarting, while nothing has been stopped yet: unsaved work in Rakiza Core's windows
        is asked about (settings not saved yet, a PDF's changes, a picture's edits…). False: Cancel was chosen,
        and Rakiza Core goes on as before."""
        w = self._window
        if w is not None and _alive(w):
            if w.is_dirty():  # (changed a moment ago: settings are kept as they're changed)
                w.save_changes()
            if not self.pdf_work_ok(w):
                return False
        # the other windows ask in their own way (a picture's edits, a file changed inside an archive…)
        for top in QApplication.topLevelWidgets():
            if top is w or not _alive(top) or not top.isVisible() or top.windowType() == Qt.Popup:
                continue
            if not top.close() and _alive(top) and top.isVisible():
                return False
        return True

    def pdf_work_ok(self, w) -> bool:
        """The PDF page's unsaved work (the editor's changes, a new order of pages) is asked about before its
        window goes (quitting, or the window made again). False: Cancel was chosen."""
        page = getattr(w, "pdf_page", None)
        if page is None or not _alive(page):
            return True
        editor = getattr(page, "editor", None)
        if editor is not None and _alive(editor) and editor.unsaved() and not editor.close_editor(ask=True):
            return False
        unsaved = getattr(page, "unsaved", None)
        if callable(unsaved) and unsaved() and not page.close_board(ask=True):
            return False
        return True

    def end_session(self, *_):
        """Windows is signing out or shutting down: save, and let every window close."""
        self.session_ending = True
        self.save_timer.stop()
        self.settings.save()
        self.cancel_all()

    def quit(self, cleanup: bool = False):
        """Ends Rakiza Core without asking anything (the installer waits for it): unsaved work is asked about
        before this (unsaved_work_ok)."""
        if self._window is not None and self._window.isVisible():
            self._window._remember_place()
        self.session_ending = True  # (the main window's closeEvent lets it go at once, no "Save your changes?")
        if self.save_timer.isActive():
            self.save_timer.stop()
            self.settings.save()
        for ev in self.cancels.values():
            ev.set()
        runner = getattr(self, "scan_runner", None)
        if runner is not None and runner.running:
            runner.stop(wait_s=3)  # (Defender's scan is stopped too, not left running)
        self.pool.waitForDone(3000)
        self.quick_pool.waitForDone(1000)
        if self.clip is not None:
            self.clip.stop()
            self.clip = None
        if self.capture is not None:
            self.capture.stop()
            self.capture = None
        if self.colors is not None:
            self.colors.stop()
            self.colors = None
        if self.quick is not None:
            self.quick.stop()
            self.quick = None
        if self.awake is not None:  # (Windows lets the PC sleep anyway once Rakiza Core has ended)
            self.awake.stop()
            self.awake = None
        if self.peek is not None:
            self.peek.stop()
            self.peek = None
        self.tray.hide()
        if self.server:
            self.server.close()
        if cleanup:
            remove_user_data()
        winutil.release_mutex()
        # Qt asks every window still shown to close, and one that says no (unsaved edits) would turn the quit
        # down after everything above was stopped, leaving Rakiza Core running without its tray icon: hidden
        # windows aren't asked
        for top in QApplication.topLevelWidgets():
            if _alive(top) and top.isVisible():
                top.hide()
        self.app.quit()

    def uninstall(self):
        unins = installed_uninstaller()
        if not unins:
            return
        if self._tell_busy() or not self.unsaved_work_ok():  # (asked before it starts, not once it's running)
            return
        winutil.release_mutex()
        try:
            # (installed for the whole PC: Windows asks for permission; answered No, Rakiza Core just stays)
            from .uninstall import shell_start
            handle, error = shell_start(str(unins), admin=True)
            if handle:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
            if handle is None and error == 1223:  # (the answer No at Windows' prompt)
                winutil.create_mutex()
                self.toast.notify(APP_NAME, tr("Nothing was changed: Windows' permission wasn't given."),
                                  linger_ms=5000)
                return
            if handle is None:
                raise OSError(error, "the uninstaller didn't start")
        except OSError:
            log.exception("could not start uninstaller")
            winutil.create_mutex()
            sheet.warning(self.window_if_open(), APP_NAME,
                          tr("Couldn't start the uninstaller. You can uninstall Rakiza Core from Windows "
                             "Settings → Apps → Installed apps."))
            return
        self.quit()

    def remove_portable(self):
        box = QMessageBox(QMessageBox.Question, tr("Remove Rakiza Core"),
                          tr("This deletes Rakiza Core's settings, removes it from the right-click menu, stops it "
                             "starting with Windows, and closes it.") + "\n\n"
                          + tr("After that, delete the Rakiza Core folder to finish removing it."),
                          parent=self.window_if_open())
        yes = box.addButton(tr("Remove"), QMessageBox.DestructiveRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        sheet.run(box)
        chosen = box.clickedButton() is yes
        box.deleteLater()
        if chosen and not self._tell_busy() and self.unsaved_work_ok():
            self.quit(cleanup=True)


PAGE_NAMES = ("Home", "Converter", "Apps", "Archives", "Viewer", "Clipboard", "Space", "Capture", "Cloud",
              "PDF", "Organizer", "Startup", "Scan", "Unlock", "Awake", "Colors", "Store",
              "Soon", "Files", "Media", "Computer", "QuickTools",
              "General", "Look", "ConvOptions", "WheelLook", "Formats", "AppsSettings", "ArchiveSettings",
              "ViewerSettings", "ClipboardSettings", "CaptureSettings", "History", "Updates", "About", "Shortcuts")
OUTPUT_EXT = {"image:pdf": ".pdf", "image:scan": ".pdf", "pdf:merge": ".pdf", "image:video": ".mp4",
              "pack:zip": ".zip", "pack:7z": ".7z"}  # what a tool makes, when it isn't Tool.target


FULL_GC_EVERY_S = 60   # with a window in use, every object is looked through at most this often (it stops the
_last_full = [0.0]      # window for up to half a second; the younger objects go each time, as before)
# while a conversion runs (a 2,000-page PDF made smaller: over a million objects), a full look held the window
# 1.2 s: then it comes at most this often, and the tidy after the job frees the rest
FULL_GC_CONVERTING_S = 300
_converting = [lambda: False]


def _full_collection_done(phase: str, info: dict):
    if phase == "stop" and info.get("generation") == 2:
        _last_full[0] = time.monotonic()


gc.callbacks.append(_full_collection_done)  # (a full look elsewhere, like the tidy after a job, counts too)


def collect_garbage():
    """What Python's own collector would have done by now (the same thresholds), on this thread."""
    young, middle, old = gc.get_count()
    every = FULL_GC_CONVERTING_S if _converting[0]() else FULL_GC_EVERY_S
    if old > 10 and (QApplication.activeWindow() is None or time.monotonic() - _last_full[0] > every):
        gc.collect(2)
    elif middle > 10:
        gc.collect(1)
    elif young > 700:
        gc.collect(0)


def _prune_backups(days: int = 30):
    """Uninstall backups older than this (Settings → Uninstaller) are removed, quietly in the background."""
    try:
        from . import uninstall
        uninstall.prune_backups(max(7, days))
    except Exception:
        log.debug("couldn't prune uninstall backups", exc_info=True)


def _all_tools(multi: bool):
    from .converters import catalog
    for tools in catalog(multi).values():
        yield from tools


def downloads_folder() -> Path:
    """The real Downloads folder (it may have been moved, e.g. into OneDrive)."""
    from PySide6.QtCore import QStandardPaths
    try:
        loc = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        if loc and Path(loc).is_dir():
            return Path(loc)
    except Exception:
        pass
    return Path.home() / "Downloads"


def clean_old_temp_files():
    """Removes Rakiza Core's leftovers in the temp folder older than a day: files opened from inside
    archives, and working folders left behind if the PC was switched off in the middle of a job."""
    import re
    import shutil
    cutoff = time.time() - 86400
    temp = Path(tempfile.gettempdir())
    # only folders with the exact names Rakiza Core (or its old name, before 1.7.0) gives its working
    # folders: a prefix, then Python's 8 random letters
    ours = re.compile(rf"^(corefold|{re.escape(LEGACY_NAME.lower())})-(open-|drag-)?[a-z0-9_]{{8}}$")
    for p in [*temp.glob("corefold-*"), *temp.glob(LEGACY_NAME.lower() + "-*")]:
        try:
            if ours.match(p.name) and p.is_dir() and not is_link(p) and p.stat().st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass
    updater.clean_old_installers()


def _alive(widget) -> bool:
    try:
        widget.objectName()
        return True
    except RuntimeError:  # the window was closed and deleted
        return False


# ----------------------------------------------------------------------------- startup

def refresh_startup_entry():
    """If Rakiza Core starts with Windows, make sure that entry points at this copy (the portable
    folder may have been moved, or an old copy may have been replaced)."""
    try:
        import winreg
        from .settings import RUN_KEY, launch_command, remove_old_startup_entry, set_startup, startup_enabled
        if remove_old_startup_entry():
            set_startup(True)  # it started with Windows under its old name: keep that choice
        if not startup_enabled():
            return
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            current = winreg.QueryValueEx(k, KEEP_ID)[0]
        if current != launch_command():
            set_startup(True)
    except Exception:
        log.debug("could not check the startup entry", exc_info=True)


def stop_old_copy():
    """A copy under one of the app's earlier names may be running from its old folder, or next to a portable
    Rakiza Core. It would open a second wheel and keep writing to the old settings, so it's closed first.
    Only those copies: another program that happens to have an old name is never touched.
    (The installer does the same; this covers the portable version.)"""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    places = []
    for name in OLD_APP_NAMES:
        exe = f"{name}.exe"
        places.append(Path(sys.executable).with_name(exe))
        for root in (Path(os.environ.get("LOCALAPPDATA", ""), "Programs"), Path(os.environ.get("ProgramFiles", ""))):
            if root.anchor:
                places.append(root / name / exe)
    places = [p for p in places if p.is_file()]
    if not places:
        return
    try:
        from .uninstall import stop_programs_at
        stop_programs_at(places)
    except Exception:
        log.debug("could not stop the old copy", exc_info=True)


def _keep_scroll(window, value: int):
    if not _alive(window):
        return
    bar = getattr(window.stack.currentWidget(), "verticalScrollBar", None)
    if bar is not None:
        bar().setValue(value)


def setup_logging():
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(LOG_DIR / "rakizacore.log", maxBytes=512_000,
                                                       backupCount=2, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    sys.excepthook = lambda t, e, tb: log.critical("unhandled error", exc_info=(t, e, tb))
    threading.excepthook = lambda a: log.critical("thread error",
                                                  exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


_qt_words = None  # the QTranslator in use for Qt's own words


def load_qt_translations(app: QApplication, lang: str):
    """Qt's own words (in standard buttons and menus) in the chosen language."""
    global _qt_words
    if _qt_words is not None:  # (the language was changed while running)
        app.removeTranslator(_qt_words)
        _qt_words.deleteLater()
        _qt_words = None
    if lang == "en":
        return
    try:
        from PySide6.QtCore import QLibraryInfo, QTranslator
        translator = QTranslator(app)
        if translator.load(f"qtbase_{lang}", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            app.installTranslator(translator)
            _qt_words = translator
    except Exception:
        log.debug("no Qt translations", exc_info=True)


def main() -> int:
    """Starts Rakiza Core. main.py has already made sure no other copy is running."""
    from .settings import migrate_old_name
    stop_old_copy()
    brought_over = migrate_old_name()  # before logging starts writing in the new folder
    setup_logging()
    if brought_over:
        log.info("brought settings over from an earlier name")
    log.info("starting %s %s", APP_NAME, __version__)
    winutil.set_app_id(APP_ID)
    argv = sys.argv[1:]

    first = Settings.load()
    lang = i18n.set_language(first.language)
    app = QApplication(sys.argv)
    from .safetext import guard
    guard()  # (labels and tooltips never load a picture a file name or an email points to)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationVersion(__version__)

    icon_path = resource("assets", "icon.png")
    if icon_path.exists():
        icon = QIcon(str(icon_path))
    else:
        pm = QPixmap(32, 32)
        pm.fill()
        icon = QIcon(pm)
    app.setWindowIcon(icon)

    from . import welcome
    if welcome.should_ask(first, argv):  # the first start after installing: which language, before anything else
        lang = i18n.set_language(welcome.ask_language(app, icon, first))
        log.info("language picked on the first start: %s", lang)
    app.setLayoutDirection(Qt.RightToLeft if i18n.is_rtl() else Qt.LeftToRight)
    load_qt_translations(app, lang)

    controller = Controller(app, icon)
    log.info("tools in use: %s; resting for now: %s", ", ".join(t.id for t in all_tools.enabled(controller.settings))
             or "none", ", ".join(t.id for t in all_tools.paused()) or "none")
    app.commitDataRequest.connect(controller.end_session)
    from . import settings_backup
    if settings_backup.RECOVERED is not None:  # settings.json was lost or damaged: a copy was used
        from .settingscard import recovered_note
        QTimer.singleShot(2000, lambda: recovered_note(controller))
    winutil.log_memory("in the tray")
    QTimer.singleShot(20_000, clean_old_temp_files)

    server = QLocalServer()
    server.setMaxPendingConnections(512)  # hundreds of files right-clicked at once
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)  # only this Windows user can talk to it
    QLocalServer.removeServer(ipc.SERVER_NAME)
    if not server.listen(ipc.SERVER_NAME):
        log.warning("could not listen for other copies: %s", server.errorString())
    controller.server = server
    inboxes: set = set()

    def on_connection():
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            if conn is None:
                return
            box = Inbox(conn)
            inboxes.add(box)

            def got(text, box=box):
                inboxes.discard(box)
                if text:
                    controller.handle_message(text)
            box.message.connect(got)
    server.newConnection.connect(on_connection)

    # keeps the right-click menu up to date and pointing at this copy (only rewritten if needed)
    controller.update_shell_menu(3000)
    QTimer.singleShot(4000, refresh_startup_entry)
    QTimer.singleShot(5000, controller.sync_archive_opening)

    s = controller.settings
    if s.last_version and s.last_version != __version__ and __version__ != "dev":
        controller.updated_from(s.last_version, renamed=brought_over)
    elif brought_over:  # first start after the rename: say what happened, once
        QTimer.singleShot(1500, lambda: controller.toast.notify(
            tr("Welcome to the new Rakiza Core"),
            tr("A new name and a new look. Your settings, history and backups were kept."),
            linger_ms=9000))
    controller.check_update_finished()
    s.last_version = __version__

    # what this start was asked to do (the same messages a helper process would send)
    message = ipc.message_for(argv)
    page = ""
    if "--page" in argv:
        rest = argv[argv.index("--page") + 1:]
        page = rest[0] if rest and rest[0] in PAGE_NAMES else ""
    if page:
        controller.show_window(page)
    elif message:
        QTimer.singleShot(200, lambda: controller.handle_message(message.decode("utf-8")))
    elif message is None and "--background" not in argv:
        controller.show_window("Home")
    fresh = s.first_run
    if fresh:
        s.first_run = False
        s.notes_shown = __version__  # (a new install: its "What's new" isn't news)
        s.known_tools = sorted({tool.id for tool in all_tools.available()})  # (offered now, in the first-start question)
        s.back_on = list(all_tools.BACK_ON)  # (offered in the question too: nothing to switch on later)
        if "--background" in argv:
            # (started in the tray, e.g. by a silent install: every tool stays on, as when the question is
            # closed, and their shortcuts work now, not only after the next start)
            QTimer.singleShot(0, controller.apply_tools)
        else:
            if not message or not message.startswith(b"show"):
                controller.show_window("Home")
            if not message or message.startswith(b"show"):  # (a plain start is "show")
                QTimer.singleShot(500, controller.first_start)  # which tools, then the tour
            else:  # (opened with a file: no question in its way, and the tools in the tray start now)
                QTimer.singleShot(0, controller.apply_tools)
    else:
        back = all_tools.switch_back_on(s)  # (Space and the Uninstaller: on for everyone, once)
        offered = all_tools.offer_new_tools(s)  # tools new in this version wait, switched off, in their section
        if back:
            if not offered:
                controller.apply_tools()
            back_names = ", ".join(tr(tool.name) for tool in back)
            QTimer.singleShot(2500 if not offered else 12000, lambda: controller.toast.notify(
                tr("Back in Rakiza Core"), tr("On again: {names}. You can switch them off in their section.",
                                           names=back_names),
                linger_ms=9000))
        if offered:
            controller.apply_tools()
            names = ", ".join(tr(tool.name) for tool in offered)
            QTimer.singleShot(2500, lambda: controller.toast.notify(
                tr("New tools in Rakiza Core"), tr("{names}. Switch on the ones you want, each in its section.",
                                                names=names),
                linger_ms=9000))
        if not s.tour_done and (not message or message.startswith(b"show")) and "--background" not in argv:
            QTimer.singleShot(700, controller.show_tour)
    s.save()
    if "--background" in argv and s.startup_later:  # (started with Windows: the programs put off come next)
        from .startup import LATER_AFTER_S
        QTimer.singleShot(LATER_AFTER_S * 1000, controller.start_later)
    threading.Thread(target=_prune_backups, args=(s.backup_days,), daemon=True).start()
    QTimer.singleShot(IDLE_TIDY_MS, controller.tidy_up)
    QTimer.singleShot(IDLE_TIDY_MS - 5000, lambda: winutil.log_memory("before the first tidy"))
    return app.exec()
