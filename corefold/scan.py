"""Corefold Scan: checks this PC for malware, without anything leaving it.

Two parts, kept apart on purpose:
- Microsoft Defender, which is part of Windows, does the scanning, through its own command-line tool
  (MpCmdRun.exe). Corefold never decides itself that a file is harmful or harmless: a threat is what
  Defender says it is, and what happens to it (quarantine) is Defender's own doing, the same as when it's
  started from Windows Security.
- Corefold's own checks look for tricks malware uses that a list of known threats can miss: a program
  dressed up as a photo (photo.jpg.exe), a name with a hidden character that turns it around, a program
  whose signature is broken, programs without a signature that start with Windows from odd places, a
  hidden PowerShell command that starts with Windows, the hosts file sending Windows Update or a bank
  somewhere else, and folders Defender has been told to skip. These are shown as "worth a look", never
  as a virus.

Nothing is uploaded or looked up online by Corefold: Defender's list of threats is the one Windows keeps
up to date, and signatures are checked with what Windows already has (see trust.py)."""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import trust

log = logging.getLogger("corefold")

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
QUICK, FULL, CUSTOM = "quick", "full", "custom"
LAST_FILE = "scan-last.json"
IGNORED_FILE = "scan-ignored.json"

# the kinds of files that run when they're opened
RUNNABLE = {".exe", ".scr", ".com", ".pif", ".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
            ".hta", ".ps1", ".msi", ".jar", ".cpl", ".lnk", ".reg", ".appref-ms", ".application", ".msc"}
PROGRAMS = {".exe", ".scr", ".com", ".pif", ".msi", ".cpl"}
SCRIPTS = {".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".ps1"}
# what a program pretends to be: "invoice.pdf.exe"
LOOKS_LIKE = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff", ".pdf", ".doc", ".docx",
              ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf", ".csv", ".odt", ".mp3", ".wav", ".m4a", ".mp4",
              ".avi", ".mkv", ".mov", ".wmv", ".zip", ".rar", ".7z", ".html", ".htm", ".eml", ".msg"}
# characters that flip how a name is shown ("Invoice\u202Efdp.exe" looks like "Invoiceexe.pdf")
FLIPPERS = {"\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069", "\u200e",
            "\u200f"}
# sites the hosts file should never send elsewhere (malware does it to stop updates, or to fake a bank's page)
GUARDED_SITES = ("microsoft.com", "windowsupdate.com", "windows.com", "update.microsoft.com", "live.com",
                 "office.com", "google.com", "gmail.com", "youtube.com", "facebook.com", "apple.com", "icloud.com",
                 "amazon.com", "paypal.com", "bing.com", "github.com", "wdcp.microsoft.com", "kaspersky.com",
                 "eset.com", "bitdefender.com", "malwarebytes.com", "avast.com", "avg.com", "norton.com",
                 "mcafee.com", "sophos.com", "virustotal.com", "trendmicro.com")
LOCAL_HOSTS = {"localhost", "localhost.localdomain", "local", "broadcasthost", "ip6-localhost", "ip6-loopback"}
# how deep to look in each place, and how much at most (so a huge Downloads folder doesn't take forever)
MAX_FILES = 400_000
MAX_SIGNATURES = 3_000


def _env(name: str, default: str = "") -> Path:
    return Path(os.environ.get(name) or default)


def home() -> Path:
    return _env("USERPROFILE", str(Path.home()))


