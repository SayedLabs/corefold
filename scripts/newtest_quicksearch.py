"""Checks finding apps and files from the quick menu on a real Windows PC: the Start menu's apps and
Windows' Apps folder, a new file in Documents found through Windows' search index (or, if the index
isn't there, through Windows' Recent items), Everything's own messages (with a real Everything, if it
can be downloaded), real key presses in the menu, opening Notepad, showing a file in File Explorer, and
pictures of the screen.

Run from the repo root: python scripts/newtest_quicksearch.py
It checks the logic, then the real thing in English (dark) and in Arabic (light), and prints one line
per check and "newtest_quicksearch: N problem(s)". Pictures go to out/newtest_quicksearch/. The file it
makes (and Windows' Recent item for it) is removed at the end."""
import ctypes
import faulthandler
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "newtest_quicksearch"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
PART = sys.argv[1:3] if len(sys.argv) >= 3 else None
faulthandler.dump_traceback_later(95 if PART else 295, exit=True)  # stuck somewhere: say where, and stop
STARTED = time.monotonic()
problems = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)
    return ok


def skip(what):
    print("skip  " + what, flush=True)


# ---------------------------------------------------------------------------- the logic (no window needed)

def logic():
    from corefold import quicksearch as qs
    check(qs.app_score("vsc", "Visual Studio Code") >= 70, "'vsc' fits Visual Studio Code (initials)")
    check(qs.app_score("psi", "PowerShell ISE") >= 70, "'psi' fits PowerShell ISE (capitals count as words)")
    check(qs.app_score("notepad", "Notepad") == 100, "'notepad' is Notepad")
    check(qs.app_score("calc", "Calculator") == 90, "'calc' starts Calculator")
    check(qs.app_score("code", "Visual Studio Code") == 75, "'code' starts one of its words")
    check(qs.app_score("zzq", "Notepad") == 0, "'zzq' fits nothing")
    apps = [qs.App("Notepad++", "a.lnk"), qs.App("Notepad", "b.lnk"), qs.App("Sticky Notes", "c", shell=True)]
    ranked = [a.name for _, a in qs.match_apps(apps, "notepad")]
    check(ranked == ["Notepad", "Notepad++"], f"Notepad comes before Notepad++ ({ranked})")
    check(qs.App("x", "C:\\a\\x.lnk").admin and not qs.App("x", "Microsoft.App!App", shell=True).admin,
          "a shortcut can run as administrator, a Store app can't")
    check(qs.aqs("cfq-12 report") == '"cfq-12" report', f"words with signs are quoted for the index ({qs.aqs('cfq-12 report')})")
    check(qs.file_score("report", "report.docx") == 100 and qs.file_score("rep", "my report.docx") == 75,
          "files whose names fit come first")
    # Everything's answer, made by hand (EVERYTHING_IPC_LIST2 with a name and a path)
    def item(name, path):
        return struct.pack("<I", len(name)) + (name + "\0").encode("utf-16-le") + \
            struct.pack("<I", len(path)) + (path + "\0").encode("utf-16-le")
    one, two = item("a.txt", "C:\\Docs"), item("Pics", "D:\\")
    head = struct.pack("<5I", 2, 2, 0, 0x3, 1)
    first = 20 + 16
    body = head + struct.pack("<2I", 0, first) + struct.pack("<2I", 1, first + len(one)) + one + two
    found = qs.parse_everything(body)
    check([(f.path, f.folder) for f in found] == [("C:\\Docs\\a.txt", False), ("D:\\Pics", True)],
          f"Everything's answer is read ({found})")
    check(qs.parse_everything(b"\0" * 7) == [], "…and a broken one is nothing")


# ---------------------------------------------------------------------------- the real Windows parts

