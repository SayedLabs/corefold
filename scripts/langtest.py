"""Languages on a real Windows PC: the "Choose your language" page of the first start, changing the
language from the button at the top of the window (no restart), the Language list in Settings, and how
the dropdown lists look when open.

Run: python scripts/langtest.py <folder> [--look dark|light]
     python scripts/langtest.py <folder> --start first|again   (runs Corefold itself, see first_start())
Prints one line per check and "langtest: N problem(s)"; pictures are saved in the folder."""
import argparse
import gc
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("out", nargs="?", default="langtest")
ap.add_argument("--look", default="dark")
ap.add_argument("--start", default="", help="first: a real first start of Corefold; again: the start after it")
ap.add_argument("--data", default="", help="the settings folder (kept between --start first and --start again)")
ARGS = ap.parse_args()
OUT = Path(ARGS.out).resolve()
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(ARGS.data).resolve() if ARGS.data else Path(tempfile.mkdtemp(prefix="corefold-lang-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.enable()  # a crash says where it happened
faulthandler.dump_traceback_later(600, exit=True)  # stuck somewhere: say where, and stop

from corefold import i18n  # noqa: E402
from corefold import settings as st  # noqa: E402
from corefold import tools as all_tools  # noqa: E402

problems: list[str] = []
errors: list[str] = []
TAG = ARGS.look


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)
    return ok


def note(what):
    print("note  " + what, flush=True)


def _hook(t, e, tb):
    text = "".join(traceback.format_exception(t, e, tb))
    errors.append(text)
    print("ERROR " + text, flush=True)


sys.excepthook = _hook


def words(lang: str, text: str) -> str:
    """text in that language (what tr() gives while it's in use)."""
    old = i18n.language()
    i18n.set_language(lang)
    try:
        return i18n.tr(text)
    finally:
        i18n.set_language(old)


def finish():
    check(not errors, f"no errors ({len(errors)})")
    print(f"langtest{'-' + ARGS.start if ARGS.start else ''}: {len(problems)} problem(s)", flush=True)
    (OUT / f"problems-{ARGS.start or TAG}.txt").write_text("\n".join(problems), "utf-8")
    sys.stdout.flush()
    os._exit(1 if problems else 0)


# ---------------------------------------------------------------------------- a real start of Corefold

def first_start():
    """Starts Corefold the way Windows does (main()), on fresh settings (--start first) or on the
    settings that start left (--start again), and answers what comes up like a person would."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from corefold import app as A
    from corefold import welcome

    if ARGS.start == "first":
        s = st.Settings()
        s.check_updates = False
        s.save()
    seen = {"welcome": 0, "picker_title": "", "rtl": False, "tour": False}
    t0 = time.time()

    def drive():
        app = QApplication.instance()
        # (what's showing, by kind: the test PC's windows aren't always the active ones)
        top = app.activeModalWidget() or next((w for w in app.topLevelWidgets() if w.isVisible() and type(w).__name__
                                               in ("ToolPicker", "Tour")), None)
        pickers = [w for w in app.topLevelWidgets() if isinstance(w, welcome.LanguagePicker) and w.isVisible()]
        if pickers:
            dlg = pickers[0]
            seen["welcome"] += 1
            if seen["welcome"] == 1:
                want = i18n.windows_language()
                check(dlg.cards[want if want in dlg.cards else "en"].isChecked(),
                      f"the language Windows is in ({want}) is picked already")
                dlg.grab().save(str(OUT / "start-1-welcome.png"))
                dlg.cards["ar"].click()
                app.processEvents()
                check(dlg.title.text() == words("ar", "Choose your language"), "the page shows in Arabic once picked")
                dlg.grab().save(str(OUT / "start-2-welcome-ar.png"))
                dlg.go.click()
            QTimer.singleShot(400, drive)
            return
        if top is not None and type(top).__name__ == "ToolPicker":
            seen["picker_title"] = top.windowTitle()
            seen["rtl"] = top.layoutDirection().name == "RightToLeft"
            top.grab().save(str(OUT / "start-3-tools.png"))
            top.accept()
        elif top is not None and type(top).__name__ == "Tour":
            seen["tour"] = True
            top.reject()
            QTimer.singleShot(1500, done)
            return
        elif top is not None:
            note(f"closed {type(top).__name__} {top.windowTitle()!r}")
            top.reject()
        elif ARGS.start == "again" and time.time() - t0 > 6:
            done()
            return
        if time.time() - t0 > 60:
            check(False, "the first start finished within a minute")
            done()
            return
        QTimer.singleShot(300, drive)

    def done():
        app = QApplication.instance()
        s = st.Settings.load()
        if ARGS.start == "first":
            check(seen["welcome"] >= 1, "the first start shows Choose your language")
            check(s.language == "ar", f"the language picked is saved ({s.language})")
            check(seen["picker_title"] == words("ar", "Welcome"),
                  f"what comes next is in that language ({seen['picker_title']!r})")
            check(seen["rtl"], "... and right to left")
            check(seen["tour"], "the tour follows")
            check(not s.first_run, "the first start is over")
        else:
            check(seen["welcome"] == 0, "the next start doesn't ask for the language again")
            check(s.language == "ar", f"... and stays in the language picked ({s.language})")
        wins = [w for w in app.topLevelWidgets() if type(w).__name__ == "MainWindow" and w.isVisible()]
        if check(bool(wins), "the window opens"):
            w = wins[0]
            check(w.lang_btn.text().strip() == i18n.LANGUAGES["ar"], f"the language button says {w.lang_btn.text()!r}")
            w.grab().save(str(OUT / f"start-{ARGS.start}-window.png"))
        finish()

    ask = welcome.should_ask

    def should_ask(*a):  # (Qt is running by now: the person's clicks can be lined up)
        QTimer.singleShot(800, drive)
        return ask(*a)
    welcome.should_ask = should_ask
    sys.argv = sys.argv[:1]
    A.main()
    finish()


if ARGS.start:
    first_start()


# ---------------------------------------------------------------------------- the rest runs in this process

s = st.Settings()
s.first_run = False
s.tour_done = True
s.check_updates = False
s.language = "en"
s.appearance = ARGS.look
s.known_tools = sorted(t.id for t in all_tools.ready())
s.tools_off = []
s.save()

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QIcon  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QMenu  # noqa: E402

i18n.set_language("en")
qapp = QApplication(sys.argv)
qapp.setQuitOnLastWindowClosed(False)
from corefold import app as A  # noqa: E402
from corefold import welcome  # noqa: E402
from corefold.atomic.widgets import Dropdown  # noqa: E402

ICON = QIcon(str(ROOT / "assets" / "icon.png"))


def settle(seconds=0.25):
    end = time.time() + seconds
    while time.time() < end:
        QTest.qWait(10)


def fits(label, what):
    """The words aren't cut off."""
    if not label.isVisible() or not label.text():
        return
    fm = label.fontMetrics()
    if label.wordWrap():
        need = fm.boundingRect(label.contentsRect(), Qt.TextWordWrap, label.text()).height()
        check(need <= label.height() + 2, f"{what}: the words fit ({need} of {label.height()} px high)")
    else:
        need = fm.horizontalAdvance(label.text())
        check(need <= label.width() + 2, f"{what}: the words fit ({need} of {label.width()} px)")


def screen_shot(rect, name):
    """What's on the screen there (not what the widget thinks it drew: corners, shadows)."""
    screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
    g = screen.geometry()
    pm = screen.grabWindow(0, rect.x() - g.x() - 12, rect.y() - g.y() - 12, rect.width() + 24, rect.height() + 24)
    pm.save(str(OUT / f"{TAG}-{name}.png"))
    return pm


# ---- 1. the "Choose your language" page

def welcome_page():
    from PySide6.QtCore import QTimer
    fresh = st.Settings()
    fresh.appearance = ARGS.look
    check(welcome.should_ask(fresh, []), "a fresh install asks for the language")
    check(not welcome.should_ask(fresh, ["--background"]), "... but not when Windows starts Corefold in the background")
    used = st.Settings()
    used.first_run = False
    check(not welcome.should_ask(used, []), "... and never again after the first start")
    order = ["en", "ar", "fr", "es", "tr", "ar"]
    state = {"i": 0}

    def step():
        dlg = next((w for w in qapp.topLevelWidgets() if isinstance(w, welcome.LanguagePicker) and w.isVisible()), None)
        if dlg is None:
            QTimer.singleShot(100, step)
            return
        if state["i"] == 0:
            want = i18n.windows_language()
            check(dlg.cards.get(want, dlg.cards["en"]).isChecked(), f"the page starts on Windows' language ({want})")
            check(dlg.cards.get(want, dlg.cards["en"]).chip != "", "... with a Windows language mark on it")
        if state["i"] < len(order):
            code = order[state["i"]]
            card = dlg.cards[code]
            QTest.mouseClick(card, Qt.LeftButton, Qt.NoModifier, card.rect().center())
            settle(0.3)
            check(card.isChecked(), f"welcome: clicking {code} picks it")
            check(dlg.title.text() == words(code, "Choose your language"), f"welcome: the page shows in {code}")
            check(dlg.go.text() == words(code, "Continue"), f"welcome: the button says {dlg.go.text()!r}")
            check(qapp.layoutDirection() == (Qt.RightToLeft if code == "ar" else Qt.LeftToRight),
                  f"welcome: {code} reads {'right to left' if code == 'ar' else 'left to right'}")
            for lab, what in ((dlg.title, "title"), (dlg.intro, "line under it"), (dlg.hello, "welcome")):
                fits(lab, f"welcome {code} {what}")
            check(dlg.go.width() >= dlg.go.sizeHint().width() - 2, f"welcome {code}: Continue isn't squeezed")
            for c, cd in dlg.cards.items():
                check(cd.width() >= cd.minimumSizeHint().width(), f"welcome {code}: the {c} card fits")
            check(dlg.isVisible() and dlg.height() < 900, f"welcome {code}: fits on the screen ({dlg.height()} px)")
            screen_shot(dlg.frameGeometry(), f"welcome-{state['i']}-{code}")
            state["i"] += 1
            QTimer.singleShot(50, step)
            return
        # the keyboard: Down moves to the next card, Enter continues
        dlg.cards["ar"].setFocus(Qt.TabFocusReason)
        QTest.keyClick(dlg.cards["ar"], Qt.Key_Down)
        settle(0.2)
        check(dlg.cards["fr"].isChecked() and dlg.code == "fr", "welcome: the Down key picks the next language")
        QTest.keyClick(dlg.cards["fr"], Qt.Key_Up)
        settle(0.2)
        check(dlg.code == "ar", "welcome: the Up key goes back")
        QTest.keyClick(dlg, Qt.Key_Return)

    QTimer.singleShot(300, step)
    t0 = time.time()
    picked = welcome.ask_language(qapp, ICON, fresh)
    check(picked == "ar", f"welcome: Enter keeps the language picked ({picked})")
    check(st.Settings.load().language == "ar", "welcome: the language is saved")
    check(time.time() - t0 < 60, "welcome: done")
    # closing the page with its X keeps the one picked (the Windows one, if nothing was clicked)
    fresh2 = st.Settings()
    fresh2.appearance = ARGS.look

    def close_it():
        dlg = next((w for w in qapp.topLevelWidgets() if isinstance(w, welcome.LanguagePicker) and w.isVisible()), None)
        if dlg is None:
            QTimer.singleShot(100, close_it)
            return
        dlg.close()
    QTimer.singleShot(300, close_it)
    want = i18n.windows_language()
    check(welcome.ask_language(qapp, ICON, fresh2) == want, f"welcome: closing it keeps Windows' language ({want})")
    # back to English for the rest
    i18n.set_language("en")
    qapp.setLayoutDirection(Qt.LeftToRight)


welcome_page()

# ---- 2. the language button at the top of the window: no restart

s = st.Settings.load()
s.language = "en"
s.first_run = False
s.save()
c = A.Controller(qapp, ICON)
restarted = []
c.restart = lambda *a, **k: restarted.append(a)
c.show_window("Converter")
w = c.window
w.resize(1100, 720)
settle(1.0)


def alive_windows() -> int:
    gc.collect()
    settle(0.3)
    return sum(1 for x in qapp.topLevelWidgets() if type(x).__name__ == "MainWindow")


def menu_pick(code: str):
    w = c.window
    btn = w.lang_btn
    check(btn.isVisible(), "the language button shows")
    bar, g = btn.parentWidget(), btn.geometry()
    gap = g.left() if i18n.is_rtl() else bar.width() - g.right() - 1
    check(gap < 24, f"the language button is at the far end of the top strip ({gap} px from it)")
    QTest.mouseClick(btn, Qt.LeftButton, Qt.NoModifier, btn.rect().center())
    settle(0.4)
    menu = qapp.activePopupWidget()
    if not check(isinstance(menu, QMenu), "clicking it opens the list of languages"):
        return False
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    check(all(i18n.LANGUAGES[k] in texts for k in ("en", "ar", "fr", "es", "tr")),
          "every language is in the list, in its own words")
    screen_shot(menu.geometry(), f"langmenu-{i18n.language()}")
    target = i18n.tr(i18n.LANGUAGES["system"]) if code == "system" else i18n.LANGUAGES[code]
    act = next(a for a in menu.actions() if a.text() == target)
    QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(act).center())
    return True


