"""Checks the quick menu and the quick wheel on a real Windows PC: their shortcuts are pressed with real
key presses, the mouse is really moved, and pictures are saved of the screen.

Used by .github/workflows/screenshots.yml. Run: python scripts/quicktest.py <folder> [en|ar] [dark|light]
Prints one line per check and "quicktest: N problem(s)"."""
import ctypes
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "quicktest").resolve()
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
LOOK = sys.argv[3] if len(sys.argv) > 3 else "dark"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-quicktest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(300, exit=True)  # stuck somewhere: say where, and stop

from corefold import settings as st  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.check_updates = False
s.language = LANG
s.appearance = LOOK
s.known_tools = ["converter", "uninstaller", "archive", "viewer", "player", "clipboard", "space", "capture"]
s.quick_told = True
s.save()

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

qapp = QApplication(sys.argv)
from corefold import i18n  # noqa: E402
i18n.set_language(LANG)
if i18n.is_rtl():
    qapp.setLayoutDirection(Qt.RightToLeft)
from corefold import app as A  # noqa: E402
from corefold import hotkey  # noqa: E402

c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
s = c.settings  # (the app's own, from here on)
u = ctypes.windll.user32
problems = []
VK_CTRL, VK_ALT, VK_SPACE, VK_ESC, VK_BACKTICK = 0x11, 0x12, 0x20, 0x1B, 0xC0


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)


def settle(seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)


