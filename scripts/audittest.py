"""Checks for the data-safety fixes: each one first makes the problem happen the way it could for real
(a crash in the middle of a sort, a different file in a moved file's place, a cut-off download, a ZIP
bomb, a password on 7-Zip's command line, deleted clipboard copies still in the file, a "Recycle Bin"
delete that would be for good) and then checks that Corefold handles it safely.

Runs on Windows (the parts that need 7-Zip or the Recycle Bin are left out elsewhere):
    python scripts/audittest.py
Prints one line per check, then "audittest: N problem(s)".
"""
import base64
import http.server
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="corefold-audittest-"))
os.environ["LOCALAPPDATA"] = str(TMP / "local")
os.environ["APPDATA"] = str(TMP / "roaming")
WINDOWS = os.name == "nt"
# folders to sort: not in the temp folder (it's inside AppData, which the Organizer never sorts)
# (a name of its own each run, made new: a folder that was already there is never used, let alone deleted)
BASE = Path(tempfile.mkdtemp(prefix="corefold-audittest-", dir=os.environ.get("SystemDrive", "C:") + "\\")) \
    if WINDOWS else TMP / "files"
BASE.mkdir(parents=True, exist_ok=True)

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


def fresh(name: str) -> Path:
    d = BASE / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


# ----------------------------------------------------------------------------- File Organizer

CRASH_AFTER = 150  # (the journal used to be saved every 200 moves only)

_CHILD = r'''
import os, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from corefold import organizer
real, n = os.rename, [0]
def rename(a, b):
    real(a, b)
    n[0] += 1
    if n[0] >= int(sys.argv[3]):
        os._exit(9)  # Corefold stops dead, as in a crash or a power cut
organizer.os.rename = rename
organizer.sort(organizer.plan(Path(sys.argv[2]), check_in_use=False))
'''


def organizer_crash():
    from corefold import organizer
    folder = fresh("sort-crash")
    for i in range(300):
        (folder / f"photo {i}.jpg").write_bytes(b"x" * (i + 1))
    child = TMP / "sortchild.py"
    child.write_text(_CHILD, encoding="utf-8")
    code = subprocess.run([sys.executable, str(child), str(ROOT), str(folder), str(CRASH_AFTER)],
                          env=dict(os.environ)).returncode
    check(code == 9, f"the sort was stopped dead after {CRASH_AFTER} files (exit {code})")
    moved = [p for p in folder.rglob("*.jpg") if p.parent != folder]
    check(len(moved) == CRASH_AFTER, f"{len(moved)} files were moved before it stopped")
    last = organizer.last_sort()
    check(last is not None and len(last["moves"]) == CRASH_AFTER,
          f"after the crash, Undo knows every move ({len(last['moves']) if last else 0} of {CRASH_AFTER})")
    res = organizer.undo()
    loose = sorted(p.name for p in folder.iterdir() if p.is_file())
    check(res.back == CRASH_AFTER and len(loose) == 300, f"Undo put all {res.back} back (loose files: {len(loose)})")
    check(not any(p.is_dir() for p in folder.iterdir()), "the folder it made is gone again")
    check(not (TMP / "roaming" / "Corefold" / organizer.JOURNAL_FILE).exists(), "the journal was cleared")


