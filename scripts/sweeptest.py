"""Presses every button on every page of the main window, on a real Windows PC, and looks for what a
person would call a bug: an error behind a click, a page that doesn't open or can't be left, text that's
cut off, something that sticks out of the window, a tool that keeps running after it's removed from the
Store, or memory that keeps growing when tools are added and removed again and again.

Run: python scripts/sweeptest.py <folder> [options]
  --lang en|ar|fr|es|tr   --look dark|light   --size 1040x700|min|max   --first-run
  --keep <folder>         use the settings already in this folder (an upgrade from an older version)
  --quick                 skip the button presses (pages, sizes and the Store only)
Prints one line per check and "sweeptest: N problem(s)"; the list is also saved in the folder."""
import argparse
import gc
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("out", nargs="?", default="sweeptest")
ap.add_argument("--lang", default="en")
ap.add_argument("--look", default="dark")
ap.add_argument("--size", default="1040x700")
ap.add_argument("--first-run", action="store_true")
ap.add_argument("--keep", default="")
ap.add_argument("--quick", action="store_true")
ap.add_argument("--rounds", type=int, default=3, help="how many times every tool is removed and added back")
ap.add_argument("--pages", default="", help="only these pages (comma-separated), to look at one closely")
ap.add_argument("--only", default="", help="only these parts (comma-separated: pages,buttons,keyboard,tray,sizes,store)")
ARGS = ap.parse_args()
OUT = Path(ARGS.out).resolve()
OUT.mkdir(parents=True, exist_ok=True)
TAG = f"{ARGS.lang}-{ARGS.look}-{ARGS.size}" + ("-first" if ARGS.first_run else "") + ("-upgrade" if ARGS.keep else "")
if os.environ.get("QT_SCALE_FACTOR"):
    TAG += "-x" + os.environ["QT_SCALE_FACTOR"]
TMP = Path(ARGS.keep).resolve() if ARGS.keep else Path(tempfile.mkdtemp(prefix="corefold-sweep-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(int(os.environ.get("SWEEP_STUCK", "1500")), exit=True)  # stuck somewhere: say where, and stop

from corefold import settings as st  # noqa: E402
from corefold import tools as all_tools  # noqa: E402

s = st.Settings.load() if ARGS.keep else st.Settings()
s.check_updates = False
s.language = ARGS.lang
s.appearance = ARGS.look
if not ARGS.keep:
    s.first_run = ARGS.first_run
    s.tour_done = not ARGS.first_run
    if not ARGS.first_run:
        s.known_tools = sorted(t.id for t in all_tools.ready())
        s.tools_off = []
s.save()

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QAbstractScrollArea, QApplication, QCheckBox,  # noqa: E402
                               QComboBox, QDialog, QFileDialog, QLabel, QMenu, QMessageBox, QPushButton,
                               QRadioButton, QSlider, QToolButton, QWidget)

from corefold import i18n  # noqa: E402
i18n.set_language(ARGS.lang)
qapp = QApplication(sys.argv)
qapp.setQuitOnLastWindowClosed(False)
if i18n.is_rtl():
    qapp.setLayoutDirection(Qt.RightToLeft)
from corefold import app as A  # noqa: E402
from corefold import window as W  # noqa: E402
from corefold.i18n import tr  # noqa: E402

A.setup_logging()
problems: list[str] = []
notes: list[str] = []
errors: list[str] = []
opened: list[str] = []   # what would have been opened outside Corefold (links, folders, programs)


def _hook(t, e, tb):
    """An error behind a click never reaches the person, so here it's a failed check."""
    text = "".join(traceback.format_exception(t, e, tb))
    errors.append(text)
    print("ERROR " + text, flush=True)


sys.excepthook = _hook

# ---- nothing leaves the test: no links, folders or programs open, no file pickers wait for a person

QDesktopServices.openUrl = staticmethod(lambda url: opened.append(QUrl(url).toString()) or True)
if hasattr(os, "startfile"):
    os.startfile = lambda *a, **k: opened.append(f"startfile {a}")
_real_popen = subprocess.Popen


def _blocked(args) -> bool:
    text = " ".join(map(str, args)) if isinstance(args, (list, tuple)) else str(args)
    # Corefold restarting itself, Explorer, an installer or an uninstaller: only noted
    return "--restarted" in text or "explorer" in text.lower() or "/SILENT" in text or "unins" in text.lower()


class _Popen(_real_popen):
    """subprocess.Popen, but what would open a program outside the test is only noted. (A class, not a
    function: asyncio makes its own Popen from this one.)"""

    def __init__(self, args, *a, **k):
        self._fake = _blocked(args)
        if not self._fake:
            super().__init__(args, *a, **k)
            return
        opened.append(f"program {args}")
        self._child_created = False
        self.args, self.pid, self.returncode = args, 0, 0
        self.stdin = self.stdout = self.stderr = None

    def poll(self):
        return 0 if self._fake else super().poll()

    def wait(self, *a, **k):
        return 0 if self._fake else super().wait(*a, **k)

    def communicate(self, *a, **k):
        return ("", "") if self._fake else super().communicate(*a, **k)

    def kill(self):
        if not self._fake:
            super().kill()

    def terminate(self):
        if not self._fake:
            super().terminate()


subprocess.Popen = _Popen
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([], ""))
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
QFileDialog.exec = lambda self: 0

