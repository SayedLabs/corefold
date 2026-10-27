"""
The right-click menu in File Explorer.

Two styles:
- "windows" (default): a normal Windows submenu, like 7-Zip's.  Right-click a photo →
  Corefold ▸ Convert to PNG / Resize… / Remove the background / … / All options…
- "wheel": one "Convert with Corefold" entry that opens the wheel.

Everything is written for the current user only (HKCU), so no admin rights are needed.
On Windows 11 these entries are under "Show more options". The menu is only rewritten when
something it shows has changed (see signature()), so starting Corefold stays fast.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from .core import ALL_EXT, APP_NAME, LEGACY_NAME, category, norm
from .i18n import tr

log = logging.getLogger("corefold")

CLASSES = r"Software\Classes"
FILE_ROOT = CLASSES + r"\SystemFileAssociations"
FOLDER_ROOT = CLASSES + r"\Directory"
LNK_ROOT = CLASSES + r"\lnkfile"  # shortcuts: "Uninstall with Corefold" (while the Uninstaller is in use)
ANY_ROOT = CLASSES + r"\*"   # every file; limited to the kinds Corefold has no own menu for (see any_file_filter)
DRIVE_ROOT = CLASSES + r"\Drive"   # drives: "Scan with Corefold" (while Scan is in use)
VERBS = ("Corefold", "CorefoldOpen", "CorefoldUninstall", "CorefoldScan")
LEGACY_VERBS = (LEGACY_NAME, LEGACY_NAME + "Open")  # written by versions before 1.7.0
UNLOCK_VERB = "CorefoldUnlock"  # Corefold Unlock's "What's using this?" (there while that tool is, Converter or not)
MENU_STYLES = {"windows": "Windows menu", "wheel": "Corefold wheel"}
OPEN_KEY = "corefold:open"     # "All options…": opens Corefold's window with the files
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
    name: str           # "Corefold" or "CorefoldOpen"
    title: str
    command: str = ""   # set for a single entry
    items: list | None = None  # set for a submenu
    applies_to: str = ""       # only show for files matching this (Windows search syntax)
    single: bool = False       # for one file at a time


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
    """The submenu for one file extension (".jpg"), for folders when ext is "", and for every other
    kind of file when ext is "*"."""
    from .converters import catalog
    cat = catalog(multi=False)
    hidden = set(settings.hidden_tools)
    items: list[Item] = []
    if ext == "*":
        add_pack = True
        ext = ".zip"  # only used for "is it usable" checks
    elif not ext:
        for t in _ordered(cat["folder"], "folder", settings):
            if t.key not in hidden:
                items.append(Item(t.key, _text(t)))
        add_pack = True
    elif ext:
        kind = category(Path("x" + ext))
        target = norm(ext)
        for t in _ordered(cat[kind], kind, settings):
            if t.key in hidden or (t.target and t.target == target) or not _usable_everywhere(t, ext):
                continue
            items.append(Item(t.key, _text(t)))
        add_pack = kind != "archive" and settings.pack_on_every_wheel
    if add_pack:
        first = True
        for t in _ordered(cat["files"], "files", settings):
            # (One PDF is only for PDFs together with pictures, which a menu made per extension can't tell)
            if t.key in hidden or t.key == "files:pdf" or not _usable_everywhere(t, ext or ".zip"):
                continue
            items.append(Item(t.key, _text(t), separator=first and bool(items)))
            first = False
    items.append(Item(OPEN_KEY, tr("All options…"), separator=bool(items)))
    return items


def plan(settings, command) -> list[Verb]:
    """Everything to write. command(*args) gives the command line that starts Corefold."""
    from . import tools as all_tools
    verbs: list[Verb] = []
    if all_tools.is_on(settings, "scan"):  # every file, folder and drive (and many at once)
        scan = command("--scan", '"%1"')
        verbs += [Verb(root, "CorefoldScan", tr("Scan with Corefold"), command=scan)
                  for root in (ANY_ROOT, FOLDER_ROOT, DRIVE_ROOT)]
    if not (settings.context_menu and all_tools.is_on(settings, "converter")):
        return verbs
    if all_tools.is_on(settings, "uninstaller"):  # shortcuts (part of the Converter's menu)
        verbs.append(Verb(LNK_ROOT, "CorefoldUninstall", tr("Uninstall with Corefold"),
                          command=command("--uninstall", '"%1"'), single=True))
    style = settings.menu_style if settings.menu_style in MENU_STYLES else "windows"
    cache: dict[str, list[Item]] = {}
    for ext in sorted(ALL_EXT):
        root = rf"{FILE_ROOT}\{ext}"
        kind = category(Path("x" + ext))
        if style == "windows":
            # (documents too: whether Word, Excel, PowerPoint or LibreOffice is there depends on the extension)
            key = norm(ext) if kind in ("image", "audio", "video", "archive", "document") else kind
            if key not in cache:
                cache[key] = items_for(ext, settings)
            verbs.append(Verb(root, "Corefold", APP_NAME, items=cache[key]))
        else:
            verbs.append(Verb(root, "Corefold", tr("Convert with Corefold"), command=command("--convert", '"%1"')))
            if kind == "archive":
                verbs.append(Verb(root, "CorefoldOpen", tr("Open in Corefold"), command=command("--browse", '"%1"')))
    if style == "windows":
        verbs.append(Verb(FOLDER_ROOT, "Corefold", APP_NAME, items=items_for("", settings)))
        verbs.append(Verb(ANY_ROOT, "Corefold", APP_NAME, items=items_for("*", settings), applies_to=any_file_filter()))
    else:
        verbs.append(Verb(FOLDER_ROOT, "Corefold", tr("Compress with Corefold"), command=command("--convert", '"%1"')))
        verbs.append(Verb(ANY_ROOT, "Corefold", tr("Compress with Corefold"), command=command("--convert", '"%1"'),
                          applies_to=any_file_filter()))
    return verbs


def any_file_filter() -> str:
    """Windows search syntax for "any file except the kinds that already have their own Corefold
    menu", so a photo doesn't get the Corefold entry twice."""
    return "NOT (" + " OR ".join(f"System.FileExtension:={e}" for e in sorted(ALL_EXT)) + ")"


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
    return [rf"{FILE_ROOT}\{ext}" for ext in sorted(ALL_EXT)] + [FOLDER_ROOT, ANY_ROOT, LNK_ROOT, DRIVE_ROOT]