def real_apps():
    from corefold import quicksearch as qs
    t = time.monotonic()
    start = qs.start_menu_apps()
    t_start = (time.monotonic() - t) * 1000
    check(len(start) > 3, f"the Start menu has apps ({len(start)}, {t_start:.0f} ms)")
    check(not any("uninstall" in a.name.lower() for a in start), "…without the Uninstall shortcuts")
    t = time.monotonic()
    folder = qs.apps_folder()
    t_folder = (time.monotonic() - t) * 1000
    check(len(folder) > 3, f"Windows' Apps folder is read ({len(folder)} items, {t_folder:.0f} ms)")
    print("      e.g. " + ", ".join(f"{n} [{p}]" for n, p in folder[:6]), flush=True)
    every = qs.all_apps()
    store = [a for a in every if a.shell]
    print(f"      {len(every)} apps in all, {len(store)} through the Apps folder", flush=True)
    names = {a.name.lower() for a in every}
    check("notepad" in names, "Notepad is one of them")
    resolved = [a.target for a in start if a.target.lower().endswith(".lnk") and qs.lnk_target(a.target)]
    check(any(os.path.exists(qs.lnk_target(p)) for p in resolved[:40]),
          f"the Start menu's shortcuts are read ({len(resolved)} of them point at files)")


def shell_add_recent(path: str):
    ctypes.windll.shell32.SHAddToRecentDocs.argtypes = [ctypes.c_uint, ctypes.c_wchar_p]
    ctypes.windll.shell32.SHAddToRecentDocs(3, path)  # SHARD_PATHW


def recent_items_for(path: Path) -> list[Path]:
    """Windows' Recent items that point at path (named "x.txt.lnk" or "x.lnk", depending on Windows)."""
    from corefold import quicksearch as qs
    recent = Path(qs.known_folder("recent"))
    try:
        lnks = [p for p in recent.iterdir() if p.suffix.lower() == ".lnk" and p.name.lower().startswith(path.stem.lower())]
    except OSError:
        return []
    return [p for p in lnks if os.path.normcase(qs.lnk_target(str(p))) == os.path.normcase(str(path))]


def recent_item(path: Path, seconds: float) -> Path | None:
    end = time.time() + seconds
    while True:
        found = recent_items_for(path)
        if found or time.time() >= end:
            return found[0] if found else None
        time.sleep(0.2)


