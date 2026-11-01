"""Every Converter tool that needs no question (pictures, videos, sounds, PDF, archives, folders) and the PDF
editor's Save, when saving goes wrong in the other ways a real PC has, the File Organizer when it can't move
into a folder, and Cloud with folders on this PC (up, back, and a move between two clouds):

  read-only   the folder it saves into can't be written to (its rights say no): what it makes goes to
              Downloads › Corefold instead, whole, and nothing is left in the folder
  locked      the file it works on is held open by another program that lets nobody read it
  long        the files and the folder it saves into are deep inside folders, past Windows' old 260 letters
  cancel      Cancel is pressed soon after it starts

Whatever happens, it must: leave the originals exactly as they were, leave no half-written file or folder
behind (one cut short must never look like a whole one), and say what went wrong in plain words, not FFmpeg's,
7-Zip's or Windows' own English. A tool that can do its work must make whole files. Cancel must stop it
within a few seconds.

Prints one line per tool, one line per problem and "writefailtest: N problem(s)". Needs FFmpeg and 7-Zip.
Run: python scripts/writefailtest.py [read-only,locked,long,cancel]"""
import faulthandler
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fulldisktest import TMP, Options, cv, fingerprints, make_files, whole  # noqa: E402 (sets up Qt and a clean profile)

from corefold import core  # noqa: E402
from corefold import organizer as org  # noqa: E402

core.set_downloads_folder(TMP / "user Downloads")  # (Downloads › Corefold, where files go that can't go where meant)

problems: list[str] = []
# what only FFmpeg, 7-Zip, Pillow or Windows say: the person must get Corefold's own words instead
RAW = re.compile(r"Errno|WinError|Permission denied|Access is denied|[Ff][Ff]mpeg|7-Zip|Error opening|Conversion failed|"
                 r"Traceback|Invalid argument|ERROR|No such file|[Cc]annot open|Could not")  # (Corefold's own start small)


def problem(what: str):
    problems.append(what)
    print("PROBLEM:", what, flush=True)


def plain(error: str) -> bool:
    return not RAW.search(error.split(": ", 1)[-1])


def deny_writing(folder: Path):
    """Takes away the right to make files and folders in it (on Windows by its rights, like a folder another
    user owns; elsewhere by its mode)."""
    if os.name == "nt":
        subprocess.run(["icacls", str(folder), "/deny", f"{os.environ['USERNAME']}:(OI)(CI)(WD,AD,WA,WEA)"],
                       check=True, capture_output=True)
    else:
        folder.chmod(0o555)


def allow_writing(folder: Path):
    if os.name == "nt":
        subprocess.run(["icacls", str(folder), "/remove:d", os.environ["USERNAME"]], capture_output=True)
    else:
        folder.chmod(0o755)


class Locked:
    """Holds a file open the way a program that lets nobody else read it does (Windows only)."""

    def __init__(self, paths: list[Path]):
        self.paths, self.handles = paths, []

    def __enter__(self):
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateFileW.restype = wintypes.HANDLE
            k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            for p in self.paths:
                if p.is_file():
                    h = k32.CreateFileW(str(p), 0x80000000, 0, None, 3, 0, None)  # (GENERIC_READ, no sharing)
                    if h not in (None, wintypes.HANDLE(-1).value):
                        self.handles.append(h)
            self.k32 = k32
        return self

    def __exit__(self, *exc):
        for h in self.handles:
            self.k32.CloseHandle(h)


def left_in(out: Path, before: set) -> list[Path]:
    return sorted(p for p in out.rglob("*") if p not in before)


def judge(case: str, files: list[Path], tool, result, errors: list[str], out: Path, before_out: set,
          originals: dict, src: Path, must_fail: bool = False) -> dict:
    left = left_in(out, before_out)
    line = (f"{case} · {files[0].name} · {tool.key}: made {len(result.outputs)}, errors {errors[:1]}, "
            f"new {[p.name for p in left][:3]}")
    print(line, flush=True)
    for e in errors:
        if not plain(e):
            problem("not said in plain words: " + line)
            break
    if (errors or not result.outputs) and any(p.is_file() for p in left):
        problem("left something behind: " + line)
    for o in result.outputs:
        for f in [o] if Path(o).is_file() else [p for p in Path(o).rglob("*") if p.is_file()]:
            if not whole(f):
                problem(f"{f.name} is cut short or empty but counted as made: " + line)
    if must_fail and result.outputs and not errors:
        problem("said it made something it couldn't have: " + line)
    if not errors and not result.outputs and not result.clipboard and not result.note and case != "cancel":
        problem("nothing made and nothing said: " + line)
    now = fingerprints(src)
    if now != originals:
        problem("an original changed: " + line)
    return now