def downloads() -> Path:
    """This person's Downloads folder (wherever they moved it)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            value = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")[0]
            if value:
                return Path(os.path.expandvars(value))
    except (OSError, ImportError):
        pass
    return home() / "Downloads"


def desktop() -> Path:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            value = winreg.QueryValueEx(k, "Desktop")[0]
            if value:
                return Path(os.path.expandvars(value))
    except (OSError, ImportError):
        pass
    return home() / "Desktop"


def temp() -> Path:
    return _env("TEMP", str(home() / "AppData" / "Local" / "Temp"))


def startup_folders() -> list[Path]:
    tail = Path("Microsoft") / "Windows" / "Start Menu" / "Programs"
    return [_env("APPDATA", str(home() / "AppData" / "Roaming")) / tail / "Startup",
            _env("ProgramData", r"C:\ProgramData") / tail / "StartUp"]


def odd_places() -> list[Path]:
    """Places programs land without being installed: a program running or starting from here, with no
    valid signature, is worth a look."""
    public = _env("PUBLIC", r"C:\Users\Public")
    return [temp(), downloads(), desktop(), public, _env("APPDATA", str(home() / "AppData" / "Roaming")),
            _env("LOCALAPPDATA", str(home() / "AppData" / "Local")), _env("ProgramData", r"C:\ProgramData"),
            Path(os.environ.get("SystemDrive", "C:") + "\\$Recycle.Bin"), _env("SystemRoot", r"C:\Windows") / "Temp"]


def _inside(path: str, folder: Path) -> bool:
    try:
        p = os.path.normcase(os.path.abspath(path))
        f = os.path.normcase(os.path.abspath(str(folder)))
    except (OSError, ValueError):
        return False
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


def _installed_place(path: str) -> bool:
    """Program Files, Windows, and the places Store apps and per-user installs (AppData\\Local\\Programs) go."""
    places = [_env("ProgramFiles", r"C:\Program Files"), _env("ProgramFiles(x86)", r"C:\Program Files (x86)"),
              _env("ProgramW6432", r"C:\Program Files"), _env("SystemRoot", r"C:\Windows"),
              _env("LOCALAPPDATA", str(home() / "AppData" / "Local")) / "Programs",
              _env("LOCALAPPDATA", str(home() / "AppData" / "Local")) / "Microsoft" / "WindowsApps"]
    return any(_inside(path, p) for p in places) and not _inside(path, _env("SystemRoot", r"C:\Windows") / "Temp")


def where_it_is(path: str) -> str:
    """A short name for the odd place a file is in ("temp", "downloads", ...), or "" if none."""
    names = (("recycle", Path(os.environ.get("SystemDrive", "C:") + "\\$Recycle.Bin")),
             ("temp", temp()), ("temp", _env("SystemRoot", r"C:\Windows") / "Temp"),
             ("downloads", downloads()), ("desktop", desktop()), ("public", _env("PUBLIC", r"C:\Users\Public")),
             ("appdata", _env("APPDATA", str(home() / "AppData" / "Roaming"))),
             ("appdata", _env("LOCALAPPDATA", str(home() / "AppData" / "Local"))),
             ("programdata", _env("ProgramData", r"C:\ProgramData")))
    if _installed_place(path):
        return ""
    for name, folder in names:
        if _inside(path, folder):
            return name
    return ""


# ============================================================================= Microsoft Defender

@dataclass
class Defender:
    exe: str = ""                 # MpCmdRun.exe ("" if Defender isn't on this PC)
    service: bool = False         # its service is running
    enabled: bool = False         # it's the antivirus that protects this PC (not switched off or passive)
    realtime: bool = False        # it checks files as they're opened
    mode: str = ""                # "Normal", "Passive Mode", ...
    updated: float = 0            # when its list of threats was last updated
    version: str = ""
    last_quick: float = 0
    last_full: float = 0
    tamper: bool = False          # tamper protection on
    others: list = field(default_factory=list)   # other antivirus programs that are on
    skipped: list = field(default_factory=list)  # folders, programs and file kinds it's told to skip
    skipped_known: bool = False   # (only an administrator can read what it skips)
    read: bool = False            # its status could be read at all

    @property
    def can_scan(self) -> bool:
        return bool(self.exe) and (self.enabled or not self.read)

    @property
    def days_old(self) -> int | None:
        if not self.updated:
            return None
        return max(0, int((time.time() - self.updated) // 86400))


def find_mpcmdrun() -> str:
    """Defender's command-line tool: the newest copy Windows updated it to, or the one in Program Files."""
    data = os.environ.get("ProgramData", r"C:\ProgramData")
    found = []
    for p in glob.glob(os.path.join(data, "Microsoft", "Windows Defender", "Platform", "*", "MpCmdRun.exe")):
        version = os.path.basename(os.path.dirname(p)).split("-")[0]
        try:
            found.append((tuple(int(x) for x in version.split(".")), p))
        except ValueError:
            continue
    if found:
        return max(found)[1]
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", "")):
        p = os.path.join(root, "Windows Defender", "MpCmdRun.exe") if root else ""
        if p and os.path.isfile(p):
            return p
    return ""


def powershell(script: str, timeout: float = 60) -> str:
    env = dict(os.environ)
    env.pop("PSModulePath", None)  # (one set by PowerShell 7 makes Windows PowerShell load the wrong modules)
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-Command", "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + script],
                       capture_output=True, timeout=timeout, creationflags=NO_WINDOW, env=env)
    return r.stdout.decode("utf-8", "replace").strip()


STATUS_SCRIPT = r"""
function T($d) { if ($d -and $d.Year -gt 1601) { [int64](($d.ToUniversalTime() - [datetime]'1970-01-01').TotalSeconds) } else { 0 } }
$s = Get-MpComputerStatus -ErrorAction SilentlyContinue
$p = Get-MpPreference -ErrorAction SilentlyContinue
$av = @(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct -ErrorAction SilentlyContinue |
        ForEach-Object { @{ n = $_.displayName; s = [int64]$_.productState; p = "$($_.pathToSignedProductExe)" } })
$svc = Get-Service WinDefend -ErrorAction SilentlyContinue
$o = @{ av = $av; svc = if ($svc) { "$($svc.Status)" } else { "" } }
if ($s) {
  $o.status = @{ am = [bool]$s.AMServiceEnabled; on = [bool]$s.AntivirusEnabled; rt = [bool]$s.RealTimeProtectionEnabled;
    mode = "$($s.AMRunningMode)"; upd = T $s.AntivirusSignatureLastUpdated; ver = "$($s.AntivirusSignatureVersion)";
    quick = T $s.QuickScanEndTime; full = T $s.FullScanEndTime; tamper = [bool]$s.IsTamperProtected }
}
if ($p) { $o.skip = @{ path = @($p.ExclusionPath); proc = @($p.ExclusionProcess); ext = @($p.ExclusionExtension) } }
$o | ConvertTo-Json -Compress -Depth 4
"""