def own_shortcut(path: Path):
    """Shortcuts made the way any app makes them (WScript.Shell), to a file and to a folder, read back."""
    from corefold import quicksearch as qs
    tmp = Path(tempfile.mkdtemp(prefix="corefold-lnk-"))
    try:
        for target, lnk in ((path, tmp / "to a file.lnk"), (path.parent, tmp / "to a folder.lnk")):
            script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:CF_LNK); "
                      "$s.TargetPath = $env:CF_TARGET; $s.Save()")
            try:
                subprocess.run(["powershell", "-NoProfile", "-Command", script], timeout=30, capture_output=True,
                               env=dict(os.environ, CF_LNK=str(lnk), CF_TARGET=str(target)))
            except Exception:
                pass
            if check(lnk.exists(), f"a shortcut {lnk.stem} was made for the test"):
                got = qs.lnk_target(str(lnk))
                check(os.path.normcase(got.rstrip("\\")) == os.path.normcase(str(target).rstrip("\\")),
                      f"…and Corefold reads where it points ({got})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def prepare(name: str) -> tuple[Path, str]:
    """Makes the file in Documents, then waits for Windows' index (or else its Recent items) to have it."""
    from corefold import quicksearch as qs
    docs = Path(qs.known_folder("documents"))
    check(docs.is_dir(), f"Documents is there ({docs})")
    path = docs / f"{name}.txt"
    path.write_text("Corefold's quick menu test. It is removed when the test ends.\n", "utf-8")
    source = ""
    qs._init_thread()
    first = qs.index_query(name)
    if first is None:
        skip("index not available: Windows' search index isn't on this PC (the Recent items are used instead)")
    else:
        end = time.time() + 60
        while time.time() < end:
            got = qs.index_query(name) or []
            if any(os.path.normcase(f.path) == os.path.normcase(str(path)) for f in got):
                source = "index"
                break
            time.sleep(1.5)
        if source:
            check(True, f"Windows' search index has the new file ({path})")
        else:
            skip("index not available: the index didn't pick up the new file within 60 seconds")
    # Windows' Recent items: the way it works with no index (and always added)
    own_shortcut(path)
    shell_add_recent(str(path))
    lnk = recent_item(path, 10)
    if lnk is None:
        skip("Windows made no Recent item for it on this PC (its Recent items may be switched off): the Recent "
             "items weren't checked with it")
    else:
        check(True, f"Windows made a Recent item for it ({lnk.name})")
        got = qs.recent_query(name)
        check(any(os.path.normcase(f.path) == os.path.normcase(str(path)) for f in got),
              f"the Recent items find it by name ({[f.path for f in got]})")
    print("      Everything is " + ("running" if qs.everything_window() else "not running"), flush=True)
    if not qs.everything_window():
        check(qs.everything_query(name) is None, "…and asking it gives nothing (no waiting)")
    src, files = qs.find_files(name)
    if source or lnk is not None or src == "everything":
        check(any(os.path.normcase(f.path) == os.path.normcase(str(path)) for f in files),
              f"all of it together finds the file (from {src})")
        check(not any("\\recent\\" in f.path.lower() for f in files), "…and not Windows' own Recent shortcut for it")
    else:
        skip("no index and no Recent items on this PC: nothing can find the file")
    return path, source or "recent"


def everything_real(path: Path, name: str):
    """A real Everything (the portable one from voidtools.com): asked over its own messages."""
    from corefold import quicksearch as qs
    if qs.everything_window():
        skip("Everything was already running: not started for the test")
        return
    left = 280 - (time.monotonic() - STARTED)  # (the whole test stays under 5 minutes)
    if left < 40:
        skip(f"no time left to try Everything ({left:.0f} s)")
        return
    tmp = Path(tempfile.mkdtemp(prefix="corefold-everything-"))
    exe = tmp / "Everything.exe"
    proc = None
    try:
        try:
            page = urllib.request.urlopen("https://www.voidtools.com/downloads/", timeout=15).read().decode("utf-8", "replace")
            m = re.search(r'href="(/?Everything-[\d.]+\.x64\.zip)"', page)
            url = "https://www.voidtools.com/" + m.group(1).lstrip("/") if m else \
                "https://www.voidtools.com/Everything-1.4.1.1026.x64.zip"
            data = urllib.request.urlopen(url, timeout=20).read()
            (tmp / "e.zip").write_bytes(data)
            with zipfile.ZipFile(tmp / "e.zip") as z:
                z.extractall(tmp)
        except Exception as e:
            skip(f"Everything couldn't be downloaded ({e})")
            return
        if not exe.exists():
            skip("Everything.exe wasn't in the download")
            return
        proc = subprocess.Popen([str(exe), "-startup"], cwd=str(tmp))
        t0 = time.monotonic()
        started = ready = False
        end = time.time() + min(45, left - 30)
        while time.time() < end and not ready:  # (it reads the drives first: asked until it says it's done)
            started = started or bool(qs.everything_window())
            ready = started and qs.everything_ready()
            if not ready:
                time.sleep(0.5)
        if not started:
            skip("Everything didn't start on this PC (it's a download from voidtools.com)")
            return
        if not ready:
            skip("Everything started but hadn't read the drives in time: it wasn't asked")
            return
        check(True, f"Everything started and read the drives ({time.monotonic() - t0:.0f} s)")
        found, answered = None, False
        end = time.time() + max(5, min(20, 280 - (time.monotonic() - STARTED) - 10))
        while time.time() < end:  # (a file made just before may take a moment to be in its list)
            found = qs.everything_query(name)
            answered = answered or found is not None
            if found and any(os.path.normcase(f.path) == os.path.normcase(str(path)) for f in found):
                break
            time.sleep(0.5)
        check(answered, "Everything answers over its messages")
        check(bool(found) and any(os.path.normcase(f.path) == os.path.normcase(str(path)) for f in found),
              f"…and has the new file ({[f.path for f in found or []][:3]})")
        t = time.monotonic()
        src, files = qs.find_files(name)
        ms = (time.monotonic() - t) * 1000
        check(src == "everything" and any(os.path.normcase(f.path) == os.path.normcase(str(path)) for f in files),
              f"…and the menu's search uses it first ({src}, {ms:.0f} ms)")
    finally:
        if proc is not None:
            try:
                subprocess.run([str(exe), "-exit"], timeout=10)
            except Exception:
                pass
            try:
                proc.wait(5)
            except Exception:
                proc.kill()
        time.sleep(0.5)
        shutil.rmtree(tmp, ignore_errors=True)


def explorer_selection(path: str, close: bool) -> list[str]:
    """What File Explorer windows have selected (asked through the shell, like a script would)."""
    script = ("$sh = New-Object -ComObject Shell.Application; foreach ($w in @($sh.Windows())) { try { "
              "foreach ($i in @($w.Document.SelectedItems())) { 'SEL|' + $i.Path; "
              "if ($env:CF_CLOSE -eq '1' -and $i.Path -eq $env:CF_PATH) { $w.Quit() } } } catch {} }")
    env = dict(os.environ, CF_PATH=path, CF_CLOSE="1" if close else "0")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                           timeout=20, env=env)
    except Exception:
        return []
    return [line[4:].strip() for line in r.stdout.splitlines() if line.startswith("SEL|")]


