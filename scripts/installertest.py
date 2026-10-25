"""The installer on a real Windows PC: installs the build in dist\\release, then checks that updating closes
Corefold the gentle way (a conversion that's running is finished first, and a different program that happens
to be called Corefold.exe is left alone), that a /DIR folder already holding other things is refused, and that
removing Corefold keeps files that aren't its own.

Windows only, after scripts\\build.ps1 (FFmpeg on PATH, to make a test video):
    python scripts/installertest.py dist\\release out\\installer
Prints one line per check, then "installertest: N problem(s)".
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if os.environ.get("CI") != "true" and os.environ.get("COREFOLD_THROWAWAY_PC") != "1":
    sys.exit("installertest: only on a throwaway Windows PC (GitHub Actions, or a virtual machine with "
             "COREFOLD_THROWAWAY_PC=1): it installs and removes Corefold for this Windows user")

RELEASE = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
SETUP = sorted(RELEASE.glob("Corefold-Setup-*.exe"))[-1]
APP_DIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Corefold"
EXE = APP_DIR / "Corefold.exe"
LOG = Path(os.environ["LOCALAPPDATA"]) / "Corefold" / "corefold.log"
TMP = Path(tempfile.mkdtemp(prefix="corefold-installertest-"))
POWERSHELL = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0"
                 / "powershell.exe")

problems = []


def check(ok, what):
    print(("ok      " if ok else "PROBLEM ") + what, flush=True)
    if not ok:
        problems.append(what)


def note(what):
    print("note    " + what, flush=True)


def pids(path: Path) -> list[int]:
    """The running programs started from exactly this file."""
    out = subprocess.run([POWERSHELL, "-NoProfile", "-Command",
                          "Get-Process | Where-Object { $_.Path -eq $env:CF_P } | ForEach-Object { $_.Id }"],
                         capture_output=True, text=True, env=dict(os.environ, CF_P=str(path))).stdout
    return [int(x) for x in out.split() if x.strip().isdigit()]


def wait_for(fn, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.5)
    return bool(fn())


def run_setup(*args, name="setup") -> tuple[int, float]:
    t = time.monotonic()
    code = subprocess.run([str(SETUP), "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={OUT / (name + '.log')}", *args],
                          timeout=900).returncode
    return code, time.monotonic() - t


def log_text() -> str:
    try:
        return LOG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


note(f"installer: {SETUP.name}")

# 1. a first install, the way people install it
code, took = run_setup("/VERYSILENT", "/TASKS=", name="1-install")
check(code == 0 and EXE.exists(), f"installed (exit {code}, {took:.0f} s)")
check(wait_for(lambda: pids(EXE), 30), "Corefold started in the tray after installing")

# 2. a different program that happens to be called Corefold.exe (another folder): never stopped
decoy_dir = TMP / "someone else"
decoy_dir.mkdir()
decoy = decoy_dir / "Corefold.exe"
shutil.copy2(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "PING.EXE", decoy)
decoy_proc = subprocess.Popen([str(decoy), "-n", "600", "127.0.0.1"], stdout=subprocess.DEVNULL,
                              creationflags=0x08000000)
check(wait_for(lambda: pids(decoy), 10), "the other Corefold.exe is running")

# 3. a conversion is running when the update starts: it's finished before Corefold closes
video = TMP / "holiday.mp4"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30", "-t", "25",
                "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(video)], check=True)
small = TMP / "holiday (small).mp4"
start = log_text()
subprocess.run([str(EXE), "--run", "video:small", str(video)], timeout=30)
check(wait_for(lambda: "video:small" in log_text()[len(start):], 60), "the conversion started")
time.sleep(3)
before = log_text()
code, took = run_setup("/SILENT", name="2-update")  # (as Corefold's own updater starts it)
after = log_text()[len(before):]
note(f"update took {took:.0f} s")
check(code == 0, f"the update installed (exit {code})")
check("the installer asked Corefold to close" in after, "Corefold was asked to close (not stopped dead)")
done = [ln for ln in after.splitlines() if "job " in ln or "converted" in ln.lower() or "finished" in ln.lower()]
for ln in done[-5:]:
    note("log: " + ln.strip()[:160])
probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(small)],
                       capture_output=True, text=True)
try:
    length = float(probe.stdout.strip() or 0)
except ValueError:
    length = 0
check(small.exists() and length > 24, f"the conversion was finished, not cut off ({length:.1f} s of 25)")
check(bool(pids(decoy)), "the other program called Corefold.exe was left running")
check(wait_for(lambda: pids(EXE), 30), "the new Corefold started again in the tray")

# 4. Corefold not running, a plain update: no waiting
for pid in pids(EXE):
    subprocess.run(["taskkill", "/pid", str(pid), "/f"], capture_output=True)
wait_for(lambda: not pids(EXE), 10)
code, took = run_setup("/VERYSILENT", name="3-update-closed")
check(code == 0 and took < 120, f"updated with Corefold closed (exit {code}, {took:.0f} s)")

# 5. installing into a folder (with /DIR) that already holds other things: refused, nothing touched
shared = TMP / "My things"
shared.mkdir()
(shared / "keep me.txt").write_text("mine")
code, _ = run_setup("/VERYSILENT", f"/DIR={shared}", name="4-dir")
check(code != 0, f"a /DIR folder with other files in it is refused (exit {code})")
check((shared / "keep me.txt").exists() and not (shared / "Corefold.exe").exists(), "and nothing in it changed")

# 6. removing Corefold while it runs: it's asked to close; files that aren't Corefold's stay
wait_for(lambda: pids(EXE), 20) or subprocess.Popen([str(EXE), "--background"])
wait_for(lambda: pids(EXE), 30)
mine = APP_DIR / "my notes.txt"
mine.write_text("I saved this here")
unins = sorted(APP_DIR.glob("unins*.exe"))[0]
before = log_text()
code = subprocess.run([str(unins), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=300).returncode
wait_for(lambda: not EXE.exists(), 60)
after = log_text()[len(before):]
check(code == 0 and not EXE.exists(), f"removed (exit {code})")
check("the installer asked Corefold to close" in after, "Corefold was asked to close before it was removed")
check(mine.exists(), "a file that isn't Corefold's stays (it used to be deleted with the folder)")
check(not (APP_DIR / "_internal").exists(), "Corefold's own files are gone")
check(bool(pids(decoy)), "the other program called Corefold.exe is still running")

decoy_proc.kill()
print(f"installertest: {len(problems)} problem(s)", flush=True)
for p in problems:
    print("  - " + p)
sys.exit(1 if problems else 0)
