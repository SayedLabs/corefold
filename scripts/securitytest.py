"""Security checks on a real Windows PC: each one first shows the weakness the old way (where it can be
shown safely) and then that Corefold's way is safe. Covers the helper that runs as administrator (the
restore point's description, links on the way, icacls going into junctions, backed-up registry files,
scheduled task names, Startup's names), programs started by name (also: the folder Corefold was started
in, and no program of Windows' own started by its name alone anywhere in the code), cloud downloads,
Unlock's delete-at-restart, booby-trapped pictures (EPS through Ghostscript) and videos that would go
online when played.

Run as administrator (GitHub's Windows runners are): python scripts/securitytest.py
Prints one line per check, then "securitytest: N problem(s)".
"""
import ast
import http.server
import io
import os
import re
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

from corefold import cloud, ipc  # noqa: E402
from corefold import startup as st  # noqa: E402
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
    got = BASE / "description.txt"
    un.powershell(f"[IO.File]::WriteAllText('{got}', $env:CF_DESCRIPTION)", env={"CF_DESCRIPTION": desc})
    check(not marker.exists(), "Corefold's way: nothing in the name runs")
    check(got.exists() and got.read_text("utf-8-sig") == desc, "…and the description arrives exactly as it was")


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
        note(f"Task Scheduler takes a doubled backslash for the plain name: {r.returncode == 0}")


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
                       ("CON", "_CON"), ("photo.jpg", "photo.jpg"), ("invoice\u202egpj.exe", "invoicegpj.exe")):
        got = cloud.local_name(name)
        check(got == want, f"cloud name {name!r} is saved as {got!r}")
        folder = TMP / "cloud"
        folder.mkdir(exist_ok=True)
        check((folder / got).resolve().parent == folder.resolve(), f"…and stays in its folder ({name!r})")
    target = TMP / "cloud" / "invoice.pdf.exe"
    cloud._stream_to(io.BytesIO(b"MZ" + b"\0" * 100), target, 102, None, None, cloud.HOSTS["onedrive"])
    mark = zone_mark(target) or b""
    check(target.exists() and b"ZoneId=3" in mark, "a cloud download gets Windows' 'from the internet' mark")


def started_in_downloads():
    """Started by a right-click on a file in Downloads, Corefold used to keep Downloads as its current
    folder, where Windows looks first for a program started by its name."""
    folder = TMP / "clicked-in"
    folder.mkdir()
    shutil.copy(system_tool("hostname.exe"), folder / "reg.exe")
    (folder / "photo.jpg").write_bytes(b"x")
    here = os.getcwd()
    os.chdir(folder)
    try:
        hostname = subprocess.run([system_tool("hostname.exe")], capture_output=True, text=True).stdout.strip()
        r = subprocess.run(["reg.exe", "/?"], capture_output=True, text=True)
        # (Windows only skips the current folder when NoDefaultCurrentDirectoryInExePath is set, as it
        # may be on this runner; on most PCs it isn't)
        note(f"the old way: started in Downloads, a planted reg.exe ran: {r.stdout.strip() == hostname} "
             f"(NoDefaultCurrentDirectoryInExePath={os.environ.get('NoDefaultCurrentDirectoryInExePath')!r})")
        argv = ["Corefold.exe", "--view", "photo.jpg", "--page", "photo.jpg"]
        ipc.leave_start_folder(argv)
        check(Path(os.getcwd()).resolve() != folder.resolve(), "Corefold leaves the folder it was started in")
        check(argv[2] == str(folder / "photo.jpg"), f"…and the file it was given keeps its full path ({argv[2]})")
        check(argv[4] == "photo.jpg", "…(a page's name stays a name)")
        r = subprocess.run(["reg.exe", "/?"], capture_output=True, text=True)
        check(r.stdout.strip() != hostname, "…so the planted reg.exe doesn't run any more")
    finally:
        os.chdir(here)


# programs of Windows' own the code may start by name only on purpose (none, for now)
BY_NAME_OK: set[str] = set()


def _full_path_first(arg) -> bool:
    """f'"{system_tool("explorer.exe")}" /select,…': one string, but the program is Windows' own."""
    parts = arg.values if isinstance(arg, ast.JoinedStr) else []
    return (len(parts) >= 2 and isinstance(parts[0], ast.Constant) and parts[0].value == '"'
            and isinstance(parts[1], ast.FormattedValue) and isinstance(parts[1].value, ast.Call)
            and getattr(parts[1].value.func, "id", "") == "system_tool")


