"""Updates without Windows' "allow this app to make changes?" each time, on a real Windows PC: installs the build in
dist\\release and checks the update task it adds (installer\\silent-update.ps1):

- only administrators and Windows can change the script, its folder and the task's own folder (_update);
- the task runs as SYSTEM, runs exactly that script with -Mode Update, and anyone signed in may start it but
  nobody else may change it (its security descriptor);
- started, it asks GitHub itself and says so in _update\\status.txt; it refuses anything not newer than what's
  installed;
- when the newest release on GitHub has a Rakiza Core installer: it downloads it, checks its SHA-256, installs it
  once Rakiza Core has closed, and starts Rakiza Core again as the person (not as SYSTEM, in their own session);
- uninstalling removes the task.

Only for a throwaway Windows PC (GitHub's): it installs and removes Rakiza Core and changes what Windows says is
installed.
    python scripts/silentupdatetest.py dist\\release out\\silent
Prints one line per check, then "silentupdatetest: N problem(s)".
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

if (os.environ.get("CI") != "true" and os.environ.get("COREFOLD_THROWAWAY_PC") != "1") or os.name != "nt":
    sys.exit("silentupdatetest: only on a throwaway Windows PC (GitHub Actions): it installs and removes Rakiza Core")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

RELEASE = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
SETUP = sorted(RELEASE.glob("RakizaCore-Setup-*.exe"))[-1]
APP_DIR = Path(os.environ.get("ProgramW6432") or os.environ["ProgramFiles"]) / "Rakiza Core"
EXE = APP_DIR / "RakizaCore.exe"
SCRIPT = APP_DIR / "silent-update.ps1"
WORK = APP_DIR / "_update"
STATUS = WORK / "status.txt"
UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{D08578D1-4B3E-404B-B56C-3FDF83BAFAE2}_is1"
REPO = os.environ.get("GITHUB_REPOSITORY", "SayedLabs/corefold")
POWERSHELL = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0"
                 / "powershell.exe")

problems = []


def check(ok, what):
    print(("ok      " if ok else "PROBLEM ") + what, flush=True)
    if not ok:
        problems.append(what)
    return ok


def note(what):
    print("        " + what, flush=True)


def ps(script: str, **env) -> str:
    r = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120, env={**os.environ, **env})
    if r.stderr.strip():
        note("powershell: " + r.stderr.strip()[:500])
    return r.stdout.strip()


def set_installed_version(v: str):
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, v)


def installed_version() -> str:
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
        return winreg.QueryValueEx(k, "DisplayVersion")[0]


def stop_app():
    ps("Get-Process RakizaCore -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep 1")


def copies() -> list[dict]:
    out = ps("@(Get-Process RakizaCore -IncludeUserName -ErrorAction SilentlyContinue | "
             "Select-Object Id, SessionId, UserName, Path) | ConvertTo-Json -Compress")
    try:
        got = json.loads(out) if out else []
    except ValueError:
        return []
    return got if isinstance(got, list) else [got]


def status() -> str:
    try:
        return STATUS.read_text("utf-8-sig").strip()
    except OSError:
        return ""


def run_task() -> float:
    t0 = time.time()
    r = subprocess.run(["schtasks.exe", "/Run", "/TN", r"\Rakiza Core\Update"], capture_output=True, text=True)
    check(r.returncode == 0, f"Task Scheduler starts the update task ({r.stdout.strip() or r.stderr.strip()})")
    return t0


def wait_status(t0: float, until, seconds: float) -> str:
    end = time.time() + seconds
    said = ""
    while time.time() < end:
        try:
            fresh = STATUS.stat().st_mtime >= t0 - 1
        except OSError:
            fresh = False
        now = status() if fresh else ""
        if now != said:
            said = now
            note(f"status: {said}")
        if said and until(said):
            return said
        time.sleep(0.5)
    return said


def save_logs(name: str):
    for f in ("update.log", "setup.log"):
        p = WORK / f
        if p.exists():
            (OUT / f"{name}-{f}").write_bytes(p.read_bytes())


# ---- install
r = subprocess.run([str(SETUP), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TASKS=", f"/LOG={OUT / 'install.log'}"],
                   timeout=600)
check(r.returncode == 0, f"installs ({SETUP.name})")
time.sleep(3)
stop_app()
check(SCRIPT.is_file(), "the update script is installed next to Rakiza Core")

# ---- (4) who may change the script, its folder and the task's folder
acl_check = r"""
$admins = @('S-1-5-18', 'S-1-5-32-544', 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
$write = [Security.AccessControl.FileSystemRights]'Write, Modify, FullControl, Delete, ChangePermissions, TakeOwnership, AppendData, CreateFiles, CreateDirectories, WriteData, DeleteSubdirectoriesAndFiles'
$out = @()
foreach ($p in $env:CF_PATHS.Split('|')) {
    if (-not (Test-Path -LiteralPath $p)) { continue }
    $acl = Get-Acl -LiteralPath $p
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($admins -notcontains $owner) { $out += "$p owner $owner" }
    foreach ($a in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($a.AccessControlType -ne 'Allow') { continue }
        $sid = $a.IdentityReference.Value
        if ($admins -contains $sid) { continue }
        # CREATOR OWNER only applies to what someone creates inside (they can't create anything: see the rest)
        if ($sid -eq 'S-1-3-0') { continue }
        if ([int]($a.FileSystemRights -band $write) -ne 0) { $out += "$p $sid $($a.FileSystemRights)" }
    }
}
$out -join "`n"
"""

# ---- (5) the task: SYSTEM, exactly this script, and nobody else may change it
info = ps(r"""
$s = New-Object -ComObject Schedule.Service; $s.Connect()
$t = $s.GetFolder('\Rakiza Core').GetTask('Update')
$d = $t.Definition
$a = @($d.Actions)[0]
[pscustomobject]@{ user = $d.Principal.UserId; level = $d.Principal.RunLevel; path = $a.Path; args = $a.Arguments;
                   sddl = $t.GetSecurityDescriptor(4); enabled = $t.Enabled } | ConvertTo-Json -Compress
""")
try:
    task = json.loads(info)
except ValueError:
    task = {}
check(bool(task), "the installer added the update task")
if task:
    note(json.dumps(task))
    user = str(task.get("user", "")).upper()
    check(user in ("SYSTEM", "S-1-5-18", "NT AUTHORITY\\SYSTEM"), f"it runs as SYSTEM ({task.get('user')})")
    check(str(task.get("path", "")).lower().endswith(r"\windowspowershell\v1.0\powershell.exe"),
          "it runs Windows' own PowerShell")
    args = str(task.get("args", ""))
    check(f'-File "{SCRIPT}"'.lower() in args.lower() and "-Mode Update" in args and "-NoProfile" in args,
          "…with exactly the installed script, in update mode, without any profile")
    import re
    sddl = str(task.get("sddl", ""))
    aces = re.findall(r"\(([^)]*)\)", sddl.split("S:")[0])
    loose = []
    for ace in aces:
        parts = ace.split(";")
        if len(parts) < 6 or parts[0] != "A":
            continue
        rights, who = parts[2], parts[5]
        if who in ("SY", "BA", "S-1-5-18", "S-1-5-32-544"):
            continue
        # anyone else: reading and starting it only (GR, GX, or the same as file rights FR/FX)
        if not re.fullmatch(r"(GR|GX|FR|FX)+", rights) and rights not in ("0x1200a9", "0x120089", "0x1201bf"):
            loose.append(ace)
    check(sddl.startswith("D:P") or "D:P" in sddl, "the task's permissions aren't inherited from anywhere")
    check(not loose, f"nobody but administrators and Windows may change the task ({sddl})")
    check(any(a.split(";")[5] in ("AU", "S-1-5-11") for a in aces if len(a.split(";")) >= 6),
          "anyone signed in may start it")

# ---- it refuses anything not newer than what's installed
set_installed_version("999.0.0")
t0 = run_task()
said = wait_status(t0, lambda s: s.startswith(("failed", "done")), 120)
save_logs("1-not-newer")
check(said.startswith("failed nothing newer"), f"it refuses a release that isn't newer than what's installed ({said})")
check(not any(WORK.glob("*.exe")), "…and leaves no installer behind")
log = (WORK / "update.log").read_text("utf-8", "replace") if (WORK / "update.log").exists() else ""
check("running as SYSTEM" in log or "as NT AUTHORITY\\SYSTEM" in log, "it ran as SYSTEM")
bad = ps(acl_check, CF_PATHS="|".join(str(p) for p in (APP_DIR, SCRIPT, WORK, STATUS)))
check(WORK.is_dir() and not bad, "only administrators and Windows can change Rakiza Core's folder, the update "
      "script, _update and what the task says in it" + (f": {bad}" if bad else ""))

# ---- a real update from GitHub (when the newest release has a Rakiza Core installer)
latest = {}
try:
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "silentupdatetest"})
    if os.environ.get("GH_TOKEN"):
        req.add_header("Authorization", "Bearer " + os.environ["GH_TOKEN"])
    with urllib.request.urlopen(req, timeout=30) as resp:
        latest = json.loads(resp.read().decode("utf-8"))
except Exception as e:  # noqa: BLE001
    note(f"couldn't ask GitHub for the newest release: {e}")
tag = str(latest.get("tag_name", "")).lstrip("vV")
has_setup = any(str(a.get("name", "")) == f"RakizaCore-Setup-{tag}.exe" for a in latest.get("assets") or [])
if not has_setup:
    note(f"the newest release ({tag or '?'}) has no RakizaCore-Setup installer yet: the real update isn't tried")
else:
    set_installed_version("0.0.1")
    subprocess.Popen([str(EXE), "--background"])
    time.sleep(8)
    before = copies()
    check(any(c.get("SessionId", 0) > 0 for c in before), "Rakiza Core runs for this person before the update")
    t0 = run_task()
    said = wait_status(t0, lambda s: s in ("ready",) or s.startswith(("failed", "done")), 600)
    check(said == "ready", f"it downloaded and checked {tag}, and waits for Rakiza Core to close ({said})")
    if said == "ready":
        time.sleep(2)
        stop_app()  # (what Rakiza Core does by itself when it reads "ready" and it asked for the update)
        said = wait_status(t0, lambda s: s.startswith(("failed", "done")), 600)
    save_logs("2-update")
    check(said == "done 0", f"it installed it ({said})")
    check(installed_version() == tag, f"Windows says {tag} is installed ({installed_version()})")
    time.sleep(6)
    after = copies()
    note(f"running after: {after}")
    check(any(c.get("SessionId", 0) > 0 for c in after), "Rakiza Core runs again in this person's session")
    check(not any(c.get("SessionId", 0) == 0 for c in after), "…and never as SYSTEM")
    me = os.environ.get("USERNAME", "").lower()
    check(all(str(c.get("UserName", "")).lower().endswith("\\" + me) for c in after if c.get("SessionId", 0) > 0),
          f"…as this person ({me})")
    log = (WORK / "update.log").read_text("utf-8", "replace") if (WORK / "update.log").exists() else ""
    check("verified RakizaCore-Setup-" in log, "it checked the installer's SHA-256 before running it")
    check(not any(WORK.glob("*.exe")), "…and deleted the installer afterwards")

# ---- (6) uninstalling removes the task
stop_app()
unins = sorted(APP_DIR.glob("unins*.exe"))
if check(bool(unins), "the uninstaller is there"):
    subprocess.run([str(unins[-1]), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=300)
    time.sleep(5)
    left = ps(r"""
$s = New-Object -ComObject Schedule.Service; $s.Connect()
try { $f = $s.GetFolder('\Rakiza Core'); 'still: ' + (@($f.GetTasks(1)) | ForEach-Object { $_.Name }) -join ', ' } catch { 'gone' }
""")
    check(left == "gone", f"uninstalling removes the update task ({left})")
    check(not WORK.exists(), "…and its folder")

print(f"silentupdatetest: {len(problems)} problem(s)")
sys.exit(1 if problems else 0)
