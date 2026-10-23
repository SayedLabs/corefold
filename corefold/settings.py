"""User settings (saved as JSON), start-with-Windows, and uninstall helpers.

Nothing here imports Qt, so the tiny helper process started by the right-click menu stays light.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .core import APP_NAME, LEGACY_NAME, Options
from .i18n import LANGUAGES
from .sounds import DONE_SOUNDS, OLD_NAMES

CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME
# where versions before 1.7.0 (the old name) kept the same things
OLD_CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / LEGACY_NAME
OLD_LOG_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / LEGACY_NAME
OLD_OUTPUT_FOLDER = str(Path.home() / "Downloads" / LEGACY_NAME)
log = logging.getLogger("corefold")


def migrate_old_name() -> bool:
    """Versions before 1.7.0 had another name. Brings their settings, history and uninstall backups
    over to the new folders, once. Never deletes anything that couldn't be moved.
    Returns True if something was brought over."""
    moved = False
    for old, new in ((OLD_CONFIG_DIR, CONFIG_DIR), (OLD_LOG_DIR, LOG_DIR)):
        try:
            if not old.is_dir():
                continue
            if not new.exists():
                try:
                    os.rename(old, new)  # same drive: instant, nothing copied
                    moved = True
                    continue
                except OSError:
                    new.mkdir(parents=True, exist_ok=True)
            for item in list(old.iterdir()):
                target = new / item.name
                if target.exists():
                    continue  # the new folder already has its own; keep that one
                try:
                    shutil.move(str(item), str(target))
                    moved = True
                except OSError:
                    log.warning("couldn't bring %s over from the old folder", item)
            try:
                old.rmdir()  # only if it's empty now
            except OSError:
                pass
        except OSError:
            log.exception("couldn't bring old settings over from %s", old)
    return moved

# key: English label (shown through tr())
SHORTCUT_LABELS = {"shift": "Shift", "ctrl+shift": "Ctrl + Shift", "alt": "Alt"}
ACCENTS = {  # key: (name, colour on light, colour on dark) — from the Corefold design system
    "core": ("Teal", "#0a7768", "#3cc7b2"),
    "ocean": ("Ocean", "#2b5fd4", "#82a8ff"),
    "grape": ("Grape", "#6b4bd6", "#ac97ff"),
    "berry": ("Berry", "#c22f5c", "#ff7fa3"),
    "amber": ("Amber", "#8c5a00", "#f2b44a"),
    "lime": ("Lime", "#3f7a12", "#9ad66b"),
    "graphite": ("Graphite", "#53625f", "#9bb0ac"),
}
DEFAULT_ACCENT = "core"
OLD_ACCENTS = {"tangerine": "core"}  # accent names from before 1.7.0
WHEEL_SIZES = {"small": 148, "medium": 172, "large": 205, "huge": 245}  # outer radius in px
WHEEL_SIZE_NAMES = {"small": "Small", "medium": "Medium", "large": "Large", "huge": "Huge"}
WHEEL_STYLES = {"ring": "Classic", "petals": "Petals", "slim": "Slim ring", "bubbles": "Bubbles", "neon": "Neon",
                "glass": "Glass"}
LABEL_STYLES = {"both": "Icon + text", "text": "Text only", "icon": "Icons only"}
HOVER_STYLES = {"fill": "Fill", "outline": "Outline", "grow": "Grow only"}
SLICE_COLORS = {"plain": "One colour", "group": "By kind of action", "rainbow": "Rainbow"}
TEXT_SIZES = {"small": 0.88, "normal": 1.0, "large": 1.15}
TEXT_SIZE_NAMES = {"small": "Small", "normal": "Normal", "large": "Large"}
WHEEL_POSITIONS = {"cursor": "Under the cursor", "center": "Middle of the screen"}
APPEARANCES = {"system": "Match Windows", "dark": "Dark", "light": "Light"}
MENU_STYLES = {"windows": "Windows menu", "wheel": "Corefold wheel"}
TOAST_CORNERS = {"auto": "Bottom corner (automatic)", "bottom-right": "Bottom right", "bottom-left": "Bottom left",
                 "top-right": "Top right", "top-left": "Top left"}
TOAST_TIMES = {"short": "Short", "normal": "Normal", "long": "Long"}
TOAST_TIME_SCALE = {"short": 0.6, "normal": 1.0, "long": 2.0}
WINDOW_TEXT = {"small": "Small", "normal": "Normal", "large": "Large", "huge": "Extra large"}
WINDOW_TEXT_PT = {"small": 9, "normal": 10, "large": 11.5, "huge": 13}
SOUNDS = DONE_SOUNDS
UNUSED_MONTHS = {3: "3 months", 6: "6 months", 12: "a year"}
BACKUP_DAYS = {7: "a week", 30: "a month", 90: "3 months", 365: "a year"}
UNINSTALL_DEPTHS = {"safe": "Safe", "normal": "Normal", "deep": "Deep"}  # the same as uninstall.DEPTHS
VIEWER_WHEEL = {"zoom": "Zooms in and out", "browse": "Goes to the next or previous picture"}
SLIDESHOW_SECONDS = {2: "2 seconds", 4: "4 seconds", 8: "8 seconds"}
EXTRACT_MODES = {"smart": "Smart: no folder inside a folder", "folder": "Always into a new folder"}
CLIP_SHORTCUTS = {"win+alt+v": "Win + Alt + V", "ctrl+shift+v": "Ctrl + Shift + V", "ctrl+alt+v": "Ctrl + Alt + V",
                  "ctrl+`": "Ctrl + `", "off": "No shortcut"}
CLIP_MAX = {100: "100", 500: "500", 1000: "1000", 5000: "5000"}
CLIP_DAYS = {1: "a day", 7: "a week", 30: "a month", 365: "a year", 0: "Never"}
CAP_SHORTCUTS = {"ctrl+printscreen": "Ctrl + Print Screen", "printscreen": "Print Screen",
                 "win+shift+a": "Win + Shift + A", "off": "No shortcut"}
CAP_RECORD_SHORTCUTS = {"ctrl+shift+printscreen": "Ctrl + Shift + Print Screen", "win+shift+r": "Win + Shift + R",
                        "off": "No shortcut"}
CAP_AFTER = {"edit": "Open it to mark it up", "copy": "Only copy it"}
CAP_DELAY = {3: "3 seconds", 5: "5 seconds", 10: "10 seconds"}
CAP_FPS = {30: "30 frames a second", 60: "60 frames a second (smoother, bigger files)"}
VIEWER_SORTS = {"name": "Name", "newest": "Newest first", "oldest": "Oldest first", "biggest": "Biggest first"}
PLAYER_SKIP = {5: "5 seconds", 10: "10 seconds", 30: "30 seconds"}
PLAYER_WHEEL = {"volume": "Changes the volume", "seek": "Goes back and forward"}
CHOICES = {"wheel_style": WHEEL_STYLES, "label_style": LABEL_STYLES, "hover_style": HOVER_STYLES,
           "slice_colors": SLICE_COLORS, "text_size": TEXT_SIZES, "wheel_position": WHEEL_POSITIONS,
           "wheel_size": WHEEL_SIZES, "sound": SOUNDS, "language": LANGUAGES, "menu_style": MENU_STYLES,
           "appearance": APPEARANCES, "shortcut": SHORTCUT_LABELS, "toast_corner": TOAST_CORNERS,
           "toast_time": TOAST_TIMES, "window_text": WINDOW_TEXT, "unused_months": UNUSED_MONTHS,
           "backup_days": BACKUP_DAYS, "uninstall_depth": UNINSTALL_DEPTHS, "extract_mode": EXTRACT_MODES,
           "viewer_wheel": VIEWER_WHEEL, "viewer_sort": VIEWER_SORTS,
           "slideshow_seconds": SLIDESHOW_SECONDS, "clip_shortcut": CLIP_SHORTCUTS, "clip_max": CLIP_MAX,
           "clip_days": CLIP_DAYS, "cap_shortcut": CAP_SHORTCUTS, "cap_record_shortcut": CAP_RECORD_SHORTCUTS,
           "cap_after": CAP_AFTER, "cap_fps": CAP_FPS, "cap_delay": CAP_DELAY, "player_skip": PLAYER_SKIP,
           "player_wheel": PLAYER_WHEEL}


# Settings kept on disk while the window has changes that weren't saved yet (see window.py): the
# window shows them straight away, but they only stay if "Save" is pressed.
HELD: dict | None = None
RECENT_ARCHIVES = 8
# not settings a person edits: what the app remembers by itself
NOT_EDITED = {"enabled", "first_run", "last_version", "menu_signature", "window_geometry", "last_page",
              "last_action", "tour_done", "watermark", "fit_mb", "open_archives", "recent_archives",
              "tools_off", "known_tools", "open_images", "open_emails", "viewer_info", "recent_views",
              "clip_paused", "open_media", "player_volume", "nav_order", "nav_width", "nav_compact",
              "quick_button_pos", "quick_told"}
# the settings of each tab of the Settings page (each one has its own "Reset to defaults")
GENERAL_KEYS = ("language", "sound", "volume", "sound_problems", "show_progress", "low_priority", "one_at_a_time")
LOOK_KEYS = ("appearance", "accent", "custom_color", "animations", "toast_corner", "toast_time", "window_text",
             "sidebar_icons")
CONVERTER_KEYS = ("shortcut", "save_mode", "output_folder", "jpg_quality", "show_unavailable", "context_menu",
                  "menu_style", "ask_names", "reveal_after", "sound_tick", "watch_downloads", "pack_on_every_wheel")
WHEEL_KEYS = ("wheel_size", "wheel_style", "wheel_opacity", "label_style", "hover_style", "slice_colors", "text_size",
              "wheel_position", "per_page", "wheel_numbers")
FORMAT_KEYS = ("hidden_tools", "favorites", "tool_order")
UNINSTALLER_KEYS = ("unused_months", "backup_days", "restore_point", "uninstall_depth")
ARCHIVE_KEYS = ("extract_mode", "extract_nested", "remember_archives")
VIEWER_KEYS = ("viewer_wheel", "viewer_confirm_delete", "slideshow_seconds", "viewer_sort")
CLIPBOARD_KEYS = ("clip_shortcut", "clip_paste", "clip_max", "clip_days", "clip_images", "clip_ignore")
PLAYER_KEYS = ("player_resume", "player_skip", "player_wheel", "player_next", "player_subtitles")
CAPTURE_KEYS = ("cap_shortcut", "cap_record_shortcut", "cap_after", "cap_delay", "cap_save", "cap_fps", "cap_mic",
                "cap_pc_sound", "cap_cursor", "cap_countdown")
UPDATE_KEYS = ("check_updates", "auto_update")
QUICK_KEYS = ("quick_menu", "quick_menu_key", "quick_menu_items", "quick_wheel", "quick_wheel_key",
              "quick_wheel_items", "quick_button", "quick_links")
# what's on the quick menu and the wheel to begin with (see quick.py)
QUICK_MENU_ITEMS = ["screenshot", "record", "clipboard", "paste", "again", "archive", "explorer", "taskmgr"]
QUICK_WHEEL_ITEMS = ["screenshot", "clipboard", "paste", "again", "tool:space", "explorer", "settings", "quickmenu"]


def editable_keys() -> list[str]:
    return [f.name for f in fields(Settings) if f.name not in NOT_EDITED]


def general_keys() -> list[str]:
    return list(GENERAL_KEYS)


@dataclass
class Settings:
    # behaviour
    language: str = "system"       # "system", "en", "ar", "fr", "es" or "tr"
    shortcut: str = "shift"
    save_mode: str = "beside"      # "beside" or "folder"
    output_folder: str = str(Path.home() / "Downloads" / "Corefold")
    jpg_quality: int = 90
    reveal_after: bool = False
    show_progress: bool = True
    enabled: bool = True
    first_run: bool = True
    check_updates: bool = True
    auto_update: bool = False
    last_version: str = ""
    ask_names: bool = False        # ask for a new name when converting several files
    sound: str = "corefold"
    volume: int = 45
    sound_problems: bool = True    # a soft low sound when something fails
    sound_tick: bool = False       # a tiny tick when the wheel opens
    watch_downloads: bool = True   # offer "Convert to JPG?" for HEIC/WebP/AVIF downloads
    pack_on_every_wheel: bool = True
    fit_mb: float = 10.0
    watermark: dict = field(default_factory=lambda: {"text": "", "logo": "", "position": "br", "size": "medium",
                                                      "opacity": 60})
    context_menu: bool = True      # Corefold in the right-click menu
    menu_style: str = "windows"    # "windows" (a normal submenu) or "wheel"
    menu_signature: str = ""       # what was last written to the right-click menu
    # the Store: tools removed by this person, and the tools they've been offered (others are "New")
    tools_off: list = field(default_factory=list)
    known_tools: list = field(default_factory=list)
    open_archives: bool = True     # archives open in Corefold with a double-click ("Open with")
    recent_archives: list = field(default_factory=list)  # archives opened lately (newest first)
    extract_mode: str = "smart"    # "Extract here": "smart" (no folder inside a folder) or "folder"
    extract_nested: bool = False   # also unpack archives found inside
    remember_archives: bool = True  # list the archives opened lately on the Archive page
    # the Viewer
    open_images: bool = True       # pictures and email files open in Corefold ("Open with")
    open_emails: bool = True
    viewer_wheel: str = "zoom"     # the mouse wheel zooms, or goes to the next picture
    viewer_confirm_delete: bool = True
    viewer_sort: str = "name"      # the order of a folder's pictures: name, newest, oldest, biggest
    slideshow_seconds: int = 4
    viewer_info: bool = False      # the details panel is open
    recent_views: list = field(default_factory=list)  # pictures and emails opened lately (newest first)
    # the Clipboard
    clip_shortcut: str = "win+alt+v"  # opens the clipboard from any app
    clip_paste: bool = True        # picking something pastes it where you were typing (not only copies it)
    clip_max: int = 500            # copies kept (pinned ones don't count)
    clip_days: int = 30            # copies older than this are forgotten (0: never)
    clip_images: bool = True       # keep pictures that are copied
    clip_paused: bool = False      # new copies aren't kept for now
    clip_ignore: str = ""          # programs whose copies are never kept ("mybank.exe, notes.exe")
    # the Player
    open_media: bool = True        # videos and music open in Corefold ("Open with")
    player_resume: bool = True     # pick up where you left off
    player_skip: int = 10          # seconds the arrow keys jump
    player_wheel: str = "volume"   # the mouse wheel over the video: volume or seeking
    player_next: bool = True       # play the next file in the folder when one ends
    player_subtitles: bool = True  # turn on a video's own subtitles
    player_volume: int = 80
    # Capture
    cap_shortcut: str = "ctrl+printscreen"          # a screenshot
    cap_record_shortcut: str = "ctrl+shift+printscreen"  # start or stop recording
    cap_after: str = "edit"        # after a screenshot: open the editor, or only copy it
    cap_save: bool = True          # also save every screenshot in Pictures\Screenshots
    cap_delay: int = 5             # seconds "Screenshot with a timer" waits
    cap_fps: int = 30
    cap_mic: str = ""              # the microphone recorded with the screen ("" = none)
    cap_pc_sound: bool = True      # what the PC plays (videos, games, calls) is recorded too
    cap_cursor: bool = True        # the mouse shows in recordings
    cap_countdown: bool = True     # 3, 2, 1 before recording starts
    # Quick access: a menu and a wheel that open from any app
    quick_menu: bool = True
    quick_menu_key: str = "ctrl+alt+space"
    quick_menu_items: list = field(default_factory=lambda: list(QUICK_MENU_ITEMS))
    quick_wheel: bool = True
    quick_wheel_key: str = "alt+`"  # hold it, point, let go (or tap it and click)
    quick_wheel_items: list = field(default_factory=lambda: list(QUICK_WHEEL_ITEMS))
    quick_button: bool = False     # a small round button on the screen that opens the wheel
    quick_button_pos: list = field(default_factory=list)  # [x, y] where it was dragged to
    quick_links: list = field(default_factory=list)  # {"id", "name", "target"}: their own apps, folders, sites
    quick_told: bool = False       # the "press … for the quick menu" tip was shown
    # the Uninstaller
    unused_months: int = 3         # "not used for …" filter
    backup_days: int = 30          # backups of removed leftovers are kept this long
    restore_point: bool = False    # tick "Make a System Restore point first" to begin with
    uninstall_depth: str = "normal"  # how deep leftovers are cleaned up to begin with (UNINSTALL_DEPTHS)
    show_unavailable: bool = True  # dim slices that can't be used (False = hide them)
    # keeping the PC fast
    low_priority: bool = True      # conversions run "in the background" (below normal priority)
    one_at_a_time: bool = True     # queue conversions instead of running several at once
    # look
    appearance: str = "system"
    accent: str = DEFAULT_ACCENT
    custom_color: str = "#0a7768"  # used when accent == "custom"
    wheel_size: str = "medium"
    wheel_style: str = "ring"
    wheel_opacity: int = 94
    label_style: str = "both"
    hover_style: str = "fill"
    slice_colors: str = "plain"
    text_size: str = "normal"
    wheel_position: str = "cursor"
    per_page: int = 10             # slices per page; the rest go on "More" pages
    animations: bool = True
    hidden_tools: list = field(default_factory=list)
    favorites: list = field(default_factory=list)
    tool_order: dict = field(default_factory=dict)  # kind -> [tool keys] in the user's order
    wheel_numbers: bool = True     # click mode: press 1–9 to pick a slice
    # popups
    toast_corner: str = "auto"
    toast_time: str = "normal"
    # the window
    window_geometry: str = ""      # where it was and how big (Qt's saveGeometry, base64)
    last_page: str = "Home"
    window_text: str = "normal"
    sidebar_icons: bool = True
    nav_order: list = field(default_factory=list)   # the tools in the sidebar, in the order they were dragged
    nav_width: int = 232
    nav_compact: bool = False      # the sidebar shows icons only
    # "Do it again": the last thing you did (tool key, its choices; never a password)
    last_action: dict = field(default_factory=dict)
    tour_done: bool = False

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            data = json.loads((CONFIG_DIR / "settings.json").read_text("utf-8"))
        except (OSError, ValueError):
            return s
        if not isinstance(data, dict):
            return s
        defaults = cls()
        for key, default in asdict(defaults).items():
            if key not in data:
                continue
            value = data[key]
            if isinstance(default, float) and type(value) is int:
                value = float(value)
            if type(value) is type(default):
                setattr(s, key, value)
        s.accent = OLD_ACCENTS.get(s.accent, s.accent)
        if s.accent not in ACCENTS and s.accent != "custom":
            s.accent = DEFAULT_ACCENT
        if not (len(s.custom_color) == 7 and s.custom_color.startswith("#")):
            s.custom_color = defaults.custom_color
        s.sound = OLD_NAMES.get(s.sound, s.sound)
        if os.path.normcase(os.path.normpath(s.output_folder)) == os.path.normcase(os.path.normpath(OLD_OUTPUT_FOLDER)):
            s.output_folder = defaults.output_folder  # the old default folder: use the new default
        for key, options in CHOICES.items():
            if getattr(s, key) not in options:
                setattr(s, key, getattr(defaults, key))
        s.wheel_opacity = min(100, max(60, s.wheel_opacity))
        s.volume = min(100, max(0, s.volume))
        s.per_page = min(16, max(6, s.per_page))
        s.player_volume = min(100, max(0, s.player_volume))
        s.jpg_quality = min(100, max(50, s.jpg_quality))
        s.fit_mb = min(4000.0, max(0.1, s.fit_mb))
        wm = dict(defaults.watermark)
        wm.update({k: v for k, v in s.watermark.items() if k in wm and type(v) is type(wm[k])})
        s.watermark = wm
        s.tool_order = {k: [t for t in v if isinstance(t, str)] for k, v in s.tool_order.items()
                        if isinstance(k, str) and isinstance(v, list)}
        s.hidden_tools = [t for t in s.hidden_tools if isinstance(t, str)]
        s.favorites = [t for t in s.favorites if isinstance(t, str)]
        s.recent_archives = [t for t in s.recent_archives if isinstance(t, str)][:RECENT_ARCHIVES]
        s.tools_off = [t for t in s.tools_off if isinstance(t, str)]
        s.recent_views = [t for t in s.recent_views if isinstance(t, str)][:RECENT_ARCHIVES]
        s.known_tools = [t for t in s.known_tools if isinstance(t, str)]
        s.nav_order = [t for t in s.nav_order if isinstance(t, str)]
        s.nav_width = min(340, max(190, s.nav_width))
        s.quick_menu_items = [t for t in s.quick_menu_items if isinstance(t, str)][:40]
        s.quick_wheel_items = [t for t in s.quick_wheel_items if isinstance(t, str)][:12]
        s.quick_links = [{"id": str(x.get("id", "")), "name": str(x.get("name", "")), "target": str(x["target"])}
                         for x in s.quick_links if isinstance(x, dict) and isinstance(x.get("target"), str)
                         and x.get("id")][:24]
        s.quick_button_pos = [int(v) for v in s.quick_button_pos if type(v) is int][:2]
        if s.quick_menu_key == s.quick_wheel_key and s.quick_menu_key != "off":
            s.quick_wheel_key = defaults.quick_wheel_key if s.quick_menu_key != defaults.quick_wheel_key else "off"
        if not s.known_tools and not s.first_run:  # from before the Store: the tools they had
            from .tools import ORIGINAL
            s.known_tools = list(ORIGINAL)
        if not isinstance(s.last_action.get("key"), str) or not isinstance(s.last_action.get("params", {}), dict):
            s.last_action = {}
        return s

    def save(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            target = CONFIG_DIR / "settings.json"
            tmp = target.with_suffix(".tmp")
            data = asdict(self)
            if HELD:
                data.update(HELD)  # unsaved changes in the window don't reach the disk
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
            os.replace(tmp, target)  # never leaves a half-written settings file
        except OSError:
            log.exception("could not save settings")

    def options(self, rename_base: str = "", rename_start: int = 1) -> Options:
        folder = Path(self.output_folder) if self.save_mode == "folder" else None
        return Options(output_folder=folder, jpg_quality=self.jpg_quality,
                       rename_base=rename_base, rename_start=rename_start)

    @property
    def shortcut_label(self) -> str:
        from .i18n import tr
        return tr(SHORTCUT_LABELS.get(self.shortcut, "Shift"))


# ----------------------------------------------------------------------------- start with Windows

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def app_command(*args: str) -> str:
    """Command line that starts this copy of Corefold with the given arguments."""
    if getattr(sys, "frozen", False):
        base = f'"{sys.executable}"'
    else:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        main_py = Path(__file__).resolve().parent.parent / "main.py"
        base = f'"{pyw if pyw.exists() else sys.executable}" "{main_py}"'
    return " ".join([base, *args])


def app_icon() -> str:
    return sys.executable if getattr(sys, "frozen", False) else ""


def launch_command() -> str:
    return app_command("--background")


def startup_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def remove_old_startup_entry() -> bool:
    """Removes the start-with-Windows entry made by versions before 1.7.0 (the old name).
    True if there was one, so the new entry can take its place."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as k:
            winreg.QueryValueEx(k, LEGACY_NAME)
            winreg.DeleteValue(k, LEGACY_NAME)
            return True
    except OSError:
        return False


