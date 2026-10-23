"""Corefold Awake: keeps the PC from going to sleep (until it's switched off, for a while, or until a
time), and turns the PC off later with Windows' own shutdown timer.

Staying awake is one call to Windows (SetThreadExecutionState) on the window's thread: it holds until
it's called again, and Windows drops it by itself when Corefold ends, so the PC can never be kept awake
by mistake after Corefold is closed. The shutdown timer is Windows' own (shutdown /s /t), so it still
happens if Corefold is closed; when it was planned is kept in the settings, so the page can show it."""
from __future__ import annotations

import logging
import math
import os
import subprocess
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import QLocale, QObject, QTime, QTimer, Signal
from PySide6.QtGui import QActionGroup

from .i18n import ltr, tr

if TYPE_CHECKING:
    from .app import Controller

log = logging.getLogger("corefold")

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

# "For a while": minutes -> name (English, shown through tr())
DURATIONS = {30: "30 minutes", 60: "1 hour", 120: "2 hours", 240: "4 hours"}
MODES = ("on", "for", "until")   # until it's switched off, for a while, until a time
MAX_MINUTES = 24 * 60
# the tray's "Keep awake" menu
TRAY_CHOICES = [("off", "Off"), ("on", "Until I turn it off"), (60, "1 hour"), (120, "2 hours")]
SHUTDOWN_MAX = 315360000          # the longest wait shutdown.exe takes (10 years)
ERROR_SHUTDOWN_IS_SCHEDULED = 1190
ERROR_NO_SHUTDOWN_IN_PROGRESS = 1116


def set_state(flags: int) -> bool:
    """Tells Windows what this thread needs (ES_CONTINUOUS alone: nothing any more)."""
    if os.name != "nt":
        return False
    import ctypes
    f = ctypes.windll.kernel32.SetThreadExecutionState
    f.argtypes = [ctypes.c_uint32]
    f.restype = ctypes.c_uint32
    return bool(f(flags))


def run_shutdown(*args: str) -> tuple[int, str]:
    """Runs Windows' shutdown.exe; its exit code and what it said."""
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "shutdown.exe")
    try:
        r = subprocess.run([exe, *args], capture_output=True, timeout=20, stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)
    said = (r.stdout + r.stderr).decode("oem" if os.name == "nt" else "utf-8", errors="replace").strip()
    return r.returncode, said


def booted_at() -> datetime | None:
    """When Windows last started (a planned shutdown doesn't outlive a restart)."""
    if os.name != "nt":
        return None
    import ctypes
    f = ctypes.windll.kernel32.GetTickCount64
    f.restype = ctypes.c_uint64
    return datetime.now() - timedelta(milliseconds=f())


def time_text(when: datetime) -> str:
    """A time of day the way Windows shows it (like the clock on the taskbar)."""
    return ltr(QLocale.system().toString(QTime(when.hour, when.minute), QLocale.FormatType.ShortFormat))


def left_text(seconds: float) -> str:
    """How long is left: "1 h 20 min", "45 min", "less than a minute"."""
    minutes = math.ceil(max(0.0, seconds) / 60)
    if seconds < 60:
        return tr("less than a minute")
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return tr("{hours} h {minutes} min", hours=hours, minutes=minutes)
    if hours:
        return tr("{hours} h", hours=hours)
    return tr("{minutes} min", minutes=minutes)


def next_time(hhmm: QTime | str, now: datetime | None = None) -> datetime:
    """The next time the clock shows hh:mm (tomorrow if that's already past today)."""
    t = hhmm if isinstance(hhmm, QTime) else QTime.fromString(hhmm, "HH:mm")
    if not t.isValid():
        t = QTime(18, 0)
    now = now or datetime.now()
    at = now.replace(hour=t.hour(), minute=t.minute(), second=0, microsecond=0)
    return at if at > now else at + timedelta(days=1)


def is_tomorrow(when: datetime) -> bool:
    return when.date() > datetime.now().date()


