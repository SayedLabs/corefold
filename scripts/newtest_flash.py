"""Checks on a real Windows PC that Corefold never flashes a window: no console window (a black box from a
program started without CREATE_NO_WINDOW), no window from a program Corefold starts in the background, and
no window of its own that shows up and is gone again a moment later. Every tool is switched on, the app
starts the way it does with Windows, the window opens every page, and the tools' own windows, popups and
jobs (a picture, an archive, a conversion) are opened and used, while flashwatch.py notes every window
Windows shows.

This test lets go of its console first, the way Corefold.exe has none: a program started without
CREATE_NO_WINDOW then really opens a console window here, as it would on people's PCs.

Run from the repository: python scripts/newtest_flash.py [folder]
Prints one line per check and "newtest_flash: N problem(s)"."""
import ctypes
import os
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "out" / "newtest_flash").resolve()
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-flashtest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
LOG = open(OUT / "newtest_flash.txt", "w", encoding="utf-8")


def say(line: str):
    """To the output file, and to the console or pipe the test was started with (if it still has one)."""
    LOG.write(line + "\n")
    LOG.flush()
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        pass


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
# no console of its own from here on (output that goes to a file or a pipe keeps going there)
ctypes.windll.kernel32.FreeConsole()

import faulthandler  # noqa: E402

START = time.time()
LIMIT = float(os.environ.get("NEWTEST_LIMIT", "420"))
faulthandler.dump_traceback_later(LIMIT + 20, exit=True, file=LOG)
problems = []


def _out_of_time():
    say(f"FAIL  the test ran out of time after {LIMIT:.0f} s, stuck here:")
    faulthandler.dump_traceback(file=LOG, all_threads=True)
    say(f"newtest_flash: {len(problems) + 1} problem(s)")
    os._exit(1)


_watchdog = threading.Timer(LIMIT, _out_of_time)
_watchdog.daemon = True
_watchdog.start()

from flashwatch import FlashWatch  # noqa: E402

from corefold import settings as st  # noqa: E402
from corefold import tools as all_tools  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.check_updates = False
s.quick_told = True
s.language = "en"
s.appearance = "dark"
s.known_tools = sorted(t.id for t in all_tools.TOOLS)
s.tools_off = []  # every tool on: each one starts what it needs in the background
s.save()

watch = FlashWatch().start()

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QIcon, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

qapp = QApplication(sys.argv)
qapp.setQuitOnLastWindowClosed(False)
from corefold import i18n  # noqa: E402
i18n.set_language(s.language)
from corefold import app as A  # noqa: E402

A.setup_logging()
errors = []


def _hook(t, e, tb):
    text = "".join(traceback.format_exception(t, e, tb))
    errors.append(text)
    say("ERROR " + text)


sys.excepthook = _hook


def check(ok, what):
    say(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        problems.append(what)
    return ok


def settle(seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)


def doing(what: str):
    watch.label = what
    say(f"note  {what}")


def part(name, fn):
    doing(name)
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")
        LOG.write(traceback.format_exc())
    settle(0.5)


# a message box that comes up (it would wait for a click) is closed after a while, not at once: a
# window that's closed within a moment of showing is what this test is looking for
def _close_dialogs():
    box = qapp.activeModalWidget()
    if box is not None:
        say(f"note  a dialog came up: {box.windowTitle()}")
        box.reject()


_closer = QTimer()
_closer.timeout.connect(_close_dialogs)
_closer.start(2500)

# ---- files to open
picture = TMP / "picture.png"
img = QImage(640, 400, QImage.Format_RGB32)
img.fill(Qt.darkCyan)
img.save(str(picture))
archive = TMP / "box.zip"
with zipfile.ZipFile(archive, "w") as z:
    z.writestr("inside/readme.txt", "hello\n" * 100)
    z.writestr("photo.png", picture.read_bytes())

# ---- start the way it starts with Windows: the tray, every tool's background part
doing("starting in the background")
c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
settle(15)  # (what tools start a few seconds after Windows does)


def every_page():
    from corefold.window import PAGES
    c.show_window("Home")
    settle(2)
    w = c.window
    for name in PAGES:
        doing(f"opening the {name} page")
        try:
            w.open_page(name)
        except Exception:
            check(False, f"the {name} page opens: {traceback.format_exc().splitlines()[-1]}")
        settle(1.5)  # (each page's background part: listing apps, startup entries, drives, Defender…)
    w.open_page("Home")
    settle(1)


def tool_windows():
    doing("the Viewer")
    c.open_viewer(picture)
    settle(2.5)
    for v in list(c.viewers):
        v.close()
    settle(1)
    doing("an archive window")
    c.open_browser(archive)
    settle(4)
    for b in list(c.browsers):
        b.close()
    settle(1)
    if c.clip is not None:
        doing("the clipboard popup")
        c.show_clipboard(from_tray=True)
        settle(1.5)
        if c.clip.popup is not None:
            c.clip.popup.hide()
        settle(1)
    if c.quick is not None:
        doing("the quick menu")
        c.quick.toggle_menu()
        settle(1.5)
        c.quick.toggle_menu()
        settle(1)
        doing("the quick wheel")
        c.quick.open_wheel_at()
        settle(1.5)
        if c.quick.wheel is not None:
            c.quick.wheel.hide()
        settle(1)
    doing("the drag wheel")
    c.wheel.popup_at_cursor([picture])
    settle(1.5)
    c.wheel.hide()
    settle(1)
    doing("a message in the corner")
    c.say("Flash test", "a message in the corner")
    settle(3)


def jobs():
    from corefold.converters import pick
    tools = [sl.tool for sl in pick([picture]).slots if sl.ok and sl.tool.run is not None and not sl.tool.ask]
    jpg = next((t for t in tools if t.key == "image:jpg"), tools[0] if tools else None)
    if check(jpg is not None, "a picture has something to be turned into"):
        doing(f"a conversion ({jpg.key})")
        c.start_job(jpg, [picture])
        settle(8)
    tools = [sl.tool for sl in pick([archive]).slots if sl.ok and sl.tool.run is not None and not sl.tool.ask]
    ex = next((t for t in tools if "extract" in t.key), None)
    if ex is not None:
        doing(f"extracting an archive ({ex.key})")
        c.start_job(ex, [archive])
        settle(8)


def hide_and_wait():
    doing("the window closed, idle in the tray")
    w = c.window_if_open()
    if w is not None:
        w.hide()
    settle(20)


for name, fn in (("every page", every_page), ("tool windows", tool_windows), ("jobs", jobs),
                 ("idle", hide_and_wait)):
    part(name, fn)

watch.stop()
for line in watch.notes():
    LOG.write("seen  " + line + "\n")
flashes = watch.problems()
for line in flashes:
    check(False, line)
check(not flashes, f"no window flashed ({len(watch.events)} windows shown in {time.time() - START:.0f} s)")
check(not errors, f"no errors behind the steps ({len(errors)})")
log_file = st.LOG_DIR / "corefold.log"
if log_file.exists():
    (OUT / "newtest_flash-log.txt").write_text(log_file.read_text("utf-8", "replace"), "utf-8")
say(f"newtest_flash: {len(problems)} problem(s)")
LOG.close()
faulthandler.disable()
os._exit(1 if problems else 0)
