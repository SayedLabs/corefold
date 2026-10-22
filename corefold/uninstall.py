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
}

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
            out.add(norm(Path(self.location.rstrip("\\/")).name))
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
    total, start = 0, time.monotonic()
    try:
        if path.is_file():
            return path.stat().st_size
        for root, dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
            if time.monotonic() - start > limit_s:
                break
    except OSError:
        pass
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


def shell_run(exe: str, args: str = "", admin: bool = False, show: int = 1, cwd: str | None = None):
    """Starts a program the way Explorer does (so its "run as administrator" request is honoured).
    Returns a process handle (0 if it started without one), or None if it didn't start (or the user
    said no to the admin prompt)."""
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
    ok = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info))
    if not ok:
        return None
    return info.hProcess or 0  # 0: started, but Windows gave no handle to wait on


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


def start_uninstall(app: App, quiet: bool = False):
    """Starts the app's own uninstaller. Returns a process handle (None if it couldn't start)."""
    if app.store:
        script = f"Remove-AppxPackage -Package '{app.store}'"
        return shell_run("powershell.exe", f'-NoProfile -NonInteractive -WindowStyle Hidden -Command "{script}"',
                         show=0)
    exe, args = uninstall_command(app, quiet)
    if not exe:
        return None
    cwd = os.path.dirname(exe) if os.path.isabs(exe) and os.path.isdir(os.path.dirname(exe)) else None
    return shell_run(exe, args, cwd=cwd)


def still_installed(app: App) -> bool:
    if app.store:
        try:
            out = powershell(f"if (Get-AppxPackage | Where-Object PackageFullName -eq '{app.store}') {{ 'yes' }}",
                             timeout=30)
            return out.strip() == "yes"
        except Exception:
            return False
    return key_exists(app.hive, UNINSTALL + "\\" + app.key, app.view)


def wait_until_removed(app: App, handle, cancel: threading.Event, on_state=None, max_s: float = 30 * 60) -> bool:
    """Waits for the uninstaller to finish. Many uninstallers start a copy of themselves and quit
    straight away, so this also watches the app's uninstall entry. True once the app is gone."""
    wait_process(handle, cancel)
    if on_state:
        on_state("finishing")
    start = time.monotonic()
    while not cancel.is_set() and time.monotonic() - start < max_s:
        if not still_installed(app):
            return True
        time.sleep(1.0 if not app.store else 2.0)
    return not still_installed(app)


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

    @property
    def is_registry(self) -> bool:
        return self.kind in ("key", "value")

    @property
    def needs_admin(self) -> bool:
        if self.is_registry:
            return self.path.upper().startswith("HKLM")
        p = os.path.normcase(self.path)
        user = os.path.normcase(os.environ.get("USERPROFILE", str(Path.home())))
        return not p.startswith(user + os.sep)

    def label(self) -> str:
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
             _env_path("USERPROFILE", "Desktop"), _env_path("PUBLIC", "Desktop")]
    return [r for r in roots if r is not None and r.is_dir()]


@functools.lru_cache(maxsize=1)
def _protected_paths() -> frozenset[str]:
    paths = {os.environ.get(n, "") for n in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)",
                                             "ProgramW6432", "ProgramData", "APPDATA", "LOCALAPPDATA",
                                             "USERPROFILE", "PUBLIC", "SystemDrive", "CommonProgramFiles",
                                             "CommonProgramFiles(x86)", "CommonProgramW6432", "TEMP", "TMP")}
    paths |= {str(r) for r in search_roots()} | {str(r) for r in shortcut_roots()}
    # Corefold's own folders (its settings, these backups, and the app itself)
    for name in ("Corefold", "Satsuma"):  # Satsuma: its name before 1.7.0
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


def _inside(child: str, parent: str) -> bool:
    c = os.path.normcase(os.path.normpath(child)).rstrip("\\/")
    p = os.path.normcase(os.path.normpath(parent)).rstrip("\\/")
    return c == p or c.startswith(p + os.sep)


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