c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
c.restart = lambda *a, **k: opened.append("restart")
c.ask_quit = lambda *a, **k: opened.append("quit")
dialogs: list[str] = []
ACCEPT = {"on": False}


def _close_dialogs():
    """A message or dialog that came up: noted and closed (or accepted, for the first-start questions)."""
    for top in (qapp.activeModalWidget(), qapp.activePopupWidget()):
        if top is None:
            continue
        text = top.text() if isinstance(top, QMessageBox) else ""
        dialogs.append(f"{top.windowTitle()}: {text}")
        print(f"note  a dialog came up: {type(top).__name__} {top.windowTitle()!r}: {text[:120]}", flush=True)
        if isinstance(top, QMenu):
            top.close()
        elif ACCEPT["on"] and isinstance(top, QDialog):
            top.accept()
        elif isinstance(top, QDialog):
            top.reject()
        else:
            top.close()


_closer = QTimer()
_closer.timeout.connect(_close_dialogs)
_closer.start(700)


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)
    return ok


def note(what):
    print("note  " + what, flush=True)
    notes.append(what)


def settle(seconds=0.25):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)


def until(cond, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def step(name, fn):
    before = len(errors)
    print(f"----- {name}", flush=True)
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()
    settle(0.3)
    check(len(errors) == before, f"{name}: no errors behind the clicks ({len(errors) - before})")


def alive(w) -> bool:
    try:
        w.objectName()
        return True
    except RuntimeError:
        return False


def apply_size(w):
    if ARGS.size == "max":
        w.showMaximized()
    elif ARGS.size == "min":
        w.showNormal()
        w.resize(w.minimumSize())
    else:
        a, b = ARGS.size.split("x")
        w.showNormal()
        w.resize(int(a), int(b))
    settle(0.4)


def every_page_name(w) -> list[str]:
    """The pages a person can reach: the sidebar's, the settings pages of the tools in use, and the
    Coming soon pages from the Store."""
    names = []
    for name in W.PAGES:
        owner = W.NAV_OWNER.get(name, name)
        if owner in getattr(w, "nav_rows", []):
            tool = all_tools.BY_PAGE.get(name)
            if tool is not None and not all_tools.is_on(c.settings, tool.id) and tool.ready:
                continue
            settings_of = [t for t in all_tools.TOOLS if name in t.settings_pages]
            if settings_of and not all_tools.is_on(c.settings, settings_of[0].id):
                continue
            names.append(name)
    if ARGS.pages:
        names = [n for n in names if n in ARGS.pages.split(",")]
    return names


def _scroll_ancestor(widget, top) -> bool:
    p = widget.parentWidget()
    while p is not None and p is not top:
        if isinstance(p, QAbstractScrollArea):
            return True
        p = p.parentWidget()
    return False


def looks_ok(w, where: str):
    """Text that's cut off, and things that stick out of the window."""
    win = QRect(QPoint(0, 0), w.size())
    cut, out = [], []
    for wid in w.findChildren(QWidget):
        if not wid.isVisible() or wid.width() <= 4 or wid.height() <= 4:
            continue
        if isinstance(wid, QLabel) and wid.text() and not wid.wordWrap() and not wid.pixmap() \
                and "<" not in wid.text() and wid.textFormat() != Qt.RichText:
            need = max(wid.fontMetrics().horizontalAdvance(line) for line in wid.text().split("\n"))
            if need > wid.contentsRect().width() - 2 * wid.margin() + 3:
                cut.append(f"label {wid.text()[:40]!r} needs {need}px, has {wid.contentsRect().width()}px")
        elif isinstance(wid, QLabel) and wid.text() and wid.wordWrap() and not wid.pixmap():
            room = wid.contentsRect().height()
            need = wid.heightForWidth(wid.width())
            if need > room + 4 and wid.maximumHeight() >= need:  # (a label that's elided on purpose has a max)
                cut.append(f"label {wid.text()[:40]!r} needs {need}px of height, has {room}px")
        elif isinstance(wid, QPushButton) and wid.text() and wid.text().strip():
            fm = wid.fontMetrics()
            need = fm.horizontalAdvance(wid.text().replace("&&", "&").replace("&", "")) + \
                (wid.iconSize().width() + 4 if not wid.icon().isNull() else 0)
            if need > wid.width() - 4:
                cut.append(f"button {wid.text()[:40]!r} needs {need}px, has {wid.width()}px")
        parent = wid.parentWidget()
        if parent is not None and wid.window() is w and not wid.isWindow() and parent.isVisible() \
                and not isinstance(parent.parentWidget(), QAbstractScrollArea) \
                and not isinstance(parent, QAbstractScrollArea) and type(parent).__name__ not in ("QStackedWidget",):
            r = wid.geometry()
            if not parent.rect().adjusted(-2, -2, 2, 2).contains(r) and r.intersects(parent.rect()):
                cut.append(f"{type(wid).__name__} {wid.objectName() or (wid.text()[:30] if hasattr(wid, 'text') and callable(wid.text) else '')!r} "
                           f"is cut by its {type(parent).__name__} {parent.objectName()!r} "
                           f"({r.x()},{r.y()} {r.width()}x{r.height()} in {parent.width()}x{parent.height()})")
        if not _scroll_ancestor(wid, w) and wid.window() is w:
            r = QRect(wid.mapTo(w, QPoint(0, 0)), wid.size())
            if not win.adjusted(-2, -2, 2, 2).contains(r) and r.intersects(win):
                out.append(f"{type(wid).__name__} {wid.objectName() or getattr(wid, 'text', lambda: '')()[:30]!r} "
                           f"at {r.x()},{r.y()} {r.width()}x{r.height()} (window {win.width()}x{win.height()})")
    # one line per page: each cut text is its own problem, so it's easy to fix one by one
    for text in sorted(set(cut))[:12]:
        check(False, f"{where}: text cut off: {text}")
    for text in sorted(set(out))[:6]:
        check(False, f"{where}: sticks out of the window: {text}")
    if not cut and not out:
        check(True, f"{where}: nothing cut off or sticking out")


def page_opens(w, name: str, what: str):
    w.open_page(name)
    settle(0.3)
    if not check(w.current_page() == name, f"{what}: the {name} page opens ({w.current_page()})"):
        return False
    here = w.stack.currentWidget()
    check(here is not None and here.isVisible() and here.width() > 150 and here.height() > 150,
          f"{what}: the {name} page shows ({here.width() if here else 0}x{here.height() if here else 0})")
    return True


def grab(w, name):
    try:
        w.grab().save(str(OUT / f"{TAG}-{name}.png"))
    except Exception:
        pass


# ---------------------------------------------------------------------------- start

def start():
    if ARGS.first_run:
        ACCEPT["on"] = True   # the first-start questions get their default answers
        c.first_start()
        settle(1.5)
        ACCEPT["on"] = False
        check(c.settings.tour_done, "the first start's tour can be finished")
        check(len(all_tools.enabled(c.settings)) >= 1, "the first start leaves some tools in use")
    w = c.window
    c.show_window("Home")
    apply_size(w)
    check(w.isVisible(), "the window opens")
    grab(w, "home")
    for t in all_tools.ready():   # everything in use, so every page is there to try
        if not all_tools.is_on(c.settings, t.id):
            c.set_tool(t.id, True)
            settle(0.4)
    w = c.window
    apply_size(w)
    check(all(all_tools.is_on(c.settings, t.id) for t in all_tools.ready()), "every ready tool can be added")


# ---------------------------------------------------------------------------- every page

def pages():
    w = c.window
    for name in every_page_name(w):
        before = len(errors)
        if page_opens(w, name, f"page {name}"):
            looks_ok(w, f"{name} page at {w.width()}x{w.height()}")
            grab(w, name.lower())
        check(len(errors) == before, f"page {name}: opens without errors")
    # Back from each page goes somewhere, and never gets stuck
    names = every_page_name(w)
    for name in names[:6]:
        w.open_page(name)
        settle(0.1)
    for _ in range(8):
        if hasattr(w, "go_back"):
            w.go_back()
            settle(0.1)
    check(w.current_page() in names, f"Back many times lands on a page ({w.current_page()})")


# ---------------------------------------------------------------------------- every button

SKIP_TEXTS = ["Uninstall", "Quit", "Exit", "Restart now", "Restart", "Sign in", "Install", "Update now",
              "Download", "Remove leftovers", "Uninstall selected", "Move to the Recycle Bin", "Delete",
              "Clean up", "Empty", "Record", "Start recording", "Take a screenshot", "Capture"]


def _skipped(text: str) -> bool:
    text = text.replace("&", "").strip()
    for s_ in SKIP_TEXTS:
        for form in (s_, tr(s_)):
            if form and text.startswith(form):
                return True
    return False


def _controls(page) -> list:
    out = []
    for wid in page.findChildren(QWidget):
        if not wid.isVisible() or not wid.isEnabled():
            continue
        if isinstance(wid, (QAbstractButton, QComboBox, QSlider)) or type(wid).__name__ in ("ToolCard", "DriveCard"):
            text = wid.text() if hasattr(wid, "text") and callable(wid.text) else ""
            if isinstance(wid, QAbstractButton) and _skipped(text):
                continue
            out.append(wid)
    return out


def _press(wid):
    if isinstance(wid, QComboBox):
        if wid.count() > 1:
            old = wid.currentIndex()
            wid.setCurrentIndex((old + 1) % wid.count())
            settle(0.2)
            if alive(wid):
                wid.setCurrentIndex(old)
    elif isinstance(wid, QSlider):
        old = wid.value()
        wid.setValue(wid.maximum())
        settle(0.1)
        if alive(wid):
            wid.setValue(wid.minimum())
            settle(0.1)
        if alive(wid):
            wid.setValue(old)
    else:
        QTest.mouseClick(wid, Qt.LeftButton, Qt.NoModifier, wid.rect().center())
        if isinstance(wid, (QCheckBox, QRadioButton)) or (isinstance(wid, QAbstractButton) and wid.isCheckable()):
            settle(0.2)
            if alive(wid) and isinstance(wid, QCheckBox):  # back as it was
                QTest.mouseClick(wid, Qt.LeftButton, Qt.NoModifier, wid.rect().center())


def buttons():
    """Every button, switch, list and slider on every page, one at a time; after each the window must
    still be open, on a page, with no errors. Settings changed by the presses are thrown away at the end."""
    names = every_page_name(c.window)
    pressed = 0
    for name in names:
        w = c.window
        if not page_opens(w, name, f"buttons on {name}"):
            continue
        i = 0
        stuck = 0
        while True:
            w = c.window
            if w.current_page() != name:
                w.open_page(name)
                settle(0.2)
            controls = _controls(w.stack.currentWidget())
            if i >= len(controls) or i > 150:
                break
            wid = controls[i]
            text = (wid.text() if hasattr(wid, "text") and callable(wid.text) else "") or wid.objectName() \
                or type(wid).__name__
            before = len(errors)
            t0 = time.time()
            try:
                _press(wid)
            except Exception:
                check(False, f"{name}: pressing {text[:40]!r}: {traceback.format_exc().splitlines()[-1]}")
            settle(0.3)
            _close_dialogs()
            settle(0.1)
            pressed += 1
            if time.time() - t0 > 3:
                note(f"{name}: pressing {text[:40]!r} took {time.time() - t0:.1f} s")
            w = c.window
            ok = len(errors) == before
            if not ok:
                check(False, f"{name}: pressing {text[:40]!r} ({type(wid).__name__}) raised an error")
            if not w.isVisible():
                check(False, f"{name}: pressing {text[:40]!r} closed the window")
                c.show_window(name)
                apply_size(c.window)
            if w.current_page() != name and not page_opens(w, name, f"back to {name} after {text[:30]!r}"):
                stuck += 1
                if stuck > 3:
                    break
            if w.is_dirty() if hasattr(w, "is_dirty") else False:
                w.discard_changes()
                settle(0.3)
            i += 1
        check(True, f"{name}: pressed {i} control(s)")
    note(f"{pressed} presses in all; would have opened: {len(opened)} ({', '.join(opened[:8])})")
    w = c.window
    if hasattr(w, "is_dirty") and w.is_dirty():
        w.discard_changes()


# ---------------------------------------------------------------------------- the Store, again and again

TOOL_MODULES = {"player": "corefold.player", "capture": "corefold.capture", "clipboard": "corefold.clipboard",
                "space": "corefold.spacepage", "viewer": "corefold.viewer", "cloud": "corefold.cloudpage"}


def _rss_mb() -> float:
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("a", ctypes.c_size_t), ("b", ctypes.c_size_t), ("c", ctypes.c_size_t),
                        ("d", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc),
                                                 pmc.cb)
        return pmc.PrivateUsage / 1e6
    except Exception:
        return 0.0


