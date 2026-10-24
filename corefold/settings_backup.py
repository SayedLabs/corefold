"""Keeping Corefold's settings safe: a file to bring them back later or on another PC ("Save my
settings…"), and copies made by themselves before every update, used if settings.json is ever lost or
damaged.

No Qt here: settings.py uses it while loading, also in the small helper process."""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

log = logging.getLogger("corefold")

FORMAT = "corefold-settings"
FORMAT_VERSION = 1
EXTENSION = ".corefold"
FOLDER = "settings-backups"   # in the settings folder: the copies made before updates
KEEP = 5                      # copies kept (the newest)
MAX_BYTES = 4_000_000         # a settings file is a few KB: anything this big isn't one
MAX_TARGET = 2000             # a quick link's target (a program, file, folder or site) longer than this is dropped
# not "settings" a person edits, but their choices all the same: they go in the file too. (Window
# size and place, recent files, "Do it again" and other things the app only remembers stay out.)
ALSO_KEPT = ("tools_off", "known_tools", "nav_order", "section_order", "nav_compact", "watermark", "fit_mb",
             "open_archives", "open_images", "open_emails", "open_media", "viewer_info", "player_volume")
RECOVERED: Path | None = None  # the copy used at start because settings.json was missing or damaged


class BadFile(Exception):
    """Why a file can't be brought back (English, shown through tr())."""


@dataclass
class Saved:
    """A settings file that was read and checked."""
    settings: dict           # the saved values (only the settings Corefold knows)
    app_version: str         # the Corefold that saved them ("" if not known)
    saved: datetime | None   # when
    startup: bool | None     # "Start Corefold when Windows starts", if it was saved


def config_dir() -> Path:
    from .settings import CONFIG_DIR
    return CONFIG_DIR


def kept_keys() -> list[str]:
    """The settings that go in the file and are brought back from it."""
    from .settings import Settings, editable_keys
    names = {f.name for f in fields(Settings)}
    return [k for k in editable_keys() if k in names] + [k for k in ALSO_KEPT if k in names]


def file_name(when: datetime | None = None) -> str:
    return f"Corefold settings {(when or datetime.now()).strftime('%Y-%m-%d')}{EXTENSION}"


def export(settings, path: Path, startup: bool | None = None, version: str | None = None):
    """Writes the settings to one file. Raises OSError if it can't."""
    if version is None:
        from . import __version__ as version
    values = asdict(settings)
    data = {"format": FORMAT, "format_version": FORMAT_VERSION, "app_version": version,
            "saved": datetime.now().isoformat(timespec="seconds"),
            "settings": {k: values[k] for k in kept_keys()}}
    if startup is not None:
        data["start_with_windows"] = bool(startup)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)


def _looks_like_settings(data: dict) -> bool:
    """A plain settings.json (like the copies made before updates) can be brought back too."""
    from .settings import Settings
    names = {f.name for f in fields(Settings)}
    return sum(1 for k in data if k in names) >= 5


def read(path: Path) -> Saved:
    """Reads and checks a settings file. Raises BadFile with the reason."""
    try:
        if path.stat().st_size > MAX_BYTES:
            raise BadFile("This isn't a Corefold settings file.")
        raw = path.read_bytes()
    except OSError as e:
        raise BadFile("Couldn't open this file.") from e
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError as e:
        raise BadFile("This isn't a Corefold settings file.") from e
    if not isinstance(data, dict):
        raise BadFile("This isn't a Corefold settings file.")
    if data.get("format") != FORMAT:
        if "format" not in data and _looks_like_settings(data):
            try:
                when = datetime.fromtimestamp(path.stat().st_mtime)
            except (OSError, ValueError):
                when = None
            return Saved(_safe_links(data), "", when, None)
        raise BadFile("This isn't a Corefold settings file.")
    version = data.get("format_version")
    if type(version) is not int or version < 1:
        raise BadFile("This isn't a Corefold settings file.")
    if version > FORMAT_VERSION:
        raise BadFile("These settings were saved by a newer Corefold. Update Corefold first, then try again.")
    values = data.get("settings")
    if not isinstance(values, dict):
        raise BadFile("This isn't a Corefold settings file.")
    try:
        when = datetime.fromisoformat(str(data.get("saved", "")))
    except ValueError:
        when = None
    app_version = data.get("app_version")
    startup = data.get("start_with_windows")
    return Saved(_safe_links(values), app_version if isinstance(app_version, str) else "", when,
                 startup if isinstance(startup, bool) else None)


