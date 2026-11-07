"""How much memory Rakiza Core holds, step by step, and what it keeps after the window is let go: waiting in the
tray, the window open on Home, every page opened, the window closed and freed, and the same again a few times (so
memory that's never handed back shows up as a climb). Runs on Windows (private memory, what Task Manager adds up)
and on Linux for a quick look (the unique memory of the process).

Run: python scripts/memtest.py <folder> [en|ar] [dark|light]
Writes <folder>/memtest.md and prints each step."""
import gc
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "memtest").resolve()
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
LOOK = sys.argv[3] if len(sys.argv) > 3 else "dark"
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")  # (as main.py does: numpy's maths gets one core, not a buffer each)
TMP = Path(tempfile.mkdtemp(prefix="corefold-memtest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
if sys.platform != "win32":  # (a quick try on Linux: the Windows-only parts stand in, as in the tests)
    import ctypes
    from unittest import mock
    ctypes.windll = mock.MagicMock()
    _reg = mock.MagicMock()
    _reg.EnumKey.side_effect = _reg.EnumValue.side_effect = OSError
    sys.modules.setdefault("winreg", _reg)
    sys.modules.setdefault("winsound", mock.MagicMock())
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(1500, exit=True)

from corefold import settings as st  # noqa: E402
from corefold import tools as all_tools  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.check_updates = False
s.language = LANG
s.appearance = LOOK
s.known_tools = sorted(t.id for t in all_tools.ready())
s.save()

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from corefold import i18n  # noqa: E402
i18n.set_language(LANG)
qapp = QApplication(sys.argv)
qapp.setQuitOnLastWindowClosed(False)
if i18n.is_rtl():
    qapp.setLayoutDirection(Qt.RightToLeft)


def working_set_mb() -> float:
    if sys.platform == "win32":
        from corefold import winutil
        return winutil.memory_info()[0] / 2**20
    with open("/proc/self/status") as f:
        return next(int(ln.split()[1]) for ln in f if ln.startswith("VmRSS")) / 1024


def private_mb() -> float:
    if sys.platform == "win32":
        from corefold import winutil
        return winutil.memory_info()[1] / 2**20
    with open("/proc/self/smaps_rollup") as f:  # (unique to the process: its own heap, not shared libraries)
        vals = {ln.split(":")[0]: int(ln.split()[1]) for ln in f if ln.split()[-1] == "kB"}
    return (vals.get("Private_Clean", 0) + vals.get("Private_Dirty", 0)) / 1024


def pymalloc() -> tuple[float, float]:
    """Python's own small-object memory: (arenas it holds, MB; what's in use inside them, MB). Much more held than
    used means the memory is in pieces (a few live objects keep whole arenas)."""
    import re
    fd, path = tempfile.mkstemp()
    saved = os.dup(2)
    try:
        sys.stderr.flush()
        os.dup2(fd, 2)
        sys._debugmallocstats()
    finally:
        os.dup2(saved, 2)
        os.close(saved)
        os.close(fd)
    text = Path(path).read_text("utf-8", "replace")
    os.unlink(path)
    arenas = re.search(r"# arenas allocated current\s*=\s*([\d,]+)", text)
    used = re.search(r"# bytes in allocated blocks\s*=\s*([\d,]+)", text)
    size = re.search(r"(\d+) arenas \* (\d+) bytes/arena", text)
    per = int(size.group(2)) if size else 1 << 20
    return ((int(arenas.group(1).replace(",", "")) * per / 2**20) if arenas else 0.0,
            (int(used.group(1).replace(",", "")) / 2**20) if used else 0.0)


def settle(seconds=0.3):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        qapp.processEvents()
        time.sleep(0.002)


rows: list[tuple] = []
_last = [0.0]


def memmap(what: str):
    """Windows: where the memory is right now (scripts/memmap.py on this process), printed."""
    if sys.platform != "win32":
        return
    import subprocess
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "memmap.py"), str(os.getpid())],
                         capture_output=True, text=True, timeout=120).stdout
    print(f"---- memory map: {what}\n{out}", flush=True)
    maps.append((what, out))


maps: list[tuple[str, str]] = []


def snap(what: str):
    gc.collect()
    settle(0.05)
    mb = private_mb()
    widgets = len(QApplication.allWidgets())
    mods = len(sys.modules)
    ws = working_set_mb()
    held, used = pymalloc()
    rows.append((what, mb, mb - _last[0], ws, held, used, widgets, mods))
    print(f"{what:<48} {mb:8.1f} MB  {mb - _last[0]:+7.1f}  ws {ws:7.1f}  py {used:6.1f}/{held:6.1f}  "
          f"widgets {widgets:6d}  modules {mods}", flush=True)
    _last[0] = mb


from corefold import app as A  # noqa: E402

A.setup_logging()
snap("imported")
c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
c.apply_tools()
settle(3)
snap("in the tray")
c.tidy_up()
snap("in the tray, tidied")

from corefold.window import PAGES  # noqa: E402


