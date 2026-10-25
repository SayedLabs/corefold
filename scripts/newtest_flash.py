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

from PySide6.QtCore import QEvent, QObject, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QCursor, QIcon, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

qapp = QApplication(sys.argv)
qapp.setQuitOnLastWindowClosed(False)
from corefold import i18n  # noqa: E402
i18n.set_language(s.language)
from corefold import app as A  # noqa: E402

A.setup_logging()
errors = []


class ShowSpy(QObject):
    """Qt's side of it: which widget each top-level window was, and what in the code showed it (so a
    window that flashed can be traced to the line that showed it)."""

    def __init__(self):
        super().__init__()
        self.open = {}
        self.quick = []  # (widget's class and size, ms it stayed, where it was shown from)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t in (QEvent.Show, QEvent.Hide) and isinstance(obj, QWidget) and obj.isWindow():
            key = id(obj)
            if t == QEvent.Show:
                stack = [f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in traceback.extract_stack()[:-1]
                         if "corefold" in f.filename.replace("\\", "/")]
                self.open[key] = (time.monotonic(), f"{type(obj).__module__}.{type(obj).__name__} "
                                  f"{obj.width()}x{obj.height()} '{obj.windowTitle()}' parent "
                                  f"{type(obj.parent()).__name__ if obj.parent() else None}", stack[-6:])
            elif key in self.open:
                at, what, stack = self.open.pop(key)
                if time.monotonic() - at < 0.6:
                    self.quick.append((what, (time.monotonic() - at) * 1000, stack, watch.label))
        return False


spy = ShowSpy()
qapp.installEventFilter(spy)


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
    from corefold import sheet
    box = qapp.activeModalWidget()
    if box is not None:
        say(f"note  a dialog came up: {box.windowTitle()}")
        box.reject()
    for layer in sheet.open_sheets():  # (questions show inside the window now, as sheets)
        if layer.dlg is not None and time.monotonic() - getattr(layer, "seen_at", time.monotonic()) > 2:
            say(f"note  a sheet came up: {layer.dlg.windowTitle()}")
            layer.dlg.reject()
        layer.seen_at = getattr(layer, "seen_at", time.monotonic())


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


def switching_tools():
    """Switching tools off and on in their section, the way a person does (the window follows)."""
    w = c.window
    for tool in [t for t in all_tools.ready() if t.id not in all_tools.ORIGINAL][:6]:
        sec = all_tools.SECTION_OF[tool.id]
        w.open_page(sec.page)
        settle(0.8)
        doing(f"switching {tool.id} off in {sec.page}")
        c.set_tool(tool.id, False)
        settle(1.2)
        doing(f"switching {tool.id} on in {sec.page}")
        c.set_tool(tool.id, True)
        settle(1.5)
        w = c.window
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


# ---- the real mouse, for what only a person's hand does (picking a widget up, a right-click, pointing)
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x2, 0x4, 0x8, 0x10


def mouse_to(x: int, y: int, steps: int = 1, wait: float = 0.0):
    start = QCursor.pos()
    for i in range(1, steps + 1):
        ctypes.windll.user32.SetCursorPos(int(start.x() + (x - start.x()) * i / steps),
                                          int(start.y() + (y - start.y()) * i / steps))
        settle(wait)


def mouse(flag: int):
    ctypes.windll.user32.mouse_event(flag, 0, 0, 0, 0)


