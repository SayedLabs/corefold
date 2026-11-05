"""Rakiza Core Space's search and live map, tried for real on a Windows PC: how much room keeping every name
takes, how fast a search runs through them, what following a disk costs while nothing changes, and that a
map kept up to date says exactly what a new look says.

Run: python scripts/spacelivetest.py
Prints one line per check and "spacelivetest: N problem(s)"."""
import ctypes
import gc
import os
import random
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="corefold-spacelive-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from corefold import space  # noqa: E402

problems = 0


def say(text: str):
    print(text, flush=True)


def check(ok: bool, text: str) -> bool:
    global problems
    say(("ok    " if ok else "FAIL  ") + text)
    if not ok:
        problems += 1
    return ok


def cpu_s() -> float:
    """The process's own CPU time so far (all its threads)."""
    if os.name != "nt":
        return time.process_time()
    from ctypes import wintypes as wt
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    times = [wt.FILETIME() for _ in range(4)]
    k32.GetProcessTimes(ctypes.c_void_p(k32.GetCurrentProcess()), *[ctypes.byref(t) for t in times])

    def s(ft):
        return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 1e7
    return s(times[2]) + s(times[3])


def private_mb() -> float:
    if os.name != "nt":
        return 0.0
    from ctypes import wintypes as wt

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]
    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(k32.GetCurrentProcess()), ctypes.byref(pmc), pmc.cb)
    return pmc.PrivateUsage / 2 ** 20


def same(a: space.Scan, b: space.Scan) -> list[str]:
    """Where two maps of the same disk differ (nothing, when they agree)."""
    out = []
    if a.root.size != b.root.size:
        out.append(f"size {a.root.size} vs {b.root.size}")
    if (a.files, a.folders) != (b.files, b.folders):
        out.append(f"files/folders {(a.files, a.folders)} vs {(b.files, b.folders)}")
    if {k: v for k, v in a.kinds.items() if v} != {k: v for k, v in b.kinds.items() if v}:
        out.append("kinds")
    if a.types != b.types:
        out.append("types")
    if a.biggest != b.biggest:
        out.append("biggest")
    todo = [(a.root, b.root)]
    while todo and len(out) < 10:
        x, y = todo.pop()
        if (x.size, x.files, x.folders) != (y.size, y.files, y.folders) or x.own_files() != y.own_files():
            out.append(f"folder {x.path}")
        if [c.name for c in x.children] != [c.name for c in y.children]:
            out.append(f"folders in {x.path}")
            continue
        todo.extend(zip(x.children, y.children))
    return out


def follow(result: space.Scan, watch: space.Watch) -> tuple[bool, int, float]:
    """One look at what Windows said changed, the way the page does it: (lost count, folders, seconds)."""
    changed, lost = watch.take()
    if lost:
        return True, 0, 0.0
    started = time.perf_counter()
    folders, jobs = {}, []
    for inside in sorted(changed, key=len):
        f = space.folder_at(result, inside)
        p = str(f.path)
        if p not in folders:
            folders[p] = f
            jobs.append((p, {c.name for c in f.children}))
    space.apply_changes(result, space.read_again(jobs), folders)
    return False, len(jobs), time.perf_counter() - started


def room_and_search():
    root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    stop = threading.Event()
    threading.Timer(240, stop.set).start()
    gc.collect()
    before = private_mb()
    r = space.scan(root, stop)
    gc.collect()
    after = private_mb()
    say(f"note  {root}: {r.files:,} files, {r.folders:,} folders in {r.seconds:.1f} s"
        f"{' (stopped)' if r.cancelled else ''}; the map takes {after - before:.0f} MB")
    names = sizes = old_way = 0
    counted: set[int] = set()  # (folders without files share one empty string and one empty list of sizes)
    todo = [r.root]
    while todo:
        f = todo.pop()
        todo.extend(f.children)
        if id(f.names) not in counted:
            counted.add(id(f.names))
            names += sys.getsizeof(f.names)
        if id(f.sizes) not in counted:
            counted.add(id(f.sizes))
            sizes += sys.getsizeof(f.sizes)
        big = f.big  # (what each folder kept before: a list of its 48 biggest as (size, name) pairs)
        old_way += sys.getsizeof(big) + sum(sys.getsizeof(t) + sys.getsizeof(t[0]) + sys.getsizeof(t[1]) for t in big)
    say(f"note  every name: {names / 2**20:.1f} MB, every size: {sizes / 2**20:.1f} MB "
        f"(keeping only each folder's 48 biggest, as before, took {old_way / 2**20:.1f} MB)")
    # (it keeps every file, not 48 a folder, so a folder of hundreds of files costs more than before and a folder of
    # a few costs less: Windows' own folder came to 15.3 MB against 14.5 MB, a folder of programs to 3 against 8)
    check(names + sizes <= old_way * 1.25, f"keeping every name takes about the room keeping the biggest did "
          f"({(names + sizes) / 2**20:.1f} MB, before {old_way / 2**20:.1f} MB)")
    per_million = (names + sizes) / max(r.files, 1) * 1e6 / 2 ** 20
    # (a folder of its own costs about 120 bytes for its names and sizes, so a drive of many small folders, like
    # Windows' own, comes to more per file than one of big folders; what counts is that it's less than before)
    say(f"note  that's {per_million:.0f} MB for a million files here")
    for query in ("dll", "kernel32", "*.exe", "notepad", "zz no such name zz", "a"):
        t = time.perf_counter()
        rows, total = space.find(r, query)
        took = time.perf_counter() - t
        check(took < 1.5, f"find {query!r}: {total:,} found in {took * 1000:.0f} ms")
    rows, _total = space.find(r, "notepad.exe")
    check(any(Path(p).name.lower() == "notepad.exe" for _, p, _ in rows), "notepad.exe is found (it's a small file)")
    del r
    gc.collect()