def organizer_identity():
    from corefold import organizer
    folder = fresh("sort-identity")
    (folder / "holiday.jpg").write_bytes(b"the real holiday photo")
    (folder / "notes.txt").write_text("my notes")
    res = organizer.sort(organizer.plan(folder, check_in_use=False))
    check(res.moved == 2, f"sorted {res.moved} files")
    last = organizer.last_sort()
    pic = Path(next(m[1] for m in last["moves"] if m[1].endswith("holiday.jpg")))
    # the person deletes the sorted photo; later, a different file arrives with the same name and place
    pic.unlink()
    time.sleep(0.05)
    pic.write_bytes(b"someone else's file that happens to have this name")
    res = organizer.undo()
    check(pic.exists() and pic.read_bytes().startswith(b"someone else's"),
          "Undo left the different file where it is")
    check(not (folder / "holiday.jpg").exists(), "and didn't move it to the old photo's place")
    check(res.changed == 1 and res.back == 1, f"counted as changed (changed {res.changed}, back {res.back})")
    check((folder / "notes.txt").exists(), "the untouched file still went back")
    # same size and time, but another file on the disk (the photo copied back over by hand, then the
    # original deleted): left alone too where the disk gives files a lasting number
    folder = fresh("sort-identity2")
    (folder / "a.png").write_bytes(b"12345")
    organizer.sort(organizer.plan(folder, check_in_use=False))
    moved = Path(organizer.last_sort()["moves"][0][1])
    st = moved.stat()
    other = moved.with_name("other.tmp")
    other.write_bytes(b"54321")
    os.utime(other, ns=(st.st_atime_ns, st.st_mtime_ns))
    moved.unlink()
    os.rename(other, moved)
    if moved.stat().st_ino:
        res = organizer.undo()
        check(res.changed == 1 and moved.exists(), "same size and time but another file: left alone")
    else:
        note("this disk has no lasting file numbers")
        organizer.undo()
    # a sort saved by an older version ([from, to] only) can still be undone
    folder = fresh("sort-old")
    (folder / "Pictures").mkdir()
    (folder / "Pictures" / "b.jpg").write_bytes(b"b")
    state = {"last": {"folder": str(folder), "time": time.time(),
                      "moves": [[str(folder / "b.jpg"), str(folder / "Pictures" / "b.jpg")]],
                      "made": [str(folder / "Pictures")]}}
    (TMP / "roaming" / "Corefold" / "organizer.json").write_text(json.dumps(state), encoding="utf-8")
    res = organizer.undo()
    check(res.back == 1 and (folder / "b.jpg").exists(), "an older version's sort is still undone")


# ----------------------------------------------------------------------------- Cloud downloads

class _Server(http.server.BaseHTTPRequestHandler):
    body = b""
    claimed = 0

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(self.claimed))
        self.end_headers()
        self.wfile.write(self.body)
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, *a):
        pass


def _serve(body: bytes, claimed: int) -> str:
    _Server.body, _Server.claimed = body, claimed
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Server)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/f"


