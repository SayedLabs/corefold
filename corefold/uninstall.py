"""Uninstalling apps and cleaning up what they leave behind.

Careful by design:
- an app's own uninstaller always runs first (it knows best what to remove);
- leftovers are only offered when they clearly belong to the app; anything less certain is shown
  but left unticked, and shared places (Windows, Microsoft, Common Files...) are never touched;
- nothing is deleted outright: files are moved into a backup folder and registry keys are exported
  to .reg files first, so everything can be put back from the Apps page for 30 days.

Nothing here imports Qt: the elevated helper (Corefold.exe --cleanup plan.json) uses it too.
"""
from __future__ import annotations

import ctypes
import functools
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .core import LEGACY_NAME, is_link  # noqa: F401 (is_link is used here and by the selftest)

log = logging.getLogger(__name__)

UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
RUN_KEYS = (r"Software\Microsoft\Windows\CurrentVersion\Run", r"Software\Microsoft\Windows\CurrentVersion\RunOnce")
BACKUP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Corefold" / "Uninstall backups"
KEEP_DAYS = 30
NO_WINDOW = 0x08000000

# Why something is offered (English; shown through tr()).
REASONS = {
    "install": "Install folder",
    "data": "Settings and data",
    "shortcut": "Shortcut",
    "startup": "Starts with Windows",
    "entry": "Uninstall entry",
    "registry": "Settings in the registry",
    "maybe": "Might belong to it",
    "package": "App data",
    "service": "Windows services it installed",
    "task": "Scheduled tasks",
    "filetype": "File types and program links",
    "userfiles": "In your own folders (check before removing)",
}

# How far a clean-up goes (English; shown through tr()). Every leftover has a level: 3 = surely the app's,
# 2 = most likely its, 1 = might be its. Some are only looked for in a deep clean-up (Leftover.deep).
DEPTHS = {"safe": "Safe", "normal": "Normal", "deep": "Deep"}
DEPTH_HELP = {
    "safe": "Only what surely belongs to the app. Nothing else is shown.",
    "normal": "What surely belongs to it is ticked. What only might is shown, so you can tick it yourself.",
    "deep": "Also looks in more places (sub-folders, your own folders, temporary files, file types), and ticks "
            "what most likely belongs to it too. Check the list before removing.",
}


def shown_at(item: "Leftover", depth: str) -> bool:
    if depth == "safe":
        return item.level >= 3
    if depth == "deep":
        return True
    return not item.deep


def ticked_at(item: "Leftover", depth: str) -> bool:
    return item.level >= 3 or (depth == "deep" and item.level >= 2)

# Folder and key names that are shared by many apps: never offered, whatever the app is called.
PROTECTED = {
    "windows", "microsoft", "commonfiles", "windowsapps", "system32", "syswow64", "programfiles",
    "programfilesx86", "programdata", "users", "public", "default", "temp", "tmp", "packages", "microsoftnet",
    "windowsdefender", "windowspowershell", "internetexplorer", "windowsnt", "windowsmail", "windowsmediaplayer",
    "windowsphotoviewer", "windowssidebar", "modifiablewindowsapps", "referenceassemblies", "msbuild",
    "microsoftshared", "installshieldinstallationinformation", "packagecache", "classes", "policies",
    "wow6432node", "clients", "registeredapplications", "odbc", "intel", "nvidia", "nvidiacorporation", "amd",
    "realtek", "google", "mozilla", "apple", "adobe", "oracle", "java", "python", "common", "shared", "bin",
    "data", "cache", "config", "settings", "app", "apps", "application", "programs", "software", "tools",
    "setup", "install", "installer", "uninstall", "updater", "update", "plugins", "crashdumps", "logs", "local",
    "locallow", "roaming", "desktop", "documents", "downloads", "startmenu", "startup", "administrativetools",
    "accessories", "systemtools", "maintenance", "accessibility", "windowsaccessories", "windowstools",
}
# endings that don't help to recognise an app ("Acme Inc.", "Tool (x64)", "App 2.4.1")
_NOISE = re.compile(r"\b(inc|llc|ltd|limited|corp|corporation|co|gmbh|ag|sa|srl|bv|plc|software|technologies|"
                    r"x64|x86|64bit|32bit|64|32|amd64|arm64|en-us|version|v)\b", re.I)


def norm(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (text or "").lower())


def clean_name(text: str) -> str:
    """ "Mozilla Firefox (x64 en-US) 124.0.1" -> "mozillafirefox"."""
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text or "")
    text = re.sub(r"\b\d+(\.\d+)+\b", " ", text)        # version numbers
    text = _NOISE.sub(" ", text)
    text = re.sub(r"[\s_-]+\d+\s*$", "", text.strip())   # "Studio 2024", "Settings 2"
    return norm(text)


# ----------------------------------------------------------------------------- the list of apps

@dataclass
class App:
    id: str
    name: str
    publisher: str = ""
    version: str = ""
    location: str = ""
    uninstall: str = ""
    quiet: str = ""
    icon: str = ""
    size: int = 0          # bytes (0 = unknown)
    date: str = ""         # "2024-05-31"
    hive: str = ""         # "HKLM" or "HKCU"
    view: int = 0          # 64 or 32 (which part of the registry, for HKLM)
    key: str = ""          # its key under ...\Uninstall
    store: str = ""        # PackageFullName, for Store apps
    family: str = ""       # PackageFamilyName, for Store apps
    msi: str = ""          # product code, for Windows Installer apps
    last_used: float = 0.0  # when it was last started (0 = unknown), see usage()
    startup: bool = False   # starts with Windows

    @property
    def needs_admin(self) -> bool:
        return self.hive == "HKLM"

    @property
    def broken(self) -> bool:
        """Its uninstaller is missing (so only "Remove leftovers" can help)."""
        if self.store or self.msi:
            return False
        exe, _ = split_command(self.uninstall)
        return not exe or (os.path.isabs(exe) and not os.path.exists(exe))

    def names(self, with_location: bool = True) -> set[str]:
        """Normalised names the app's folders and keys might have."""
        out = set()
        for text in (self.name, self.name.split(" - ")[0]):
            n = clean_name(text)
            if n:
                out.add(n)
            pub = clean_name(self.publisher)
            if pub and n.startswith(pub) and len(n) - len(pub) >= 3:
                out.add(n[len(pub):])  # "Mozilla Firefox" -> also "firefox"
        if self.location and with_location:
            # the install folder's name counts only when it is the app's name (or a longer form of
            # it): "C:\\Program Files\\Vendor" for "Vendor Tool" would match the whole vendor folder
            loc = norm(Path(self.location.rstrip("\\/")).name)
            if any(loc == n or loc.startswith(n) for n in set(out)):
                out.add(loc)
        if self.family:
            out.add(norm(self.family.split("_")[0]))
        return {n for n in out if len(n) >= 3 and n not in PROTECTED}


def _winreg():
    import winreg
    return winreg


def _views(hive: str) -> list[int]:
    return [64, 32] if hive == "HKLM" else [0]


def _root(hive: str):
    w = _winreg()
    return {"HKLM": w.HKEY_LOCAL_MACHINE, "HKCU": w.HKEY_CURRENT_USER}[hive]


def _access(view: int, write: bool = False) -> int:
    w = _winreg()
    flags = w.KEY_ALL_ACCESS if write else w.KEY_READ
    if view == 64:
        flags |= getattr(w, "KEY_WOW64_64KEY", 0x0100)
    elif view == 32:
        flags |= getattr(w, "KEY_WOW64_32KEY", 0x0200)
    return flags


def _values(hive: str, path: str, view: int) -> dict:
    w = _winreg()
    out = {}
    try:
        with w.OpenKey(_root(hive), path, 0, _access(view)) as k:
            i = 0
            while True:
                try:
                    name, value, _kind = w.EnumValue(k, i)
                except OSError:
                    break
                out[name] = value
                i += 1
    except OSError:
        pass
    return out


