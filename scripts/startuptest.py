"""Corefold Startup, tried for real on a Windows PC: made-up programs are added to the places Windows
starts things from, then listed, switched off and on (by code and by clicking the page), searched and
filtered, and everything is checked against what Task Manager writes. Every other startup entry on the
PC must be exactly as it was at the end, and nothing is ever deleted.

Run: python scripts/startuptest.py <folder> [en|ar|fr|es|tr] [dark|light]
Prints one line per check and "startuptest: N problem(s)"; pictures of the page go in the folder."""
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "startuptest").resolve()
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
LOOK = sys.argv[3] if len(sys.argv) > 3 else "dark"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-startuptest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(600, exit=True)  # stuck somewhere: say where, and stop

import winreg  # noqa: E402

from corefold import settings as st  # noqa: E402
from corefold import startup  # noqa: E402
from corefold import tools as all_tools  # noqa: E402

problems = []
errors = []
SYSTEM32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
NOTEPAD = SYSTEM32 / "notepad.exe"
GONE = TMP / "gone" / "NotThereAnyMore.exe"
TEST = ["CfStartupTest", "CfStartupGone", "CfStartupLater", "CfStartupWide", "CfStartupWide32"]
HKLM_64 = winreg.KEY_WOW64_64KEY
HKLM_32 = winreg.KEY_WOW64_32KEY


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)


def step(name, fn):
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()


# ----------------------------------------------------------------------------- the registry, by hand

def _open(hive, path, view=0, write=False):
    access = (winreg.KEY_ALL_ACCESS if write else winreg.KEY_READ) | view
    return winreg.CreateKeyEx(hive, path, 0, access) if write else winreg.OpenKey(hive, path, 0, access)


def put(hive, path, name, value, kind=winreg.REG_SZ, view=0):
    with _open(hive, path, view, write=True) as k:
        winreg.SetValueEx(k, name, 0, kind, value)


def get(hive, path, name, view=0):
    try:
        with _open(hive, path, view) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def drop(hive, path, name, view=0):
    try:
        with _open(hive, path, view, write=True) as k:
            winreg.DeleteValue(k, name)
    except OSError:
        pass


def snapshot() -> dict:
    """Every startup entry and every on/off note on this PC (to prove the rest is left alone)."""
    out = {}
    places = [("HKCU", winreg.HKEY_CURRENT_USER, startup.RUN, 0),
              ("HKLM64", winreg.HKEY_LOCAL_MACHINE, startup.RUN, HKLM_64),
              ("HKLM32", winreg.HKEY_LOCAL_MACHINE, startup.RUN, HKLM_32)]
    for tail in ("Run", "Run32", "StartupFolder"):
        places += [(f"HKCU-{tail}", winreg.HKEY_CURRENT_USER, startup.APPROVED + "\\" + tail, 0),
                   (f"HKLM-{tail}", winreg.HKEY_LOCAL_MACHINE, startup.APPROVED + "\\" + tail, HKLM_64)]
    for label, hive, path, view in places:
        try:
            with _open(hive, path, view) as k:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    if name not in TEST and not name.startswith("CfStartup") and name != st.APP_NAME:
                        out[(label, name)] = value
                    i += 1
        except OSError:
            pass
    return out


def make_shortcut(link: Path, target: Path):
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}'); "
              f"$s.TargetPath = '{target}'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True)


def find(entries, value, where=None):
    return next((e for e in entries if e.value == value and (where is None or e.where == where)), None)


BEFORE = snapshot()
print(f"admin: {startup.is_admin()}, 64-bit Windows: {startup._is_64bit()}, "
      f"{len(BEFORE)} other startup entries and notes on this PC", flush=True)


# ----------------------------------------------------------------------------- the list and the switches