def product_on(state: int) -> bool:
    """Windows Security Center's note for an antivirus program: is it switched on."""
    return (state >> 12) & 0xF in (1, 3)


def is_defender(name: str, path: str = "") -> bool:
    return "windowsdefender:" in path.lower() or name.strip().lower() in (
        "windows defender", "microsoft defender antivirus", "microsoft defender")


def defender_status() -> Defender:
    """What Defender is doing on this PC (and which other antivirus programs are on)."""
    d = Defender(exe=find_mpcmdrun())
    try:
        raw = json.loads(powershell(STATUS_SCRIPT) or "{}")
    except (ValueError, OSError, subprocess.SubprocessError):
        log.warning("couldn't read Defender's status", exc_info=True)
        return d
    if not isinstance(raw, dict):
        return d
    d.service = raw.get("svc", "") == "Running"
    s = raw.get("status") or {}
    if s:
        d.read = True
        d.enabled = bool(s.get("am")) and bool(s.get("on")) and "passive" not in str(s.get("mode", "")).lower()
        d.realtime = bool(s.get("rt")) and d.enabled
        d.mode = str(s.get("mode", ""))
        d.updated = float(s.get("upd") or 0)
        d.version = str(s.get("ver", ""))
        d.last_quick = float(s.get("quick") or 0)
        d.last_full = float(s.get("full") or 0)
        d.tamper = bool(s.get("tamper"))
    avs = raw.get("av") or []
    if isinstance(avs, dict):
        avs = [avs]
    for a in avs:
        if isinstance(a, dict) and not is_defender(str(a.get("n", "")), str(a.get("p", ""))) \
                and product_on(int(a.get("s") or 0)):
            d.others.append(str(a.get("n", "")).strip())
    skip = raw.get("skip") or {}
    items, readable = [], bool(skip)
    for kind in ("path", "proc", "ext"):
        values = skip.get(kind) or []
        for v in [values] if isinstance(values, str) else values:
            if not isinstance(v, str) or not v.strip():
                continue
            if v.startswith("N/A"):   # "N/A: Must be an administrator to view exclusions"
                readable = False
            else:
                items.append((kind, v.strip()))
    d.skipped_known = readable
    d.skipped = items if readable else []
    return d


@dataclass
class Threat:
    name: str                 # Defender's name for it ("Trojan:Win32/Wacatac.B!ml")
    files: list = field(default_factory=list)   # where it was found
    status: str = ""          # "quarantined", "removed", "blocked", "allowed", "failed", "" (still there / unknown)
    severity: str = ""        # "low", "moderate", "high", "severe"
    category: str = ""

    @property
    def gone(self) -> bool:
        return self.status in ("quarantined", "removed", "blocked")


@dataclass
class ScanResult:
    kind: str
    paths: list = field(default_factory=list)
    started: float = 0
    finished: float = 0
    defender_ran: bool = False        # Defender scanned (and finished)
    defender_problem: str = ""        # "", "not-here", "off", "failed", "stopped"
    defender_code: int = 0
    threats: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    looked_at: int = 0                # files Corefold's own checks went through
    skipped: list = field(default_factory=list)   # what Defender skipped because it's told to
    stopped: bool = False


SCAN_TYPES = {QUICK: "1", FULL: "2", CUSTOM: "3"}
_FOUND = re.compile(r"found\s+(\d+)\s+threat", re.I)


def parse_scan_output(text: str) -> tuple[int, list[Threat]]:
    """MpCmdRun's answer: how many threats it found, and each one with its files."""
    count = 0
    for m in _FOUND.finditer(text):
        count += int(m.group(1))
    threats: list[Threat] = []
    current: Threat | None = None
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "threat" and value:
            current = Threat(value)
            threats.append(current)
        elif current is not None and key in ("file", "containerfile", "folder", "process", "regkey", "service",
                                             "startup", "webfile", "behavior"):
            place = line.split(":", 1)[1].strip()   # (a path has a ":" of its own: "file : C:\\x.exe")
            if place and place not in current.files:
                current.files.append(place)
    return max(count, len(threats)), threats


STATUS_IDS = {2: "removed", 3: "quarantined", 4: "removed", 5: "allowed", 6: "blocked", 102: "failed", 103: "failed",
              104: "failed", 107: "failed"}
SEVERITY_IDS = {1: "low", 2: "moderate", 4: "high", 5: "severe"}

DETECTIONS_SCRIPT = r"""
$since = [datetime]'1970-01-01'.AddSeconds(%d)
$names = @{}; $sev = @{}; $cat = @{}
Get-MpThreat -ErrorAction SilentlyContinue | ForEach-Object { $names["$($_.ThreatID)"] = $_.ThreatName; $sev["$($_.ThreatID)"] = [int]$_.SeverityID; $cat["$($_.ThreatID)"] = [int]$_.CategoryID }
@(Get-MpThreatDetection -ErrorAction SilentlyContinue | Where-Object { $_.InitialDetectionTime.ToUniversalTime() -ge $since -or $_.LastThreatStatusChangeTime.ToUniversalTime() -ge $since } |
  ForEach-Object { @{ id = "$($_.ThreatID)"; name = $names["$($_.ThreatID)"]; sev = $sev["$($_.ThreatID)"]; cat = $cat["$($_.ThreatID)"];
    st = [int]$_.ThreatStatusID; ok = [bool]$_.ActionSuccess; res = @($_.Resources) } }) | ConvertTo-Json -Compress -Depth 4
"""