def find_leftovers(app: App, others: list[App] | None = None, forced: bool = False) -> list[Leftover]:
    """What the app left behind. others = the apps still installed (their folders are never offered)."""
    keys = app.names()
    found: dict[str, Leftover] = {}
    other_places = [o.location for o in (others or [])
                    if o.id != app.id and o.location and _safe_folder(o.location)]

    def belongs_to_other(path: str) -> bool:
        for loc in other_places:
            if _inside(loc, path) or (_inside(path, loc) and not _inside(path, app.location or "\0")):
                return True
        return False

    def add(item: Leftover):
        key = (item.kind, os.path.normcase(item.path), item.value.lower(), item.view)
        k = "|".join(map(str, key))
        if k in found:
            old = found[k]
            old.safe = old.safe or item.safe
            return
        item.app = app.name
        found[k] = item

    # 1. the install folder (and the uninstaller's own folder)
    places = []
    if app.location:
        places.append((app.location, True))
    exe, _ = split_command(app.uninstall)
    if exe and os.path.isabs(exe) and not app.msi:
        places.append((os.path.dirname(exe), _match(Path(os.path.dirname(exe)).name, keys) == "exact"))
    if app.icon and os.path.isabs(app.icon):
        places.append((os.path.dirname(app.icon), _match(Path(os.path.dirname(app.icon)).name, keys) == "exact"))
    # Ticked when it's in one of the usual places apps install to, or named after the app. A folder
    # somewhere else ("D:\\Tools") may hold the person's own things too, so that one is only offered.
    core = app.names(with_location=False)
    usual = [r for r in (_env_path("ProgramFiles"), _env_path("ProgramFiles(x86)"), _env_path("ProgramW6432"),
                         _env_path("LOCALAPPDATA", "Programs"), _env_path("ProgramData"), _env_path("APPDATA"),
                         _env_path("LOCALAPPDATA")) if r is not None]
    for place, sure in places:
        if sure and os.path.isdir(place) and _safe_folder(place) and not belongs_to_other(place):
            ticked = any(_inside(place, str(r)) for r in usual) or _match(Path(place).name, core) == "exact"
            add(Leftover("folder", os.path.normpath(place), "install" if ticked else "maybe", ticked))

    # 2. folders named after the app in Program Files, ProgramData, AppData...
    pub = clean_name(app.publisher)
    for root in search_roots():
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                if not child.is_dir() or child.is_symlink():
                    continue
            except OSError:
                continue
            how = _match(child.name, keys)
            if how and _safe_folder(str(child)) and not belongs_to_other(str(child)):
                # a short, plain name ("Notes") might be another app's: shown, but not ticked
                sure = how == "exact" and len(norm(child.name)) >= 6
                add(Leftover("folder", str(child), "data" if sure else "maybe", sure))
            elif pub and len(pub) >= 3 and norm(child.name) in (pub, norm(app.publisher)) and pub not in PROTECTED:
                try:
                    grand = [g for g in child.iterdir() if g.is_dir()]
                except OSError:
                    continue
                for g in grand:
                    how = _match(g.name, keys)
                    if how and _safe_folder(str(g)) and not belongs_to_other(str(g)):
                        add(Leftover("folder", str(g), "data" if how == "exact" else "maybe", how == "exact"))
    if app.family:  # Store apps keep their data here
        p = _env_path("LOCALAPPDATA", "Packages", app.family)
        if p and p.is_dir():
            add(Leftover("folder", str(p), "package", True))

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
                    if _match(e.name, keys) == "exact" and _safe_folder(str(e)):
                        add(Leftover("folder", str(e), "shortcut", True))
                    continue
                if e.suffix.lower() not in (".lnk", ".url"):
                    continue
            except OSError:
                continue
            target = lnk_target(e) if e.suffix.lower() == ".lnk" else ""
            points_in = bool(loc and target and _inside(target, loc))
            if points_in or _match(e.stem, keys) == "exact":
                add(Leftover("shortcut", str(e), "shortcut", True))

    # 4. the registry: Software\<App>, Software\<Publisher>\<App> (both views), startup entries
    for hive in ("HKCU", "HKLM"):
        for view in _views(hive):
            for sub in _subkeys(hive, "Software", view):
                if norm(sub) in PROTECTED:
                    continue
                how = _match(sub, keys)
                if how:
                    sure = how == "exact" and len(norm(sub)) >= 6
                    add(Leftover("key", f"{hive}\\Software\\{sub}", "registry" if sure else "maybe",
                                 sure, view=view if hive == "HKLM" else 0))
                elif pub and norm(sub) in (pub, norm(app.publisher)):
                    children = _subkeys(hive, f"Software\\{sub}", view)
                    hits = [c for c in children if _match(c, keys) == "exact"]
                    for c in hits:
                        add(Leftover("key", f"{hive}\\Software\\{sub}\\{c}", "registry", True,
                                     view=view if hive == "HKLM" else 0))
                    if hits and len(hits) == len(children) and not _values(hive, f"Software\\{sub}", view):
                        add(Leftover("key", f"{hive}\\Software\\{sub}", "registry", True,
                                     view=view if hive == "HKLM" else 0))
            for run in RUN_KEYS:
                for name, value in _values(hive, run, view).items():
                    exe_path, _ = split_command(str(value))
                    inside = bool(loc and exe_path and _inside(exe_path, loc))
                    named = _match(name, keys) == "exact" or (exe_path and _match(Path(exe_path).stem, keys) == "exact")
                    gone = bool(exe_path and os.path.isabs(exe_path) and not os.path.exists(exe_path))
                    if inside or (named and gone):
                        add(Leftover("value", f"{hive}\\{run}", "startup", True, value=name,
                                     view=view if hive == "HKLM" else 0))
    # 5. its uninstall entry, if it's still there (a broken uninstaller, or "Remove leftovers" on its own)
    if not app.store and key_exists(app.hive, UNINSTALL + "\\" + app.key, app.view):
        add(Leftover("key", f"{app.hive}\\{UNINSTALL}\\{app.key}", "entry", forced or app.broken,
                     view=app.view if app.hive == "HKLM" else 0))

    items = list(found.values())
    # a folder inside another offered folder is covered by it
    folders = [i for i in items if i.kind == "folder"]
    items = [i for i in items if not (i.kind in ("folder", "file", "shortcut") and
                                      any(f is not i and _inside(i.path, f.path) for f in folders))]
    for i in items:
        if i.kind in ("folder", "file"):
            i.size = folder_size(Path(i.path), limit_s=2)
        elif i.kind == "shortcut":
            try:
                i.size = os.path.getsize(i.path)
            except OSError:
                pass
    order = {"install": 0, "data": 1, "package": 1, "shortcut": 2, "startup": 3, "registry": 4, "entry": 5,
             "maybe": 6}
    return sorted(items, key=lambda i: (order.get(i.reason, 9), i.path.lower()))


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