def listing():
    put(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupTest", f'"{NOTEPAD}" /startup')
    put(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupGone", f'"{GONE}" --tray')
    folder = startup.folders()["User-Folder"]
    folder.mkdir(parents=True, exist_ok=True)
    make_shortcut(folder / "CfStartupNotes.lnk", NOTEPAD)
    (folder / "desktop.ini").write_text("[.ShellClassInfo]\n")
    t0 = time.time()
    entries = startup.list_entries()
    took = time.time() - t0
    check(took < 5, f"the list is read in {took:.2f} s ({len(entries)} entries)")
    e = find(entries, "CfStartupTest", "HKCU-Run")
    check(e is not None, "a program in this user's Run key is listed")
    if e:
        check(e.on and not e.missing and not e.admin, "it's on, its program is there, no admin needed")
        check(e.exe.lower() == str(NOTEPAD).lower(), f"its program is found ({e.exe})")
        check(e.name.lower() != "cfstartuptest" and "notepad" in e.name.lower(),
              f"it's shown by the program's own name ({e.name!r})")
        check("microsoft" in e.publisher.lower(), f"its publisher is read from the file ({e.publisher!r})")
    g = find(entries, "CfStartupGone")
    check(g is not None and g.missing, "an entry whose program is gone is listed and marked")
    s_ = find(entries, "CfStartupNotes.lnk", "User-Folder")
    check(s_ is not None, "a shortcut in the Startup folder is listed")
    if s_:
        check(s_.exe.lower() == str(NOTEPAD).lower(), f"the shortcut's program is found ({s_.exe})")
    check(find(entries, "desktop.ini") is None, "desktop.ini isn't listed")
    check(all(x.id for x in entries) and len({x.id for x in entries}) == len(entries), "every entry is unique")
    for x in entries:
        if x.value not in TEST and not x.value.startswith("CfStartup"):
            print(f"      on this PC: {x.where:13} {'on ' if x.on else 'off'} {x.name!r} ({x.publisher!r})"
                  f"{' [program gone]' if x.missing else ''}", flush=True)


def switching():
    run_before = get(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupTest")
    e = find(startup.list_entries(), "CfStartupTest", "HKCU-Run")
    t0 = time.time()
    check(startup.set_on(e, False), "switched off")
    note = get(winreg.HKEY_CURRENT_USER, startup.APPROVED + r"\Run", "CfStartupTest")
    check(isinstance(note, bytes) and len(note) == 12 and note[0] == 3,
          f"the note is the one Task Manager writes ({note.hex() if isinstance(note, bytes) else note})")
    check(abs(startup.note_time(note) - t0) < 120, "the note says when it was switched off")
    check(get(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupTest") == run_before,
          "the entry itself is untouched (not deleted, not changed)")
    again = find(startup.list_entries(), "CfStartupTest", "HKCU-Run")
    check(again is not None and not again.on and again.off_since > 0, "listed as off, with the date")
    check(startup.set_on(again, True), "switched back on")
    note = get(winreg.HKEY_CURRENT_USER, startup.APPROVED + r"\Run", "CfStartupTest")
    check(note == startup.ON_BYTES, "the note is Task Manager's \"on\"")
    check(get(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupTest") == run_before, "the entry is still untouched")
    # what Task Manager itself writes, in every form seen on real PCs
    for raw, on in (("020000000000000000000000", True), ("030000001234567890abcd01", False),
                    ("060000000000000000000000", True), ("070000000000000000000000", False),
                    ("01000000", False)):
        put(winreg.HKEY_CURRENT_USER, startup.APPROVED + r"\Run", "CfStartupTest", bytes.fromhex(raw),
            winreg.REG_BINARY)
        x = find(startup.list_entries(), "CfStartupTest", "HKCU-Run")
        check(x is not None and x.on == on, f"Task Manager's {raw[:2]}… note reads as {'on' if on else 'off'}")
    put(winreg.HKEY_CURRENT_USER, startup.APPROVED + r"\Run", "CfStartupTest", startup.ON_BYTES, winreg.REG_BINARY)
    # the Startup folder's own notes
    s_ = find(startup.list_entries(), "CfStartupNotes.lnk", "User-Folder")
    check(startup.set_on(s_, False), "a Startup folder shortcut switched off")
    note = get(winreg.HKEY_CURRENT_USER, startup.APPROVED + r"\StartupFolder", "CfStartupNotes.lnk")
    check(isinstance(note, bytes) and note[0] == 3, "its note is in StartupFolder, like Task Manager's")
    check((startup.folders()["User-Folder"] / "CfStartupNotes.lnk").exists(), "the shortcut itself is still there")
    check(startup.set_on(s_, True), "and back on")


def whole_pc():
    """Entries for everyone on the PC (HKLM, both 64 and 32-bit): an administrator changes them."""
    if not startup.is_admin():
        check(True, "not an administrator here: the PC-wide entries are only listed")
        return
    put(winreg.HKEY_LOCAL_MACHINE, startup.RUN, "CfStartupWide", f'"{NOTEPAD}"', view=HKLM_64)
    put(winreg.HKEY_LOCAL_MACHINE, startup.RUN, "CfStartupWide32", f'"{NOTEPAD}"', view=HKLM_32)
    entries = startup.list_entries()
    w64, w32 = find(entries, "CfStartupWide", "HKLM-Run"), find(entries, "CfStartupWide32", "HKLM-Run32")
    check(w64 is not None and w64.admin, "a 64-bit entry for everyone is listed, as one that needs an administrator")
    check(w32 is not None and w32.admin, "a 32-bit entry for everyone is listed")
    if w64:
        check(startup.set_on(w64, False), "the 64-bit one switched off")
        note = get(winreg.HKEY_LOCAL_MACHINE, startup.APPROVED + r"\Run", "CfStartupWide", HKLM_64)
        check(isinstance(note, bytes) and note[0] == 3, "its note is under HKLM ...\\StartupApproved\\Run")
        check(startup.set_on(w64, True), "and back on")
    if w32:
        check(startup.set_on(w32, False), "the 32-bit one switched off")
        note = get(winreg.HKEY_LOCAL_MACHINE, startup.APPROVED + r"\Run32", "CfStartupWide32", HKLM_64)
        check(isinstance(note, bytes) and note[0] == 3, "its note is under ...\\StartupApproved\\Run32")
        check(startup.set_on(w32, True), "and back on")
    # the way it's done for a person who isn't an administrator: reg.exe, started as administrator
    ok = startup._write_note_as_admin("HKLM", startup.APPROVED + r"\Run", "CfStartupWide", startup.off_note())
    note = get(winreg.HKEY_LOCAL_MACHINE, startup.APPROVED + r"\Run", "CfStartupWide", HKLM_64)
    check(ok and isinstance(note, bytes) and len(note) == 12 and note[0] == 3,
          "switching off through Windows' administrator prompt (reg.exe) writes the same note")
    ok = startup._write_note_as_admin("HKLM", startup.APPROVED + r"\Run", "CfStartupWide", startup.ON_BYTES)
    check(ok and get(winreg.HKEY_LOCAL_MACHINE, startup.APPROVED + r"\Run", "CfStartupWide", HKLM_64)
          == startup.ON_BYTES, "and on again")
    check(not startup._write_note_as_admin("HKLM", startup.APPROVED + r"\Run", 'Bad"Name', startup.ON_BYTES),
          "a name that can't be passed to reg.exe safely is refused")
    # no permission and no prompt allowed: told, and nothing changes
    real = startup._write_note

    def refuse(*_a):
        raise PermissionError(5, "Access is denied")
    startup._write_note = refuse
    try:
        try:
            startup.set_on(w64, False, ask_admin=False)
            check(False, "no permission: it says it needs an administrator")
        except startup.NeedsAdmin:
            check(True, "no permission: it says it needs an administrator")
    finally:
        startup._write_note = real
    check(get(winreg.HKEY_LOCAL_MACHINE, startup.APPROVED + r"\Run", "CfStartupWide", HKLM_64) == startup.ON_BYTES,
          "and nothing changed")


def new_since():
    startup.mark_new(startup.list_entries())   # (the first look: nothing is new)
    put(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupLater", f'"{NOTEPAD}" /later')
    entries = startup.list_entries()
    count = startup.mark_new(entries)
    later = find(entries, "CfStartupLater")
    check(later is not None and later.new and count == 1, f"a program added since the last look is New ({count})")
    entries = startup.list_entries()
    startup.mark_new(entries)
    check(not find(entries, "CfStartupLater").new, "and isn't New the time after")
    drop(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupLater")


def own_entry():
    """Corefold's own line and its setting in Settings → General always agree."""
    st.set_startup(True)
    check(st.startup_enabled(), "Corefold set to start with Windows")
    own = next((e for e in startup.list_entries() if e.own), None)
    check(own is not None, "Corefold's own line is found")
    if own:
        check(startup.set_on(own, False) and not st.startup_enabled(),
              "switched off on the Startup page (or in Task Manager): Settings says off too")
        st.set_startup(True)
        check(st.startup_enabled() and next(e for e in startup.list_entries() if e.own).on,
              "switched on in Settings: really on again")
    st.set_startup(False)
    check(not st.startup_enabled(), "and off")


# ----------------------------------------------------------------------------- the page, clicked like a person

def page():
    s = st.Settings()
    s.first_run = False
    s.tour_done = True
    s.check_updates = False
    s.language = LANG
    s.appearance = LOOK
    s.known_tools = sorted(t.id for t in all_tools.ready())
    s.tools_off = ["startup"]
    s.save()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from corefold import i18n
    i18n.set_language(LANG)
    qapp = QApplication.instance() or QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    if i18n.is_rtl():
        qapp.setLayoutDirection(Qt.RightToLeft)
    from corefold import app as A
    A.setup_logging()

    def hook(t, e, tb):
        text = "".join(traceback.format_exception(t, e, tb))
        errors.append(text)
        print("ERROR " + text, flush=True)
    sys.excepthook = hook

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

    c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
    c.show_window("Home")
    w = c.window
    w.resize(1100, 740)
    settle(0.6)
    check("Startup" not in w.nav_rows, "removed in the Store: not in the sidebar")
    check("corefold.startuppage" not in sys.modules, "removed in the Store: its page isn't loaded at all")
    c.set_tool("startup", True)
    settle(0.6)
    w = c.window
    check("Startup" in w.nav_rows, "added from the Store: in the sidebar")
    w.resize(1100, 740)
    w.open_page("Startup")
    pg = w.startup_page
    check(pg is not None, "the page opens")
    check(until(lambda: not pg.loading and pg.rows, 15), f"the list comes up ({len(pg.rows)} rows)")
    settle(0.5)
    w.grab().save(str(OUT / f"startup-{LANG}-{LOOK}.png"))
    row = next((r for r in pg.rows if r.entry.value == "CfStartupTest"), None)
    check(row is not None and row.switch.isChecked(), "the made-up program is there, switched on")
    if row is None:
        return
    check(not row.isHidden() and row.switch.isVisible(), "its switch can be seen")
    QTest.mouseClick(row.switch, Qt.LeftButton, Qt.NoModifier, row.switch.rect().center())
    check(until(lambda: row.switch.isEnabled() and not pg.busy, 15), "clicking its switch: done")
    note = get(winreg.HKEY_CURRENT_USER, startup.APPROVED + r"\Run", "CfStartupTest")
    check(not row.switch.isChecked() and isinstance(note, bytes) and note[0] == 3,
          "clicking its switch switched it off (the same note as Task Manager)")
    check(get(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupTest") is not None, "and deleted nothing")
    check(pg.problem.isHidden(), "no problem shown")
    settle(0.3)
    QTest.mouseClick(pg.filter_buttons["off"], Qt.LeftButton)
    settle(0.4)
    shown = [r for r in pg.rows if not r.isHidden()]
    check(shown and all(not r.entry.on for r in shown) and row in shown,
          f"\"Switched off\" shows only the ones that are off ({len(shown)})")
    w.grab().save(str(OUT / f"startup-off-{LANG}-{LOOK}.png"))
    QTest.mouseClick(pg.filter_buttons[""], Qt.LeftButton)
    QTest.mouseClick(row.switch, Qt.LeftButton, Qt.NoModifier, row.switch.rect().center())
    check(until(lambda: row.switch.isEnabled() and not pg.busy, 15), "clicking it again: done")
    note = get(winreg.HKEY_CURRENT_USER, startup.APPROVED + r"\Run", "CfStartupTest")
    check(row.switch.isChecked() and note == startup.ON_BYTES, "and it's on again")
    QTest.keyClicks(pg.search, "cfstartup")
    settle(0.5)
    shown = [r for r in pg.rows if not r.isHidden()]
    check(shown and all("cfstartup" in (r.entry.value + r.entry.name).lower() for r in shown),
          f"searching finds only the made-up ones ({len(shown)})")
    pg.search.clear()
    QTest.keyClicks(pg.search, "zzzz-nothing")
    settle(0.4)
    check(not pg.empty.isHidden() and all(r.isHidden() for r in pg.rows), "searching for nothing says so")
    w.grab().save(str(OUT / f"startup-nothing-{LANG}-{LOOK}.png"))
    pg.search.clear()
    settle(0.3)
    # something adds itself while the page is open: "Look again" shows it as New
    put(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupLater", f'"{NOTEPAD}" /later')
    QTest.mouseClick(pg.refresh_btn, Qt.LeftButton)
    check(until(lambda: not pg.loading and any(r.entry.value == "CfStartupLater" for r in pg.rows), 15),
          "Look again: the new one is listed")
    later = next((r for r in pg.rows if r.entry.value == "CfStartupLater"), None)
    check(later is not None and later.entry.new and not pg.filter_buttons["new"].isHidden(),
          "it's marked New, and the New filter is there")
    gone = next((r for r in pg.rows if r.entry.value == "CfStartupGone"), None)
    check(gone is not None and gone.entry.missing, "the one whose program is gone says so")
    settle(0.3)
    w.grab().save(str(OUT / f"startup-new-{LANG}-{LOOK}.png"))
    # a change Windows refuses: the switch goes back and the page says why
    real = startup._write_note

    def refuse(*_a):
        raise PermissionError(5, "Access is denied")
    real_admin = startup._write_note_as_admin
    startup._write_note = refuse
    startup._write_note_as_admin = lambda *a: False  # (as if the person said no to Windows' prompt)
    try:
        QTest.mouseClick(row.switch, Qt.LeftButton, Qt.NoModifier, row.switch.rect().center())
        check(until(lambda: row.switch.isEnabled() and not pg.busy, 15), "a refused change: done")
        check(row.switch.isChecked() and not pg.problem.isHidden(),
              "a refused change: the switch goes back on and the page says Windows didn't allow it")
        w.grab().save(str(OUT / f"startup-refused-{LANG}-{LOOK}.png"))
    finally:
        startup._write_note = real
        startup._write_note_as_admin = real_admin
    check(get(winreg.HKEY_CURRENT_USER, startup.APPROVED + r"\Run", "CfStartupTest") == startup.ON_BYTES,
          "and nothing changed")
    # going away and coming back reads the list again, without losing what was New
    w.open_page("Home")
    settle(0.3)
    w.open_page("Startup")
    check(until(lambda: not pg.loading, 15), "back on the page: read again")
    later = next((r for r in pg.rows if r.entry.value == "CfStartupLater"), None)
    check(later is not None and later.entry.new, "what was New this time is still New")
    for small in ((760, 520), (1500, 900)):
        w.resize(*small)
        settle(0.4)
        sticking = [r for r in pg.rows if not r.isHidden() and r.switch.mapTo(w, r.switch.rect().topRight()).x() > w.width()]
        check(not sticking, f"at {small[0]}×{small[1]} every switch is inside the window")
        w.grab().save(str(OUT / f"startup-{small[0]}-{LANG}-{LOOK}.png"))
    c.set_tool("startup", False)
    settle(0.5)
    check("Startup" not in c.window.nav_rows, "removed again: gone from the sidebar")
    check(not errors, f"no errors behind any click ({len(errors)})")


def tidy():
    for name in TEST:
        drop(winreg.HKEY_CURRENT_USER, startup.RUN, name)
        drop(winreg.HKEY_CURRENT_USER, startup.APPROVED + r"\Run", name)
    drop(winreg.HKEY_CURRENT_USER, startup.APPROVED + r"\StartupFolder", "CfStartupNotes.lnk")
    if startup.is_admin():
        for name, view in (("CfStartupWide", HKLM_64), ("CfStartupWide32", HKLM_32)):
            drop(winreg.HKEY_LOCAL_MACHINE, startup.RUN, name, view)
        drop(winreg.HKEY_LOCAL_MACHINE, startup.APPROVED + r"\Run", "CfStartupWide", HKLM_64)
        drop(winreg.HKEY_LOCAL_MACHINE, startup.APPROVED + r"\Run32", "CfStartupWide32", HKLM_64)
    after = snapshot()
    changed = [k for k in set(BEFORE) | set(after) if BEFORE.get(k) != after.get(k)]
    check(not changed, f"every other startup entry and note on the PC is exactly as it was {changed[:5]}")


for name, fn in (("listing", listing), ("switching", switching), ("whole PC", whole_pc), ("new", new_since),
                 ("own entry", own_entry), ("page", page)):
    step(name, fn)
step("tidy", tidy)
(OUT / f"startuptest-{LANG}.txt").write_text("\n".join(problems) or "none", "utf-8")
print(f"startuptest: {len(problems)} problem(s)", flush=True)
faulthandler.disable()
os._exit(1 if problems else 0)