def cloud_downloads():
    import urllib.request
    from corefold import cloud
    folder = fresh("cloud")
    data = os.urandom(300_000)
    qx = cloud.QuickXorHash()
    qx.update(data)
    good = "quickxor:" + qx.digest()

    def get(body, claimed, size, check):
        target = folder / "report.pdf"
        target.unlink(missing_ok=True)
        try:
            cloud._stream_to(urllib.request.urlopen(_serve(body, claimed), timeout=10), target, size, None, None,
                             "", check)
            return "", target
        except cloud.CloudError as e:
            return str(e), target

    err, target = get(data, len(data), len(data), good)
    check(not err and target.read_bytes() == data, f"a whole download that matches is kept ({err or 'ok'})")
    # the connection drops half-way: the service said 300 KB, 150 KB came
    err, target = get(data[:150_000], len(data), len(data), good)
    check(err == cloud.CUT_OFF and not target.exists(), f"a download cut off half-way isn't kept ({err})")
    # the server itself sends less than the account says the file is (and says so in its own header)
    err, target = get(data[:150_000], 150_000, len(data), "")
    check(err == cloud.CUT_OFF and not target.exists(), f"fewer bytes than the file's size: not kept ({err})")
    # the right size but not the same content
    bad = bytearray(data)
    bad[1234] ^= 0xFF
    err, target = get(bytes(bad), len(data), len(data), good)
    check(err == cloud.NOT_THE_SAME and not target.exists(), f"changed on the way: not kept ({err})")
    db = cloud.DropboxHash()
    db.update(data)
    err, target = get(bytes(bad), len(data), len(data), "dropbox:" + db.digest())
    check(err == cloud.NOT_THE_SAME, f"Dropbox's check catches it too ({err})")
    check(not list(folder.glob("*.corefold-part")), "no half files left behind")
    # the two content checks give the services' own answers (test values from their documentation and rclone)
    vectors = [(b"", "AAAAAAAAAAAAAAAAAAAAAAAAAAA="), (base64.b64decode("Sg=="), "SgAAAAAAAAAAAAAAAQAAAAAAAAA="),
               (base64.b64decode("2fuxhBJXtpWFe8dOfdGeHw=="), "O9tHLAghgSvYohKFyMMxnNCHaHg="),
               (base64.b64decode("NbYXsp5/K6mR+NmHwExjvWeWDJFnXTKWVlzYHoesp2E="), "wjuAuWDiq04qDt1R8hHWDDcwVoQ=")]
    ok = True
    for raw, want in vectors:
        h = cloud.QuickXorHash()
        for k in range(0, len(raw), 5):
            h.update(raw[k:k + 5])
        ok = ok and h.digest() == want
    check(ok, "OneDrive's quickXorHash matches Microsoft's values")
    ok = True
    for n, want in ((1, "1cd6ef71e6e0ff46ad2609d403dc3fee244417089aa4461245a4e4fe23a55e42"),
                    (4194304, "d4d63bac5b866c71620185392a8a6218ac1092454a2d16f820363b69852befa3"),
                    (8388609, "02c8a4aefc2bfc9036f89a7098001865885938ca580e5c9e5db672385edd303c")):
        h = cloud.DropboxHash()
        blob = b"A" * n
        for k in range(0, n, 256 * 1024):
            h.update(blob[k:k + 256 * 1024])
        ok = ok and h.digest() == want
    check(ok, "Dropbox's content_hash matches Dropbox's values")


# ----------------------------------------------------------------------------- damaged ZIPs and ZIP bombs

def _local_entry(name: str, raw_size: int, lie: int | None = None, descriptor: bool = False) -> bytes:
    """One ZIP piece (its own header and data, no table of contents): raw_size zero bytes, deflated."""
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    crc, parts, left = 0, [], raw_size
    block = bytes(16 * 1024 * 1024)
    while left:
        piece = block[:min(left, len(block))]
        crc = zlib.crc32(piece, crc)
        parts.append(comp.compress(piece))
        left -= len(piece)
    parts.append(comp.flush())
    data = b"".join(parts)
    flag = 8 if descriptor else 0
    usize = raw_size if lie is None else lie
    head = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, flag, 8, 0, 0, 0 if descriptor else crc,
                       0 if descriptor else len(data), 0 if descriptor else usize, len(name), 0)
    tail = struct.pack("<IIII", 0x08074B50, crc, len(data), usize) if descriptor else b""
    return head + name.encode() + data + tail


