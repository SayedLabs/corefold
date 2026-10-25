"""Quick access: a menu and a wheel that open from any app with a shortcut, with the things a person
uses most (Corefold's tools, its quick actions, and their own apps, folders and websites).

What can go on them is listed once here (ACTIONS); the menu (quickmenu.py) and the wheel
(quickwheel.py) are only built the first time they're opened, and nothing at all runs while both (and
the "Keep a window on top" shortcut) are switched off in Settings."""
from __future__ import annotations

import logging
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices

from . import tools as all_tools
from .i18n import tr

log = logging.getLogger("corefold")

MAX_WHEEL = 12   # slices on the wheel (more would be too small to hit)
MAX_LINKS = 24


@dataclass(frozen=True)
class Action:
    id: str
    label: str        # English, shown through tr() (a link's own name is shown as it is)
    icon: str         # icons.py
    tool: str = ""    # only there while this tool is in use ("" = always)


ACTIONS: dict[str, Action] = {a.id: a for a in (
    Action("screenshot", "Take a screenshot", "screenshot", "capture"),
    Action("screenshot_later", "Screenshot with a timer", "timer", "capture"),
    Action("pin", "Pin what's copied to the screen", "pin", "capture"),
    Action("clipboard", "Clipboard history", "clipboard", "clipboard"),
    Action("paste", "Convert what's copied", "arrow_swap", "converter"),
    Action("again", "Do it again", "arrow_sync", "converter"),
    Action("archive", "Open an archive", "archive", "archive"),
    Action("explorer", "File Explorer", "folder_open"),
    Action("taskmgr", "Task Manager", "top_speed"),
    Action("lock", "Lock the PC", "lock_closed"),
    Action("home", "Open Corefold", "home"),
    Action("settings", "Settings", "settings"),
    Action("store", "All tools", "apps"),  # (its id is from when this was the Store)
    Action("updates", "Check for updates", "arrow_download"),
    Action("quickmenu", "Quick menu", "layout"),
    Action("ontop", "Keep a window on top", "pin"),
    Action("sleep", "Sleep", "weather_moon"),
    Action("hibernate", "Hibernate", "weather_moon"),
    Action("restart", "Restart", "arrow_clockwise"),
    Action("shutdown", "Shut down", "power"),
    Action("signout", "Sign out", "sign_out"),
    Action("recycle", "Empty the Recycle Bin", "delete"),
    Action("awake", "Keep awake", "coffee", "awake"),
    Action("pickcolor", "Pick a colour", "color", "colors"),
)}


@dataclass
class Entry:
    """One thing on the menu or the wheel, ready to show."""
    id: str
    label: str
    icon: str
    hint: str = ""     # a second, smaller line (a link's address)
    en: str = ""       # its English name (searching in English works in every language)


def link_icon(target: str) -> str:
    t = target.strip().lower()
    if t.startswith(("http://", "https://", "www.")):
        return "globe"
    if t.endswith((".exe", ".lnk", ".bat", ".cmd", ".appref-ms")):
        return "rocket"
    if t.endswith(("\\", "/")) or (os.name == "nt" and len(t) <= 3 and t[1:2] == ":"):
        return "folder"
    try:  # (not on a network share: one that's offline holds the menu up for seconds at every key typed)
        p = Path(target)
        if not t.startswith(("\\\\", "//")) and p.is_dir():
            return "folder"
    except (OSError, ValueError):
        pass
    return "document" if "." in Path(t).name else "link"


def new_link(name: str, target: str) -> dict:
    return {"id": uuid.uuid4().hex[:8], "name": name.strip()[:60], "target": target.strip()}