def detections_since(since: float) -> list[dict]:
    """What Defender itself noted about the threats it found since then (what it did with each)."""
    try:
        out = powershell(DETECTIONS_SCRIPT % int(since), timeout=60)
        data = json.loads(out) if out else []
    except (ValueError, OSError, subprocess.SubprocessError):
        log.debug("couldn't read Defender's detections", exc_info=True)
        return []
    if isinstance(data, dict):
        data = [data]
    return [d for d in data if isinstance(d, dict)]


def _resource_path(res: str) -> str:
    """ "file:_C:\\x\\y.exe" or "containerfile:_C:\\a.zip" -> the path."""
    _, _, rest = res.partition(":_")
    return rest or res


def _same_file(a: str, b: str) -> bool:
    def norm(x: str) -> str:
        return os.path.normcase(x.split("->")[0].strip())
    return norm(a) == norm(b)


def add_detections(threats: list[Threat], detections: list[dict], add_unknown: bool = True):
    """Fills in what Defender did with each threat, from its own notes. A note belongs to a threat when the
    name and a file match (the same threat found earlier somewhere else is another note). add_unknown also
    adds threats only its notes mention (a quick or full scan doesn't always list them in its answer)."""
    for d in detections:
        name = str(d.get("name") or "")
        status = STATUS_IDS.get(int(d.get("st") or 0), "")
        files = [_resource_path(str(r)) for r in (d.get("res") or []) if r]
        t = next((t for t in threats if t.name.lower() == name.lower() and
                  (not t.files or any(_same_file(a, b) for a in t.files for b in files))), None)
        if t is None:
            if not name or not add_unknown:
                continue
            t = Threat(name, [])
            threats.append(t)
        for f in files:
            if not any(_same_file(f, x) for x in t.files):
                t.files.append(f)
        if status:
            t.status = status
        t.severity = SEVERITY_IDS.get(int(d.get("sev") or 0), t.severity)


class Stopped(Exception):
    pass


_FAILED = re.compile(r"failed with hr\s*=\s*(0x[0-9a-f]+)", re.I)
_SKIPPED = re.compile(r"^Scanning (.+) was skipped\.\s*$", re.I | re.M)


def run_defender(kind: str, paths: list[str], cancel: threading.Event, exe: str = "",
                 report_only: bool = False) -> tuple[int, str]:
    """One Defender scan (a custom scan does each path in turn). Returns (its answer: 0 nothing found, 2 found
    something, anything else Windows' error code; everything it said). report_only (custom scans only) finds
    without acting, and also looks inside archives and at files Defender is told to skip. Stopping kills it
    and tells Defender to stop scanning too."""
    exe = exe or find_mpcmdrun()
    targets = [None] if kind != CUSTOM else paths
    code, said = 0, []
    for target in targets:
        args = [exe, "-Scan", "-ScanType", SCAN_TYPES[kind]]
        if target:
            args += ["-File", target]
            if report_only:
                args.append("-DisableRemediation")
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        out: list[bytes] = []
        reader = threading.Thread(target=lambda p=proc, o=out: o.append(p.stdout.read()), daemon=True)
        reader.start()
        while proc.poll() is None:
            if cancel.is_set():
                proc.kill()
                if kind != CUSTOM:
                    try:
                        subprocess.run([exe, "-Scan", "-Cancel"], capture_output=True, timeout=30,
                                       creationflags=NO_WINDOW)
                    except (OSError, subprocess.SubprocessError):
                        pass
                raise Stopped()
            time.sleep(0.2)
        reader.join(5)
        text = b"".join(out).decode("utf-8", "replace")
        said.append(text)
        this = proc.returncode & 0xFFFFFFFF
        failed = _FAILED.search(text)
        if failed:   # (it answers 2 for "couldn't scan" too: the words say which)
            this = int(failed.group(1), 16)
        if this == 2 or (this not in (0, 2) and code in (0,)):
            code = this
    return code, "\n".join(said)


def skipped_by_defender(text: str) -> list[str]:
    """What a custom scan skipped because Defender is told to skip it ("Scanning X was skipped.")."""
    return [m.group(1).strip() for m in _SKIPPED.finditer(text)]


# ----------------------------------------------------------------------------- Defender's quarantine

@dataclass
class Quarantined:
    threat: str
    path: str
    when: str = ""


def parse_quarantine(text: str) -> list[Quarantined]:
    """ "MpCmdRun -Restore -ListAll": ThreatName = X, then one line per file ("file:C:\\... quarantined at ...")."""
    items: list[Quarantined] = []
    threat = ""
    for line in text.splitlines():
        s = line.strip()
        if s.lower().startswith("threatname"):
            threat = s.split("=", 1)[1].strip() if "=" in s else ""
            continue
        if not threat or ":" not in s:
            continue
        kind, _, rest = s.partition(":")
        if kind.strip().lower() not in ("file", "containerfile"):
            continue
        when = ""
        m = re.search(r"\s+quarantined at\s+(.+)$", rest, re.I)
        if m:
            when = m.group(1).strip()
            rest = rest[:m.start()]
        items.append(Quarantined(threat, rest.strip(), when))
    return items