def run(case: str, files: list[Path], tool, out: Path, **kw):
    """The tool's job; if it hasn't ended after 4 minutes, where every thread is gets printed and the test
    stops (a job that never ends is a problem too, and this says which one and where)."""
    print(f"  … {case} · {files[0].name} · {tool.key}", flush=True)
    faulthandler.dump_traceback_later(240, exit=True)
    try:
        return cv.run_job(tool, files, Options(output_folder=out), **kw)
    finally:
        faulthandler.cancel_dump_traceback_later()


# what the PDF editor's Save sends: a filled box drawn on the first page and that page turned
EDITS = {"edits": [{"kind": "rect", "page": 0, "points": [60, 60, 200, 120], "color": "#cc0000", "width": 2,
                    "fill": "#cc0000"}], "turns": {"0": 90}}


def tools(inputs):
    """Each tool with the files it's tried on and what it's given: every tool that needs no question, and the
    PDF editor's Save (it saves through the same job as the rest)."""
    from corefold import pdfedit
    for files in inputs:
        for slot in cv.tools_for(files)[1]:
            t = slot.tool
            if not (slot.reason or t.ask or t.opens or t.run is None):
                yield files, t, {}
        if len(files) == 1 and files[0].suffix.lower() == ".pdf":
            yield files, pdfedit.EDIT, {"params": EDITS}


def empty(out: Path):
    for p in list(out.iterdir()):
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)


def read_only(inputs, src):
    out = TMP / "read-only folder"
    out.mkdir()
    originals = fingerprints(src)
    deny_writing(out)
    try:
        try:
            (out / "probe").write_bytes(b"x")
            print("note  this account can still write there (an administrator on Linux): skipped")
            (out / "probe").unlink()
            return 0
        except OSError:
            pass
        n = 0
        spare = core.fallback_folder()
        for files, tool, kw in tools(inputs):
            before = set(out.rglob("*"))
            result, errors = run("read-only", files, tool, out, **kw)
            originals = judge("read-only", files, tool, result, errors, out, before, originals, src)
            for o in result.outputs:
                if spare not in Path(o).parents:
                    problem(f"read-only · {files[0].name} · {tool.key}: {o} isn't in Downloads › Corefold")
            if left_in(out, before):
                problem(f"read-only · {files[0].name} · {tool.key}: something got into the read-only folder")
            n += 1
        return n
    finally:
        allow_writing(out)
        shutil.rmtree(out, ignore_errors=True)


def locked(inputs, src):
    if os.name != "nt":
        print("note  locking a file so nobody can read it is Windows' own: skipped")
        return 0
    out = TMP / "out-locked"
    out.mkdir()
    originals = fingerprints(src)
    n = 0
    for files, tool, kw in tools(inputs):
        held = [p for f in files for p in ([f] if f.is_file() else sorted(f.rglob("*")))]
        before = set(out.rglob("*"))
        with Locked(held):
            result, errors = run("locked", files, tool, out, **kw)
        originals = judge("locked", files, tool, result, errors, out, before, originals, src, must_fail=True)
        empty(out)
        n += 1
    shutil.rmtree(out, ignore_errors=True)
    return n


def long_paths():
    deep = TMP
    for i in range(6):
        deep = deep / f"مجلد طويل جدا {i} a folder with a long name to go past Windows' old limit"
    try:
        (deep / "src").mkdir(parents=True)
        (deep / "out").mkdir()
    except OSError as e:
        print(f"note  Windows can't make folders this deep here ({e}): long names are off on this PC, skipped")
        return 0
    print(f"note  the files are {len(str(deep / 'src'))} letters deep")
    src, out = deep / "src", deep / "out"
    inputs = make_files(src)
    originals = fingerprints(src)
    n = 0
    for files, tool, kw in tools(inputs):
        before = set(out.rglob("*"))
        result, errors = run("long", files, tool, out, **kw)
        originals = judge("long", files, tool, result, errors, out, before, originals, src)
        empty(out)
        n += 1
    return n


def cancel(inputs, src):
    out = TMP / "out-cancel"
    out.mkdir()
    originals = fingerprints(src)
    n = 0
    for files, tool, kw in tools(inputs):
        before = set(out.rglob("*"))
        stop = threading.Event()
        got = {}
        job = threading.Thread(target=lambda: got.update(r=run("cancel", files, tool, out, cancel=stop, **kw)),
                               daemon=True)
        job.start()
        time.sleep(0.15)
        stop.set()
        t0 = time.perf_counter()
        job.join(60)
        took = time.perf_counter() - t0
        if job.is_alive():
            problem(f"cancel · {files[0].name} · {tool.key}: still going a minute after Cancel")
            faulthandler.dump_traceback(all_threads=True)
            break
        elif took > 8:
            problem(f"cancel · {files[0].name} · {tool.key}: took {took:.0f} s to stop after Cancel")
        result, errors = got["r"]
        originals = judge("cancel", files, tool, result, errors, out, before, originals, src)
        empty(out)
        n += 1
    shutil.rmtree(out, ignore_errors=True)
    return n


