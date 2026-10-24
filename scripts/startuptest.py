"""Corefold Startup, tried for real on a Windows PC: made-up programs are added to the places Windows
starts things from, then listed, switched off and on (by code and by clicking the page), searched and
filtered, and everything is checked against what Task Manager writes. Store apps' startup tasks are
tried with a made-up package, and scheduled tasks with a made-up task. Every other startup entry on the
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
TEST = ["CfStartupTest", "CfStartupGone", "CfStartupLater", "CfStartupHost", "CfStartupWide", "CfStartupWide32",
        "CfStartupLeft", "CfStartupAway", 'Cf"Startup\\Odd', "CfStartupWideLeft", "CfStartupWideLeft32"]
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


AWAY_DRIVE = next(f"{d}:" for d in "QRSTUVWXYZ" if not os.path.isdir(f"{d}:\\"))


def find(entries, value, where=None):
    return next((e for e in entries if e.value == value and (where is None or e.where == where)), None)


BEFORE = snapshot()
print(f"admin: {startup.is_admin()}, 64-bit Windows: {startup._is_64bit()}, "
      f"{len(BEFORE)} other startup entries and notes on this PC", flush=True)


# ----------------------------------------------------------------------------- the list and the switches

def listing():
    put(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupTest", f'"{NOTEPAD}" /startup')
    put(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupGone", f'"{GONE}" --tray')
    put(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupAway", f'"{AWAY_DRIVE}\\Tools\\Away.exe"')
    put(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupHost", "rundll32.exe shell32.dll,Control_RunDLL",
        winreg.REG_EXPAND_SZ)
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
    away = find(entries, "CfStartupAway")
    check(away is not None and not away.missing,
          f"a program on a drive that isn't plugged in ({AWAY_DRIVE}) isn't called gone (it may come back)")
    h = find(entries, "CfStartupHost")
    check(h is not None and h.name == "CfStartupHost" and not h.publisher and not h.missing
          and h.exe.lower().endswith(r"system32\rundll32.exe"),
          f"a program started through rundll32 keeps its own entry name ({h.name if h else None!r}, {h.exe if h else ''})")
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


def leftovers():
    """Lines whose program was removed: listed apart, and removed only when asked, with a copy kept."""
    put(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupLeft", f'"{GONE}" /left')
    put(winreg.HKEY_CURRENT_USER, startup.APPROVED + r"\Run", "CfStartupLeft", startup.off_note(time.time() - 9e6),
        winreg.REG_BINARY)
    gone_target = TMP / "gone-soon" / "LeftBehind.exe"
    gone_target.parent.mkdir(parents=True, exist_ok=True)
    gone_target.write_bytes(b"MZ")
    folder = startup.folders()["User-Folder"]
    make_shortcut(folder / "CfStartupLeft.lnk", gone_target)
    gone_target.unlink()
    entries = startup.list_entries()
    mine = [e for e in startup.leftovers(entries) if e.value.startswith("CfStartup")]
    names = sorted(e.value for e in mine)
    check(names == ["CfStartupGone", "CfStartupLeft", "CfStartupLeft.lnk"],
          f"the made-up leftovers are found, and only them ({names})")
    check(all(not e.own for e in startup.leftovers(entries)), "Corefold's own line is never a leftover")
    test_run = get(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupTest")
    left = [e for e in mine if e.value != "CfStartupGone"]
    removed = startup.remove_leftovers(left)
    check(sorted(e.value for e in removed) == ["CfStartupLeft", "CfStartupLeft.lnk"], "both are removed")
    check(get(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupLeft") is None, "the line is gone from the Run key")
    check(get(winreg.HKEY_CURRENT_USER, startup.APPROVED + r"\Run", "CfStartupLeft") is None, "and its on/off note too")
    check(not (folder / "CfStartupLeft.lnk").exists(), "the shortcut is gone from the Startup folder (to the Recycle Bin)")
    check(get(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupGone") is not None,
          "a leftover that wasn't asked about is left alone")
    check(get(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupTest") == test_run, "a working program is left alone")
    import json
    try:
        kept = json.loads((st.CONFIG_DIR / startup.REMOVED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = []
    copy = next((k for k in kept if k.get("value") == "CfStartupLeft"), None)
    check(copy is not None and str(GONE) in copy["command"] and copy["note"].startswith("03"),
          "a copy of the line and its note was kept first")
    # a program that comes back in the meantime isn't removed
    put(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupLeft", f'"{NOTEPAD}"')
    e = find(startup.list_entries(), "CfStartupLeft")
    e.missing, e.exe = True, str(NOTEPAD)
    check(not startup.remove_leftovers([e]) and get(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupLeft"),
          "a line whose program is there again isn't removed")
    drop(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupLeft")
    # the way it's done without permission: one .reg file, imported by reg.exe as administrator
    odd = 'Cf"Startup\\Odd'
    put(winreg.HKEY_CURRENT_USER, startup.RUN, odd, f'"{GONE}"')
    key = startup._reg_key("HKCU", startup.RUN, 0)
    ok = startup._delete_as_admin([(key, odd)])
    check(ok and get(winreg.HKEY_CURRENT_USER, startup.RUN, odd) is None,
          "through Windows' administrator prompt (reg import), even a name with \" and \\ in it")
    if startup.is_admin():
        put(winreg.HKEY_LOCAL_MACHINE, startup.RUN, "CfStartupWideLeft", f'"{GONE}"', view=HKLM_64)
        put(winreg.HKEY_LOCAL_MACHINE, startup.RUN, "CfStartupWideLeft32", f'"{GONE}"', view=HKLM_32)
        ok = startup._delete_as_admin([(startup._reg_key("HKLM", startup.RUN, 64), "CfStartupWideLeft"),
                                       (startup._reg_key("HKLM", startup.RUN, 32), "CfStartupWideLeft32")])
        check(ok and get(winreg.HKEY_LOCAL_MACHINE, startup.RUN, "CfStartupWideLeft", HKLM_64) is None
              and get(winreg.HKEY_LOCAL_MACHINE, startup.RUN, "CfStartupWideLeft32", HKLM_32) is None,
              "PC-wide leftovers (64 and 32-bit) are removed with one prompt")
        put(winreg.HKEY_LOCAL_MACHINE, startup.RUN, "CfStartupWideLeft", f'"{GONE}"', view=HKLM_64)
        w = find(startup.list_entries(), "CfStartupWideLeft", "HKLM-Run")
        check(w is not None and w.missing and [x.value for x in startup.remove_leftovers([w])] == ["CfStartupWideLeft"],
              "and through remove_leftovers itself")


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
    check(not w.shows("Startup"), "switched off: not shown")
    check("corefold.startuppage" not in sys.modules, "switched off: its page isn't loaded at all")
    c.set_tool("startup", True)
    settle(0.6)
    w = c.window
    check(w.shows("Startup"), "switched on: shown in its section")
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
    settle(0.4)  # (the parts are laid out again, as before a person's next click)
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
    check(gone is not None and gone.parent() is pg.groups["gone"] and not pg.heads["gone"].isHidden()
          and pg.clean_btn.isVisible(), "it's in its own part, \"Left behind by removed programs\", with Remove")
    check(row_in(pg, "CfStartupTest", "on"), "a working program is in the part that starts with Windows")
    row = next((r for r in pg.rows if r.entry.value == "CfStartupTest"), None)  # (the list was made again)
    check(row is not None and row.switch.isChecked(), "after Look again, the made-up program is still on")
    if row is None:
        return
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
    # the leftovers part, then removing them (only the made-up ones: the runner's own stay)
    w.resize(1100, 740)
    # one switched off long ago (as on a friend's PC): it's in "Switched off", with its date
    put(winreg.HKEY_CURRENT_USER, startup.APPROVED + r"\Run", "CfStartupHost", startup.off_note(time.time() - 3e7),
        winreg.REG_BINARY)
    QTest.mouseClick(pg.refresh_btn, Qt.LeftButton)
    check(until(lambda: not pg.loading and row_in(pg, "CfStartupHost", "off"), 15),
          "one switched off long ago is in the \"Switched off\" part")
    gone = next((r for r in pg.rows if r.entry.value == "CfStartupGone"), None)
    settle(0.3)
    if gone is not None:
        pg.scroll.ensureWidgetVisible(gone)
        settle(0.4)
        w.grab().save(str(OUT / f"startup-leftovers-{LANG}-{LOOK}.png"))
    real_left = startup.leftovers
    startup.leftovers = lambda es: [e for e in real_left(es) if e.value.startswith("CfStartup")]
    try:
        pg.remove_leftovers(ask=False)
        check(until(lambda: not pg.busy and not pg.loading
                    and not any(r.entry.value == "CfStartupGone" for r in pg.rows), 20),
              "Remove leftovers: the made-up leftover is gone from the list")
        check(get(winreg.HKEY_CURRENT_USER, startup.RUN, "CfStartupGone") is None, "and from Windows")
        check(pg.problem.isHidden(), "no problem shown")
    finally:
        startup.leftovers = real_left
    for small in ((760, 520), (1500, 900)):
        w.resize(*small)
        settle(0.4)
        sticking = [r for r in pg.rows if not r.isHidden() and r.switch.mapTo(w, r.switch.rect().topRight()).x() > w.width()]
        check(not sticking, f"at {small[0]}×{small[1]} every switch is inside the window")
        w.grab().save(str(OUT / f"startup-{small[0]}-{LANG}-{LOOK}.png"))
    c.set_tool("startup", False)
    settle(0.5)
    check(not c.window.shows("Startup"), "switched off again: not shown")
    check(not errors, f"no errors behind any click ({len(errors)})")


def row_in(pg, value, part):
    r = next((r for r in pg.rows if r.entry.value == value), None)
    return r is not None and r.parent() is pg.groups[part]


def store_apps():
    """Store apps start with Windows from a "startup task" in their package: made-up packages in a test key
    are listed, switched off and on (the number Task Manager writes), and one the organisation decides
    can't be switched. Then this PC's real Store apps are read the same way."""
    real = startup.list_entries()
    print(f"this PC's Store apps that can start with Windows: "
          f"{[(e.name, e.on) for e in real if e.where == 'Store-App']}", flush=True)
    saved = startup.STORE_PACKAGES, startup.STORE_STATES
    startup.STORE_PACKAGES, startup.STORE_STATES = STORE_TEST + r"\Packages", STORE_TEST + r"\SystemAppData"
    try:
        root = TMP / "WindowsApps" / "CfStoreTest_1.0.0.0_x64__cf0test0id"
        (root / "Assets").mkdir(parents=True)
        (root / "Assets" / "StoreLogo.scale-100.png").write_bytes(
            bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                          "1f15c4890000000d49444154789c6360f8cfc0f01f0005000201e2213"
                          "0bc0000000049454e44ae426082"))
        (root / "AppxManifest.xml").write_text("""<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap5="http://schemas.microsoft.com/appx/manifest/uap/windows10/5">
  <Identity Name="CfStoreTest" Publisher="CN=Test" Version="1.0.0.0" ProcessorArchitecture="x64"/>
  <Properties><DisplayName>Cf Store Test</DisplayName><PublisherDisplayName>Cf Test Maker</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo></Properties>
  <Applications><Application Id="App" Executable="CfStore.exe"><Extensions>
    <uap5:Extension Category="windows.startupTask" Executable="CfStore.exe" EntryPoint="Windows.FullTrustApplication">
      <uap5:StartupTask TaskId="CfAutoStart" Enabled="true" DisplayName="Cf Store Helper"/></uap5:Extension>
    <uap5:Extension Category="windows.startupTask" Executable="CfStore.exe" EntryPoint="Windows.FullTrustApplication">
      <uap5:StartupTask TaskId="CfAskFirst" Enabled="false"/></uap5:Extension>
    <uap5:Extension Category="windows.startupTask" Executable="CfStore.exe" EntryPoint="Windows.FullTrustApplication">
      <uap5:StartupTask TaskId="CfByRules" Enabled="true"/></uap5:Extension>
  </Extensions></Application></Applications>
