"""Checks what typing in the quick menu finds (sums, units, Windows Settings pages, the web search), its
Windows commands (asked about first, never really run here) and "Keep a window on top", on a real
Windows PC: real key presses, a real Notepad window, the real clipboard, and pictures of the screen.

Run from the repo root: python scripts/newtest_quick.py
It checks the logic, then runs itself in English (dark) and in Arabic (light), and prints one line per
check and "newtest_quick: N problem(s)". Pictures go to out/newtest_quick/."""
import ctypes
import faulthandler
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "newtest_quick"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
PART = sys.argv[1:3] if len(sys.argv) >= 3 else None
faulthandler.dump_traceback_later(125 if PART else 290, exit=True)  # stuck somewhere: say where, and stop
problems = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)
    return ok


# ---------------------------------------------------------------------------- the logic (no window needed)

def logic():
    from corefold import i18n, quickfind as qf
    i18n.set_language("en")
    sums = {"12*(3+4)": "84", "2^10": "1024", "15% of 80": "12", "sqrt(2)": "1.41421356237", "0.1+0.2": "0.3",
            "١٢*٧": "84", "٣٫٥ * 2": "7", "3,5*2": "7", "1,000 + 1": "1001", "80 + 15%": "92", "10/4": "2.5",
            "۲^۳": "8", "(1+2)*3": "9", "√16": "4", "3x4": "12"}
    for text, want in sums.items():
        a = qf.calculate(text)
        check(a is not None and a.value == want, f"sum {text!r} = {want} ({a.value if a else None})")
    for text in ("1/0", "2**99999", "9**9999", "hello", "7", "-5", "e", "__import__('os')", "(1+", "1+*2",
                 "().__class__", "x*2", "a" * 200):
        check(qf.calculate(text) is None, f"{text[:30]!r} isn't a sum (or can't be worked out)")
    # sums that would take ages (or all the memory) if they were worked out: refused straight away
    for text in ("round(5,-999999999)", "round(5; -999999999)", "round(5; 999999999)", "round(2.5; 1.5)",
                 "round(1,2,3)", "9**9**9", "2**10000*2**10000", "floor(1e308)*floor(1e308)", "10**400",
                 "(10**400)**2", "exp(1e9)", "ceil(1e308)**2", "sqrt(4, 9)", "max(9**9999, 1)"):
        start = time.perf_counter()
        a = qf.calculate(text)
        took = time.perf_counter() - start
        check(a is None and took < 0.05, f"{text!r} is refused quickly ({a.value if a else None}, "
                                          f"{took * 1000:.1f} ms)")
    for text, want in {"round(3.14159, 2)": "3.14", "round(1234, -2)": "1200", "(10**300)%7": "1"}.items():
        start = time.perf_counter()
        a = qf.calculate(text)
        check(a is not None and a.value == want and time.perf_counter() - start < 0.05, f"{text!r} = {want}")
    units = {"10 km to mi": "6.21371", "5 ft in cm": "152.4", "100 f to c": "37.7778", "-40 c = f": "-40",
             "0 c to k": "273.15", "3.5 GB in MB": "3584", "60 mph to km/h": "96.5606", "2 l to cups": "8.45351",
             "1 day in hours": "24", "1 acre in m2": "4046.86", "10 kg → lb": "22.0462", "١٠ km to mi": "6.21371",
             "5 in in cm": "12.7", "90 min to h": "1.5", "8 bits to bytes": "1"}
    for text, want in units.items():
        a = qf.convert(text)
        check(a is not None and a.value == want, f"{text!r} is {want} ({a.value if a else None})")
    for text in ("10 km to kg", "10 km", "km to mi", "10 parsecs to mi"):
        check(qf.convert(text) is None, f"{text!r} isn't a conversion")
    check(qf.answer("10 km to mi").kind == "unit" and qf.answer("12*7").kind == "calc", "the answer says which it is")
    first = qf.settings_pages("display")
    check(bool(first) and first[0][1].uri == "ms-settings:display", "'display' finds the Display settings page")
    check(qf.settings_pages("wifi")[0][1].uri == "ms-settings:network-wifi", "'wifi' finds Wi-Fi")
    check(qf.settings_pages("wallpaper")[0][1].uri == "ms-settings:personalization-background",
          "'wallpaper' finds Background")
    check(not qf.settings_pages("zzz"), "nothing matches 'zzz'")
    i18n.set_language("ar")
    check(qf.settings_pages("العرض")[0][1].uri == "ms-settings:display", "in Arabic 'العرض' finds Display")
    check(qf.settings_pages("display")[0][1].uri == "ms-settings:display", "…and English still works in Arabic")
    check(qf.convert("10 km إلى mi") is not None, "'إلى' works as 'to' in Arabic")
    i18n.set_language("fr")
    check(qf.convert("10 km en mi") is not None, "the translated 'to' works (French 'en')")
    i18n.set_language("en")
    check(qf.search_url("duckduckgo", "a b&c") == "https://duckduckgo.com/?q=a+b%26c", "the web search address")
    check(qf.search_url("bing", "x").startswith("https://www.bing.com/"), "…with the chosen engine")