def organizer_read_only():
    """Sorting into a kind folder that can't be written to: the file stays where it was, it's counted as not
    moved, and Undo still puts back what did move."""
    mess = TMP / "Downloads"
    mess.mkdir()
    (mess / "song.mp3").write_bytes(b"ID3" + os.urandom(2000))
    (mess / "notes.txt").write_text("hello", "utf-8")
    p = org.plan(mess, check_in_use=False, known=True)
    music = p.folders.get("music")
    if music is None:
        problem(f"organizer: no music folder planned ({sorted(p.folders)})")
        return
    music.mkdir(exist_ok=True)
    deny_writing(music)
    try:
        before = fingerprints(mess)
        r = org.sort(p)
        print(f"organizer · read-only Music folder: moved {r.moved}, failed {[(f.name, why) for f, why in r.failed]}")
        if not (mess / "song.mp3").is_file() or not any(f.name == "song.mp3" for f, _ in r.failed):
            problem("organizer: the song isn't where it was, or isn't counted as not moved")
        u = org.undo()
        if u.failed:
            problem(f"organizer: Undo after a move that failed couldn't put back {u.failed}")
        if fingerprints(mess) != before:
            problem("organizer: a file changed or went missing")
    finally:
        allow_writing(music)
        shutil.rmtree(mess, ignore_errors=True)