def entry(settings, action_id: str) -> Entry | None:
    """What an action looks like right now, or None if it can't be used (its tool was removed,
    a link was deleted)."""
    if action_id.startswith("tool:"):
        tool = next((t for t in all_tools.TOOLS if t.id == action_id[5:]), None)
        if tool is None or not all_tools.is_on(settings, tool.id):
            return None
        return Entry(action_id, tr(tool.nav), tool.icon, en=tool.nav)
    if action_id.startswith("link:"):
        for link in settings.quick_links:
            if link.get("id") == action_id[5:]:
                return Entry(action_id, link.get("name") or link.get("target", ""), link_icon(link.get("target", "")),
                             link.get("target", ""))
        return None
    a = ACTIONS.get(action_id)
    if a is None or (a.tool and not all_tools.is_on(settings, a.tool)):
        return None
    if a.id == "hibernate":  # (only on a PC where it's switched on)
        from .quicksys import hibernate_allowed
        if not hibernate_allowed():
            return None
    return Entry(a.id, tr(a.label), a.icon, en=a.label)


def entries(settings, ids: list[str]) -> list[Entry]:
    out, seen = [], set()
    for i in ids:
        if i in seen:
            continue
        seen.add(i)
        e = entry(settings, i)
        if e is not None:
            out.append(e)
    return out


def tool_entries(settings) -> list[Entry]:
    return [Entry(f"tool:{t.id}", tr(t.nav), t.icon, en=t.nav) for t in all_tools.enabled(settings)]


def matches(e: Entry, query: str) -> bool:
    q = query.strip().lower()
    return bool(q) and any(q in text.lower() for text in (e.label, e.en, e.hint))


def link_entries(settings) -> list[Entry]:
    return entries(settings, [f"link:{link.get('id')}" for link in settings.quick_links])


def choosable(settings) -> list[Entry]:
    """Everything that can be put on the menu or the wheel."""
    ids = [a.id for a in ACTIONS.values()] + [f"tool:{t.id}" for t in all_tools.enabled(settings)]
    ids += [f"link:{link.get('id')}" for link in settings.quick_links]
    return entries(settings, ids)


def open_target(target: str) -> bool:
    """Opens a file, folder, app or website the way double-clicking it would."""
    target = target.strip().strip('"')
    if not target:
        return False
    if target.lower().startswith("www."):
        target = "https://" + target
    if "://" in target:
        return QDesktopServices.openUrl(QUrl(target))
    try:
        if os.name == "nt":
            os.startfile(os.path.expandvars(target))  # type: ignore[attr-defined]
            return True
        return QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.expandvars(target)))
    except OSError:
        log.info("couldn't open %s", target)
        return False


def _windows(cmd: list[str]):
    """One of Windows' own programs, by its full path (never one of the same name in the current folder)."""
    if os.name != "nt":
        return
    from .core import system_tool
    cmd = [system_tool(cmd[0]), *cmd[1:]]
    try:
        subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True)
    except OSError:
        log.exception("couldn't start %s", cmd[0])


def _lock():
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.LockWorkStation()