def set_startup(on: bool):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        log.exception("could not change startup setting")


# ----------------------------------------------------------------------------- uninstall

def installed_uninstaller() -> Path | None:
    """The installer's uninstall program, or None for the portable / from-source version."""
    if not getattr(sys, "frozen", False):
        return None
    found = sorted(Path(sys.executable).resolve().parent.glob("unins*.exe"))
    return found[0] if found else None


def remove_user_data():
    """Portable version clean-up: settings, logs, the startup entry and the right-click menu."""
    from . import assoc, shellmenu
    set_startup(False)
    try:
        shellmenu.remove()
    except Exception:
        log.exception("could not remove the right-click menu")
    try:
        assoc.unregister()  # "Open with" for archives
    except Exception:
        log.exception("could not remove opening archives with Corefold")
    logging.shutdown()
    shutil.rmtree(CONFIG_DIR, ignore_errors=True)
    # LOG_DIR also holds the backups of apps removed with Corefold ("Uninstall backups"): those are
    # the person's files, so they stay (they can be deleted from the Apps page, or by hand)
    try:
        items = list(LOG_DIR.iterdir())
    except OSError:
        items = []
    for item in items:
        if item.name == "Uninstall backups" and any(item.iterdir()):
            continue
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            try:
                item.unlink()
            except OSError:
                pass
    try:
        LOG_DIR.rmdir()  # only if nothing is left
    except OSError:
        pass
