"""User settings (saved as JSON), start-with-Windows, and uninstall helpers."""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .converters import Options

APP_NAME = "Satsuma"
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME
log = logging.getLogger("satsuma")

SHORTCUT_LABELS = {"shift": "Shift", "ctrl+shift": "Ctrl + Shift", "alt": "Alt"}
ACCENTS = {  # key: (name, color)
    "tangerine": ("Tangerine", "#ff8c1a"),
    "lime": ("Lime", "#84cc16"),
    "berry": ("Berry", "#e11d74"),
    "ocean": ("Ocean", "#0ea5e9"),
    "grape": ("Grape", "#8b5cf6"),
    "graphite": ("Graphite", "#71717a"),
}
WHEEL_SIZES = {"small": 135, "medium": 168, "large": 205}  # outer radius in px
APPEARANCES = ("system", "dark", "light")


@dataclass
class Settings:
    # behaviour
    shortcut: str = "shift"
    save_mode: str = "beside"  # "beside" or "folder"
    output_folder: str = str(Path.home() / "Downloads" / "Satsuma")
    jpg_quality: int = 90
    reveal_after: bool = False
    show_progress: bool = True
    enabled: bool = True
    first_run: bool = True
    check_updates: bool = True
    auto_update: bool = False
    last_version: str = ""
    # look
    appearance: str = "system"
    accent: str = "tangerine"
    wheel_size: str = "medium"
    animations: bool = True
    hidden_tools: list = field(default_factory=list)

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            data = json.loads((CONFIG_DIR / "settings.json").read_text("utf-8"))
        except (OSError, ValueError):
            return s
        if not isinstance(data, dict):
            return s
        for key, default in asdict(s).items():
            if key in data and type(data[key]) is type(default):
                setattr(s, key, data[key])
        if s.shortcut not in SHORTCUT_LABELS:
            s.shortcut = "shift"
        if s.accent not in ACCENTS:
            s.accent = "tangerine"
        if s.wheel_size not in WHEEL_SIZES:
            s.wheel_size = "medium"
        if s.appearance not in APPEARANCES:
            s.appearance = "system"
        s.jpg_quality = min(100, max(50, s.jpg_quality))
        s.hidden_tools = [t for t in s.hidden_tools if isinstance(t, str)]
        return s

    def save(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            (CONFIG_DIR / "settings.json").write_text(json.dumps(asdict(self), indent=2), "utf-8")
        except OSError:
            log.exception("could not save settings")

    def options(self) -> Options:
        folder = Path(self.output_folder) if self.save_mode == "folder" else None
        return Options(output_folder=folder, jpg_quality=self.jpg_quality)

    @property
    def shortcut_label(self) -> str:
        return SHORTCUT_LABELS.get(self.shortcut, "Shift")


# ----------------------------------------------------------------------------- start with Windows

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{pyw if pyw.exists() else sys.executable}" "{main_py}" --background'


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
    """Portable version clean-up: settings, logs and the startup entry."""
    set_startup(False)
    logging.shutdown()
    for folder in (CONFIG_DIR, LOG_DIR):
        shutil.rmtree(folder, ignore_errors=True)