# ---------------------------------------------------------------------------- the real thing, in one language

def part(lang, look):
    tmp = Path(tempfile.mkdtemp(prefix="corefold-newtest-quick-"))
    os.environ["APPDATA"] = str(tmp / "roaming")
    os.environ["LOCALAPPDATA"] = str(tmp / "local")
    from corefold import settings as st
    s = st.Settings()
    s.first_run = False
    s.tour_done = True
    s.check_updates = False
    s.language = lang
    s.appearance = look
    s.known_tools = ["converter", "uninstaller", "archive", "viewer", "player", "clipboard", "space", "capture"]
    s.quick_told = True
    s.save()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtWidgets import QApplication, QComboBox
    qapp = QApplication(sys.argv[:1])
    from corefold import i18n
    i18n.set_language(lang)
    if i18n.is_rtl():
        qapp.setLayoutDirection(Qt.RightToLeft)
    from corefold import app as A
    from corefold import quick, quicksys
    c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
    s = c.settings
    u = ctypes.windll.user32
    ran = []  # Windows commands that would have run (never for real in a test)
    quicksys.run = lambda action_id: ran.append(action_id) or True
    quicksys._start = lambda cmd: ran.append(cmd[0])
    VK_CTRL, VK_ALT, VK_SPACE, VK_ESC, VK_ENTER, VK_LWIN = 0x11, 0x12, 0x20, 0x1B, 0x0D, 0x5B

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

    def press(*vks):
        for vk in vks:
            u.keybd_event(vk, 0, 0, 0)
            settle(0.03)
        for vk in reversed(vks):
            u.keybd_event(vk, 0, 2, 0)
            settle(0.03)

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("ki", KEYBDINPUT), ("pad", ctypes.c_ubyte * 8)]

    def type_text(text):
        """Real typing of any character (Arabic digits too), the way an input method sends it."""
        for ch in text:
            for flags in (0x4, 0x4 | 0x2):  # KEYEVENTF_UNICODE, then KEYEVENTF_KEYUP
                inp = INPUT(type=1, ki=KEYBDINPUT(0, ord(ch), flags, 0, 0))
                u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            settle(0.03)
        settle(0.15)

    def clipboard_text():
        """What's on Windows' clipboard (read like any other app would)."""
        k = ctypes.windll.kernel32
        u.GetClipboardData.restype = ctypes.c_void_p
        k.GlobalLock.argtypes, k.GlobalLock.restype = [ctypes.c_void_p], ctypes.c_void_p
        k.GlobalUnlock.argtypes = [ctypes.c_void_p]
        for _ in range(20):
            if u.OpenClipboard(None):
                break
            settle(0.05)
        else:
            return None
        try:
            h = u.GetClipboardData(13)  # CF_UNICODETEXT
            if not h:
                return ""
            p = k.GlobalLock(h)
            try:
                return ctypes.wstring_at(p)
            finally:
                k.GlobalUnlock(h)
        finally:
            u.CloseClipboard()

    def shot(name):
        settle(0.3)
        QGuiApplication.primaryScreen().grabWindow(0).save(str(OUT / f"{name}-{lang}-{look}.png"))

    def menu():
        return c.quick.menu if c.quick is not None else None

    def menu_visible():
        m = menu()
        return m is not None and m.isVisible()

    def open_menu():
        if menu_visible():
            menu().hide()
            settle(0.2)
        press(VK_CTRL, VK_ALT, VK_SPACE)
        ok = until(menu_visible, 3) and until(menu().isActiveWindow, 2)
        settle(0.3)
        return ok

    def step(name, fn):
        try:
            fn()
        except Exception:
            check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")
            traceback.print_exc()
        if menu_visible():
            menu().hide()
        settle(0.2)

    def sums():
        check(until(lambda: c.quick is not None, 3), "quick access is running")
        u.SetCursorPos(500, 400)
        check(open_menu(), "the shortcut opens the menu, with the keyboard")
        m = menu()
        type_text("12*7")
        check(m.query == "12*7", f"typing a sum ({m.query!r})")
        check(bool(m.spots) and m.spots[0].kind == "answer" and m.spots[0].entry.label == "84",
              f"…the first row is its answer, 84 ({m.spots[0].entry.label if m.spots else None})")
        check(m.selected == 0, "…and it's the one picked")
        check(m.spots[-1].entry.id == "web", "…with the web search as the last row")
        u.OpenClipboard(None)
        u.EmptyClipboard()
        u.CloseClipboard()
        press(VK_ENTER)
        check(until(lambda: clipboard_text() == "84", 2), f"Enter copies 84 ({clipboard_text()!r})")
        check(m.copied and m.isVisible(), "…says so on the row")
        check(until(lambda: not m.isVisible(), 2), "…and then puts the menu away")
        # a bigger sum, for the picture
        check(open_menu(), "the menu opens again")
        type_text("12*(3+4)")
        check(m.spots and m.spots[0].entry.en == "84", "12*(3+4) is 84")
        shot("calc")
        press(VK_ESC)
        check(m.query == "" and m.isVisible(), "Esc clears what was typed")
        type_text("١٢*٧")
        check(m.spots and m.spots[0].kind == "answer" and m.spots[0].entry.en == "84",
              f"Arabic digits work (١٢*٧ = {m.spots[0].entry.en if m.spots else None})")
        press(VK_ESC)
        type_text("1/0")
        check(m.spots and m.spots[0].kind != "answer", "1/0 has no answer row (and nothing breaks)")

    def units():
        check(open_menu(), "the menu opens for a unit")
        m = menu()
        type_text("10 km to mi")
        check(m.query == "10 km to mi", f"typing a unit with spaces ({m.query!r})")
        check(m.spots and m.spots[0].kind == "answer", "…the first row converts it")
        shot("unit")
        press(VK_ENTER)
        check(until(lambda: (clipboard_text() or "").startswith("6.21"), 2),
              f"Enter copies the number ({clipboard_text()!r})")
        until(lambda: not m.isVisible(), 2)

    def windows_settings():
        check(open_menu(), "the menu opens for Windows' settings")
        m = menu()
        type_text("display")
        first = m.spots[0] if m.spots else None
        check(first is not None and first.entry.id == "ms-settings:display" and first.kind == "wide",
              f"'display': the first row is the Display settings page ({first.entry.id if first else None})")
        shot("settings")
        press(VK_ESC)
        type_text("so")
        check(any(sp.entry.id == "ms-settings:sound" for sp in m.spots), "'so' finds Sound")
        shot("settings-sound")
        if lang == "ar":
            press(VK_ESC)
            type_text("العرض")
            check(m.spots and m.spots[0].entry.id == "ms-settings:display", "in Arabic, 'العرض' finds Display")
        press(VK_ESC)
        type_text("corefold website")
        check(m.spots and m.spots[-1].entry.id == "web", "anything else: the last row searches the web")
        press(VK_ESC)
        press(VK_ESC)
        check(until(lambda: not m.isVisible(), 2), "…nothing was opened, and Esc closes the menu")

    def commands():
        check(quick.entry(s, "hibernate") is None or quicksys.hibernate_allowed(),
              f"Hibernate is only offered when it's switched on ({quicksys.hibernate_allowed()})")
        for i in ("sleep", "restart", "shutdown", "signout", "recycle", "ontop"):
            check(quick.entry(s, i) is not None, f"{i} can go on the menu and the wheel")
        items, size = quicksys.recycle_bin()
        check(items >= 0 and size >= 0, f"the Recycle Bin can be looked at ({items} items, {size} bytes)")
        check(open_menu(), "the menu opens for Restart")
        m = menu()
        type_text("restart")
        idx = next((i for i, sp in enumerate(m.spots) if sp.entry.id == "restart"), -1)
        check(idx >= 0, "'restart' finds Restart")
        m.selected = max(idx, 0)
        press(VK_ENTER)
        asked = m.confirm == "restart" and m.spots and m.spots[0].entry.id == "confirm:restart"
        if asked:
            press(VK_ENTER)  # a double press (straight after it asked): never taken as the answer
            m.run(m.spots[0])
        settle(0.2)
        check(asked, "Enter on it asks first: a confirm row")
        check(m.isVisible() and m.confirm == "restart" and not ran,
              f"a second press straight after it asked doesn't count ({ran})")
        shot("confirm")
        press(VK_ESC)
        check(m.confirm == "" and m.isVisible() and m.query == "restart", "Esc goes back (it didn't restart)")
        settle(0.5)
        check(not ran, "…and nothing ran")
        # the whole way, with the command itself switched off for this test
        idx = next((i for i, sp in enumerate(m.spots) if sp.entry.id == "restart"), -1)
        if not check(idx >= 0, "Restart is there again"):
            return
        m.selected = idx
        press(VK_ENTER)
        settle(0.9)  # (a deliberate second press, not a double one)
        press(VK_ENTER)
        check(until(lambda: bool(ran), 2) and ran == ["restart"], f"a second Enter on the confirm row does it ({ran})")
        ran.clear()
        # from the wheel, it's asked about in the menu too
        c.quick.run("shutdown")
        check(until(lambda: menu_visible() and menu().confirm == "shutdown", 2), "picked on the wheel, it's asked "
              "about in the menu too")
        press(VK_ESC)
        press(VK_ESC)
        settle(0.5)
        check(not ran, "…and Esc cancels it")

    def on_top():
        before = set(_windows_of_class("Notepad"))
        proc = subprocess.Popen(["notepad.exe"])
        hwnd = 0
        end = time.time() + 15
        while time.time() < end and not hwnd:
            settle(0.2)
            new = [h for h in _windows_of_class("Notepad") if h not in before]
            hwnd = new[0] if new else 0
        if not check(bool(hwnd), "Notepad opened"):
            return
        try:
            u.SetWindowPos(ctypes.c_void_p(hwnd), None, 300, 200, 700, 450, 0x4 | 0x10)  # NOZORDER|NOACTIVATE
            from corefold.quickmenu import _bring_to_front
            _bring_to_front(hwnd)
            check(until(lambda: quicksys.foreground() == hwnd, 3), "…and it's in front")
            check(not quicksys.is_on_top(hwnd), "…not on top to begin with")
            keys = (VK_CTRL, VK_LWIN, ord("T"))
            if not c.quick.ontop_key.ok:  # (Windows or another app, like PowerToys, has Ctrl + Win + T here)
                from corefold import shortcutspage
                entry = next(e for e in shortcutspage.GLOBAL if e.setting == "ontop_key")
                check(shortcutspage.state(c, entry) == "taken", "Ctrl + Win + T is taken on this PC: Settings → "
                      "Shortcuts says another app is using it (and nothing else nags about it)")
                s.ontop_key = "ctrl+alt+shift+t"
                c.settings_changed()
                keys = (VK_CTRL, VK_ALT, 0x10, ord("T"))
                _bring_to_front(hwnd)
                until(lambda: quicksys.foreground() == hwnd, 2)
            check(c.quick.ontop_key.ok, f"the keep-on-top shortcut works ({s.ontop_key})")
            press(*keys)
            check(until(lambda: quicksys.is_on_top(hwnd), 3),
                  f"{hotkey_display(s.ontop_key)} keeps Notepad on top (WS_EX_TOPMOST)")
            flash = getattr(c.quick, "_flash", None)
            check(flash is not None and until(flash.isVisible, 1), "…with a coloured border round it for a moment")
            shot("ontop")
            settle(1.5)
            check(_gone(flash), "…which is gone a second later")
            _bring_to_front(hwnd)
            until(lambda: quicksys.foreground() == hwnd, 2)
            press(*keys)
            check(until(lambda: not quicksys.is_on_top(hwnd), 3), "pressing it again puts it back to normal")
            # from the menu: the window that was in front before it opened
            _bring_to_front(hwnd)
            until(lambda: quicksys.foreground() == hwnd, 2)
            check(open_menu(), "the menu opens over Notepad")
            check(c.quick.front == hwnd, "…and knows Notepad was in front")
            m = menu()
            type_text("on top")
            idx = next((i for i, sp in enumerate(m.spots) if sp.entry.id == "ontop"), -1)
            check(idx >= 0, "'on top' finds Keep a window on top")
            m.selected = max(idx, 0)
            press(VK_ENTER)
            check(until(lambda: quicksys.is_on_top(hwnd), 3), "…and picking it keeps Notepad on top")
            c.quick.toggle_on_top(hwnd)
            check(not quicksys.is_on_top(hwnd), "…and back again")
            check(quicksys.usable(int(m.winId())) == 0, "Corefold's own windows are never the one kept on top")
            desktop = u.GetShellWindow()
            check(quicksys.usable(desktop) == 0, "…nor the desktop")
        finally:
            u.PostMessageW(ctypes.c_void_p(hwnd), 0x0010, 0, 0)  # WM_CLOSE
            settle(0.8)
            if proc.poll() is None:
                proc.kill()
            for h in _windows_of_class("Notepad"):
                if h not in before:
                    u.PostMessageW(ctypes.c_void_p(h), 0x0010, 0, 0)

    def settings_page():
        from corefold.quickpage import KeyRecorder
        c.show_window("Quick")
        w = c.window
        w.resize(1100, 860)
        settle(0.6)
        page = w.stack.currentWidget()
        check(len(page.findChildren(KeyRecorder)) == 3, "the Quick access tab has the keep-on-top shortcut")
        combos = [cb for cb in page.findChildren(QComboBox) if cb.findData("duckduckgo") >= 0]
        if check(len(combos) == 1, "…and a choice of search engine"):
            combos[0].setCurrentIndex(combos[0].findData("google"))
            check(s.quick_search_engine == "google", "picking Google is kept")
            combos[0].setCurrentIndex(combos[0].findData("duckduckgo"))
        shot("settings-page")
        w.hide()
        settle(0.2)

    for name, fn in (("sums", sums), ("units", units), ("settings", windows_settings), ("commands", commands),
                     ("on top", on_top), ("page", settings_page)):
        step(name, fn)
    check(not ran, f"no Windows command was left to run ({ran})")


