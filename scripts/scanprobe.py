"""TEMPORARY: what Microsoft Defender says and does on this PC (for building Corefold Scan)."""
import ctypes
import glob
import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def ps(script, timeout=300):
    t = time.time()
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, timeout=timeout)
    print(f"--- ps ({time.time() - t:.1f} s, exit {r.returncode})")
    print(r.stdout.decode("utf-8", "replace")[:6000])
    print(r.stderr.decode("utf-8", "replace")[:2000])


def run(args, timeout=1800):
    t = time.time()
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print("--- TIMEOUT", args)
        return
    print(f"--- {' '.join(map(str, args))} ({time.time() - t:.1f} s, exit {r.returncode} = {r.returncode & 0xffffffff:#x})")
    print("raw head:", r.stdout[:80])
    print(r.stdout.decode("utf-8", "replace")[:6000])
    print("stderr:", r.stderr.decode("utf-8", "replace")[:2000])


print("admin:", ctypes.windll.shell32.IsUserAnAdmin())
plats = sorted(glob.glob(os.path.expandvars(r"%ProgramData%\Microsoft\Windows Defender\Platform\*\MpCmdRun.exe")))
print("platforms:", plats)
old = os.path.expandvars(r"%ProgramFiles%\Windows Defender\MpCmdRun.exe")
print("old:", old, os.path.exists(old))
mp = plats[-1] if plats else old
ps("Get-MpComputerStatus | Format-List *")
ps("Get-MpComputerStatus | ConvertTo-Json -Compress")
ps("Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | Format-List *")
ps("Get-MpPreference | Select-Object DisableRealtimeMonitoring,ExclusionPath,MAPSReporting,SubmitSamplesConsent,"
   "QuarantinePurgeItemsAfterDelay | Format-List")
ps("Get-Service WinDefend, WdNisSvc, wscsvc -ErrorAction SilentlyContinue | Format-List Name,Status,StartType")
run([mp, "-h"])
d = Path(os.environ.get("RUNNER_TEMP", os.environ["TEMP"])) / "cfprobe"
d.mkdir(exist_ok=True)
e = d / "eicar.com"
e.write_bytes(b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*")
time.sleep(2)
print("eicar there after writing:", e.exists())
(d / "clean.txt").write_text("hello")
run([mp, "-Scan", "-ScanType", "3", "-File", str(d / "clean.txt")])
run([mp, "-Scan", "-ScanType", "3", "-File", str(d), "-DisableRemediation"])
print("eicar there after -DisableRemediation:", e.exists())
run([mp, "-Scan", "-ScanType", "3", "-File", str(d)])
time.sleep(3)
print("eicar there after scan:", e.exists())
ps("Get-MpThreatDetection | ConvertTo-Json -Depth 3")
ps("Get-MpThreat | ConvertTo-Json -Depth 3")
run([mp, "-Restore", "-ListAll"])
run([mp, "-Restore", "-Name", "Virus:DOS/EICAR_Test_File", "-FilePath", str(e)])
time.sleep(2)
print("eicar there after restore:", e.exists())
run([mp, "-Restore", "-ListAll"])
run([mp, "-Scan", "-ScanType", "3", "-File", str(e)])
run([mp, "-Restore", "-ListAll"])
run([mp, "-Scan", "-ScanType", "3", "-File", str(d / "nothere.exe")])
# a standard (not administrator) user: what works for them
pw = "Cf-Probe-" + str(int(time.time()))[-6:] + "a!A"
ps(f"$p = ConvertTo-SecureString '{pw}' -AsPlainText -Force; New-LocalUser -Name cfprobe -Password $p | Out-Null; "
   f"'made'")
out = d / "user.txt"
(d / "user").mkdir(exist_ok=True)
e2 = d / "user" / "eicar.com"
e2.write_bytes(b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*")
ps(f"icacls '{d}' /grant 'cfprobe:(OI)(CI)F' | Out-Null; $p = ConvertTo-SecureString '{pw}' -AsPlainText -Force; "
   f"$c = New-Object System.Management.Automation.PSCredential('cfprobe', $p); "
   f"$cmd = '& \"{mp}\" -Scan -ScanType 3 -File \"{d / 'user'}\" > \"{d}\\u1.txt\" 2>&1; \"exit $LASTEXITCODE\" >> \"{d}\\u1.txt\"; "
   f"& \"{mp}\" -Restore -ListAll > \"{d}\\u2.txt\" 2>&1; \"exit $LASTEXITCODE\" >> \"{d}\\u2.txt\"; "
   f"Get-MpComputerStatus | Select AntivirusEnabled,RealTimeProtectionEnabled,AMRunningMode > \"{d}\\u3.txt\" 2>&1; "
   f"Get-MpThreatDetection > \"{d}\\u4.txt\" 2>&1'; "
   f"$pr = Start-Process powershell.exe -Credential $c -ArgumentList '-NoProfile','-Command',$cmd -Wait -PassThru "
   f"-WorkingDirectory '{d}' -LoadUserProfile; 'started ' + $pr.ExitCode", timeout=900)
for n in ("u1", "u2", "u3", "u4"):
    p = d / f"{n}.txt"
    print(f"=== standard user {n}:", p.read_bytes().decode("utf-16", "replace") if p.exists() and p.read_bytes()[:2] == b"\xff\xfe"
          else (p.read_text("utf-8", "replace") if p.exists() else "(none)"))
run([mp, "-Restore", "-ListAll"])
t = time.time()
run([mp, "-Scan", "-ScanType", "1"], timeout=1500)
print("quick scan took", time.time() - t)
ps("Get-MpComputerStatus | Select QuickScanEndTime,QuickScanStartTime,FullScanEndTime | Format-List")
