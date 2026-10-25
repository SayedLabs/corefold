"""Corefold Awake: keeps the PC from going to sleep (until it's switched off, for a while, until a
time, or by itself while chosen programs are open), and turns the PC off later with Windows' own
shutdown timer. On battery, it lets the PC sleep again once the battery runs low.

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
LOW_BATTERY = 15                  # percent left, on battery, below which it lets the PC sleep again
WATCH_MS = 30_000                 # how often the programs and the battery are looked at (only when needed)
MAX_PROGRAMS = 30
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


def battery() -> tuple[bool, int] | None:
    """(plugged in, percent left), or None on a PC without a battery (or when Windows can't say)."""
    if os.name != "nt":
        return None
    import ctypes

    class POWER(ctypes.Structure):
        _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte), ("percent", ctypes.c_ubyte),
                    ("saver", ctypes.c_ubyte), ("left", ctypes.c_ulong), ("full", ctypes.c_ulong)]
    p = POWER()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(p)):
        return None
    if p.flag & 128 or p.flag == 255 or p.percent > 100:  # (no battery, or not known)
        return None
    return p.ac == 1, int(p.percent)


def battery_low(state: tuple[bool, int] | None = None) -> bool:
    """On battery, with less than LOW_BATTERY percent left."""
    state = battery() if state is None else state
    return state is not None and not state[0] and state[1] < LOW_BATTERY


def program_names() -> set[str]:
    """The file names (lowercase) of the programs running now: one quick list from Windows."""
    if os.name != "nt":
        return set()
    import ctypes
    k = ctypes.WinDLL("kernel32")
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.CloseHandle.argtypes = [ctypes.c_void_p]

    class ENTRY(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint32), ("usage", ctypes.c_uint32), ("pid", ctypes.c_uint32),
                    ("heap", ctypes.c_size_t), ("module", ctypes.c_uint32), ("threads", ctypes.c_uint32),
                    ("parent", ctypes.c_uint32), ("priority", ctypes.c_long), ("flags", ctypes.c_uint32),
                    ("exe", ctypes.c_wchar * 260)]
    snap = k.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return set()
    names = set()
    try:
        e = ENTRY()
        e.size = ctypes.sizeof(e)
        ok = k.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            names.add(e.exe.lower())
            ok = k.Process32NextW(snap, ctypes.byref(e))
    finally:
        k.CloseHandle(snap)
    return names


def program_title(path: str) -> str:
    """A program's own name ("OBS Studio"), else its file name."""
    from .clipboard import app_name
    return app_name(path) if os.path.isabs(path) else os.path.splitext(path)[0]


# parts of Windows itself that have windows (never worth staying awake for)
WINDOWS_PARTS = {"explorer.exe", "applicationframehost.exe", "textinputhost.exe", "shellexperiencehost.exe",
                 "searchhost.exe", "searchapp.exe", "startmenuexperiencehost.exe", "systemsettings.exe",
                 "lockapp.exe", "taskmgr.exe"}


def window_programs() -> list[str]:
    """The programs that have a window open now (their full paths, once each), to pick from."""
    if os.name != "nt":
        return []
    import ctypes
    import ctypes.wintypes as wt
    from .clipboard import process_path
    u = ctypes.windll.user32
    pids: list[int] = []
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def each(hwnd, _):
        if u.IsWindowVisible(hwnd) and u.GetWindowTextLengthW(hwnd) and not u.GetWindow(hwnd, 4):  # GW_OWNER
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pids.append(pid.value)
        return True
    try:
        u.EnumWindows(PROC(each), 0)
    except Exception:
        return []
    found: dict[str, str] = {}
    for pid in dict.fromkeys(pids):
        if pid == os.getpid():
            continue
        path = process_path(pid)
        name = os.path.basename(path).lower()
        if path and name not in WINDOWS_PARTS and name not in found:
            found[name] = path
    return list(found.values())


def booted_at() -> datetime | None:
    """When Windows last started (a planned shutdown doesn't outlive a restart)."""
    if os.name != "nt":
        return None
    import ctypes
    f = ctypes.windll.kernel32.GetTickCount64
    f.restype = ctypes.c_uint64
    return datetime.now() - timedelta(milliseconds=f())