def programs_in_the_code():
    """Every subprocess call in corefold/: the program is never just a name like "explorer.exe" (Windows
    would look in Corefold's own and the current folder first), and nothing goes through a shell."""
    found = []
    for path in sorted((ROOT / "corefold").rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
            if name not in ("run", "Popen", "call", "check_call", "check_output"):
                continue
            base = fn.value.id if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) else ""
            if base not in ("subprocess", "sp"):
                continue
            where = f"{path.relative_to(ROOT)}:{node.lineno}"
            for kw in node.keywords:
                if kw.arg == "shell" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                    found.append(f"{where} uses shell=")
            if node.args and isinstance(node.args[0], (ast.List, ast.Tuple)) and node.args[0].elts:
                first = node.args[0].elts[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    prog = first.value
                    if prog not in BY_NAME_OK and not re.search(r"[\\/]", prog) and prog != "xdg-open":
                        found.append(f"{where} starts {prog!r} by its name alone")
            if node.args and isinstance(node.args[0], (ast.Constant, ast.JoinedStr)) and not _full_path_first(node.args[0]):
                found.append(f"{where} gives a command as one string, not starting with a full path")
    for f in found:
        note(f)
    check(not found, f"no program started by its name alone, and no shell ({len(found)} found)")


def booby_trapped_pictures():
    """An EPS file named photo.jpg: plain Pillow opens it as EPS, and drawing it runs Ghostscript (the
    one in the current folder or on PATH). Corefold's Pillow doesn't know EPS at all."""
    pic = TMP / "photo.jpg"
    pic.write_bytes(b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\nnewpath 0 0 moveto 10 10 lineto stroke\n"
                    b"showpage\n")
    r = subprocess.run([sys.executable, "-I", "-c", "import sys; from PIL import Image; "
                        "print(Image.open(sys.argv[1]).format)", str(pic)], capture_output=True, text=True)
    check(r.stdout.strip() == "EPS", "the old way: plain Pillow takes photo.jpg for EPS (proof of the problem)")
    from PIL import Image, UnidentifiedImageError
    from corefold.converters import setup_pillow
    setup_pillow()
    try:
        with Image.open(pic) as im:
            got = im.format
    except UnidentifiedImageError:
        got = None
    check(got is None, f"Corefold's Pillow refuses it (got {got})")
    ok = TMP / "real.png"
    Image.new("RGB", (8, 8), "red").save(ok)
    with Image.open(ok) as im:
        check(im.format == "PNG" and im.size == (8, 8), "…and real pictures still open")


def startup_names():
    bad = "Updater\r\n[HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run]\r\n\"x\"=\"calc.exe\""
    key = "HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run"
    text = st._reg_text([(key, "Fine \"one\""), (key, bad)])
    check("calc.exe" not in text and text.count("[") == 1, "a Startup name with a line break can't add lines to the .reg file")
    check('"Fine \\"one\\""=-' in text, "…a plain name still goes in")
    check(not st.plain_name(bad) and st.plain_name("OneDrive"), "plain_name tells them apart")


class _Count(http.server.BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        _Count.hits += 1
        self.send_response(404)
        self.end_headers()

    def log_message(self, *a):
        pass


def videos_going_online():
    """A "video" that is really a playlist of parts on the internet: a player that fetched them would tell
    that site the file was opened. Played from the PC, Corefold's player stays offline."""
    import threading
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtWidgets import QApplication as QGuiApplication  # (widgets are made later on)
    from PySide6.QtMultimedia import QMediaPlayer
    app = QGuiApplication.instance() or QGuiApplication([])
    server = http.server.HTTPServer(("127.0.0.1", 0), _Count)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    playlist = ("#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\n"
                f"http://127.0.0.1:{port}/part0.ts\n#EXTINF:2,\nhttp://127.0.0.1:{port}/part1.ts\n#EXT-X-ENDLIST\n")
    fake = TMP / "holiday.mp4"      # (FFmpeg itself won't take a playlist called .mp4)
    fake.write_text(playlist)
    named = TMP / "holiday.m3u8"    # (one called what it is, opened with Corefold's player)
    named.write_text(playlist)

    def play(path: Path, seconds: float = 6.0):
        player = QMediaPlayer()
        player.setSource(QUrl.fromLocalFile(str(path)))
        player.play()
        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()
        status, duration = player.mediaStatus(), player.duration()
        player.stop()
        player.setSource(QUrl())
        player.deleteLater()
        app.processEvents()
        return status, duration

    from corefold import player as corefold_player
    os.environ.pop("QT_FFMPEG_PROTOCOL_WHITELIST", None)
    _Count.hits = 0
    play(fake)
    note(f"the old way: a playlist called .mp4 asked the internet for its parts {_Count.hits} time(s)")
    _Count.hits = 0
    play(named)
    note(f"the old way: a playlist called .m3u8 asked the internet for its parts {_Count.hits} time(s)")
    # (on GitHub's runner Qt's FFmpeg stayed offline by itself both times; the whitelist is a second lock)
    corefold_player.local_only()
    for f in (fake, named):
        _Count.hits = 0
        play(f)
        check(_Count.hits == 0, f"Corefold's player stays offline with {f.name} ({_Count.hits} asks)")
    ffmpeg = find_program("ffmpeg")
    if ffmpeg:
        real = TMP / "real.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=160x120:rate=10",
                        "-f", "lavfi", "-i", "sine=duration=3", "-shortest", "-pix_fmt", "yuv420p", str(real)],
                       capture_output=True, timeout=120)
        status, duration = play(real, 4.0)
        check(real.exists() and duration > 2000, f"…and a real video still plays offline ({duration} ms, {status})")
    else:
        note("no FFmpeg on PATH: the real-video part was skipped")
    corefold_player.local_only(False)
    server.shutdown()