def store_cycles():
    """Every tool removed and added back, three times over: the sidebar, pages and tray follow, nothing
    of a removed tool keeps running, and the window and memory don't grow each round."""
    sizes = []
    ids = [t.id for t in all_tools.ready()]
    for round_ in range(ARGS.rounds):
        for tid in ids:
            c.set_tool(tid, False)
            settle(0.3)
            w = c.window
            tool = all_tools.BY_ID[tid] if hasattr(all_tools, "BY_ID") else next(t for t in all_tools.TOOLS if t.id == tid)
            check(tool.page not in w.nav_rows, f"round {round_ + 1}: removing {tid} takes it out of the sidebar")
            check(w.current_page() in ("Store",) or w.isVisible(), f"round {round_ + 1}: after removing {tid} the "
                                                                    f"window is still open ({w.current_page()})")
            if tid == "clipboard":
                check(c.clip is None, "a removed Clipboard stops watching what's copied")
            if tid == "capture":
                check(c.capture is None, "a removed Capture lets go of its shortcuts")
            w.open_page(tool.page)
            settle(0.1)
            check(w.current_page() != tool.page, f"round {round_ + 1}: a removed {tid} page can't be opened")
        check(len(all_tools.enabled(c.settings)) == 0, f"round {round_ + 1}: every tool can be removed")
        c.show_window("Home")
        settle(0.3)
        looks_ok(c.window, f"Home with no tools, round {round_ + 1}")
        page_opens(c.window, "Store", f"the Store with no tools, round {round_ + 1}")
        for tid in reversed(ids):
            c.set_tool(tid, True)
            settle(0.3)
            check(all_tools.is_on(c.settings, tid), f"round {round_ + 1}: {tid} can be added back")
        settle(1.0)
        gc.collect()
        settle(0.5)
        tops = len([x for x in qapp.topLevelWidgets() if alive(x)])
        everything = len(qapp.allWidgets())
        sizes.append((tops, everything, _rss_mb()))
        note(f"round {round_ + 1}: {tops} windows, {everything} widgets, {sizes[-1][2]:.0f} MB")
    if len(sizes) >= 3:
        check(sizes[-1][1] <= sizes[1][1] * 1.1 + 50, f"widgets don't pile up as tools come and go "
                                                       f"({' → '.join(str(x[1]) for x in sizes)})")
        if sizes[-1][2]:
            per_round = (sizes[-1][2] - sizes[1][2]) / (len(sizes) - 2)
            check(per_round <= (3 if len(sizes) > 5 else 15), f"memory doesn't keep growing as tools come and go "
                  f"({' → '.join(f'{x[2]:.0f}' for x in sizes)} MB, {per_round:.1f} MB a round)")
    apply_size(c.window)


