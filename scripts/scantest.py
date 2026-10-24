"""Corefold Scan, tried for real on a Windows PC with Microsoft Defender.

The test threat is EICAR: a harmless line of text every antivirus agrees to treat as a virus, made for
testing exactly this. It's put together here from two halves so this file itself is never flagged.

Checked: Defender's state is read; signatures (Windows' own catalog-signed files, a signed program, the
same program changed after signing, and with its signature taken out); the "downloaded from" note; names
dressed up as another kind of file; the hosts file; what starts with Windows and what's running from odd
places; a custom scan that finds EICAR (also inside a ZIP, and also where Defender is told not to look) and
lets Defender put it in quarantine; the quarantine list and putting a file back; stopping a scan (Defender
must stop too); the right-click menu; and the page itself, clicked like a person, with pictures.

Run: python scripts/scantest.py <folder> [en|ar|fr|es|tr] [dark|light] [--quick-scan]
Prints one line per check and "scantest: N problem(s)"; pictures of the page go in the folder."""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "scantest").resolve()
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
LOOK = sys.argv[3] if len(sys.argv) > 3 else "dark"
QUICK = "--quick-scan" in sys.argv
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-scantest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(1500, exit=True)  # stuck somewhere: say where, and stop

import winreg  # noqa: E402

from corefold import scan, shellmenu, trust  # noqa: E402
from corefold import settings as st  # noqa: E402
from corefold import tools as all_tools  # noqa: E402

problems = []
errors = []
SYSTEM32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
TEST_RUN = ["CfScanTestHidden", "CfScanTestScript", "CfScanTestOdd"]
WORK = Path(os.environ.get("TEMP", str(TMP))) / "cf-scantest"
EXCLUSIONS_BEFORE: list[str] = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)


def note(what):
    print("      " + what, flush=True)


def step(name, fn):
    t = time.time()
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()
    note(f"({name}: {time.time() - t:.1f} s)")


def ps(script: str, timeout=120) -> str:
    return scan.powershell(script, timeout=timeout)


