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


def settle(seconds=0.3):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        qapp.processEvents()
        time.sleep(0.002)


rows: list[tuple[str, float, float, float, int, int]] = []
_last = [0.0]


def snap(what: str):
    gc.collect()
    settle(0.05)
    mb = private_mb()
    widgets = len(QApplication.allWidgets())
    mods = len(sys.modules)
    ws = working_set_mb()
    rows.append((what, mb, mb - _last[0], ws, widgets, mods))
    print(f"{what:<48} {mb:8.1f} MB  {mb - _last[0]:+7.1f}  ws {ws:7.1f}  widgets {widgets:6d}  modules {mods}",
          flush=True)
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


def space_look():
    """Space looks through a drive (the map shown, then left for Home and forgotten, as after 5 minutes)."""
    from corefold.window import PAGES as _P  # noqa: F401
    target = Path(os.environ.get("MEMTEST_SPACE", "C:\\" if sys.platform == "win32" else "/usr"))
    w = c.window
    if not w.shows("Space"):
        return
    w.open_page("Space")
    settle(1)
    page = w.space_page
    page.show_section("map")
    page.start_scan(target)
    t = time.monotonic()
    while (page.job is not None or page.scan is None) and time.monotonic() - t < 900:
        settle(0.5)
    files = page.scan.files if page.scan is not None else 0
    settle(2)
    snap(f"Space map shown ({files:,} files, {time.monotonic() - t:.0f} s)")
    w.open_page("Home")
    settle(1)
    page._forget_old_maps()
    settle(1)
    c.tidy_up()
    snap("Space map forgotten (left for Home)")


open_all(True)
close_and_free()
for i in range(2):
    open_all(False)
    close_and_free()
open_all(False)
space_look()
close_and_free()
open_all(True)
close_and_free()

threads = threading.active_count()
blas = "numpy" in sys.modules
text = ["## Memory, step by step", "", f"{sys.platform}, {LANG}, {LOOK}; numpy loaded at the end: {blas}; "
        f"Python threads at the end: {threads}", "",
        "| Step | Private memory | Change | Working set | Widgets alive | Modules |", "|---|---|---|---|---|---|"]
text += [f"| {w} | {mb:.1f} MB | {d:+.1f} | {ws:.1f} MB | {n} | {m} |" for w, mb, d, ws, n, m in rows]
(OUT / "memtest.md").write_text("\n".join(text) + "\n", "utf-8")
print("\n".join(text), flush=True)
os._exit(0)