def open_all(every_page: bool):
    c.show_window("Home")
    c.window.resize(1100, 740)
    settle(4)  # (what waits a moment after the window opens: warm pages, modules read on a thread)
    snap("window open: Home")
    if not every_page:
        return
    w = c.window
    pages = [p for p in PAGES if w.shows(p)]
    for name in pages:
        before = private_mb()
        w.open_page(name)
        settle(1.0)
        gc.collect()
        grew = private_mb() - before
        if grew > 1.5:
            print(f"   page {name}: {grew:+.1f} MB", flush=True)
    snap("window open: every page seen")


def close_and_free():
    w = c.window
    w.close()
    settle(0.5)
    c._free_window()
    settle(0.5)
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # (the event loop's job in the app)
    settle(0.5)
    gc.collect()
    settle(0.5)
    c.tidy_up()
    settle(0.5)
    snap("window closed and freed")


def held_folders() -> str:
    """Space folders still alive (should be none once the maps are forgotten), and what holds the top one."""
    from corefold import space as sp
    gc.collect()
    folders = [o for o in gc.get_objects() if isinstance(o, sp.Folder)]
    if not folders:
        return "0"
    holders = []
    for top in [f for f in folders if f.parent is None][:2]:
        for r in gc.get_referrers(top):
            if r is folders or isinstance(r, sp.Folder):
                continue
            for x in gc.get_referrers(r) if isinstance(r, (list, dict)) else [r]:
                owner = next((y for y in gc.get_referrers(x) if getattr(y, "__dict__", None) is x), None) \
                    if isinstance(x, dict) else None
                if x is not folders and type(x).__name__ not in ("frame", "list_iterator"):
                    holders.append(type(owner or x).__name__)
    return f"{len(folders):,} (held by {', '.join(sorted(set(holders))) or '?'})"


def space_look():
    """Space looks through a drive (the map shown, then left for Home and forgotten, as after 5 minutes)."""
    target = Path(os.environ.get("MEMTEST_SPACE", "C:\\" if sys.platform == "win32" else "/usr"))
    w = c.window
    if not w.shows("Space"):
        return
    w.open_page("Space")
    settle(1)
    page = w.space_page
    page.show_section("map")
    mods_before = set(sys.modules)
    if os.environ.get("MEMTEST_SURVIVORS"):
        import tracemalloc
        tracemalloc.start(16)
    page.start_scan(target)
    t = time.monotonic()
    while (page.job is not None or page.scan is None) and time.monotonic() - t < 900:
        settle(0.5)
    files = page.scan.files if page.scan is not None else 0
    if os.environ.get("MEMTEST_MODS"):
        print("NEW MODULES:", " ".join(sorted(set(sys.modules) - mods_before)), flush=True)
    settle(2)
    snap(f"Space map shown ({files:,} files, {time.monotonic() - t:.0f} s)")
    w.open_page("Home")
    settle(1)
    page._forget_old_maps()
    settle(1)
    c.tidy_up()
    snap("Space map forgotten (left for Home)")
    if os.environ.get("MEMTEST_SURVIVORS"):  # (what was made during the look and is still alive: it keeps
        import tracemalloc                    # Python's arenas from going back to Windows)
        snapshot = tracemalloc.take_snapshot()
        tracemalloc.stop()
        stats = snapshot.statistics("traceback")
        print(f"SURVIVORS: {sum(st.count for st in stats):,} blocks, {sum(st.size for st in stats) / 2**20:.1f} MB",
              flush=True)

        def imported(st):
            return any("importlib" in fr.filename or fr.filename.endswith("linecache.py") for fr in st.traceback)
        rest = [st for st in stats if not imported(st)]
        print(f"SURVIVORS not from reading modules: {sum(st.count for st in rest):,} blocks, "
              f"{sum(st.size for st in rest) / 2**20:.2f} MB, made at {len(rest):,} places", flush=True)
        for st in rest[:60]:
            print(f"  {st.count:7,} blocks {st.size / 1024:9.1f} KB", flush=True)
            for fr in st.traceback.format()[-12:]:
                print("     " + fr.strip(), flush=True)
    notes.append(f"Space folders still in memory after the maps were forgotten: {held_folders()}")


notes: list[str] = []


open_all(True)
memmap("window open, every page seen")
close_and_free()
memmap("window closed and freed")
for _ in range(2):
    open_all(False)
    close_and_free()
open_all(False)
space_look()
memmap("Space map forgotten")
close_and_free()
open_all(True)
close_and_free()

threads = threading.active_count()
notes.append(f"Space folders still in memory at the end: {held_folders()}")
blas = "numpy" in sys.modules
text = ["## Memory, step by step", "", f"{sys.platform}, {LANG}, {LOOK}; numpy loaded at the end: {blas}; "
        f"Python threads at the end: {threads}", "", *[f"- {n}" for n in notes], "",
        "| Step | Private memory | Change | Working set | Python objects / arenas | Widgets alive | Modules |",
        "|---|---|---|---|---|---|---|"]
text += [f"| {w} | {mb:.1f} MB | {d:+.1f} | {ws:.1f} MB | {u:.1f} / {h:.1f} MB | {n} | {m} |"
         for w, mb, d, ws, h, u, n, m in rows]
text += [f"\n### Memory map: {what}\n\n```\n{out}```" for what, out in maps]
(OUT / "memtest.md").write_text("\n".join(text) + "\n", "utf-8")
print("\n".join(text), flush=True)
os._exit(0)