def eicar(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(EICAR)
    return path


def unsigned_copy(src: Path, dst: Path) -> Path:
    """A copy of a signed program with its signature taken out (the PE's security directory zeroed)."""
    data = bytearray(src.read_bytes())
    pe = int.from_bytes(data[0x3C:0x40], "little")
    opt = pe + 24
    magic = int.from_bytes(data[opt:opt + 2], "little")
    dirs = opt + (96 if magic == 0x10B else 112)
    sec = dirs + 4 * 8
    data[sec:sec + 8] = bytes(8)
    dst.write_bytes(bytes(data))
    return dst


def broken_copy(src: Path, dst: Path) -> Path:
    """A copy of a signed program changed after it was signed."""
    data = bytearray(src.read_bytes())
    i = len(data) // 3
    data[i] = (data[i] + 1) % 256
    dst.write_bytes(bytes(data))
    return dst


def defender_skips(folder: Path, want: bool, limit=90) -> bool:
    """Defender takes a while to follow a change to what it's told to skip: wait until a scan of a clean
    file in the folder is skipped (or isn't), so the test sees what a real PC would."""
    probe = folder / "probe.txt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("nothing here")
    t = time.time()
    said = ""
    while time.time() - t < limit:
        said = scan.run_defender(scan.CUSTOM, [str(probe)], threading.Event(), scan.find_mpcmdrun())[1]
        if bool(scan.skipped_by_defender(said)) == want:
            note(f"Defender {'skips' if want else 'looks at'} {folder.name} after {time.time() - t:.0f} s")
            probe.unlink()
            return True
        time.sleep(2)
    note(f"Defender still {'looks at' if want else 'skips'} {folder.name} after {limit} s: {said.strip()[-300:]!r}")
    probe.unlink()
    return False


def mpcmdrun_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq MpCmdRun.exe"], capture_output=True, text=True).stdout
    return "MpCmdRun.exe" in out


def our_mpcmdrun_left() -> bool:
    """A Defender scan this test started is still going (Windows' own tasks start MpCmdRun too, now and then:
    those aren't ours)."""
    out = ps("Get-CimInstance Win32_Process -Filter \"Name='MpCmdRun.exe'\" | "
             "ForEach-Object { \"$($_.ParentProcessId)`t$($_.CommandLine)\" }")
    ours = [line for line in out.splitlines() if line.split("\t")[0].strip() == str(os.getpid())]
    others = [line for line in out.splitlines() if line.strip() and line not in ours]
    if others:
        note(f"Windows' own MpCmdRun: {others}")
    return bool(ours)


# ----------------------------------------------------------------------------- Defender's state

def state():
    exe = scan.find_mpcmdrun()
    check(bool(exe) and os.path.isfile(exe), f"Defender's command-line tool is found ({exe})")
    t = time.time()
    d = scan.defender_status()
    note(f"status: read={d.read} enabled={d.enabled} realtime={d.realtime} mode={d.mode!r} updated={d.days_old} "
         f"days ago, others={d.others}, skipped={d.skipped} (known {d.skipped_known}) in {time.time() - t:.1f} s")
    check(d.read and d.enabled, "Defender's status is read: it's on")
    check(d.updated > 0, "when its list of threats was updated is known")
    check(time.time() - t < 30, "reading the status is quick enough")  # (the first PowerShell on a cold PC is slow)
    if d.skipped_known:
        EXCLUSIONS_BEFORE.extend(v for k, v in d.skipped if k == "path")
        ch = scan.Checks()
        ch.skipped(d)
        broad = [f for f in ch.findings if f.level == "warn"]
        check(not EXCLUSIONS_BEFORE or broad, f"a whole drive Defender skips is worth a look ({len(broad)})")


# ----------------------------------------------------------------------------- signatures and names

def signatures():
    WORK.mkdir(parents=True, exist_ok=True)
    for name in ("notepad.exe", "cmd.exe", "kernel32.dll"):
        p = SYSTEM32 / name
        if not p.exists():
            continue
        sig = trust.signature(str(p))
        note(f"{name}: {sig}")
        check(sig.ok and "microsoft" in sig.signer.lower(), f"Windows' own {name} is signed by Microsoft")
    py = Path(sys.executable)
    sig = trust.signature(str(py))
    note(f"python: {sig}")
    check(sig.ok and sig.signer, f"a signed program is signed ({sig.signer})")
    check(not sig.catalog, "and its signature is its own (not a catalog's)")
    plain = unsigned_copy(py, WORK / "unsigned.exe")
    sig = trust.signature(str(plain))
    note(f"unsigned copy: {sig}")
    check(sig.state == trust.UNSIGNED, "the same program with its signature taken out: not signed")
    broken = broken_copy(py, WORK / "changed.exe")
    sig = trust.signature(str(broken))
    note(f"changed copy: {sig}")
    check(sig.state == trust.BROKEN, "the same program changed after signing: broken signature")
    text = WORK / "readme.txt"
    text.write_text("hello")
    check(not trust.signature(str(text)).ok, "a text file isn't called signed")
    check(trust.signature(str(WORK / "not-there.exe")).state == trust.UNKNOWN, "a missing file: can't tell")
    t = time.time()
    for _ in range(20):
        trust.signature(str(SYSTEM32 / "notepad.exe"))
    each = (time.time() - t) / 20
    check(each < 0.5, f"a signature check takes {each * 1000:.0f} ms")
    # where it came from
    got = WORK / "download.exe"
    shutil.copy(py, got)
    with open(str(got) + ":Zone.Identifier", "w") as f:
        f.write("[ZoneTransfer]\nZoneId=3\nReferrerUrl=https://www.example.org/page\n"
                "HostUrl=https://downloads.example.com/download.exe\n")
    o = trust.downloaded_from(str(got))
    check(o is not None and o.from_internet and o.host == "downloads.example.com" and o.referrer == "example.org",
          f"the downloaded-from note is read ({o})")
    check(trust.downloaded_from(str(text)) is None, "a file without the note has none")


def names():
    cases = {"invoice.pdf.exe": ".pdf", "Photo.JPG.scr": ".jpg", "song.mp3   .exe": ".mp3", "report.docx.js": ".docx",
             "setup.exe": "", "archive.tar.gz": "", "My.Program.exe": "", "notes.pdf.lnk": "", "x.pdf": "",
             "movie.mkv.bat": ".mkv"}
    for name, want in cases.items():
        check(scan.disguised(name) == want, f"{name!r} pretends to be {want or 'nothing'}")
    flip = "Invoice\u202efdp.exe"
    check(scan.flipped(flip) and scan.shown_name(flip) == "Invoicefdp.exe", "a name turned around is noticed")
    check(not scan.flipped("normal name.exe"), "a normal name isn't")
    folder = WORK / "names"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(sys.executable, folder / "holiday.jpg.exe")
    (folder / "holiday.jpg").write_bytes(b"\xff\xd8\xff")
    flipped = folder / ("cv\u202efdp.exe")
    try:
        shutil.copy(sys.executable, flipped)
    except OSError:
        flipped = None
    broken_copy(Path(sys.executable), folder / "tool.exe")
    ch = scan.Checks()
    ch.walk(folder, depth=2)
    kinds = {(f.kind, os.path.basename(f.path)) for f in ch.findings}
    note(f"found in the folder: {sorted(kinds)}")
    check(("disguised", "holiday.jpg.exe") in kinds, "holiday.jpg.exe is noticed")
    check(not any(n == "holiday.jpg" for _, n in kinds), "the real photo isn't")
    check(flipped is None or ("flipped", flipped.name) in kinds, "the turned-around name is noticed")
    check(("broken", "tool.exe") in kinds, "the program with a broken signature is noticed")


def hosts():
    f = WORK / "hosts"
    f.write_text("# a comment\n127.0.0.1 localhost\n0.0.0.0 ads.example.net\n10.1.2.3 update.microsoft.com "
                 "www.paypal.com\n::1 localhost\n", encoding="utf-8")
    found = scan.hosts_redirects(f)
    check(sorted(found) == [("update.microsoft.com", "10.1.2.3"), ("www.paypal.com", "10.1.2.3")],
          f"the hosts file sending Windows Update and PayPal elsewhere is noticed, ads blocking isn't ({found})")
    check(scan.hosts_redirects(scan.hosts_path()) == [], "this PC's own hosts file is clean")
    check(scan.broad_exclusion("path", "C:\\") and scan.broad_exclusion("path", str(scan.downloads())) and
          scan.broad_exclusion("ext", "exe") and not scan.broad_exclusion("path", r"C:\Projects\build"),
          "skipping a whole drive, Downloads or .exe files is a lot; one project folder isn't")


def startup():
    temp = WORK / "startup"
    temp.mkdir(parents=True, exist_ok=True)
    (temp / "run.vbs").write_text('WScript.Echo "hi"')
    odd = unsigned_copy(Path(sys.executable), temp / "updater.exe")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN) as k:
        winreg.SetValueEx(k, "CfScanTestHidden", 0, winreg.REG_SZ,
                          "powershell.exe -NoProfile -w hidden -enc SQBFAFgA")
        winreg.SetValueEx(k, "CfScanTestScript", 0, winreg.REG_SZ, f'wscript.exe //B "{temp / "run.vbs"}"')
        winreg.SetValueEx(k, "CfScanTestOdd", 0, winreg.REG_SZ, f'"{odd}" --tray')
    try:
        ch = scan.Checks()
        t = time.time()
        ch.startup()
        note(f"startup checks: {time.time() - t:.1f} s, {len(ch.findings)} found")
        by = {f.detail.get("name"): f for f in ch.findings}
        check("CfScanTestHidden" in by and by["CfScanTestHidden"].kind == "startup-hidden",
              "a hidden PowerShell command that starts with Windows is noticed")
        check("CfScanTestScript" in by and by["CfScanTestScript"].kind == "startup-script" and
              by["CfScanTestScript"].level == "warn", "a script starting from a temporary folder is worth a look")
        check("CfScanTestOdd" in by and by["CfScanTestOdd"].kind == "startup" and by["CfScanTestOdd"].level == "warn",
              "a program nobody signed, starting from a temporary folder, is worth a look")
        others = [f for f in ch.findings if not str(f.detail.get("name", "")).startswith("CfScanTest")]
        note(f"other startup findings on this PC: {[(f.kind, f.path) for f in others]}")
    finally:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN, 0, winreg.KEY_SET_VALUE) as k:
            for name in TEST_RUN:
                try:
                    winreg.DeleteValue(k, name)
                except OSError:
                    pass