def until(cond, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def down(vk):
    u.keybd_event(vk, 0, 0, 0)
    settle(0.03)


def up(vk):
    u.keybd_event(vk, 0, 2, 0)
    settle(0.03)


def press(*vks):
    for vk in vks:
        down(vk)
    for vk in reversed(vks):
        up(vk)


def type_text(text):
    for ch in text.upper():
        press(ord(ch))


def move(x, y):
    u.SetCursorPos(int(x), int(y))
    settle(0.08)


def click(x, y):
    move(x, y)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    settle(0.05)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    settle(0.15)


def shot(name):
    """The real screen (what a person sees, with the app behind the menu), not only the widget."""
    settle(0.25)
    screen = QGuiApplication.primaryScreen()
    screen.grabWindow(0).save(str(OUT / f"{name}-{LANG}-{LOOK}.png"))


def step(name, fn):
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()
    for w in (c.quick and c.quick.menu, c.quick and c.quick.wheel):
        if w is not None and w.isVisible():
            w.hide()
    settle(0.2)


def menu_visible():
    return c.quick is not None and c.quick.menu is not None and c.quick.menu.isVisible()


def wheel_visible():
    return c.quick is not None and c.quick.wheel is not None and c.quick.wheel.isVisible()


def starts():
    check(until(lambda: c.quick is not None, 3), "quick access is running")
    check(c.quick.menu is None and c.quick.wheel is None, "…but nothing of the menu or wheel is built yet")
    ok_menu, ok_wheel = c.quick.keys_ok()
    check(ok_menu, f"the menu's shortcut ({hotkey.display(s.quick_menu_key)}) works")
    check(ok_wheel, f"the wheel's shortcut ({hotkey.display(s.quick_wheel_key)}) works")
    check(c.act_quick.isVisible(), "the tray menu has Quick menu")


def menu():
    move(400, 300)
    press(VK_CTRL, VK_ALT, VK_SPACE)
    check(until(menu_visible, 3), "Ctrl + Alt + Space opens the quick menu")
    m = c.quick.menu
    check(until(m.isActiveWindow, 2), "…and it has the keyboard")
    check(any(sp.entry.id == "tool:converter" for sp in m.spots), "…with the tools on it")
    check(any(sp.entry.id == "screenshot" for sp in m.spots), "…and the quick actions")
    shot("quickmenu")
    type_text("task")
    settle(0.2)
    check(m.query == "task", f"typing searches it ({m.query!r})")
    check(bool(m.spots) and m.spots[0].entry.id == "taskmgr", "…and finds Task Manager (in English in every language)")
    shot("quickmenu-search")
    press(VK_ESC)
    check(m.query == "" and m.isVisible(), "Esc clears the search first")
    press(0x27)  # right arrow
    press(0x28)  # down arrow
    check(m.selected > 0, f"the arrow keys move between tiles ({m.selected})")
    for _ in range(12):
        press(0x28)
    sel = m.spots[m.selected]
    check(m.view.contains(m.shown(sel.rect).center()), f"the selected tile is always in view (scrolled {m.scroll:.0f})")
    press(VK_ESC)
    check(until(lambda: not m.isVisible(), 2), "…then Esc closes it")
    press(VK_CTRL, VK_ALT, VK_SPACE)
    check(until(menu_visible, 3), "the shortcut opens it again")
    settle(0.3)
    press(VK_CTRL, VK_ALT, VK_SPACE)
    check(until(lambda: not m.isVisible(), 2), "…and the shortcut again closes it")
    press(VK_CTRL, VK_ALT, VK_SPACE)
    until(menu_visible, 3)
    settle(0.3)
    g = m.geometry()
    click(g.left() + 8, g.top() + 8)  # outside the panel
    check(until(lambda: not m.isVisible(), 2), "a click outside the panel closes it")
    # a tile does what it says: "Open Corefold"
    s.quick_menu_items = ["home"] + [i for i in s.quick_menu_items if i != "home"]
    press(VK_CTRL, VK_ALT, VK_SPACE)
    until(menu_visible, 3)
    settle(0.3)
    home = next(sp for sp in m.spots if sp.entry.id == "home")
    m._keep_in_view(m.spots.index(home))
    centre = m.mapToGlobal(m.shown(home.rect).center().toPoint())
    click(centre.x(), centre.y())
    check(until(lambda: c.window_if_open() is not None and c.window.isVisible(), 3),
          "clicking Open Corefold on it opens the window")
    check(not m.isVisible(), "…and puts the menu away")
    c.window.hide()
    settle(0.2)


def wheel():
    s.quick_wheel_items = ["home", "settings", "clipboard", "explorer"]
    centre = QPoint(700, 450)
    move(centre.x(), centre.y())
    # hold the shortcut, point up (the first slice) and let go
    down(VK_ALT)
    down(VK_BACKTICK)
    check(until(wheel_visible, 3), f"holding {hotkey.display(s.quick_wheel_key)} opens the quick wheel")
    w = c.quick.wheel
    settle(0.35)
    wc = w.geometry().center()
    move(wc.x(), wc.y() - 120)
    settle(0.1)
    check(w.hover == 0, f"pointing up picks the first slice ({w.hover})")
    shot("quickwheel")
    up(VK_BACKTICK)
    up(VK_ALT)
    check(until(lambda: not w.isVisible(), 2), "letting go closes it")
    check(until(lambda: c.window_if_open() is not None and c.window.isVisible(), 3), "…and does it (opens Corefold)")
    c.window.hide()
    settle(0.2)
    # a quick tap: it stays open for a click or a number
    move(centre.x(), centre.y())
    press(VK_ALT, VK_BACKTICK)
    check(until(wheel_visible, 3), "a quick tap opens it")
    settle(0.5)
    check(w.isVisible() and not w.holding, "…and it stays open")
    press(ord("2"))
    check(until(lambda: not w.isVisible(), 2), "pressing 2 picks the second slice")
    check(until(lambda: c.window_if_open() is not None and c.window.isVisible()
                and c.window.current_page() == "General", 3), "…Settings")
    c.window.hide()
    settle(0.2)
    move(centre.x(), centre.y())
    press(VK_ALT, VK_BACKTICK)
    until(wheel_visible, 3)
    settle(0.4)
    press(VK_ESC)
    check(until(lambda: not w.isVisible(), 2), "Esc closes it")
    # a click on a slice
    press(VK_ALT, VK_BACKTICK)
    until(wheel_visible, 3)
    settle(0.4)
    wc = w.geometry().center()
    click(wc.x() + 4, wc.y() - 130)
    check(until(lambda: not w.isVisible(), 2), "clicking a slice picks it")
    until(lambda: c.window_if_open() is not None and c.window.isVisible(), 3)
    if c.window_if_open() is not None:
        c.window.hide()


def floating_button():
    s.quick_button = True
    c.settings_changed()
    b = c.quick.button
    check(b is not None and b.isVisible(), "the round button shows when switched on")
    if b is None:
        return
    shot("quickbutton")
    g = b.geometry()
    click(g.center().x(), g.center().y())
    check(until(wheel_visible, 2), "clicking it opens the wheel")
    press(VK_ESC)
    until(lambda: not wheel_visible(), 2)
    # dragging it moves it and remembers where
    before = b.pos()
    move(g.center().x(), g.center().y())
    u.mouse_event(0x0002, 0, 0, 0, 0)
    for i in range(1, 11):
        move(g.center().x() - i * 12, g.center().y() + i * 6)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    settle(0.2)
    check(b.pos() != before and not wheel_visible(), "dragging it moves it (and doesn't open the wheel)")
    check(s.quick_button_pos == [b.x(), b.y()], "…and its place is remembered")
    s.quick_button = False
    c.settings_changed()
    check(c.quick.button is None, "switching it off removes it")


def settings_page():
    from corefold.quickpage import KeyRecorder
    c.show_window("Quick")
    w = c.window
    w.resize(1100, 820)
    settle(0.5)
    shot("quick-settings")
    page = w.stack.currentWidget()
    recs = page.findChildren(KeyRecorder)
    check(len(recs) == 3, "the page has the three shortcut buttons (menu, wheel, keep on top)")
    menu_rec = recs[0]
    menu_rec.recorded.emit("alt+f4")
    check(s.quick_menu_key == "ctrl+alt+space", "a shortcut Windows uses (Alt + F4) is refused")
    menu_rec.recorded.emit(s.clip_shortcut)
    check(s.quick_menu_key == "ctrl+alt+space", "the Clipboard's shortcut is refused")
    menu_rec.recorded.emit("ctrl+alt+m")
    check(s.quick_menu_key == "ctrl+alt+m" and c.quick.menu_key.key == "ctrl+alt+m" and c.quick.menu_key.ok,
          "a new shortcut is used straight away")
    w.hide()
    settle(0.2)
    press(VK_CTRL, VK_ALT, ord("M"))
    check(until(menu_visible, 3), "…and opens the menu")
    press(VK_ESC)
    until(lambda: not menu_visible(), 2)
    press(VK_CTRL, VK_ALT, VK_SPACE)
    settle(0.6)
    check(not menu_visible(), "the old shortcut doesn't any more")
    # a shortcut another app has: say so
    other = u.RegisterHotKey(None, 77, 0x2 | 0x1 | 0x4000, ord("K"))
    menu_rec.recorded.emit("ctrl+alt+k")
    check(other and not c.quick.menu_key.ok, "a shortcut another app has is noticed")
    if other:
        u.UnregisterHotKey(None, 77)
    menu_rec.recorded.emit("ctrl+alt+space")
    check(c.quick.menu_key.ok, "…and picking another one fixes it")


def switched_off():
    s.quick_menu = False
    s.quick_wheel = False
    c.settings_changed()
    check(c.quick is not None and c.quick.menu_key.key == "off" and c.quick.wheel_key.key == "off"
          and c.quick.ontop_key.key == s.ontop_key, "with both off, only the keep-on-top shortcut is left")
    s.ontop_key = "off"
    c.settings_changed()
    check(c.quick is None, "switching that off too stops quick access completely")
    check(not c.act_quick.isVisible(), "…and takes it off the tray menu")
    free = u.RegisterHotKey(None, 78, 0x2 | 0x1 | 0x4000, VK_SPACE)
    check(bool(free), "…and lets go of its shortcuts")
    if free:
        u.UnregisterHotKey(None, 78)
    press(VK_CTRL, VK_ALT, VK_SPACE)
    settle(0.5)
    check(c.quick is None, "pressing the shortcut then does nothing")
    s.quick_menu = True
    c.settings_changed()
    check(c.quick is not None and c.quick.menu_key.ok and c.quick.wheel is None, "switching the menu back on")
    s.ontop_key = st.Settings().ontop_key
    c.settings_changed()


for name, fn in (("starts", starts), ("menu", menu), ("wheel", wheel), ("button", floating_button),
                 ("settings", settings_page), ("off", switched_off)):
    step(name, fn)
(OUT / f"quicktest-{LANG}.txt").write_text("\n".join(problems) or "none", "utf-8")
print(f"quicktest: {len(problems)} problem(s)", flush=True)
faulthandler.disable()
from testexit import finish  # (Qt can crash in Windows' tidy-up at exit, after every check passed)
finish(0)