def _mpcmdrun(args: list[str], admin: bool, timeout: float = 120) -> tuple[int, str]:
    """Runs MpCmdRun; as administrator (Windows asks first) when admin is True, through a temporary file
    for its answer. (None, "") style failures come back as (-1, "")."""
    exe = find_mpcmdrun()
    if not exe:
        return -1, ""
    if not admin:
        r = subprocess.run([exe, *args], capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        return r.returncode & 0xFFFFFFFF, r.stdout.decode("utf-8", "replace")
    import tempfile
    from .uninstall import shell_run, wait_process
    fd, out = tempfile.mkstemp(prefix="corefold-defender-", suffix=".txt")
    os.close(fd)
    try:
        quoted = " ".join(f'"{a}"' if " " in a or not a else a for a in args)
        cmd = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
        handle = shell_run(cmd, f'/c ""{exe}" {quoted} > "{out}" 2>&1"', admin=True, show=0)
        if handle is None:
            return -1, ""   # (the person said no)
        code = wait_process(handle, timeout_s=timeout)
        text = Path(out).read_bytes().decode("utf-8", "replace")
        return (code if code is not None else -1) & 0xFFFFFFFF, text
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


def needs_admin(text: str) -> bool:
    t = text.lower()
    return "elevat" in t or "administrator" in t or "access is denied" in t or "0x80070005" in t


def quarantine(ask_admin: bool = True) -> list[Quarantined] | None:
    """What Defender keeps in quarantine. None if it couldn't be read (or the person said no to Windows'
    prompt)."""
    code, text = _mpcmdrun(["-Restore", "-ListAll"], admin=False)
    if needs_admin(text) and ask_admin:
        code, text = _mpcmdrun(["-Restore", "-ListAll"], admin=True)
    if code == 0xFFFFFFFF or (code not in (0, 2) and not text.strip()):
        return None
    return parse_quarantine(text)


def restore(item: Quarantined) -> bool:
    """Asks Defender to put a file back from quarantine (Windows asks for an administrator first)."""
    # ("-Name" and "-FilePath" can't be given together: a file is put back by its path)
    args = ["-Restore", "-FilePath", item.path] if item.path else ["-Restore", "-Name", item.threat]
    code, text = _mpcmdrun(args, admin=False)
    if code != 0 and needs_admin(text):
        code, text = _mpcmdrun(args, admin=True)
    log.info("restoring %s from quarantine: %#x %s", item.path, code, text.strip()[-300:])
    return code == 0


# ============================================================================= Corefold's own checks

@dataclass
class Finding:
    kind: str              # "disguised", "flipped", "broken", "untrusted", "startup", "startup-script",
                           # "startup-hidden", "running", "hosts", "skipped"
    path: str              # the file (or for "hosts", the hosts file; for "skipped", what's skipped)
    level: str = "warn"    # "warn" (worth a look) or "info" (good to know)
    detail: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.kind}|{os.path.normcase(self.path)}|{self.detail.get('what', '')}"


def disguised(name: str) -> str:
    """The kind of file a program's name pretends to be ("invoice.pdf.exe" -> ".pdf"), or ""."""
    lower = name.lower()
    stem, ext = os.path.splitext(lower)
    if ext not in RUNNABLE or ext == ".lnk":
        return ""
    stem = stem.rstrip(" ._-\u3000\u00a0")
    inner = os.path.splitext(stem)[1]
    if inner in LOOKS_LIKE:
        return inner
    return ""


def flipped(name: str) -> bool:
    return any(ch in FLIPPERS for ch in name)


def shown_name(name: str) -> str:
    """How a name with a flipping character really reads (the characters taken out, so it can be shown)."""
    return "".join(ch for ch in name if ch not in FLIPPERS)


