"""Checks Corefold Colors on a real Windows PC: known colours are painted over the whole screen, the picker
is opened (by its shortcut and from the app), the mouse is really moved and clicked, and what lands on the
clipboard is compared with the colour painted there, in HEX, RGB and HSL. The arrow keys must move exactly
one screen pixel, at 100% and (in a second and third run of this script) at 125%, 150% and 200%.

Run from the repository: python scripts/newtest_colors.py [folder]
Prints one line per check and "newtest_colors: N problem(s)". Pictures go to out/newtest_colors/."""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else ROOT / "out" / "newtest_colors")
MODE = next((a for a in sys.argv[1:] if a.startswith("--")), "")   # "", "--scaled", "--removed" or "--arabic"
SCALE = os.environ.get("QT_SCALE_FACTOR", "1")
OUT = OUT.resolve()
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-colortest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
import threading  # noqa: E402

START = time.time()
# how long this run may take: the whole test ends in under 5 minutes, a run of its own in what it was given
LIMIT = float(os.environ.get("NEWTEST_LIMIT", "60")) if MODE else 280.0
faulthandler.dump_traceback_later(LIMIT + 10, exit=True)  # (the last resort, if even the watchdog is stuck)

from corefold import settings as st  # noqa: E402
from corefold import tools as all_tools  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.check_updates = False
s.quick_told = True
s.language = "ar" if MODE == "--arabic" else "en"
s.appearance = "light" if MODE == "--arabic" else "dark"
s.known_tools = sorted(t.id for t in all_tools.TOOLS)
s.tools_off = ["colors"] if MODE == "--removed" else []
s.colors_shortcut = "win+shift+c" if not MODE else "off"  # (the first run still holds it)
s.save()

from PySide6.QtCore import QPointF, QRect, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget  # noqa: E402

qapp = QApplication(sys.argv)
from corefold import i18n  # noqa: E402
i18n.set_language(s.language)
if i18n.is_rtl():
    qapp.setLayoutDirection(Qt.RightToLeft)
from corefold import app as A  # noqa: E402

c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
s = c.settings  # (the app's own, from here on)
u = ctypes.windll.user32
problems = []
TAG = f" at {round(float(SCALE) * 100)}%" if MODE == "--scaled" else ""


def _out_of_time():
    """Stuck somewhere: say where, and still end with the summary line."""
    print(f"FAIL  the test ran out of time after {LIMIT:.0f} s, stuck here:" + TAG, flush=True)
    faulthandler.dump_traceback(all_threads=True)
    sys.stderr.flush()
    print(f"newtest_colors: {len(problems) + 1} problem(s)" + TAG, flush=True)
    os._exit(1)


_watchdog = threading.Timer(LIMIT, _out_of_time)
_watchdog.daemon = True
_watchdog.start()


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what + TAG, flush=True)
    if not ok:
        problems.append(what)
    return ok


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


# ---- real mouse and keys (SendInput)

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort), ("wParamH", ctypes.c_ushort)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


def send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    sent = u.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    settle(0.05)
    return sent == len(inputs)


def mouse(flags):
    i = INPUT(type=0)
    i.u.mi = MOUSEINPUT(0, 0, 0, flags, 0, 0)
    return i


def key(vk, up=False):
    i = INPUT(type=1)
    i.u.ki = KEYBDINPUT(vk, 0, 0x0002 if up else 0, 0, 0)
    return i


def click(right=False):
    return send(mouse(0x0008 if right else 0x0002), mouse(0x0010 if right else 0x0004))


def press(*vks):
    return send(*[key(v) for v in vks], *[key(v, True) for v in reversed(vks)])


VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN, VK_RETURN, VK_ESC = 0x25, 0x26, 0x27, 0x28, 0x0D, 0x1B
VK_LWIN, VK_SHIFT, VK_C = 0x5B, 0x10, 0x43


def cursor() -> tuple[int, int]:
    import ctypes.wintypes as wt
    pt = wt.POINT()
    u.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def move(x, y):
    u.SetCursorPos(int(x), int(y))
    settle(0.25)


def clip_text() -> str:
    settle(0.1)
    return QGuiApplication.clipboard().text()


def set_clip(text):
    QGuiApplication.clipboard().setText(text)
    settle(0.1)


# ---- known colours, painted over the whole screen pixel for pixel

SQUARES = [  # colour, how it's copied as RGB and HSL
    ("#1A2B3C", "rgb(26, 43, 60)", "hsl(210, 40%, 17%)"),
    ("#E5484D", "rgb(229, 72, 77)", "hsl(358, 75%, 59%)"),
    ("#30A46C", "rgb(48, 164, 108)", "hsl(151, 55%, 42%)"),
    ("#7F3FBF", "rgb(127, 63, 191)", "hsl(270, 50%, 50%)"),
]