def _peak_mb() -> float:
    if WINDOWS:
        import ctypes
        from ctypes import wintypes as wt

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC(cb=ctypes.sizeof(PMC))
        ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc),
                                                 pmc.cb)
        return pmc.PeakPagefileUsage / 2 ** 20
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def zip_bombs():
    from corefold import archives
    from corefold.core import Ctx, Options
    folder = fresh("bombs")
    ctx = Ctx(Options())
    # 1. a small file that unpacks to 3 GB, in one piece: stopped at the budget, nothing big kept
    bomb = folder / "bomb.zip"
    bomb.write_bytes(_local_entry("a.bin", 3 * 2 ** 30, descriptor=True))
    note(f"bomb: {bomb.stat().st_size // 1024} KB that unpack to 3 GB")
    out = fresh("bombs-out")
    before = _peak_mb()
    t = time.monotonic()
    good, bad, stopped = archives._scan_zip(bomb, out, ctx, budget=64 * 2 ** 20)
    took = time.monotonic() - t
    grew = _peak_mb() - before
    kept = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    check(stopped and good == 0, f"the bomb is stopped (good {good}, damaged {bad}, stopped {stopped})")
    check(kept == 0 and not list(out.rglob("*.part")), f"nothing of it is left on the disk ({kept} bytes)")
    check(took < 30, f"stopped quickly ({took:.1f} s)")
    check(grew < 200, f"memory stayed small (peak grew {grew:.0f} MB)")
    # 2. the header says 1 MB, the data unpacks to 200 MB: not believed, cut off at 1 MB
    liar = folder / "liar.zip"
    liar.write_bytes(_local_entry("b.bin", 200 * 2 ** 20, lie=2 ** 20))
    out = fresh("bombs-out2")
    good, bad, stopped = archives._scan_zip(liar, out, ctx)
    check(good == 0 and bad == 1 and not any(out.rglob("b.bin")), "a piece bigger than its header says isn't kept")
    # 3. a real damaged ZIP (its table of contents cut off) is still rescued
    real = folder / "real.zip"
    real.write_bytes(_local_entry("docs/one.txt", 5000) + _local_entry("two.txt", 70_000, descriptor=True))
    out = fresh("bombs-out3")
    good, bad, stopped = archives._scan_zip(real, out, ctx)
    check(good == 2 and bad == 0 and not stopped and (out / "docs" / "one.txt").stat().st_size == 5000,
          f"a damaged but honest ZIP is still rescued ({good} good, {bad} damaged)")
    # 4. the whole Repair, the way the app runs it: an error, no "(repaired)" file, no hidden folder left
    old = archives.RESCUE_MIN, archives.RESCUE_RATIO
    archives.RESCUE_MIN, archives.RESCUE_RATIO = 64 * 2 ** 20, 1
    try:
        err = ""
        try:
            archives.repair_archive(bomb, ctx)
        except RuntimeError as e:
            err = str(e)
        note(f"Repair said: {err}")
        check(bool(err), "Repair of the bomb ends with a message")
        check(not list(folder.glob("*repaired*")) and not list(folder.glob(".corefold-unpacking-*")),
              "and leaves nothing behind")
    finally:
        archives.RESCUE_MIN, archives.RESCUE_RATIO = old
    # 5. extracting with 7-Zip: refused when it can't fit, and stopped before the disk is full (the last GB is kept free)
    if WINDOWS and archives.find_7z():
        big = folder / "big.7z"
        src = fresh("bombs-src")
        with open(src / "zeros.bin", "wb") as f:  # (1 GB: big enough that 7-Zip is still writing when it's checked)
            for _ in range(64):
                f.write(bytes(16 * 2 ** 20))
        subprocess.run([archives.find_7z(), "a", "-t7z", "-mx1", str(big), str(src / "zeros.bin")],
                       capture_output=True, check=True)
        free = shutil.disk_usage(folder).free
        old_reserve = archives.RESCUE_RESERVE
        archives.RESCUE_RESERVE = free - 300 * 2 ** 20  # (as if only 300 MB more could be written)
        declared = archives._declared_size
        try:
            err = ""
            try:  # what it says it holds doesn't fit: refused before anything is written
                archives.extract(big, Ctx(Options(), params={"dest": str(fresh("bombs-dest"))}))
            except RuntimeError as e:
                err = str(e)
            check("needs" in err or "bomb" in err,
                  f"an archive too big for the disk is refused before unpacking ({err or 'not'})")
            check(not list((BASE / "bombs-dest").iterdir()), "and nothing was written")
            archives._declared_size = lambda *a: None  # (as if it didn't say, or said less than it holds)
            err = ""
            try:
                archives.extract(big, Ctx(Options(), params={"dest": str(fresh("bombs-dest"))}))
            except RuntimeError as e:
                err = str(e)
            check("nearly full" in err, f"7-Zip was stopped before the disk filled up ({err or 'not stopped'})")
            check(not list((BASE / "bombs-dest").iterdir()), "and the half-unpacked files are gone")
        finally:
            archives.RESCUE_RESERVE = old_reserve
            archives._declared_size = declared
    else:
        note("no 7-Zip here: the disk-full check of 7-Zip isn't tried")