def cloud_folders_with_junctions():
    """A folder uploaded to the cloud, or a cloud folder on this PC, with a junction inside: what it points
    at (another folder, or the folder itself: round in a circle) is never taken along."""
    from corefold.cloudlocal import Local
    outside = BASE / "cloud-outside"
    outside.mkdir()
    (outside / "private.txt").write_text("not for the cloud")
    mine = BASE / "cloud-mine"
    mine.mkdir()
    (mine / "holiday.txt").write_text("fine")
    junction(mine / "elsewhere", outside)
    junction(mine / "round", mine)

    class Fake:
        def __init__(self):
            self.sent = []

        def new_folder(self, name, folder):
            return cloud.Entry(name, True, name)

        def upload(self, src, folder, progress=None, cancel=None):
            self.sent.append(src.name)

    fake = Fake()
    cloud.upload_tree(fake, mine, None)
    check(fake.sent == ["holiday.txt"], f"uploading a folder takes only its own files ({fake.sent})")
    local = Local(cloud.Account("onedrive", "", extra={"local": str(mine)}))
    names = sorted(e.name for e in local.list(None))
    check(names == ["holiday.txt"], f"a cloud folder on this PC doesn't show its junctions ({names})")
    got = BASE / "cloud-got"
    got.mkdir()
    n = cloud.download_tree(local, cloud.Entry("cloud-mine", True, str(mine)), got)
    check(n == 1 and not (got / "cloud-mine" / "elsewhere").exists(), f"…nor copies what they point at ({n} file(s))")


def delete_at_restart_junctions():
    """Unlock's "Delete when the PC restarts" runs as administrator: a folder with a junction inside (to
    another folder, or to Windows) only loses the junction itself, never what it points at, and nothing
    is taken through a junction, nor from Windows, programs' folders or a whole drive."""
    from corefold import unlock
    outside = BASE / "unlock-outside"
    outside.mkdir()
    (outside / "private.txt").write_text("not to be deleted")
    box = BASE / "unlock-box"
    box.mkdir()
    (box / "stuck.txt").write_text("delete me")
    junction(box / "elsewhere", outside)
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    junction(box / "windows", windir)
    order = unlock.at_restart_order([str(box)])
    norm = {os.path.normcase(x) for x in order}
    want = {os.path.normcase(str(x)) for x in (box / "stuck.txt", box / "elsewhere", box / "windows", box)}
    check(norm == want, f"a folder with junctions inside: only its own things and the junctions ({len(order)})")
    check(os.path.normcase(order[-1]) == os.path.normcase(str(box)), "…the folder itself last")
    check(unlock.at_restart_order([str(box / "elsewhere")]) == [str(box / "elsewhere")],
          "a junction chosen on its own is only the junction")
    allowed = unlock.at_restart_allowed
    check(allowed(str(box)) and allowed(str(box / "elsewhere")), "the chosen folder and its junctions are allowed")
    check(not allowed(str(box / "elsewhere" / "private.txt")), "a file reached through a junction is refused")
    check(not allowed(str(box / "windows" / "notepad.exe")), "…Windows reached through a junction too")
    for place in (windir, windir / "System32", Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
                  Path(os.environ.get("SystemDrive", "C:") + "\\")):
        check(not allowed(str(place)), f"{place} is refused")