# ---------------------------------------------------------------------------- the tray menu

def tray():
    """Every entry in the tray menu (but Quit and updates) does something without an error."""
    skip = {tr("Quit"), tr("Check for updates"), tr("Pause")}
    for act in list(c.menu.actions()):
        if not act.isVisible() or act.isSeparator() or act.menu() is not None or act.text() in skip:
            continue
        before = len(errors)
        act.trigger()
        settle(0.6)
        _close_dialogs()
        settle(0.2)
        for v in list(getattr(c, "viewers", [])) + list(getattr(c, "browsers", [])):
            if alive(v):
                v.close()
        if c.clip is not None and hasattr(c.clip, "hide_popup"):
            try:
                c.clip.hide_popup()
            except Exception:
                pass
        for top in qapp.topLevelWidgets():  # the capture overlay, the clipboard popup
            if alive(top) and top.isVisible() and top is not c.window and top.isWindow() and \
                    type(top).__name__ not in ("MainWindow", "Toast"):
                top.hide()
        check(len(errors) == before, f"the tray's {act.text()!r} works without an error")
    c.menu.aboutToShow.emit()
    c.recent_menu.aboutToShow.emit()
    settle(0.2)


# ---------------------------------------------------------------------------- keyboard

def keyboard():
    """Tab through each page and press Esc (which steps back, or closes the window to the tray): no errors."""
    w = c.window
    for name in every_page_name(w)[:40]:
        before = len(errors)
        c.show_window(name)
        settle(0.15)
        w = c.window
        for _ in range(25):
            QTest.keyClick(qapp.focusWidget() or w, Qt.Key_Tab)
        qapp.processEvents()
        QTest.keyClick(qapp.focusWidget() or w, Qt.Key_Escape)
        settle(0.1)
        check(len(errors) == before, f"Tab and Esc on {name} work without an error")
    c.show_window("Home")
    apply_size(c.window)


