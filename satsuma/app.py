"""
Satsuma - Shift-drag file converter for Windows.

Runs in the system tray. Hold Shift while dragging a file in File Explorer or on the
desktop (or right-click it → Convert with Satsuma) and a wheel of formats appears at the
cursor; pick one to convert.
"""
from __future__ import annotations

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

from PySide6.QtCore import QFileSystemWatcher, QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication, QIcon, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import UPDATE_REPO, __version__, archives, dialogs, sounds, updater, winutil
from .converters import PASSWORD_ERROR, Options, Result, Tool, run_job
from .core import os_open
from .history import History, size_of
from .settings import APP_NAME, LOG_DIR, Settings, installed_uninstaller, remove_user_data, set_context_menu
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
        try:
            result, errors = run_job(self.tool, self.files, self.options, self._progress, self.cancel,
                                     self.params)
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
        self.icon = icon
        self.settings = Settings.load()
        self.history = History()
        self.theme = self._make_theme()
        app.setStyleSheet(stylesheet(self.theme))

        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.ids = itertools.count(1)
        self.cancels: dict[int, threading.Event] = {}
        self.jobs: dict[int, dict] = {}  # id -> tool, files, bytes_in, params, rename, after
        self.browsers: list = []         # open archive windows
        self.pending: list[Path] = []  # files arriving from the right-click menu
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
        self.signals.progress.connect(self.toast.set_progress)
        self.signals.finished.connect(self.job_done)
        self.toast.cancel_requested.connect(self.cancel_job)

        self.tray = QSystemTrayIcon(icon)
        self.tray.activated.connect(self._tray_clicked)
        menu = QMenu()
        menu.addAction("Open Satsuma", self.show_window)
        menu.addAction("Settings", lambda: self.show_window("General"))
        menu.addAction("Convert what's copied", self.paste)
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

        # "Convert to JPG?" for photos that land in Downloads
        self.downloads = Path.home() / "Downloads"
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

    def _make_theme(self):
        s = self.settings
        return build_theme(s.appearance, s.accent, s.custom_color, s.wheel_opacity)

    def _hide_drag_wheel(self):
        if not self.wheel.click_mode:
            self.wheel.hide()

    def set_context_menu(self, on: bool):
        self.settings.context_menu = on
        set_context_menu(on)
        self.settings_changed()

    def play_sound(self, name: str):
        if name and name != "off":
            winutil.play_sound(sounds.prepared(name, self.settings.volume))

    def play_done_sound(self):
        self.play_sound(self.settings.sound)

    def play_problem_sound(self):
        if self.settings.sound_problems and self.settings.sound != "off":
            self.play_sound("problem")

    def _wheel_opened(self):
        if self.settings.sound_tick:
            self.play_sound("tick")

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
                out = folder / time.strftime("Pasted picture %Y-%m-%d %H.%M.%S.png")
                if image.save(str(out), "PNG"):
                    return [out]
        return []

    def paste(self):
        files = self.clipboard_files()
        if files:
            self.show_window("Home")
            self.window.show_files(files)
        else:
            self.toast.notify("Nothing to convert", "Copy a picture or some files first (Ctrl + C), then try again.",
                              linger_ms=4500)

    # ---- downloads helper

    def watch_downloads(self, on: bool):
        self.settings.watch_downloads = on
        folder = str(self.downloads)
        if on and self.downloads.is_dir():
            self.seen_downloads = {p.name for p in self.downloads.iterdir()} if self.downloads.exists() else set()
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
            if path.suffix.lower() in (".heic", ".heif", ".webp", ".avif") and path.is_file():
                self._offer_jpg(path)
                break

    def _offer_jpg(self, path: Path, tries: int = 0):
        # wait until the browser has finished writing the file
        try:
            size = path.stat().st_size
        except OSError:
            return
        if tries < 10 and (size == 0 or time.time() - path.stat().st_mtime < 1.5):
            QTimer.singleShot(1000, lambda: self._offer_jpg(path, tries + 1))
            return
        from .converters import catalog
        tool = next(t for t in catalog()["image"] if t.key == "image:jpg")
        self.toast.notify(f"{path.name} downloaded", "Many apps can't open this kind of picture. Make a JPG copy?",
                          "Convert to JPG", lambda: self.start_job(tool, [path]), 12000)

    # ---- archives

    def ask_password(self, files: list[Path], wrong: bool = False) -> str | None:
        answer = dialogs.ask("password", files, self.icon, self.theme.dark, wrong=wrong)
        return None if answer is None else answer["password"]

    def open_browser(self, src: Path, password: str = ""):
        from .browser import ArchiveBrowser
        if not password and archives.is_encrypted(src):
            password = self.ask_password([src])
            if password is None:
                return
        try:
            entries = archives.list_entries(src, password)
        except archives.NeedPassword:  # names are hidden too (7z with a password)
            new = self.ask_password([src], wrong=bool(password))
            if new:
                self.open_browser(src, new)
            return
        except Exception as e:
            self.toast.notify("Couldn't open the archive", f"{src.name}: {e}")
            self.play_problem_sound()
            return
        win = ArchiveBrowser(self, src, password, entries, self.icon)
        self.browsers = [b for b in self.browsers if _alive(b)] + [win]
        win.show()
        win.raise_()
        win.activateWindow()

    # ---- right-click menu: files arrive one process at a time, so collect them briefly

    def files_from_menu(self, paths: list[str]):
        for p in paths:
            path = Path(p)
            if path.exists() and path not in self.pending:
                self.pending.append(path)
        if self.pending:
            self.pending_timer.start(350)

    def _open_pending(self):
        files, self.pending = self.pending, []
        if files:
            self.wheel.popup_at_cursor(files)

    def browse_from_menu(self, path: str):
        p = Path(path)
        if p.exists():
            self.open_browser(p)

    def apply_theme(self):
        self.theme = self._make_theme()
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

    def tool_chosen(self, tool: Tool, files: list[Path]):
        """Called when a slice is picked. Waits until the drop has finished (Explorer is
        still in its drag loop during dropEvent), then asks any questions and converts."""
        QTimer.singleShot(0, lambda: self._ask_and_start(tool, files))

    def _ask_and_start(self, tool: Tool, files: list[Path]):
        dark = self.theme.dark
        if tool.opens == "browse":
            self.open_browser(files[0])
            return
        params: dict = {}
        if tool.password and any(archives.is_encrypted(f) for f in files):
            password = self.ask_password(files)
            if password is None:
                return
            params["password"] = password
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
        if params.get("rename_base"):  # the archive window has its own name box
            rename = {"rename_base": params.pop("rename_base")}
        elif self.settings.ask_names and tool.renames and len(files) > 1:
            answer = dialogs.ask("rename", files, self.icon, dark)
            if answer is None:
                return
            rename = answer
        self.start_job(tool, files, params, rename)

    def start_job(self, tool: Tool, files: list[Path], params: dict | None = None, rename: dict | None = None,
                  after: str | None = None):
        job_id = next(self.ids)
        cancel = threading.Event()
        self.cancels[job_id] = cancel
        params = params or {}
        self.jobs[job_id] = {"tool": tool, "files": files, "bytes_in": sum(size_of(f) for f in files),
                             "params": params, "rename": rename or {}, "after": after}
        if self.settings.show_progress:
            what = files[0].name if len(files) == 1 else f"{len(files)} items"
            self.toast.start(job_id, tool.description, what)
        options = self.settings.options(rename.get("rename_base", "") if rename else "",
                                        rename.get("rename_start", 1) if rename else 1)
        self.pool.start(Job(job_id, tool, files, options, self.signals, cancel, params))
        self._update_tooltip()
        log.info("job %s: %s on %d item(s)", job_id, tool.key, len(files))

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
        job = self.jobs.pop(job_id, None) or {"tool": None, "files": [], "bytes_in": 0, "params": {}, "rename": {},
                                               "after": None}
        tool, files = job["tool"], job["files"]
        self._update_tooltip()

        # wrong password: ask again and retry the same job
        if tool and tool.password and errors and not result.outputs and not cancelled \
                and all(PASSWORD_ERROR in e for e in errors):
            self.toast.hide()
            password = self.ask_password(files, wrong=bool(job["params"].get("password")))
            if password is not None:
                self.start_job(tool, files, dict(job["params"], password=password), job["rename"], job["after"])
            return

        after = job["after"]
        if after == "open" and result.outputs and not errors:
            self.toast.hide()
            try:
                os_open(result.outputs[0])
            except OSError as e:
                self.toast.notify("Couldn't open the file", str(e))
            return
        if after == "reload":
            for b in self.browsers:
                if _alive(b) and files and b.src == files[0]:
                    b.reload()
        record = tool and not cancelled and not tool.key.startswith(("archive:open", "archive:test"))
        if record:
            self.history.record(tool.description, files, result.outputs, len(errors), job["bytes_in"])
            if self.window.isVisible():
                self.window.refresh_history()
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
        if not cancelled and (result.outputs or result.note) and not (errors and not result.outputs):
            self.play_done_sound()
        elif errors and not cancelled:
            self.play_problem_sound()
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
            "This deletes Satsuma's settings, removes it from the right-click menu, stops it starting "
            "with Windows, and closes it.\n\n"
            "After that, delete the Satsuma folder to finish removing it.")
        if answer == QMessageBox.StandardButton.Yes:
            self.quit(cleanup=True)