def _subkeys(hive: str, path: str, view: int) -> list[str]:
    w = _winreg()
    out = []
    try:
        with w.OpenKey(_root(hive), path, 0, _access(view)) as k:
            i = 0
            while True:
                try:
                    out.append(w.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except OSError:
        pass
    return out


def key_exists(hive: str, path: str, view: int) -> bool:
    w = _winreg()
    try:
        with w.OpenKey(_root(hive), path, 0, _access(view)):
            return True
    except OSError:
        return False


def _date(text) -> str:
    text = str(text or "")
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return ""


def _icon_path(text: str) -> str:
    text = (text or "").strip().strip('"')
    text = re.sub(r",\s*-?\d+$", "", text).strip().strip('"')
    return os.path.expandvars(text)


def list_apps(include_store: bool = True) -> list[App]:
    apps: dict[tuple, App] = {}
    for hive in ("HKLM", "HKCU"):
        for view in _views(hive):
            for sub in _subkeys(hive, UNINSTALL, view):
                v = _values(hive, UNINSTALL + "\\" + sub, view)
                name = str(v.get("DisplayName") or "").strip()
                if not name or v.get("SystemComponent") == 1 or v.get("ParentKeyName"):
                    continue
                if str(v.get("ReleaseType", "")).lower() in ("update", "hotfix", "security update"):
                    continue
                uninstall = str(v.get("UninstallString") or "").strip()
                msi = sub if (v.get("WindowsInstaller") == 1 and re.fullmatch(r"\{[0-9A-Fa-f-]{36}\}", sub)) else ""
                if not uninstall and not msi:
                    continue
                size = v.get("EstimatedSize")
                app = App(
                    id=f"{hive}|{view}|{sub}", name=name, publisher=str(v.get("Publisher") or "").strip(),
                    version=str(v.get("DisplayVersion") or "").strip(),
                    location=os.path.expandvars(str(v.get("InstallLocation") or "").strip().strip('"')),
                    uninstall=uninstall, quiet=str(v.get("QuietUninstallString") or "").strip(),
                    icon=_icon_path(str(v.get("DisplayIcon") or "")),
                    size=int(size) * 1024 if isinstance(size, int) and size > 0 else 0,
                    date=_date(v.get("InstallDate")), hive=hive, view=view, key=sub, msi=msi)
                same = (norm(app.name), app.version)
                if same in apps:  # listed twice (both registry views): keep the first
                    continue
                apps[same] = app
    result = list(apps.values())
    if include_store:
        try:
            result += store_apps()
        except Exception:
            log.debug("couldn't list Store apps", exc_info=True)
    return sorted(result, key=lambda a: a.name.lower())


STORE_SCRIPT = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$names = @{}
Get-StartApps | ForEach-Object { $f = ($_.AppID -split '!')[0]; if (-not $names.ContainsKey($f)) { $names[$f] = $_.Name } }
Get-AppxPackage | Where-Object { -not $_.IsFramework -and -not $_.NonRemovable -and ($_.SignatureKind -eq 'Store' -or $_.SignatureKind -eq 'Developer') -and $names.ContainsKey($_.PackageFamilyName) } |
  ForEach-Object { [pscustomobject]@{ n = $names[$_.PackageFamilyName]; full = $_.PackageFullName; fam = $_.PackageFamilyName; pub = $_.PublisherId; ver = $_.Version.ToString(); loc = $_.InstallLocation; sig = $_.SignatureKind.ToString() } } |
  ConvertTo-Json -Compress
"""


def powershell(script: str, timeout: float = 90) -> str:
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-Command", script], capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    return r.stdout.decode("utf-8", "replace").strip()


def store_apps() -> list[App]:
    out = powershell(STORE_SCRIPT)
    if not out:
        return []
    data = json.loads(out)
    if isinstance(data, dict):
        data = [data]
    apps = []
    for d in data:
        name = str(d.get("n") or "").strip()
        full = str(d.get("full") or "")
        if not name or not full:
            continue
        publisher = "Microsoft" if full.startswith("Microsoft.") else ""
        apps.append(App(id="store|" + full, name=name, publisher=publisher, version=str(d.get("ver") or ""),
                        location=str(d.get("loc") or ""), store=full, family=str(d.get("fam") or "")))
    return apps


# ----------------------------------------------------------------------------- when an app was last used

USERASSIST = r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
# Windows writes some paths in UserAssist with a "known folder" id instead of the folder
_KNOWN_FOLDERS = {  # id: (environment variables to try, then the folders under it)
    "{6d809377-6af0-444b-8957-a3773f02200e}": (("ProgramW6432", "ProgramFiles"), ()),
    "{7c5a40ef-a0fb-4bfc-874a-c0f2e0b9fa8e}": (("ProgramFiles(x86)",), ()),
    "{905e63b6-c1bf-494e-b29c-65b732d3d21a}": (("ProgramFiles",), ()),
    "{f1b32785-6fba-4fcf-9d55-7b8e7f157091}": (("LOCALAPPDATA",), ()),
    "{3eb685db-65f9-4cf6-a03a-e3ef65729f3d}": (("APPDATA",), ()),
    "{f38bf404-1d43-42f2-9305-67de0b28fc23}": (("SystemRoot",), ()),
    "{a77f5d77-2e2b-44c3-a6a2-aba601054a51}": (("APPDATA",), ("Microsoft", "Windows", "Start Menu", "Programs")),
    "{0139d44e-6afe-49f2-8690-3dafcae6ffb8}": (("ProgramData",), ("Microsoft", "Windows", "Start Menu", "Programs")),
    "{9e3995ab-1f9c-4f13-b827-48b24b6c7174}": (("APPDATA",), ("Microsoft", "Internet Explorer", "Quick Launch",
                                                             "User Pinned")),
}


def _expand_known(path: str) -> str:
    head, _, rest = path.partition("\\")
    known = _KNOWN_FOLDERS.get(head.lower())
    if not known:
        return path
    names, below = known
    base = next((os.environ[n] for n in names if os.environ.get(n)), "")
    return os.path.join(base, *below, rest) if base else path


def usage() -> dict[str, float]:
    """When programs were last started from the Start menu, the taskbar, a shortcut or File Explorer
    (Windows keeps this for every account, in UserAssist). {lower-case path or app id: time}."""
    import codecs
    w = _winreg()
    out: dict[str, float] = {}
    for guid in _subkeys("HKCU", USERASSIST, 0):
        try:
            with w.OpenKey(w.HKEY_CURRENT_USER, f"{USERASSIST}\\{guid}\\Count") as k:
                i = 0
                while True:
                    try:
                        name, data, _kind = w.EnumValue(k, i)
                    except OSError:
                        break
                    i += 1
                    if not isinstance(data, bytes) or len(data) < 68:
                        continue
                    ft = int.from_bytes(data[60:68], "little")
                    if not ft:
                        continue
                    when = ft / 10_000_000 - 11_644_473_600
                    if not 946_684_800 < when < time.time() + 86400:  # 2000 … now: anything else is noise
                        continue
                    path = _expand_known(codecs.decode(name, "rot13"))
                    if path.lower().endswith(".lnk") and os.path.isabs(path):
                        path = lnk_target(Path(path)) or path
                    key = os.path.normcase(os.path.normpath(path)) if os.path.isabs(path) else path.lower()
                    out[key] = max(out.get(key, 0.0), when)
        except OSError:
            continue
    return out


def last_used(app: App, used: dict[str, float]) -> float:
    """The last time any program of this app was started (0 = Windows doesn't know)."""
    best = 0.0
    if app.store or app.family:
        fam = (app.family or app.store).lower()
        for key, when in used.items():
            if key.startswith(fam.lower()):
                best = max(best, when)
        return best
    places = [app.location] if app.location and _safe_folder(app.location) else []
    if not places and app.icon and os.path.isabs(app.icon) and _safe_folder(os.path.dirname(app.icon)):
        places.append(os.path.dirname(app.icon))
    for place in places:
        root = os.path.normcase(os.path.normpath(place)).rstrip("\\/") + os.sep
        for key, when in used.items():
            if key.startswith(root):
                best = max(best, when)
    return best


def startup_programs() -> list[str]:
    """The programs in the start-with-Windows entries (read once for the whole list)."""
    out = []
    for hive in ("HKCU", "HKLM"):
        for view in _views(hive):
            for value in _values(hive, RUN_KEYS[0], view).values():
                exe, _ = split_command(str(value))
                if exe:
                    out.append(exe)
    return out


def starts_with_windows(app: App, programs: list[str] | None = None) -> bool:
    """One of its programs is in a start-with-Windows entry."""
    loc = app.location if app.location and _safe_folder(app.location) else ""
    if not loc:
        return False
    return any(_inside(exe, loc) for exe in (startup_programs() if programs is None else programs))


# ----------------------------------------------------------------------------- remembered between visits

LIST_CACHE = BACKUP_DIR.parent / "apps.json"  # Store apps (slow to list) and sizes worked out before


def load_cache() -> dict:
    try:
        data = json.loads(LIST_CACHE.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(data: dict):
    try:
        LIST_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = LIST_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        os.replace(tmp, LIST_CACHE)
    except OSError:
        log.debug("couldn't save the list of apps", exc_info=True)


def cached_store_apps(data: dict) -> list[App]:
    apps = []
    for d in data.get("store") or []:
        try:
            app = App(**{k: v for k, v in d.items() if k not in ("last_used", "startup")})
        except TypeError:
            continue
        if app.store:
            apps.append(app)
    return apps


def size_key(app: App) -> str:
    return f"{app.id}|{app.version}|{app.location}"


def fill_sizes(apps: list[App], cancel: threading.Event | None = None, on_size=None, budget_s: float = 20):
    """Works out sizes the apps don't tell (by adding up their install folder), quickest first."""
    start = time.monotonic()
    for app in apps:
        if cancel is not None and cancel.is_set() or time.monotonic() - start > budget_s:
            return
        if app.size or app.store or not app.location or not _safe_folder(app.location):
            continue
        size = folder_size(Path(app.location), limit_s=3)
        if size:
            app.size = size
            if on_size:
                on_size(app)


def folder_size(path: Path, limit_s: float = 5) -> int:
    """The size of everything in a folder. Windows gives each file's size with the folder's listing, so
    no file is asked about on its own (that kept Python busy enough to slow the window down)."""
    total, start = 0, time.monotonic()
    try:
        if path.is_file():
            return path.stat().st_size
    except OSError:
        return 0
    waiting = [str(path)]
    while waiting:
        try:
            with os.scandir(waiting.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if not e.is_symlink() and not (hasattr(e, "is_junction") and e.is_junction()):
                                waiting.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
        if time.monotonic() - start > limit_s:
            break
    return total


# ----------------------------------------------------------------------------- running the uninstaller

def split_command(cmd: str) -> tuple[str, str]:
    """ "C:\\Program Files\\App\\uninstall.exe" /S -> (exe, "/S"). Copes with paths that have
    spaces but no quotes, which many uninstall entries have."""
    cmd = os.path.expandvars((cmd or "").strip())
    if not cmd:
        return "", ""
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end > 0:
            return cmd[1:end], cmd[end + 1:].strip()
        return cmd.strip('"'), ""
    lower = cmd.lower()
    for m in re.finditer(r"\.(exe|cmd|bat|com)\b", lower):
        exe = cmd[:m.end()]
        if " " not in exe or os.path.exists(exe):
            return exe, cmd[m.end():].strip()
    parts = cmd.split(" ", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def uninstall_command(app: App, quiet: bool = False) -> tuple[str, str]:
    if app.msi and (not app.uninstall or "msiexec" in app.uninstall.lower()):
        return "msiexec.exe", f"/x {app.msi}" + (" /qb" if quiet else "")
    cmd = app.quiet if (quiet and app.quiet) else app.uninstall
    exe, args = split_command(cmd)
    if os.path.basename(exe).lower() == "msiexec.exe":  # "/I{code}" opens the "change" screen; /X removes
        args = re.sub(r"(?i)/i\s*(\{)", r"/x \1", args)
    return exe, args


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("fMask", ctypes.c_ulong), ("hwnd", ctypes.c_void_p),
                ("lpVerb", ctypes.c_wchar_p), ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
                ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int), ("hInstApp", ctypes.c_void_p),
                ("lpIDList", ctypes.c_void_p), ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
                ("dwHotKey", ctypes.c_uint32), ("hIconOrMonitor", ctypes.c_void_p), ("hProcess", ctypes.c_void_p)]


SEE_MASK_NOCLOSEPROCESS, SEE_MASK_NOASYNC, SEE_MASK_FLAG_NO_UI = 0x40, 0x100, 0x400


def shell_start(exe: str, args: str = "", admin: bool = False, show: int = 1, cwd: str | None = None):
    """Starts a program the way Explorer does (so its "run as administrator" request is honoured).
    Returns (process handle, 0), with handle 0 if it started without one, or (None, Windows error code)
    if it didn't start: 1223 when the user said no to the admin prompt, 2 or 3 when it isn't there."""
    try:  # Windows asks for COM to be ready on the thread that calls ShellExecuteEx
        ctypes.windll.ole32.CoInitializeEx(None, 0x2 | 0x4)  # apartment threaded, no OLE1 DDE
    except (AttributeError, OSError):
        pass
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = "runas" if admin else "open"
    info.lpFile = exe
    info.lpParameters = args or None
    info.lpDirectory = cwd
    info.nShow = show
    ok = _dll("shell32").ShellExecuteExW(ctypes.byref(info))
    if not ok:
        return None, ctypes.get_last_error()
    return info.hProcess or 0, 0  # 0: started, but Windows gave no handle to wait on


def shell_run(exe: str, args: str = "", admin: bool = False, show: int = 1, cwd: str | None = None):
    """shell_start without the reason: a process handle (0 if it started without one), or None."""
    return shell_start(exe, args, admin, show, cwd)[0]


def wait_process(handle, cancel: threading.Event | None = None, timeout_s: float | None = None) -> int | None:
    """Waits for a process started by shell_run. Returns its exit code (None if stopped waiting)."""
    if not handle:
        return None
    k32 = ctypes.windll.kernel32
    start = time.monotonic()
    try:
        while True:
            r = k32.WaitForSingleObject(ctypes.c_void_p(handle), 250)
            if r in (-1, 0xFFFFFFFF):  # not a handle we can wait on
                return None
            if r == 0:
                code = ctypes.c_ulong()
                k32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
                return code.value
            if cancel is not None and cancel.is_set():
                return None
            if timeout_s is not None and time.monotonic() - start > timeout_s:
                return None
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))


def still_installed(app: App) -> bool:
    if app.store:
        try:
            out = powershell(f"if (Get-AppxPackage | Where-Object PackageFullName -eq '{app.store}') {{ 'yes' }}",
                             timeout=30)
            return out.strip() == "yes"
        except Exception:
            return True  # couldn't check: treat it as still installed, so nothing of it is touched
    return key_exists(app.hive, UNINSTALL + "\\" + app.key, app.view)


# Why an app is still there after its uninstaller (English; shown through tr(), with {code} or {error})
OUTCOMES = {
    "refused": "Administrator permission was refused, so its uninstaller didn't run.",
    "missing": "Its uninstaller is missing. Use \"Remove leftovers only\" to clean up what's left.",
    "not_started": "Its uninstaller couldn't be started (Windows error {code}).",
    "busy": "Another install or update is running (often Windows Update). Wait for it to finish, then try again.",
    "cancelled": "It was cancelled in the uninstaller's window.",
    "failed": "Its uninstaller closed without removing it (code {code}).",
    "store_failed": "Windows couldn't remove it: {error}",
    "still": "Its uninstaller finished, but the app is still installed. It may have been cancelled, or it needs "
             "a restart to finish.",
    "restart": "It will be fully removed after you restart the PC.",
    "not_installed": "Windows says it isn't installed any more. Use \"Remove leftovers only\" to clean up "
                     "what's left.",
    "stopped": "You stopped its uninstaller.",
}
# what Windows Installer's exit codes mean (msiexec)
_MSI_CODES = {1602: "cancelled", 1618: "busy", 1605: "not_installed", 3010: "restart", 1641: "restart"}


@dataclass
class Outcome:
    result: str      # "removed", or a key of OUTCOMES
    code: int = 0    # the uninstaller's exit code or the Windows error
    error: str = ""  # Windows' own words (Store apps)


class Control:
    """What the user can ask for while an uninstaller runs (set from the window, read by the worker)."""

    def __init__(self, cancel: threading.Event):
        self.cancel = cancel                # the window was closed: stop following it, leave it running
        self.skip = threading.Event()       # "the uninstaller is done": stop waiting for it
        self.stop = threading.Event()       # "stop the uninstaller": end its processes
        self.front = threading.Event()      # "show its window"


def run_uninstaller(app: App, quiet: bool, control: Control, on_state=None, stuck_s: float = 60,
                    finish_s: float = 20) -> Outcome | None:
    """Runs the app's own uninstaller and follows it until it's done (None if the window was closed).
    on_state(state) hears what is happening: "starting" (Windows may ask for permission first),
    "window" (its window is open: the user follows its steps), "working" (running with no window),
    "stuck" (no window and it hasn't done anything for stuck_s seconds), "cant_stop" (asked to stop it,
    but it runs as administrator), "checking" (it ended: making sure the app is gone)."""
    tell = on_state or (lambda s: None)
    if app.store:
        return _run_store(app, control, tell)
    exe, args = uninstall_command(app, quiet)
    if not exe or (os.path.isabs(exe) and not os.path.exists(exe)):
        return Outcome("missing")
    tell("starting")
    cwd = os.path.dirname(exe) if os.path.isabs(exe) and os.path.isdir(os.path.dirname(exe)) else None
    handle, error = shell_start(exe, args, cwd=cwd)
    if handle is None:
        return Outcome("refused" if error == 1223 else "missing" if error in (2, 3) else "not_started", error)
    msi = os.path.basename(exe).lower() == "msiexec.exe"
    if not handle:  # nothing to follow but its uninstall entry
        return _wait_entry(app, control, tell)
    procs = Processes(handle)
    state, last, idle_since, next_check, gone_at = "", None, time.monotonic(), 0.0, None
    try:
        while True:
            if control.cancel.is_set():
                return None
            if control.skip.is_set():
                break
            if control.stop.is_set():
                control.stop.clear()
                if procs.stop():
                    return Outcome("stopped")
                tell("cant_stop")
            procs.refresh()
            if not procs.alive():
                break
            now = time.monotonic()
            if now >= next_check:  # cheap: one registry key
                next_check = now + 2
                if gone_at is None and not still_installed(app):
                    gone_at = now
            if gone_at is not None and now - gone_at > 15:
                break  # the app is gone: whatever it left open (a "thanks" page...) isn't waited for
            busy = procs.activity()
            if last is None or busy[0] - last[0] > 20_000 or busy[1] != last[1]:  # 2 ms of work, or any I/O
                idle_since = now
            last = busy
            windows = procs.windows()
            new = "window" if windows else "stuck" if now - idle_since >= stuck_s else "working"
            if new == "window" and (state != "window" or control.front.is_set()):
                show_window(windows[0])  # it may open behind Corefold
            control.front.clear()
            if new != state:
                state = new
                tell(new)
            time.sleep(0.5)
        code = procs.exit_code()
    finally:
        procs.close()
    tell("checking")
    quick = control.skip.is_set() or (msi and code in _MSI_CODES)
    deadline = time.monotonic() + (3 if quick else finish_s)  # some finish removing after they close
    while still_installed(app):
        if control.cancel.is_set():
            return None
        if time.monotonic() > deadline:
            if msi and code in _MSI_CODES:
                return Outcome(_MSI_CODES[code], code)
            return Outcome("failed" if code else "still", code)
        time.sleep(1)
    return Outcome("removed", code)


def _wait_entry(app: App, control: Control, tell, max_s: float = 30 * 60) -> Outcome | None:
    """Started without a process to follow: waits for its uninstall entry to go."""
    tell("working")
    start = time.monotonic()
    while still_installed(app):
        if control.cancel.is_set():
            return None
        if control.skip.is_set() or control.stop.is_set() or time.monotonic() - start > max_s:
            return Outcome("still")
        time.sleep(1)
    return Outcome("removed")


def _run_store(app: App, control: Control, tell) -> Outcome | None:
    tell("working")
    script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
              f"try {{ Remove-AppxPackage -Package '{app.store}' -ErrorAction Stop }} "
              "catch { [Console]::Out.Write($_.Exception.Message); exit 1 }")
    import tempfile
    with tempfile.TemporaryFile() as out:
        p = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-Command", script], stdout=out, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        while p.poll() is None:
            if control.cancel.is_set():
                return None
            if control.stop.is_set():
                p.kill()
                return Outcome("stopped")
            time.sleep(0.3)
        out.seek(0)
        text = out.read().decode("utf-8", "replace").strip()
    tell("checking")
    if not still_installed(app):
        return Outcome("removed")
    if p.returncode:
        line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        return Outcome("store_failed", p.returncode, short_text(line, 200) or f"0x{p.returncode & 0xFFFFFFFF:08X}")
    return Outcome("still")


