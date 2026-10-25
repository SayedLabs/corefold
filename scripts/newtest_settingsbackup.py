"""Checks settings in a file ("Save my settings…" / "Bring back settings…"), the copies kept before updates,
"Report a problem" and Settings → Shortcuts, on a real Windows PC.

Run from the repository's folder: python scripts/newtest_settingsbackup.py
Pictures of the pages go to out/newtest_settingsbackup/. Everything else happens in a temporary folder (the
user's real settings, Desktop and registry are left alone). Prints one line per check and
"newtest_settingsbackup: N problem(s)".

(The window parts run in child copies of this script, one for each language and look:
  python scripts/newtest_settingsbackup.py --ui en dark
  python scripts/newtest_settingsbackup.py --ui ar light --taken
  python scripts/newtest_settingsbackup.py --ui fr dark)"""
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "newtest_settingsbackup"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-settingsbackup-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(280, exit=True)  # stuck somewhere: say where, and stop

UI = "--ui" in sys.argv
problems = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)
    return ok


def step(name, fn):
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()


# ============================================================================ the parts without a window

def logic():
    from corefold import settings as st
    from corefold import settings_backup as sb
    from corefold import tools as all_tools
    cfg = st.CONFIG_DIR
    cfg.mkdir(parents=True, exist_ok=True)

    def fresh(**values):
        s = st.Settings()
        s.first_run = False
        for k, v in values.items():
            setattr(s, k, v)
        s.save()
        return st.Settings.load()

    ready = [t.id for t in all_tools.ready()]
    not_ready = [t.id for t in all_tools.coming()]

    def roundtrip():
        s = fresh(known_tools=sorted(ready))
        first = dict(language="fr", accent="grape", jpg_quality=77, clip_max=1000, quick_menu_key="ctrl+alt+j",
                     cap_shortcut="win+shift+a", animations=False, tools_off=sorted(["space", "player"]),
                     window_geometry="AAAA", last_page="About")
        for k, v in first.items():
            setattr(s, k, v)
        s.save()
        path = TMP / sb.file_name()
        sb.export(s, path, startup=True, version="9.9.9")
        check(path.name.startswith("Corefold settings ") and path.suffix == ".corefold",
              f"the file is named like {path.name!r}")
        data = json.loads(path.read_text("utf-8"))
        check(data.get("format") == sb.FORMAT and data.get("format_version") == 1 and data.get("app_version") == "9.9.9"
              and isinstance(data.get("saved"), str), "the file says what it is, its format version, app version, date")
        check("window_geometry" not in data["settings"] and "last_page" not in data["settings"]
              and "recent_views" not in data["settings"] and "last_action" not in data["settings"],
              "window size, last page, recent files and 'Do it again' aren't in the file")
        check(data["settings"].get("tools_off") == sorted(["space", "player"]), "the Store's choices are in the file")
        # change them all again
        second = dict(language="es", accent="berry", jpg_quality=60, clip_max=100, quick_menu_key="ctrl+alt+k",
                      cap_shortcut="printscreen", animations=True, tools_off=["capture"], window_geometry="BBBB",
                      last_page="Home")
        for k, v in second.items():
            setattr(s, k, v)
        s.save()
        saved = sb.read(path)
        check(saved.app_version == "9.9.9" and saved.saved is not None and saved.startup is True,
              "reading it back gives its version, date and start-with-Windows choice")
        new = sb.apply(s, saved)
        for k in ("language", "accent", "jpg_quality", "clip_max", "quick_menu_key", "cap_shortcut", "animations"):
            check(getattr(new, k) == first[k], f"brought back: {k} = {getattr(new, k)!r}")
        check(new.tools_off == sorted(["space", "player"]), f"brought back: the Store's choices ({new.tools_off})")
        check(new.window_geometry == "BBBB" and new.last_page == "Home",
              "the window's size and last page stay as they are now")

    def bad_files():
        junk = TMP / "junk.corefold"
        junk.write_bytes(os.urandom(300))
        for name, content, want in (
                ("random bytes", None, "This isn't a Corefold settings file."),
                ("a list", "[1, 2, 3]", "This isn't a Corefold settings file."),
                ("another program's JSON", '{"format": "something-else", "settings": {}}',
                 "This isn't a Corefold settings file."),
                ("a newer format", json.dumps({"format": sb.FORMAT, "format_version": 99, "settings": {}}),
                 "These settings were saved by a newer Corefold. Update Corefold first, then try again."),
                ("settings that aren't a dict", json.dumps({"format": sb.FORMAT, "format_version": 1,
                                                            "settings": [1]}), "This isn't a Corefold settings file.")):
            if content is not None:
                junk.write_text(content, "utf-8")
            try:
                sb.read(junk)
                check(False, f"{name} is refused")
            except sb.BadFile as e:
                check(str(e) == want, f"{name} is refused: {e}")
        try:
            sb.read(TMP / "not there.corefold")
            check(False, "a missing file is refused")
        except sb.BadFile as e:
            check(str(e) == "Couldn't open this file.", f"a missing file is refused: {e}")
        # wrong kinds of values are dropped the same way as at start
        junk.write_text(json.dumps({"format": sb.FORMAT, "format_version": 1, "settings": {
            "jpg_quality": "high", "language": "klingon", "volume": 900, "accent": 5, "clip_max": 500}}), "utf-8")
        s = fresh()
        new = sb.apply(s, sb.read(junk))
        check(new.jpg_quality == 90 and new.language == "system" and new.volume == 100 and new.accent == "core",
              f"values of the wrong kind are dropped or kept in range ({new.jpg_quality}, {new.language}, "
              f"{new.volume}, {new.accent})")
        # a plain settings.json (like a copy kept before an update) can be brought back too
        plain = TMP / "settings-1.0-2026-01-01.json"
        plain.write_text(json.dumps({"language": "tr", "accent": "lime", "jpg_quality": 80, "volume": 10,
                                     "sound": "corefold"}), "utf-8")
        new = sb.apply(s, sb.read(plain))
        check(new.language == "tr" and new.accent == "lime", "a plain settings.json is accepted too")
        # quick links open programs: odd ones are dropped, and the question lists the others
        notepad = "C:\\Windows\\notepad.exe"
        junk.write_text(json.dumps({"format": sb.FORMAT, "format_version": 1, "settings": {"quick_links": [
            {"id": "a", "name": "Notepad", "target": notepad},
            {"id": "b", "name": "Number", "target": 12},
            {"id": "c", "name": "Huge", "target": "C:\\" + "x" * 3000},
            {"id": "d", "name": "No target"}, "not a link"]}}), "utf-8")
        saved = sb.read(junk)
        check([x.get("id") for x in saved.settings["quick_links"]] == ["a"],
              f"quick links whose target isn't plain text, or is huge, are dropped ({saved.settings['quick_links']})")
        check(sb.apply(s, saved).quick_links == [{"id": "a", "name": "Notepad", "target": notepad}],
              "…and the good one is brought back")
        from corefold import settingscard
        note = settingscard.links_note(saved)
        check(f"Notepad ({notepad})" in note and "(1)" in note,
              f"the question says which programs the links open: {note.strip()!r}")
        check(settingscard.links_note(sb.read(plain)) == "", "…and says nothing when there are none")

    def tools():
        s = fresh(known_tools=sorted(ready), tools_off=[])
        f = TMP / "tools.corefold"
        # a file that says "every tool on", including ones that aren't ready, and knows nothing of Capture
        s2 = fresh(known_tools=sorted(set(ready) - {"capture"} | set(not_ready)), tools_off=[])
        sb.export(s2, f, version="1.0")
        s.tools_off = ["capture"]  # off here now
        new = sb.apply(s, sb.read(f))
        check(not any(all_tools.is_on(new, t) for t in not_ready), "tools that aren't ready are never switched on")
        check("capture" in new.tools_off, "a tool newer than the file keeps its state (Capture stays off)")
        check(set(new.known_tools) >= set(ready), "tools known here stay known")

    def recovery():
        for p in (cfg / sb.FOLDER).glob("*") if (cfg / sb.FOLDER).is_dir() else []:
            p.unlink()
        versions = ["1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.5.0", "1.6.0", "1.7.0"]
        for i, v in enumerate(versions):
            fresh(jpg_quality=60 + i)
            got = sb.keep_copy(v)
            check(got is not None and got.exists(), f"a copy is kept before updating from {v}")
            past = time.time() - (len(versions) - i) * 60
            os.utime(got, (past, past))  # (each one a minute older than the next)
        copies = sb.backups()
        check(len(copies) == 5, f"only the newest 5 copies are kept ({len(copies)})")
        check(copies and "1.7.0" in copies[0].name and not any("1.1.0" in p.name or "1.2.0" in p.name for p in copies),
              f"the oldest copies went first ({[p.name for p in copies]})")
        # the function the updater calls before running the installer makes one too
        from corefold import updater
        started = []
        real = updater.subprocess.Popen
        updater.subprocess.Popen = lambda *a, **k: started.append(a)
        try:
            fresh(jpg_quality=99)
            updater.run_installer(TMP / "Corefold-Setup-9.9.9.exe")
        finally:
            updater.subprocess.Popen = real
        copies = sb.backups()
        check(started and len(copies) == 5 and json.loads(copies[0].read_text("utf-8"))["jpg_quality"] == 99,
              "installing an update keeps a copy of the settings first (still 5 kept)")
        # settings.json damaged at start: kept aside, and the newest copy is used
        garbage = '{"language": "fr", "jpg_quality": 7'  # cut off half-way
        (cfg / "settings.json").write_text(garbage, "utf-8")
        (cfg / "settings.broken.json").unlink(missing_ok=True)
        sb.RECOVERED = None
        s = st.Settings.load()
        check(s.jpg_quality == 99, f"a damaged settings.json: the newest copy is used (jpg_quality {s.jpg_quality})")
        broken = cfg / "settings.broken.json"
        check(broken.exists() and broken.read_text("utf-8") == garbage, "…the damaged one is kept as "
              "settings.broken.json, as it was")
        check(sb.RECOVERED is not None and sb.RECOVERED == copies[0],
              "…and Corefold knows a copy was used (to say so)")
        s.save()
        check(json.loads((cfg / "settings.json").read_text("utf-8"))["jpg_quality"] == 99,
              "…and saving writes a good settings.json again")
        (cfg / "settings.json").unlink()
        s = st.Settings.load()
        check(s.jpg_quality == 99, "a missing settings.json: the newest copy is used")
        (cfg / "settings.json").write_text("[]", "utf-8")
        s = st.Settings.load()
        check(s.jpg_quality == 99 and broken.read_text("utf-8") == "[]",
              "settings.json that isn't settings at all: kept aside, and the copy is used")
        for p in sb.backups():
            p.unlink()
        (cfg / "settings.json").write_text("{nope", "utf-8")
        s = st.Settings.load()
        check(s.jpg_quality == 90, "damaged with no copy at all: the defaults")

    def report():
        from corefold import report as rp
        user = os.environ.get("USERNAME") or Path.home().name
        home = str(Path.home())
        st.LOG_DIR.mkdir(parents=True, exist_ok=True)
        (st.LOG_DIR / "corefold.log").write_text(
            f"2026-10-23 INFO starting\nopened {home}\\Pictures\\cat.png\nC:\\Users\\{user}\\Desktop\\a b.pdf\n"
            f"C:/Users/{user.upper()}/Downloads/x.zip\nsigned in as {user}\n"
            "C:\\Users\\Public\\Documents\\shared.txt\n", "utf-8")
        (st.LOG_DIR / "corefold.log.1").write_text(f"older {home}\n", "utf-8")
        (st.LOG_DIR / "player.json").write_text("{}", "utf-8")  # not a log: stays out
        (st.LOG_DIR / "Clipboard").mkdir(exist_ok=True)
        (st.LOG_DIR / "Clipboard" / "history.json").write_text("secret", "utf-8")
        s = fresh(output_folder=str(Path.home() / "Downloads" / "Corefold"),
                  recent_views=[f"{home}\\Pictures\\private.png"], quick_links=[
                      {"id": "a", "name": "Notes", "target": f"C:\\Users\\{user}\\Documents\\notes.txt"}])
        desk = TMP / "Desktop"
        desk.mkdir()
        path = rp.make(s, desk, ["1920 x 1080 at 100%"])
        check(path.parent == desk and path.name.startswith("Corefold report ") and path.suffix == ".zip",
              f"the report is made: {path.name}")
        with zipfile.ZipFile(path) as z:
            names = sorted(z.namelist())
            texts = {n: z.read(n).decode("utf-8") for n in names}
        check(names == ["logs/corefold.log", "logs/corefold.log.1", "settings.json", "system.txt"],
              f"it has the logs, the settings and system.txt, nothing else ({names})")
        leaked = [n for n, t in texts.items() if user.lower() in t.lower()]
        check(not leaked, f"the Windows user name ({user}) is in none of them ({leaked})")
        check("C:\\Users\\<you>\\Desktop" in texts["logs/corefold.log"] and "C:/Users/<you>/Downloads"
              in texts["logs/corefold.log"] and "Users\\\\<you>" in texts["settings.json"],
              "paths say C:\\Users\\<you> instead")
        check("C:\\Users\\Public\\Documents" in texts["logs/corefold.log"], "other folders under Users are kept")
        check("private.png" not in texts["settings.json"], "the names of pictures opened lately aren't in it")
        system = texts["system.txt"]
        print("      system.txt:\n        " + system.strip().replace("\n", "\n        "))
        check("Corefold" in system and ("Windows" in system or os.name != "nt") and "-bit" in system
              and "1920 x 1080 at 100%" in system and "Tools in use:" in system and "Language:" in system,
              "system.txt has the version, Windows, 64-bit, language, screens and tools")
        if os.name == "nt":
            check("build " in system and "64-bit" in system, "…with Windows' build number and 64-bit")
        again = rp.make(s, desk, [])
        check(again != path and again.exists(), f"a second report doesn't overwrite the first ({again.name})")
        where = rp.report_folder()
        check(where.is_dir(), f"the report goes on the Desktop (here: {where.name})")
        # GitHub's page, filled in with the version and Windows only
        url = rp.issue_url("SayedLabs/corefold", "9.9.9")
        q = parse_qs(urlsplit(url).query)
        check(url.startswith("https://github.com/SayedLabs/corefold/issues/new?"), "the report page is GitHub's")
        body, title = q.get("body", [""])[0], q.get("title", [""])[0]
        check("9.9.9" in title and "Corefold 9.9.9" in body and rp.windows_text() in body,
              "its title and text have the version and Windows")
        bad = [x for x in (user, home, "Users", "\\", "AppData", "Desktop\\", ":/") if x and x.lower() in
               (url + body).lower().replace("https://", "")]
        check(not bad, f"…and no path or user name ({bad})")
        check(" " not in url and "\n" not in url, "…and is a proper web address")

    step("bringing settings back", roundtrip)
    step("files that aren't settings", bad_files)
    step("the Store's tools", tools)
    step("copies kept before updates", recovery)
    step("report a problem", report)


