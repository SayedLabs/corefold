"""Checks the Viewer's Quick look on a real Windows PC: a real File Explorer window on a folder of test
files, real key presses (SendInput), the selection read from Explorer itself, the shortcut only taken
while File Explorer or the desktop is in front (Notepad keeps Ctrl + Space), and pictures of each preview.

Run from the repo root: python scripts/newtest_peek.py
It checks the logic, then runs itself in English (dark) and in Arabic (light), and prints one line per
check and "newtest_peek: N problem(s)". Pictures go to out/newtest_peek/."""
import ctypes
import faulthandler
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "newtest_peek"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
PART = sys.argv[1:3] if len(sys.argv) >= 3 else None
faulthandler.dump_traceback_later(135 if PART else 295, exit=True)  # stuck somewhere: say where, and stop
problems = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)
    return ok


# ---------------------------------------------------------------------------- the logic (no window needed)

def logic():
    from corefold import peekview as pv
    check(pv.decode_text("مرحبا".encode("utf-16")) == "مرحبا", "UTF-16 with a byte-order mark")
    check(pv.decode_text("abc\r\n".encode("utf-16-le") * 5) == "abc\r\n" * 5, "UTF-16 without one")
    check(pv.decode_text("café".encode("utf-8")) == "café", "UTF-8")
    check(pv.decode_text("aé".encode("utf-8")[:-1], cut=True) == "a", "UTF-8 cut in the middle of a letter at the end")
    check(pv.decode_text(b"\xef\xbb\xbfhi") == "hi", "UTF-8 with a byte-order mark")
    check(pv.decode_text("caf\u00e9".encode("cp1252")) == "caf\u00e9", "an old Windows (ANSI) text file")
    check(pv.looks_like_text(b"plain words\n") and not pv.looks_like_text(bytes(range(256))),
          "text is told apart from other files")
    for name, kind in (("a.PNG", "picture"), ("a.heic", "picture"), ("a.mp4", "video"), ("a.flac", "audio"),
                       ("a.pdf", "pdf"), ("a.py", "text"), ("a.zip", "archive"), ("a.7z", "archive"),
                       ("a.xyz", "")):
        check(pv.kind_of(Path(tempfile.gettempdir()) / "nothing-here" / name) == kind, f"{name} is {kind or 'looked at'}")
    from corefold import hotkey, settings as st
    check(st.Settings().peek_key == "ctrl+space" and hotkey.parse("ctrl+space") == (0x2, 0x20),
          "Ctrl + Space to begin with")


# ---------------------------------------------------------------------------- test files

def make_files(folder: Path) -> dict:
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPageSize, QPainter, QPdfWriter
    files = {}
    img = QImage(3000, 2000, QImage.Format_RGB32)
    p = QPainter(img)
    g = QLinearGradient(0, 0, 3000, 2000)
    g.setColorAt(0, QColor("#0a7768"))
    g.setColorAt(1, QColor("#f0b429"))
    p.fillRect(img.rect(), g)
    p.setPen(QColor("white"))
    f = QFont("Segoe UI", 160)
    f.setBold(True)
    p.setFont(f)
    p.drawText(img.rect(), Qt.AlignCenter, "Quick look")
    p.end()
    files["picture"] = folder / "a photo.png"
    img.save(str(files["picture"]))
    files["text"] = folder / "b notes.txt"
    files["text"].write_text("Shopping list\n\n" + "\n".join(f"{i}. something to buy — شيء للشراء" for i in range(1, 80)),
                             "utf-8")
    files["pdf"] = folder / "c document.pdf"
    w = QPdfWriter(str(files["pdf"]))
    w.setPageSize(QPageSize(QPageSize.A4))
    w.setResolution(96)
    p = QPainter(w)
    for n in range(1, 4):
        if n > 1:
            w.newPage()
        p.setFont(QFont("Segoe UI", 28))
        p.drawText(QRect(60, 60, 600, 80), Qt.AlignLeft, f"Page {n}")
        p.setFont(QFont("Segoe UI", 12))
        for line in range(30):
            p.drawText(60, 180 + line * 26, "The quick brown fox jumps over the lazy dog. " * 2)
    p.end()
    files["folder"] = folder / "d folder"
    files["folder"].mkdir()
    for i in range(6):
        (files["folder"] / f"file {i}.bin").write_bytes(b"\0" * 4096 * (i + 1))
    (files["folder"] / "inner").mkdir()
    files["archive"] = folder / "e archive.zip"
    with zipfile.ZipFile(files["archive"], "w") as z:
        for i in range(12):
            z.writestr(f"photos/photo {i}.jpg", b"x" * 1000 * i)
    files["other"] = folder / "f data.dat"
    files["other"].write_bytes(bytes(range(256)) * 40)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        files["video"] = folder / "g clip.mp4"
        subprocess.run([ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25", "-t", "3",
                        "-pix_fmt", "yuv420p", str(files["video"])], timeout=60)
        if not files["video"].exists():
            del files["video"]
    return files