def short_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


# ----------------------------------------------------------------------------- following its processes

@functools.cache
def _dll(name: str):
    """A private copy of a Windows library (its own argument types, and errors kept for get_last_error)."""
    lib = ctypes.WinDLL(name, use_last_error=True)
    H, U32, P = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p
    if name == "kernel32":
        for fn, args, res in (("CreateToolhelp32Snapshot", (U32, U32), H), ("Process32FirstW", (H, P), ctypes.c_int),
                              ("Process32NextW", (H, P), ctypes.c_int), ("OpenProcess", (U32, ctypes.c_int, U32), H),
                              ("WaitForSingleObject", (H, U32), U32), ("GetExitCodeProcess", (H, P), ctypes.c_int),
                              ("GetProcessTimes", (H, P, P, P, P), ctypes.c_int),
                              ("GetProcessIoCounters", (H, P), ctypes.c_int), ("TerminateProcess", (H, U32), ctypes.c_int),
                              ("GetProcessId", (H,), U32), ("CloseHandle", (H,), ctypes.c_int),
                              ("QueryFullProcessImageNameW", (H, U32, P, P), ctypes.c_int),
                              ("MoveFileExW", (ctypes.c_wchar_p, ctypes.c_wchar_p, U32), ctypes.c_int)):
            getattr(lib, fn).argtypes, getattr(lib, fn).restype = args, res
    elif name == "user32":
        for fn, args, res in (("EnumWindows", (_ENUM_PROC, P), ctypes.c_int),
                              ("GetWindowThreadProcessId", (H, ctypes.POINTER(U32)), U32),
                              ("IsWindowVisible", (H,), ctypes.c_int), ("IsIconic", (H,), ctypes.c_int),
                              ("GetWindowRect", (H, P), ctypes.c_int), ("ShowWindow", (H, ctypes.c_int), ctypes.c_int),
                              ("SetForegroundWindow", (H,), ctypes.c_int)):
            getattr(lib, fn).argtypes, getattr(lib, fn).restype = args, res
    return lib


_ENUM_PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p) if sys.platform == "win32" else None


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32), ("th32ProcessID", ctypes.c_uint32),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_uint32),
                ("cntThreads", ctypes.c_uint32), ("th32ParentProcessID", ctypes.c_uint32),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_wchar * 260)]