# ============================================================================ the window (a child copy)

HELPER = r"""
import ctypes, sys, time
u = ctypes.windll.user32
ok = u.RegisterHotKey(None, 7, 0x2 | 0x4000, 0x2C)   # Ctrl + Print Screen, like another app would
print("ready" if ok else "failed", flush=True)
sys.stdin.readline()   # held until the test lets go
"""


def window(lang: str, look: str, taken: bool):
    from corefold import settings as st
    from corefold import tools as all_tools
    s = st.Settings()
    s.first_run = False
    s.tour_done = True
    s.check_updates = False
    s.language = lang
    s.appearance = look
    s.known_tools = sorted(t.id for t in all_tools.TOOLS)
    s.tools_off = []
    s.quick_told = True
    s.quick_menu = True
    s.quick_wheel = True
    s.watch_downloads = False
    s.context_menu = False
    s.save()

    helper = None
    if taken and os.name == "nt":
        helper = subprocess.Popen([sys.executable, "-c", HELPER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True)
        check(helper.stdout.readline().strip() == "ready", "a helper app takes Ctrl + Print Screen first")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox
    qapp = QApplication(sys.argv)
    from corefold import i18n
    i18n.set_language(lang)
    if i18n.is_rtl():
        qapp.setLayoutDirection(Qt.RightToLeft)
    from corefold import app as A
    from corefold import settingscard, shortcutspage
    tr = i18n.tr

    def settle(seconds=0.3):
        end = time.time() + seconds
        while time.time() < end:
            qapp.processEvents()
            time.sleep(0.01)

    def until(cond, seconds=8):
        end = time.time() + seconds
        while time.time() < end:
            qapp.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return False

    c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
    restarted = []
    c.restart = lambda page="General": restarted.append(page)
    try:
        check(until(lambda: c.clip is not None and c.capture is not None and c.quick is not None),
              "Clipboard, Capture and Quick access are running")
        c.show_window("Shortcuts")
        w = c.window
        w.resize(1100, 860)
        settle(0.8)
        check(w.current_page() == "Shortcuts", "Settings → Shortcuts opens")
        page = w.shortcuts_page
        page.refresh()
        states = {e.setting: shortcutspage.state(c, e) for e in shortcutspage.GLOBAL}
        print("      states:", states)
        texts = {e.setting: (k.text().strip("\u202a\u202c"), st_.text()) for e, k, st_, _ in page.rows}
        print("      shown:", texts)
        check(states["clip_shortcut"] == "working" and texts["clip_shortcut"][1] == tr("Working"),
              "the clipboard's shortcut: Working")
        check(states["quick_menu_key"] == "working" and states["quick_wheel_key"] == "working",
              "the quick menu's and wheel's shortcuts: Working")
        check(states["shortcut"] == "working" and texts["shortcut"][0] == tr("Shift"),
              "the Converter's drag key: Shift")
        if taken and os.name == "nt":
            check(states["cap_shortcut"] == "taken" and texts["cap_shortcut"][1] == tr("Another app is using it"),
                  "the screenshot shortcut another app took: Another app is using it")
        else:
            check(states["cap_shortcut"] == "working", "the screenshot shortcut: Working")
        check(texts["quick_menu_key"][0] == "Ctrl + Alt + Space", "shortcuts are written the way people read them")
        page.expand_all()
        settle(0.6)
        w.grab().save(str(OUT / f"shortcuts-{lang}-{look}.png"))
        bar = w.stack.currentWidget().verticalScrollBar()
        bar.setValue(bar.maximum())
        settle(0.4)
        w.grab().save(str(OUT / f"shortcuts-{lang}-{look}-2.png"))
        # the smallest window: the words wrap, nothing is cut off or sticks out
        size = w.size()
        w.resize(720, 460)
        settle(0.6)
        from PySide6.QtWidgets import QLabel, QPushButton
        area = w.stack.currentWidget()
        marks = "\u202a\u202c"
        cut = [lab.text()[:40] for lab in area.widget().findChildren(QLabel) if lab.isVisible() and lab.text()
               and ((not lab.wordWrap() and lab.fontMetrics().horizontalAdvance(lab.text().strip(marks))
                     > lab.contentsRect().width() + 3)
                    or (lab.wordWrap() and lab.heightForWidth(lab.width()) > lab.height() + 4))]
        cut += [b.text()[:30] for b in area.widget().findChildren(QPushButton) if b.isVisible() and b.text()
                and b.fontMetrics().horizontalAdvance(b.text()) > b.width() - 4]
        check(not cut and area.widget().width() <= area.viewport().width(),
              f"at 720 x 460 nothing on Shortcuts is cut off ({cut}, {area.widget().width()} of "
              f"{area.viewport().width()}px)")
        w.grab().save(str(OUT / f"shortcuts-{lang}-{look}-small.png"))
        w.resize(size)
        settle(0.3)
        # "Change" opens the setting
        by_setting = {e.setting: e for e in shortcutspage.GLOBAL}  # (by name: new lines can go anywhere)
        for entry, page_name in ((by_setting["clip_shortcut"], "ClipboardSettings"),
                                 (by_setting["quick_menu_key"], "Quick"), (by_setting["shortcut"], "ConvOptions"),
                                 (by_setting["peek_key"], "ViewerSettings")):
            w.open_page("Shortcuts")
            settle(0.2)
            page.change(entry)
            settle(0.5)
            check(w.current_page() == page_name, f"Change next to {entry.label!r} opens {page_name}")
        # a tool that isn't used: listed as not in use
        c.settings.tools_off = ["clipboard"]
        page.refresh()
        check(shortcutspage.state(c, shortcutspage.GLOBAL[1]) == "unused", "a tool that isn't used: Not in use")
        c.settings.tools_off = []
        page.refresh()
        # Ctrl + K finds it
        check(any(cmd.title == tr("Shortcuts") for cmd in w._commands()), "the search (Ctrl + K) finds Shortcuts")

        # the General page: save, change, bring back (the restart is only noted)
        w.open_page("General")
        settle(0.5)
        bar = w.stack.currentWidget().verticalScrollBar()
        bar.setValue(bar.maximum())  # (the new card is at the bottom)
        settle(0.3)
        w.grab().save(str(OUT / f"general-{lang}-{look}.png"))
        s = c.settings
        s.jpg_quality, s.accent, s.clip_max = 71, "ocean", 5000
        s.save()
        f = TMP / "mine.corefold"
        w.pending_startup = False  # (never touches this PC's start-with-Windows entry)
        got = settingscard.save_file(w, f)
        check(got == f and f.exists(), "Save my settings writes the file")
        s.jpg_quality, s.accent, s.clip_max = 55, "berry", 100
        geometry = s.window_geometry = "CCCC"
        w.update_save_bar()
        real_startup = st.set_startup
        st.set_startup = lambda on: restarted.append(f"startup {on}")
        try:
            check(settingscard.bring_back(w, f, ask=False), "Bring back settings reads it and uses it")
        finally:
            st.set_startup = real_startup
        on_disk = json.loads((st.CONFIG_DIR / "settings.json").read_text("utf-8"))
        check(on_disk["jpg_quality"] == 71 and on_disk["accent"] == "ocean" and on_disk["clip_max"] == 5000,
              "the settings on disk are the ones from the file")
        check(on_disk["window_geometry"] == geometry, "…but not the window's size")
        check(restarted and restarted[-1] == "General", f"…and Corefold restarts to use them ({restarted})")
        check(not w.is_dirty(), "nothing is left waiting for Save")
        # a file that isn't settings: nothing changes
        junk = TMP / "junk.corefold"
        junk.write_text("hello", "utf-8")
        before = (st.CONFIG_DIR / "settings.json").read_text("utf-8")
        check(not settingscard.bring_back(w, junk, ask=False)
              and (st.CONFIG_DIR / "settings.json").read_text("utf-8") == before, "a file that isn't settings changes "
              "nothing")
        # what the questions look like
        from corefold import settings_backup as sb
        box = QMessageBox(QMessageBox.Question, tr("Bring back settings"), settingscard.describe(sb.read(f)),
                          parent=w)
        box.setInformativeText(tr("Your current settings are replaced with these, and Corefold restarts to use "
                                  "them. Your history, clipboard and window size stay as they are."))
        box.show()
        settle(0.4)
        box.grab().save(str(OUT / f"bring-back-question-{lang}-{look}.png"))
        box.close()

        # About: the report card, and making the report (into a test folder, not the real Desktop)
        w.open_page("About")
        settle(0.6)
        w.grab().save(str(OUT / f"about-{lang}-{look}.png"))
        from corefold import report as rp
        opened, shown = [], []
        real = (rp.make, rp.report_folder)
        desk = TMP / "Desktop"
        desk.mkdir(exist_ok=True)
        import corefold.winutil as wu
        real_reveal = wu.reveal_in_explorer
        from PySide6.QtGui import QDesktopServices
        real_open = QDesktopServices.openUrl
        rp.make = lambda settings, folder=None, screens=None: real[0](settings, desk, screens)
        wu.reveal_in_explorer = lambda p: shown.append(p)
        QDesktopServices.openUrl = staticmethod(lambda url: opened.append(url.toString()))
        try:
            path = rp.report_problem(w, ask=False)
        finally:
            rp.make = real[0]
            wu.reveal_in_explorer = real_reveal
            QDesktopServices.openUrl = real_open
        check(path is not None and path.exists() and shown == [path], "Report a problem makes the zip and shows it "
              "in File Explorer")
        from corefold import UPDATE_REPO
        if UPDATE_REPO:
            check(opened and "/issues/new?" in opened[0], f"…and opens GitHub's report page ({opened[:1]})")
    except Exception:
        check(False, f"the window: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()
    finally:
        if helper is not None:
            try:
                helper.stdin.write("\n")
                helper.stdin.flush()
                helper.wait(5)
            except Exception:
                helper.kill()
        try:
            c.quit()
        except Exception:
            pass


# ============================================================================ run

if UI:
    i = sys.argv.index("--ui")
    lang = sys.argv[i + 1] if len(sys.argv) > i + 1 else "en"
    look = sys.argv[i + 2] if len(sys.argv) > i + 2 else "dark"
    window(lang, look, "--taken" in sys.argv)
    print(f"window {lang} {look}: {len(problems)} problem(s)")
    sys.exit(1 if problems else 0)

logic()
for args in (["en", "dark"], ["ar", "light", "--taken"], ["fr", "dark"]):
    print(f"---- the window: {' '.join(args)}", flush=True)
    try:
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--ui", *args], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=120, cwd=str(ROOT))
        out = child.stdout + child.stderr
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        out += "\nFAIL  the window check took too long"
    for line in out.splitlines():
        print("  " + line if not line.startswith(("ok    ", "FAIL  ")) else line, flush=True)
        if line.startswith("FAIL  "):
            problems.append(line[6:])
    if "problem(s)" not in out:
        check(False, f"the window check ({' '.join(args)}) didn't finish")

print(f"newtest_settingsbackup: {len(problems)} problem(s)")
sys.exit(1 if problems else 0)