def more_places():
    """The start places beyond Run, a debugger in front of a program, services, browsers, the proxy and
    Defender's policies: each test entry is noticed, then taken out again."""
    temp = WORK / "more"
    temp.mkdir(parents=True, exist_ok=True)
    odd = unsigned_copy(Path(sys.executable), temp / "helper.exe")
    hkcu, hklm = winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE
    pol = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run"
    cmdp = r"Software\Microsoft\Command Processor"
    ifeo = scan.IFEO + r"\cf-scantest-target.exe"
    edge = r"Software\Policies\Microsoft\Edge\ExtensionInstallForcelist"
    svc = r"SYSTEM\CurrentControlSet\Services\CfScanTestSvc"
    made = []

    def put(hive, key, name, value, kind=winreg.REG_SZ):
        with winreg.CreateKeyEx(hive, key, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
            winreg.SetValueEx(k, name, 0, kind, value)
        made.append((hive, key, name))
    try:
        put(hkcu, pol, "CfScanTestPolicy", f'"{odd}"')
        auto_before = scan._reg_values(hkcu, cmdp).get("AutoRun")
        if auto_before is None:
            put(hkcu, cmdp, "AutoRun", f'"{odd}"')
        put(hklm, ifeo, "Debugger", f'"{odd}"')
        put(hkcu, edge, "1", "abcdefghijklmnopabcdefghijklmnop;https://example.invalid/update.xml")
        put(hklm, svc, "ImagePath", f'"{odd}"', winreg.REG_EXPAND_SZ)
        put(hklm, svc, "Type", 0x10, winreg.REG_DWORD)
        ch = scan.Checks()
        t = time.time()
        for fn in (ch.more_startup, ch.services, ch.browsers, ch.defender_policies):
            fn()
        took = time.time() - t
        note(f"more checks: {took:.1f} s, {[(f.kind, f.path, f.level) for f in ch.findings]}")
        same = lambda f: os.path.normcase(f.path) == os.path.normcase(str(odd))  # noqa: E731
        check(any(f.kind == "startup" and same(f) and f.detail.get("name") == "CfScanTestPolicy"
                  for f in ch.findings), "a program started by the policy Run key is noticed")
        if auto_before is None:
            check(any(f.kind == "startup" and same(f) and f.detail.get("name") == "AutoRun" for f in ch.findings),
                  "a program cmd.exe runs every time it opens (AutoRun) is noticed")
        check(any(f.kind == "replaced" and f.path.lower() == "cf-scantest-target.exe" and f.level == "warn"
                  for f in ch.findings), "a debugger put in front of a program is worth a look")
        check(any(f.kind == "browser" and f.detail.get("type") == "extension" and f.level == "warn"
                  for f in ch.findings), "an add-on forced into Edge is worth a look")
        check(any(f.kind == "service" and same(f) and f.detail.get("name") == "CfScanTestSvc" for f in ch.findings),
              "a service nobody signed, running from a temporary folder, is noticed")
        check(not any(f.kind == "service" and "system32" in f.path.lower() for f in ch.findings),
              "Windows' own services aren't")
        check(took < 60, "these checks are quick enough")
    finally:
        for hive, key, name in reversed(made):
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
                    winreg.DeleteValue(k, name)
            except OSError:
                pass
        for hive, key in ((hklm, ifeo), (hklm, svc), (hkcu, edge)):
            try:
                winreg.DeleteKeyEx(hive, key, winreg.KEY_WOW64_64KEY, 0)
            except OSError:
                pass


def running():
    folder = WORK / "running"
    folder.mkdir(parents=True, exist_ok=True)
    exe = unsigned_copy(Path(sys.executable), folder / "helper.exe")
    proc = subprocess.Popen([str(exe), "-c", "import time; time.sleep(120)"])
    try:
        time.sleep(1.5)
        if proc.poll() is not None:
            note("the unsigned copy didn't start; skipped")
            return
        ch = scan.Checks()
        t = time.time()
        ch.running()
        note(f"running checks: {time.time() - t:.1f} s, {[(f.kind, f.path, f.level) for f in ch.findings]}")
        mine = [f for f in ch.findings if os.path.normcase(f.path) == os.path.normcase(str(exe))]
        check(mine and mine[0].kind == "running" and mine[0].level == "warn",
              "a program nobody signed, running from a temporary folder, is worth a look")
        check(time.time() - t < 30, "looking at what's running is quick enough")
    finally:
        proc.kill()


# ----------------------------------------------------------------------------- Defender scans

def defender_scans():
    if EXCLUSIONS_BEFORE:  # (the test PC skips whole drives; a real one doesn't)
        ps("Remove-MpPreference -ExclusionPath " + ",".join(f"'{p}'" for p in EXCLUSIONS_BEFORE))
    folder = WORK / "threats"
    check(defender_skips(folder, False), "Defender looks at the test folder once it's no longer told to skip it")
    eicar(folder / "eicar.com")
    with zipfile.ZipFile(folder / "parcel.zip", "w") as z:
        z.writestr("inside/eicar.com", EICAR)
    (folder / "clean.txt").write_text("nothing here")
    found = []
    t = time.time()
    r = scan.scan(scan.CUSTOM, [str(folder)], on_finding=found.append)
    note(f"custom scan: {time.time() - t:.1f} s, code {r.defender_code:#x}, problem {r.defender_problem!r}, "
         f"threats {r.threats}, skipped {r.skipped}")
    check(r.defender_ran and not r.defender_problem, "Defender scanned the folder")
    names = [th["name"] for th in r.threats]
    check(any("eicar" in n.lower() for n in names), f"it found the test threat ({names})")
    files = [f for th in r.threats for f in th["files"]]
    check(any(f.lower().endswith("eicar.com") and "parcel.zip" not in f.lower() for f in files),
          "the loose file is listed")
    check(any("parcel.zip" in f.lower() for f in files), "and the one inside the ZIP too")
    check(not (folder / "eicar.com").exists(), "Defender took the threat away")
    check(all(th["status"] in ("quarantined", "removed", "blocked") for th in r.threats),
          f"and says what it did ({[th['status'] for th in r.threats]})")
    check((folder / "clean.txt").exists(), "the clean file is still there")
    last = scan.load_last()
    check(last.get("threats") == len(r.threats) and last.get("kind") == "custom", "the last scan is remembered")
    # one file
    clean = WORK / "clean-one.txt"
    clean.write_text("fine")
    r = scan.scan(scan.CUSTOM, [str(clean)])
    check(r.defender_ran and not r.threats, "a clean file: no threats")
    # where Defender is told not to look: it's still found (and left, since Defender won't touch it)
    hidden = WORK / "skipped-by-defender"
    ps(f"Add-MpPreference -ExclusionPath '{hidden}'")
    try:
        check(defender_skips(hidden, True), "Defender follows being told to skip a folder")
        eicar(hidden / "eicar.com")
        r = scan.scan(scan.CUSTOM, [str(hidden)])
        note(f"skipped folder: threats {r.threats}, skipped {r.skipped}")
        check(r.threats and any("eicar" in th["name"].lower() for th in r.threats),
              "a threat where Defender is told not to look is still found")
        there = (hidden / "eicar.com").exists()
        note(f"after the scan the file is {'still there' if there else 'gone'}")
        check(r.threats and all((th["status"] == "") == there for th in r.threats),
              "what the page says Defender did matches what happened to the file")
        # (which pass acts on it: Defender looking without acting must leave it)
        eicar(hidden / "eicar.com")
        scan.run_defender(scan.CUSTOM, [str(hidden)], threading.Event(), scan.find_mpcmdrun(), report_only=True)
        time.sleep(3)
        check((hidden / "eicar.com").exists(), "looking without acting leaves the file where it is")
        (hidden / "eicar.com").unlink(missing_ok=True)
    finally:
        ps(f"Remove-MpPreference -ExclusionPath '{hidden}'")
    # a path that isn't there
    r = scan.scan(scan.CUSTOM, [str(WORK / "nothing-here.exe")])
    check(r.defender_problem == "failed", f"a file that isn't there: Defender says it couldn't ({r.defender_code:#x})")
    said = ("Scan starting...\r\n\r\nScan finished.\r\n\r\nCleaning started...\r\n\r\nCleaning failed.\r\n\r\n"
            "CmdTool: Failed with hr = 0x80508023.")
    check(scan._CLEANING_FAILED.search(said) and not scan._CLEANING_FAILED.search("CmdTool: Failed with hr = 0x2"),
          "a scan that finished but couldn't clean up isn't called a failed scan")


def quarantine():
    items = scan.quarantine(ask_admin=False)
    note(f"quarantine: {items}")
    raw = scan._mpcmdrun(["-Restore", "-ListAll"], admin=False)[1]
    note("raw list: " + raw.strip().replace("\r", "")[:1500])
    check(items is not None and any("eicar" in i.threat.lower() for i in items),
          f"the quarantine lists the test threat ({len(items or [])} items)")
    mine = [i for i in items or [] if i.path.lower().endswith("eicar.com") and "threats" in i.path.lower()
            and "->" not in i.path]
    if not mine:
        check(False, "the quarantined file has its place")
        return
    item = mine[0]
    check(scan.restore(item), "putting it back: Defender says done")
    time.sleep(2)
    back = Path(item.path)
    check(back.exists(), "the file is back where it was")
    after = scan.quarantine(ask_admin=False) or []
    check(not any(i.path == item.path for i in after), "and isn't in the quarantine any more")
    try:
        back.unlink()
    except OSError:
        pass


def as_admin():
    """Asking Defender as administrator: no file's name can make cmd.exe run something (& ^ ; % are allowed in
    file names)."""
    exe = scan.find_mpcmdrun()
    out = str(WORK / "answer.txt")
    odd = r"C:\x\eicar&md;cf-injected&.com"
    prog, params = scan._admin_command(exe, ["-Restore", "-FilePath", odd], out)
    check(prog == exe and f'"{odd}"' in params, "a file's path is never given to cmd.exe")
    check(scan._admin_command(exe, ["-Restore", "-FilePath", 'a".exe'], out) is None,
          "an argument with a quote in it is refused")
    prog = scan._admin_command(exe, ["-Restore", "-ListAll"], out)[0]
    check(prog.lower().endswith("cmd.exe"), "fixed words still bring back Defender's answer")
    # for real: a threat whose name hides a command, quarantined, then put back as administrator
    folder = WORK / "odd-names"
    name = "eicar&md;cf-injected&.com"
    eicar(folder / name)
    scan.scan(scan.CUSTOM, [str(folder)])
    time.sleep(1)
    if (folder / name).exists():
        check(False, "the oddly named threat is quarantined")
        return
    mine = [i for i in scan.quarantine(ask_admin=False) or [] if i.path.lower().endswith(name)]
    check(mine, "and it's in the quarantine")
    places = [Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cf-injected",
              Path.cwd() / "cf-injected", folder / "cf-injected", Path.home() / "cf-injected",
              Path(exe).parent / "cf-injected"]
    if mine:
        box = {}
        th = threading.Thread(target=lambda: box.setdefault("r", scan._mpcmdrun(
            ["-Restore", "-FilePath", mine[0].path], admin=True)), daemon=True)
        th.start()
        th.join(150)
        note(f"restored as administrator: {box.get('r')}")
        check(box.get("r", (-1,))[0] == 0, "putting it back as administrator: Defender says done")
        time.sleep(2)
        check((folder / name).exists(), "the file is back")
    made = [str(p) for p in places if p.exists()]
    check(not made, f"and the command hidden in its name didn't run {made}")
    for p in places:
        shutil.rmtree(p, ignore_errors=True)
    shutil.rmtree(folder, ignore_errors=True)


def stopping():
    cancel = threading.Event()
    box = {}
    th = threading.Thread(target=lambda: box.setdefault("r", scan.scan(scan.QUICK, cancel=cancel)))
    th.start()
    end = time.time() + 30
    while time.time() < end and not mpcmdrun_running():
        time.sleep(0.2)
    check(mpcmdrun_running(), "a quick scan starts Defender")
    time.sleep(3)
    t = time.time()
    cancel.set()
    th.join(60)
    check(not th.is_alive(), f"Stop: the scan stops ({time.time() - t:.1f} s)")
    r = box.get("r")
    check(r is not None and r.stopped and r.defender_problem == "stopped", "and says it was stopped")
    time.sleep(3)
    check(not our_mpcmdrun_left(), "and Defender isn't left scanning")
    out = scan.powershell("(Get-MpComputerStatus).QuickScanEndTime")
    note(f"Defender's own quick-scan end time after stopping: {out!r}")


def quick_scan():
    """A whole quick scan, the way the PC normally is (the test PC skips its drives, which keeps it short).
    Stopped after 15 minutes so a slow PC doesn't hold everything up."""
    if EXCLUSIONS_BEFORE:
        ps("Add-MpPreference -ExclusionPath " + ",".join(f"'{p}'" for p in EXCLUSIONS_BEFORE))
    try:
        _quick_scan()
    finally:
        if EXCLUSIONS_BEFORE:
            ps("Remove-MpPreference -ExclusionPath " + ",".join(f"'{p}'" for p in EXCLUSIONS_BEFORE))
            defender_skips(WORK / "threats", False)


def _quick_scan():
    cancel = threading.Event()
    box = {}
    th = threading.Thread(target=lambda: box.setdefault("r", scan.scan(scan.QUICK, cancel=cancel)))
    t = time.time()
    th.start()
    th.join(15 * 60)
    if th.is_alive():
        cancel.set()
        th.join(60)
        note(f"quick scan: still going after 15 minutes, stopped ({time.time() - t:.0f} s)")
        check(not th.is_alive() and not our_mpcmdrun_left(), "a long quick scan stops when asked")
        return
    r = box["r"]
    took = time.time() - t
    note(f"quick scan: {took:.0f} s, code {r.defender_code:#x}, problem {r.defender_problem!r}, "
         f"{len(r.threats)} threats, {len(r.findings)} findings, {r.looked_at} files looked at")
    for f in r.findings:
        note(f"   finding: {f['kind']} {f['level']} {f['path']} {f['detail']}")
    check(r.defender_ran and r.defender_problem in ("", "cleaning"), f"a whole quick scan finishes ({took:.0f} s)")


# ----------------------------------------------------------------------------- the right-click menu

def menu():
    from corefold import ipc
    s = st.Settings()
    s.tools_off = []
    cmd = lambda *a: '"C:\\Program Files\\Corefold\\Corefold.exe" ' + " ".join(a)  # noqa: E731
    verbs = shellmenu.plan(s, cmd)
    mine = [v for v in verbs if v.name == "CorefoldScan"]
    check({v.root for v in mine} == {shellmenu.ANY_ROOT, shellmenu.FOLDER_ROOT, shellmenu.DRIVE_ROOT},
          "Scan with Corefold is on files, folders and drives")
    s.tools_off = ["scan"]
    check(not [v for v in shellmenu.plan(s, cmd) if v.name == "CorefoldScan"], "and not when Scan is removed")
    s.tools_off = ["converter"]
    verbs = shellmenu.plan(s, cmd)
    check(verbs and all(v.name == "CorefoldScan" for v in verbs), "without the Converter, only Scan's entry")
    check(ipc.message_for(["--scan", "C:\\a b\\x.exe"]) == b"scan\nC:\\a b\\x.exe", "the entry asks the app to scan")
    shellmenu.write(shellmenu.plan(st.Settings(), cmd), cmd, "")
    try:
        for root in (shellmenu.ANY_ROOT, shellmenu.FOLDER_ROOT, shellmenu.DRIVE_ROOT):
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root + r"\shell\CorefoldScan\command") as k:
                value = winreg.QueryValueEx(k, "")[0]
            check("--scan" in value and '"%1"' in value, f"written for {root.split(chr(92))[-1]}")
    finally:
        shellmenu.remove()
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, shellmenu.DRIVE_ROOT + r"\shell\CorefoldScan")
        check(False, "taking the menu away takes Scan's entry too")
    except OSError:
        check(True, "taking the menu away takes Scan's entry too")