def processes() -> list[tuple[int, int, str]]:
    """Every running process: (id, the id of the process that started it, program name)."""
    k = _dll("kernel32")
    snap = k.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return []
    out = []
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = k.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out.append((e.th32ProcessID, e.th32ParentProcessID, e.szExeFile))
            ok = k.Process32NextW(snap, ctypes.byref(e))
    finally:
        k.CloseHandle(snap)
    return out


# programs an uninstaller may start that aren't part of it (a "sorry to see you go" page...)
NOT_PART = {"explorer.exe", "msedge.exe", "chrome.exe", "firefox.exe", "iexplore.exe", "opera.exe", "brave.exe",
            "vivaldi.exe", "conhost.exe", "werfault.exe", "openwith.exe"}
SYNCHRONIZE, QUERY_LIMITED, TERMINATE = 0x100000, 0x1000, 0x1


class Processes:
    """An uninstaller and every program it starts: many copy themselves to a temporary folder, start
    the copy and quit straight away, and the copy does the work."""

    def __init__(self, handle):
        k = _dll("kernel32")
        first = k.GetProcessId(handle)
        self.handles = {first: handle}   # id: handle (kept open, so the id can't be reused meanwhile)
        self.ended: dict[int, int] = {}  # id: exit code, in the order they ended
        self.first = first

    def refresh(self):
        k = _dll("kernel32")
        for pid, parent, name in processes():
            if parent in self.handles and pid not in self.handles and name.lower() not in NOT_PART:
                h = k.OpenProcess(SYNCHRONIZE | QUERY_LIMITED | TERMINATE, False, pid) or \
                    k.OpenProcess(SYNCHRONIZE | QUERY_LIMITED, False, pid)
                if h:
                    self.handles[pid] = h
        for pid, h in self.handles.items():
            if pid not in self.ended and k.WaitForSingleObject(h, 0) == 0:
                code = ctypes.c_uint32()
                k.GetExitCodeProcess(h, ctypes.byref(code))
                self.ended[pid] = code.value

    def alive(self) -> list[int]:
        return [pid for pid in self.handles if pid not in self.ended]

    def activity(self) -> tuple[int, int]:
        """(processor time used, number of reads and writes) so far, by all of them."""
        k = _dll("kernel32")
        cpu = io = 0
        for pid in self.alive():
            times = [ctypes.c_uint64() for _ in range(4)]
            if k.GetProcessTimes(self.handles[pid], *(ctypes.byref(t) for t in times)):
                cpu += times[2].value + times[3].value
            counters = (ctypes.c_uint64 * 6)()
            if k.GetProcessIoCounters(self.handles[pid], counters):
                io += counters[0] + counters[1] + counters[2]
        return cpu, io

    def windows(self) -> list[int]:
        return windows_of(set(self.alive()))

    def exit_code(self) -> int:
        """The first one's code, or (when it just started a copy of itself and quit) the last one's."""
        code = self.ended.get(self.first, 0)
        if not code and len(self.ended) > 1:
            code = list(self.ended.values())[-1]
        return code

    def stop(self) -> bool:
        """Ends them all. False if some keep running (they run as administrator, and Corefold doesn't)."""
        k = _dll("kernel32")
        for pid in self.alive():
            k.TerminateProcess(self.handles[pid], 1)
        end = time.monotonic() + 3
        while time.monotonic() < end:
            self.refresh()
            if not self.alive():
                return True
            time.sleep(0.2)
        return False

    def close(self):
        k = _dll("kernel32")
        for h in self.handles.values():
            k.CloseHandle(h)
        self.handles = {}


def program_of(pid: int) -> str:
    """The program file a running process was started from ("" if Windows won't say)."""
    k = _dll("kernel32")
    h = k.OpenProcess(QUERY_LIMITED, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_uint32(1024)
        return buf.value if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else ""
    finally:
        k.CloseHandle(h)


def programs_in(folders: list[str]) -> list[tuple[int, str]]:
    """The running programs started from inside these folders: (process id, program file)."""
    folders = [f for f in folders if f and _safe_folder(f)]
    if not folders:
        return []
    me = os.getpid()
    out = []
    for pid, _parent, _name in processes():
        if pid in (0, 4, me):
            continue
        exe_path = program_of(pid)
        if exe_path and any(_inside(exe_path, f) for f in folders):
            out.append((pid, exe_path))
    return out


def close_programs(pids: list[int]) -> list[int]:
    """Ends these processes. Returns the ones still running (they run as administrator, or won't end)."""
    k = _dll("kernel32")
    handles = {}
    for pid in pids:
        h = k.OpenProcess(SYNCHRONIZE | TERMINATE, False, pid)
        if h:
            handles[pid] = h
            k.TerminateProcess(h, 1)
    still = [pid for pid in pids if pid not in handles]
    for pid, h in handles.items():
        if k.WaitForSingleObject(h, 3000) != 0:
            still.append(pid)
        k.CloseHandle(h)
    return still


def windows_of(pids: set[int]) -> list[int]:
    """The visible windows these processes have open."""
    if not pids:
        return []
    u = _dll("user32")
    found = []

    def each(hwnd, _):
        pid = ctypes.c_uint32()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and u.IsWindowVisible(hwnd):
            r = (ctypes.c_long * 4)()
            if u.IsIconic(hwnd) or (u.GetWindowRect(hwnd, r) and r[2] > r[0] and r[3] > r[1]):
                found.append(hwnd)
        return 1
    u.EnumWindows(_ENUM_PROC(each), None)
    return found


def show_window(hwnd):
    u = _dll("user32")
    try:
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)  # SW_RESTORE
        u.SetForegroundWindow(hwnd)
    except OSError:
        pass


# ----------------------------------------------------------------------------- finding leftovers

@dataclass
class Leftover:
    kind: str              # "folder", "file", "shortcut", "key", "value"
    path: str              # a file path, or a registry key like "HKCU\Software\Acme"
    reason: str = "data"   # see REASONS
    safe: bool = True      # ticked to start with
    size: int = 0
    view: int = 0          # registry view (64 / 32) for HKLM keys
    value: str = ""        # the value's name, for kind "value"
    app: str = ""
    level: int = 3         # 3 = surely the app's, 2 = most likely, 1 = might be (see DEPTHS)
    deep: bool = False     # only looked for in a deep clean-up

    @property
    def is_registry(self) -> bool:
        return self.kind in ("key", "value", "service")

    @property
    def needs_admin(self) -> bool:
        if self.kind in ("service", "task"):
            return True
        if self.is_registry:
            return self.path.upper().startswith("HKLM")
        p = os.path.normcase(self.path)
        user = os.path.normcase(os.environ.get("USERPROFILE", str(Path.home())))
        return not p.startswith(user + os.sep)

    def label(self) -> str:
        if self.kind == "service":
            return self.path.rsplit("\\", 1)[-1] + (f" ({self.value})" if self.value else "")
        if self.kind == "task":
            return self.path.lstrip("\\")
        text = f"{self.path} → {self.value}" if self.kind == "value" else self.path
        if self.is_registry and self.view == 32:
            from .i18n import tr
            text += " " + tr("(32-bit)")
        return text


def _env_path(name: str, *more: str) -> Path | None:
    v = os.environ.get(name)
    return Path(v, *more) if v else None


def search_roots() -> list[Path]:
    roots = [_env_path("ProgramFiles"), _env_path("ProgramFiles(x86)"), _env_path("ProgramW6432"),
             _env_path("ProgramData"), _env_path("APPDATA"), _env_path("LOCALAPPDATA"),
             _env_path("LOCALAPPDATA", "Programs"), _env_path("USERPROFILE", "AppData", "LocalLow")]
    seen, out = set(), []
    for r in roots:
        if r is None:
            continue
        k = os.path.normcase(str(r))
        if k not in seen and r.is_dir():
            seen.add(k)
            out.append(r)
    return out


def shortcut_roots() -> list[Path]:
    roots = [_env_path("APPDATA", "Microsoft", "Windows", "Start Menu", "Programs"),
             _env_path("ProgramData", "Microsoft", "Windows", "Start Menu", "Programs"),
             _env_path("USERPROFILE", "Desktop"), _env_path("PUBLIC", "Desktop"),
             _env_path("APPDATA", "Microsoft", "Internet Explorer", "Quick Launch", "User Pinned", "TaskBar")]
    return [r for r in roots if r is not None and r.is_dir()]


@functools.lru_cache(maxsize=1)
def _protected_paths() -> frozenset[str]:
    paths = {os.environ.get(n, "") for n in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)",
                                             "ProgramW6432", "ProgramData", "APPDATA", "LOCALAPPDATA",
                                             "USERPROFILE", "PUBLIC", "SystemDrive", "CommonProgramFiles",
                                             "CommonProgramFiles(x86)", "CommonProgramW6432", "TEMP", "TMP")}
    paths |= {str(r) for r in search_roots()} | {str(r) for r in shortcut_roots()}
    # Corefold's own folders (its settings, these backups, and the app itself)
    for name in ("Corefold", LEGACY_NAME):
        paths |= {str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / name),
                  str(Path(os.environ.get("APPDATA", str(Path.home()))) / name)}
    if getattr(sys, "frozen", False):
        paths.add(os.path.dirname(sys.executable))
    home = os.environ.get("USERPROFILE", "")
    if home:
        paths |= {str(Path(home, d)) for d in ("Documents", "Downloads", "Desktop", "Pictures", "Music",
                                                   "Videos", "AppData", "OneDrive")}
    return frozenset(os.path.normcase(os.path.normpath(p)).rstrip("\\/") for p in paths if p)


def _safe_folder(path: str) -> bool:
    """Not a drive, not a system or shared folder, and not one of the places apps live in."""
    if not path:
        return False
    p = os.path.normcase(os.path.normpath(path)).rstrip("\\/")
    if len(Path(p).parts) < 3:  # "C:\" or "C:\Something"
        return False
    protected = _protected_paths()
    if p in protected or any(q.startswith(p + os.sep) for q in protected):
        return False  # a protected place, or a folder that holds one (like a whole drive or C:\\Users)
    windir = os.path.normcase(os.environ.get("SystemRoot", "c:\\windows"))
    if p == windir or p.startswith(windir + os.sep):
        return False
    if any(norm(part) in ("windowsapps", "system32", "syswow64", "winsxs") for part in Path(p).parts):
        return False  # Store apps' own folders and Windows' system folders are Windows' business
    return norm(Path(p).name) not in PROTECTED


# Windows' own and Microsoft's folders: only ever offered for a Microsoft app, and never ticked
_SYSTEM_PREFIXES = ("microsoft", "windows")


def _system_named(name: str) -> bool:
    return norm(name).startswith(_SYSTEM_PREFIXES)


