"""Updating Corefold on a real Windows PC keeps everything where the person had it: installs the real older
releases (downloaded from GitHub by the workflow), changes things the way a person would, updates to the
build in dist\\release the way Corefold's own updater does, and checks what's left:

- Corefold installed for the whole PC (Program Files): a copy installed the old way, for one user alone, is moved
  over and removed (its desktop icon and taskbar pin kept in place, pointing at the new one); one picked with
  /DIR the old way moves too, and its folder is left as it was (only Corefold's usual folder is deleted)
- an update of a whole-PC install stays in its folder (also one picked with /DIR), Corefold running again
- the Start menu shortcut with Corefold's app id (so a taskbar pin made from it and the running Corefold are
  one button), a desktop icon left alone (not written again), one deleted staying deleted
- "start with Windows" switched off staying off, and on staying on (pointing at the new place)
- every setting and saved file kept, tools switched off staying off, and the tools resting in this version
  not running (their settings untouched)
- an update cut off half-way leaves the older Corefold working, and the next update goes through
- a fresh install: shortcuts with the app id, start-up entry when asked for

Only for a throwaway Windows PC (GitHub's): it installs and removes Corefold for this Windows user and
changes its settings, so it refuses to run anywhere else.
    python scripts/upgradetest.py dist\\release out\\upgrade old\\Corefold-Setup-1.9.0.exe old\\Corefold-Setup-1.10.0.exe
Prints one line per check, then "upgradetest: N problem(s)".
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

if (os.environ.get("CI") != "true" and os.environ.get("COREFOLD_THROWAWAY_PC") != "1") or os.name != "nt":
    sys.exit("upgradetest: only on a throwaway Windows PC (GitHub Actions): it removes and installs Corefold")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

RELEASE = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
OLD_SETUPS = [Path(p).resolve() for p in sys.argv[3:]]
OUT.mkdir(parents=True, exist_ok=True)
SETUP = sorted(RELEASE.glob("Corefold-Setup-*.exe"))[-1]
NEW = re.match(r"Corefold-Setup-(.+)\.exe$", SETUP.name).group(1)
APP_DIR = Path(os.environ.get("ProgramW6432") or os.environ["ProgramFiles"]) / "Corefold"  # (for the whole PC)
USER_DIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Corefold"  # (the old way: for one user alone)
CONFIG = Path(os.environ["APPDATA"]) / "Corefold"
LOG = Path(os.environ["LOCALAPPDATA"]) / "Corefold" / "corefold.log"
START_MENU = Path(os.environ["ProgramData"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Corefold.lnk"
USER_START_MENU = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Corefold.lnk"
PIN = (Path(os.environ["APPDATA"]) / "Microsoft" / "Internet Explorer" / "Quick Launch" / "User Pinned" / "TaskBar"
       / "Corefold.lnk")
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{D08578D1-4B3E-404B-B56C-3FDF83BAFAE2}_is1"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_ID = "AtomicWorks.Corefold"
TMP = Path(tempfile.gettempdir()) / f"corefold-upgradetest-{uuid.uuid4().hex[:8]}"  # (its own, never one there)
TMP.mkdir()
CUSTOM: list[Path] = []  # (folders this check picked with /DIR)
POWERSHELL = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0"
                 / "powershell.exe")
import winreg  # noqa: E402

problems = []


def check(ok, what):
    print(("ok      " if ok else "PROBLEM ") + what, flush=True)
    if not ok:
        problems.append(what)


def note(what):
    print("note    " + what, flush=True)


def ps(script: str, **env) -> str:
    return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                          text=True, env=dict(os.environ, **env), timeout=120).stdout.strip()


DESKTOP = Path(ps("[Environment]::GetFolderPath('Desktop')")) / "Corefold.lnk"
PUBLIC_DESKTOP = Path(ps("[Environment]::GetFolderPath('CommonDesktopDirectory')")) / "Corefold.lnk"


def pids(exe: Path) -> list[int]:
    out = ps("Get-Process | Where-Object { $_.Path -eq $env:CF_P } | ForEach-Object { $_.Id }", CF_P=str(exe))
    return [int(x) for x in out.split() if x.isdigit()]


def wait_for(fn, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.5)
    return bool(fn())


def reg(key: str, name: str, root=winreg.HKEY_CURRENT_USER) -> str | None:
    try:
        with winreg.OpenKey(root, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            return str(winreg.QueryValueEx(k, name)[0])
    except OSError:
        return None


def set_startup(on: bool, exe: Path):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "Corefold", 0, winreg.REG_SZ, f'"{exe}" --background')
        else:
            try:
                winreg.DeleteValue(k, "Corefold")
            except FileNotFoundError:
                pass


def app_id(lnk: Path) -> str:
    return ps("$f = Get-Item -LiteralPath $env:CF_L; "
              "(New-Object -ComObject Shell.Application).NameSpace($f.DirectoryName).ParseName($f.Name)"
              ".ExtendedProperty('System.AppUserModel.ID')", CF_L=str(lnk))


def same_file(a, b) -> bool:
    """(a short 8.3 name like RUNNER~1 and the long one are the same file)"""
    try:
        return bool(a) and os.path.samefile(a, b)
    except OSError:
        return False


def log_text() -> str:
    try:
        return LOG.read_text("utf-8", errors="replace")
    except OSError:  # (not made yet: Corefold makes it as it starts)
        return ""


def log_since(start: int) -> str:
    """What the log gained since it was start long (all of it when it was started afresh meanwhile: it's
    kept to 512 KB, then begun again)."""
    text = log_text()
    return text[start:] if len(text) >= start else text


def lnk_target(lnk: Path) -> str:
    return ps("(New-Object -ComObject WScript.Shell).CreateShortcut($env:CF_L).TargetPath", CF_L=str(lnk))


def installed_version() -> str | None:
    """The version installed for the whole PC."""
    return reg(UNINSTALL_KEY, "DisplayVersion", winreg.HKEY_LOCAL_MACHINE)


def installed_dir() -> Path | None:
    where = reg(UNINSTALL_KEY, "Inno Setup: App Path", winreg.HKEY_LOCAL_MACHINE)
    return Path(where) if where else None


def user_version() -> str | None:
    """The version installed the old way, for this user alone."""
    return reg(UNINSTALL_KEY, "DisplayVersion")


def user_dir() -> Path | None:
    where = reg(UNINSTALL_KEY, "Inno Setup: App Path")
    return Path(where) if where else None


def run(setup: Path, *args, name: str) -> int:
    t = time.monotonic()
    code = subprocess.run([str(setup), "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={OUT / (name + '.log')}", *args],
                          timeout=900).returncode
    note(f"{name}: exit {code} in {time.monotonic() - t:.0f} s")
    return code


def stop_all():
    subprocess.run(["taskkill", "/f", "/im", "Corefold.exe"], capture_output=True)
    wait_for(lambda: not ps("Get-Process Corefold -ErrorAction SilentlyContinue"), 15)


def remove_corefold():
    """Takes Corefold off this (throwaway) PC, with its settings, so the next case starts clean (installed for
    the whole PC, and a copy installed the old way for this user alone)."""
    stop_all()
    for where, version in ((installed_dir, installed_version), (user_dir, user_version)):
        folder = where()
        for unins in sorted(folder.glob("unins*.exe")) if folder and folder.is_dir() else []:
            subprocess.run([str(unins), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=300)
            break
        wait_for(lambda: version() is None, 30)
        if folder:  # (the uninstaller runs on from a copy of itself: wait for it to finish with the folder)
            wait_for(lambda: not any(folder.glob("unins*.*")), 60)
            time.sleep(2)
            left = sorted(str(p.relative_to(folder)) for p in folder.rglob("*")) if folder.is_dir() else []
            check(not left, f"removing Corefold leaves nothing of its own in {folder} ({', '.join(left[:8]) or 'gone'})")
    for folder in (APP_DIR, USER_DIR, *CUSTOM):  # (to go on whatever was left)
        if folder.exists():
            subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(folder)], capture_output=True)
    for p in (CONFIG, LOG.parent):
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(p)], capture_output=True)
    for lnk in (DESKTOP, PUBLIC_DESKTOP, START_MENU, USER_START_MENU, PIN):
        lnk.unlink(missing_ok=True)
    set_startup(False, APP_DIR / "Corefold.exe")


def settings() -> dict:
    try:
        return json.loads((CONFIG / "settings.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def seed(exe: Path):
    """Uses the older version the way a person would: starts it once, then changes settings (as the window
    would save them) and leaves files of its own."""
    subprocess.Popen([str(exe), "--background"])
    wait_for(lambda: (CONFIG / "settings.json").exists(), 60)
    time.sleep(4)
    stop_all()
    s = settings()
    s.update({"language": "ar", "appearance": "light", "first_run": False, "tour_done": True,
              "check_updates": False, "tools_off": ["capture", "pdf"], "quick_menu": True,
              "clip_shortcut": "ctrl+shift+v", "volume": 37})
    s["known_tools"] = sorted(set(s.get("known_tools") or []) | {"capture", "clipboard", "pdf", "converter"})
    (CONFIG / "settings.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), "utf-8")
    (CONFIG / "my-notes-upgradetest.txt").write_text("kept?", "utf-8")
    return s


def files_of(folder: Path) -> set[str]:
    return {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()} if folder.is_dir() else set()


def after_update_checks(case: str, folder: Path, before: dict, kept_files: set[str], start: int,
                        updated: bool = True):
    exe = folder / "Corefold.exe"
    check(installed_version() == NEW, f"{case}: version {NEW} installed (found {installed_version()})")
    check(installed_dir() is not None and installed_dir().resolve() == folder.resolve(),
          f"{case}: still in {folder} (found {installed_dir()})")
    check(wait_for(lambda: pids(exe), 60), f"{case}: Corefold started again after the update")
    check(wait_for(lambda: f"starting Corefold {NEW}" in log_since(start), 30),
          f"{case}: the new version is the one running")
    check(not (folder / "_next").exists() and not (folder / "_previous").exists(),
          f"{case}: nothing of the update is left in the folder (_next, _previous)")
    check(app_id(START_MENU) == APP_ID, f"{case}: the Start menu shortcut (for everyone) has Corefold's app id")
    check(same_file(lnk_target(START_MENU), exe), f"{case}: the Start menu shortcut opens {exe}")
    check(not USER_START_MENU.exists(), f"{case}: no second Start menu shortcut of this user's own")
    time.sleep(6)  # (the tools in use have started by now)
    started = log_since(start)
    if updated:
        check("updated from " in started and f"to {NEW}" in started, f"{case}: Corefold knows it was updated")
        if (Path(__file__).resolve().parent.parent / "assets" / "release-notes" / f"{NEW}.md").exists():
            check("(what's new waits)" in started, f"{case}: What's new waits for the window to open")
    after = settings()
    for key in ("language", "appearance", "tools_off", "quick_menu", "clip_shortcut", "volume"):
        check(after.get(key) == before.get(key), f"{case}: setting {key} kept ({after.get(key)!r})")
    check(set(after.get("known_tools") or []) >= set(before.get("known_tools") or []),
          f"{case}: the tools this person knew are still known")
    check(kept_files <= files_of(CONFIG), f"{case}: every file in Corefold's settings folder kept")
    background = [ln for ln in started.splitlines() if "running in the background:" in ln]
    check(not any(t in ln for ln in background for t in ("clipboard", "capture", "quick access", "awake")),
          f"{case}: nothing of the resting tools runs ({background[-1].split(': ', 1)[-1] if background else 'none'})")
    in_use = [ln.split("tools in use:", 1)[1].split(";")[0].strip() for ln in started.splitlines()
              if "tools in use:" in ln]
    check(not updated or bool(in_use) and "pdf" not in in_use[-1] and "clipboard" not in in_use[-1] and "converter" in in_use[-1],
          f"{case}: PDF stays off, the Clipboard rests, the Converter is on ({in_use[-1] if in_use else '?'})")


note(f"new: {SETUP.name}; older: {', '.join(p.name for p in OLD_SETUPS) or 'none'}")
remove_corefold()

def moved_over_checks(case: str, old_dir: Path, deleted: bool):
    """A copy installed the old way, for this user alone, after the update moved it to the whole PC."""
    check(user_version() is None, f"{case}: the old copy is no longer in Windows' list of installed apps")
    if deleted:
        left = sorted(str(p.relative_to(old_dir)) for p in old_dir.rglob("*")) if old_dir.is_dir() else []
        check(not left and not old_dir.exists(), f"{case}: the old copy's folder is gone ({', '.join(left[:6]) or 'gone'})")
    else:
        check((old_dir / "Corefold.exe").exists(), f"{case}: a folder picked with /DIR the old way is left as it was")
    check(not pids(old_dir / "Corefold.exe"), f"{case}: the old copy isn't running")


# ---- each older release (installed for one user alone), updated the way Corefold's own updater does it
for n, old in enumerate(OLD_SETUPS):
    ver = re.match(r"Corefold-Setup-(.+)\.exe$", old.name).group(1)
    case = f"from {ver}"
    keep_icon = n % 2 == 1      # (one case keeps the desktop icon, the other deletes it)
    startup_on = n % 2 == 1     # (one keeps "start with Windows" on, the other switches it off)
    pinned = n % 2 == 1         # (one has Corefold pinned to the taskbar)
    code = run(old, "/VERYSILENT", "/TASKS=desktopicon,startup", name=f"{ver}-install")
    check(code == 0 and user_version() == ver, f"{case}: {ver} installed for this user")
    stop_all()
    before = seed(USER_DIR / "Corefold.exe")
    kept_files = files_of(CONFIG)
    set_startup(startup_on, USER_DIR / "Corefold.exe")
    if not keep_icon:
        DESKTOP.unlink(missing_ok=True)
    if pinned and USER_START_MENU.exists():  # (a pin is a copy of the shortcut in the taskbar's own folder)
        PIN.parent.mkdir(parents=True, exist_ok=True)
        PIN.write_bytes(USER_START_MENU.read_bytes())
    subprocess.Popen([str(USER_DIR / "Corefold.exe"), "--background"])  # (running, as it is when it updates)
    wait_for(lambda: pids(USER_DIR / "Corefold.exe"), 30)
    time.sleep(3)
    start = LOG.stat().st_size if LOG.exists() else 0
    code = run(SETUP, "/SILENT", "/SP-", name=f"{ver}-update")  # (Corefold's updater adds the same switches)
    check(code == 0, f"{case}: the update installed")
    check("the installer asked Corefold to close" in log_since(start), f"{case}: the old copy was asked to close")
    after_update_checks(case, APP_DIR, before, kept_files, start)
    moved_over_checks(case, USER_DIR, deleted=True)
    if keep_icon:
        check(DESKTOP.exists() and same_file(lnk_target(DESKTOP), APP_DIR / "Corefold.exe"),
              f"{case}: the desktop icon stays, and opens the Corefold in Program Files")
        check(not PUBLIC_DESKTOP.exists(), f"{case}: no second desktop icon (for everyone)")
    else:
        check(not DESKTOP.exists() and not PUBLIC_DESKTOP.exists(), f"{case}: a deleted desktop icon stays deleted")
    if pinned:
        check(PIN.exists() and same_file(lnk_target(PIN), APP_DIR / "Corefold.exe"),
              f"{case}: the taskbar pin opens the Corefold in Program Files")
    run_now = reg(RUN_KEY, "Corefold")
    if startup_on:
        check(run_now is not None and str(APP_DIR / "Corefold.exe").lower() in run_now.lower(),
              f"{case}: starting with Windows stays on, with the Corefold in Program Files ({run_now})")
    else:
        check(run_now is None, f"{case}: starting with Windows stays off ({run_now})")
    remove_corefold()

# ---- a folder picked with /DIR the old way: Corefold moves to Program Files, the folder is left alone
if OLD_SETUPS:
    old = OLD_SETUPS[-1]
    custom = TMP / "My apps" / "Corefold here"
    CUSTOM.append(custom)
    code = run(old, "/VERYSILENT", "/TASKS=", f"/DIR={custom}", name="custom-install")
    check(code == 0 and (custom / "Corefold.exe").exists(), f"custom folder: installed into {custom}")
    stop_all()
    before = seed(custom / "Corefold.exe")
    kept_files = files_of(CONFIG)
    start = LOG.stat().st_size if LOG.exists() else 0
    code = run(SETUP, "/SILENT", "/SP-", name="custom-update")
    check(code == 0, "custom folder: the update installed")
    after_update_checks("custom folder", APP_DIR, before, kept_files, start)
    moved_over_checks("custom folder", custom, deleted=False)
    remove_corefold()

# ---- an update cut off half-way: the older Corefold still works, and updating again goes through
if OLD_SETUPS:
    old = OLD_SETUPS[-1]
    ver = re.match(r"Corefold-Setup-(.+)\.exe$", old.name).group(1)
    run(old, "/VERYSILENT", "/TASKS=", name="cut-install")
    stop_all()
    proc = subprocess.Popen([str(SETUP), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={OUT / 'cut.log'}"])
    time.sleep(4)
    subprocess.run(["taskkill", "/f", "/t", "/pid", str(proc.pid)], capture_output=True)
    for name in ("Corefold-Setup-*", "is-*"):  # (the installer's own helper, if it outlived the kill)
        ps(f"Get-Process | Where-Object {{ $_.ProcessName -like '{name}' }} | Stop-Process -Force")
    time.sleep(3)
    stop_all()
    # (cut before the end, the copy for this user is still the whole one; after it, the one in Program Files)
    folder = USER_DIR if user_version() == ver and (USER_DIR / "Corefold.exe").exists() else APP_DIR
    exe = folder / "Corefold.exe"
    note(f"after the cut: for this user {user_version()}, for the PC {installed_version()}, running from {folder}")
    check(exe.exists() and (user_version() == ver or installed_version() == NEW),
          "cut off: a whole Corefold is still installed")
    if exe.exists():
        start = LOG.stat().st_size if LOG.exists() else 0
        inner = folder / "_internal"
        note(f"after the cut: _internal {'has ' + str(sum(1 for _ in inner.rglob('*'))) + ' files' if inner.is_dir() else 'missing'}"
             f", left by the cut: {', '.join(sorted(q.name for q in [*APP_DIR.glob('is-*'), *APP_DIR.glob('_next')])) or 'nothing'}")
        started = subprocess.Popen([str(exe), "--background"], stderr=subprocess.PIPE)
        ok = wait_for(lambda: pids(exe), 60) and wait_for(lambda: "starting Corefold" in log_since(start), 30)
        if not ok:
            ended = started.poll()
            err = started.stderr.read().decode("utf-8", "replace")[-600:] if ended is not None else ""
            note(f"after the cut Corefold {'ended with ' + str(ended) if ended is not None else 'is still running'}"
                 f"; log: {log_since(start)[-400:]!r}; errors: {err!r}")
        check(ok, "cut off: Corefold still starts")
    code = run(SETUP, "/SILENT", "/SP-", name="cut-again")
    check(code == 0 and installed_version() == NEW and user_version() is None, "cut off: updating again goes through")
    remove_corefold()

# ---- a fresh install (for the whole PC), then an update of it, the way Corefold's updater runs it
code = run(SETUP, "/VERYSILENT", "/TASKS=desktopicon,startup", name="fresh")
check(code == 0 and installed_version() == NEW and same_file(installed_dir(), APP_DIR), f"fresh: installed in {APP_DIR}")
check(app_id(START_MENU) == APP_ID and app_id(PUBLIC_DESKTOP) == APP_ID, "fresh: both shortcuts have Corefold's app id")
check(reg(RUN_KEY, "Corefold") is not None, "fresh: starts with Windows (asked for)")
check(wait_for(lambda: pids(APP_DIR / "Corefold.exe"), 60), "fresh: Corefold started")
icon_time = PUBLIC_DESKTOP.stat().st_mtime_ns if PUBLIC_DESKTOP.exists() else None
before, kept_files = settings(), files_of(CONFIG)
start = LOG.stat().st_size if LOG.exists() else 0
code = run(SETUP, "/SILENT", "/SP-", name="whole-pc-update")
check(code == 0, "whole PC: the update installed")
check("the installer asked Corefold to close" in log_since(start), "whole PC: Corefold was asked to close")
after_update_checks("whole PC", APP_DIR, before, kept_files, start, updated=False)
check(PUBLIC_DESKTOP.exists() and PUBLIC_DESKTOP.stat().st_mtime_ns == icon_time,
      "whole PC: the desktop icon was left alone (so it keeps its place)")
check(not DESKTOP.exists(), "whole PC: no second desktop icon of this user's own")
remove_corefold()

# ---- a folder picked with /DIR for the whole PC is kept by the update
custom = TMP / "Shared apps" / "Corefold"
CUSTOM.append(custom)
code = run(SETUP, "/VERYSILENT", "/TASKS=", f"/DIR={custom}", name="custom-pc-install")
check(code == 0 and (custom / "Corefold.exe").exists(), f"whole PC, own folder: installed into {custom}")
wait_for(lambda: pids(custom / "Corefold.exe"), 60)
before, kept_files = settings(), files_of(CONFIG)
start = LOG.stat().st_size if LOG.exists() else 0
code = run(SETUP, "/SILENT", "/SP-", name="custom-pc-update")
check(code == 0, "whole PC, own folder: the update installed")
after_update_checks("whole PC, own folder", custom, before, kept_files, start, updated=False)
check(not (APP_DIR / "Corefold.exe").exists(), "whole PC, own folder: no second Corefold in Program Files")
remove_corefold()

print(f"upgradetest: {len(problems)} problem(s)", flush=True)
for p in problems:
    print("  - " + p)
sys.exit(1 if problems else 0)