# ---------------------------------------------------------------------------- the real thing, in one language

def part(lang, look):
    # (the long spelling: the temp folder is often written C:\\Users\\RUNNER~1\\…, and File Explorer gives long names)
    tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="corefold-newtest-peek-")))
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
    from PySide6.QtWidgets import QApplication
    qapp = QApplication(sys.argv[:1])
    from corefold import i18n
    i18n.set_language(lang)
    if i18n.is_rtl():
        qapp.setLayoutDirection(Qt.RightToLeft)
    from corefold import app as A
    from corefold import peekshell, peekview
    from corefold.quickmenu import _bring_to_front
    c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
    s = c.settings
    u = ctypes.windll.user32
    u.FindWindowW.restype = ctypes.c_void_p
    VK_CTRL, VK_SPACE, VK_ESC, VK_RIGHT, VK_LEFT = 0x11, 0x20, 0x1B, 0x27, 0x25
    folder = tmp / "files"
    folder.mkdir()
    files = make_files(folder)
    opened: list[int] = []   # File Explorer windows this test opened (closed at the end)
    made: list[Path] = []    # files put on the desktop (removed at the end)

    def settle(seconds=0.3):
        end = time.time() + seconds
        while time.time() < end:
            qapp.processEvents()
            time.sleep(0.01)

    def until(cond, seconds=5):
        end = time.time() + seconds
        while time.time() < end:
            qapp.processEvents()
            try:
                if cond():
                    return True
            except RuntimeError:
                pass
            time.sleep(0.01)
        return False

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("ki", KEYBDINPUT), ("pad", ctypes.c_ubyte * 8)]

    def key(vk, up=False):
        inp = INPUT(type=1, ki=KEYBDINPUT(vk, 0, 0x2 if up else 0, 0, 0))
        u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def press(*vks):
        for vk in vks:
            key(vk)
            settle(0.03)
        for vk in reversed(vks):
            key(vk, up=True)
            settle(0.03)

    def ctrl_space() -> float:
        """Presses Ctrl + Space; the time it was pressed."""
        key(VK_CTRL)
        time.sleep(0.03)
        t0 = time.perf_counter()
        key(VK_SPACE)
        time.sleep(0.02)
        key(VK_SPACE, up=True)
        key(VK_CTRL, up=True)
        return t0

    def win():
        return c.peek.window if c.peek is not None else None

    def shown():
        w = win()
        return w is not None and w.isVisible()

    def shot(name):
        settle(0.4)
        w = win()
        if w is None or not w.isVisible():
            return
        g = w.frameGeometry()
        QGuiApplication.primaryScreen().grabWindow(0, g.x(), g.y(), g.width(), g.height()).save(
            str(OUT / f"{name}-{lang}-{look}.png"))

    def front(hwnd):
        for _ in range(3):
            _bring_to_front(hwnd)
            if until(lambda: peekshell.foreground() == hwnd, 2):
                return True
        return False

    def key_free() -> bool:
        """Nobody holds Ctrl + Space right now (this test could take it)."""
        if u.RegisterHotKey(None, 0x7711, 0x2 | 0x4000, VK_SPACE):
            u.UnregisterHotKey(None, 0x7711)
            return True
        return False

    def step(name, fn):
        try:
            fn()
        except Exception:
            check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")
            traceback.print_exc()
        if shown():
            win().close_peek(back=False)
        settle(0.2)

    def explorer():
        before = set(_windows_of_class("CabinetWClass"))
        # (written out by hand: a list would put the quotes round "/select,…" too, and Explorer wouldn't select)
        subprocess.Popen(f'explorer.exe /select,"{files["picture"]}"')
        hwnd = 0
        end = time.time() + 20
        while time.time() < end and not hwnd:
            settle(0.2)
            new = [h for h in _windows_of_class("CabinetWClass") if h not in before]
            hwnd = new[0] if new else 0
        if hwnd:
            opened.append(hwnd)
            u.SetWindowPos(ctypes.c_void_p(hwnd), None, 120, 80, 1100, 700, 0x4 | 0x10)
        return hwnd

    state = {}

    def explain():
        """What the last look at File Explorer found (for the log)."""
        for line in peekshell.notes:
            print(f"        shell: {line}", flush=True)

    def hooked():
        if c.peek is not None:
            for line in c.peek.watcher.history[-6:]:
                print(f"        in front: {line}", flush=True)

    def start():
        check(until(lambda: c.peek is not None, 3), "Quick look is running with the Viewer in use")
        state["before"] = peekshell.foreground()  # (the terminal)
        hwnd = explorer()
        if not check(bool(hwnd), "a File Explorer window opened on the test folder"):
            return
        state["hwnd"] = hwnd
        print(f"      (the File Explorer window: {hwnd:#x} {peekshell.window_class(hwnd)!r}; this test runs "
              f"{'as' if ctypes.windll.shell32.IsUserAnAdmin() else 'not as'} administrator)", flush=True)
        check(until(lambda: os.path.normcase(peekshell.shown_folder(hwnd)) == os.path.normcase(str(folder)), 10),
              f"…showing the test folder ({peekshell.shown_folder(hwnd)!r}, wanted {str(folder)!r})")
        picked = lambda: peekshell.selection(hwnd) == [str(files["picture"])]  # noqa: E731
        if not until(picked, 5):
            # (Explorer on GitHub's runner opens the folder but doesn't always select the file "/select," names)
            print("      (Explorer didn't select the picture by itself: selecting it here)", flush=True)
            peekshell.select(hwnd, str(files["picture"]))
        ok = check(until(picked, 5), f"the selection is read from File Explorer ({peekshell.selection(hwnd)})")
        explain()
        if ok:
            t = time.perf_counter()
            peekshell.selection(hwnd)
            print(f"      (read in {(time.perf_counter() - t) * 1000:.0f} ms)", flush=True)
        check(front(hwnd), "File Explorer is in front")
        if not until(lambda: c.peek.watcher.registered, 3) and state.get("before"):
            # (once on GitHub's runner the hook heard nothing when the test forced Explorer to the front with
            # AttachThreadInput; going to the terminal and back is what a person would do)
            hooked()
            print("      (the hook didn't hear File Explorer come to the front: going to the terminal and back)",
                  flush=True)
            front(state["before"])
            settle(0.5)
            front(hwnd)
        check(until(lambda: c.peek.watcher.registered, 3), "…so the shortcut is taken (the foreground hook saw it)")
        hooked()
        check(not key_free(), "…and Windows says Ctrl + Space is taken now")

    def picture():
        hwnd = state.get("hwnd")
        if not hwnd:
            return
        front(hwnd)
        until(lambda: c.peek.watcher.registered, 3)
        t0 = ctrl_space()
        if not check(until(shown, 5), "Ctrl + Space opens the preview window"):
            hooked()
            explain()
            return
        w = win()
        first_ms = (w.shown_at - t0) * 1000
        print(f"      (from the key press to the window: {first_ms:.0f} ms, the first time)", flush=True)
        check(w.current() == files["picture"], f"…with the selected picture ({w.current()})")
        check(w.kind == "picture", "…shown as a picture")
        check(until(lambda: isinstance(w.content, peekview._Fit), 5), "…and the picture appears")
        print(f"      (the picture itself after {(time.perf_counter() - t0) * 1000:.0f} ms)", flush=True)
        check(bool(w.windowFlags() & Qt.FramelessWindowHint), "…in a window without a frame")
        screen = QGuiApplication.screenAt(w.geometry().center())
        area = screen.availableGeometry() if screen else None
        check(area is not None and abs(w.geometry().center().x() - area.center().x()) < 4
              and abs(w.geometry().center().y() - area.center().y()) < 4, "…in the middle of the screen")
        check(until(w.isActiveWindow, 3), "…and it has the keyboard")
        shot("picture")
        check(until(lambda: len(w.files) > 1, 5), f"the rest of the folder is read for ← and → ({len(w.files)})")
        before = w.current()
        press(VK_RIGHT)
        check(until(lambda: w.current() != before, 2) and w.current() is not None
              and w.current().parent == folder, f"→ goes to the next file in the folder ({w.current()})")
        press(VK_LEFT)
        check(until(lambda: w.current() == before, 2), "← goes back")
        press(VK_ESC)
        check(until(lambda: not shown(), 2), "Esc closes it")
        check(until(lambda: peekshell.foreground() == hwnd, 3), "…and File Explorer is in front again")
        check(until(lambda: c.peek.watcher.registered, 3), "…with the shortcut taken again")
        # again, now that it's been built once
        t0 = ctrl_space()
        if check(until(shown, 5), "Ctrl + Space opens it again"):
            ms = (win().shown_at - t0) * 1000
            check(ms < 300, f"…in {ms:.0f} ms from the key press (aim: under 300 ms)")
            until(win().isActiveWindow, 3)
            settle(0.2)
            ctrl_space()
            check(until(lambda: not shown(), 2), "Ctrl + Space in the preview closes it")
        until(lambda: peekshell.foreground() == hwnd, 3)
        until(lambda: c.peek.watcher.registered, 3)
        t0 = ctrl_space()
        if check(until(shown, 5), "…and opens it once more"):
            until(win().isActiveWindow, 3)
            settle(0.2)
            press(VK_SPACE)
            check(until(lambda: not shown(), 2), "Space closes it too")

    def each_kind():
        hwnd = state.get("hwnd")
        if not hwnd:
            return
        wanted = {"text": "QPlainTextEdit", "pdf": "QScrollArea", "folder": "QWidget", "archive": "QTreeWidget",
                  "other": "QWidget", "video": "_Media"}
        for kind, cls in wanted.items():
            path = files.get(kind)
            if path is None:
                print(f"      (no {kind} file: ffmpeg isn't on PATH, skipped)", flush=True)
                continue
            front(hwnd)
            if not check(peekshell.select(hwnd, str(path)) and
                         until(lambda: peekshell.selection(hwnd) == [str(path)], 5),
                         f"{path.name} is selected in File Explorer"):
                explain()
                continue
            until(lambda: c.peek.watcher.registered, 3)
            ctrl_space()
            if not check(until(shown, 5) and win().current() == path, f"…Ctrl + Space shows {path.name}"):
                continue
            w = win()
            check(until(lambda: type(w.content).__name__ == cls, 8), f"…as {kind} ({type(w.content).__name__})")
            if kind == "pdf":
                check(until(lambda: w.pages_box is not None and w.pages_box.count() == 3, 8), "…all 3 pages drawn")
            if kind == "folder":
                check(until(lambda: w.folder_label is not None and ("6" in w.folder_label.text()
                                                                   or "٦" in w.folder_label.text()), 5),
                      f"…its files counted ({w.folder_label.text() if w.folder_label is not None else None})")
            if kind == "archive":
                check(w.content.topLevelItemCount() == 12, "…its 12 items listed")
            if kind == "video":
                settle(1.5)
                check(w.content.audio.isMuted(), "…playing without sound")
            check(w.corefold_btn.isVisible() == (kind in ("archive", "video")),
                  f"…'Open with Corefold' is offered only when Corefold opens it ({w.corefold_btn.isVisible()})")
            shot(kind)
            press(VK_ESC)
            check(until(lambda: not shown(), 2), "…Esc closes it")
            check(w.content is None, "…and nothing of the preview is kept")

    def several():
        hwnd = state.get("hwnd")
        if not hwnd:
            return
        front(hwnd)
        peekshell.select(hwnd, str(files["picture"]))
        peekshell.select(hwnd, str(files["text"]), only=False)
        if not check(until(lambda: len(peekshell.selection(hwnd)) == 2, 5), "two files selected"):
            return
        until(lambda: c.peek.watcher.registered, 3)
        ctrl_space()
        if not check(until(shown, 5) and len(win().files) == 2, "…Ctrl + Space shows the two of them"):
            return
        w = win()
        first = w.current()
        until(w.isActiveWindow, 3)
        press(VK_RIGHT)
        check(until(lambda: w.current() != first, 2), f"→ goes to the other one ({w.current().name})")
        press(VK_RIGHT)
        check(until(lambda: w.current() == first, 2), "…and round to the first again")
        press(VK_ESC)
        until(lambda: not shown(), 2)

    def desktop():
        desk_dir = Path(os.path.expanduser("~")) / "Desktop"
        progman = u.FindWindowW("Progman", None) or 0
        if not check(desk_dir.is_dir() and bool(progman), "the desktop is there"):
            return
        path = desk_dir / "corefold quick look test.txt"
        path.write_text("On the desktop", "utf-8")
        made.append(path)
        settle(1.5)  # (the desktop shows new files a moment later)
        _bring_to_front(progman)
        settle(0.5)
        fg = peekshell.foreground()
        check(peekshell.is_files_window(fg), f"the desktop is in front ({peekshell.window_class(fg)})")
        ok = until(lambda: peekshell.select(progman, str(path)) and str(path) in peekshell.selection(progman), 6)
        if not check(ok, f"a file on the desktop is selected ({peekshell.selection(progman)})"):
            return
        _bring_to_front(progman)
        check(until(lambda: c.peek.watcher.registered, 3), "…the shortcut is taken while the desktop is in front")
        ctrl_space()
        check(until(shown, 5) and win().current() == path, "…and Ctrl + Space shows the desktop file")
        shot("desktop")
        press(VK_ESC)
        until(lambda: not shown(), 2)

    def notepad():
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
            check(front(hwnd), "…and it's in front")
            check(until(lambda: not c.peek.watcher.registered, 3), "…so Quick look let go of the shortcut")
            check(key_free(), "…Windows says nobody holds Ctrl + Space (Notepad gets it)")
            ctrl_space()
            settle(0.8)
            check(not shown(), "Ctrl + Space in Notepad doesn't open a preview")
            check(peekshell.foreground() == hwnd, "…and Notepad is still in front")
        finally:
            u.PostMessageW(ctypes.c_void_p(hwnd), 0x0010, 0, 0)  # WM_CLOSE
            settle(0.8)
            if proc.poll() is None:
                proc.kill()
            for h in _windows_of_class("Notepad"):
                if h not in before:
                    u.PostMessageW(ctypes.c_void_p(h), 0x0010, 0, 0)

    def freed():
        w = win()
        check(w is not None and not w.isVisible(), "the window is hidden after closing")
        c._free_popups()
        settle(0.3)
        check(c.peek.window is None, "…and freed a while later (like the quick menu)")

    def tool_off():
        watcher = c.peek.watcher
        thread = watcher._thread
        hwnd = state.get("hwnd")
        if hwnd:
            front(hwnd)
            until(lambda: watcher.registered, 3)
        s.tools_off = ["viewer"]
        c._peek_tool()  # (only Quick look: the rest of apply_tools() would change Windows' "Open with" lists)
        settle(0.3)
        check(c.peek is None, "removing the Viewer stops Quick look")
        check(thread is None or not thread.is_alive(), "…its thread (and the foreground hook) is gone")
        if hwnd:
            front(hwnd)
            settle(0.3)
            check(key_free(), "…and Ctrl + Space is free even with File Explorer in front")
        s.tools_off = []
        c._peek_tool()
        check(c.peek is not None, "putting the Viewer back starts it again")
        s.peek_key = "off"
        c.settings_changed()
        check(c.peek is None, "…and switching its shortcut off stops it")
        s.peek_key = "ctrl+space"
        c.settings_changed()

    def settings_page():
        c.show_window("ViewerSettings")
        w = c.window
        w.resize(1100, 860)
        settle(0.6)
        combo = getattr(w, "peek_key", None)
        if check(combo is not None and combo.findData("ctrl+shift+space") >= 0, "Settings → Viewer has the shortcut"):
            combo.setCurrentIndex(combo.findData("ctrl+shift+space"))
            check(s.peek_key == "ctrl+shift+space" and c.peek.watcher.key == "ctrl+shift+space",
                  "picking another shortcut is used straight away")
            combo.setCurrentIndex(combo.findData("ctrl+space"))
        QGuiApplication.primaryScreen().grabWindow(0).save(str(OUT / f"settings-{lang}-{look}.png"))
        c.show_window("Shortcuts")
        settle(0.6)
        QGuiApplication.primaryScreen().grabWindow(0).save(str(OUT / f"shortcuts-{lang}-{look}.png"))
        w.hide()
        settle(0.2)

    try:
        for name, fn in (("start", start), ("picture", picture), ("kinds", each_kind), ("several", several),
                         ("desktop", desktop), ("notepad", notepad), ("freed", freed), ("off", tool_off),
                         ("page", settings_page)):
            step(name, fn)
    finally:
        for h in opened:
            u.PostMessageW(ctypes.c_void_p(h), 0x0010, 0, 0)  # WM_CLOSE: the File Explorer windows opened here
        for p in made:
            try:
                p.unlink()
            except OSError:
                pass
        settle(0.5)
        c.quit()


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
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=140)
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
print(f"newtest_peek: {len(problems)} problem(s)", flush=True)
faulthandler.disable()
sys.exit(1 if problems else 0)