def _inside(child: str, parent: str) -> bool:
    c = os.path.normcase(os.path.normpath(child)).rstrip("\\/")
    p = os.path.normcase(os.path.normpath(parent)).rstrip("\\/")
    return c == p or c.startswith(p + os.sep)


def app_at(path, apps: list[App]) -> App | None:
    """The installed program a folder (or a file) belongs to, from where the programs say they're
    installed. None for shared places (Program Files itself, Windows…)."""
    best, best_len = None, -1
    for app in apps:
        loc = (app.location or "").strip()
        if not loc or app.store or not _safe_folder(loc) or not _inside(str(path), loc):
            continue
        if len(loc) > best_len:  # (the most precise one: "Foo\\Bar" before "Foo")
            best, best_len = app, len(loc)
    return best


def app_for_shortcut(path: Path, apps: list[App]) -> App | None:
    """The installed program a shortcut (or a program file) opens: from the folder it's installed in,
    or else from the folder its icon or its uninstaller is in."""
    target = lnk_target(path) if path.suffix.lower() == ".lnk" else str(path)
    if not target:
        return None
    found = app_at(target, apps)
    if found is not None:
        return found
    folder = os.path.dirname(target)
    if not _safe_folder(folder):
        return None
    for app in apps:
        if app.store:
            continue
        for exe in (app.icon.split(",")[0].strip().strip('"'), split_command(app.uninstall)[0]):
            if exe and os.path.isabs(exe) and _inside(os.path.dirname(exe), folder) and \
                    _inside(folder, os.path.dirname(exe)):
                return app
    return None


def _match(name: str, keys: set[str]) -> str:
    """ "exact", "maybe" or ""."""
    n = norm(name)
    if not n or n in PROTECTED:
        return ""
    if n in keys or clean_name(name) in keys:
        return "exact"
    for k in keys:
        if len(k) >= 5 and n.startswith(k) and len(n) - len(k) <= 12:
            return "maybe"
    return ""


def lnk_target(path: Path) -> str:
    """Where a Windows shortcut points (the local path), or ""."""
    try:
        data = path.read_bytes()[:65536]
        if len(data) < 0x4C or data[:4] != b"\x4c\x00\x00\x00":
            return ""
        flags = int.from_bytes(data[0x14:0x18], "little")
        pos = 0x4C
        if flags & 0x01:  # HasLinkTargetIDList
            pos += 2 + int.from_bytes(data[pos:pos + 2], "little")
        if not flags & 0x02:  # HasLinkInfo
            return ""
        info = data[pos:]
        header = int.from_bytes(info[4:8], "little")
        if header >= 0x24:
            off = int.from_bytes(info[28:32], "little")
            if off:
                end = off
                while end + 1 < len(info) and info[end:end + 2] != b"\x00\x00":
                    end += 2
                return info[off:end].decode("utf-16-le", "replace")
        off = int.from_bytes(info[16:20], "little")
        end = info.find(b"\x00", off)
        return info[off:end].decode("mbcs" if sys.platform == "win32" else "latin-1", "replace")
    except (OSError, ValueError):
        return ""


def _only_its_shortcuts(folder: Path, keys: set[str], loc: str) -> bool:
    """A Start-menu folder holds nothing but this app's shortcuts (so it can go as a whole)."""
    try:
        entries = list(folder.rglob("*"))[:200]
    except OSError:
        return False
    for e in entries:
        try:
            if e.is_dir():
                continue
        except OSError:
            return False
        if e.suffix.lower() not in (".lnk", ".url", ".ini"):
            return False
        if e.suffix.lower() == ".ini":  # desktop.ini
            continue
        target = lnk_target(e) if e.suffix.lower() == ".lnk" else ""
        if not ((loc and target and _inside(target, loc)) or _match(e.stem, keys)):
            return False
    return True


def find_leftovers(app: App, others: list[App] | None = None, forced: bool = False) -> list[Leftover]:
    """What the app left behind, everything a deep clean-up would offer (see DEPTHS for what each depth
    shows and ticks). others = the apps still installed (their folders are never offered)."""
    keys = app.names()
    found: dict[str, Leftover] = {}
    other_places = []
    for o in others or []:
        if o.id == app.id:
            continue
        # where another installed app lives: its folder, and where its uninstaller and icon are
        exe_o, _ = split_command(o.uninstall)
        for place in (o.location, os.path.dirname(exe_o) if exe_o and os.path.isabs(exe_o) else "",
                      os.path.dirname(o.icon) if o.icon and os.path.isabs(o.icon) else ""):
            if place and _safe_folder(place):
                other_places.append(place)
    microsoft_app = "microsoft" in norm(app.publisher)

    def system_ok(name: str) -> bool:
        return not _system_named(name) or microsoft_app

    def belongs_to_other(path: str) -> bool:
        for loc in other_places:
            if _inside(loc, path) or (_inside(path, loc) and not _inside(path, app.location or "\0")):
                return True
        return False

    def add(item: Leftover):
        key = (item.kind, os.path.normcase(item.path), item.value.lower(), item.view)
        k = "|".join(map(str, key))
        if k in found:  # found twice: it counts as found the surer way
            old = found[k]
            old.level, old.deep = max(old.level, item.level), old.deep and item.deep
            if item.level > old.level or old.reason == "maybe":
                old.reason = item.reason
            return
        item.app = app.name
        found[k] = item

    def folder(path, reason: str, level: int, deep: bool = False):
        add(Leftover("folder", os.path.normpath(str(path)), reason if level >= 3 else "maybe" if reason in
                     ("data", "install") else reason, level=level, deep=deep))

    def dirs(root: Path, limit: int = 2000) -> list[Path]:
        try:
            out = []
            for child in root.iterdir():
                if len(out) >= limit:
                    break
                try:
                    if child.is_dir() and not is_link(child):
                        out.append(child)
                except OSError:
                    continue
            return out
        except OSError:
            return []

    def usable(path) -> bool:
        return _safe_folder(str(path)) and not belongs_to_other(str(path))

    # 1. the install folder (and the uninstaller's own folder)
    places = []
    if app.location:
        places.append((app.location, True))
    exe, _ = split_command(app.uninstall)
    if exe and os.path.isabs(exe) and not app.msi:
        places.append((os.path.dirname(exe), _match(Path(os.path.dirname(exe)).name, keys) == "exact"))
    if app.icon and os.path.isabs(app.icon):
        places.append((os.path.dirname(app.icon), _match(Path(os.path.dirname(app.icon)).name, keys) == "exact"))
    # Sure when it's in one of the usual places apps install to and named after the app. Its registered
    # folder under another name is most likely its too; a folder somewhere else ("D:\\Tools") may hold the
    # person's own things, so that one only might be.
    core = app.names(with_location=False)
    usual = [r for r in (_env_path("ProgramFiles"), _env_path("ProgramFiles(x86)"), _env_path("ProgramW6432"),
                         _env_path("LOCALAPPDATA", "Programs"), _env_path("ProgramData"), _env_path("APPDATA"),
                         _env_path("LOCALAPPDATA")) if r is not None]
    for place, own in places:
        if own and os.path.isdir(place) and usable(place) and not is_link(place) and system_ok(Path(place).name):
            named = bool(_match(Path(place).name, core)) and not _system_named(Path(place).name)
            in_usual = any(_inside(place, str(r)) for r in usual)
            folder(place, "install", 3 if named and in_usual else 2 if in_usual or named else 1)

    # 2. folders named after the app in Program Files, ProgramData, AppData... (a deep clean-up also
    # looks one level further: "AppData\\Local\\Some Vendor\\App")
    pub = clean_name(app.publisher)
    for root in search_roots():
        for child in dirs(root):
            if not system_ok(child.name):
                continue
            how = _match(child.name, keys)
            if how and usable(child):
                # a short, plain name ("Notes") might be another app's
                sure = how == "exact" and len(norm(child.name)) >= 6 and not _system_named(child.name)
                folder(child, "data", 3 if sure else 2)
            elif pub and len(pub) >= 3 and norm(child.name) in (pub, norm(app.publisher)) and pub not in PROTECTED:
                for g in dirs(child, 500):
                    how = _match(g.name, keys)
                    if how and system_ok(g.name) and usable(g):
                        folder(g, "data", 3 if how == "exact" else 2)
            elif norm(child.name) not in PROTECTED:
                for g in dirs(child, 300):
                    if _match(g.name, keys) == "exact" and len(norm(g.name)) >= 6 and system_ok(g.name) \
                            and usable(g):
                        folder(g, "data", 2, deep=True)
    if app.family:  # Store apps keep their data here
        p = _env_path("LOCALAPPDATA", "Packages", app.family)
        if p and p.is_dir():
            add(Leftover("folder", str(p), "package"))
    # deep: temporary files, and folders in the person's own places (saves, exports): never ticked
    temp = _env_path("TEMP")
    for child in dirs(temp) if temp else []:
        if _match(child.name, keys) == "exact" and len(norm(child.name)) >= 5 and usable(child):
            folder(child, "data", 2, deep=True)
    home = os.environ.get("USERPROFILE", "")
    for own in ((home, "Documents"), (home, "Saved Games"), (home, "Documents", "My Games")) if home else ():
        for child in dirs(Path(*own), 500):
            if _match(child.name, keys) == "exact" and len(norm(child.name)) >= 6 and system_ok(child.name) \
                    and usable(child):
                add(Leftover("folder", str(child), "userfiles", level=1, deep=True))

    # 3. shortcuts: named after the app, or pointing into its (now gone) install folder
    loc = app.location if app.location and _safe_folder(app.location) else ""
    for root in shortcut_roots():
        try:
            entries = list(root.rglob("*")) if "Programs" in root.parts else list(root.iterdir())
        except OSError:
            continue
        for e in entries[:5000]:
            try:
                if e.is_dir():
                    if _match(e.name, keys) == "exact" and _safe_folder(str(e)) and not is_link(e) \
                            and system_ok(e.name):
                        add(Leftover("folder", str(e), "shortcut", level=3 if _only_its_shortcuts(e, keys, loc) else 2))
                    continue
                if e.suffix.lower() not in (".lnk", ".url"):
                    continue
            except OSError:
                continue
            target = lnk_target(e) if e.suffix.lower() == ".lnk" else ""
            points_in = bool(loc and target and _inside(target, loc))
            if points_in or _match(e.stem, keys) == "exact":
                add(Leftover("shortcut", str(e), "shortcut"))

    # 4. the registry: Software\<App>, Software\<Publisher>\<App> (both views), startup entries
    for hive in ("HKCU", "HKLM"):
        for view in _views(hive):
            hv = view if hive == "HKLM" else 0
            for sub in _subkeys(hive, "Software", view):
                if norm(sub) in PROTECTED:
                    continue
                how = _match(sub, keys)
                if how:
                    sure = how == "exact" and len(norm(sub)) >= 6
                    add(Leftover("key", f"{hive}\\Software\\{sub}", "registry" if sure else "maybe",
                                 level=3 if sure else 2, view=hv))
                elif pub and norm(sub) in (pub, norm(app.publisher)):
                    children = _subkeys(hive, f"Software\\{sub}", view)
                    hits = [c for c in children if _match(c, keys) == "exact"]
                    for c in hits:
                        add(Leftover("key", f"{hive}\\Software\\{sub}\\{c}", "registry", view=hv))
                    if hits and len(hits) == len(children) and not _values(hive, f"Software\\{sub}", view):
                        add(Leftover("key", f"{hive}\\Software\\{sub}", "registry", view=hv))
                elif system_ok(sub):  # deep: Software\<Some vendor>\<App>
                    for c in _subkeys(hive, f"Software\\{sub}", view)[:300]:
                        if _match(c, keys) == "exact" and len(norm(c)) >= 6:
                            add(Leftover("key", f"{hive}\\Software\\{sub}\\{c}", "maybe", level=2, view=hv,
                                         deep=True))
            for run in RUN_KEYS:
                for name, value in _values(hive, run, view).items():
                    exe_path, _ = split_command(str(value))
                    inside = bool(loc and exe_path and _inside(exe_path, loc))
                    named = _match(name, keys) == "exact" or (exe_path and _match(Path(exe_path).stem, keys) == "exact")
                    gone = bool(exe_path and os.path.isabs(exe_path) and not os.path.exists(exe_path))
                    if inside or (named and gone):
                        add(Leftover("value", f"{hive}\\{run}", "startup", value=name, view=hv))
            _program_links(app, keys, loc, hive, view, add)
    # 5. its uninstall entry, if it's still there (a broken uninstaller, or "Remove leftovers" on its own)
    if not app.store and key_exists(app.hive, UNINSTALL + "\\" + app.key, app.view):
        add(Leftover("key", f"{app.hive}\\{UNINSTALL}\\{app.key}", "entry", level=3 if forced or app.broken else 2,
                     view=app.view if app.hive == "HKLM" else 0))
    # 6. Windows services and scheduled tasks that run one of its programs
    try:
        for item in services_of(app, keys, loc) + tasks_of(app, keys, loc):
            add(item)
    except Exception:
        log.debug("looking for services and tasks of %s failed", app.name, exc_info=True)

    items = list(found.values())
    # a folder inside another offered folder is covered by it
    folders = [i for i in items if i.kind == "folder"]
    items = [i for i in items if not (i.kind in ("folder", "file", "shortcut") and
                                      any(f is not i and _inside(i.path, f.path) and f.level >= i.level
                                          for f in folders))]
    for i in items:
        i.safe = ticked_at(i, "normal")
        if i.kind in ("folder", "file"):
            i.size = folder_size(Path(i.path), limit_s=2)
        elif i.kind == "shortcut":
            try:
                i.size = os.path.getsize(i.path)
            except OSError:
                pass
    order = {"install": 0, "data": 1, "package": 1, "shortcut": 2, "startup": 3, "service": 3, "task": 3,
             "registry": 4, "filetype": 4, "entry": 5, "maybe": 6, "userfiles": 7}
    return sorted(items, key=lambda i: (order.get(i.reason, 9), i.path.lower()))