# ---------------------------------------------------------------------------- the menu, in one language

def part(lang, look):
    name = os.environ["CF_QS_NAME"]
    file_path = os.environ["CF_QS_FILE"]
    tmp = Path(tempfile.mkdtemp(prefix="corefold-newtest-qs-"))
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
    from PySide6.QtWidgets import QApplication, QCheckBox
    qapp = QApplication(sys.argv[:1])
    from corefold import i18n
    i18n.set_language(lang)
    if i18n.is_rtl():
        qapp.setLayoutDirection(Qt.RightToLeft)
    from corefold import app as A
    from corefold import quicksearch as qs
    from corefold.i18n import tr
    c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
    s = c.settings
    u = ctypes.windll.user32
    VK_CTRL, VK_ALT, VK_SPACE, VK_ESC, VK_ENTER, VK_SHIFT = 0x11, 0x12, 0x20, 0x1B, 0x0D, 0x10

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

    def type_text(text, wait=0.15):
        for ch in text:
            for flags in (0x4, 0x4 | 0x2):  # KEYEVENTF_UNICODE, then KEYEVENTF_KEYUP
                inp = INPUT(type=1, ki=KEYBDINPUT(0, ord(ch), flags, 0, 0))
                u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            settle(0.03)
        settle(wait)

    def shot(what):
        settle(0.3)
        QGuiApplication.primaryScreen().grabWindow(0).save(str(OUT / f"{what}-{lang}-{look}.png"))

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
        settle(0.2)
        return ok

    def file_row(m):
        for i, sp in enumerate(m.spots):
            if sp.kind == "item" and sp.entry.id.startswith("find:") and \
                    os.path.normcase(sp.entry.id[5:]) == os.path.normcase(file_path):
                return i
        return -1

    def step(what, fn):
        try:
            fn()
        except Exception:
            check(False, f"{what}: {traceback.format_exc().splitlines()[-1]}")
            traceback.print_exc()
        if menu_visible():
            menu().hide()
        settle(0.2)

    def files():
        check(until(lambda: c.quick is not None, 3), "quick access is running")
        u.SetCursorPos(500, 400)
        check(open_menu(), "the shortcut opens the menu")
        m = menu()
        check(until(lambda: bool(qs.apps()), 10), f"the apps are listed when it opens ({len(qs.apps())})")
        type_text(name, wait=0)
        t0 = time.monotonic()
        got = until(lambda: file_row(m) >= 0, 8)
        first_ms = (time.monotonic() - t0) * 1000
        check(got, f"typing its name finds the new file ({m.finder.source if m.finder else '?'}, "
                   f"{first_ms:.0f} ms after the last key, the first search)")
        i = file_row(m)
        if i >= 0:
            sp = m.spots[i]
            check(os.path.normcase(sp.entry.hint) == os.path.normcase(str(Path(file_path).parent)),
                  f"…with its folder on the row ({sp.entry.hint})")
            check(m.item_icon(sp.entry, 22) is not None, "…and its own icon")
        shot("files")
        # again, timed (Windows' search is warmed up now)
        press(VK_ESC)
        check(m.isVisible() and m.query == "", "Esc clears what was typed")
        type_text(name[:-1], wait=0)
        t0 = time.monotonic()
        got = until(lambda: file_row(m) >= 0, 5)
        ms = (time.monotonic() - t0) * 1000
        print(f"      type to first file row: {ms:.0f} ms (aim: under 500 ms)", flush=True)
        check(got and ms < 1500, f"part of its name finds it too, {ms:.0f} ms after the last key "
                                 f"({'under' if ms < 500 else 'over'} the 500 ms aimed for)")
        if lang != "en" or not got:
            return
        m.selected = file_row(m)
        m.update()
        check(tr("Shift + Enter: show in folder") == m._side_note(m.spots[m.selected], True),
              "the row says Shift + Enter shows it in its folder")
        press(VK_SHIFT, VK_ENTER)
        check(until(lambda: not m.isVisible(), 2), "Shift + Enter puts the menu away")
        sel = []
        end = time.time() + 12
        while time.time() < end:
            settle(0.5)
            sel = explorer_selection(file_path, close=False)
            if any(os.path.normcase(x) == os.path.normcase(file_path) for x in sel):
                break
        check(any(os.path.normcase(x) == os.path.normcase(file_path) for x in sel),
              f"…and File Explorer shows it, selected ({sel})")
        shot("explorer")
        explorer_selection(file_path, close=True)
        settle(1)
        check(not any(os.path.normcase(x) == os.path.normcase(file_path) for x in explorer_selection(file_path, False)),
              "…closed again")

    def apps_rows():
        check(open_menu(), "the menu opens for an app")
        m = menu()
        type_text("notepad")
        first = m.spots[0] if m.spots else None
        check(first is not None and first.kind == "item" and first.entry.label.lower() == "notepad",
              f"'notepad': Notepad is the first row ({first.entry.label if first else None})")
        if first is not None and first.kind == "item":
            check(m.item_icon(first.entry, 22) is not None, "…with its own icon")
            item = m.found_items.get(first.entry.id)
            if item is not None and item.admin:
                check(m._side_note(first, True) == tr("Ctrl + Enter: run as administrator"),
                      "…and says Ctrl + Enter runs it as administrator")
        shot("apps")
        if lang == "en" and first is not None and first.kind == "item":
            before = set(_windows_of_class("Notepad"))
            m.selected = 0
            press(VK_ENTER)
            new = []
            end = time.time() + 15
            while time.time() < end and not new:
                settle(0.3)
                new = [h for h in _windows_of_class("Notepad") if h not in before]
            check(bool(new), "Enter starts Notepad")
            for h in new:
                u.PostMessageW(ctypes.c_void_p(h), 0x0010, 0, 0)  # WM_CLOSE
            settle(1)
            check(not [h for h in _windows_of_class("Notepad") if h not in before], "…closed again")
        calc = [a for a in qs.apps() if a.name.lower() == "calculator"]
        if not calc:
            skip("no Calculator on this PC ('calc' not checked)")
        else:
            check(open_menu(), "the menu opens for Calculator")
            type_text("calc")
            first = m.spots[0] if m.spots else None
            check(first is not None and first.entry.label.lower() == "calculator",
                  f"'calc': Calculator is the first row ({first.entry.label if first else None})")
            if lang == "ar":
                shot("calc")

    def closing():
        check(open_menu(), "the menu opens to be closed while it looks")
        m = menu()
        type_text(name[:5], wait=0)
        settle(DEBOUNCE + 0.01)  # (the search has started on its thread)
        press(VK_ESC)
        press(VK_ESC)
        check(until(lambda: not m.isVisible(), 2), "Esc puts it away while it's looking")
        settle(1.5)
        check(not m.isVisible() and not m.finder.files, "…and what came back afterwards was dropped")
        check(open_menu() and menu().query == "" and not [sp for sp in menu().spots if sp.kind == "item"],
              "…and it opens again as it was")
        m = menu()
        m.finder.search(name)
        settle(DEBOUNCE + 0.02)
        m.hide()
        settle(1.5)
        check(not m.isVisible() and not m.finder.files, "closed straight after a search began: nothing breaks")

    def prefixes():
        check(open_menu(), "the menu opens for '?'")
        m = menu()
        type_text("?" + name)
        check([sp.entry.id for sp in m.spots] == ["web"], f"a leading '?' only searches the web "
                                                         f"({[sp.entry.id for sp in m.spots]})")
        press(VK_ESC)
        s.quick_find = False
        type_text("notepad")
        settle(0.5)
        check(not [sp for sp in m.spots if sp.kind == "item"], "with “Find files and apps as I type” off, none are found")
        s.quick_find = True

    def settings_page():
        c.show_window("Quick")
        w = c.window
        w.resize(1100, 860)
        settle(0.6)
        page = w.stack.currentWidget()
        boxes = [b for b in page.findChildren(QCheckBox) if b.text() == tr("Find files and apps as I type")]
        if check(len(boxes) == 1 and boxes[0].isChecked(), "Settings → Quick access has “Find files and apps as I type”, on"):
            boxes[0].setChecked(False)
            check(s.quick_find is False, "…switching it off is kept")
            boxes[0].setChecked(True)
            check(s.quick_find is True, "…and on again")
        shot("settings-page")
        w.hide()
        settle(0.2)

    DEBOUNCE = qs.DEBOUNCE_MS / 1000
    for what, fn in (("files", files), ("apps", apps_rows), ("closing", closing), ("prefixes", prefixes),
                     ("page", settings_page)):
        step(what, fn)