def _safe_links(values: dict) -> dict:
    """Quick links open programs: only ones with a plain, sensible target are kept from a file."""
    links = values.get("quick_links")
    if links is None:
        return values
    values = dict(values)
    values["quick_links"] = [x for x in links if isinstance(links, list) and isinstance(x, dict)
                             and isinstance(x.get("target"), str) and 0 < len(x["target"]) <= MAX_TARGET
                             and isinstance(x.get("name", ""), str)] if isinstance(links, list) else []
    return values


def links(saved: Saved) -> list[tuple[str, str]]:
    """(name, target) of the quick links the file brings (they open programs, files and sites)."""
    return [(str(x.get("name") or ""), x["target"]) for x in saved.settings.get("quick_links") or []
            if x.get("id")]


def apply(current, saved: Saved):
    """The settings to use from now on: the saved ones, checked the same way as when Corefold starts, over
    the current ones (what the file doesn't have, like the window's size, stays as it is)."""
    from . import tools as all_tools
    from .settings import Settings
    restored = Settings.from_data(saved.settings)
    out = copy.deepcopy(current)
    for key in kept_keys():
        if key in saved.settings:
            setattr(out, key, copy.deepcopy(getattr(restored, key)))
    # the tools: as they were in the file, except tools newer than the file (it couldn't choose for
    # those, so they stay as they are now). A tool that isn't ready is never switched on: all_tools
    # only uses ready tools, whatever the list says.
    known_then = set(restored.known_tools) if "known_tools" in saved.settings else set()
    off = set(restored.tools_off) if "tools_off" in saved.settings else set(current.tools_off)
    for tool in all_tools.TOOLS:
        if tool.id not in known_then:
            off.discard(tool.id)
            if tool.id in current.tools_off:
                off.add(tool.id)
    out.tools_off = sorted(off)
    out.known_tools = sorted(known_then | set(current.known_tools))
    return out


# ----------------------------------------------------------------------------- copies made by themselves

def backups() -> list[Path]:
    """The copies made before updates, newest first."""
    folder = config_dir() / FOLDER
    try:
        found = [p for p in folder.glob("settings-*.json") if p.is_file()]
    except OSError:
        return []

    def age(p: Path):
        try:
            return p.stat().st_mtime, p.name
        except OSError:
            return 0, p.name
    return sorted(found, key=age, reverse=True)


def keep_copy(version: str, keep: int = KEEP) -> Path | None:
    """Copies settings.json into settings-backups (before an update is installed, or before settings are
    brought back from a file), and keeps only the newest few copies. Never raises."""
    try:
        src = config_dir() / "settings.json"
        text = src.read_text("utf-8")
        if not isinstance(json.loads(text), dict):
            return None  # a damaged file would only push a good copy out
        folder = config_dir() / FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^0-9A-Za-z.+-]", "_", version or "dev")[:40]
        target = folder / f"settings-{safe}-{datetime.now().strftime('%Y-%m-%d')}.json"
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text, "utf-8")
        os.replace(tmp, target)
        now = time.time()
        os.utime(target, (now, now))
        for old in backups()[keep:]:
            try:
                old.unlink()
            except OSError:
                pass
        log.info("kept a copy of the settings: %s", target.name)
        return target
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.exception("couldn't keep a copy of the settings")
        return None


def recover(path: Path, broken: bool) -> dict | None:
    """settings.json is missing or damaged. A damaged one is kept aside as settings.broken.json (never just
    written over), and the newest copy made before an update is used instead, if there is one."""
    global RECOVERED
    if broken:
        try:
            os.replace(path, path.with_name("settings.broken.json"))
            log.warning("the settings file was damaged; it was kept as settings.broken.json")
        except OSError:
            log.exception("couldn't keep the damaged settings file aside")
    for copy_path in backups():
        try:
            data = json.loads(copy_path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            log.warning("the settings file was %s; using the copy %s", "damaged" if broken else "missing",
                        copy_path.name)
            RECOVERED = copy_path
            return data
    return None