APP_PATHS = r"Software\Microsoft\Windows\CurrentVersion\App Paths"
# names under Software\Classes that are Windows' own lists, never one app's
_CLASS_LISTS = {"clsid", "interface", "typelib", "appid", "directory", "folder", "drive", "allfilesystemobjects",
                "applications", "systemfileassociations", "mime", "protocols", "installer", "wow6432node",
                "localsettings", "unknown", "exefile", "lnkfile", "batfile", "cmdfile", "comfile", "dllfile",
                "msi.package", "msipackage", "inifile", "txtfile", "regfile", "htmlfile", "urlfile", "jpegfile",
                "pngfile", "giffile", "zip", "compressedfolder", "mailto", "http", "https", "ftp", "file"}


def _class_name_ok(name: str) -> bool:
    return bool(name) and not name.startswith((".", "{", "*")) and norm(name) not in _CLASS_LISTS \
        and norm(name) not in PROTECTED and not _system_named(name)


def _program_links(app: App, keys: set[str], loc: str, hive: str, view: int, add):
    """App Paths entries and file types (Software\\Classes) that start one of its programs."""
    hv = view if hive == "HKLM" else 0
    for sub in _subkeys(hive, APP_PATHS, view):
        target = str(_values(hive, f"{APP_PATHS}\\{sub}", view).get("", "") or "")
        exe_path, _ = split_command(target)
        inside = bool(loc and exe_path and _inside(exe_path, loc))
        gone = bool(exe_path and os.path.isabs(exe_path) and not os.path.exists(exe_path))
        if inside or (gone and _match(Path(sub).stem, keys) == "exact"):
            add(Leftover("key", f"{hive}\\{APP_PATHS}\\{sub}", "filetype", level=3 if inside else 2, view=hv))
    if not loc:  # file types are recognised by the folder their program is in
        return
    for sub in _subkeys(hive, r"Software\Classes", view):
        if not _class_name_ok(sub):
            continue
        command = str(_values(hive, f"Software\\Classes\\{sub}\\shell\\open\\command", view).get("", "") or "")
        exe_path, _ = split_command(command)
        if exe_path and _inside(exe_path, loc):
            add(Leftover("key", f"{hive}\\Software\\Classes\\{sub}", "filetype", level=2, view=hv))
    for sub in _subkeys(hive, r"Software\Classes\Applications", view):
        command = str(_values(hive, f"Software\\Classes\\Applications\\{sub}\\shell\\open\\command", view)
                      .get("", "") or "")
        exe_path, _ = split_command(command)
        if exe_path and _inside(exe_path, loc):
            add(Leftover("key", f"{hive}\\Software\\Classes\\Applications\\{sub}", "filetype", level=3, view=hv))


SERVICES = r"SYSTEM\CurrentControlSet\Services"


def _service_program(name: str) -> str:
    v = _values("HKLM", f"{SERVICES}\\{name}", 0)
    exe_path, _ = split_command(str(v.get("ImagePath") or ""))
    if exe_path.startswith("\\??\\"):
        exe_path = exe_path[4:]
    return exe_path


def _windows_program(path: str) -> bool:
    """Part of Windows (or not a real path): never touched."""
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    p = os.path.normcase(path)
    return not os.path.isabs(path) or _inside(path, windir) or p.endswith(("svchost.exe", "dllhost.exe")) or \
        p.startswith(("\\systemroot", "system32"))


def services_of(app: App, keys: set[str], loc: str) -> list[Leftover]:
    """Windows services that run one of its programs (sure), or are named after it and whose program is
    gone (most likely)."""
    out = []
    for name in _subkeys("HKLM", SERVICES, 0):
        exe_path = _service_program(name)
        if not exe_path or _windows_program(exe_path):
            continue
        shown = str(_values("HKLM", f"{SERVICES}\\{name}", 0).get("DisplayName") or "")
        inside = bool(loc and _inside(exe_path, loc))
        named = _match(name, keys) == "exact" or (shown and not shown.startswith("@") and
                                                  _match(shown, keys) == "exact")
        if inside or (named and not os.path.exists(exe_path)):
            out.append(Leftover("service", f"HKLM\\{SERVICES}\\{name}", "service", level=3 if inside else 2,
                                value=shown if not shown.startswith("@") else ""))
    return out


