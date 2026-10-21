"""User settings (saved as JSON), start-with-Windows, and uninstall helpers.

Nothing here imports Qt, so the tiny helper process started by the right-click menu stays light.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .core import APP_NAME, Options
from .i18n import LANGUAGES
from .sounds import DONE_SOUNDS, OLD_NAMES

CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME
log = logging.getLogger("satsuma")

# key: English label (shown through tr())
SHORTCUT_LABELS = {"shift": "Shift", "ctrl+shift": "Ctrl + Shift", "alt": "Alt"}
ACCENTS = {  # key: (name, color)
    "tangerine": ("Tangerine", "#ff8c1a"),
    "lime": ("Lime", "#84cc16"),
    "berry": ("Berry", "#e11d74"),
    "ocean": ("Ocean", "#0ea5e9"),
    "grape": ("Grape", "#8b5cf6"),
    "graphite": ("Graphite", "#71717a"),
}
WHEEL_SIZES = {"small": 135, "medium": 168, "large": 205, "huge": 245}  # outer radius in px
WHEEL_SIZE_NAMES = {"small": "Small", "medium": "Medium", "large": "Large", "huge": "Huge"}
WHEEL_STYLES = {"ring": "Classic", "petals": "Petals", "slim": "Slim ring"}
LABEL_STYLES = {"both": "Icon + text", "text": "Text only", "icon": "Icons only"}
HOVER_STYLES = {"fill": "Fill", "outline": "Outline", "grow": "Grow only"}
SLICE_COLORS = {"plain": "One colour", "group": "By kind of action"}
TEXT_SIZES = {"small": 0.88, "normal": 1.0, "large": 1.15}
TEXT_SIZE_NAMES = {"small": "Small", "normal": "Normal", "large": "Large"}
WHEEL_POSITIONS = {"cursor": "Under the cursor", "center": "Middle of the screen"}
APPEARANCES = {"system": "Match Windows", "dark": "Dark", "light": "Light"}
MENU_STYLES = {"windows": "Windows menu", "wheel": "Satsuma wheel"}
SOUNDS = DONE_SOUNDS
CHOICES = {"wheel_style": WHEEL_STYLES, "label_style": LABEL_STYLES, "hover_style": HOVER_STYLES,
           "slice_colors": SLICE_COLORS, "text_size": TEXT_SIZES, "wheel_position": WHEEL_POSITIONS,
           "wheel_size": WHEEL_SIZES, "sound": SOUNDS, "language": LANGUAGES, "menu_style": MENU_STYLES,
           "appearance": APPEARANCES, "shortcut": SHORTCUT_LABELS}


@dataclass
class Settings:
    # behaviour
    language: str = "system"       # "system", "en" or "ar"
    shortcut: str = "shift"
    save_mode: str = "beside"      # "beside" or "folder"
    output_folder: str = str(Path.home() / "Downloads" / "Satsuma")
    jpg_quality: int = 90
    reveal_after: bool = False
    show_progress: bool = True
    enabled: bool = True
    first_run: bool = True
    check_updates: bool = True
    auto_update: bool = False
    last_version: str = ""
    ask_names: bool = False        # ask for a new name when converting several files
    sound: str = "satsuma"
    volume: int = 45
    sound_problems: bool = True    # a soft low sound when something fails
    sound_tick: bool = False       # a tiny tick when the wheel opens
    watch_downloads: bool = True   # offer "Convert to JPG?" for HEIC/WebP/AVIF downloads
    pack_on_every_wheel: bool = True
    fit_mb: float = 10.0
    watermark: dict = field(default_factory=lambda: {"text": "", "logo": "", "position": "br", "size": "medium",
                                                      "opacity": 60})
    context_menu: bool = True      # Satsuma in the right-click menu
    menu_style: str = "windows"    # "windows" (a normal submenu) or "wheel"
    menu_signature: str = ""       # what was last written to the right-click menu
    show_unavailable: bool = True  # dim slices that can't be used (False = hide them)
    # keeping the PC fast
    low_priority: bool = True      # conversions run "in the background" (below normal priority)
    one_at_a_time: bool = True     # queue conversions instead of running several at once
    # look
    appearance: str = "system"
    accent: str = "tangerine"
    custom_color: str = "#ff8c1a"  # used when accent == "custom"
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
        if s.accent not in ACCENTS and s.accent != "custom":
            s.accent = "tangerine"
        if not (len(s.custom_color) == 7 and s.custom_color.startswith("#")):
            s.custom_color = "#ff8c1a"
        s.sound = OLD_NAMES.get(s.sound, s.sound)
        for key, options in CHOICES.items():
            if getattr(s, key) not in options:
                setattr(s, key, getattr(defaults, key))
        s.wheel_opacity = min(100, max(60, s.wheel_opacity))
        s.volume = min(100, max(0, s.volume))
        s.per_page = min(16, max(6, s.per_page))
        s.jpg_quality = min(100, max(50, s.jpg_quality))
        s.fit_mb = min(4000.0, max(0.1, s.fit_mb))
        wm = dict(defaults.watermark)
        wm.update({k: v for k, v in s.watermark.items() if k in wm and type(v) is type(wm[k])})
        s.watermark = wm
        s.tool_order = {k: [t for t in v if isinstance(t, str)] for k, v in s.tool_order.items()
                        if isinstance(k, str) and isinstance(v, list)}
        s.hidden_tools = [t for t in s.hidden_tools if isinstance(t, str)]
        s.favorites = [t for t in s.favorites if isinstance(t, str)]
        return s

    def save(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            target = CONFIG_DIR / "settings.json"
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), "utf-8")
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
    """Command line that starts this copy of Satsuma with the given arguments."""
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
    from . import shellmenu
    set_startup(False)
    try:
        shellmenu.remove()
    except Exception:
        log.exception("could not remove the right-click menu")
    logging.shutdown()
    for folder in (CONFIG_DIR, LOG_DIR):
        shutil.rmtree(folder, ignore_errors=True)