# ---------------------------------------------------------------------------- quick menu and wheel

def quick():
    """The quick menu and the quick wheel open and close again and again, in every corner of the screen,
    and always stay on the screen."""
    q = getattr(c, "quick", None)
    if q is None:
        note("no quick access in this version")
        return
    from PySide6.QtGui import QCursor
    screen = QGuiApplication.primaryScreen().availableGeometry()
    spots = [screen.center(), screen.topLeft() + QPoint(2, 2), screen.topRight() + QPoint(-2, 2),
             screen.bottomLeft() + QPoint(2, -2), screen.bottomRight() + QPoint(-2, -2)]
    for round_ in range(3):
        for spot in spots:
            before = len(errors)
            QCursor.setPos(spot)
            q.toggle_menu()
            settle(0.5)
            m = getattr(q, "menu", None)
            if check(m is not None and m.isVisible(), f"the quick menu opens (round {round_ + 1}, at {spot.x()},{spot.y()})"):
                g = m.frameGeometry()
                scr = (QGuiApplication.screenAt(spot) or QGuiApplication.primaryScreen()).geometry()
                check(scr.adjusted(-2, -2, 2, 2).contains(g), f"the quick menu is on the screen ({g.x()},{g.y()} "
                                                              f"{g.width()}x{g.height()} on {scr.width()}x{scr.height()})")
                if round_ == 0 and spot == spots[0]:
                    grab(m, "quick-menu")
            q.toggle_menu()
            settle(0.5)
            check(m is None or not m.isVisible(), "the quick menu closes with its shortcut again")
            q.open_wheel_at(spot)
            settle(0.5)
            wh = getattr(q, "wheel", None)
            if check(wh is not None and wh.isVisible(), f"the quick wheel opens at {spot.x()},{spot.y()}"):
                g = wh.frameGeometry()
                scr = (QGuiApplication.screenAt(spot) or QGuiApplication.primaryScreen()).geometry()
                check(scr.adjusted(-2, -2, 2, 2).contains(g), f"the quick wheel is on the screen at {spot.x()},"
                                                              f"{spot.y()} ({g.x()},{g.y()} {g.width()}x{g.height()})")
                if round_ == 0 and spot == spots[0]:
                    grab(wh, "quick-wheel")
                QTest.keyClick(wh, Qt.Key_Escape)
                settle(0.4)
                if wh.isVisible():
                    check(False, "Esc closes the quick wheel")
                    wh.hide()
            check(len(errors) == before, f"the quick menu and wheel at {spot.x()},{spot.y()}: no errors")
    # the floating button comes and goes with its setting
    s_ = c.settings
    was = s_.quick_button
    for on in (True, False, True, was):
        s_.quick_button = on
        c.settings_changed()
        settle(0.3)
        b = getattr(c.quick, "button", None) if c.quick else None
        check(bool(b is not None and b.isVisible()) == on, f"the floating button {'shows' if on else 'goes away'}")