class QuickAccess(QObject):
    """The shortcuts and what they open. Made only while the menu, the wheel or the keep-on-top shortcut is on."""

    def __init__(self, controller):
        super().__init__()
        from .hotkey import Hotkey
        self.c = controller
        self.menu = None    # quickmenu.QuickMenu, built the first time it's opened
        self.wheel = None   # quickwheel.LauncherWheel, the same
        self.button = None  # quickwheel.FloatingButton, while it's switched on
        self.menu_key = Hotkey()
        self.menu_key.pressed.connect(self.toggle_menu, Qt.ConnectionType.QueuedConnection)
        self.wheel_key = Hotkey()
        self.wheel_key.pressed.connect(self.open_wheel_from_key, Qt.ConnectionType.QueuedConnection)
        # "Keep a window on top" has a shortcut of its own (it works with the menu and the wheel off too)
        self.ontop_key = Hotkey()
        self.ontop_key.pressed.connect(lambda: self.toggle_on_top(), Qt.ConnectionType.QueuedConnection)
        self.front = 0       # the window that was in front when the menu or the wheel opened
        self._emptier = None  # quicksys.RecycleEmptier, while the Recycle Bin is being emptied
        self.apply_settings()

    @property
    def settings(self):
        return self.c.settings

    def apply_settings(self):
        s = self.settings
        self.menu_key.set(s.quick_menu_key if s.quick_menu else "off")
        self.wheel_key.set(s.quick_wheel_key if s.quick_wheel else "off")
        self.ontop_key.set(s.ontop_key)
        if not s.quick_menu and self.menu is not None:
            self.menu.close()
            self.menu.deleteLater()
            self.menu = None
        if not s.quick_wheel and self.wheel is not None:
            self.wheel.close()
            self.wheel.deleteLater()
            self.wheel = None
        want_button = s.quick_wheel and s.quick_button
        if want_button and self.button is None:
            from .quickwheel import FloatingButton
            self.button = FloatingButton(self)
            self.button.show_saved()
        elif not want_button and self.button is not None:
            self.button.close()
            self.button.deleteLater()
            self.button = None

    def keys_ok(self) -> tuple[bool, bool]:
        return self.menu_key.ok, self.wheel_key.ok

    def stop(self):
        self.menu_key.stop()
        self.wheel_key.stop()
        self.ontop_key.stop()
        for w in (self.menu, self.wheel, self.button):
            if w is not None:
                w.close()
                w.deleteLater()
        self.menu = self.wheel = self.button = None

    def apply_theme(self, theme):
        for w in (self.menu, self.wheel, self.button):
            if w is not None:
                w.theme = theme
                w.update()

    # ---- the menu

    def toggle_menu(self):
        if not self.settings.quick_menu:
            return
        if self.wheel is not None and self.wheel.isVisible():
            self.wheel.hide()
        if self.menu is None:
            from .quickmenu import QuickMenu
            self.menu = QuickMenu(self)
        if self.menu.isVisible():
            self.menu.close_animated()
        else:
            self._remember_front()
            self.menu.open()

    def ask(self, action_id: str):
        """Restarting, shutting down, signing out and emptying the Recycle Bin are asked about in the menu
        first (also when they were picked on the wheel)."""
        if self.menu is None:
            from .quickmenu import QuickMenu
            self.menu = QuickMenu(self)
        if self.wheel is not None and self.wheel.isVisible():
            self.wheel.hide()
        if action_id == "recycle":
            from .quicksys import recycle_bin
            if recycle_bin()[0] == 0:
                if self.menu.isVisible():
                    self.menu.hide()
                self.c.say(tr("The Recycle Bin is already empty"), "")
                return
        if not self.menu.isVisible():
            self.menu.open()
        self.menu.ask(action_id)

    def _remember_front(self):
        from .quicksys import foreground, usable
        self.front = usable(foreground())

    # ---- the wheel

    def _wheel(self):
        if self.wheel is None:
            from .quickwheel import LauncherWheel
            self.wheel = LauncherWheel(self)
        return self.wheel

    def open_wheel_from_key(self):
        if not self.settings.quick_wheel:
            return
        w = self._wheel()
        if w.isVisible():
            w.hide()
            return
        self._remember_front()
        if self.menu is not None and self.menu.isVisible():
            self.menu.hide()
        from .hotkey import parse
        spec = parse(self.settings.quick_wheel_key)
        w.popup(hold_vk=spec[1] if spec else 0)

    def open_wheel_at(self, center=None):
        if self.settings.quick_wheel:
            self._remember_front()
            self._wheel().popup(center=center)

    # ---- doing things

    def run(self, action_id: str, confirmed: bool = False):
        """Does what an entry on the menu or the wheel says (once the menu or wheel is out of the way)."""
        from .quicksys import CONFIRM
        if action_id in CONFIRM and not confirmed:
            QTimer.singleShot(0, lambda: self.ask(action_id))
            return
        delay = 180 if action_id in ("screenshot", "screenshot_later") else 0  # (not in the picture)
        if action_id in ("sleep", "hibernate", "restart", "shutdown", "signout"):
            delay = 400  # (the menu is gone before the screen goes)
        QTimer.singleShot(delay, lambda: self._run(action_id))

    def _run(self, action_id: str):
        c = self.c
        log.info("quick access: %s", action_id if not action_id.startswith("link:") else "a link")
        try:
            if action_id.startswith("tool:"):
                tool = next((t for t in all_tools.TOOLS if t.id == action_id[5:]), None)
                if tool is not None and all_tools.is_on(c.settings, tool.id):
                    c.show_window(tool.page)
            elif action_id.startswith("link:"):
                link = next((x for x in c.settings.quick_links if x.get("id") == action_id[5:]), None)
                if link is not None and not open_target(link.get("target", "")):
                    c.tell(tr("Couldn't open it"), tr("{name} isn't there any more, or can't be opened.",
                                                       name=link.get("name") or link.get("target", "")))
            else:
                self._builtin(action_id)
        except Exception:
            log.exception("quick access: %s failed", action_id)

    def _builtin(self, action_id: str):
        c = self.c
        actions: dict[str, Callable[[], object]] = {
            "screenshot": lambda: c.capture and c.capture.screenshot(),
            "screenshot_later": lambda: c.capture and c.capture.screenshot_later(),
            "pin": lambda: c.capture and c.capture.pin_last(),
            "clipboard": lambda: c.show_clipboard(),
            "paste": c.paste,
            "again": c.do_again,
            "archive": c.choose_archive,
            "explorer": lambda: _windows(["explorer.exe"]),
            "taskmgr": lambda: _windows(["taskmgr.exe"]),
            "lock": _lock,
            "home": c.show_window,
            "settings": lambda: c.show_window("General"),
            "store": lambda: c.show_window("Home"),
            "updates": lambda: c.check_updates(manual=True),
            "quickmenu": self.toggle_menu,
            "ontop": lambda: self.toggle_on_top(self.front),
            "recycle": self._empty_recycle_bin,
            "awake": lambda: c.awake and c.awake.toggle(),
            "pickcolor": lambda: c.colors and QTimer.singleShot(150, c.colors.pick),  # (once the menu is gone)
        }
        from .quicksys import SYSTEM, run as run_system
        for name in SYSTEM:
            actions.setdefault(name, lambda n=name: run_system(n))
        fn = actions.get(action_id)
        if fn is not None:
            fn()

    # ---- Windows' own commands and keeping a window on top (see quicksys.py)

    def _empty_recycle_bin(self):
        if self._emptier is not None:
            return
        from .core import human_size
        from .quicksys import RecycleEmptier

        def done(freed: int):
            self._emptier = None
            self.c.say(tr("Recycle Bin emptied"), tr("{size} freed", size=human_size(freed)) if freed else "")
        self._emptier = RecycleEmptier()
        self._emptier.done.connect(done, Qt.ConnectionType.QueuedConnection)
        self._emptier.start()

    def toggle_on_top(self, hwnd: int = 0):
        """Keeps the window in front (or hwnd) above all the others, or puts it back to normal."""
        from . import quicksys as sysc
        hwnd = sysc.usable(hwnd or sysc.foreground())
        if not hwnd:
            self.c.tell(tr("No window to keep on top"), tr("Click the window you want first, then try again."))
            return
        name = sysc.title(hwnd) or tr("This window")
        on = not sysc.is_on_top(hwnd)
        if not sysc.set_on_top(hwnd, on):
            self.c.tell(tr("Couldn't keep it on top"),
                        tr("Windows didn't let Corefold change “{title}”. It may be running as administrator.",
                           title=name))
            return
        log.info("quick access: a window %s", "stays on top" if on else "is back to normal")
        if on:
            from .hotkey import display
            key = display(self.settings.ontop_key)
            self.c.say(tr("“{title}” stays on top", title=name),
                       tr("Press {key} again to put it back to normal.", key=key) if key else "")
            rect = sysc.frame(hwnd)
            if rect.isValid():
                self._flash = sysc.Flash(rect, self.c.theme.accent)  # (kept until it's gone)
                self._flash.flash()
        else:
            self.c.say(tr("“{title}” is back to normal", title=name), "")