# ----------------------------------------------------------------------------- "to the Recycle Bin"

def _find_dialog(pid: int, timeout: float = 15) -> int:
    import ctypes
    from ctypes import wintypes as wt
    user32 = ctypes.windll.user32
    found = []
    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def each(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        found.clear()
        user32.EnumWindows(proc(each), 0)
        if found:
            return found[0]
        time.sleep(0.2)
    return 0


def recycle_for_good():
    """A file too big for the Recycle Bin (the same as a drive with none): the old flags deleted it for good
    without a word. Now Windows asks first, and saying no keeps it. (Only a Recycle Bin the person turned
    off in its settings still deletes without asking: that's their own choice in Windows.)"""
    if not WINDOWS:
        note("Windows only")
        return
    import ctypes
    import winreg
    from corefold import space, uninstall
    folder = fresh("recycle")
    buf = ctypes.create_unicode_buffer(64)
    drive = str(folder)[:3]
    ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(drive, buf, 64)
    guid = buf.value[buf.value.find("{"):buf.value.find("}") + 1]
    check(bool(guid), f"the drive's volume {guid}")
    key_path = rf"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume\{guid}"
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
    try:
        old = winreg.QueryValueEx(key, "MaxCapacity")[0]
    except OSError:
        old = None
    winreg.SetValueEx(key, "MaxCapacity", 0, winreg.REG_DWORD, 1)  # this drive's Recycle Bin: 1 MB at most
    try:
        # the old way: gone for good, no question
        big = b"x" * (5 * 2 ** 20)
        before = folder / "old way.txt"
        before.write_bytes(big)
        op = uninstall.SHFILEOPSTRUCTW()
        op.wFunc, op.pFrom, op.fFlags = 3, str(before) + "\0", 0x04 | 0x10 | 0x40 | 0x400
        ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if before.exists():
            note("this drive still has a Recycle Bin here; the old way can't be shown")
        else:
            note("the old way: deleted for good, nothing was asked (the problem)")
        for name, fn in (("Space", lambda p: space.recycle([p])), ("the rest of the app", uninstall.recycle)):
            target = folder / f"{name}.txt"
            target.write_bytes(big)
            answer = {}
            worker = threading.Thread(target=lambda: answer.setdefault("r", fn(target)), daemon=True)
            worker.start()
            hwnd = _find_dialog(os.getpid())
            check(bool(hwnd), f"{name}: Windows asks before deleting it for good")
            if hwnd:
                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE: the person says no
            worker.join(15)
            check(not worker.is_alive() and target.exists(), f"{name}: saying no keeps the file")
            check(not answer.get("r"), f"{name}: and it isn't counted as gone ({answer.get('r')})")
    finally:
        if old is None:
            winreg.DeleteValue(key, "MaxCapacity")
        else:
            winreg.SetValueEx(key, "MaxCapacity", 0, winreg.REG_DWORD, old)
        winreg.CloseKey(key)
    # with the Recycle Bin on, nothing is asked and the file goes there
    target = folder / "normal.txt"
    target.write_text("x")
    check(space.recycle([target]) == 1 and not target.exists(), "with a Recycle Bin, it goes there without a question")


# ----------------------------------------------------------------------------- archive passwords

def _cmdlines_while(fn, name="7z.exe") -> tuple[list[str], object]:
    """Runs fn while Windows is asked, again and again, for the command line of every running 7z.exe (what any
    program on the PC can read)."""
    ps = subprocess.Popen(["powershell", "-NoProfile", "-Command",
                           "$end=(Get-Date).AddSeconds(40); while((Get-Date) -lt $end){ "
                           f"Get-CimInstance Win32_Process -Filter \"Name='{name}'\" | "
                           "ForEach-Object { 'CMD ' + $_.CommandLine }; "
                           "if (Test-Path $env:CF_STOP) { break } }"],
                          stdout=subprocess.PIPE, env=dict(os.environ, CF_STOP=str(TMP / "stop")))
    time.sleep(2.5)  # (PowerShell starting)
    try:
        result = fn()
    finally:
        (TMP / "stop").write_text("")
    out = ps.communicate(timeout=60)[0].decode("utf-8", "replace")
    (TMP / "stop").unlink()
    return [ln[4:] for ln in out.splitlines() if ln.startswith("CMD ")], result


def archive_passwords():
    from corefold import archives
    from corefold.core import Ctx, Options
    exe = archives.find_7z()
    if not WINDOWS or not exe:
        note("needs Windows and 7-Zip")
        return
    folder = fresh("passwords")
    src = fresh("passwords-src")
    (src / "secret.txt").write_text("the content", encoding="utf-8")
    (src / "big.bin").write_bytes(os.urandom(64 * 2 ** 20))  # (takes a few seconds: long enough to be seen)
    pw = "Sesame 42 كلمة"
    # the old way: the password is right there in 7-Zip's command line
    old = folder / "old.7z"
    seen, _ = _cmdlines_while(lambda: subprocess.run([exe, "a", "-t7z", "-mx=9", f"-p{pw}", str(old),
                                                      str(src / "big.bin")], capture_output=True))
    note(f"the old way, 7-Zip's command line as other programs see it: {'shown' if any(pw in c for c in seen) else 'not caught'}")
    # Corefold's way, for each kind of archive and each thing done with a password
    for fmt, password in (("7z", pw), ("zip", "Pass word 1 ")):
        out = folder / f"locked.{fmt}"
        made, outs = _cmdlines_while(lambda: archives.pack_into([src / "secret.txt", src / "big.bin"], out,
                                                                Ctx(Options()), fmt, "normal", password=password))
        check(out.exists(), f"{fmt}: made with a password")
        check(made and not any(password.strip() in c for c in made),
              f"{fmt}: the password isn't in 7-Zip's command line ({len(made)} looks)")
        check(archives.is_encrypted(out), f"{fmt}: it's really locked")
        check(archives.password_works(out, password), f"{fmt}: the right password opens it")
        check(not archives.password_works(out, password + "x"), f"{fmt}: a wrong one doesn't")
        names = sorted(e.path for e in archives.list_entries(out, password))
        check(names == ["big.bin", "secret.txt"], f"{fmt}: listed with the password ({names})")
        check(archives.read_member(out, "secret.txt", password) == b"the content", f"{fmt}: a file read from it")
        try:
            archives.read_member(out, "secret.txt", "wrong")
            check(False, f"{fmt}: a wrong password asks again")
        except archives.NeedPassword:
            check(True, f"{fmt}: a wrong password asks again")
        except RuntimeError as e:
            check(False, f"{fmt}: a wrong password asks again ({e})")
        dest = fresh(f"passwords-out-{fmt}")
        seen, res = _cmdlines_while(lambda: archives.extract(out, Ctx(Options(), params={
            "password": password, "dest": str(dest)})))
        check((dest / "locked" / "secret.txt").read_text(encoding="utf-8") == "the content",
              f"{fmt}: extracted with the password")
        check(not any(password.strip() in c for c in seen), f"{fmt}: and not on the command line then either")
        try:
            archives.extract(out, Ctx(Options(), params={"password": "nope", "dest": str(fresh("pw-wrong"))}))
            check(False, f"{fmt}: extracting with a wrong password asks again")
        except archives.NeedPassword:
            check(True, f"{fmt}: extracting with a wrong password asks again")
        check(archives.test_archive(out, Ctx(Options(), params={"password": password})) is not None,
              f"{fmt}: tested with the password")
        (src / "added.txt").write_text("added")
        archives.add_files(out, [src / "added.txt"], Ctx(Options(), params={"password": password}))
        (src / "secret.txt").write_text("edited", encoding="utf-8")
        archives.update_member(out, "secret.txt", src / "secret.txt", Ctx(Options(), params={"password": password}))
        (src / "secret.txt").write_text("the content", encoding="utf-8")
        archives.delete_entries(out, ["big.bin"], Ctx(Options(), params={"password": password}))
        names = sorted(e.path for e in archives.list_entries(out, password))
        check(names == ["added.txt", "secret.txt"], f"{fmt}: added to, changed and taken from ({names})")
        check(archives.read_member(out, "secret.txt", password) == b"edited", f"{fmt}: the change is in it")
        check(archives.password_works(out, password), f"{fmt}: still locked with the same password")


# ----------------------------------------------------------------------------- Clipboard history

def _file_has(folder: Path, secret: bytes) -> bool:
    return any(secret in p.read_bytes() for p in folder.glob("history*.db*") if p.is_file())


def clipboard_wiped():
    import sqlite3
    from corefold import clipboard
    secret = "CARD 4111 1111 1111 1111 exp 09/29"
    raw = secret.encode()
    # the old way: a deleted row stays in the file's free space
    folder = fresh("clip-old")
    db = sqlite3.connect(str(folder / "history.db"))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, text TEXT, html TEXT, image TEXT,"
               " width INTEGER, height INTEGER, digest TEXT UNIQUE NOT NULL, pinned INTEGER DEFAULT 0, created REAL,"
               " used REAL, app TEXT, size INTEGER)")  # (the history as Corefold 1.9 made it)
    db.execute("INSERT INTO items (kind, text, digest) VALUES ('text', ?, 'x')", (secret * 50,))
    db.commit()
    db.execute("DELETE FROM items")
    db.commit()
    db.close()
    note(f"the old way, deleted copy still in the file: {_file_has(folder, raw)}")
    # an old history like that is wiped once when Corefold opens it
    h = clipboard.History(folder)
    h.db.execute("SELECT 1").fetchone()
    h.close()
    check(not _file_has(folder, raw), "an older history: copies deleted before are wiped when it's opened")
    # Corefold's way: deleted, forgotten after its days, and cleared
    folder = fresh("clip-new")
    h = clipboard.History(folder)
    one = h.add(clipboard.text_item(secret + " one"))
    h.add(clipboard.text_item("ordinary text"))
    check(_file_has(folder, raw), "a copy is kept (it's in the file)")
    h.delete(one.id)
    check(not _file_has(folder, (secret + " one").encode()), "deleted: it's gone from the file and its log")
    old = h.add(clipboard.text_item(secret + " two"))
    h.db.execute("UPDATE items SET used = ? WHERE id = ?", (time.time() - 40 * 86400, old.id))
    h.db.commit()
    h.trim(500, 30)
    check(not _file_has(folder, (secret + " two").encode()), "forgotten after 30 days: gone from the file")
    h.add(clipboard.text_item(secret + " three"))
    h.clear(keep_pinned=True)
    h.close()
    check(not _file_has(folder, raw), "Clear: nothing of it left in the file")
    # a damaged history that was put aside goes once it's older than the days copies are kept
    damaged = folder / "history (damaged).db"
    damaged.write_bytes(b"SQLite format 3\0" + raw)
    os.utime(damaged, (time.time() - 31 * 86400,) * 2)
    h = clipboard.History(folder)
    h.trim(500, 30)
    h.close()
    check(not damaged.exists(), "an old damaged history (with copies in it) is removed")


step("File Organizer: a crash part-way through a sort", organizer_crash)
step("File Organizer: Undo checks it's the same file", organizer_identity)
step("Cloud: downloads checked before they're kept", cloud_downloads)
step("Damaged ZIPs and ZIP bombs", zip_bombs)
step("Archive passwords stay off the command line", archive_passwords)
step("Clipboard: deleted copies are wiped", clipboard_wiped)
step("Recycle Bin deletes that would be for good", recycle_for_good)

print(f"audittest: {len(problems)} problem(s)", flush=True)
for p in problems:
    print("  - " + p)
shutil.rmtree(TMP, ignore_errors=True)
shutil.rmtree(BASE, ignore_errors=True)
sys.exit(1 if problems else 0)
