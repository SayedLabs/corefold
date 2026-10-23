"""Clicks through the main window like a person would, on a real Windows PC: adds every tool from the
Store with its own button, opens every page from the sidebar, goes in and out of every part of a page,
and checks what's really on the screen (not only what Qt thinks it drew), so a blank or white window,
a page that can't be left, or an error behind a click shows up here.

Run: python scripts/navtest.py <folder> [en|ar] [dark|light]
Prints one line per check and "navtest: N problem(s)"."""
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "navtest").resolve()
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
LOOK = sys.argv[3] if len(sys.argv) > 3 else "dark"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-navtest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(900, exit=True)  # stuck somewhere: say where, and stop

from corefold import settings as st  # noqa: E402
from corefold import tools as all_tools  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.check_updates = False
s.language = LANG
s.appearance = LOOK
s.known_tools = sorted(t.id for t in all_tools.ready())
s.tools_off = sorted(t.id for t in all_tools.ready() if t.id not in all_tools.ORIGINAL)  # like a first start
s.save()

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication, QIcon  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402
from PySide6.QtWidgets import QLabel as QWidgetLabel  # noqa: E402

from corefold import i18n  # noqa: E402
i18n.set_language(LANG)
qapp = QApplication(sys.argv)
qapp.setQuitOnLastWindowClosed(False)
if i18n.is_rtl():
    qapp.setLayoutDirection(Qt.RightToLeft)
from corefold import app as A  # noqa: E402
from corefold.i18n import tr  # noqa: E402
from corefold import work  # noqa: E402

A.setup_logging()
problems = []
errors = []


def _hook(t, e, tb):
    """An error behind a click never reaches the person, so here it's a failed check."""
    text = "".join(traceback.format_exception(t, e, tb))
    errors.append(text)
    print("ERROR " + text, flush=True)


sys.excepthook = _hook
c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
dialogs = []


def _close_dialogs():
    from PySide6.QtWidgets import QMessageBox
    box = qapp.activeModalWidget()
    if box is not None:
        text = box.text() if isinstance(box, QMessageBox) else ""
        dialogs.append(f"{box.windowTitle()}: {text}")
        print(f"note  a dialog came up: {box.windowTitle()}: {text}", flush=True)
        box.reject()


_closer = QTimer()
_closer.timeout.connect(_close_dialogs)
_closer.start(1500)


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)
    return ok


def settle(seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)


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
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()
    settle(0.2)
    check(len(errors) == before, f"{name}: no errors behind the clicks ({len(errors) - before})")


def click(widget):
    """A real mouse click in the middle of it (through Windows' own mouse, so overlays count)."""
    widget.window().raise_()
    widget.window().activateWindow()
    settle(0.1)
    QTest.mouseClick(widget, Qt.LeftButton, Qt.NoModifier, widget.rect().center())
    settle(0.4)


def screen_shot(name: str, w=None):
    """What's really on the screen where the window is, next to what Qt drew for it.
    Returns how different the two are (0: the same, 1: nothing alike): a window that shows up white
    or empty on screen while Qt drew a page is far apart."""
    w = w or c.window
    settle(0.6)
    screen = w.screen() or QGuiApplication.primaryScreen()
    g = w.geometry()
    real = screen.grabWindow(0, g.x(), g.y(), g.width(), g.height()).toImage()
    drawn = w.grab().toImage()
    real.save(str(OUT / f"{LANG}-{LOOK}-{name}-screen.png"))
    drawn.save(str(OUT / f"{LANG}-{LOOK}-{name}.png"))
    if real.isNull() or drawn.isNull():
        return 1.0
    real = real.scaled(80, 50)
    drawn = drawn.scaled(80, 50)
    diff = 0
    for y in range(50):
        for x in range(80):
            a, b = real.pixelColor(x, y), drawn.pixelColor(x, y)
            diff += abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())
    return diff / (80 * 50 * 3 * 255)


def white_share(img) -> float:
    img = img.scaled(80, 50)
    white = sum(1 for y in range(50) for x in range(80) if img.pixelColor(x, y).lightness() > 245)
    return white / 4000


def flash_after(g, seconds=0.8) -> float:
    """The whitest the screen got where the window is, in the moments after a click."""
    worst = 0.0
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        screen = QGuiApplication.screenAt(g.center()) or QGuiApplication.primaryScreen()
        worst = max(worst, white_share(screen.grabWindow(0, g.x(), g.y(), g.width(), g.height()).toImage()))
        time.sleep(0.01)
    return worst


def page_widget(w, name):
    from corefold.window import PAGES
    return w.stack.widget(PAGES.index(name))


def page_ok(w, name, what):
    """The page is the one asked for, it was really built, and it shows on screen as drawn."""
    check(w.current_page() == name, f"{what}: the {name} page is open ({w.current_page()})")
    check(name not in w._unbuilt, f"{what}: the {name} page was built")
    pw = page_widget(w, name)
    check(pw.isVisible() and pw.width() > 200 and pw.height() > 200, f"{what}: the {name} page is showing "
                                                                         f"({pw.width()}x{pw.height()})")
    d = screen_shot(f"{name.lower()}-{what.split()[0].lower()}", w)
    check(d < 0.12, f"{what}: the screen shows the {name} page as drawn (difference {d:.2f})")