class Checks:
    """Corefold's own checks, collected as they're made (so the page can show them while Defender works)."""

    def __init__(self, cancel: threading.Event | None = None, on_finding=None):
        self.cancel = cancel or threading.Event()
        self.on_finding = on_finding
        self.findings: list[Finding] = []
        self.keys: set[str] = set()
        self.looked_at = 0
        self.signatures = 0
        self._sig_cache: dict[str, trust.Signature] = {}

    def add(self, f: Finding):
        if f.key in self.keys:
            return
        self.keys.add(f.key)
        self.findings.append(f)
        if self.on_finding:
            self.on_finding(f)

    def signature(self, path: str) -> trust.Signature:
        key = os.path.normcase(path)
        if key not in self._sig_cache:
            self.signatures += 1
            self._sig_cache[key] = trust.signature(path)
        return self._sig_cache[key]

    # ---- one file

    def file(self, path: str, deep: bool = True, origin: bool = False):
        """Name tricks for any file; for programs and scripts, the signature (when deep)."""
        self.looked_at += 1
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1].lower()
        if flipped(name):
            self.add(Finding("flipped", path, "warn", {"shown": shown_name(name)}))
        pretends = disguised(name)
        if pretends:
            self.add(Finding("disguised", path, "warn", {"looks": pretends, "is": ext}))
        if deep and ext in trust.SIGNABLE and ext in PROGRAMS | {".ps1"} and self.signatures < MAX_SIGNATURES:
            sig = self.signature(path)
            detail = {}
            if origin:
                o = trust.downloaded_from(path)
                if o is not None and o.host:
                    detail["from"] = o.host
            if sig.state == trust.BROKEN:
                self.add(Finding("broken", path, "warn", {"signer": sig.signer, **detail}))
            elif sig.state == trust.UNTRUSTED:
                self.add(Finding("untrusted", path, "warn", {"signer": sig.signer, **detail}))

    def walk(self, folder: Path, depth: int, deep: bool = True, origin: bool = False, limit: int = MAX_FILES):
        """Every file in a folder (down to depth folders in), checked with file()."""
        stack = [(str(folder), 0)]
        start = self.looked_at
        while stack:
            if self.cancel.is_set() or self.looked_at - start >= limit:
                return
            here, level = stack.pop()
            try:
                with os.scandir(here) as it:
                    for entry in it:
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if level < depth:
                                    stack.append((entry.path, level + 1))
                                continue
                        except OSError:
                            continue
                        self.file(entry.path, deep, origin)
            except OSError:
                continue

    # ---- what starts with Windows

    def startup(self):
        """Programs and scripts that start with Windows (the Run keys and the Startup folders): a script, a
        hidden PowerShell command, something from the internet, or a program from an odd place with no valid
        signature."""
        for where, name, command in startup_commands():
            if self.cancel.is_set():
                return
            self.command(where, name, command)

    def command(self, where: str, name: str, command: str):
        low = command.lower()
        exe, args = split_command(command)
        exe_low = os.path.basename(exe).lower()
        detail = {"name": name, "command": command, "where": where}
        target = exe
        if exe_low in ("powershell.exe", "powershell", "pwsh.exe", "pwsh") and re.search(
                r"(^|\s)[-/](e|en|enc|enco|encod|encode|encoded|encodedc|encodedcommand|w\s+hidden|"
                r"windowstyle\s+hidden|nop\b.*-w\s+h)", args.lower()):
            self.add(Finding("startup-hidden", exe, "warn", detail))
            return
        if re.search(r"https?://", low) or exe_low in ("mshta.exe", "mshta") or \
                (exe_low in ("regsvr32.exe", "regsvr32") and "/i:" in low and "scrobj" in low):
            self.add(Finding("startup-hidden", exe or command, "warn", detail))
            return
        if exe_low in ("wscript.exe", "cscript.exe", "wscript", "cscript", "cmd.exe", "cmd"):
            script = _first_path(args)
            if script:
                target = script
        ext = os.path.splitext(target)[1].lower()
        if ext in SCRIPTS:
            place = where_it_is(target)
            self.add(Finding("startup-script", target, "warn" if place in ("temp", "downloads", "recycle", "public")
                             else "info", {**detail, "place": place}))
            return
        if not target or not os.path.isabs(target) or not os.path.isfile(target):
            return
        self.file(target, deep=False)
        place = where_it_is(target)
        if not place:
            return
        sig = self.signature(target)
        if sig.state in (trust.UNSIGNED, trust.BROKEN, trust.UNTRUSTED, trust.EXPIRED):
            self.add(Finding("startup", target, "warn" if place in ("temp", "recycle", "downloads", "public")
                             or sig.state in (trust.BROKEN, trust.UNTRUSTED) else "info",
                             {**detail, "place": place, "sig": sig.state}))

    # ---- what's running now

    def running(self):
        """Programs running right now from an odd place (Temp, Downloads, the Recycle Bin...) with no valid
        signature, or with a disguised name."""
        for path in running_programs():
            if self.cancel.is_set():
                return
            name = os.path.basename(path)
            if disguised(name) or flipped(name):
                self.file(path, deep=False)
                continue
            place = where_it_is(path)
            if place not in ("temp", "downloads", "recycle", "public", "desktop", "appdata", "programdata"):
                continue
            sig = self.signature(path)
            if sig.state in (trust.UNSIGNED, trust.BROKEN, trust.UNTRUSTED):
                level = "warn" if place in ("temp", "recycle", "public") or sig.state != trust.UNSIGNED else "info"
                self.add(Finding("running", path, level, {"place": place, "sig": sig.state}))

    # ---- the hosts file

    def hosts(self):
        path = hosts_path()
        for site, address in hosts_redirects(path):
            self.add(Finding("hosts", str(path), "warn", {"site": site, "to": address, "what": site}))

    # ---- what Defender is told to skip

    def skipped(self, d: Defender):
        for kind, value in d.skipped:
            level = "warn" if broad_exclusion(kind, value) else "info"
            self.add(Finding("skipped", value, level, {"what": kind}))


