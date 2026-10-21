"""
Satsuma - Shift-drag file converter for Windows.

Runs in the system tray. Hold Shift while dragging a file in File Explorer or on the
desktop and a wheel of formats appears at the cursor; drop the file on one to convert.
"""
from __future__ import annotations

import itertools
import logging
import logging.handlers
import os
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication, QIcon, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import UPDATE_REPO, __version__, updater, winutil
from .converters import Options, Result, Tool, run_job
from .settings import APP_NAME, LOG_DIR, Settings, installed_uninstaller, remove_user_data
from .theme import build_theme, stylesheet
from .toast import Toast
from .wheel import Wheel
from .window import MainWindow

APP_ID = "Satsuma.FileConverter"
# A key-drag only opens the wheel if it starts in one of these windows:
# File Explorer and the desktop. ("#32770" would add Open/Save dialogs.)
SOURCE_WINDOW_CLASSES = {"CabinetWClass", "ExploreWClass", "Progman", "WorkerW"}
DRAG_THRESHOLD_PX = 14
UPDATE_JOB_ID = -1  # the update download shows in the same popup as conversions
UPDATE_CHECK_EVERY_MS = 6 * 3600 * 1000
log = logging.getLogger("satsuma")


def resource(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


# ----------------------------------------------------------------------------- background jobs

class JobSignals(QObject):
    progress = Signal(int, float)
    finished = Signal(int, object, list, bool)  # job id, Result, errors, cancelled


class Job(QRunnable):
    def __init__(self, job_id: int, tool: Tool, files: list[Path], options: Options,
                 signals: JobSignals, cancel: threading.Event):
        super().__init__()
        self.job_id, self.tool, self.files, self.options = job_id, tool, files, options
        self.signals, self.cancel = signals, cancel
        self._last = -1.0

    def _progress(self, frac: float):
        if frac - self._last >= 0.005 or frac >= 1.0:
            self._last = frac
            self.signals.progress.emit(self.job_id, frac)

    def run(self):
        try:
            result, errors = run_job(self.tool, self.files, self.options, self._progress, self.cancel)
        except Exception as e:
            log.exception("job crashed")
            result, errors = Result(), [str(e)]
        self.signals.finished.emit(self.job_id, result, errors, self.cancel.is_set())


class UpdateSignals(QObject):
    checked = Signal(object, str, bool)  # UpdateInfo or None, error text, asked by the user?
    progress = Signal(int, float)
    downloaded = Signal(str)
    failed = Signal(str)


# ----------------------------------------------------------------------------- key-drag detector

class DragWatcher(QObject):
    """Notices a Shift-drag (or the chosen key) that starts in File Explorer or on the desktop."""
    started = Signal()
    released = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.pressed = self.armed = self.active = False
        self.press_pos = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(20)

    def poll(self):
        down = winutil.key_down(winutil.primary_button())
        if down and not self.pressed:
            self.pressed = True
            self.press_pos = QCursor.pos()
            self.armed = self.settings.enabled and \
                winutil.window_class_under_cursor() in SOURCE_WINDOW_CLASSES
        elif not down and self.pressed:
            self.pressed = self.armed = self.active = False
            self.released.emit()
            return
        if self.pressed and self.armed and not self.active and winutil.shortcut_held(self.settings.shortcut):
            if (QCursor.pos() - self.press_pos).manhattanLength() > DRAG_THRESHOLD_PX:
                self.active = True
                self.started.emit()


# ----------------------------------------------------------------------------- controller

class Controller(QObject):
    def __init__(self, app: QApplication, icon: QIcon):
        super().__init__()
        self.app = app
        self.server: QLocalServer | None = None
        self.settings = Settings.load()
        self.theme = build_theme(self.settings.appearance, self.settings.accent)
        app.setStyleSheet(stylesheet(self.theme))

        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.ids = itertools.count(1)
        self.cancels: dict[int, threading.Event] = {}

        self.wheel = Wheel(self.settings, self.theme)
        self.wheel.chosen.connect(self.start_job)
        self.watcher = DragWatcher(self.settings)
        self.watcher.started.connect(self.wheel.popup_at_cursor)
        self.watcher.released.connect(lambda: QTimer.singleShot(150, self.wheel.hide))

        self.signals = JobSignals()
        self.toast = Toast(icon)
        self.signals.progress.connect(self.toast.set_progress)
        self.signals.finished.connect(self.job_done)
        self.toast.cancel_requested.connect(self.cancel_job)

        self.tray = QSystemTrayIcon(icon)
        self.tray.activated.connect(self._tray_clicked)
        menu = QMenu()
        menu.addAction("Open Satsuma", self.show_window)
        menu.addAction("Settings", lambda: self.show_window("General"))
        self.act_pause = menu.addAction("Pause")
        self.act_pause.setCheckable(True)
        self.act_pause.setChecked(not self.settings.enabled)
        self.act_pause.toggled.connect(lambda paused: self.set_enabled(not paused))
        menu.addAction("Check for updates", lambda: self.check_updates(manual=True))
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self.menu = menu
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

        self.window = MainWindow(self, icon)
        self.window.apply_theme(self.theme)
        self._update_tooltip()
        self.tray.show()

        try:  # follow Windows light/dark changes while running
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._system_theme_changed)
        except AttributeError:
            pass

    # ---- ui

    def show_window(self, page_name: str | None = None):
        self.window.sync()
        if page_name:
            self.window.open_page(page_name)
        self.window.show()
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def _tray_clicked(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def _update_tooltip(self):
        busy = len(self.cancels)
        if busy:
            self.tray.setToolTip(f"{APP_NAME}: converting ({busy})")
        elif not self.settings.enabled:
            self.tray.setToolTip(f"{APP_NAME}: paused")
        else:
            self.tray.setToolTip(f"{APP_NAME}: hold {self.settings.shortcut_label} while dragging a file")

    def apply_theme(self):
        self.theme = build_theme(self.settings.appearance, self.settings.accent)
        self.app.setStyleSheet(stylesheet(self.theme))
        self.wheel.theme = self.theme
        self.window.apply_theme(self.theme)

    def _system_theme_changed(self, *_):
        if self.settings.appearance == "system":
            self.apply_theme()

    def settings_changed(self):
        self.settings.save()
        self.window.refresh()
        self._update_tooltip()

    def set_enabled(self, on: bool):
        self.settings.enabled = on
        self.act_pause.blockSignals(True)
        self.act_pause.setChecked(not on)
        self.act_pause.blockSignals(False)
        self.window.sync()
        self.settings_changed()

    # ---- jobs

    def start_job(self, tool: Tool, files: list[Path]):
        job_id = next(self.ids)
        cancel = threading.Event()
        self.cancels[job_id] = cancel
        if self.settings.show_progress:
            what = files[0].name if len(files) == 1 else f"{len(files)} files"
            self.toast.start(job_id, tool.description, what)
        self.pool.start(Job(job_id, tool, files, self.settings.options(), self.signals, cancel))
        self._update_tooltip()
        log.info("job %s: %s on %d file(s)", job_id, tool.key, len(files))

    def cancel_job(self, job_id: int):
        if job_id == UPDATE_JOB_ID:
            if self.update_cancel:
                self.update_cancel.set()
            return
        ev = self.cancels.get(job_id)
        if ev:
            ev.set()

    def job_done(self, job_id: int, result: Result, errors: list[str], cancelled: bool):
        self.cancels.pop(job_id, None)
        self._update_tooltip()
        if result.clipboard:
            QGuiApplication.clipboard().setText(result.clipboard)
        first = result.outputs[0] if result.outputs else None

        if cancelled:
            title, detail, linger = "Cancelled", "Nothing was saved for the unfinished file.", 3000
        elif errors:
            title = "Couldn't convert" if not result.outputs else "Some files couldn't be converted"
            more = f"\n…and {len(errors) - 3} more" if len(errors) > 3 else ""
            detail, linger = "\n".join(errors[:3]) + more, 9000
        elif result.note:
            title, detail, linger = "Done", result.note, 5000
        elif result.outputs:
            title = "Done"
            detail = f"Saved {first.name}" if len(result.outputs) == 1 else f"Saved {len(result.outputs)} items"
            linger = 4500
        else:
            title, detail, linger = "Nothing to do", "", 3000

        important = bool(errors or result.note)
        if self.settings.show_progress or important:
            shown = self.toast.finish(job_id, title, detail, first, linger)
            if not shown and important:
                icon = QSystemTrayIcon.Warning if errors else QSystemTrayIcon.Information
                self.tray.showMessage(title, detail, icon)
        if first and self.settings.reveal_after and not errors and not cancelled:
            winutil.reveal_in_explorer(first)

    # ---- updates

    def updates_supported(self) -> bool:
        return bool(UPDATE_REPO) and __version__ != "dev"

    def can_self_install(self) -> bool:
        return installed_uninstaller() is not None

    def check_updates(self, manual: bool = False):
        manual = manual is True  # timer/menu signals may pass other values
        if not self.updates_supported():
            if manual:
                self.toast.notify("Updates aren't set up", "This copy wasn't built with an update source.")
            return
        if (not manual and not self.settings.check_updates) or self.update_state in ("checking", "downloading"):
            return
        self.update_state = "checking"
        self.window.refresh_updates()

        def work():
            info, err = None, ""
            try:
                info = updater.check(UPDATE_REPO, __version__)
            except updater.UpdateError as e:
                err = str(e)
            except Exception:
                log.exception("update check failed")
                err = "something went wrong"
            self.usignals.checked.emit(info, err, manual)
        threading.Thread(target=work, daemon=True).start()

    def _update_checked(self, info, err: str, manual: bool):
        if err:
            self.update_state, self.update_error = "error", err
            log.info("update check: %s", err)
            if manual:
                self.toast.notify("Couldn't check for updates", err)
        elif info:
            self.update_state, self.update_info = "available", info
            log.info("update available: %s", info.version)
            if self.settings.auto_update and self.can_self_install():
                self.window.refresh_updates()
                self.start_update()
                return
            if manual or self.notified_version != info.version:
                self.notified_version = info.version
                self.toast.notify(f"Satsuma {info.version} is available",
                                  "One click to update. Your settings are kept.",
                                  "Update now" if self.can_self_install() else "Get it", self.start_update, 15000)
        else:
            self.update_state = "uptodate"
            if manual:
                self.toast.notify("You're up to date", f"Satsuma {__version__} is the newest version.",
                                  linger_ms=4000)
        self.window.refresh_updates()

    def start_update(self):
        info = self.update_info
        if not info or self.update_state == "downloading":
            return
        if not self.can_self_install():  # portable copy: open the download page instead
            QDesktopServices.openUrl(QUrl(info.page_url))
            return
        self.update_state = "downloading"
        self.update_cancel = threading.Event()
        self.toast.start(UPDATE_JOB_ID, f"Downloading Satsuma {info.version}",
                         "Satsuma restarts by itself when it's done.")
        self.window.refresh_updates()
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
                self.usignals.failed.emit("something went wrong")
        threading.Thread(target=work, daemon=True).start()

    def _update_downloaded(self, path: str):
        if self.cancels:  # let running conversions finish first
            QTimer.singleShot(2000, lambda: self._update_downloaded(path))
            return
        self.toast.finish(UPDATE_JOB_ID, "Installing the update…", "Satsuma will be back in a moment.", None, 5000)
        winutil.release_mutex()
        try:
            updater.run_installer(Path(path))
        except OSError:
            log.exception("could not start installer")
            winutil.create_mutex()
            self._update_failed("couldn't start the installer")
            return
        QTimer.singleShot(600, self.quit)

    def _update_failed(self, msg: str):
        self.update_state = "available" if self.update_info else "error"
        if msg == "cancelled":
            self.toast.finish(UPDATE_JOB_ID, "Update cancelled", "You can update later from the Updates page.",
                              None, 4000)
        else:
            self.update_error = msg
            self.toast.finish(UPDATE_JOB_ID, "Update failed", msg, None, 9000)
        self.window.refresh_updates()

    # ---- quitting / removing

    def quit(self, cleanup: bool = False):
        for ev in self.cancels.values():
            ev.set()
        self.pool.waitForDone(3000)
        self.tray.hide()
        if self.server:
            self.server.close()
        if cleanup:
            remove_user_data()
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
            QMessageBox.warning(self.window, APP_NAME, "Couldn't start the uninstaller. You can uninstall "
                                "Satsuma from Windows Settings → Apps → Installed apps.")
            return
        self.quit()

    def remove_portable(self):
        answer = QMessageBox.question(
            self.window, "Remove Satsuma",
            "This deletes Satsuma's settings, stops it starting with Windows, and closes it.\n\n"
            "After that, delete the Satsuma folder to finish removing it.")
        if answer == QMessageBox.StandardButton.Yes:
            self.quit(cleanup=True)


# ----------------------------------------------------------------------------- startup

SERVER_NAME = f"Satsuma-{os.environ.get('USERNAME', 'user')}"


def setup_logging():
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(LOG_DIR / "satsuma.log", maxBytes=512_000,
                                                       backupCount=2, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    sys.excepthook = lambda t, e, tb: log.critical("unhandled error", exc_info=(t, e, tb))
    threading.excepthook = lambda a: log.critical("thread error",
                                                  exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def tell_running_copy(show: bool) -> bool:
    """If Satsuma is already running, ask it to show its window (optionally) and return True."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(400):
        return False
    if show:
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(400)
    sock.disconnectFromServer()
    return True


def main() -> int:
    setup_logging()
    log.info("starting %s %s", APP_NAME, __version__)
    winutil.set_app_id(APP_ID)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    background = "--background" in sys.argv

    if tell_running_copy(show=not background):
        return 0  # the copy that's already running opens its window instead
    winutil.create_mutex()

    icon_path = resource("assets", "icon.png")
    if icon_path.exists():
        icon = QIcon(str(icon_path))
    else:
        pm = QPixmap(32, 32)
        pm.fill()
        icon = QIcon(pm)
    app.setWindowIcon(icon)

    controller = Controller(app, icon)

    server = QLocalServer()
    QLocalServer.removeServer(SERVER_NAME)
    server.listen(SERVER_NAME)
    controller.server = server

    def on_connection():
        conn = server.nextPendingConnection()
        if conn is None:
            return

        def read():
            if bytes(conn.readAll()).startswith(b"show"):
                controller.show_window()
            conn.disconnectFromServer()
        conn.readyRead.connect(read)
    server.newConnection.connect(on_connection)

    s = controller.settings
    if s.last_version and s.last_version != __version__ and __version__ != "dev":
        QTimer.singleShot(1500, lambda: controller.toast.notify(
            "Satsuma was updated", f"You're now on version {__version__}. Your settings were kept.", linger_ms=6000))
    s.last_version = __version__
    if s.first_run or not background:
        s.first_run = False
        controller.show_window("Home")
    s.save()
    return app.exec()