def cloud_local(cases: list[str]):
    """Cloud with folders on this PC (OneDrive's, Dropbox's, Google Drive's own folder): sending files and folders
    up, saving them back, and moving a folder from one cloud to another, in the same cases. Whatever happens, the
    file it came from stays as it was, nothing half-copied is left (no ".corefold-part" and no file cut short),
    the error is Corefold's own words, and a move takes nothing away unless all of it got there."""
    from corefold import cloud
    from corefold.cloudlocal import Local
    base = TMP / "cloud"
    a_root, b_root, pc = base / "OneDrive", base / "Dropbox", base / "this PC"
    for d in (a_root, b_root, pc):
        d.mkdir(parents=True)
    a = Local(cloud.Account("onedrive", "", extra={"local": str(a_root)}))
    b = Local(cloud.Account("dropbox", "", extra={"local": str(b_root)}))
    mine = pc / "photos رحلة"
    (mine / "inside").mkdir(parents=True)
    (mine / "a.jpg").write_bytes(os.urandom(300_000))
    (mine / "inside" / "b.txt").write_text("مرحبا hello\n" * 2000, "utf-8")
    big = pc / "big video.mp4"
    with open(big, "wb") as f:  # (big enough that Cancel comes while it's being copied)
        for _ in range(96):
            f.write(os.urandom(1024 * 1024))
    n = [0]

    def same(folder: Path) -> dict:  # (what's in a folder, by where in it: to compare a copy with where it came from)
        return {p.relative_to(folder): h for p, h in fingerprints(folder).items()}

    def check(what: str, err: str | None, kept: dict, src: Path, *places: Path):
        n[0] += 1
        print(f"cloud · {what}: {err or 'done'}", flush=True)
        if err is not None and not plain(err):
            problem(f"cloud · {what}: not said in plain words ({err})")
        if fingerprints(src) != kept:
            problem(f"cloud · {what}: what it came from changed")
        for place in places:
            for f in place.rglob("*"):
                if f.name.endswith(".corefold-part"):
                    problem(f"cloud · {what}: a half-copied file was left ({f.name})")
                elif f.is_file() and f.stat().st_size == 0:
                    problem(f"cloud · {what}: an empty file was left ({f.name})")

    def attempt(fn) -> str | None:
        try:
            fn()
        except cloud.CloudError as e:
            return str(e)
        except Exception as e:  # (Python's or Windows' own error reached the page: said as it is, not Corefold's)
            problem(f"cloud · not caught as Corefold's own words: {type(e).__name__}: {e}")
            return str(e)
        return None

    if "read-only" in cases:
        shut = a_root / "shared folder"
        shut.mkdir()
        deny_writing(shut)
        try:
            try:
                (shut / "probe").write_bytes(b"x")
                (shut / "probe").unlink()
                print("note  this account can still write there (an administrator on Linux): cloud read-only skipped")
            except OSError:
                folder = a._entry(shut)
                for src in (mine / "a.jpg", mine):
                    kept = fingerprints(pc)
                    err = attempt(lambda: cloud.upload_tree(a, src, folder))
                    if err is None:
                        problem(f"cloud · read-only · up {src.name}: said it went up into a folder that can't be written to")
                    if any(shut.iterdir()):
                        problem(f"cloud · read-only · up {src.name}: something got into the read-only folder")
                    check(f"read-only · up {src.name}", err, kept, pc, shut)
        finally:
            allow_writing(shut)
            shutil.rmtree(shut, ignore_errors=True)
        cloud.upload_tree(a, mine, None)
        up = a._entry(a_root / mine.name)
        stuck = base / "read-only here"
        stuck.mkdir()
        deny_writing(stuck)
        try:
            try:
                (stuck / "probe").write_bytes(b"x")
                (stuck / "probe").unlink()
            except OSError:
                kept = fingerprints(a_root)
                err = attempt(lambda: cloud.download_tree(a, up, stuck))
                if err is None:
                    problem("cloud · read-only · save to this PC: said it saved into a folder that can't be written to")
                check("read-only · save to this PC", err, kept, a_root, stuck)
        finally:
            allow_writing(stuck)
            shutil.rmtree(stuck, ignore_errors=True)
        shutil.rmtree(a_root / mine.name, ignore_errors=True)

    if "locked" in cases and os.name == "nt":
        kept = fingerprints(pc)
        with Locked([mine / "inside" / "b.txt"]):
            err = attempt(lambda: cloud.upload_tree(a, mine / "inside" / "b.txt", None))
        if err is None:
            problem("cloud · locked · up a file nobody may read: said it went up")
        check("locked · up a file", err, kept, pc, a_root)
        # a move of a folder with a locked file inside: it must stay where it was, all of it
        moving = a_root / "to move"
        shutil.copytree(mine, moving)
        kept = fingerprints(a_root)
        with Locked([moving / "inside" / "b.txt"]):
            err = attempt(lambda: cloud.send(a, a._entry(moving), b, None, move=True))
        if not (moving / "a.jpg").is_file() or not (moving / "inside" / "b.txt").is_file():
            problem("cloud · locked · move: the folder was taken away though a file in it couldn't be copied")
        check("locked · move a folder with a held file", err, kept, a_root, b_root)
        shutil.rmtree(moving, ignore_errors=True)
        for p in list(b_root.iterdir()):
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()

    if "long" in cases:
        deep = a_root
        for i in range(5):
            deep = deep / f"مجلد طويل جدا {i} a cloud folder with a long name to go past the old limit"
        try:
            deep.mkdir(parents=True)
        except OSError as e:
            print(f"note  long names are off on this PC ({e}): cloud long skipped")
        else:
            kept = fingerprints(pc)
            err = attempt(lambda: cloud.upload_tree(a, mine, a._entry(deep)))
            got = deep / mine.name / "inside" / "b.txt"
            if err is not None or not got.is_file() or got.read_bytes() != (mine / "inside" / "b.txt").read_bytes():
                problem(f"cloud · long: the folder didn't go up whole into a deep folder ({err})")
            back = base / "back"
            back.mkdir()
            err2 = attempt(lambda: cloud.download_tree(a, a._entry(deep / mine.name), back))
            if err2 is not None or same(back / mine.name) != same(mine):
                problem(f"cloud · long: saving it back from the deep folder didn't give the same files ({err2})")
            check("long · up and back", err or err2, kept, pc, a_root, back)
            shutil.rmtree(back, ignore_errors=True)
            shutil.rmtree(a_root / deep.relative_to(a_root).parts[0], ignore_errors=True)

    if "cancel" in cases:
        kept = fingerprints(pc)
        stop = threading.Event()
        got = {}

        def up():
            got["err"] = attempt(lambda: cloud.upload_tree(a, big, None, progress=lambda p: stop.set(), cancel=stop))
        t = threading.Thread(target=up, daemon=True)
        t.start()
        t.join(60)
        if t.is_alive():
            problem("cloud · cancel: still copying a minute after Cancel")
        else:
            if got.get("err") is None:
                problem("cloud · cancel: said the big file went up though Cancel was pressed while it was copied")
            if (a_root / big.name).exists():
                problem("cloud · cancel: a file cut short is in the cloud folder under the real name")
            check("cancel · up a big file", got.get("err"), kept, pc, a_root)
    print(f"cloud: {n[0]} cases tried")


def main():
    cases = (sys.argv[1] if len(sys.argv) > 1 else "read-only,locked,long,cancel").split(",")
    src = TMP / "originals"
    src.mkdir()
    inputs = make_files(src)
    counts = {}
    if "read-only" in cases:
        counts["read-only"] = read_only(inputs, src)
        organizer_read_only()
    if "locked" in cases:
        counts["locked"] = locked(inputs, src)
    if "long" in cases:
        counts["long"] = long_paths()
    if "cancel" in cases:
        counts["cancel"] = cancel(inputs, src)
    cloud_local(cases)
    print("tools tried: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    print(f"writefailtest: {len(problems)} problem(s)")
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