def shut_down_at() -> datetime | None:
    """When Windows was last shut down. With Fast Startup on, "Shut down" only puts Windows to sleep in a
    file, so the time since it started (booted_at) goes on from before; Windows still writes this down
    then, and a planned shutdown doesn't outlive either."""
    if os.name != "nt":
        return None
    try:
        import struct
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Windows") as k:
            raw, _kind = winreg.QueryValueEx(k, "ShutdownTime")
        ticks = struct.unpack("<Q", bytes(raw)[:8])[0]  # (a FILETIME: 100 ns steps since 1601, UTC)
        return datetime.fromtimestamp(ticks / 10_000_000 - 11_644_473_600) if ticks else None
    except (OSError, ValueError, TypeError, struct.error, OverflowError):
        return None


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
    timer. Made by the Controller (self.c.awake) and stopped when the tool is switched off."""

    changed = Signal()

    def __init__(self, controller: "Controller"):
        super().__init__()
        self.c = controller
        self.mode = ""                     # "" (sleeps as usual), "on" (until switched off), "until", or
                                           # "app" (by itself, while one of the chosen programs is open)
        self.end: datetime | None = None   # when "until" ends
        self.app = ""                      # "app": the program keeping it awake (its file name, lowercase)
        self.waived: set[str] = set()      # chosen programs open when it was told to let the PC sleep (not
                                           # again for them until they're closed)
        self.low = False                   # on battery and running low: it doesn't keep the PC awake
        self.failed = False                # Windows didn't take it
        self.tray_minutes = None           # "1 hour" or "2 hours" picked in the tray menu
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._tick)
        self.watcher = QTimer(self)        # the chosen programs and the battery, only while that matters
        self.watcher.setInterval(WATCH_MS)
        self.watcher.timeout.connect(self.watch)
        self.menu = None
        self.add_tray_menu()
        QTimer.singleShot(8000, self, self.watch)  # (a chosen program already open; not while Corefold starts)

    @property
    def on(self) -> bool:
        return bool(self.mode)

    # ---- staying awake

    def keep(self, end: datetime | None = None):
        """Stay awake until end (None: until it's switched off)."""
        self.mode, self.end = ("until", end) if end is not None else ("on", None)
        self.tray_minutes = None
        self.app = ""
        self._apply()
        self._schedule()
        self._rewatch()
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
        if was_on and not ended and self.c.settings.awake_apps:
            self.waived = self._chosen() & program_names()
        self.mode, self.end, self.tray_minutes, self.app = "", None, None, ""
        self.timer.stop()
        set_state(ES_CONTINUOUS)
        self.failed = False
        if was_on:
            log.info("awake: off%s", " (time's up)" if ended else "")
        if ended:
            self.c.say(tr("Corefold Awake"), tr("Your PC can go to sleep again as usual."))
        self._rewatch()
        self._changed()

    # ---- by itself: the chosen programs, and the battery

    def programs(self) -> list[str]:
        return list(self.c.settings.awake_apps)

    def set_programs(self, paths: list[str]):
        seen, kept = set(), []
        for p in paths:
            name = os.path.basename(p).lower()
            if name and name not in seen:
                seen.add(name)
                kept.append(p)
        self.c.settings.awake_apps = kept[:MAX_PROGRAMS]
        self.c.settings_changed()
        self.watch()

    def _rewatch(self):
        """Looks every WATCH_MS only while there's something to look for."""
        needed = bool(self.c.settings.awake_apps) or (self.on and self.c.settings.awake_low_battery)
        if needed and not self.watcher.isActive():
            self.watcher.start()
        elif not needed:
            self.watcher.stop()

    def watch(self, running: set[str] | None = None, power: tuple[bool, int] | None | bool = False):
        """Stays awake while a chosen program is open (and lets go once it's closed), and lets the PC sleep
        when the battery runs low. running and power are for trying it out; normally Windows is asked."""
        s = self.c.settings
        state = battery() if power is False else power
        low = self.low = s.awake_low_battery and battery_low(state)
        if low and self.on:
            log.info("awake: the battery is low (%s%%), letting the PC sleep", state[1])
            self.let_sleep(ended=False)
            self.waived = set()
            self.c.say(tr("Corefold Awake"), tr("The battery is low, so your PC can go to sleep again."))
            return
        wanted = self._chosen()
        if wanted or self.mode == "app":
            names = program_names() if running is None else {n.lower() for n in running}
            open_now = sorted(wanted & names)
            self.waived &= set(open_now)
            if self.mode == "app" and self.app not in open_now:
                log.info("awake: %s closed", self.app)
                self.mode, self.app = "", ""
                self.let_sleep()
            elif not self.on and not self.low:
                hit = next((n for n in open_now if n not in self.waived), "")
                if hit:
                    self.mode, self.end, self.app, self.tray_minutes = "app", None, hit, None
                    log.info("awake: %s is open", hit)
                    self._apply()
                    self._changed()
        self._rewatch()

    def _chosen(self) -> set[str]:
        return {os.path.basename(p).lower() for p in self.c.settings.awake_apps}

    def app_title(self) -> str:
        path = next((p for p in self.c.settings.awake_apps if os.path.basename(p).lower() == self.app), self.app)
        return program_title(path)

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
        if self.mode == "app":
            return tr("Staying awake while {app} is open", app=self.app_title())
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
        current = "off" if not self.on else None if self.mode == "app" else "on" if self.end is None \
            else self.tray_minutes
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
        boot, off = booted_at(), shut_down_at()
        if at <= datetime.now() or (planned is not None and ((boot is not None and boot > planned) or
                                                             (off is not None and off > planned))):
            self._forget_shutdown()
            return None
        return at

    def plan_shutdown(self, at: datetime, replace: bool = False) -> int:
        """Asks Windows to turn the PC off at that time. Returns 0, or Windows' error code
        (ERROR_SHUTDOWN_IS_SCHEDULED: something else has one planned; replace=True cancels that first)."""
        if self.planned_shutdown() is not None or replace:
            run_shutdown("/a")
            self._forget_shutdown()
        # (never sooner than a minute: a time that went by while a question was open mustn't turn the PC off
        # at once, closing programs without saving)
        seconds = max(60, min(SHUTDOWN_MAX, math.ceil((at - datetime.now()).total_seconds())))
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
        self.watcher.stop()
        if self.on:
            set_state(ES_CONTINUOUS)
            log.info("awake: off (stopped)")
        self.mode, self.end, self.app = "", None, ""
        self._remove_tray_menu()

    def _remove_tray_menu(self):
        if self.menu is not None:
            self.c.menu_icons = [e for e in self.c.menu_icons if e[0] is not self.menu.menuAction()]
            self.c.menu.removeAction(self.menu.menuAction())
            self.menu.deleteLater()
            self.menu = None
