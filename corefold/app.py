"""
Corefold - everyday tools for Windows (Converter, Uninstaller, Archive).

Runs in the system tray. Hold Shift while dragging a file in File Explorer or on the
desktop (or right-click it → Corefold) and a wheel of formats appears at the cursor;
pick one to convert.

Kept light on purpose: the main window is only built when it's opened and is freed again a
while after it's closed, conversions run at low priority one at a time, and memory that isn't
needed any more is handed back to Windows when Corefold is idle.
"""
from __future__ import annotations

import copy
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

from . import UPDATE_REPO, __version__, i18n, icons, ipc, shellmenu, sounds, updater, winutil
from . import tools as all_tools
from .converters import PASSWORD_ERROR, Options, Result, Tool, pick, release_memory, run_job, warm_up
from .core import (LEGACY_NAME, category, human_size, is_link, lower_this_thread, norm, os_open, set_background_priority,
                   set_downloads_folder, short_name, unique_path)
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

APP_ID = "AtomicWorks.Corefold"
# A key-drag only opens the wheel if it starts in one of these windows:
# File Explorer and the desktop. ("#32770" would add Open/Save dialogs.)
SOURCE_WINDOW_CLASSES = {"CabinetWClass", "ExploreWClass", "Progman", "WorkerW"}
DRAG_THRESHOLD_PX = 14
UPDATE_JOB_ID = -1  # the update download shows in the same popup as conversions
UPDATE_CHECK_EVERY_MS = 6 * 3600 * 1000
LIGHT_TIDY_MS = 4_000          # memory a finished job used goes back this soon
IDLE_TIDY_MS = 45_000          # hand memory back to Windows this long after the last job
FREE_WINDOW_AFTER_MS = 90_000  # free the main window this long after it's closed
FREE_POPUPS_AFTER_MS = 60_000  # ... and the quick menu, quick wheel and clipboard popup after they close
MENU_WRITE_DELAY_MS = 1500     # wait for more changes before rewriting the right-click menu
MENU_BATCH_MS = 700            # right-clicked files arrive one process at a time; collect them
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
                 signals: JobSignals, cancel: threading.Event, params: dict):
        super().__init__()
        self.job_id, self.tool, self.files, self.options = job_id, tool, files, options
        self.signals, self.cancel, self.params = signals, cancel, params
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
        try:
            result, errors = run_job(self.tool, self.files, self.options, self._progress, self.cancel,
                                     self.params, lambda i, n, name: self.signals.item.emit(self.job_id, i, n, name))
        except Exception as e:
            log.exception("job crashed")
            result, errors = Result(), [str(e)]
        self.signals.finished.emit(self.job_id, result, errors, self.cancel.is_set())


class UpdateSignals(QObject):
    checked = Signal(object, str, bool)  # UpdateInfo or None, error text, asked by the user?
    progress = Signal(int, float)
    downloaded = Signal(str)
    failed = Signal(str)


class BackgroundSignals(QObject):
    menu_written = Signal(str)         # signature of what's now in the right-click menu
    menu_files_ready = Signal(object)  # [(tool key, files)] read and ready to run
    archive_listed = Signal(object)    # (archive, password, entries or None, error or None)


# ----------------------------------------------------------------------------- key-drag detector