def pictures_from_elsewhere():
    """An email, a drawing, a label or a tooltip naming a picture by a path (a shared folder's
    \\\\server\\share\\x.png makes Windows sign in to that computer): the old way really opened it, Corefold's
    way opens nothing. A local share of this PC (\\\\localhost\\C$) stands in for another computer."""
    from PySide6.QtGui import QImage, QColor, QTextDocument
    from PySide6.QtWidgets import QApplication, QLabel, QListWidgetItem, QTextBrowser
    app = QApplication.instance() or QApplication([])
    from corefold import mail, mailview, safetext, updater
    probe = BASE / "probe.png"
    img = QImage(200, 200, QImage.Format_RGB32)
    img.fill(QColor("red"))
    img.save(str(probe))
    unc = "\\\\localhost\\" + str(probe).replace(":", "$", 1)
    for where in (probe.as_uri(), unc):
        page = f'<img src="{where}">'

        class Old(QTextBrowser):   # what 1.9.0 did: None for anything not in the email
            def loadResource(self, kind, url):
                return None
        old = Old()
        old.setHtml(page)
        old.resize(400, 400)
        old.show()   # (a text view only loads its pictures once it's laid out on screen)
        app.processEvents()
        check(old.document().size().height() > 150, f"the old way really opened {where[:40]}… (proof of the problem)")
        body = mailview.MailBody(mail.Message(html=page))
        body.setHtml(mailview.safe_html(page))
        body.resize(400, 400)
        body.show()
        app.processEvents()
        check(body.document().size().height() < 50, f"an email's picture at {where[:40]}… isn't opened")
        doc = safetext.offline_document()
        doc.setHtml(page)
        check(doc.size().height() < 50, "nor when the email is saved as PDF")
        plain = QTextDocument()
        plain.setHtml(page)
        check(plain.size().height() > 150, "a plain text document really opens it (proof of the problem)")
        old.close()
        body.close()
    safetext.guard()
    label = QLabel(f'Hi <img src="{probe.as_uri()}">')
    check(label.sizeHint().height() < 60, "a label with a name that looks like HTML shows it as text")
    item = QListWidgetItem()
    item.setToolTip(f'<img src="{unc}">')
    check(item.toolTip().startswith("<p") and "&lt;img" in item.toolTip(), "so does a tooltip")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="200" '
           f'height="200"><image width="200" height="200" xlink:href="{unc}"/></svg>').encode()
    clean = safetext.safe_svg(svg)
    check(b"localhost" not in clean, "a drawing's picture on a shared folder is taken out")
    entity = (b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY p "' + unc.encode() + b'">]><svg xmlns="http://www.w3.org/'
              b'2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><image xlink:href="&p;"/></svg>')
    check(b"localhost" not in safetext.safe_svg(entity), "also when it hides behind an XML entity")
    check(safetext.raster_picture(svg).isNull(), "a drawing given as a picture's bytes isn't drawn")
    try:
        updater.parse_release({"tag_name": "1.99.0<img src=x>", "assets": []}, "SayedLabs/corefold")
        check(False, "an odd version from GitHub is refused")
    except updater.UpdateError:
        check(True, "an odd version from GitHub is refused")
    try:
        cloud._request_once("GET", "file:///C:/Windows/win.ini", "", None, None, 5, True)
        check(False, "a cloud address that isn't https is refused")
    except cloud.CloudError:
        check(True, "a cloud address that isn't https is refused")
    check(ipc.same_user(os.getpid()), "the pipe check knows this account's own process")


step("restore point description", restore_point_description)
step("icacls and junctions", icacls_into_junctions)
step("links on the way", links_on_the_way)
step("backed-up registry files", registry_files)
step("scheduled task names", task_names)
step("Store package names", store_names)
step("programs started by name", programs_by_name)
step("cloud downloads", cloud_downloads)
step("cloud folders with junctions", cloud_folders_with_junctions)
step("delete at restart and junctions", delete_at_restart_junctions)
step("started from a folder", started_in_downloads)
step("programs in the code", programs_in_the_code)
step("Startup's names", startup_names)
step("booby-trapped pictures", booby_trapped_pictures)
step("videos that go online", videos_going_online)
step("pictures from elsewhere", pictures_from_elsewhere)

shutil.rmtree(BASE, ignore_errors=True)
shutil.rmtree(TMP, ignore_errors=True)
print(f"securitytest: {len(problems)} problem(s)")
for p in problems:
    print("  - " + p)
sys.exit(1 if problems else 0)
