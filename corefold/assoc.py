"""Opening archives with a double-click.

Corefold adds itself to "Open with" for archives (for the current user only, no admin rights), and
registers as an app Windows can make the default for them. Windows doesn't let apps make themselves
the default: the person picks it in Settings, which Corefold can open at the right place."""
from __future__ import annotations

import logging
import os

from .core import APP_NAME

log = logging.getLogger("corefold")

PROGID = "Corefold.Archive"
CLASSES = r"Software\Classes"
CAPABILITIES = r"Software\Corefold\Capabilities"
REGISTERED = r"Software\RegisteredApplications"
# the archives a double-click should open (the ones people meet; Corefold reads more than these)
EXTENSIONS = (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".txz", ".iso", ".cab", ".zipx",
              ".001", ".lzh", ".arj", ".wim")


def _command(app_command) -> str:
    return app_command("--browse", '"%1"')


def is_current(app_command) -> bool:
    """True if the registration is there and points at this copy of Corefold."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{CLASSES}\{PROGID}\shell\open\command") as k:
            return winreg.QueryValueEx(k, "")[0] == _command(app_command)
    except OSError:
        return False


def _registered() -> bool:
    import winreg
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{CLASSES}\{PROGID}").Close()
        return True
    except OSError:
        return False


def register(app_command, icon: str, description: str):
    import winreg
    SZ = winreg.REG_SZ
    hk = winreg.HKEY_CURRENT_USER
    with winreg.CreateKey(hk, rf"{CLASSES}\{PROGID}") as k:
        winreg.SetValueEx(k, "", 0, SZ, description)
        winreg.SetValueEx(k, "FriendlyTypeName", 0, SZ, description)
    if icon:
        with winreg.CreateKey(hk, rf"{CLASSES}\{PROGID}\DefaultIcon") as k:
            winreg.SetValueEx(k, "", 0, SZ, f'"{icon}",0')
    with winreg.CreateKey(hk, rf"{CLASSES}\{PROGID}\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, SZ, _command(app_command))
    for ext in EXTENSIONS:
        with winreg.CreateKey(hk, rf"{CLASSES}\{ext}\OpenWithProgids") as k:
            winreg.SetValueEx(k, PROGID, 0, winreg.REG_NONE, b"")
    with winreg.CreateKey(hk, CAPABILITIES) as k:
        winreg.SetValueEx(k, "ApplicationName", 0, SZ, APP_NAME)
        winreg.SetValueEx(k, "ApplicationDescription", 0, SZ, description)
    with winreg.CreateKey(hk, CAPABILITIES + r"\FileAssociations") as k:
        for ext in EXTENSIONS:
            winreg.SetValueEx(k, ext, 0, SZ, PROGID)
    with winreg.CreateKey(hk, REGISTERED) as k:
        winreg.SetValueEx(k, APP_NAME, 0, SZ, CAPABILITIES)
    _tell_explorer()
    log.info("archives open with Corefold")


def unregister():
    """Takes out only what register() wrote."""
    import winreg
    from .shellmenu import _delete_tree
    hk = winreg.HKEY_CURRENT_USER
    for ext in EXTENSIONS:
        try:
            with winreg.OpenKey(hk, rf"{CLASSES}\{ext}\OpenWithProgids", 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, PROGID)
        except OSError:
            pass
    try:
        with winreg.OpenKey(hk, REGISTERED, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
    except OSError:
        pass
    _delete_tree(winreg, rf"{CLASSES}\{PROGID}")
    _delete_tree(winreg, r"Software\Corefold")
    _tell_explorer()


def sync(on: bool, app_command, icon: str, description: str):
    """Registers (or un-registers) only when something changed, so starting Corefold stays quick."""
    if os.name != "nt":
        return
    try:
        if on and not is_current(app_command):
            register(app_command, icon, description)
        elif not on and _registered():
            unregister()
    except OSError:
        log.exception("couldn't update opening archives with Corefold")


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