class DragWatcher(QObject):
    """Notices a Shift-drag (or the chosen key) that starts in File Explorer or on the desktop.

    Checks the mouse often only while a button is held; otherwise it looks rarely (a drag lasts far
    longer than that), so Corefold uses next to no CPU while it waits in the tray. With the Converter
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

    MAX_BYTES = 4 * 1024 * 1024  # thousands of file paths; anything bigger isn't from Corefold

    def _read(self):
        if not self.done:
            self.buf.extend(bytes(self.conn.readAll()))
            if len(self.buf) > self.MAX_BYTES:
                self.buf.clear()
                self._finish()

    def _check(self):
        if self.done:
            return
        self._read()
        if self.conn.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self._finish()

    def _finish(self):
        if self.done:
            return
        self._read()
        self.done = True
        self.message.emit(self.buf.decode("utf-8", "replace"))
        self.conn.deleteLater()


# ----------------------------------------------------------------------------- controller

class Controller(QObject):
    def __init__(self, app: QApplication, icon: QIcon):
        super().__init__()
        self.app = app
        self.server: QLocalServer | None = None
        self.icon = icon
        self.settings = Settings.load()
        set_background_priority(self.settings.low_priority)
        self.history = History()
        self.theme = self._make_theme()
        winutil.log_memory("Qt started")
        app.setStyleSheet(self._css())
        winutil.log_memory("look applied")
        self._window = None
        self.told_about_tray = False
        self.session_ending = False

        self.pool = QThreadPool(self)
        self.quick_pool = QThreadPool(self)  # opening one file from an archive never waits in line
        self.quick_pool.setMaxThreadCount(2)
        self.apply_performance()
        self.ids = itertools.count(1)
        self.cancels: dict[int, threading.Event] = {}
        self.uninstall_busy = 0  # an app's uninstall or leftover removal is running (Apps page)
        self.jobs: dict[int, dict] = {}  # id -> tool, files, bytes_in, params, rename, after
        self.browsers: list = []         # open archive windows
        self.viewers: list = []          # open picture and email windows
        self.pending: list[Path] = []    # files arriving from the right-click menu for the wheel
        self.pending_open: list[Path] = []  # ... for the window ("All options…")
        self.pending_runs: dict[str, list[Path]] = {}  # tool key -> files ("Convert to PNG" in the menu)
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
        self.toast = Toast(icon)
        self.signals.started.connect(self._job_started)
        self.signals.item.connect(self._job_item)
        self.signals.progress.connect(self.toast.set_progress)
        self.signals.finished.connect(self.job_done)
        self.toast.cancel_requested.connect(self.cancel_job)
        self._apply_toast_settings()
        self.compares: list = []  # open before/after windows
        self.bg = BackgroundSignals()
        self.bg.menu_written.connect(self._menu_written)
        self.bg.menu_files_ready.connect(self._run_menu_files)
        self.bg.archive_listed.connect(self._archive_listed)
        self.listing: set[str] = set()       # archives being read for the archive window
        self.recent_outputs: list[str] = []  # files Corefold just made (not offered as "downloads")

        self.tray = QSystemTrayIcon(icon)
        self.tray.activated.connect(self._tray_clicked)
        menu = QMenu()
        act_open = menu.addAction(tr("Open Corefold"), self.show_window)
        act_settings = menu.addAction(tr("Settings"), lambda: self.show_window("General"))
        self.act_paste = menu.addAction(tr("Convert what's copied"), self.paste)
        self.act_archive = menu.addAction(tr("Open an archive…"), self.choose_archive)
        self.act_clip = menu.addAction(tr("Clipboard history"), lambda: self.show_clipboard(from_tray=True))
        self.act_shot = menu.addAction(tr("Take a screenshot"), lambda: self.capture and self.capture.screenshot())
        self.act_quick = menu.addAction(tr("Quick menu"), lambda: self.quick and self.quick.toggle_menu())
        self.menu_icons = [(act_open, "home"), (act_settings, "settings"), (self.act_paste, "arrow_swap"),
                           (self.act_archive, "archive"), (self.act_clip, "clipboard"), (self.act_shot, "screenshot"),
                           (self.act_quick, "layout")]
        self.clip = None     # Corefold Clipboard, while it's in use
        self.capture = None  # Corefold Capture, while it's in use
        self.quick = None    # the quick menu and wheel, while one of them is switched on
        self.act_again = menu.addAction(tr("Do it again…"), self.do_again)
        menu.aboutToShow.connect(self._update_again_action)
        self.recent_menu = menu.addMenu(tr("Recent files"))
        self.menu_icons += [(self.act_again, "arrow_sync"), (self.recent_menu.menuAction(), "history")]
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self.act_pause = menu.addAction(tr("Pause"))
        self.act_pause.setCheckable(True)
        self.act_pause.setChecked(not self.settings.enabled)
        self.act_pause.toggled.connect(lambda paused: self.set_enabled(not paused))
        self.act_cancel = menu.addAction(tr("Stop all conversions"), self.cancel_all)
        self.act_cancel.setVisible(False)
        self.menu_icons.append((menu.addAction(tr("Check for updates"), lambda: self.check_updates(manual=True)),
                                "arrow_download"))
        menu.addSeparator()
        menu.addAction(tr("Quit"), self.ask_quit)
        self.menu = menu
        for m in (menu, self.recent_menu):
            round_menu(m)
        self._tray_tools()
        self._color_menu_icons()
        self.tray.setContextMenu(menu)

        # updates
        self.update_state = "idle"  # idle, checking, uptodate, available, downloading, error
        self.update_info: updater.UpdateInfo | None = None
        self.update_error = ""
        self.update_cancel: threading.Event | None = None
        self.notified_version = ""
        self.usignals = UpdateSignals()
        self.usignals.checked.connect(self._update_checked)
        self.usignals.progress.connect(self.toast.set_progress)
        self.usignals.downloaded.connect(self._update_downloaded)
        self.usignals.failed.connect(self._update_failed)
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.check_updates)
        self.update_timer.start(UPDATE_CHECK_EVERY_MS)
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
        self.tidy_timer.timeout.connect(self.tidy_up)
        self.light_tidy_timer = QTimer(self)  # soon after a job: hand back the memory it used
        self.light_tidy_timer.setSingleShot(True)
        self.light_tidy_timer.timeout.connect(self.light_tidy)
        self.free_window_timer = QTimer(self)
        self.free_window_timer.setSingleShot(True)
        self.free_window_timer.timeout.connect(self._free_window)
        self.free_popups_timer = QTimer(self)
        self.free_popups_timer.setSingleShot(True)
        self.free_popups_timer.timeout.connect(self._free_popups)

        self._update_tooltip()
        self.tray.show()
        winutil.log_memory("tray icon shown")

        # "Convert to JPG?" for photos that land in Downloads
        self.downloads = downloads_folder()
        set_downloads_folder(self.downloads)
        default_out = str(Path.home() / "Downloads" / "Corefold")
        if self.settings.output_folder == default_out and self.downloads != Path.home() / "Downloads":
            self.settings.output_folder = str(self.downloads / "Corefold")  # Downloads was moved: follow it
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

        if not self.settings.first_run:  # (once main() has put tools that are new in this version in the Store)
            QTimer.singleShot(0, self._background_tools)

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
        from .window import MainWindow
        geometry, shown = old.saveGeometry(), old.isVisible()
        new = MainWindow(self, self.icon, **keep)
        new.apply_theme(self.theme)
        new.restoreGeometry(geometry)
        new._placed = True
        new.open_page(page)
        self._window = new
        # the same place on the page (adding a tool from the Store doesn't jump back to the top)
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

        def swap():
            if _alive(new):
                new.setWindowOpacity(1.0)
                new.raise_()
                new.activateWindow()
            if _alive(old):
                old.hide()
                old.deleteLater()
        QTimer.singleShot(60, swap)

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
        self.tidy_timer.start(2000)

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
        w = self.window
        w.sync()
        w.refresh_history()  # conversions may have finished while the window was hidden
        if w.panel.paths and not w.isVisible():
            w.panel.refresh()  # files on the Home page may have been moved or deleted meanwhile
        if page_name:
            w.open_page(page_name)
        if not w.isVisible():  # (drawn before it's seen, so it never shows up white first)
            w.setWindowOpacity(0.0)
            w.show()
            w.repaint()
            QTimer.singleShot(60, lambda: w.setWindowOpacity(1.0) if _alive(w) else None)
        w.show()
        w.showNormal()
        w.raise_()
        w.activateWindow()

    def _tray_clicked(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def _fill_recent(self):
        """The tray's "Recent files": the last few files Corefold made; click one to see it."""
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
            winutil.play_sound(sounds.prepared(name, self.settings.volume))

    def play_done_sound(self):
        self.play_sound(self.settings.sound)

    def play_problem_sound(self):
        if self.settings.sound_problems and self.settings.sound != "off":
            self.play_sound("problem")

    def _wheel_opened(self):
        self.tidy_timer.stop()
        if self.settings.sound_tick:
            self.play_sound("tick")

    # ---- keeping Corefold light

    def apply_performance(self):
        s = self.settings
        set_background_priority(s.low_priority)
        self.pool.setMaxThreadCount(1 if s.one_at_a_time else 2)

    def light_tidy(self):
        """A few seconds after the last job: memory it used goes back to Windows straight away
        (the background remover stays ready a little longer, in case more pictures follow)."""
        if self.cancels:
            return
        import gc
        gc.collect()
        winutil.trim_memory()

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
            self.show_window("Converter")
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
        for name in sorted(new):
            path = self.downloads / name
            if os.path.normcase(str(path)) in {os.path.normcase(p) for p in self.recent_outputs}:
                continue  # made by Corefold itself
            if path.suffix.lower() in (".heic", ".heif", ".webp", ".avif") and path.is_file():
                self._offer_jpg(path)
                break

    def _offer_jpg(self, path: Path, tries: int = 0):
        # wait until the browser has finished writing the file
        try:
            st = path.stat()
        except OSError:
            return
        if tries < 10 and (st.st_size == 0 or time.time() - st.st_mtime < 1.5):
            QTimer.singleShot(1000, lambda: self._offer_jpg(path, tries + 1))
            return
        if winutil.user_busy():  # a game or presentation is full screen: ask later (for up to 10 minutes)
            if tries < 30:
                QTimer.singleShot(20_000, lambda: self._offer_jpg(path, max(tries, 10) + 1))
            return
        from .converters import catalog
        tool = next(t for t in catalog()["image"] if t.key == "image:jpg")
        self.toast.notify(tr("{name} downloaded", name=short_name(path.name, 34)),
                          tr("Many apps can't open this kind of picture. Make a JPG copy?"),
                          tr("Convert to JPG"), lambda: self.start_job(tool, [path]), 12000)

    # ---- archives

    def ask_password(self, files: list[Path], wrong: bool = False) -> str | None:
        answer = dialogs.ask("password", files, self.icon, self.theme.dark, wrong=wrong)
        return None if answer is None else answer["password"]

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

        def work():
            entries, error = None, None
            try:
                entries = archives.list_entries(src, password)
            except Exception as e:
                error = e
            try:
                self.bg.archive_listed.emit((src, password, entries, error))
            except RuntimeError:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _archive_listed(self, payload):
        from .browser import ArchiveBrowser
        src, password, entries, error = payload
        self.listing.discard(str(src).lower())
        if isinstance(error, archives.NeedPassword):  # names are hidden too (7z with a password)
            new = self.ask_password([src], wrong=bool(password))
            if new:
                self.open_browser(src, new)
            return
        if error is not None:
            self.offer_repair(src, error)
            return
        if not password and any(e.encrypted for e in entries):
            password = self.ask_password([src])
            if password is None:
                return
        win = ArchiveBrowser(self, src, password, entries, self.icon)
        log.info("archive window opened (%d items)", len(entries))
        self.browsers = [b for b in self.browsers if _alive(b)] + [win]
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

    # ---- the Store: the tools this person uses

    def set_tool(self, tool_id: str, on: bool):
        """Adds or removes a tool (from the Store): the window is made again with or without it."""
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
            self.rebuild_window("Store", committed=w.committed, pending_startup=w.pending_startup)
        QTimer.singleShot(400, lambda: self._tool_added_tip(tool_id) if on else None)  # (after the window is drawn)

    def _tool_added_tip(self, tool_id: str):
        s = self.settings
        if tool_id == "capture" and self.capture is not None:
            from .hotkey import SHORTCUT_NAMES
            key = SHORTCUT_NAMES.get(s.cap_shortcut)
            if key and self.capture.shot_key.ok:
                self.toast.notify(tr("Corefold Capture"), tr("Press {key} to take a screenshot.", key=key),
                                  linger_ms=7000)
        if tool_id == "clipboard" and self.clip is not None:
            from .clipboard import HOTKEY_NAMES
            key = HOTKEY_NAMES.get(s.clip_shortcut)
            if key and self.clip.hotkey.ok:
                self.toast.notify(tr("Corefold Clipboard"), tr("Press {key} in any app to open your clipboard, then "
                                                               "pick what to paste.", key=key), linger_ms=7000)

    def apply_tools(self):
        """Starts or stops what each tool does in the background, to match the tools in use."""
        self.watch_downloads(self.settings.watch_downloads)
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
        self.act_quick.setVisible(self.quick is not None and self.settings.quick_menu)

    def _background_tools(self):
        self._clipboard_tool()
        self._capture_tool()
        self._quick_tool()
        self._tray_tools()
        running = [name for name, on in (("clipboard", self.clip), ("capture", self.capture),
                                         ("quick access", self.quick)) if on is not None]
        if running:
            log.info("running in the background: %s", ", ".join(running))
            winutil.log_memory("background tools started")

    def _capture_tool(self):
        """Corefold Capture's shortcuts are only there while it's in use."""
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
        return self.settings.cap_shortcut, self.settings.cap_record_shortcut

    # ---- Quick access

    def _quick_tool(self):
        """The quick menu's and wheel's shortcuts are only there while one of them is switched on."""
        s = self.settings
        on = s.quick_menu or s.quick_wheel
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
        return s.quick_menu, s.quick_menu_key, s.quick_wheel, s.quick_wheel_key, s.quick_button

    # ---- Corefold Clipboard

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
        return s.clip_shortcut, s.clip_max, s.clip_days

    def show_clipboard(self, from_tray: bool = False):
        """The clipboard popup, near where you're typing (what you pick is pasted there)."""
        if self.clip is None:
            return
        from .clipboard import foreground_window
        target = 0 if from_tray else foreground_window()
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
                          + tr("If it's damaged, Corefold can try to rescue the files that can still be read, "
                               "into a new ZIP next to it. The archive itself isn't changed."))
        box.setWindowIcon(self.icon)
        rescue = box.addButton(tr("Rescue files"), QMessageBox.AcceptRole)
        box.addButton(tr("Close"), QMessageBox.RejectRole)
        box.exec()
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
        """open_images / open_emails (Settings → Viewer): applied at once, like opening archives."""
        setattr(self.settings, key, on)
        self.settings.save()
        self.sync_archive_opening()

    def sync_archive_opening(self):
        """Double-clicking archives, pictures and emails opens them in Corefold, as chosen in Settings
        (and only for the tools in use)."""
        from . import assoc
        s = self.settings
        viewer = all_tools.is_on(s, "viewer")
        wanted = {"archive": (s.open_archives and all_tools.is_on(s, "archive"), tr("Corefold Archive")),
                  "image": (s.open_images and viewer, tr("Corefold Viewer")),
                  "email": (s.open_emails and viewer, tr("Corefold Viewer")),
                  "media": (s.open_media and all_tools.is_on(s, "player"), tr("Corefold Player"))}
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
        menu_on = s.context_menu and all_tools.is_on(s, "converter")  # the menu is the Converter's
        if not menu_on and s.menu_signature == "off":
            return
        self.menu_busy = True

        def work():
            signature = ""
            try:
                if not menu_on:
                    shellmenu.remove()
                    signature = "off"
                else:
                    command, icon = app_command, app_icon()
                    verbs = shellmenu.plan(s, command)
                    signature = shellmenu.signature(verbs, command, icon)
                    if signature != s.menu_signature:
                        shellmenu.write(verbs, command, icon)
                        log.info("right-click menu updated")
            except Exception:
                log.exception("could not update the right-click menu")
                signature = ""
            try:
                self.bg.menu_written.emit(signature)
            except RuntimeError:
                pass  # quitting
        threading.Thread(target=work, daemon=True).start()

    def _menu_written(self, signature: str):
        self.menu_busy = False
        if signature and signature != self.settings.menu_signature:
            self.settings.menu_signature = signature
            self.settings.save()

    # ---- requests from the right-click menu (each file arrives from its own helper process)

    def handle_message(self, text: str):
        word, _, rest = text.partition("\n")
        lines = [line.strip() for line in rest.split("\n") if line.strip()]
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
        elif word == "play" and lines:
            self.open_player(Path(lines[0]))
        elif word == "run" and len(lines) >= 2:
            self.run_from_menu(lines[0], lines[1:])

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

    def _open_pending(self):
        files, self.pending = self.pending, []
        if files:
            self.wheel.popup_at_cursor(files)
        files, self.pending_open = self.pending_open, []
        if files:
            self.show_window("Converter")
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

    def open_viewer(self, path: Path):
        """Opens a picture (with the rest of its folder) or an email file in Corefold Viewer."""
        from .viewer import is_picture
        if not path.is_file():
            return
        self.viewers = [v for v in self.viewers if _alive(v)]
        if path.suffix.lower() in (".eml", ".msg"):
            from . import mail
            from .mailview import MailWindow
            try:
                win = MailWindow(self, path, self.icon)
            except (mail.MailError, OSError) as e:
                self.tell(tr("Couldn't open the email"), f"{short_name(path.name)}: {e}")
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
        self.remember_view(path)
        win.show()
        win.raise_()
        win.activateWindow()

    def open_player(self, path: Path):
        """Plays a video or song (with the rest of its folder) in Corefold Player."""
        if not path.is_file():
            return
        self.players = [p for p in getattr(self, "players", []) if _alive(p)]
        for p in self.players:  # one window: a new file plays in it
            if p.isVisible():
                p.open(path)
                if not p.isFullScreen():
                    p.showNormal()
                p.raise_()
                p.activateWindow()
                return
        from .player import PlayerWindow
        try:
            win = PlayerWindow(self, path)
        except Exception as e:  # (Qt's player missing from a broken install)
            log.exception("couldn't start the player")
            self.tell(tr("Can't play this here"), str(e))
            return
        self.players.append(win)
        win.show()
        win.raise_()
        win.activateWindow()

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
        self.theme = self._make_theme()
        if not wheel_only:
            # restyles every window: only when it changes, and drawn once at the end instead of widget by widget
            shown = [w for w in self.app.topLevelWidgets() if w.isVisible()]
            for w in shown:
                w.setUpdatesEnabled(False)
            try:
                self.app.setStyleSheet(self._css())
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
        self.watcher.refresh()
        if self._window is not None:
            self._window.refresh()
        self._update_tooltip()
        if menu:
            self.update_shell_menu()

    def set_enabled(self, on: bool):
        self.settings.enabled = on
        self.act_pause.blockSignals(True)
        self.act_pause.setChecked(not on)
        self.act_pause.blockSignals(False)
        if self._window is not None:
            self._window.sync()
        self.settings_changed()

    def restart(self, page: str = "General"):
        """Starts a fresh copy of Corefold (used after changing the language)."""
        if self.cancels or self.uninstall_busy:
            self.toast.notify(tr("Restart later"), tr("Corefold restarts when the conversion is done."),
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
        if tool.ask:
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
                  after: str | None = None, carry: dict | None = None):
        self.tidy_timer.stop()
        job_id = next(self.ids)
        cancel = threading.Event()
        quick = tool.key.startswith("archive:open")
        waiting = not quick and any(not j["quick"] for j in self.jobs.values()) and self.settings.one_at_a_time
        self.cancels[job_id] = cancel
        params = params or {}
        what = short_name(files[0].name) if len(files) == 1 else tr("{count} items", count=len(files))
        self.jobs[job_id] = {"tool": tool, "files": files, "bytes_in": sum(size_of(f) for f in files),
                             "params": params, "rename": rename or {}, "after": after, "quick": quick,
                             "what": what, "carry": carry or {"outputs": [], "errors": []}}
        if self.settings.show_progress:
            self.toast.start(job_id, tool.description,
                             tr("{what} · waiting for the one before it", what=what) if waiting else what)
        options = self.settings.options(rename.get("rename_base", "") if rename else "",
                                        rename.get("rename_start", 1) if rename else 1)
        (self.quick_pool if quick else self.pool).start(Job(job_id, tool, files, options, self.signals, cancel,
                                                            params))
        self._update_tooltip()
        log.info("job %s: %s on %d item(s)", job_id, tool.key, len(files))

    def _job_started(self, job_id: int):
        job = self.jobs.get(job_id)
        if job:
            self.toast.set_detail(job_id, job["what"])

    def _job_item(self, job_id: int, number: int, total: int, name: str):
        self.toast.set_detail(job_id, tr("{number} of {total} · {name}", number=number, total=total,
                                         name=short_name(name)))

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
                self.start_job(tool, retry or files, dict(job["params"], password=password), job["rename"],
                               job["after"], carry=keep)
                return
        if carry["outputs"] or carry["errors"]:  # put together with the tries before this one
            result.outputs = carry["outputs"] + result.outputs
            errors = carry["errors"] + errors
        if carry.get("files"):  # the history counts every file of the job, not just the retried ones
            files = carry["files"]
            job["bytes_in"] = carry.get("bytes_in", job["bytes_in"])
        self.recent_outputs = (self.recent_outputs + [str(p) for p in result.outputs])[-200:]

        after = job["after"]
        if after == "open" and result.outputs and not errors:
            self.toast.hide()
            try:
                os_open(result.outputs[0])
            except OSError as e:
                self.toast.notify(tr("Couldn't open the file"), str(e))
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

        extra_text, extra = "", None
        if result.undo and not cancelled:
            done = list(result.undo)
            extra_text, extra = tr("Undo"), lambda: self.undo(done)
        elif self._can_compare(files, result.outputs, errors, cancelled):
            before, after_file = files[0], result.outputs[0]
            extra_text, extra = tr("Compare"), lambda: self.compare(before, after_file)
        important = bool(errors or result.note or result.undo)
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
        if first and self.settings.reveal_after and not errors and not cancelled:
            winutil.reveal_in_explorer(first)

    def first_start(self):
        """The very first start: which tools this person wants, then the short tour."""
        from .store import ask_tools
        picked = ask_tools(self.icon, self.theme.dark)
        s = self.settings
        ready = {tool.id for tool in all_tools.ready()}
        s.known_tools = sorted(set(s.known_tools) | ready)
        if picked is not None:
            s.tools_off = sorted(ready - picked)
        s.save()
        self.apply_tools()
        if self._window is not None:
            self.rebuild_window("Home")
        if not s.tour_done:
            QTimer.singleShot(300, self.show_tour)

    def show_tour(self):
        from .tour import Tour
        dlg = Tour(self.icon, self.settings.shortcut_label, self.theme.accent)
        dlg.exec()
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
    NOT_REPEATED = {"password", "new_password", "pages"}

    def remember_action(self, tool: Tool, params: dict, rename: dict):
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
            self.update_state, self.update_error = "error", err
            log.info("update check: %s", err)
            if manual:
                self.toast.notify(tr("Couldn't check for updates"), err)
        elif info:
            self.update_state, self.update_info = "available", info
            log.info("update available: %s", info.version)
            if self.settings.auto_update and self.can_self_install():
                if not manual and winutil.user_busy():  # a game or presentation is full screen: install a bit later
                    QTimer.singleShot(15 * 60_000, self.check_updates)
                    self._refresh_updates_page()
                    return
                self._refresh_updates_page()
                self.start_update()
                return
            if manual or (self.notified_version != info.version and not winutil.user_busy()):
                shown = self.toast.notify(tr("Corefold {version} is available", version=info.version),
                                          tr("One click to update. Your settings are kept."),
                                          tr("Update now") if self.can_self_install() else tr("Get it"),
                                          self.start_update, 15000)
                if shown:  # if a conversion was on screen, say it at the next check instead
                    self.notified_version = info.version
        else:
            self.update_state = "uptodate"
            if manual:
                self.toast.notify(tr("You're up to date"),
                                  tr("Corefold {version} is the newest version.", version=__version__),
                                  linger_ms=4000)
        self._refresh_updates_page()

    def start_update(self):
        info = self.update_info
        if not info or self.update_state == "downloading":
            return
        if not self.can_self_install():  # portable copy: open the download page instead
            QDesktopServices.openUrl(QUrl(info.page_url))
            return
        self.update_state = "downloading"
        self.update_cancel = threading.Event()
        self.toast.start(UPDATE_JOB_ID, tr("Downloading Corefold {version}", version=info.version),
                         tr("Corefold restarts by itself when it's done."))
        self._refresh_updates_page()
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

    def _update_downloaded(self, path: str):
        if self.cancels or self.uninstall_busy:  # let running conversions (or an uninstall) finish first
            QTimer.singleShot(2000, lambda: self._update_downloaded(path))
            return
        self.toast.finish(UPDATE_JOB_ID, tr("Installing the update…"), tr("Corefold will be back in a moment."),
                          None, 5000)
        winutil.release_mutex()
        try:
            updater.run_installer(Path(path))
        except OSError:
            log.exception("could not start installer")
            winutil.create_mutex()
            self._update_failed(tr("couldn't start the installer"))
            return
        QTimer.singleShot(600, self.quit)

    def _update_failed(self, msg: str):
        self.update_state = "available" if self.update_info else "error"
        if msg == updater.CANCELLED:
            self.toast.finish(UPDATE_JOB_ID, tr("Update cancelled"), tr("You can update later from the Updates page."),
                              None, 4000)
        else:
            self.update_error = msg
            self.toast.finish(UPDATE_JOB_ID, tr("Update failed"), msg, None, 9000)
        self._refresh_updates_page()

    # ---- quitting / removing

    def ask_quit(self):
        """Quit, but check first if a conversion would be stopped half-way."""
        if self.uninstall_busy:  # moving an app's leftovers into its backup: never stop half-way
            self.toast.notify(tr("Corefold is busy"), tr("It's removing an app's leftovers. Quit when that's done."),
                              linger_ms=5000)
            return
        if self.cancels:
            box = QMessageBox(QMessageBox.Question, tr("Quit Corefold?"),
                              tr("A conversion is still running. Quitting stops it, and the unfinished file isn't "
                                 "saved."), parent=self.window_if_open())
            yes = box.addButton(tr("Quit"), QMessageBox.DestructiveRole)
            box.addButton(tr("Keep converting"), QMessageBox.RejectRole)
            box.exec()
            chosen = box.clickedButton() is yes
            box.deleteLater()
            if not chosen:
                return
        self.quit()

    def end_session(self, *_):
        """Windows is signing out or shutting down: save, and let every window close."""
        self.session_ending = True
        self.save_timer.stop()
        self.settings.save()
        self.cancel_all()

    def quit(self, cleanup: bool = False):
        if self._window is not None and self._window.isVisible():
            self._window._remember_place()
        if self.save_timer.isActive():
            self.save_timer.stop()
            self.settings.save()
        for ev in self.cancels.values():
            ev.set()
        self.pool.waitForDone(3000)
        self.quick_pool.waitForDone(1000)
        if self.clip is not None:
            self.clip.stop()
            self.clip = None
        if self.capture is not None:
            self.capture.stop()
            self.capture = None
        if self.quick is not None:
            self.quick.stop()
            self.quick = None
        for p in getattr(self, "players", []):
            if _alive(p):
                p.close()
        self.tray.hide()
        if self.server:
            self.server.close()
        if cleanup:
            remove_user_data()
        winutil.release_mutex()
        self.app.quit()

    def uninstall(self):
        unins = installed_uninstaller()
        if not unins:
            return
        winutil.release_mutex()
        try:
            subprocess.Popen([str(unins)])
        except OSError:
            log.exception("could not start uninstaller")
            winutil.create_mutex()
            QMessageBox.warning(self.window_if_open(), APP_NAME,
                                tr("Couldn't start the uninstaller. You can uninstall Corefold from Windows "
                                   "Settings → Apps → Installed apps."))
            return
        self.quit()

    def remove_portable(self):
        box = QMessageBox(QMessageBox.Question, tr("Remove Corefold"),
                          tr("This deletes Corefold's settings, removes it from the right-click menu, stops it "
                             "starting with Windows, and closes it.") + "\n\n"
                          + tr("After that, delete the Corefold folder to finish removing it."),
                          parent=self.window_if_open())
        yes = box.addButton(tr("Remove"), QMessageBox.DestructiveRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        chosen = box.clickedButton() is yes
        box.deleteLater()
        if chosen:
            self.quit(cleanup=True)


PAGE_NAMES = ("Home", "Converter", "Apps", "Archives", "Viewer", "Player", "Clipboard", "Space", "Capture", "Cloud",
              "Store", "PlayerSettings",
              "Soon",
              "General", "Look", "ConvOptions", "WheelLook", "Formats", "AppsSettings", "ArchiveSettings",
              "ViewerSettings", "ClipboardSettings", "CaptureSettings", "History", "Updates", "About")
OUTPUT_EXT = {"image:pdf": ".pdf", "image:scan": ".pdf", "pdf:merge": ".pdf", "image:video": ".mp4",
              "pack:zip": ".zip", "pack:7z": ".7z"}  # what a tool makes, when it isn't Tool.target


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
    """Removes Corefold's leftovers in the temp folder older than a day: files opened from inside
    archives, and working folders left behind if the PC was switched off in the middle of a job."""
    import re
    import shutil
    cutoff = time.time() - 86400
    temp = Path(tempfile.gettempdir())
    # only folders with the exact names Corefold (or its old name, before 1.7.0) gives its working
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
    """If Corefold starts with Windows, make sure that entry points at this copy (the portable
    folder may have been moved, or an old copy may have been replaced)."""
    try:
        import winreg
        from .settings import RUN_KEY, launch_command, remove_old_startup_entry, set_startup, startup_enabled
        if remove_old_startup_entry():
            set_startup(True)  # it started with Windows under its old name: keep that choice
        if not startup_enabled():
            return
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            current = winreg.QueryValueEx(k, APP_NAME)[0]
        if current != launch_command():
            set_startup(True)
    except Exception:
        log.debug("could not check the startup entry", exc_info=True)


def stop_old_copy():
    """A copy from before 1.7.0 (the old name) may be running, e.g. next to a portable Corefold.
    It would open a second wheel and keep writing to the old settings, so it's closed first.
    (The installer does the same; this covers the portable version.)"""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        subprocess.run(["taskkill", "/f", "/im", f"{LEGACY_NAME}.exe"], capture_output=True, timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


def _keep_scroll(window, value: int):
    if not _alive(window):
        return
    bar = getattr(window.stack.currentWidget(), "verticalScrollBar", None)
    if bar is not None:
        bar().setValue(value)


def setup_logging():
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(LOG_DIR / "corefold.log", maxBytes=512_000,
                                                       backupCount=2, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    sys.excepthook = lambda t, e, tb: log.critical("unhandled error", exc_info=(t, e, tb))
    threading.excepthook = lambda a: log.critical("thread error",
                                                  exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def load_qt_translations(app: QApplication, lang: str):
    """Qt's own words (in standard buttons and menus) in the chosen language."""
    if lang == "en":
        return
    try:
        from PySide6.QtCore import QLibraryInfo, QTranslator
        translator = QTranslator(app)
        if translator.load(f"qtbase_{lang}", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            app.installTranslator(translator)
    except Exception:
        log.debug("no Qt translations", exc_info=True)


def main() -> int:
    """Starts Corefold. main.py has already made sure no other copy is running."""
    from .settings import migrate_old_name
    stop_old_copy()
    brought_over = migrate_old_name()  # before logging starts writing in the new folder
    setup_logging()
    if brought_over:
        log.info("brought settings over from %s", LEGACY_NAME)
    log.info("starting %s %s", APP_NAME, __version__)
    winutil.set_app_id(APP_ID)
    argv = sys.argv[1:]

    settings_lang = Settings.load().language
    lang = i18n.set_language(settings_lang)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationVersion(__version__)
    if i18n.is_rtl():
        app.setLayoutDirection(Qt.RightToLeft)
    load_qt_translations(app, lang)

    icon_path = resource("assets", "icon.png")
    if icon_path.exists():
        icon = QIcon(str(icon_path))
    else:
        pm = QPixmap(32, 32)
        pm.fill()
        icon = QIcon(pm)
    app.setWindowIcon(icon)

    controller = Controller(app, icon)
    app.commitDataRequest.connect(controller.end_session)
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
    if brought_over:  # first start after the rename: say what happened, once
        QTimer.singleShot(1500, lambda: controller.toast.notify(
            tr("Welcome to the new Corefold"),
            tr("A new name and a new look. Your settings, history and backups were kept."),
            linger_ms=9000))
    elif s.last_version and s.last_version != __version__ and __version__ != "dev":
        QTimer.singleShot(1500, lambda: controller.toast.notify(
            tr("Corefold was updated"),
            tr("You're now on version {version}. Your settings were kept.", version=__version__), linger_ms=6000))
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
        s.known_tools = sorted({tool.id for tool in all_tools.ready()})  # (offered now, in the first-start question)
        if not message or not message.startswith(b"show"):
            controller.show_window("Home")
        if not message and "--background" not in argv:
            QTimer.singleShot(500, controller.first_start)  # which tools, then the tour
    else:
        offered = all_tools.offer_new_tools(s)  # tools new in this version wait in the Store
        if offered:
            controller.apply_tools()
            names = ", ".join(tr(tool.name) for tool in offered)
            QTimer.singleShot(2500, lambda: controller.toast.notify(
                tr("New tools in the Store"), tr("{names}. Add the ones you want from Corefold's Store.", names=names),
                linger_ms=9000))
        if not s.tour_done and not message and "--background" not in argv:
            QTimer.singleShot(700, controller.show_tour)
    s.save()
    threading.Thread(target=_prune_backups, args=(s.backup_days,), daemon=True).start()
    QTimer.singleShot(IDLE_TIDY_MS, controller.tidy_up)
    QTimer.singleShot(IDLE_TIDY_MS - 5000, lambda: winutil.log_memory("before the first tidy"))
    return app.exec()