class Board(QWidget):
    """The screen's own pixels (a picture as big as the monitor, drawn 1:1): four squares, and a small
    patch where every pixel has a colour of its own (to see that the picker is exact to the pixel)."""

    def __init__(self, screen):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.screen_ = screen
        self.geo = screen.geometry()
        self.ratio = screen.devicePixelRatio()
        w, h = round(self.geo.width() * self.ratio), round(self.geo.height() * self.ratio)
        img = QImage(w, h, QImage.Format_RGB32)
        img.fill(QColor("#F2F2F2"))
        p = QPainter(img)
        side = h // 5
        self.squares = []
        for i, (hex_, _, _) in enumerate(SQUARES):
            r = QRect(w * (2 * i + 1) // 8 - side // 2, h // 4, side, side)
            p.fillRect(r, QColor(hex_))
            self.squares.append(r)
        p.end()
        self.fine = QRect(w // 2 - 20, h * 3 // 4 - 20, 40, 40)
        for x in range(self.fine.width()):
            for y in range(self.fine.height()):
                img.setPixelColor(self.fine.x() + x, self.fine.y() + y,
                                  QColor((x * 37 + 11) % 256, (y * 53 + 7) % 256, ((x + y) * 29) % 256))
        img.setDevicePixelRatio(self.ratio)
        self.img = img
        self.setGeometry(self.geo)

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawImage(QPointF(0, 0), self.img)

    def native(self, px: int, py: int) -> tuple[int, int]:
        """A pixel of the picture, in Windows' screen pixels."""
        return self.geo.x() + px, self.geo.y() + py

    def pixel(self, px: int, py: int) -> str:
        col = self.img.pixelColor(px, py)
        return f"#{col.red():02X}{col.green():02X}{col.blue():02X}"


def picker_open() -> bool:
    p = c.colors.picker if c.colors is not None else None
    return p is not None and bool(p.loupes) and all(lp.isVisible() for lp in p.loupes)


def picker_closed() -> bool:
    return c.colors is None or c.colors.picker is None


def loupe_hex() -> str:
    from corefold.colors import hex_of
    p = c.colors.picker if c.colors is not None else None
    lp = p.active() if p is not None else None
    col = lp.color() if lp is not None else None
    return hex_of(col) if col is not None else ""


def open_picker(how: str = "app") -> bool:
    if how == "key":
        press(VK_LWIN, VK_SHIFT, VK_C)
    else:
        c.colors.pick()
    return until(picker_open, 4)


def give_up_picking():
    """Leaves nothing behind after a check that failed half-way."""
    if c.colors is not None and c.colors.picker is not None:
        c.colors.picker.finish(None)
        settle(0.3)


def shot(name):
    QGuiApplication.primaryScreen().grabWindow(0).save(str(OUT / f"{name}.png"))


# ---- the checks

def starts():
    c.apply_tools()
    settle(0.5)
    check(c.colors is not None, "Colors starts when it's in use")
    check(c.colors.key.ok, "its shortcut (Win + Shift + C) is registered")
    taken = u.RegisterHotKey(None, 79, 0x8 | 0x4 | 0x4000, VK_C)
    if taken:
        u.UnregisterHotKey(None, 79)
    check(not taken, "…so another app can't take it while Colors has it")
    check(c.act_color.isVisible(), "the tray menu has \"Pick a colour\"")


def by_shortcut(board):
    set_clip("before the shortcut")
    a = board.squares[0].center()
    move(*board.native(a.x(), a.y()))
    if not check(open_picker("key"), "Win + Shift + C opens the picker on every screen"):
        return
    move(*board.native(a.x(), a.y()))
    check(loupe_hex() == SQUARES[0][0], f"the magnifier shows the colour under the mouse ({loupe_hex()})")
    shot("picker-loupe")
    lp = c.colors.picker.active()
    if lp is not None:
        lp.grab().save(str(OUT / "picker-loupe-window.png"))
    press(VK_ESC)
    check(until(picker_closed, 3), "Esc closes the picker")
    give_up_picking()
    check(clip_text() == "before the shortcut", "…and leaves the clipboard alone")
    check(s.colors_history == [], "…and nothing is added to the colours picked")


def formats(board, fmts=("hex", "rgb", "hsl")):
    for fmt in fmts:
        s.colors_format = fmt
        for (hex_, rgb, hsl), square in zip(SQUARES, board.squares):
            want = {"hex": hex_, "rgb": rgb, "hsl": hsl}[fmt]
            set_clip("nothing yet")
            if not check(open_picker(), f"the picker opens ({fmt}, {hex_})"):
                give_up_picking()
                continue
            ctr = square.center()
            move(*board.native(ctr.x(), ctr.y()))
            seen = loupe_hex()
            click()
            check(until(picker_closed, 3), f"a click picks and closes ({fmt}, {hex_})")
            give_up_picking()
            got = clip_text()
            check(got == want, f"the clipboard has {want!r} (got {got!r}; the magnifier showed {seen})")
            check(s.colors_history[:1] == [hex_], f"…and {hex_} is at the top of the colours picked")
            if fmt == "hex" and hex_ == SQUARES[1][0]:
                t = c.colors.toast
                check(t is not None and t.isVisible() and t.color.name().upper() == hex_.upper(),
                      "a small note with the colour says it was copied")
                shot("copied-note")
    s.colors_format = "hex"


def right_click_cancels(board):
    set_clip("still this")
    before = list(s.colors_history)
    if not check(open_picker(), "the picker opens (for a right-click)"):
        return
    ctr = board.squares[2].center()
    move(*board.native(ctr.x(), ctr.y()))
    click(right=True)
    check(until(picker_closed, 3), "a right-click closes the picker")
    give_up_picking()
    check(clip_text() == "still this" and s.colors_history == before, "…without copying or keeping anything")


def pixel_exact(board):
    """The arrow keys move one screen pixel, and the colour picked is that exact pixel."""
    s.colors_format = "hex"
    set_clip("")
    if not check(open_picker(), "the picker opens (for the arrow keys)"):
        return
    fx, fy = board.fine.x() + 10, board.fine.y() + 10
    move(*board.native(fx, fy))
    check(loupe_hex() == board.pixel(fx, fy), f"the magnifier is on the exact pixel under the mouse "
                                              f"({loupe_hex()} vs {board.pixel(fx, fy)})")
    shot(f"picker-pixels-{SCALE}")
    press(VK_RIGHT)
    settle(0.2)
    check(cursor() == board.native(fx + 1, fy), f"→ moves the mouse one pixel right ({cursor()})")
    check(loupe_hex() == board.pixel(fx + 1, fy), "…and the magnifier shows that pixel")
    press(VK_DOWN)
    press(VK_DOWN)
    press(VK_LEFT)
    press(VK_UP)
    settle(0.2)
    check(cursor() == board.native(fx, fy + 1), f"↓ ↓ ← ↑ ends one pixel below where it began ({cursor()})")
    want = board.pixel(fx, fy + 1)
    press(VK_RETURN)
    check(until(picker_closed, 3), "Enter picks")
    give_up_picking()
    check(clip_text() == want, f"…exactly that pixel's colour ({clip_text()!r}, wanted {want!r})")


def history():
    tool = c.colors
    tool.clear()
    for i in range(30):
        tool.remember(f"#10{i:02X}20")
        tool.remember(f"#10{i:02X}20")
    h = list(s.colors_history)
    check(len(h) == 24, f"the colours picked keep the last 24 ({len(h)})")
    check(h[0] == "#101D20" and h[-1] == "#100620", "…newest first")
    check(all(a != b for a, b in zip(h, h[1:])), "…and the same colour twice in a row is kept once")
    settle(1.0)
    saved = json.loads((st.CONFIG_DIR / "settings.json").read_text("utf-8")).get("colors_history")
    check(saved == h, "…and they're saved")
    w = c.window_if_open()
    check(w is None or not w.is_dirty(), "picking colours doesn't ask to save the settings")


SAMPLE = ["#3CC7B2", "#E5484D", "#30A46C", "#0090FF", "#F5A524", "#8E4EC6", "#1A2B3C", "#FFFFFF", "#000000",
          "#FFD1DC", "#12A594", "#8B8D98", "#C22F5C", "#6B4BD6"]


def page():
    from corefold.colors import hsl_text
    s.colors_history = list(SAMPLE)
    c.show_window("Colors")
    settle(1.0)
    w = c.window
    pg = w.colors_page
    if not check(pg is not None and w.current_page() == "Colors", "the Colors page opens"):
        return
    check(len(pg.swatches) == len(SAMPLE), f"it shows every colour picked ({len(pg.swatches)})")
    pg.show_color(SAMPLE[3])
    settle(0.3)
    w.grab().save(str(OUT / "page-en-dark.png"))
    pg._copy("hsl")
    check(clip_text() == hsl_text(SAMPLE[3]), f"its HSL Copy button copies {hsl_text(SAMPLE[3])!r}")
    check(pg.copy_buttons["hsl"].text() == "Copied", "…and says so")
    pg._remove()
    settle(0.2)
    check(SAMPLE[3] not in s.colors_history and len(pg.swatches) == len(SAMPLE) - 1, "Remove takes one colour off")
    # a colour typed or pasted
    pg.typed.setText("not a colour")
    pg.typed.returnPressed.emit()
    check(pg.typed_note.isVisible() and len(pg.swatches) == len(SAMPLE) - 1, "something that isn't a colour is refused")
    pg.typed.setText("rgb(30, 144, 255)")
    pg.typed.returnPressed.emit()
    settle(0.2)
    check(s.colors_history[0] == "#1E90FF" and pg.selected == "#1E90FF" and not pg.typed_note.isVisible()
          and not pg.typed.text(), f"rgb(30, 144, 255) typed: added and shown ({s.colors_history[:2]})")
    pg._copy("oklch")
    check(clip_text() == "oklch(65.2% 0.190 253.2)", f"OKLCH copied as CSS writes it ({clip_text()!r})")
    pg._copy("vec4")
    check(clip_text() == "vec4(0.118, 0.565, 1.000, 1.0)", f"VEC4 copied ({clip_text()!r})")
    said = [lab.text() for lab in pg.samples.values()]
    check(said == ["Aa  3.2:1  ·  Large text only", "Aa  6.5:1  ·  AA"], f"how readable text is on it: {said}")
    pg.typed.setText("hsl(0, 100%, 50%)")
    pg.typed.returnPressed.emit()
    pg.typed.setText("#fff")
    pg.typed.returnPressed.emit()
    pg.typed.setText("navy")
    pg.typed.returnPressed.emit()
    settle(0.2)
    check(s.colors_history[:3] == ["#000080", "#FFFFFF", "#FF0000"], f"hsl(), short HEX and names too "
                                                                      f"({s.colors_history[:3]})")
    w.grab().save(str(OUT / "page-typed-en-dark.png"))

    def answer():
        box = qapp.activeModalWidget()
        if isinstance(box, QMessageBox):
            for b in box.findChildren(QPushButton):
                if b.text().replace("&", "") == "Clear":
                    b.click()
                    return
        QTimer.singleShot(200, answer)
    QTimer.singleShot(400, answer)
    pg._clear()
    settle(0.3)
    check(s.colors_history == [] and pg.empty.isVisible(), "Clear all (after asking) forgets them all")
    # another app has the shortcut: the page says so
    taken = u.RegisterHotKey(None, 80, 0x8 | 0x1 | 0x4000, VK_C)  # Win + Alt + C
    pg.shortcut.setCurrentIndex(pg.shortcut.findData("win+alt+c"))
    settle(0.3)
    check(taken and not c.colors.key.ok and pg.key_note.isVisible(), "a shortcut another app has is noticed")
    if taken:
        u.UnregisterHotKey(None, 80)
    pg.shortcut.setCurrentIndex(pg.shortcut.findData("win+shift+c"))
    settle(0.3)
    check(c.colors.key.ok and not pg.key_note.isVisible(), "…and going back to Win + Shift + C fixes it")
    pg.show_color(SAMPLE[0])
    # "Pick a colour" on the page: the window steps aside, and comes back
    QTest.mouseClick(pg.pick_btn, Qt.LeftButton)
    opened = until(picker_open, 4)
    check(opened and not w.isVisible(), "\"Pick a colour\" hides the window, then opens the picker")
    press(VK_ESC)
    until(picker_closed, 3)
    give_up_picking()
    check(until(lambda: c.window.isVisible(), 3), "…and the window comes back after")
    c.window.hide()


def arabic_page():
    """A run in Arabic, light: the page, right to left."""
    c.apply_tools()
    s.colors_history = list(SAMPLE)
    c.show_window("Colors")
    settle(1.0)
    pg = c.window.colors_page
    if not check(pg is not None and c.window.current_page() == "Colors", "the Colors page opens in Arabic, light"):
        return
    pg.show_color(SAMPLE[1])
    settle(0.3)
    c.window.grab().save(str(OUT / "page-ar-light.png"))
    check(pg.isRightToLeft() and len(pg.swatches) == len(SAMPLE), "…right to left, with every colour")


def removed():
    c.set_tool("colors", False)
    settle(0.5)
    check(c.colors is None, "removing Colors in the Store stops it")
    check(not c.act_color.isVisible(), "…takes \"Pick a colour\" off the tray menu")
    free = u.RegisterHotKey(None, 81, 0x8 | 0x4 | 0x4000, VK_C)
    check(bool(free), "…and lets go of Win + Shift + C")
    if free:
        u.UnregisterHotKey(None, 81)
    c.set_tool("colors", True)
    settle(0.5)
    check(c.colors is not None and c.colors.key.ok, "adding it back takes the shortcut again")
    c.set_tool("colors", False)
    settle(0.3)


def nothing_loaded():
    """A run with Colors removed: none of it is loaded."""
    c.apply_tools()
    c.show_window("Home")
    settle(1.0)
    check("corefold.colors" not in sys.modules and "corefold.colorspage" not in sys.modules,
          "with Colors removed, none of it is loaded")
    check(c.colors is None and not c.act_color.isVisible(), "…no shortcut and nothing in the tray menu")
    free = u.RegisterHotKey(None, 82, 0x8 | 0x4 | 0x4000, VK_C)
    check(bool(free), "…and Win + Shift + C stays free")
    if free:
        u.UnregisterHotKey(None, 82)


def step(name, fn, *args):
    try:
        fn(*args)
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()
    give_up_picking()
    settle(0.2)


def sub_runs():
    """The same picking at 125%, 150% and 200%, a start with Colors removed, and the page in Arabic (each a
    run of its own). Each one writes to a file and gets a time limit; meanwhile this run keeps answering
    Windows (another app setting the clipboard waits for the one that had it, which is this one)."""
    runs = (("1.25", "--scaled"), ("1.5", "--scaled"), ("2", "--scaled"), ("1", "--removed"), ("1", "--arabic"))
    deadline = START + LIMIT - 15
    for scale, mode in runs:
        what = f"the run {mode} at {scale}"
        left = deadline - time.time()
        if left < 20:
            check(False, f"{what} was skipped: no time left")
            continue
        limit = min(55.0, left)
        env = dict(os.environ)
        env["QT_SCALE_FACTOR"] = scale
        env["NEWTEST_LIMIT"] = str(int(limit - 8))
        env.pop("APPDATA", None)
        env.pop("LOCALAPPDATA", None)
        log_path = OUT / f"run{mode[1:]}-{scale}.txt"
        t0 = time.time()
        with open(log_path, "w", encoding="utf-8") as log_file:
            proc = subprocess.Popen([sys.executable, __file__, str(OUT), mode], env=env, stdout=log_file,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            while proc.poll() is None and time.time() - t0 < limit:
                settle(0.1)
            if proc.poll() is None:
                check(False, f"{what} took too long (stopped after {limit:.0f} s)")
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=15)
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    pass
        out = log_path.read_text("utf-8", errors="replace")
        for line in out.splitlines():
            print(line, flush=True)
            if line.startswith("FAIL"):
                problems.append(line[6:])
        if not any(line.startswith("newtest_colors") for line in out.splitlines()):
            check(False, f"{what} didn't finish")
        print(f"note  {what} took {time.time() - t0:.0f} s", flush=True)


home = cursor()
old_clip = QGuiApplication.clipboard().text() if not MODE else None  # (the first run puts it back)
if MODE == "--removed":
    step("nothing loaded", nothing_loaded)
elif MODE == "--arabic":
    step("arabic page", arabic_page)
else:
    c.apply_tools()
    settle(0.3)
    board = Board(QGuiApplication.primaryScreen())
    board.show()
    board.raise_()
    settle(0.8)
    if MODE == "--scaled":
        check(c.colors is not None, "Colors starts")
        if c.colors is not None:
            step("formats", formats, board, ("hex",))
            step("pixel exact", pixel_exact, board)
    else:
        step("starts", starts)
        step("by shortcut", by_shortcut, board)
        step("formats", formats, board)
        step("right-click", right_click_cancels, board)
        step("pixel exact", pixel_exact, board)
    board.close()
    settle(0.3)
    if not MODE:
        step("history", history)
        step("page", page)
        step("removed", removed)
        print(f"note  the first run's own checks took {time.time() - START:.0f} s", flush=True)
        sub_runs()
move(*home)
if old_clip is not None:
    set_clip(old_clip)
_watchdog.cancel()
print(f"note  {time.time() - START:.0f} s in all" + TAG, flush=True)
print(f"newtest_colors: {len(problems)} problem(s)" + TAG, flush=True)
faulthandler.disable()
from testexit import finish  # (Qt can crash in Windows' tidy-up at exit, after every check passed)
finish(1 if problems else 0)
