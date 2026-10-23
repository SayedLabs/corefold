"""Opening files with a double-click: archives (Corefold Archive), pictures and emails (Corefold Viewer),
videos and music (Corefold Player).

Corefold adds itself to "Open with" for these (for the current user only, no admin rights), and
registers as an app Windows can make the default for them. Windows doesn't let apps make themselves
the default: the person picks it in Settings, which Corefold can open at the right place."""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

from .core import APP_NAME

log = logging.getLogger("corefold")

CLASSES = r"Software\Classes"
CAPABILITIES = r"Software\Corefold\Capabilities"
REGISTERED = r"Software\RegisteredApplications"

# one change to the registry at a time: switching a setting on and off quickly started two at once,
# and one could delete a key the other was still writing to
_lock = threading.RLock()
_pending = None


@dataclass(frozen=True)
class Group:
    progid: str
    flag: str              # what the double-click runs: Corefold.exe <flag> "file"
    extensions: tuple


GROUPS = {
    # the archives a double-click should open (the ones people meet; Corefold reads more than these)
    "archive": Group("Corefold.Archive", "--browse",
                     (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".txz", ".iso", ".cab", ".zipx",
                      ".001", ".lzh", ".arj", ".wim")),
    "image": Group("Corefold.Picture", "--view",
                   (".jpg", ".jpeg", ".jfif", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif",
                    ".avif", ".ico")),
    "email": Group("Corefold.Email", "--view", (".eml", ".msg")),
    "media": Group("Corefold.Media", "--play",
                   (".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm", ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts", ".flv",
                    ".3gp", ".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma")),
}
# (kept for older callers: the archive group)
PROGID = GROUPS["archive"].progid
EXTENSIONS = GROUPS["archive"].extensions


def _command(app_command, group: Group) -> str:
    return app_command(group.flag, '"%1"')


def is_current(app_command, name: str = "archive") -> bool:
    """True if the registration is there and points at this copy of Corefold."""
    import winreg
    group = GROUPS[name]
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{CLASSES}\{group.progid}\shell\open\command") as k:
            return winreg.QueryValueEx(k, "")[0] == _command(app_command, group)
    except OSError:
        return False


def _registered(name: str = "archive") -> bool:
    import winreg
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{CLASSES}\{GROUPS[name].progid}").Close()
        return True
    except OSError:
        return False


def register(app_command, icon: str, description: str, name: str = "archive"):
    import winreg
    group = GROUPS[name]
    SZ = winreg.REG_SZ
    hk = winreg.HKEY_CURRENT_USER
    with winreg.CreateKey(hk, rf"{CLASSES}\{group.progid}") as k:
        winreg.SetValueEx(k, "", 0, SZ, description)
        winreg.SetValueEx(k, "FriendlyTypeName", 0, SZ, description)
    if icon:
        with winreg.CreateKey(hk, rf"{CLASSES}\{group.progid}\DefaultIcon") as k:
            winreg.SetValueEx(k, "", 0, SZ, f'"{icon}",0')
    with winreg.CreateKey(hk, rf"{CLASSES}\{group.progid}\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, SZ, _command(app_command, group))
    for ext in group.extensions:
        with winreg.CreateKey(hk, rf"{CLASSES}\{ext}\OpenWithProgids") as k:
            winreg.SetValueEx(k, group.progid, 0, winreg.REG_NONE, b"")
    with winreg.CreateKey(hk, CAPABILITIES) as k:
        winreg.SetValueEx(k, "ApplicationName", 0, SZ, APP_NAME)
        winreg.SetValueEx(k, "ApplicationDescription", 0, SZ, description)
    with winreg.CreateKey(hk, CAPABILITIES + r"\FileAssociations") as k:
        for ext in group.extensions:
            winreg.SetValueEx(k, ext, 0, SZ, group.progid)
    with winreg.CreateKey(hk, REGISTERED) as k:
        winreg.SetValueEx(k, APP_NAME, 0, SZ, CAPABILITIES)
    _tell_explorer()
    log.info("%s files open with Corefold", name)


def unregister(name: str | None = None):
    """Takes out only what register() wrote: for one group, or for all of them (uninstalling)."""
    with _lock:
        _unregister(name)


def _unregister(name: str | None):
    import winreg
    from .shellmenu import _delete_tree
    hk = winreg.HKEY_CURRENT_USER
    for key in ([name] if name else list(GROUPS)):
        group = GROUPS[key]
        for ext in group.extensions:
            try:
                with winreg.OpenKey(hk, rf"{CLASSES}\{ext}\OpenWithProgids", 0, winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, group.progid)
            except OSError:
                pass
        try:
            with winreg.OpenKey(hk, CAPABILITIES + r"\FileAssociations", 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
                for ext in group.extensions:
                    try:
                        if winreg.QueryValueEx(k, ext)[0] == group.progid:
                            winreg.DeleteValue(k, ext)
                    except OSError:
                        pass
        except OSError:
            pass
        _delete_tree(winreg, rf"{CLASSES}\{group.progid}")
    if not any(_registered(key) for key in GROUPS):  # nothing left: Corefold isn't an app for any file
        try:
            with winreg.OpenKey(hk, REGISTERED, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, APP_NAME)
        except OSError:
            pass
        _delete_tree(winreg, r"Software\Corefold")
    _tell_explorer()


def sync(on, app_command, icon: str, description: str, name: str = "archive"):
    """Registers (or un-registers) only when something changed, so starting Corefold stays quick.
    on may be a bool (for the group called name) or {group: (on, description)} for several."""
    if os.name != "nt":
        return
    wanted = on if isinstance(on, dict) else {name: (bool(on), description)}
    with _lock:
        for key, (want, text) in wanted.items():
            try:
                if want and not is_current(app_command, key):
                    register(app_command, icon, text, key)
                elif not want and _registered(key):
                    _unregister(key)
            except OSError:
                log.exception("couldn't update opening %s files with Corefold", key)


def sync_in_background(on, app_command, icon: str, description: str = ""):
    """sync() without holding up the window. When settings change quickly, the changes are done one
    after another and the newest choice is the one that stays."""
    global _pending
    _pending = (on, app_command, icon, description)
    thread = threading.Thread(target=_sync_pending, daemon=True)
    thread.start()
    return thread


def _sync_pending():
    global _pending
    with _lock:
        job, _pending = _pending, None
        if job:
            sync(*job)


def open_default_apps() -> bool:
    """Opens Windows' Default apps settings at Corefold's page (or the general page on older Windows)."""
    for target in (f"ms-settings:defaultapps?registeredAppUser={APP_NAME}", "ms-settings:defaultapps"):
        try:
            os.startfile(target)
            return True
        except (OSError, AttributeError):
            continue
    return False


def _tell_explorer():
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        pass
