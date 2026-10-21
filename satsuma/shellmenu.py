"""
The right-click menu in File Explorer.

Two styles:
- "windows" (default): a normal Windows submenu, like 7-Zip's.  Right-click a photo →
  Satsuma ▸ Convert to PNG / Resize… / Remove the background / … / All options…
- "wheel": one "Convert with Satsuma" entry that opens the wheel.

Everything is written for the current user only (HKCU), so no admin rights are needed.
On Windows 11 these entries are under "Show more options". The menu is only rewritten when
something it shows has changed (see signature()), so starting Satsuma stays fast.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from .core import ALL_EXT, APP_NAME, category, norm
from .i18n import tr

log = logging.getLogger("satsuma")

CLASSES = r"Software\Classes"
FILE_ROOT = CLASSES + r"\SystemFileAssociations"
FOLDER_ROOT = CLASSES + r"\Directory"
VERBS = ("Satsuma", "SatsumaOpen")
MENU_STYLES = {"windows": "Windows menu", "wheel": "Satsuma wheel"}
OPEN_KEY = "satsuma:open"     # "All options…": opens Satsuma's window with the files
SEPARATOR_BEFORE = 0x20       # ECF_SEPARATORBEFORE
_lock = threading.Lock()


@dataclass
class Item:
    key: str            # tool key, or OPEN_KEY
    text: str
    separator: bool = False


@dataclass
class Verb:
    root: str           # registry key that holds "shell"
    name: str           # "Satsuma" or "SatsumaOpen"
    title: str
    command: str = ""   # set for a single entry
    items: list | None = None  # set for a submenu


# ----------------------------------------------------------------------------- what the menu shows

def _ordered(tools, kind: str, settings) -> list:
    fav = {k: i for i, k in enumerate(settings.favorites)}
    custom = {k: i for i, k in enumerate(settings.tool_order.get(kind, []))}
    default = {t.key: i for i, t in enumerate(tools)}
    return sorted(tools, key=lambda t: (fav.get(t.key, len(fav)), custom.get(t.key, len(custom) + default[t.key])))


def _usable_everywhere(tool, ext: str) -> bool:
    """Leaves out tools that can never work for this kind of file (e.g. Office missing)."""
    if not (tool.static_check and tool.check):
        return True
    try:
        return not tool.check([Path("x" + ext)])
    except Exception:
        return True


def _text(tool) -> str:
    text = tool.description
    if (tool.ask or tool.opens) and "…" not in text:
        text += "…"
    return text


def items_for(ext: str, settings) -> list[Item]:
    """The submenu for one file extension (".jpg"), or for folders when ext is ""."""
    from .converters import catalog
    cat = catalog(multi=False)
    hidden = set(settings.hidden_tools)
    items: list[Item] = []
    if ext:
        kind = category(Path("x" + ext))
        target = norm(ext)
        for t in _ordered(cat[kind], kind, settings):
            if t.key in hidden or (t.target and t.target == target) or not _usable_everywhere(t, ext):
                continue
            items.append(Item(t.key, _text(t)))
        add_pack = kind != "archive" and settings.pack_on_every_wheel
    else:
        add_pack = True
    if add_pack:
        first = True
        for t in _ordered(cat["files"], "files", settings):
            if t.key in hidden or not _usable_everywhere(t, ext or ".zip"):
                continue
            items.append(Item(t.key, _text(t), separator=first and bool(items)))
            first = False
    items.append(Item(OPEN_KEY, tr("All options…"), separator=bool(items)))
    return items


def plan(settings, command) -> list[Verb]:
    """Everything to write. command(*args) gives the command line that starts Satsuma."""
    verbs: list[Verb] = []
    style = settings.menu_style if settings.menu_style in MENU_STYLES else "windows"
    cache: dict[str, list[Item]] = {}
    for ext in sorted(ALL_EXT):
        root = rf"{FILE_ROOT}\{ext}"
        kind = category(Path("x" + ext))
        if style == "windows":
            key = norm(ext) if kind in ("image", "audio", "video", "archive") else kind
            if key not in cache:
                cache[key] = items_for(ext, settings)
            verbs.append(Verb(root, "Satsuma", APP_NAME, items=cache[key]))
        else:
            verbs.append(Verb(root, "Satsuma", tr("Convert with Satsuma"), command=command("--convert", '"%1"')))
            if kind == "archive":
                verbs.append(Verb(root, "SatsumaOpen", tr("Open in Satsuma"), command=command("--browse", '"%1"')))
    if style == "windows":
        verbs.append(Verb(FOLDER_ROOT, "Satsuma", APP_NAME, items=items_for("", settings)))
    else:
        verbs.append(Verb(FOLDER_ROOT, "Satsuma", tr("Compress with Satsuma"), command=command("--convert", '"%1"')))
    return verbs


def signature(verbs: list[Verb], command, icon: str) -> str:
    data = json.dumps([asdict(v) for v in verbs] + [command("--run", "x"), icon], sort_keys=True,
                      ensure_ascii=False)
    return hashlib.sha1(data.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------- writing to the registry

def _delete_tree(winreg, path: str):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | winreg.KEY_WRITE)
    except OSError:
        return
    with key:
        while True:
            try:
                sub = winreg.EnumKey(key, 0)
            except OSError:
                break
            _delete_tree(winreg, path + "\\" + sub)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


def _all_roots() -> list[str]:
    return [rf"{FILE_ROOT}\{ext}" for ext in sorted(ALL_EXT)] + [FOLDER_ROOT]


def remove():
    """Takes every Satsuma entry out of the right-click menu."""
    import winreg
    with _lock:
        for root in _all_roots():
            for verb in VERBS:
                _delete_tree(winreg, rf"{root}\shell\{verb}")


def write(verbs: list[Verb], command, icon: str):
    """Replaces Satsuma's right-click entries with these (run it off the window's thread)."""
    import winreg
    SZ, DWORD = winreg.REG_SZ, winreg.REG_DWORD
    with _lock:
        for root in _all_roots():
            for verb in VERBS:
                _delete_tree(winreg, rf"{root}\shell\{verb}")
        for v in verbs:
            base = rf"{v.root}\shell\{v.name}"
            try:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
                    winreg.SetValueEx(k, "MUIVerb", 0, SZ, v.title)
                    if icon:
                        winreg.SetValueEx(k, "Icon", 0, SZ, icon)
                    # "Player" keeps the entry when many files are selected (Windows hides it past 15)
                    winreg.SetValueEx(k, "MultiSelectModel", 0, SZ, "Player")
                    if v.items is not None:
                        winreg.SetValueEx(k, "SubCommands", 0, SZ, "")  # "this is a submenu"
                if v.items is None:
                    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\command") as k:
                        winreg.SetValueEx(k, "", 0, SZ, v.command)
                    continue
                for i, item in enumerate(v.items):
                    sub = rf"{base}\shell\{i + 1:02d}"  # Windows sorts submenu entries by key name
                    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, sub) as k:
                        winreg.SetValueEx(k, "MUIVerb", 0, SZ, item.text)
                        winreg.SetValueEx(k, "MultiSelectModel", 0, SZ, "Player")
                        if item.separator:
                            winreg.SetValueEx(k, "CommandFlags", 0, DWORD, SEPARATOR_BEFORE)
                    args = ("--open", '"%1"') if item.key == OPEN_KEY else ("--run", item.key, '"%1"')
                    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, sub + r"\command") as k:
                        winreg.SetValueEx(k, "", 0, SZ, command(*args))
            except OSError:
                log.exception("could not write right-click menu for %s", v.root)
        _tell_explorer()


def _tell_explorer():
    """Asks Explorer to notice the new menu straight away."""
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        pass
