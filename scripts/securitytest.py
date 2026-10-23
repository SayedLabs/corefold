"""Security checks on a real Windows PC: each one first shows the weakness the old way (where it can be
shown safely) and then that Corefold's way is safe. Covers the helper that runs as administrator (the
restore point's description, links on the way, icacls going into junctions, backed-up registry files,
scheduled task names), programs started by name, and cloud downloads.

Run as administrator (GitHub's Windows runners are): python scripts/securitytest.py
Prints one line per check, then "securitytest: N problem(s)".
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BASE = Path(os.environ.get("SystemDrive", "C:") + "\\") / "corefold-securitytest"
shutil.rmtree(BASE, ignore_errors=True)
BASE.mkdir()
TMP = Path(tempfile.mkdtemp(prefix="corefold-securitytest-"))
os.environ["LOCALAPPDATA"] = str(TMP / "local")
os.environ["APPDATA"] = str(TMP / "roaming")

from corefold import cloud  # noqa: E402
from corefold import uninstall as un  # noqa: E402
from corefold.core import find_program, is_link, system_tool, zone_mark  # noqa: E402

problems = []


def check(ok, what):
    print(("ok      " if ok else "PROBLEM ") + what, flush=True)
    if not ok:
        problems.append(what)


def note(what):
    print("note    " + what, flush=True)


def step(name, fn):
    print(f"== {name}", flush=True)
    try:
        fn()
    except Exception:
        traceback.print_exc()
        problems.append(f"{name} crashed")


def junction(link: Path, target: Path):
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=True)


def acl(path: Path) -> str:
    return subprocess.run([system_tool("icacls.exe"), str(path)], capture_output=True, text=True).stdout


def restore_point_description():
    """The restore point's description holds the app's name, and the helper runs it in PowerShell as
    administrator. Only ' was taken out; PowerShell takes ’ ‘ ‚ ‛ as quotes too."""
    marker = BASE / "injected.txt"
    name = f"X\u2019; New-Item -ItemType File '{marker}' | Out-Null; \u2019"
    desc = f"Before uninstalling {name}"
    old = desc.replace("'", "")  # what 1.8.0 did (without the 60 letters limit, to keep the path)
    subprocess.run([system_tool("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command",
                    f"Write-Output '{old}'"], capture_output=True, timeout=60)
    note(f"old way: a name with ’ ran a command: {marker.exists()}")
    check(marker.exists(), "the old way really let an app's name run a command (proof of the problem)")
    marker.unlink(missing_ok=True)
    out = un.powershell("Write-Output $env:CF_DESCRIPTION", env={"CF_DESCRIPTION": desc})
    check(not marker.exists(), "Corefold's way: nothing in the name runs")
    check(out == desc, "…and the description arrives exactly as it was")


def icacls_into_junctions():
    """After moving a folder out of Program Files as administrator, its permissions were reset with
    icacls /T /L. A junction inside (any account can make one, e.g. in C:\\ProgramData) points elsewhere."""
    target = BASE / "secret"
    target.mkdir()
    data = target / "data.txt"
    data.write_text("x")
    lock = [system_tool("icacls.exe"), str(data), "/inheritance:r", "/grant:r", "*S-1-5-32-544:F", "*S-1-5-18:F"]
    subprocess.run(lock, capture_output=True, check=True)  # its own permissions: nothing inherited
    check("(I)" not in acl(data), "the file behind the junction starts with only its own permissions")
    moved = BASE / "moved"
    moved.mkdir()
    (moved / "app.exe").write_text("x")
    junction(moved / "link", target)
    subprocess.run([system_tool("icacls.exe"), str(moved), "/reset", "/T", "/C", "/L", "/Q"], capture_output=True)
    followed = "(I)" in acl(data)
    note(f"icacls /reset /T /L went through the junction and reset the file behind it: {followed}")
    print(acl(data).strip(), flush=True)
    subprocess.run(lock, capture_output=True, check=True)
    un.reset_permissions(moved)
    check("(I)" not in acl(data), "Corefold's way leaves what a junction inside points at alone")
    check("(I)" in acl(moved / "app.exe"), "…and still resets the real files")
    os.rmdir(moved / "link")


def links_on_the_way():
    """The helper running as administrator never works through a link on the way to a leftover."""
    real = BASE / "elsewhere"
    (real / "AcmeTool").mkdir(parents=True)
    (real / "AcmeTool" / "file.txt").write_text("keep me")
    vendor = BASE / "AcmeVendor"
    junction(vendor, real)
    leftover = vendor / "AcmeTool"
    check(is_link(vendor) and un.through_link(leftover), "a junction on the way is seen")
    check(not un.through_link(real / "AcmeTool"), "a plain path isn't")
    folder = un.new_backup(["sectest"])
    res = un.remove([un.Leftover("folder", str(leftover), "data")], folder, ["sectest"], admin=True)
    check(bool(res["failed"]) and (real / "AcmeTool" / "file.txt").exists(),
          "as administrator, a leftover behind a junction isn't moved")
    os.rmdir(vendor)
    res = un.remove([un.Leftover("folder", str(real / "AcmeTool"), "data")], folder, ["sectest"], admin=True)
    check(not res["failed"] and not (real / "AcmeTool").exists(), "…while the same folder by its real path is")
    r = un.restore(folder, admin=True)
    check(not r["failed"] and (real / "AcmeTool" / "file.txt").exists(), "…and is put back")


def registry_files():
    """A backed-up .reg file is put back as administrator; only the item's own key may be in it."""
    folder = TMP / "reg"
    folder.mkdir()
    item = {"kind": "key", "path": "HKLM\\Software\\AcmeSecTest"}
    good = "Windows Registry Editor Version 5.00\r\n\r\n[HKEY_LOCAL_MACHINE\\Software\\AcmeSecTest]\r\n\"a\"=\"b\"\r\n"
    (folder / "good.reg").write_bytes(b"\xff\xfe" + good.encode("utf-16-le"))
    check(un._reg_file_ok(folder / "good.reg", item), "a .reg file like reg.exe writes is taken")
    (folder / "plain.reg").write_text(good.replace("Windows Registry Editor Version 5.00", "REGEDIT4"), "utf-8")
    check(not un._reg_file_ok(folder / "plain.reg", item), "a plain-text .reg file isn't")
    bad = good + "\r\n[HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Services\\Evil]\r\n\"ImagePath\"=\"x\"\r\n"
    (folder / "bad.reg").write_bytes(b"\xff\xfe" + bad.encode("utf-16-le"))
    check(not un._reg_file_ok(folder / "bad.reg", item), "a .reg file with another key in it isn't")
    exported = folder / "exported.reg"
    un._reg_exe("export", "HKEY_CURRENT_USER\\Software\\Microsoft\\Notepad", str(exported), "/y")
    if exported.exists():
        check(un._reg_file_ok(exported, {"kind": "key", "path": "HKCU\\Software\\Microsoft\\Notepad"}),
              "a file reg.exe export really wrote is taken")
    value = folder / "value.reg"
    value.write_text(un.value_to_reg("HKCU\\" + un.RUN_KEYS[0], "Acme", "C:\\x.exe", 1), "utf-16")
    check(un._reg_file_ok(value, {"kind": "value", "path": "HKCU\\" + un.RUN_KEYS[0], "value": "Acme"}),
          "a startup value Corefold backed up is taken")


def task_names():
    for name in ("\\\\Microsoft\\Windows\\Defrag\\ScheduledDefrag", "\\Microsoft \\Windows\\X",
                 "\\microsoft\\X", "\\Acme\\..\\Microsoft\\X", "\\Acme\\X\"", "Acme"):
        check(not un.item_allowed({"kind": "task", "path": name}), f"task {name!r} is refused")
    for name in ("\\Acme Updater", "\\Acme\\Update {1234-ABCD}", "\\MicrosoftEdgeUpdateTaskMachineCore"):
        check(un.item_allowed({"kind": "task", "path": name}), f"task {name!r} is taken")
    if os.name == "nt":
        r = subprocess.run([system_tool("schtasks.exe"), "/Query", "/TN", "\\\\Microsoft\\Windows\\Defrag\\ScheduledDefrag"],
                           capture_output=True)
        note(f"Task Scheduler reads a doubled backslash as the plain name: {r.returncode == 0}")


def store_names():
    check(bool(un._PACKAGE_NAME.match("Microsoft.WindowsCalculator_11.2405.2.0_x64__8wekyb3d8bbwe")),
          "a real Store package name is taken")
    check(not un._PACKAGE_NAME.match("x' ; calc ; '"), "one with a quote isn't")


def programs_by_name():
    """A program started by its name alone is looked for in the current folder too."""
    folder = TMP / "downloads"
    folder.mkdir()
    shutil.copy(system_tool("hostname.exe"), folder / "reg.exe")
    shutil.copy(system_tool("hostname.exe"), folder / "7z.exe")
    here = os.getcwd()
    os.chdir(folder)
    try:
        hostname = subprocess.run([system_tool("hostname.exe")], capture_output=True, text=True).stdout.strip()
        r = subprocess.run(["reg.exe", "/?"], capture_output=True, text=True)
        note(f"\"reg.exe\" by name ran the one in the current folder: {r.stdout.strip() == hostname}")
        r = subprocess.run([system_tool("reg.exe"), "/?"], capture_output=True, text=True)
        check(r.stdout.strip() != hostname and "REG" in r.stdout.upper(), "Corefold's way runs Windows' reg.exe")
        old_path = os.environ["PATH"]
        os.environ["PATH"] = os.pathsep.join(p for p in old_path.split(os.pathsep) if "7-zip" not in p.lower())
        try:
            found = shutil.which("7z")
            note(f"shutil.which('7z') picked the current folder's: {bool(found) and Path(found).parent == folder}")
            mine = find_program("7z")
            check(not mine or Path(mine).parent != folder, "find_program never picks the current folder's")
        finally:
            os.environ["PATH"] = old_path
    finally:
        os.chdir(here)


def cloud_downloads():
    for name, want in (("..\\..\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\x.bat", "x.bat"),
                       ("C:\\Windows\\x.dll", "x.dll"), ("C:x.exe", "C_x.exe"), ("a/b.txt", "b.txt"), ("..", "file"),
                       ("CON", "_CON"), ("photo.jpg", "photo.jpg")):
        got = cloud.local_name(name)
        check(got == want, f"cloud name {name!r} is saved as {got!r}")
        folder = TMP / "cloud"
        folder.mkdir(exist_ok=True)
        check((folder / got).resolve().parent == folder.resolve(), f"…and stays in its folder ({name!r})")
    target = TMP / "cloud" / "invoice.pdf.exe"
    cloud._stream_to(io.BytesIO(b"MZ" + b"\0" * 100), target, 102, None, None, cloud.HOSTS["onedrive"])
    mark = zone_mark(target) or b""
    check(target.exists() and b"ZoneId=3" in mark, "a cloud download gets Windows' 'from the internet' mark")


step("restore point description", restore_point_description)
step("icacls and junctions", icacls_into_junctions)
step("links on the way", links_on_the_way)
step("backed-up registry files", registry_files)
step("scheduled task names", task_names)
step("Store package names", store_names)
step("programs started by name", programs_by_name)
step("cloud downloads", cloud_downloads)

shutil.rmtree(BASE, ignore_errors=True)
shutil.rmtree(TMP, ignore_errors=True)
print(f"securitytest: {len(problems)} problem(s)")
for p in problems:
    print("  - " + p)
sys.exit(1 if problems else 0)