before = alive_windows()
for code in ["ar", "fr", "es", "tr", "system", "en", "ar", "en"]:
    page = c.window.current_page()
    t0 = time.time()
    if not menu_pick(code):
        continue
    end = time.time() + 15
    want = i18n.resolve(code)
    while time.time() < end and (i18n.language() != want
                                 or c.window.lang_btn.text().strip() != i18n.LANGUAGES[want]):
        settle(0.05)
    settle(0.6)
    took = time.time() - t0
    w = c.window
    check(i18n.language() == want, f"{code}: Corefold is in {want} now ({i18n.language()})")
    check(took < 6, f"{code}: changed in {took:.1f} s, with no restart")
    check(not restarted, f"{code}: nothing restarted")
    check(w.isVisible() and w.current_page() == page,
          f"{code}: the window stays open on the same page ({w.current_page()})")
    check(c.settings.language == code and st.Settings.load().language == code, f"{code}: saved")
    check(qapp.layoutDirection() == (Qt.RightToLeft if want == "ar" else Qt.LeftToRight), f"{code}: reading direction")
    check(c.menu.actions()[0].text() == words(want, "Open Corefold"), f"{code}: the tray menu is in {want}")
    home = w.nav.item(w.nav_rows.index("Home")).text()
    check(home == words(want, "Home"), f"{code}: the window is in {want} ({home!r})")
    w.open_page("General")  # (a Settings page is made the first time it's opened)
    check(w.language.currentData() == code, f"{code}: the Language list in Settings shows it")
    w.open_page(page)
    check(c.toast.open_btn.text() == words(want, "Open"), f"{code}: popups are in {want}")
    w.grab().save(str(OUT / f"{TAG}-switch-{code}.png"))