def buttons(root, text):
    return [b for b in root.findChildren(QPushButton) if b.text() == text and b.isVisible()]


def nav_click(w, name):
    row = w.nav_rows.index(name)
    item = w.nav.item(row)
    w.nav.scrollToItem(item)
    rect = w.nav.visualItemRect(item)
    w.raise_()
    w.activateWindow()
    QTest.mouseClick(w.nav.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    settle(0.5)


# ---------------------------------------------------------------------------- the Store

def store_add():
    """Every tool added with its own Add button, the way a person does it."""
    c.show_window("Store")
    c.window.resize(1100, 720)
    settle(0.6)
    screen_shot("store-before")
    for tool in all_tools.ready():
        if all_tools.is_on(c.settings, tool.id):
            continue
        w = c.window
        if w.current_page() != "Store":
            nav_click(w, "Store")
        store = page_widget(w, "Store")
        add = None
        for b in buttons(store, tr("Add")):
            labels = [lab.text() for lab in b.parentWidget().findChildren(QWidgetLabel)]
            if tr(tool.name) in labels:
                add = b
                break
        if not check(add is not None, f"the Store has an Add button for {tool.id}"):
            continue
        old = c.window
        old.raise_()
        old.activateWindow()
        settle(0.1)
        QTest.mouseClick(add, Qt.LeftButton, Qt.NoModifier, add.rect().center())
        geometry = old.geometry()
        flash = flash_after(geometry)
        if c.theme.dark:
            check(flash < 0.3, f"adding {tool.id} doesn't flash a white window (whitest moment {flash:.2f})")
        settle(1.0)
        w = c.window
        check(all_tools.is_on(c.settings, tool.id), f"{tool.id} is added")
        check(w.isVisible(), f"after adding {tool.id} the window is still open")
        check(not work.alive(old) or not old.isVisible() or old is w, f"after adding {tool.id} only one window is open")
        check(tool.page in w.nav_rows, f"{tool.id} is in the sidebar")
        page_ok(w, "Store", f"adding {tool.id}")
        real = (w.screen() or QGuiApplication.primaryScreen()).grabWindow(
            0, w.geometry().x(), w.geometry().y(), w.width(), w.height()).toImage()
        if c.theme.dark:
            check(white_share(real) < 0.3, f"after adding {tool.id} the window isn't white ({white_share(real):.2f})")
        # its Open button in the Store
        for b in buttons(page_widget(w, "Store"), tr("Open")):
            labels = [lab.text() for lab in b.parentWidget().findChildren(QWidgetLabel)]
            if tr(tool.name) in labels:
                click(b)
                page_ok(w, tool.page, f"open {tool.id} from the Store")
                break
        else:
            check(False, f"the Store has an Open button for {tool.id}")



# ---------------------------------------------------------------------------- every page

def every_page():
    """Every row in the sidebar opens its page, and every page can be left again by the sidebar."""
    w = c.window
    w.show()
    names = [n for n in w.nav_rows if n]
    for name in names:
        nav_click(w, name)
        page_ok(w, name, f"sidebar {name}")
        # its Settings button, then back
        tool = all_tools.BY_PAGE.get(name)
        if tool is not None and tool.settings:
            gear = buttons(page_widget(w, name), tr("Settings"))
            if check(bool(gear), f"{name} has a Settings button"):
                click(gear[0])
                check(w.current_page() == tool.settings, f"{name}'s Settings button opens {tool.settings} "
                                                         f"({w.current_page()})")
                nav_click(w, name)
                check(w.current_page() == name, f"back to {name} from its settings ({w.current_page()})")
    # settings tabs
    nav_click(w, "General")
    for first, row in w.settings_tabs.items():  # (a list on the side of the Settings pages)
        rect = w.settings_list.visualRect(w.settings_list.model().index(row, 0))
        QTest.mouseClick(w.settings_list.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
        settle(0.2)
        check(w.current_page() == first, f"the {first} settings tab ({w.current_page()})")
    for page_name, b in w.settings_subtabs.items():
        w.open_page("ConvOptions")
        settle(0.2)
        click(b)
        check(w.current_page() == page_name, f"the {page_name} settings sub-tab ({w.current_page()})")
    # Back (Alt + Left) goes to the page before
    nav_click(w, "Home")
    nav_click(w, "History")
    nav_click(w, "About")
    for want in ("History", "Home"):
        QTest.keyClick(w, Qt.Key_Left, Qt.AltModifier)
        settle(0.3)
        check(w.current_page() == want, f"Alt + Left goes back to {want} ({w.current_page()})")
    nav_click(w, "Home")
    # the Home page's tool cards
    for page_name, card in list(w.tool_cards.items()):
        nav_click(w, "Home")
        click(card)
        check(w.current_page() == page_name, f"the {page_name} card on Home opens it ({w.current_page()})")


# ---------------------------------------------------------------------------- Space

def space_nav():
    w = c.window
    nav_click(w, "Space")
    pg = w.space_page
    if not check(pg is not None, "the Space page"):
        return
    cards = [b for b in pg.findChildren(QPushButton) if b.objectName() == "driveCard" and b.isVisible()]
    check(len(cards) >= 1, f"Space lists the drives ({len(cards)})")
    screen_shot("space-drives")
    if not cards:
        return
    first = cards[0]
    t0 = time.time()
    click(first)
    check(pg.job is not None or pg.scan is not None, "clicking the first drive starts looking through it")
    screen_shot("space-busy")
    seen = []
    done = until(lambda: (seen.append(pg.busy_line.text()) or True) and pg.job is None, 90)
    took = time.time() - t0
    print(f"note  the first drive: {'done' if done else 'still looking'} after {took:.0f} s; last progress: "
          f"{seen[-1] if seen else ''}", flush=True)
    if not done:
        click(next(b for b in pg.findChildren(QPushButton) if b.text() == tr("Stop")))
        check(until(lambda: pg.job is None, 30), "Stop stops looking")
    check(pg.scan is not None and pg.scan.files > 1000, f"the first drive is mapped "
                                                          f"({pg.scan.files if pg.scan else 0} files in {took:.0f} s)")
    screen_shot("space-map")
    if pg.scan is not None:
        print(f"note  {pg.scan.files:,} files, {pg.scan.folders:,} folders, {pg.scan.denied} private, "
              f"{pg.scan.seconds:.1f} s, cancelled {pg.scan.cancelled}", flush=True)
    # Back inside the map: up a folder, then the drives (on a smaller folder, so it's quick)
    if pg.scan is not None:
        pg._back_to_drives()
    pg.start_scan(Path(sys.base_prefix))
    check(until(lambda: pg.job is None, 120) and pg.scan is not None and pg.scan.files > 2000,
          f"a folder with thousands of files is mapped ({pg.scan.files if pg.scan else 0})")
    if pg.scan is not None and pg.scan.root.children:
        pg.open_folder(pg.scan.root.children[0])
        settle(0.2)
        QTest.keyClick(w, Qt.Key_Left, Qt.AltModifier)
        settle(0.3)
        check(pg.here is pg.scan.root, "Alt + Left in Space goes up a folder")
        QTest.keyClick(w, Qt.Key_Left, Qt.AltModifier)
        settle(0.3)
        check(pg.scan is None and pg.drives_box.isVisible(), "…and then back to the drives")
        cards = [b for b in pg.findChildren(QPushButton) if b.objectName() == "driveCard" and b.isVisible()]
        if cards:
            click(cards[0])
            settle(0.5)
            QTest.keyClick(w, Qt.Key_Left, Qt.AltModifier)
            check(until(lambda: pg.job is None, 30) and pg.drives_box.isVisible(),
                  "Alt + Left while it looks stops and goes back to the drives")
            pg.start_scan(Path(sys.base_prefix))
            until(lambda: pg.job is None, 120)
    # going back to the drives and picking another one
    back = [b for b in pg.findChildren(QPushButton) if b.isVisible() and b.text() in (tr("Other drives"), tr("Back"))]
    if check(bool(back), "the map has a way back to the drives"):
        click(back[0])
        cards = [b for b in pg.findChildren(QPushButton) if b.objectName() == "driveCard" and b.isVisible()]
        check(len(cards) >= 1, "back at the drives")
    # leaving the page and coming back while it looks
    cards = [b for b in pg.findChildren(QPushButton) if b.objectName() == "driveCard" and b.isVisible()]
    if cards:
        click(cards[-1])
        nav_click(w, "Home")
        nav_click(w, "Space")
        check(w.current_page() == "Space", "Space can be left and opened again while it looks")
        stop = [b for b in pg.findChildren(QPushButton) if b.isVisible() and b.text() == tr("Stop")]
        if stop:
            click(stop[0])
        until(lambda: pg.job is None, 30)
    for key in ("clean", "dupes", "map"):
        pg.show_section(key)
        settle(0.3)
        check(pg.section == key, f"the Space {key} tab")
    until(lambda: pg.job is None, 60)


for name, fn in (("store", store_add), ("pages", every_page), ("space", space_nav)):
    step(name, fn)
log_file = st.LOG_DIR / "corefold.log"
if log_file.exists():
    (OUT / f"{LANG}-navtest-log.txt").write_text(log_file.read_text("utf-8", "replace"), "utf-8")
(OUT / f"{LANG}-navtest.txt").write_text("\n".join(problems + errors) or "none", "utf-8")
print(f"navtest: {len(problems)} problem(s)", flush=True)
faulthandler.disable()
os._exit(0)