def inside_the_window():
    """Everything that used to open a little window of its own, done by hand in the window: none may open
    one now, not even for a moment (a question, a dialog, a menu, a dropdown's list, a tip, a widget picked
    up and moved on the Home page, the corner message while the window is in use)."""
    from corefold import sheet
    from corefold import tools as all_tools
    from corefold.atomic.widgets import Dropdown
    c.show_window("Home")
    w = c.window
    w.raise_()
    w.activateWindow()
    settle(1.5)
    t0 = time.monotonic()
    mine = {int(w.winId())}
    board = w.home_board

    doing("changing the Home page and moving a widget by hand")
    board.set_editing(True)
    settle(0.8)
    first, second = board.frames[0], board.frames[1]
    a = first.mapToGlobal(first.rect().center())
    b = second.mapToGlobal(second.rect().center())
    mouse_to(a.x(), a.y(), 1, 0.2)
    mouse(MOUSEEVENTF_LEFTDOWN)
    settle(0.1)
    mouse_to(b.x(), b.y() + 20, 25, 0.02)
    settle(0.4)
    mouse(MOUSEEVENTF_LEFTUP)
    settle(0.8)
    check(board.lifted is None, "a widget picked up by hand is put down again")
    doing("a widget's right-click menu")
    p = board.frames[0].mapToGlobal(board.frames[0].rect().center())
    QTimer.singleShot(900, lambda: [la.picked(None) for la in w.findChildren(sheet._MenuLayer)])
    mouse_to(p.x(), p.y(), 1, 0.1)
    board.set_editing(False)
    settle(0.5)
    mouse(MOUSEEVENTF_RIGHTDOWN)
    mouse(MOUSEEVENTF_RIGHTUP)
    settle(1.5)
    doing("pointing at a button (its tip)")
    q = w.lang_btn.mapToGlobal(w.lang_btn.rect().center())
    mouse_to(q.x(), q.y(), 6, 0.05)
    settle(1.8)
    doing("a question")
    QTimer.singleShot(1200, lambda: [la.dlg.reject() for la in sheet.open_sheets()])
    w._remove_tool(all_tools.BY_ID["organizer"])
    settle(0.8)
    doing("adding a widget (the gallery)")
    QTimer.singleShot(1200, lambda: [la.dlg.reject() for la in sheet.open_sheets()])
    board.open_gallery()
    settle(0.8)
    doing("a dropdown's list")
    w.open_page("General")
    settle(1.2)
    boxes = [d for d in w.findChildren(Dropdown) if d.isVisible()]
    if boxes:
        r = boxes[0].mapToGlobal(boxes[0].rect().center())
        QTimer.singleShot(1200, lambda: [la.picked(None) for la in w.findChildren(sheet._MenuLayer)])
        mouse_to(r.x(), r.y(), 4, 0.05)
        mouse(MOUSEEVENTF_LEFTDOWN)
        mouse(MOUSEEVENTF_LEFTUP)
        settle(1.8)
    doing("a message while the window is in use")
    w.open_page("Home")
    c.say("In the window", "a message inside Corefold's window")
    settle(2)
    c.toast.hide()
    doing("a dialog asked from the tray (it opens the window, inside it)")
    w.hide()
    settle(0.8)
    from corefold import dialogs
    QTimer.singleShot(1500, lambda: [la.dlg.reject() for la in sheet.open_sheets()])
    dialogs.ask("resize", [picture], c.icon, c.theme.dark)
    settle(1)
    mine.add(int(c.window.winId()))
    extra = watch.own_windows(t0, mine)
    for line in extra:
        check(False, line)
    check(not extra, "nothing opened a window of its own: it all showed inside Corefold's window")


def hide_and_wait():
    doing("the window closed, idle in the tray")
    w = c.window_if_open()
    if w is not None:
        w.hide()
    settle(20)


for name, fn in (("every page", every_page), ("inside the window", inside_the_window),
                 ("switching tools", switching_tools), ("tool windows", tool_windows), ("jobs", jobs),
                 ("idle", hide_and_wait)):
    part(name, fn)

watch.stop()
for line in watch.notes():
    LOG.write("seen  " + line + "\n")
for what, ms, stack, label in spy.quick:
    say(f"note  Qt showed and hid {what} within {ms:.0f} ms (while: {label}), shown from: " + " < ".join(reversed(stack)))
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
from testexit import finish  # (Qt can crash in Windows' tidy-up at exit, after every check passed)
finish(1 if problems else 0)