def scheduled_tasks() -> list[tuple[str, str]]:
    """(task name, the program it runs) for every scheduled task Windows lists."""
    import csv
    import io
    try:
        r = subprocess.run(["schtasks.exe", "/Query", "/FO", "CSV", "/V", "/NH"], capture_output=True, timeout=60,
                           creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return []
    text = r.stdout.decode("mbcs" if sys.platform == "win32" else "utf-8", "replace")
    out, seen = [], set()
    for row in csv.reader(io.StringIO(text)):
        if len(row) > 8 and row[1].startswith("\\") and row[1] not in seen:
            seen.add(row[1])
            out.append((row[1], row[8]))
    return out


def tasks_of(app: App, keys: set[str], loc: str) -> list[Leftover]:
    out = []
    for name, command in scheduled_tasks():
        if name.lower().startswith("\\microsoft\\"):
            continue
        exe_path, _ = split_command(command)
        if not exe_path or _windows_program(exe_path) and not (loc and _inside(exe_path, loc)):
            continue
        inside = bool(loc and _inside(exe_path, loc))
        leaf = name.rsplit("\\", 1)[-1]
        named = _match(leaf, keys) == "exact" or any(len(k) >= 6 and k in norm(leaf) for k in keys)
        if inside or (named and not os.path.exists(exe_path)):
            out.append(Leftover("task", name, "task", level=3 if inside else 2))
    return out



# ----------------------------------------------------------------------------- removing (with a backup)

def _reg_root_name(path: str) -> str:
    head, _, rest = path.partition("\\")
    full = {"HKLM": "HKEY_LOCAL_MACHINE", "HKCU": "HKEY_CURRENT_USER"}.get(head.upper(), head)
    return f"{full}\\{rest}" if rest else full


def _reg_exe(*args: str, timeout: float = 60) -> bool:
    try:
        r = subprocess.run(["reg.exe", *args], capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _view_flag(view: int) -> list[str]:
    return ["/reg:64"] if view == 64 else ["/reg:32"] if view == 32 else []


def _reg_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def value_to_reg(path: str, name: str, value, kind: int) -> str:
    """A .reg file that puts one registry value back."""
    if kind == 1:  # REG_SZ
        data = _reg_string(str(value))
    elif kind == 4:  # REG_DWORD
        data = f"dword:{int(value) & 0xFFFFFFFF:08x}"
    else:
        if kind in (2, 7):  # REG_EXPAND_SZ, REG_MULTI_SZ
            raw = ("\0".join(value) + "\0\0" if kind == 7 else str(value) + "\0").encode("utf-16-le")
        elif kind == 11:  # REG_QWORD
            raw = int(value).to_bytes(8, "little")
        else:
            raw = bytes(value or b"")
        data = f"hex({kind:x}):" + ",".join(f"{b:02x}" for b in raw) if kind != 3 else \
            "hex:" + ",".join(f"{b:02x}" for b in raw)
    return f"Windows Registry Editor Version 5.00\r\n\r\n[{_reg_root_name(path)}]\r\n{_reg_string(name)}={data}\r\n"


# ---- what the remove and restore steps accept
# They also run as administrator (the elevated helper reads its plan from a file in the user's
# folder), so every item is checked again here, whatever the plan says: only the places leftovers
# can come from, never Windows' own files or settings.

def _path_ok(path: str, kind: str) -> bool:
    """A file, shortcut or folder that may be moved away or put back."""
    if not path or not os.path.isabs(path):
        return False
    p = os.path.normcase(os.path.normpath(path)).rstrip("\\/")
    windir = os.path.normcase(os.environ.get("SystemRoot", "c:\\windows"))
    if p == windir or p.startswith(windir + os.sep):
        return False
    if any(norm(part) in ("windowsapps", "system32", "syswow64", "winsxs") for part in Path(p).parts):
        return False
    if _profile_place(p):
        return False
    if kind == "folder":
        return _safe_folder(path)
    return p not in _protected_paths() and len(Path(p).parts) >= 3


_PROFILE_FOLDERS = {"documents", "desktop", "downloads", "pictures", "music", "videos", "appdata", "onedrive",
                    "favorites", "contacts", "links", "savedgames", "searches", "3dobjects"}
_APPDATA_FOLDERS = {"roaming", "local", "locallow"}


def _profile_place(p: str) -> bool:
    """Any account's profile folder or one of its standard folders (whoever's environment this is):
    "C:\\Users\\Sam", "...\\Sam\\Documents", "...\\AppData\\Roaming", "...\\Local\\Programs"."""
    parts = [norm(x) for x in Path(p).parts[1:]]
    if not parts or parts[0] != "users":
        return False
    rest = parts[2:]  # after "Users\\<name>"
    if len(parts) == 2 or not rest:
        return True
    if len(rest) == 1:
        return rest[0] in _PROFILE_FOLDERS
    if rest[0] == "appdata" and len(rest) == 2:
        return rest[1] in _APPDATA_FOLDERS
    return rest[:3] == ["appdata", "local", "programs"] and len(rest) == 3


def _registry_ok(path: str, kind: str) -> bool:
    """A registry key or value that may be removed or put back: an app's own key under Software,
    its uninstall entry, or (values only) its line in a startup key."""
    head, _, sub = path.partition("\\")
    if head.upper() not in ("HKCU", "HKLM"):
        return False
    s = sub.strip("\\").lower()
    if kind == "value":
        return s in (k.lower() for k in RUN_KEYS)
    parts = s.split("\\")
    if kind == "service":  # one service's own key (never one of Windows' services: see _service_ok)
        return head.upper() == "HKLM" and len(parts) == 4 and "\\".join(parts[:3]) == SERVICES.lower()
    if s.startswith(UNINSTALL.lower() + "\\") or (s.startswith(APP_PATHS.lower() + "\\") and
                                                  len(parts) == len(APP_PATHS.split("\\")) + 1):
        return True
    if parts[:2] == ["software", "classes"]:  # a file type or program link of its own
        if len(parts) >= 4 and parts[2] == "applications":
            return parts[3].endswith(".exe")
        return len(parts) >= 3 and _class_name_ok(parts[2])
    return len(parts) >= 2 and parts[0] == "software" and norm(parts[1]) not in PROTECTED


def _reg_file_ok(reg_file: Path, item: dict) -> bool:
    """Every key a backed-up .reg file would write is the item's own key (or inside it)."""
    try:
        text = reg_file.read_text("utf-16")
    except (OSError, UnicodeError):
        try:
            text = reg_file.read_text("utf-8-sig")
        except (OSError, UnicodeError):
            return False
    want = _reg_root_name(str(item.get("path", ""))).lower()
    # a 32-bit key is exported with its real place, under WOW6432Node
    wants = {want, want.replace("\\software\\", "\\software\\wow6432node\\", 1)}
    keys = [ln.strip()[1:-1].lstrip("-").lower() for ln in text.splitlines() if ln.strip().startswith("[")]
    if not keys:
        return False
    if item.get("kind") == "value":
        return all(k in wants for k in keys)
    return all(any(k == w or k.startswith(w + "\\") for w in wants) for k in keys)


def item_allowed(item: dict) -> bool:
    kind = str(item.get("kind", ""))
    path = str(item.get("path", ""))
    if kind in ("folder", "file", "shortcut"):
        return _path_ok(path, kind) and not is_link(path)
    if kind in ("key", "value", "service"):
        return _registry_ok(path, kind) and (kind != "value" or bool(item.get("value")))
    if kind == "task":
        return path.startswith("\\") and not path.lower().startswith("\\microsoft\\") and '"' not in path \
            and len(path) < 400
    return False


def _service_ok(path: str) -> bool:
    """A service that may be removed now: it exists, and its program isn't part of Windows."""
    name = path.rsplit("\\", 1)[-1]
    exe_path = _service_program(name)
    return bool(exe_path) and not _windows_program(exe_path)


def _move(src: Path, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dest)
    except OSError as e:
        if getattr(e, "winerror", None) == 17 or e.errno == 18:  # another drive: copy, then remove
            shutil.move(str(src), str(dest))
        else:
            raise


def _move_files(src: Path, dest: Path, overwrite: bool = False) -> list[str]:
    """Moves a folder file by file (for when some of its files are in use and it can't move as a whole),
    then removes the folders left empty. Returns the files that couldn't be moved."""
    left = []
    for root, folders, files in os.walk(src):
        rel = os.path.relpath(root, src)
        for f in files:
            a, b = os.path.join(root, f), os.path.join(dest, rel, f)
            try:
                if os.path.exists(b) and not overwrite:
                    left.append(a)
                    continue
                os.makedirs(os.path.dirname(b), exist_ok=True)
                os.replace(a, b)
            except OSError:
                left.append(a)
        folders[:] = [d for d in folders if not is_link(os.path.join(root, d))]
    for root, _folders, _files in sorted(os.walk(src), key=lambda t: -len(t[0])):
        try:
            os.rmdir(root)
        except OSError:
            pass
    return left


def remove_on_restart(paths: list[str]) -> int:
    """Asks Windows to delete these files (and then their folders, once empty) when the PC restarts.
    Needs administrator. Returns how many were taken."""
    k32 = _dll("kernel32")
    done, parents = 0, set()
    for path in paths:
        if k32.MoveFileExW(path, None, 4):  # MOVEFILE_DELAY_UNTIL_REBOOT
            done += 1
            parents.add(os.path.dirname(path))
    for folder in sorted(parents, key=len, reverse=True):
        k32.MoveFileExW(folder, None, 4)  # Windows only removes it if it's empty by then
    return done


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]


def recycle(path: Path) -> bool:
    """Sends a file or folder to the Recycle Bin (where it can be put back from)."""
    op = SHFILEOPSTRUCTW()
    op.wFunc = 3  # FO_DELETE
    op.pFrom = str(path) + "\0"  # the list ends with two nulls
    op.fFlags = 0x04 | 0x10 | 0x40 | 0x400  # silent, no questions, allow undo (Recycle Bin), no error windows
    ok = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted
    return ok and not path.exists()


def _same_drive(a: Path, b: Path) -> bool:
    return os.path.splitdrive(str(a))[0].lower() == os.path.splitdrive(str(b))[0].lower()


def new_backup(app_names: list[str]) -> Path:
    stamp = time.strftime("%Y-%m-%d %H.%M.%S")
    name = re.sub(r'[<>:"/\\|?*]', "", ", ".join(app_names))[:60].strip() or "apps"
    folder = BACKUP_DIR / f"{stamp} {name}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _manifest(folder: Path) -> dict:
    try:
        return json.loads((folder / "backup.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {"apps": [], "time": time.time(), "items": []}


def _save_manifest(folder: Path, data: dict):
    tmp = folder / "backup.json.tmp"
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), "utf-8")
    os.replace(tmp, folder / "backup.json")


def remove(items: list[Leftover], folder: Path, apps: list[str] | None = None, admin: bool = False,
           on_item=None, close_running: bool = False) -> dict:
    """Moves files into the backup folder and exports, then deletes, registry keys and values.
    Returns {"done": [...], "failed": [...], "freed": bytes}; failed items can be tried again as admin.
    on_item(number, count, item) hears which one is next. close_running: first end the programs still
    running from the folders (they keep their files in use)."""
    if close_running:
        try:
            close_programs([pid for pid, _ in programs_in([i.path for i in items if i.kind == "folder"])])
        except Exception:
            log.debug("couldn't close the programs still running", exc_info=True)
    data = _manifest(folder)
    if apps:
        data["apps"] = sorted(set(data.get("apps", [])) | set(apps))
    data["time"] = data.get("time") or time.time()
    data.setdefault("items", [])
    done, failed, freed = [], [], 0

    def keep(record):
        """Written down straight away: if Corefold or the PC stops half-way, the backup still lists
        everything that was already moved, so it can all be put back."""
        if record.get("stored"):
            data["items"].append(record)
            _save_manifest(folder, data)
    winreg = _winreg() if any(i.is_registry for i in items) else None
    for number, item in enumerate(items):
        if on_item:
            on_item(number, len(items), item)
        n = uuid.uuid4().hex[:10]
        record = dict(asdict(item), stored="")
        try:
            if not item_allowed(record):
                raise PermissionError("not a place Corefold cleans up")
            if item.kind in ("folder", "file", "shortcut"):
                src = Path(item.path)
                if not src.exists():
                    done.append(record)
                    continue
                if item.kind == "folder" and not _safe_folder(item.path):
                    raise PermissionError("protected folder")
                if not _same_drive(src, folder):
                    # on another drive (a games drive...): copying it into the backup could take ages
                    # and fill the system drive, so it goes to that drive's Recycle Bin instead
                    if not recycle(src):
                        raise PermissionError("couldn't move to the Recycle Bin")
                    record["stored"] = "recycle"
                    freed += item.size
                    done.append(record)
                    keep(record)
                    _remove_empty_parent(src.parent)
                    continue
                dest = folder / "files" / n / src.name
                try:
                    _move(src, dest)
                except OSError:  # some of its files are in use (or locked): take what can be taken
                    if item.kind != "folder" or not src.is_dir():
                        raise
                    left = _move_files(src, dest)
                    if left and admin:  # still in use: Windows removes them when the PC restarts
                        record["left"] = len(left)
                        record["on_restart"] = remove_on_restart(left)
                    if not dest.exists():  # nothing could be moved
                        if not record.get("on_restart"):
                            raise
                        done.append(record)
                        continue
                    if left and not admin:  # the rest is tried again as administrator (see remove_selected)
                        record["left"] = len(left)
                        failed.append(asdict(item))
                if admin:  # moved out of Program Files: let the user delete the backup later
                    subprocess.run(["icacls", str(dest), "/reset", "/T", "/C", "/L", "/Q"], capture_output=True,
                                   creationflags=NO_WINDOW, timeout=300)
                record["stored"] = str(dest.relative_to(folder))
                freed += item.size
                _remove_empty_parent(src.parent)
            elif item.kind == "service":
                if not _service_ok(item.path):
                    raise PermissionError("not a service Corefold removes")
                name = item.path.rsplit("\\", 1)[-1]
                reg_file = folder / "registry" / f"{n}.reg"
                reg_file.parent.mkdir(parents=True, exist_ok=True)
                if not _reg_exe("export", _reg_root_name(item.path), str(reg_file), "/y"):
                    raise PermissionError("couldn't back up " + item.path)
                _run("sc.exe", "stop", name)
                for _ in range(20):  # it may take a moment to stop
                    if "STOPPED" in _run("sc.exe", "query", name) or not key_exists("HKLM", f"{SERVICES}\\{name}", 0):
                        break
                    time.sleep(0.5)
                if not _run_ok("sc.exe", "delete", name):
                    reg_file.unlink(missing_ok=True)
                    raise PermissionError("couldn't remove the service " + name)
                record["stored"] = str(reg_file.relative_to(folder))
            elif item.kind == "task":
                xml = _run("schtasks.exe", "/Query", "/TN", item.path, "/XML", raw=True)
                if not xml:
                    done.append(record)  # already gone
                    continue
                task_file = folder / "tasks" / f"{n}.xml"
                task_file.parent.mkdir(parents=True, exist_ok=True)
                if not xml.startswith((b"\xff\xfe", b"\xfe\xff")):  # it says UTF-16, but comes as plain text
                    xml = xml.decode("mbcs" if sys.platform == "win32" else "utf-8", "replace").encode("utf-16")
                task_file.write_bytes(xml)
                if not _run_ok("schtasks.exe", "/Delete", "/TN", item.path, "/F"):
                    task_file.unlink(missing_ok=True)
                    raise PermissionError("couldn't remove the task " + item.path)
                record["stored"] = str(task_file.relative_to(folder))
            elif item.kind == "key":
                reg_file = folder / "registry" / f"{n}.reg"
                reg_file.parent.mkdir(parents=True, exist_ok=True)
                root = _reg_root_name(item.path)
                head, _, sub = item.path.partition("\\")
                if not key_exists(head.upper(), sub, item.view):
                    done.append(record)
                    continue
                if not _reg_exe("export", root, str(reg_file), "/y", *_view_flag(item.view)):
                    raise PermissionError("couldn't back up " + item.path)
                if not _reg_exe("delete", root, "/f", *_view_flag(item.view)):
                    reg_file.unlink(missing_ok=True)
                    raise PermissionError("couldn't remove " + item.path)
                record["stored"] = str(reg_file.relative_to(folder))
            elif item.kind == "value":
                head, _, sub = item.path.partition("\\")
                with winreg.OpenKey(_root(head.upper()), sub, 0, _access(item.view, write=True)) as k:
                    value, kind = winreg.QueryValueEx(k, item.value)
                    reg_file = folder / "registry" / f"{n}.reg"
                    reg_file.parent.mkdir(parents=True, exist_ok=True)
                    reg_file.write_text(value_to_reg(item.path, item.value, value, kind), "utf-16")
                    winreg.DeleteValue(k, item.value)
                record["stored"] = str(reg_file.relative_to(folder))
            done.append(record)
            keep(record)
        except FileNotFoundError:
            done.append(record)
        except Exception as e:
            log.info("couldn't remove %s: %s", item.label(), e)
            failed.append(asdict(item))
    return {"done": done, "failed": failed, "freed": freed}


def _run(*args: str, raw: bool = False, timeout: float = 60):
    """A Windows command's output ("" or b"" if it failed)."""
    try:
        r = subprocess.run(list(args), capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return b"" if raw else ""
    if raw:
        return r.stdout if r.returncode == 0 else b""
    return r.stdout.decode("mbcs" if sys.platform == "win32" else "utf-8", "replace")


def _run_ok(*args: str, timeout: float = 60) -> bool:
    try:
        return subprocess.run(list(args), capture_output=True, timeout=timeout,
                              creationflags=NO_WINDOW).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _remove_empty_parent(parent: Path):
    """After moving "Acme\\Tool" away, also remove "Acme" if nothing else is in it."""
    try:
        if _safe_folder(str(parent)) and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def items_from(dicts: list[dict]) -> list[Leftover]:
    names = {f for f in Leftover.__dataclass_fields__}
    return [Leftover(**{k: v for k, v in d.items() if k in names}) for d in dicts]


# ----------------------------------------------------------------------------- backups and undo

def list_backups() -> list[tuple[Path, dict]]:
    out = []
    try:
        folders = [p for p in BACKUP_DIR.iterdir() if p.is_dir()]
    except OSError:
        return out
    for f in folders:
        data = _manifest(f)
        if data.get("items"):
            out.append((f, data))
    return sorted(out, key=lambda t: t[1].get("time", 0), reverse=True)


def backup_size(folder: Path) -> int:
    return folder_size(folder / "files", limit_s=2)


def restore(folder: Path) -> dict:
    """Puts everything in a backup back where it was. Returns {"done": n, "failed": [items]}."""
    data = _manifest(folder)
    left, done, recycled = [], 0, 0
    for item in data.get("items", []):
        if item.get("stored") == "recycle":
            recycled += 1  # in the Recycle Bin: put it back from there
            continue
        stored = folder / item.get("stored", "")
        try:
            if not item_allowed(item) or not _inside(str(stored.resolve()), str(folder.resolve())):
                raise PermissionError("not something Corefold backed up")
            if item["kind"] in ("folder", "file", "shortcut"):
                target = Path(item["path"])
                if not stored.exists():
                    continue
                if target.exists():
                    # what was in use stayed behind (or the app made the folder again): put the rest
                    # back around it, never over a file that's there
                    if item["kind"] != "folder" or not target.is_dir() or _move_files(stored, target):
                        raise FileExistsError(str(target))
                else:
                    _move(stored, target)
            elif item["kind"] == "task":
                if not _run_ok("schtasks.exe", "/Create", "/TN", item["path"], "/XML", str(stored), "/F"):
                    raise PermissionError("couldn't put back the task " + item["path"])
            else:
                if not _reg_file_ok(stored, item):
                    raise PermissionError("unexpected keys in " + str(stored))
                if not _reg_exe("import", str(stored), *_view_flag(int(item.get("view") or 0))):
                    raise PermissionError("couldn't import " + str(stored))
            done += 1
        except Exception as e:
            log.info("couldn't restore %s: %s", item.get("path"), e)
            left.append(item)
    data["items"] = left
    if left:
        _save_manifest(folder, data)
    else:
        shutil.rmtree(folder, ignore_errors=True)
    return {"done": done, "failed": left, "recycled": recycled}


def delete_backup(folder: Path) -> bool:
    shutil.rmtree(folder, ignore_errors=True)
    return not folder.exists()


def prune_backups(days: int = KEEP_DAYS):
    cutoff = time.time() - days * 86400
    for folder, data in list_backups():
        if data.get("time", time.time()) < cutoff:
            delete_backup(folder)


# ----------------------------------------------------------------------------- the admin helper

def run_elevated(command: list[str], kind: str, payload: dict, cancel: threading.Event | None = None) -> dict | None:
    """Runs "Corefold.exe --<kind> plan.json" as administrator and returns what it wrote back
    (None if the user said no to the admin prompt)."""
    work = BACKUP_DIR.parent / "helper"
    work.mkdir(parents=True, exist_ok=True)
    plan = work / f"{kind}-{uuid.uuid4().hex[:8]}.json"
    plan.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    result = plan.with_suffix(".result.json")
    exe, args = command[0], " ".join(f'"{a}"' for a in command[1:] + [f"--{kind}", str(plan)])
    handle = shell_run(exe, args, admin=True, show=0)
    if handle is None:
        plan.unlink(missing_ok=True)
        return None
    wait_process(handle, cancel)
    try:
        return json.loads(result.read_text("utf-8"))
    except (OSError, ValueError):
        return {"error": "no result"}
    finally:
        plan.unlink(missing_ok=True)
        result.unlink(missing_ok=True)


def helper_main(argv: list[str]) -> int:
    """What the elevated copy does: --cleanup plan.json, --restore plan.json or --restore-point plan.json."""
    kind = next((a[2:] for a in argv if a in ("--cleanup", "--restore", "--restore-point")), "")
    try:
        plan = Path(argv[argv.index("--" + kind) + 1])
        payload = json.loads(plan.read_text("utf-8"))
    except (IndexError, ValueError, OSError):
        return 2
    # only plans Corefold wrote (in its "helper" folder), about its own backup folders. The plan's
    # own place is used, not this account's: when someone signs in with another (administrator)
    # account at the admin prompt, this copy runs as that account, with other folders.
    folder = str(payload.get("folder", "")) if isinstance(payload, dict) else ""
    home = plan.resolve().parent.parent  # ...\Corefold
    if plan.resolve().parent.name.lower() != "helper" or home.name.lower() not in ("corefold", LEGACY_NAME.lower()) or \
            (kind != "restore-point" and not (folder and _inside(str(Path(folder).resolve()),
                                                                  str(home / BACKUP_DIR.name)))):
        return 2
    try:
        if kind == "cleanup":
            out = remove(items_from(payload["items"]), Path(payload["folder"]), payload.get("apps"), admin=True,
                         close_running=bool(payload.get("close")))
        elif kind == "restore":
            r = restore(Path(payload["folder"]))
            out = {"done": r["done"], "failed": r["failed"]}
        else:
            desc = str(payload.get("description", "Corefold"))[:60].replace("'", "")
            powershell(f"Checkpoint-Computer -Description '{desc}' -RestorePointType APPLICATION_UNINSTALL",
                       timeout=600)
            out = {"done": 1}
    except Exception as e:
        out = {"error": str(e)}
    plan.with_suffix(".result.json").write_text(json.dumps(out, ensure_ascii=False), "utf-8")
    return 0