def _move(src: Path, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dest)
    except OSError as e:
        if getattr(e, "winerror", None) == 17 or e.errno == 18:  # another drive: copy, then remove
            shutil.move(str(src), str(dest))
        else:
            raise


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


def remove(items: list[Leftover], folder: Path, apps: list[str] | None = None, admin: bool = False) -> dict:
    """Moves files into the backup folder and exports, then deletes, registry keys and values.
    Returns {"done": [...], "failed": [...], "freed": bytes}; failed items can be tried again as admin."""
    data = _manifest(folder)
    if apps:
        data["apps"] = sorted(set(data.get("apps", [])) | set(apps))
    done, failed, freed = [], [], 0
    winreg = _winreg() if any(i.is_registry for i in items) else None
    for item in items:
        n = uuid.uuid4().hex[:10]
        record = dict(asdict(item), stored="")
        try:
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
                    _remove_empty_parent(src.parent)
                    continue
                dest = folder / "files" / n / src.name
                _move(src, dest)
                if admin:  # moved out of Program Files: let the user delete the backup later
                    subprocess.run(["icacls", str(dest), "/reset", "/T", "/C", "/Q"], capture_output=True,
                                   creationflags=NO_WINDOW, timeout=300)
                record["stored"] = str(dest.relative_to(folder))
                freed += item.size
                _remove_empty_parent(src.parent)
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
        except FileNotFoundError:
            done.append(record)
        except Exception as e:
            log.info("couldn't remove %s: %s", item.label(), e)
            failed.append(asdict(item))
    data.setdefault("items", []).extend(r for r in done if r.get("stored"))
    data["time"] = data.get("time") or time.time()
    if data["items"]:
        _save_manifest(folder, data)
    return {"done": done, "failed": failed, "freed": freed}


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
            if item["kind"] in ("folder", "file", "shortcut"):
                target = Path(item["path"])
                if not stored.exists():
                    continue
                if target.exists():
                    raise FileExistsError(str(target))
                _move(stored, target)
            else:
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
    try:
        if kind == "cleanup":
            out = remove(items_from(payload["items"]), Path(payload["folder"]), payload.get("apps"), admin=True)
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
