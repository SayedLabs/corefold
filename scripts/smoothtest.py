"""How smooth Corefold feels, measured on a real Windows PC: every page opened (the first time and again),
the sidebar, light and dark, another language, a tool switched off and on, the window made bigger and smaller,
pages scrolled, a question shown and closed, the Home page changed, and the window doing nothing (what it
costs then). A heartbeat on the window's thread notes every moment it couldn't draw: a gap over two frames is
a stutter a person may see, over a quarter of a second the window feels stuck. Every stutter says where the
time went (the window thread's stack), and any window that shows outside Corefold's own is noted.

Run: python scripts/smoothtest.py <folder> [en|ar] [dark|light]
Writes <folder>/smoothtest.md (a table, worst first) and prints "smoothtest: N problem(s)"."""
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "smoothtest").resolve()
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
LOOK = sys.argv[3] if len(sys.argv) > 3 else "dark"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-smoothtest-"))
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
faulthandler.dump_traceback_later(1500, exit=True)  # stuck somewhere: say where, and stop

from corefold import settings as st  # noqa: E402
from corefold import tools as all_tools  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.check_updates = False
s.animations = True  # (the movement is part of what's measured)
s.language = LANG
s.appearance = LOOK
s.known_tools = sorted(t.id for t in all_tools.ready())
s.tools_off = []  # every tool there is in use: the most the window ever has to do
s.save()

from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractScrollArea, QApplication  # noqa: E402

from corefold import i18n  # noqa: E402
i18n.set_language(LANG)
qapp = QApplication(sys.argv)
qapp.setQuitOnLastWindowClosed(False)
if i18n.is_rtl():
    qapp.setLayoutDirection(Qt.RightToLeft)

# The first text drawn on a PC that just started waits for Windows to read its fonts (3-13 s on GitHub's fresh
# PCs, never on a person's PC that's been on a while): done before anything's timed.
from PySide6.QtGui import QFont, QFontMetrics  # noqa: E402
from corefold.atomic import tokens as _tokens  # noqa: E402
for _fam in {_tokens.TEXT_FAMILY, _tokens.FONT_DISPLAY.strip('"'), "Segoe UI"}:
    for _bold in (False, True):
        _f = QFont(_fam, 11)
        _f.setBold(_bold)
        QFontMetrics(_f).horizontalAdvance("Corefold مرحبا ✓ → 123")

FRAME = 1 / 60
STUTTER = 1.5 * FRAME   # a gap this long drops a frame or more: a moving thing jumps
STUCK = 0.25            # the window not answering this long feels stuck


class Beat:
    """A heartbeat every 2 ms on the window's thread; a second thread notes, for every gap, how long it was and
    what the window's thread was doing in the middle of it."""

    def __init__(self):
        self.last = time.perf_counter()
        self.label = ""
        self.gaps: list[tuple[str, float, str]] = []
        self.main = threading.main_thread().ident
        self._where = ""
        self._seen_at = 0.0
        self.timer = QTimer()
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(2)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        threading.Thread(target=self._watch, daemon=True).start()

    def _tick(self):
        now = time.perf_counter()
        gap = now - self.last
        if gap > STUTTER and self.label:
            self.gaps.append((self.label, gap, self._where if self._seen_at > self.last else "?"))
        self.last = now

    def _watch(self):
        while True:
            time.sleep(0.008)
            if time.perf_counter() - self.last > STUTTER * 0.8 and self._seen_at <= self.last:
                self._seen_at = time.perf_counter()
                self._where = _where(sys._current_frames().get(self.main))


def _where(frame) -> str:
    if frame is None:
        return "?"
    stack = traceback.extract_stack(frame)
    mine = [f for f in stack if "corefold" in f.filename.replace("\\", "/") and "/scripts/" not in
            f.filename.replace("\\", "/")]
    pick = (mine or stack)[-4:]
    return " < ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in reversed(pick))


beat = Beat()
results: list[dict] = []
problems: list[str] = []
errors: list[str] = []


def _hook(t, e, tb):
    text = "".join(traceback.format_exception(t, e, tb))
    errors.append(text)
    print("ERROR " + text, flush=True)


sys.excepthook = _hook


def settle(seconds=0.3):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        qapp.processEvents()
        time.sleep(0.001)