# ----------------------------------------------------------------------------- the page, clicked like a person

def page():
    s = st.Settings()
    s.first_run = False
    s.tour_done = True
    s.check_updates = False
    s.language = LANG
    s.appearance = LOOK
    s.known_tools = sorted(t.id for t in all_tools.ready())
    s.tools_off = ["scan"]
    s.context_menu = False
    s.save()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox
    from corefold import i18n
    i18n.set_language(LANG)
    qapp = QApplication.instance() or QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    if i18n.is_rtl():
        qapp.setLayoutDirection(Qt.RightToLeft)
    from corefold import app as A
    A.setup_logging()

    def hook(t, e, tb):
        text = "".join(traceback.format_exception(t, e, tb))
        errors.append(text)
        print("ERROR " + text, flush=True)
    sys.excepthook = hook

    def settle(seconds=0.3):
        end = time.time() + seconds
        while time.time() < end:
            qapp.processEvents()
            time.sleep(0.02)

    def until(cond, seconds=10):
        end = time.time() + seconds
        while time.time() < end:
            qapp.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return False

    c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
    c.show_window("Home")
    w = c.window
    w.resize(1100, 760)
    settle(0.6)
    check(not w.shows("Scan"), "switched off: not shown")
    check("corefold.scanpage" not in sys.modules, "switched off: its page isn't loaded at all")
    c.handle_message("scan\n" + str(WORK))
    settle(1.2)
    check(getattr(c, "scan_runner", None) is None, "switched off: a right-click scan request does nothing")
    c.set_tool("scan", True)
    settle(0.6)
    w = c.window
    check(w.shows("Scan"), "switched on: shown in its section")
    w.resize(1100, 760)
    w.open_page("Scan")
    pg = w.scan_page
    check(pg is not None, "the page opens")
    check(until(lambda: pg.defender is not None, 20), "Defender's state comes up")
    settle(0.4)
    check(not pg.status_title.text().endswith("…"), f"and is shown: {pg.status_title.text()!r}")
    w.grab().save(str(OUT / f"scan-{LANG}-{LOOK}.png"))
    # a right-click "Scan with Corefold" on a folder with a threat, a disguised program and a clean file
    folder = WORK / "page"
    eicar(folder / "eicar.com")
    shutil.copy(sys.executable, folder / "letter.pdf.exe")
    (folder / "photo.jpg").write_bytes(b"\xff\xd8\xff")
    w.open_page("Home")
    settle(0.2)
    c.handle_message("scan\n" + str(folder))
    check(until(lambda: c.scan_runner.running, 5), "a right-click scan starts")
    check(until(lambda: w.current_page() == "Scan" and pg.running.isVisible(), 5),
          "and the page shows it running")
    settle(0.6)
    w.grab().save(str(OUT / f"scan-running-{LANG}-{LOOK}.png"))
    check(not pg.quick_btn.isEnabled() and not pg.full_btn.isEnabled(), "the scan buttons wait while it runs")
    check(until(lambda: not c.scan_runner.running, 120), "the scan finishes")
    settle(0.6)
    check(pg.verdict.isVisible() and not pg.running.isVisible(), "what it found is shown")
    check(len(pg.threat_rows) >= 1, f"the threat is listed ({len(pg.threat_rows)})")
    check(any(r.f["kind"] == "disguised" for r in pg.finding_rows), "the disguised program is listed")
    check(not any(os.path.basename(r.f["path"]) == "photo.jpg" for r in pg.finding_rows), "the photo isn't")
    check(pg.last_line.text() != "", f"the last scan is shown: {pg.last_line.text()!r}")
    w.grab().save(str(OUT / f"scan-found-{LANG}-{LOOK}.png"))
    pg.scroll.verticalScrollBar().setValue(pg.scroll.verticalScrollBar().maximum())
    settle(0.3)
    w.grab().save(str(OUT / f"scan-found-end-{LANG}-{LOOK}.png"))
    # "Don't show this again"
    row = next((r for r in pg.finding_rows if r.f["kind"] == "disguised"), None)
    if row is not None:
        pg.ignore(row)
        settle(0.3)
        check(not any(r.f["kind"] == "disguised" for r in pg.finding_rows), "Don't show this again: it's gone")
        r = scan.scan(scan.CUSTOM, [str(folder / "letter.pdf.exe")])
        check(not any(f["kind"] == "disguised" for f in r.findings), "and the next scan leaves it out too")
    # going away and coming back keeps what it found
    w.open_page("Home")
    settle(0.2)
    w.open_page("Scan")
    settle(0.4)
    check(pg.verdict.isVisible(), "back on the page: what it found is still there")
    # the window freed and made again while nothing runs: the page shows the last result
    # a quick scan from the button, stopped with Stop
    QTest.mouseClick(pg.quick_btn, Qt.LeftButton)
    check(until(lambda: c.scan_runner.running and pg.running.isVisible(), 5), "Quick scan: it starts")
    check(until(mpcmdrun_running, 20), "and Defender is scanning")
    settle(2)
    QTest.mouseClick(pg.stop_btn, Qt.LeftButton)
    check(until(lambda: not c.scan_runner.running, 60), "Stop: it stops")
    settle(0.5)
    check(pg.verdict.isVisible() and pg.verdict_title.text() == i18n.tr("The scan was stopped"),
          f"and says so: {pg.verdict_title.text()!r}")
    settle(2)
    check(not our_mpcmdrun_left(), "Defender isn't left scanning")
    w.grab().save(str(OUT / f"scan-stopped-{LANG}-{LOOK}.png"))
    # the quarantine
    from corefold.scanpage import QuarantineDialog
    dlg = QuarantineDialog(pg)
    dlg.show()
    check(until(lambda: not dlg.state.text().endswith("…"), 30), f"the quarantine is read: {dlg.state.text()!r}")
    settle(0.4)
    check(dlg.group.isVisible(), "and lists what's in it")
    dlg.grab().save(str(OUT / f"scan-quarantine-{LANG}-{LOOK}.png"))
    dlg.close()
    # a threat where Defender is told not to look: found, and the page says truly what happened to it
    hidden = WORK / "page-skipped"
    ps(f"Add-MpPreference -ExclusionPath '{hidden}'")
    try:
        defender_skips(hidden, True)
        eicar(hidden / "eicar.com")
        pg.scan_paths([str(hidden)])
        check(until(lambda: not c.scan_runner.running, 120), "a scan where Defender is told not to look finishes")
        settle(0.5)
        there = (hidden / "eicar.com").exists()
        check(pg.threat_rows and all((r.threat.get("status") == "") == there for r in pg.threat_rows),
              f"the threat is shown {'as still there' if there else 'as dealt with'}, as it is")
    finally:
        ps(f"Remove-MpPreference -ExclusionPath '{hidden}'")
        shutil.rmtree(hidden, ignore_errors=True)
    # a threat Defender left where it is: "Delete it…" (a harmless file stands in for it)
    left = WORK / "left-behind" / "notes.txt"
    left.parent.mkdir(parents=True, exist_ok=True)
    left.write_text("a stand-in")
    pg.result = scan.ScanResult(scan.CUSTOM, [str(left.parent)], time.time(), time.time(), defender_ran=True,
                                threats=[{"name": "Test:Left/Behind", "files": [str(left)], "status": "",
                                          "severity": "", "category": ""}])
    pg._show_result(pg.result)
    settle(0.4)
    still = [r for r in pg.threat_rows if r.threat.get("status") == ""]
    check(still, "a threat Defender left is shown as still there")
    w.grab().save(str(OUT / f"scan-still-there-{LANG}-{LOOK}.png"))
    if still:
        real = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        try:
            pg.delete_threat(still[0])
        finally:
            QMessageBox.question = real
        settle(0.4)
        check(not left.exists(), "Delete it…: the file is gone")
        check(pg.threat_rows and pg.threat_rows[0].threat.get("status") == "deleted", "and the row says so")
    for size in ((760, 540), (1500, 900)):
        w.resize(*size)
        settle(0.5)
        pg.scroll.verticalScrollBar().setValue(0)
        settle(0.2)
        sticking = [b.text() for b in (pg.quick_btn, pg.full_btn, pg.file_btn, pg.folder_btn, pg.security_btn,
                                       pg.quarantine_btn)
                    if b.isVisible() and b.mapTo(w, b.rect().topRight()).x() > w.width() + 1]
        check(not sticking, f"at {size[0]}×{size[1]} every button is inside the window {sticking}")
        cut = [b.text() for b in (pg.quick_btn, pg.full_btn, pg.file_btn, pg.folder_btn)
               if b.width() < b.sizeHint().width() - 2]
        check(not cut, f"at {size[0]}×{size[1]} no button's words are cut {cut}")
        w.grab().save(str(OUT / f"scan-{size[0]}-{LANG}-{LOOK}.png"))
    # removed while a scan runs: the scan stops too
    pg.start(scan.QUICK)
    check(until(mpcmdrun_running, 20), "a scan is running")
    c.set_tool("scan", False)
    settle(1)
    check(not c.window.shows("Scan"), "switched off again: not shown")
    check(until(lambda: not our_mpcmdrun_left(), 30), "and its scan stopped with it")
    check(not errors, f"no errors behind any click ({len(errors)})")


def tidy():
    subprocess.run(["taskkill", "/F", "/IM", "MpCmdRun.exe"], capture_output=True)
    if EXCLUSIONS_BEFORE:
        ps("Add-MpPreference -ExclusionPath " + ",".join(f"'{p}'" for p in EXCLUSIONS_BEFORE))
    shutil.rmtree(WORK, ignore_errors=True)


parts = [("state", state), ("signatures", signatures), ("names", names), ("hosts", hosts), ("startup", startup),
         ("more places", more_places), ("running", running), ("menu", menu), ("Defender scans", defender_scans), ("quarantine", quarantine),
         ("as administrator", as_admin), ("stopping", stopping)]
if QUICK:
    parts.append(("quick scan", quick_scan))
parts.append(("page", page))
try:
    for name, fn in parts:
        step(name, fn)
finally:
    step("tidy", tidy)
(OUT / f"scantest-{LANG}.txt").write_text("\n".join(problems) or "none", "utf-8")
print(f"scantest: {len(problems)} problem(s)", flush=True)
faulthandler.disable()
os._exit(1 if problems else 0)