def _windows_of_class(cls):
    u = ctypes.windll.user32
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def each(h, _):
        buf = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(ctypes.c_void_p(h), buf, 64)
        if buf.value == cls and u.IsWindowVisible(ctypes.c_void_p(h)):
            found.append(int(h))
        return True
    u.EnumWindows(proc(each), None)
    return found


if PART:
    part(*PART)
    print(f"({' '.join(PART)}) {len(problems)} problem(s)", flush=True)
    faulthandler.disable()
    os._exit(1 if problems else 0)

logic()
name = f"cfqs{random.randint(10000, 99999)}"
path = None
try:
    from PySide6.QtWidgets import QApplication
    _qapp = QApplication(sys.argv[:1])  # (for the icons)
    try:
        real_apps()
    except Exception:
        check(False, f"apps: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()
    path, source = prepare(name)
    for lang, look in (("en", "dark"), ("ar", "light")):
        print(f"---- {lang} {look} (files from {source})", flush=True)
        env = dict(os.environ, CF_QS_NAME=name, CF_QS_FILE=str(path))
        try:
            r = subprocess.run([sys.executable, str(Path(__file__).resolve()), lang, look], cwd=str(ROOT), env=env,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=100)
            out = r.stdout + r.stderr
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            out += "\nFAIL  timed out"
        print(out, flush=True)
        fails = [line[6:] for line in out.splitlines() if line.startswith("FAIL  ")]
        if not any(line.startswith(f"({lang} {look}) ") for line in out.splitlines()):
            fails.append(f"the {lang} {look} run didn't finish")
        problems += [f"({lang}) {f}" for f in fails]
    everything_real(path, name)
except Exception:
    check(False, f"the test itself: {traceback.format_exc().splitlines()[-1]}")
    traceback.print_exc()
finally:
    for p in ([path, *recent_items_for(path)] if path is not None else []):
        try:
            if p is not None and p.exists():
                p.unlink()
        except OSError:
            pass
    check(path is None or not path.exists(), "the test's file is removed")
    check(path is None or not recent_items_for(path), "…and its Recent item")
(OUT / "problems.txt").write_text("\n".join(problems) or "none", "utf-8")
print(f"newtest_quicksearch: {len(problems)} problem(s)", flush=True)
faulthandler.disable()
sys.exit(1 if problems else 0)