check(alive_windows() <= before + 1, f"old windows are let go ({alive_windows()} window(s))")

# ---- 3. Settings > General > Language, then Save: also straight away

w = c.window
w.open_page("General")
settle(0.5)
i = w.language.findData("fr")
w.language.setCurrentIndex(i)
settle(0.2)
check(i18n.language() == "en", "the Language list waits for Save")
check(w.save_btn.isVisible(), "Save shows")
QTest.mouseClick(w.save_btn, Qt.LeftButton, Qt.NoModifier, w.save_btn.rect().center())
end = time.time() + 15
while time.time() < end and i18n.language() != "fr":
    settle(0.05)
settle(0.6)
check(i18n.language() == "fr", "Save puts Corefold in French straight away")
check(not restarted, "... with no restart")
check(c.window.current_page() == "General" and not c.window.is_dirty(), "... still on General, nothing left to save")
note("back to English")
c.switch_language("en")
settle(1.0)

# ---- 4. dropdowns, open, in this language and in Arabic


def dropdowns(lang):
    w = c.window
    w.open_page("General")
    settle(0.5)
    boxes = [b for b in w.stack.currentWidget().findChildren(QComboBox) if b.isVisible()]
    check(boxes and all(isinstance(b, Dropdown) for b in boxes), f"{lang}: every list on General is a Dropdown")
    all_boxes = w.findChildren(QComboBox)
    plain = [b.objectName() or type(b).__name__ for b in all_boxes if not isinstance(b, Dropdown)]
    check(not plain, f"{lang}: no plain lists left in the window ({plain[:5]})")
    for n in range(min(2, len(boxes))):
        b = [x for x in c.window.stack.currentWidget().findChildren(QComboBox) if x.isVisible()][n]
        b.showPopup()
        settle(0.5)
        box = b.view().window()
        check(box.isVisible(), f"{lang}: the list opens")
        check(box.testAttribute(Qt.WA_TranslucentBackground), f"{lang}: its corners are see-through")
        geo, under = box.geometry(), b.mapToGlobal(b.rect().bottomLeft())
        check(abs(geo.top() - under.y()) <= 12 or geo.bottom() <= b.mapToGlobal(b.rect().topLeft()).y(),
              f"{lang}: it opens right below the box ({geo.top()} vs {under.y()})")
        check(geo.width() >= b.width() - 2, f"{lang}: at least as wide as the box")
        scr = (b.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        check(scr.contains(geo), f"{lang}: on the screen")
        delegate = b.view().itemDelegate()
        check(type(delegate).__name__ == "_DropdownRow", f"{lang}: drawn by the design system")
        rows = [b.view().sizeHintForRow(r) for r in range(b.count())]
        check(min(rows) >= 28, f"{lang}: rows are easy to click ({min(rows)} px)")
        screen_shot(geo, f"dropdown-{lang}-{n}")
        # the mouse over another row, then a click picks it
        old = b.currentIndex()
        r = (old + 1) % b.count()
        rect = b.view().visualRect(b.model().index(r, 0))
        QTest.mouseMove(b.view().viewport(), rect.center())
        settle(0.2)
        screen_shot(geo, f"dropdown-{lang}-{n}-hover")
        QTest.mouseClick(b.view().viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
        settle(0.3)
        check(b.currentIndex() == r, f"{lang}: clicking a row picks it")
        b.setCurrentIndex(old)
        if c.window.is_dirty():
            c.window.discard_changes()
            settle(0.5)
            c.window.open_page("General")
            settle(0.3)


dropdowns("en")
c.switch_language("ar")
settle(1.0)
dropdowns("ar")
c.switch_language("en")
settle(0.5)
finish()