def remove(unlock: bool = True):
    """Takes every Corefold entry out of the right-click menu (unlock=False: Corefold Unlock's stays)."""
    import winreg
    with _lock:
        for root in _all_roots():
            for verb in VERBS + LEGACY_VERBS + ((UNLOCK_VERB,) if unlock else ()):
                _delete_tree(winreg, rf"{root}\shell\{verb}")


def write(verbs: list[Verb], command, icon: str):
    """Replaces Corefold's right-click entries with these (run it off the window's thread)."""
    import winreg
    SZ, DWORD = winreg.REG_SZ, winreg.REG_DWORD
    with _lock:
        for root in _all_roots():
            for verb in VERBS + LEGACY_VERBS:
                _delete_tree(winreg, rf"{root}\shell\{verb}")
        for v in verbs:
            base = rf"{v.root}\shell\{v.name}"
            try:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
                    winreg.SetValueEx(k, "MUIVerb", 0, SZ, v.title)
                    if v.applies_to:
                        winreg.SetValueEx(k, "AppliesTo", 0, SZ, v.applies_to)
                    if icon:
                        winreg.SetValueEx(k, "Icon", 0, SZ, icon)
                    # "Player" keeps the entry when many files are selected (Windows hides it past 15)
                    winreg.SetValueEx(k, "MultiSelectModel", 0, SZ, "Single" if v.single else "Player")
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


# ----------------------------------------------------------------------------- Corefold Unlock's entry

def unlock_plan(command) -> list[Verb]:
    """The "What's using this?" entry on every file and folder, opening Corefold Unlock with it."""
    title, cmd = tr("What's using this?"), command("--unlock", '"%1"')
    return [Verb(ANY_ROOT, UNLOCK_VERB, title, command=cmd), Verb(FOLDER_ROOT, UNLOCK_VERB, title, command=cmd)]


def sync_unlock(on: bool, written: str, command, icon: str) -> str:
    """Writes or removes Corefold Unlock's entry, only if it changed since `written` (its signature then).
    Returns its signature now ("off" when it's not there)."""
    import winreg
    if not on:
        if written != "off":
            with _lock:
                for root in (ANY_ROOT, FOLDER_ROOT):
                    _delete_tree(winreg, rf"{root}\shell\{UNLOCK_VERB}")
            _tell_explorer()
        return "off"
    verbs = unlock_plan(command)
    sig = signature(verbs, command, icon)
    if sig == written:
        return sig
    SZ = winreg.REG_SZ
    with _lock:
        for v in verbs:
            base = rf"{v.root}\shell\{v.name}"
            _delete_tree(winreg, base)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
                winreg.SetValueEx(k, "MUIVerb", 0, SZ, v.title)
                if icon:
                    winreg.SetValueEx(k, "Icon", 0, SZ, icon)
                winreg.SetValueEx(k, "MultiSelectModel", 0, SZ, "Player")
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\command") as k:
                winreg.SetValueEx(k, "", 0, SZ, v.command)
    _tell_explorer()
    return sig


def _tell_explorer():
    """Asks Explorer to notice the new menu straight away."""
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        pass