def make_tree(top: Path, folders: int, files: int):
    random.seed(7)
    paths = [top]
    for i in range(folders):
        p = random.choice(paths) / f"folder {i}"
        p.mkdir(parents=True, exist_ok=True)
        paths.append(p)
    for i in range(files):
        (random.choice(paths) / f"file {i}.{random.choice(['txt', 'jpg', 'mp4', 'dat', 'pdf'])}").write_bytes(
            b"x" * random.randint(0, 5000))
    return paths


def live():
    top = TMP / "tree"
    paths = make_tree(top, 200, 5000)
    r = space.scan(top, workers=4)
    w = space.Watch(top)
    if not check(w.start(), "Windows tells what changes in a folder"):
        return
    r.watch = w
    time.sleep(0.5)
    cpu0 = cpu_s()
    time.sleep(10)
    idle = cpu_s() - cpu0
    check(idle < 0.3, f"following a folder costs nothing while nothing changes ({idle * 1000:.0f} ms of CPU in 10 s)")
    # a lot changes at once: new folders full of files, files gone, grown and renamed
    random.seed(9)
    fresh = top / "copied in" / "deeper"
    fresh.mkdir(parents=True)
    for i in range(2000):
        (fresh / f"new {i}.mp4").write_bytes(b"n" * 100)
    files = [p for p in top.rglob("*") if p.is_file() and "copied in" not in str(p)]
    for p in random.sample(files, 300):
        p.unlink()
    files = [p for p in top.rglob("*") if p.is_file() and "copied in" not in str(p)]
    for p in random.sample(files, 50):
        p.write_bytes(b"g" * 20000)
    for i, p in enumerate(random.sample(files, 100)):
        if p.exists():
            p.rename(p.with_name(f"renamed {i}{p.suffix}"))
    shutil.rmtree(random.choice([p for p in paths[1:] if p.exists() and p.parent == top]), ignore_errors=True)
    time.sleep(2)
    total_s, rounds, lost_any = 0.0, 0, False
    for _ in range(5):
        lost, count, took = follow(r, w)
        lost_any = lost_any or lost
        total_s += took
        rounds += count
        time.sleep(0.5)
    if lost_any:
        say("note  Windows lost count (too much at once): the page makes the map again quietly then")
        r = space.scan(top, workers=4)
    else:
        say(f"note  {rounds} folders read again in {total_s * 1000:.0f} ms")
        check(total_s < 5, "bringing the map up to date takes a few seconds at most")
    diff = same(r, space.scan(top, workers=4))
    check(not diff, "the map kept up to date says what a new look says" + (f": {diff}" if diff else ""))
    w.stop()
    time.sleep(0.5)
    check(not w.alive, "following stops when asked")


def whole_drive():
    drive = os.environ.get("SystemDrive", "C:") + "\\"
    w = space.Watch(Path(drive))
    if not check(w.start(), f"Windows tells what changes on {drive}"):
        return
    cpu0 = cpu_s()
    seen: set = set()
    lost = False
    for _ in range(15):
        time.sleep(2)
        changed, was_lost = w.take()
        seen |= changed
        lost = lost or was_lost
    used = cpu_s() - cpu0
    w.stop()
    say(f"note  {drive} for 30 s: {len(seen)} folders changed by Windows and programs{' (lost count once)' if lost else ''}")
    check(used < 1.0, f"following the whole drive for 30 s used {used * 1000:.0f} ms of CPU")


for step in (room_and_search, live, whole_drive):
    try:
        step()
    except Exception:
        import traceback
        traceback.print_exc()
        check(False, f"{step.__name__} ran to the end")
shutil.rmtree(TMP, ignore_errors=True)
say(f"spacelivetest: {problems} problem(s)")
sys.path.insert(0, str(ROOT / "scripts"))
import testexit  # noqa: E402
testexit.finish(1 if problems else 0)