def hotkey_display(combo):
    from corefold.hotkey import display
    return display(combo)


def _windows_of_class(name):
    u = ctypes.windll.user32
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def each(h, _):
        buf = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(ctypes.c_void_p(h), buf, 64)
        if buf.value == name and u.IsWindowVisible(ctypes.c_void_p(h)):
            found.append(int(h))
        return True
    u.EnumWindows(proc(each), None)
    return found


def _gone(widget):
    try:
        return widget is None or not widget.isVisible()
    except RuntimeError:  # (deleted: gone)
        return True


if PART:
    part(*PART)
    print(f"({' '.join(PART)}) {len(problems)} problem(s)", flush=True)
    faulthandler.disable()
    from testexit import finish  # (Qt can crash in Windows' tidy-up at exit, after every check passed)
    finish(1 if problems else 0)

logic()
for lang, look in (("en", "dark"), ("ar", "light")):
    print(f"---- {lang} {look}", flush=True)
    try:
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), lang, look], cwd=str(ROOT),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=135)
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        out += "\nFAIL  timed out"
    print(out, flush=True)
    fails = [line[6:] for line in out.splitlines() if line.startswith("FAIL  ")]
    if not any(line.startswith(f"({lang} {look}) ") for line in out.splitlines()):
        fails.append(f"the {lang} {look} run didn't finish")
    problems += [f"({lang}) {f}" for f in fails]
(OUT / "problems.txt").write_text("\n".join(problems) or "none", "utf-8")
print(f"newtest_quick: {len(problems)} problem(s)", flush=True)
faulthandler.disable()
sys.exit(1 if problems else 0)