def broad_exclusion(kind: str, value: str) -> bool:
    """An exclusion that leaves a lot unchecked: a whole drive, a user folder, Temp, Downloads, or a kind
    of file that runs."""
    if kind == "ext":
        return ("." + value.lstrip(".*").lower()) in RUNNABLE
    if kind == "proc":
        return False
    v = os.path.normcase(os.path.expandvars(value.rstrip("\\/*")))
    if re.fullmatch(r"[a-z]:", v) or v in ("", "\\"):
        return True
    wide = [home(), temp(), downloads(), desktop(), _env("SystemRoot", r"C:\Windows"),
            _env("APPDATA", str(home() / "AppData" / "Roaming")), _env("LOCALAPPDATA", str(home() / "AppData" / "Local")),
            _env("ProgramData", r"C:\ProgramData"), Path(os.environ.get("SystemDrive", "C:") + "\\Users")]
    return any(v == os.path.normcase(str(p)) for p in wide)


def hosts_path() -> Path:
    return _env("SystemRoot", r"C:\Windows") / "System32" / "drivers" / "etc" / "hosts"


def hosts_redirects(path: Path) -> list[tuple[str, str]]:
    """Lines in the hosts file that send a well-known site somewhere else."""
    try:
        text = path.read_bytes().decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        parts = line.split()
        if len(parts) < 2:
            continue
        address = parts[0]
        for host in parts[1:]:
            h = host.lower().rstrip(".")
            if h in LOCAL_HOSTS:
                continue
            if any(h == s or h.endswith("." + s) for s in GUARDED_SITES):
                out.append((h, address))
    return out


def split_command(command: str) -> tuple[str, str]:
    """ "\"C:\\x y\\a.exe\" --tray" -> ("C:\\x y\\a.exe", "--tray"), with %VARS% filled in and programs Windows
    finds by name ("rundll32.exe") given their place."""
    from .uninstall import split_command as split
    exe, args = split(os.path.expandvars(command.strip()))
    exe = exe.strip().strip('"')
    if exe and not os.path.isabs(exe):
        system = _env("SystemRoot", r"C:\Windows")
        for folder in (system / "System32", system):
            candidate = folder / exe
            if not candidate.suffix:
                candidate = candidate.with_suffix(".exe")
            if candidate.is_file():
                return str(candidate), args
    return exe, args


def _first_path(args: str) -> str:
    """The first file named in a program's arguments ("//B \"C:\\x\\run.vbs\"" -> "C:\\x\\run.vbs")."""
    for m in re.finditer(r'"([^"]+)"|(\S+)', args):
        word = (m.group(1) or m.group(2) or "").strip()
        if word.startswith(("/", "-")) and not os.path.isabs(word):
            continue
        word = os.path.expandvars(word)
        if os.path.splitext(word)[1]:
            return word
    return ""