def _parse(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


class AwakeTool(QObject):
    """Corefold Awake while it's in use: staying awake, the tray's "Keep awake" menu, and the shutdown
    timer. Made by the Controller (self.c.awake) and stopped when the tool is removed in the Store."""

    changed = Signal()

    def __init__(self, controller: "Controller"):
        super().__init__()
        self.c = controller
        self.mode = ""                     # "" (sleeps as usual), "on" (until switched off) or "until"
        self.end: datetime | None = None   # when "until" ends
        self.failed = False                # Windows didn't take it
        self.tray_minutes = None           # "1 hour" or "2 hours" picked in the tray menu
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._tick)
        self.menu = None
        self.add_tray_menu()

    @property
    def on(self) -> bool:
        return bool(self.mode)

    # ---- staying awake

    def keep(self, end: datetime | None = None):
        """Stay awake until end (None: until it's switched off)."""
        self.mode, self.end = ("until", end) if end is not None else ("on", None)
        self.tray_minutes = None
        self._apply()
        self._schedule()
        self._changed()

    def keep_for(self, minutes: int):
        self.keep(datetime.now() + timedelta(minutes=max(1, min(MAX_MINUTES, int(minutes)))))

    def keep_until(self, hhmm: QTime | str):
        self.keep(next_time(hhmm))

    def start(self):
        """Stay awake the way it was last chosen on the page."""
        s = self.c.settings
        mode = s.awake_mode if s.awake_mode in MODES else "on"
        if mode == "for":
            self.keep_for(s.awake_minutes)
        elif mode == "until":
            self.keep_until(s.awake_until or "18:00")
        else:
            self.keep()

    def toggle(self):
        """On or off (for a shortcut or the quick menu)."""
        if self.on:
            self.let_sleep()
        else:
            self.start()

    def let_sleep(self, ended: bool = False):
        """Back to sleeping as usual (ended: the time chosen is up, so say so)."""
        was_on = self.on
        self.mode, self.end, self.tray_minutes = "", None, None
        self.timer.stop()
        set_state(ES_CONTINUOUS)
        self.failed = False
        if was_on:
            log.info("awake: off%s", " (time's up)" if ended else "")
        if ended:
            self.c.say(tr("Corefold Awake"), tr("Your PC can go to sleep again as usual."))
        self._changed()

    def set_screen(self, on: bool):
        """Keep the screen on too (or let it turn off), straight away if it's staying awake."""
        self.c.settings.awake_screen = bool(on)
        self.c.settings_changed()
        if self.on:
            self._apply()
        self._changed()

    def _flags(self) -> int:
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        if self.c.settings.awake_screen:
            flags |= ES_DISPLAY_REQUIRED
        return flags

    def _apply(self):
        self.failed = not set_state(self._flags()) and os.name == "nt"
        log.info("awake: %s%s%s", "until " + self.end.isoformat(timespec="minutes") if self.end else "on",
                 ", screen too" if self.c.settings.awake_screen else "", " (Windows refused)" if self.failed else "")

    def _schedule(self):
        """Wakes up at the end, or every minute until then (the time left on the page stays right,
        and a PC put to sleep by hand that wakes after the end still lets go)."""
        self.timer.stop()
        if self.mode != "until" or self.end is None:
            return
        left = (self.end - datetime.now()).total_seconds()
        if left <= 0:
            self.let_sleep(ended=True)
            return
        self.timer.start(int(math.ceil(min(left, 60) * 1000)) + 20)

    def _tick(self):
        if self.mode == "until" and self.end is not None and datetime.now() >= self.end:
            self.let_sleep(ended=True)
            return
        self._schedule()
        self.changed.emit()

    def status(self) -> str:
        if not self.on:
            return tr("Your PC can sleep as usual")
        if self.end is None:
            return tr("Staying awake until you turn it off")
        if is_tomorrow(self.end):
            return tr("Staying awake until {time} tomorrow", time=time_text(self.end))
        return tr("Staying awake until {time}", time=time_text(self.end))

    def seconds_left(self) -> float | None:
        return None if self.end is None else (self.end - datetime.now()).total_seconds()

    def _changed(self):
        self.c._update_tooltip()
        self.changed.emit()

    # ---- the tray's "Keep awake" menu

    def add_tray_menu(self):
        """Puts "Keep awake" in the tray menu (again, after the tray menu was made again in another language)."""
        self._remove_tray_menu()
        from . import icons
        from .theme import round_menu
        from PySide6.QtWidgets import QMenu
        menu = QMenu(tr("Keep awake"))
        round_menu(menu)
        self.state_action = menu.addAction("")
        self.state_action.setEnabled(False)
        menu.addSeparator()
        self.group = QActionGroup(menu)
        self.group.setExclusive(True)
        self.choice_actions = []
        for key, text in TRAY_CHOICES:
            act = menu.addAction(tr(text))
            act.setCheckable(True)
            act.setData(key)
            self.group.addAction(act)
            act.triggered.connect(lambda _=False, k=key: self._tray_chosen(k))
            self.choice_actions.append(act)
        menu.aboutToShow.connect(self._fill_tray_menu)
        tray_menu = self.c.menu
        before = getattr(self.c, "act_again", None)
        if before is not None and before in tray_menu.actions():
            tray_menu.insertMenu(before, menu)
        else:
            tray_menu.addMenu(menu)
        self.menu = menu
        entry = (menu.menuAction(), "coffee")
        self.c.menu_icons.append(entry)
        t = self.c.theme
        menu.menuAction().setIcon(icons.icon("coffee", t.muted, selected=t.text))

    def _fill_tray_menu(self):
        self.state_action.setText(self.status())
        current = "off" if not self.on else "on" if self.end is None else self.tray_minutes
        self.group.setExclusive(False)   # (a time picked on the page has no tick here)
        for act in self.choice_actions:
            act.setChecked(act.data() == current)
        self.group.setExclusive(True)

    def _tray_chosen(self, key):
        if key == "off":
            self.let_sleep()
        elif key == "on":
            self.keep()
        else:
            self.keep_for(int(key))
            self.tray_minutes = int(key)

    # ---- the shutdown timer

    def planned_shutdown(self) -> datetime | None:
        """When Windows will turn the PC off (planned here), or None. Forgets one that's past, or that a
        restart since has cleared."""
        s = self.c.settings
        at, planned = _parse(s.shutdown_at), _parse(s.shutdown_planned)
        if at is None:
            if s.shutdown_at or s.shutdown_planned:
                self._forget_shutdown()
            return None
        boot = booted_at()
        if at <= datetime.now() or (planned is not None and boot is not None and boot > planned):
            self._forget_shutdown()
            return None
        return at

    def plan_shutdown(self, at: datetime, replace: bool = False) -> int:
        """Asks Windows to turn the PC off at that time. Returns 0, or Windows' error code
        (ERROR_SHUTDOWN_IS_SCHEDULED: something else has one planned; replace=True cancels that first)."""
        if self.planned_shutdown() is not None or replace:
            run_shutdown("/a")
            self._forget_shutdown()
        seconds = max(1, min(SHUTDOWN_MAX, math.ceil((at - datetime.now()).total_seconds())))
        code, said = run_shutdown("/s", "/t", str(seconds))
        log.info("shutdown in %d s: code %d %s", seconds, code, said)
        if code == 0:
            s = self.c.settings
            s.shutdown_at = (datetime.now() + timedelta(seconds=seconds)).isoformat(timespec="seconds")
            s.shutdown_planned = datetime.now().isoformat(timespec="seconds")
            s.save()   # (not only a moment later: it must outlive Corefold)
        self.changed.emit()
        return code

    def cancel_shutdown(self) -> int:
        """Cancels the shutdown. Returns 0, or Windows' error code (ERROR_NO_SHUTDOWN_IN_PROGRESS:
        there wasn't one any more, which is fine too)."""
        code, said = run_shutdown("/a")
        log.info("shutdown cancelled: code %d %s", code, said)
        if code in (0, ERROR_NO_SHUTDOWN_IN_PROGRESS):
            self._forget_shutdown()
        self.changed.emit()
        return code

    def _forget_shutdown(self):
        s = self.c.settings
        s.shutdown_at = s.shutdown_planned = ""
        s.save()

    # ---- the tool removed, or Corefold quitting

    def stop(self):
        self.timer.stop()
        if self.on:
            set_state(ES_CONTINUOUS)
            log.info("awake: off (stopped)")
        self.mode, self.end = "", None
        self._remove_tray_menu()

    def _remove_tray_menu(self):
        if self.menu is not None:
            self.c.menu_icons = [e for e in self.c.menu_icons if e[0] is not self.menu.menuAction()]
            self.c.menu.removeAction(self.menu.menuAction())
            self.menu.deleteLater()
            self.menu = None