def measure(label: str, fn, after=0.5, moving=True, limit=None):
    """Does it, then waits `after` seconds for what it starts (movement, pages made later) to finish.
    moving: a step with movement in it (a page gliding in, the sidebar folding), where every stutter shows;
    limit: the longest a single gap may be before it counts as a problem (the default depends on moving)."""
    settle(0.15)
    before = len(beat.gaps)
    errs = len(errors)
    beat.label = label
    cpu0, t0 = time.process_time(), time.perf_counter()
    try:
        fn()
    except Exception:
        errors.append(traceback.format_exc())
        print(f"ERROR {label}: {traceback.format_exc()}", flush=True)
    settle(after)
    took = time.perf_counter() - t0
    cpu = time.process_time() - cpu0
    beat.label = ""
    mine = beat.gaps[before:]
    worst = max((g for _, g, _ in mine), default=0.0)
    dropped = sum(max(0, round(g / FRAME) - 1) for _, g, _ in mine)
    where = max(mine, key=lambda x: x[1])[2] if mine else ""
    limit = limit if limit is not None else (0.1 if moving else STUCK)
    bad = worst > limit
    others = [w for w in qapp.topLevelWidgets() if w.isVisible() and w is not c.window and
              type(w).__name__ not in ("Toast", "QSystemTrayIcon")]
    row = dict(label=label, worst=worst, stutters=len(mine), dropped=dropped, took=took, cpu=cpu, where=where,
               bad=bad, windows=", ".join(type(w).__name__ for w in others), error=len(errors) > errs)
    results.append(row)
    flag = "SLOW " if bad else ("ERR  " if row["error"] else "ok   ")
    print(f"{flag}{label}: worst {worst * 1000:.0f} ms, {len(mine)} stutter(s), ~{dropped} frame(s) dropped"
          + (f" | {where}" if mine else "") + (f" | WINDOW {row['windows']}" if others else ""), flush=True)
    if bad:
        problems.append(f"{label}: stuck {worst * 1000:.0f} ms at {where}")
    if others:
        problems.append(f"{label}: a window of its own showed ({row['windows']})")
        for w in others:
            w.hide()
    if row["error"]:
        problems.append(f"{label}: an error")
    return row


# ---------------------------------------------------------------------------- start

from corefold import app as A  # noqa: E402
from corefold import sheet  # noqa: E402

A.setup_logging()
c = None


def _start():
    global c
    c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
    c.apply_tools()
    c.window.resize(1100, 740)
    c.show_window("Home")


beat.label = "start"
t0 = time.perf_counter()
_start()
QTest.qWaitForWindowExposed(c.window, 10000)
shown = time.perf_counter() - t0
settle(2.0)  # (what starts a moment after the window opens: counts against "start")
beat.label = ""
w = c.window
w.activateWindow()
start_gaps = [g for g in beat.gaps if g[0] == "start"]
print(f"note start: window shown after {shown:.2f} s; {len(start_gaps)} stutter(s) in the first 2 s, worst "
      f"{max((g for _, g, _ in start_gaps), default=0) * 1000:.0f} ms", flush=True)
results.append(dict(label="start (window shown)", worst=max((g for _, g, _ in start_gaps), default=0.0),
                    stutters=len(start_gaps), dropped=0, took=shown, cpu=time.process_time(),
                    where=max(start_gaps, key=lambda x: x[1])[2] if start_gaps else "", bad=False, windows="",
                    error=False))

from corefold.window import PAGES  # noqa: E402



# (which pages open, found without timing: made the first time below, cold)
pages = [p for p in PAGES if w.shows(p) or p in ("General", "Look", "Quick", "Shortcuts", "Updates", "About")]

# ---------------------------------------------------------------------------- every page, first time and again

for rnd in ("first time", "again"):
    for name in pages:
        if name == "Home" and rnd == "first time":
            continue
        measure(f"open {name} ({rnd})", lambda n=name: w.open_page(n), after=0.45)
    measure(f"open Home ({rnd})", lambda: w.open_page("Home"), after=0.45)


# ---------------------------------------------------------------------------- scrolling


def _scroller():
    page = w.stack.currentWidget()
    areas = [a for a in page.findChildren(QAbstractScrollArea) if a.isVisible()
             and a.verticalScrollBar().maximum() > 0]
    if isinstance(page, QAbstractScrollArea) and page.verticalScrollBar().maximum() > 0:
        areas.insert(0, page)
    return max(areas, key=lambda a: a.viewport().width() * a.viewport().height(), default=None)