def startup_commands() -> list[tuple[str, str, str]]:
    """(where, name, command) for everything in the Run keys and Startup folders (switched off ones too:
    something switched off can still be switched on again)."""
    out = []
    try:
        import winreg
    except ImportError:
        return out
    run = r"Software\Microsoft\Windows\CurrentVersion\Run"
    places = [("HKCU", winreg.HKEY_CURRENT_USER, run, 0),
              ("HKCU", winreg.HKEY_CURRENT_USER, run + "Once", 0),
              ("HKLM", winreg.HKEY_LOCAL_MACHINE, run, 0x0100),
              ("HKLM", winreg.HKEY_LOCAL_MACHINE, run, 0x0200),
              ("HKLM", winreg.HKEY_LOCAL_MACHINE, run + "Once", 0x0100)]
    for label, hive, key, view in places:
        try:
            with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as k:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    i += 1
                    if name and isinstance(value, str) and value.strip():
                        out.append((label, name, value))
        except OSError:
            pass
    for folder in startup_folders():
        try:
            files = [p for p in folder.iterdir() if p.is_file() and p.name.lower() != "desktop.ini"]
        except OSError:
            continue
        for p in files:
            command = f'"{p}"'
            if p.suffix.lower() == ".lnk":
                from .uninstall import lnk_target
                try:
                    target = lnk_target(p)
                except Exception:
                    target = ""
                if target:
                    command = f'"{target}"'
            out.append(("Folder", p.name, command))
    seen, unique = set(), []
    for item in out:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def running_programs() -> list[str]:
    """The program file of every process this person can see."""
    if os.name != "nt":
        return []
    import ctypes
    from .uninstall import processes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p,
                                               ctypes.POINTER(ctypes.c_uint32)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    paths = set()
    for pid, _parent, _name in processes():
        if pid <= 4:
            continue
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_uint32(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                paths.add(buf.value)
        finally:
            k32.CloseHandle(h)
    return sorted(paths)


# ============================================================================= a whole scan

def corefold_checks(kind: str, paths: list[str], checks: Checks, defender: Defender | None):
    """Corefold's own checks for one kind of scan."""
    if kind == CUSTOM:
        for p in paths:
            if checks.cancel.is_set():
                return
            if os.path.isdir(p):
                checks.walk(Path(p), depth=64, origin=True)
            elif os.path.isfile(p):
                checks.file(p, origin=True)
        return
    steps = [checks.startup, checks.running, checks.hosts]
    for step in steps:
        if checks.cancel.is_set():
            return
        try:
            step()
        except Exception:
            log.warning("a check failed", exc_info=True)
    if defender is not None:
        checks.skipped(defender)
    folders = [(downloads(), 4), (desktop(), 2), (_env("PUBLIC", r"C:\Users\Public"), 3), (temp(), 2)]
    folders += [(f, 1) for f in startup_folders()]
    if kind == FULL:
        folders = [(home(), 64)] + folders
    for folder, depth in folders:
        if checks.cancel.is_set():
            return
        checks.walk(folder, depth, deep=True, origin=folder in (downloads(), desktop()))


def scan(kind: str, paths: list[str] | None = None, cancel: threading.Event | None = None, on_finding=None,
         defender: Defender | None = None, on_stage=None) -> ScanResult:
    """A whole scan: Defender and Corefold's own checks side by side. on_finding(Finding) and on_stage(str)
    are called (on this thread or the checks' thread) as things are found."""
    cancel = cancel or threading.Event()
    # (full, long names: Defender's notes use them, and "C:\\Users\\RUNNER~1" wouldn't match)
    paths = [os.path.realpath(str(p)) if os.path.exists(str(p)) else str(p) for p in (paths or [])]
    result = ScanResult(kind, paths, started=time.time())
    checks = Checks(cancel, on_finding)
    side = threading.Thread(target=lambda: _safe(corefold_checks, kind, paths, checks, defender), daemon=True,
                            name="corefold-scan-checks")
    side.start()
    exe = defender.exe if defender is not None and defender.exe else find_mpcmdrun()
    if not exe:
        result.defender_problem = "not-here"
    elif defender is not None and defender.read and not defender.enabled:
        result.defender_problem = "off"
    else:
        if on_stage:
            on_stage("defender")
        try:
            if kind == CUSTOM:
                code, threats, said = _custom_scan(paths, cancel, exe, result)
            else:
                code, said = run_defender(kind, paths, cancel, exe)
                count, threats = parse_scan_output(said)
                if code == 2 or count:
                    time.sleep(1.0)   # (Defender notes what it did a moment after it answers)
                    add_detections(threats, detections_since(result.started - 2))
            result.defender_code = code
            if code in (0, 2):
                result.defender_ran = True
                result.threats = [asdict(t) for t in threats]
            else:
                result.defender_problem = "failed"
                log.warning("Defender's scan failed: %#x %s", code, said.strip()[-500:])
        except Stopped:
            result.stopped = True
            result.defender_problem = "stopped"
        except Exception:
            log.warning("Defender's scan failed", exc_info=True)
            result.defender_problem = "failed"
    if on_stage:
        on_stage("checks")
    while side.is_alive():
        side.join(0.2)
    result.stopped = result.stopped or cancel.is_set()
    ignored = load_ignored()
    result.findings = [asdict(f) for f in checks.findings if f.key not in ignored]
    result.looked_at = checks.looked_at
    result.finished = time.time()
    if not result.stopped:
        save_last(result)
    return result


def _custom_scan(paths: list[str], cancel: threading.Event, exe: str, result: ScanResult):
    """Files, folders or drives someone picked. First Defender looks without acting, which also looks inside
    archives and at files it's told to skip (malware sometimes tells it to skip itself); if it finds
    anything, it scans those places again the usual way, so it deals with them (quarantine) the way it
    always does. A threat it still leaves where it is (because it's told to skip it) is shown as still there."""
    code, said = run_defender(CUSTOM, paths, cancel, exe, report_only=True)
    count, threats = parse_scan_output(said)
    if code != 2 or not threats:
        return code, threats, said
    places = [p for p in paths if any(_inside(f.split("->")[0].strip(), Path(p)) for t in threats for f in t.files)]
    act_code, act_said = run_defender(CUSTOM, places or paths, cancel, exe)
    result.skipped = skipped_by_defender(act_said)
    time.sleep(1.0)
    add_detections(threats, detections_since(result.started - 2), add_unknown=False)
    for t in threats:
        if t.status:
            continue
        files = [f.split("->")[0].strip() for f in t.files]
        if files and not any(os.path.exists(f) for f in files):
            t.status = "removed"   # (gone from where it was: Defender took it, though its notes didn't say how)
    return code, threats, said + "\n" + act_said


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception:
        log.warning("Corefold's checks failed", exc_info=True)


# ----------------------------------------------------------------------------- remembered between runs

def _config(name: str) -> Path:
    from .settings import CONFIG_DIR
    return CONFIG_DIR / name


def _write(path: Path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        log.debug("couldn't save %s", path, exc_info=True)


def save_last(result: ScanResult):
    _write(_config(LAST_FILE), {"kind": result.kind, "paths": result.paths[:5], "when": result.finished,
                                "threats": len(result.threats), "findings": len(result.findings),
                                "defender": result.defender_ran})


def load_last() -> dict:
    try:
        data = json.loads(_config(LAST_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_ignored() -> set[str]:
    try:
        data = json.loads(_config(IGNORED_FILE).read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except (OSError, ValueError):
        return set()


def ignore(finding_key: str):
    keys = load_ignored()
    keys.add(finding_key)
    _write(_config(IGNORED_FILE), sorted(keys))


def finding_key(d: dict) -> str:
    return Finding(d["kind"], d["path"], d.get("level", "warn"), d.get("detail", {})).key