</Package>""", "utf-8")
        key = startup.STORE_PACKAGES + "\\" + root.name
        put(winreg.HKEY_CURRENT_USER, key, "PackageRootFolder", str(root))
        put(winreg.HKEY_CURRENT_USER, key, "DisplayName", "Cf Store Test")
        family = "CfStoreTest_cf0test0id"
        check(startup.family_of(root.name) == family, f"a package's family name ({startup.family_of(root.name)})")
        put(winreg.HKEY_CURRENT_USER, startup.STORE_STATES + rf"\{family}\CfByRules", "State", 3, winreg.REG_DWORD)
        found = [e for e in startup.list_entries() if e.where == "Store-App"]
        auto = find(found, "CfAutoStart")
        ask = find(found, "CfAskFirst")
        rules = find(found, "CfByRules")
        if not check(auto and ask and rules, f"the Store app's three startup tasks are listed "
                                             f"{[(e.name, e.value) for e in found]}"):
            return
        check(auto.on and not ask.on and not rules.on, "on as the app was made (Enabled), off when it asks first, "
                                                       "and off when the organisation's rules say so")
        check(auto.name == "Cf Store Helper" and ask.name == "Cf Store Test" and auto.publisher == "Cf Test Maker",
              f"named as the task or the app says, with its maker ({auto.name}, {ask.name}, {auto.publisher})")
        check(auto.extra.get("icon", "").endswith("StoreLogo.scale-100.png"), f"its logo ({auto.extra.get('icon')})")
        check(not auto.admin and rules.locked and not auto.locked, "this user's own: no administrator, and the one "
                                                                    "under rules can't be switched")
        state = lambda task: get(winreg.HKEY_CURRENT_USER, startup.STORE_STATES + rf"\{family}\{task}", "State")  # noqa: E731
        check(startup.set_on(auto, False) and not auto.on and state("CfAutoStart") == 1,
              f"switched off the way Task Manager does it (State {state('CfAutoStart')})")
        again = find([e for e in startup.list_entries() if e.where == "Store-App"], "CfAutoStart")
        check(again is not None and not again.on, "…and it's still off when the list is read again")
        check(startup.set_on(auto, True) and auto.on and state("CfAutoStart") == 2,
              f"switched back on (State {state('CfAutoStart')})")
        check(startup.set_on(ask, True) and state("CfAskFirst") == 2, "one that asks first can be switched on too")
        check(not startup.set_on(rules, True) and state("CfByRules") == 3, "the organisation's choice is left alone")
    finally:
        startup.STORE_PACKAGES, startup.STORE_STATES = saved
        try:
            subprocess.run([str(SYSTEM32 / "reg.exe"), "delete", "HKCU\\" + STORE_TEST, "/f"], capture_output=True,
                           timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass


STORE_TEST = r"Software\CorefoldStartupTest"
TASK_TEST = "\\CfStartupTestTask"


def schtasks(*args) -> subprocess.CompletedProcess:
    return subprocess.run([str(SYSTEM32 / "schtasks.exe"), *args], capture_output=True, timeout=60)


def scheduled_tasks():
    """Programs started by a scheduled task when someone signs in (what Autoruns shows): a made-up task is
    listed with its program's name, switched off and on with Windows' own schtasks, and removed again."""
    real = startup.task_entries()
    print(f"this PC's scheduled tasks that start at sign-in: {[(e.name, e.value, e.on) for e in real][:12]}",
          flush=True)
    check(not any(e.value.startswith("\\Microsoft\\") for e in real), "Windows' own tasks aren't listed")
    made = schtasks("/Create", "/SC", "ONLOGON", "/TN", TASK_TEST, "/TR", f'"{NOTEPAD}" /cf', "/F")
    if not check(made.returncode == 0, f"a test task was made ({made.stderr.decode('mbcs', 'replace').strip()})"):
        return
    try:
        task = find(startup.list_entries(), TASK_TEST, "Task")
        if not check(task is not None, "the task is in the list"):
            return
        check(task.on and task.exe.lower() == str(NOTEPAD).lower() and "/cf" in task.command,
              f"switched on, with the program it runs ({task.on}, {task.command})")
        check(task.name != "CfStartupTestTask", f"named after its program, not the task ({task.name})")
        check(task.extra.get("mine") and not task.admin, "this user's own task needs no administrator")
        check(not task.missing and task not in startup.leftovers([task]), "its program is there")
        check(startup.set_on(task, False) and not task.on and startup._task_enabled(TASK_TEST) is False,
              "switched off (Task Scheduler says Disabled)")
        again = find(startup.list_entries(), TASK_TEST, "Task")
        check(again is not None and not again.on, "…and it's still off when the list is read again")
        check(startup.set_on(task, True) and task.on and startup._task_enabled(TASK_TEST) is True,
              "switched back on")
    finally:
        schtasks("/Delete", "/TN", TASK_TEST, "/F")
    check(find(startup.list_entries(), TASK_TEST, "Task") is None, "gone from the list once deleted")


def tidy():
    for name in TEST:
        drop(winreg.HKEY_CURRENT_USER, startup.RUN, name)
        drop(winreg.HKEY_CURRENT_USER, startup.APPROVED + r"\Run", name)
    drop(winreg.HKEY_CURRENT_USER, startup.APPROVED + r"\StartupFolder", "CfStartupNotes.lnk")
    for link in ("CfStartupNotes.lnk", "CfStartupLeft.lnk"):
        try:
            (startup.folders()["User-Folder"] / link).unlink()
        except OSError:
            pass
    if startup.is_admin():
        for name, view in (("CfStartupWide", HKLM_64), ("CfStartupWide32", HKLM_32), ("CfStartupWideLeft", HKLM_64),
                           ("CfStartupWideLeft32", HKLM_32)):
            drop(winreg.HKEY_LOCAL_MACHINE, startup.RUN, name, view)
        drop(winreg.HKEY_LOCAL_MACHINE, startup.APPROVED + r"\Run", "CfStartupWide", HKLM_64)
        drop(winreg.HKEY_LOCAL_MACHINE, startup.APPROVED + r"\Run32", "CfStartupWide32", HKLM_64)
    after = snapshot()
    changed = [k for k in set(BEFORE) | set(after) if BEFORE.get(k) != after.get(k)]
    check(not changed, f"every other startup entry and note on the PC is exactly as it was {changed[:5]}")


for name, fn in (("listing", listing), ("switching", switching), ("whole PC", whole_pc), ("new", new_since),
                 ("own entry", own_entry), ("leftovers", leftovers), ("Store apps", store_apps), ("scheduled tasks", scheduled_tasks),
                 ("page", page)):
    step(name, fn)
step("tidy", tidy)
(OUT / f"startuptest-{LANG}.txt").write_text("\n".join(problems) or "none", "utf-8")
print(f"startuptest: {len(problems)} problem(s)", flush=True)
faulthandler.disable()
os._exit(1 if problems else 0)