def scroll_page(name):
    w.open_page(name)
    settle(0.5)
    area = _scroller()
    if area is None:
        return
    bar = area.verticalScrollBar()

    def run():
        for _ in range(2):  # down and up, like a mouse wheel, 60 times a second
            for v in [bar.maximum() * i // 30 for i in range(31)]:
                bar.setValue(v)
                settle(FRAME)
            for v in [bar.maximum() * i // 30 for i in range(30, -1, -1)]:
                bar.setValue(v)
                settle(FRAME)

    measure(f"scroll {name}", run, after=0.2)


for name in pages:
    scroll_page(name)
w.open_page("Home")
settle(0.4)

# ---------------------------------------------------------------------------- hover across the sidebar


def hover_nav():
    nav = w.nav
    for row in range(nav.count()):
        r = nav.visualItemRect(nav.item(row))
        QTest.mouseMove(nav.viewport(), r.center())
        settle(0.03)
    QTest.mouseMove(w, QPoint(w.width() // 2, w.height() // 2))


measure("hover over the sidebar", hover_nav, after=0.3)

# ---------------------------------------------------------------------------- sidebar folding, look, language

for i in range(2):
    measure(f"fold the sidebar ({i + 1})", w.toggle_nav, after=0.6)
for i in range(2):
    measure(f"light/dark switch ({i + 1})", w.toggle_dark, after=0.6, moving=False, limit=1.0)
other = "en" if LANG == "ar" else "ar"
measure(f"language -> {other}", lambda: c.switch_language(other), after=0.8, moving=False, limit=1.5)
measure(f"language -> {LANG}", lambda: c.switch_language(LANG), after=0.8, moving=False, limit=1.5)
w = c.window

# ---------------------------------------------------------------------------- window size


def resize_window():
    for wd, ht in [(1100, 740), (1000, 700), (900, 650), (820, 600), (1000, 720), (1200, 800), (1400, 880),
                   (1100, 740)]:
        w.resize(wd, ht)
        settle(FRAME)


for name in ("Home", "Files", "Cloud", "General"):
    if name in pages:
        w.open_page(name)
        settle(0.4)
        measure(f"resize on {name}", resize_window, after=0.3)
w.open_page("Home")
settle(0.3)

# ---------------------------------------------------------------------------- a question, a menu


def question():
    QTimer.singleShot(700, lambda: sheet.dismiss(sheet.waiting()) if sheet.waiting() is not None else None)
    sheet.question(w, "Smooth?", "A question shown inside the window, and closed.")


measure("a question opens and closes", question, after=0.6)


def menu():
    from PySide6.QtWidgets import QMenu
    m = QMenu(w)
    for i in range(6):
        m.addAction(f"Row {i + 1}")
    QTimer.singleShot(600, lambda: sheet.dismiss(sheet.waiting()) if sheet.waiting() is not None else None)
    sheet.menu(m, w.mapToGlobal(QPoint(w.width() // 2, w.height() // 3)))


measure("a menu opens and closes", menu, after=0.6)

# ---------------------------------------------------------------------------- the Home page, changed

board = getattr(w, "home_board", None)
if board is not None:
    measure("Home: customize on", lambda: board.set_editing(True), after=0.6)
    measure("Home: customize off", lambda: board.set_editing(False), after=0.6)

# ---------------------------------------------------------------------------- a tool off and on

files_on = [t for t in all_tools.SECTION_BY_PAGE["Files"].tools if all_tools.state(t) == all_tools.AVAILABLE] \
    if "Files" in all_tools.SECTION_BY_PAGE else []
if files_on:
    w.open_page("Files")
    settle(0.5)
    tool = files_on[-1]
    measure(f"switch {tool} off", lambda: c.set_tool(tool, False), after=0.8, moving=False, limit=1.0)
    w = c.window
    measure(f"switch {tool} on", lambda: c.set_tool(tool, True), after=0.8, moving=False, limit=1.0)
    w = c.window

# ---------------------------------------------------------------------------- doing nothing


def idle(label, seconds=15):
    settle(1.0)
    beat.timer.stop()  # (the test's own heartbeat isn't the app's work)
    cpu0, t0 = time.process_time(), time.perf_counter()
    loop = QEventLoop()  # (Qt's own waiting, as in the app: a test loop's polling isn't counted)
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()
    share = (time.process_time() - cpu0) / (time.perf_counter() - t0) * 100
    beat.timer.start()
    print(f"note {label}: {share:.2f} % of one CPU core", flush=True)
    results.append(dict(label=label, worst=0.0, stutters=0, dropped=0, took=seconds, cpu=share, where="",
                        bad=share > 1.0, windows="", error=False, idle=True))
    if share > 1.0:
        problems.append(f"{label}: {share:.2f} % CPU doing nothing")


w.open_page("Home")
idle("idle, window open on Home")
w.open_page("Files")
idle("idle, window open on Files")
w.hide()
idle("idle, window closed to the tray")

# ---------------------------------------------------------------------------- what it found

if errors:
    problems.append(f"{len(errors)} error(s)")


def _row(r):
    if r.get("idle"):
        return f"| {r['label']} | | | | | {r['cpu']:.2f} % CPU | |"
    return (f"| {r['label']} | {r['worst'] * 1000:.0f} | {r['stutters']} | {r['dropped']} | {r['took']:.2f} | "
            f"{r['cpu']:.2f} | {r['where'].replace('|', '/')} |")


lines = [f"# Smoothness: {LANG}, {LOOK}", "",
         f"{len(problems)} problem(s). Worst gap in ms; a stutter is a gap over {STUTTER * 1000:.0f} ms "
         f"(a frame dropped). Time and CPU in seconds.", ""]
lines += [f"- {p}" for p in problems] + [""]
lines += ["| step | worst ms | stutters | frames dropped | time s | CPU s | where the worst one was |",
          "|---|---|---|---|---|---|---|"]
lines += [_row(r) for r in sorted(results, key=lambda r: -r["worst"])]
(OUT / "smoothtest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
if errors:
    (OUT / "smoothtest-errors.txt").write_text("\n\n".join(errors), encoding="utf-8")
print(f"smoothtest: {len(problems)} problem(s)", flush=True)
for p in problems:
    print(f"  {p}", flush=True)
os._exit(0)