# ---------------------------------------------------------------------------- sizes

def sizes():
    """Each page at the smallest window, and big: nothing cut off or sticking out."""
    w = c.window
    for label_, how in (("smallest", lambda: (w.showNormal(), w.resize(w.minimumSize()))),
                        ("1366x768", lambda: (w.showNormal(), w.resize(1366, 768))),
                        ("maximized", lambda: w.showMaximized())):
        how()
        settle(0.5)
        for name in every_page_name(w):
            w.open_page(name)
            settle(0.15)
            looks_ok(w, f"{name} page, {label_} window ({w.width()}x{w.height()})")
        grab(w, f"store-{label_}")
    apply_size(w)


sections = [("start", start), ("pages", pages)]
if not ARGS.quick:
    sections += [("buttons", buttons), ("keyboard", keyboard), ("tray", tray), ("quick", quick)]
sections += [("sizes", sizes), ("store", store_cycles)]
if ARGS.only:
    sections = [x for x in sections if x[0] == "start" or x[0] in ARGS.only.split(",")]
for name, fn in sections:
    step(name, fn)
    if not alive(c.window) or not c.window.isVisible():
        c.show_window("Home")
        apply_size(c.window)

log_file = st.LOG_DIR / "corefold.log"
if log_file.exists():
    text = log_file.read_text("utf-8", "replace")
    (OUT / f"{TAG}-log.txt").write_text(text, "utf-8")
    bad = [line for line in text.splitlines() if " ERROR " in line or "Traceback" in line]
    check(not bad, f"the log has no errors ({len(bad)}: {bad[:3]})")
(OUT / f"{TAG}-problems.txt").write_text("\n".join(problems + ["", "errors:"] + errors + ["", "notes:"] + notes)
                                         or "none", "utf-8")
print(f"sweeptest {TAG}: {len(problems)} problem(s)", flush=True)
faulthandler.disable()
os._exit(0)