def _alive(widget) -> bool:
    try:
        widget.objectName()
        return True
    except RuntimeError:  # the window was closed and deleted
        return False


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


def tell_running_copy(message: bytes) -> bool:
    """If Satsuma is already running, send it a message ("show", "convert\n<paths>") and return True."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(400):
        return False
    if message:
        sock.write(message)
        sock.flush()
        sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        sock.waitForDisconnected(1000)
    return True


def parse_args(argv: list[str]) -> tuple[bool, list[str], str]:
    background = "--background" in argv
    convert: list[str] = []
    browse = ""
    if "--convert" in argv:
        convert = [a for a in argv[argv.index("--convert") + 1:] if not a.startswith("--")]
    if "--browse" in argv:
        rest = [a for a in argv[argv.index("--browse") + 1:] if not a.startswith("--")]
        browse = rest[0] if rest else ""
    return background, convert, browse


def main() -> int:
    if "--remove-shell-menu" in sys.argv:  # used by the uninstaller
        set_context_menu(False)
        return 0
    setup_logging()
    log.info("starting %s %s", APP_NAME, __version__)
    winutil.set_app_id(APP_ID)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    background, convert, browse = parse_args(sys.argv[1:])

    if convert:
        message = ("convert\n" + "\n".join(convert)).encode("utf-8")
    elif browse:
        message = ("browse\n" + browse).encode("utf-8")
    else:
        message = b"" if background else b"show"
    if tell_running_copy(message):
        return 0  # the copy that's already running takes it from here
    if not winutil.create_mutex():
        # Another copy is just starting (e.g. several files right-clicked at once): wait for it.
        for _ in range(40):
            time.sleep(0.2)
            if tell_running_copy(message):
                return 0
        log.warning("another copy holds the mutex but isn't answering")
        return 0

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

        buf = bytearray()

        def read():
            buf.extend(bytes(conn.readAll()))

        def done():
            read()
            text = buf.decode("utf-8", "replace")
            if text.startswith("show"):
                controller.show_window()
            elif text.startswith("convert\n"):
                controller.files_from_menu([line for line in text.split("\n")[1:] if line.strip()])
            elif text.startswith("browse\n"):
                controller.browse_from_menu(text.split("\n", 1)[1].strip())
            conn.deleteLater()
        conn.readyRead.connect(read)
        conn.disconnected.connect(done)
    server.newConnection.connect(on_connection)

    if controller.settings.context_menu:  # keeps the menu pointing at this copy of Satsuma
        QTimer.singleShot(2500, lambda: set_context_menu(True))

    s = controller.settings
    if s.last_version and s.last_version != __version__ and __version__ != "dev":
        QTimer.singleShot(1500, lambda: controller.toast.notify(
            "Satsuma was updated", f"You're now on version {__version__}. Your settings were kept.", linger_ms=6000))
    s.last_version = __version__
    if s.first_run or not (background or convert or browse):
        s.first_run = False
        controller.show_window("Home")
    s.save()
    if convert:
        QTimer.singleShot(200, lambda: controller.files_from_menu(convert))
    if browse:
        QTimer.singleShot(200, lambda: controller.browse_from_menu(browse))
    return app.exec()
