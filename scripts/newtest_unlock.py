"""Corefold Unlock, tried for real on a Windows PC: small helper programs hold files open (one with a
window, one without), and Unlock must find them with the right process ids, close the one with a window
politely, force the other one closed, and then find nothing, so the file can be deleted. A folder with a
locked file inside is found too, and the parts of Windows are never offered to close. The "What's using
this?" right-click entry must be there while the tool is in use and gone once it's removed. The page is
built with a real list in English (dark) and Arabic (light), and pictures of it are saved.

Run: python scripts/newtest_unlock.py [folder]
Prints one line per check and "newtest_unlock: N problem(s)"."""
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PART = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ("core", "page") else "all"
ARGS = [a for a in sys.argv[1:] if a not in ("core", "page")]
OUT = Path(ARGS[0] if ARGS else ROOT / "out" / "newtest_unlock").resolve()
LANG = ARGS[1] if len(ARGS) > 1 else "en"
LOOK = ARGS[2] if len(ARGS) > 2 else "dark"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-unlocktest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(290, exit=True)  # stuck somewhere: say where, and stop

problems = []
children: list[subprocess.Popen] = []
NO_WINDOW = 0x08000000

# a program with no window that keeps files open: python hold.py <ready> <file>...
HOLD = """import sys, time
held = [open(p, "a") for p in sys.argv[2:]]
open(sys.argv[1], "w").close()
time.sleep(150)
"""
# a program with a window (like Notepad) that keeps a file open: python window.py <ready> <file>
WINDOW = """import sys
held = open(sys.argv[2], "a")
try:
    import tkinter as tk
    root = tk.Tk()
    root.title("Corefold Unlock test")
    root.geometry("320x120+60+60")
    tk.Label(root, text="Holding a file open").pack(padx=20, pady=30)
    root.after(400, lambda: open(sys.argv[1], "w").close())
    root.after(150000, root.destroy)
    root.mainloop()
except ImportError:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QLabel
    app = QApplication(sys.argv)
    w = QLabel("Holding a file open")
    w.setWindowTitle("Corefold Unlock test")
    w.resize(320, 120)
    w.show()
    QTimer.singleShot(400, lambda: open(sys.argv[1], "w").close())
    QTimer.singleShot(150000, app.quit)
    app.exec()
"""


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


def start(kind: str, *files: Path) -> subprocess.Popen:
    """Starts a helper that holds these files open, and waits until it has."""
    script = TMP / f"{kind}.py"
    script.write_text(HOLD if kind == "hold" else WINDOW, "utf-8")
    ready = TMP / f"ready-{kind}-{len(children)}"
    flags = NO_WINDOW if kind == "hold" else 0
    p = subprocess.Popen([sys.executable, str(script), str(ready), *map(str, files)], creationflags=flags)
    children.append(p)
    end = time.time() + 20
    while time.time() < end and not ready.exists() and p.poll() is None:
        time.sleep(0.05)
    time.sleep(0.3)
    check(ready.exists(), f"the {kind} helper started (pid {p.pid}) and has {len(files)} file(s) open")
    return p


def gone(p: subprocess.Popen, seconds=5.0) -> bool:
    try:
        p.wait(seconds)
        return True
    except subprocess.TimeoutExpired:
        return False


def cleanup():
    for p in children:
        if p.poll() is None:
            p.kill()


# ----------------------------------------------------------------------------- finding and closing, no window

def core():
    from corefold import unlock

    print(f"admin: {unlock.is_admin()}", flush=True)
    work_dir = TMP / "files"
    folder = work_dir / "folder" / "inside"
    folder.mkdir(parents=True)
    held, doc, inner = work_dir / "held.txt", work_dir / "doc.txt", folder / "locked.txt"
    for f in (held, doc, inner):
        f.write_text("Corefold Unlock test\n")
    a = start("hold", held, inner)
    b = start("window", doc)

    def finds_both():
        t0 = time.time()
        found = unlock.find([held, doc])
        took = time.time() - t0
        pids = {p.pid for p in found.programs}
        check(a.pid in pids and b.pid in pids, f"both helpers are found with their own pids ({sorted(pids)}; "
                                               f"want {a.pid} and {b.pid}) in {took:.2f} s")
        for prog in found.programs:
            if prog.pid in (a.pid, b.pid):
                check(bool(prog.name) and prog.exe.lower().endswith((".exe")),
                      f"pid {prog.pid}: name {prog.name!r}, program {prog.exe!r}")
                check(prog.closable and not prog.critical and not prog.blocked and not prog.own,
                      f"pid {prog.pid} can be closed (kind {prog.kind})")
        only = unlock.find([doc])
        check([p.pid for p in only.programs] == [b.pid], f"only the window helper has doc.txt open "
                                                          f"({[p.pid for p in only.programs]})")
    step("finding both", finds_both)

    def folder_found():
        found = unlock.find([work_dir / "folder"])
        pids = [p.pid for p in found.programs]
        check(a.pid in pids and b.pid not in pids, f"a folder with a locked file deep inside is found ({pids})")
        check(found.files == 1 and not found.capped, f"the folder's files were asked about ({found.files})")
    step("a folder", folder_found)

    def caps_and_links():
        many = TMP / "many"
        many.mkdir()
        for i in range(7):
            (many / f"{i}.txt").write_text("x")
        files, capped = unlock.files_of([many], limit=5)
        check(len(files) == 5 and capped, f"a folder's files stop at the limit and it says so ({len(files)})")
        files, capped = unlock.files_of([many])
        check(len(files) == 7 and not capped, "under the limit, every file and no note")
        outside = TMP / "outside"
        outside.mkdir()
        (outside / "elsewhere.txt").write_text("x")
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(many / "link"), str(outside)], capture_output=True)
        if r.returncode == 0:
            files, _ = unlock.files_of([many])
            check(not any("elsewhere" in f for f in files), "a junction inside the folder isn't followed")
    step("limits and links", caps_and_links)

    def own_and_windows():
        mine = TMP / "mine.txt"
        mine.write_text("x")
        with open(mine):
            found = unlock.find([mine])
        me = [p for p in found.programs if p.pid == os.getpid()]
        check(len(me) == 1 and me[0].own and not me[0].closable, "a file this program has open is "
                                                                 "\"Corefold itself\" and never closed")
        names = unlock.process_names()
        check(any(n.lower() == "csrss.exe" for n in names.values()), f"every program's name is known "
                                                                     f"({len(names)} running)")
        for pid, name in list(names.items()):
            if name.lower() in ("csrss.exe", "lsass.exe", "wininit.exe", "services.exe") or pid == 4:
                info = unlock.RM_PROCESS_INFO()
                info.Process.dwProcessId = pid
                info.ApplicationType = 0
                prog = unlock.program_of(info, names)
                check(prog.critical and not prog.closable and not unlock.force_close(prog),
                      f"{name} (pid {pid}) is part of Windows: never offered, never closed")
        check(not unlock.close_politely([unlock.Program(pid=4, name="System", critical=True)]).get(4) == "closed",
              "the System process isn't asked to close")
    step("Corefold itself and the parts of Windows", own_and_windows)

    def polite():
        prog = next(p for p in unlock.find([doc]).programs if p.pid == b.pid)
        check(unlock.windows_of(b.pid) != [], "the window helper's window is found")
        t0 = time.time()
        answers = unlock.close_politely([prog])
        check(answers.get(b.pid) == "closed", f"asking it to close works ({answers}, {time.time() - t0:.1f} s)")
        check(gone(b, 3), "the window helper has ended")
        check(not unlock.find([doc]).programs, "nothing has doc.txt open any more")
    step("closing politely", polite)

    def forced():
        prog = next(p for p in unlock.find([held]).programs if p.pid == a.pid)
        answers = unlock.close_politely([prog], wait_s=1)
        check(answers.get(a.pid) == "no_window", f"the helper without a window can only be forced ({answers})")
        check(unlock.running(prog), "it's still running after being asked")
        stale = unlock.Program(pid=a.pid, name="old", started=prog.started + 1)
        unlock.force_close(stale)
        check(unlock.running(prog) and a.poll() is None,
              "a program that only has the same id (not the same start time) is never closed")
        check(unlock.force_close(prog), "forcing it to close works")
        check(gone(a, 3), "the helper without a window has ended")
        check(not unlock.running(prog), "it's not running any more")
        after = unlock.find([held, doc, work_dir / "folder"])
        check(not after.programs, f"afterwards nothing is found ({[p.pid for p in after.programs]})")
        from corefold.space import recycle
        check(recycle([held]) == 1 and not held.exists(), "and the file can be deleted (to the Recycle Bin)")
        inner.unlink()
        check(not inner.exists(), "the file in the folder can be deleted too")
    step("forcing", forced)

    def messages():
        from corefold import ipc
        check(ipc.message_for(["--unlock", r"C:\a b\c.txt"]) == "unlock\nC:\\a b\\c.txt".encode(),
              "\"--unlock <file>\" from the right-click menu becomes an unlock message")
    step("the message from the menu", messages)


# ----------------------------------------------------------------------------- the page, and the right-click entry

def page_part():
    from corefold import settings as st
    from corefold import tools as all_tools

    s = st.Settings()
    s.first_run, s.tour_done, s.check_updates = False, True, False
    s.language, s.appearance = LANG, LOOK
    s.known_tools = sorted(t.id for t in all_tools.ready())
    s.tools_off = []
    s.quick_told = True
    s.save()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    qapp = QApplication(sys.argv)
    from corefold import i18n
    i18n.set_language(LANG)
    if i18n.is_rtl():
        qapp.setLayoutDirection(Qt.RightToLeft)
    from corefold import app as A
    from corefold import shellmenu

    def until(cond, seconds=10.0):
        end = time.time() + seconds
        while time.time() < end:
            qapp.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return False

    c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
    check(all_tools.is_on(c.settings, "unlock"), "Corefold Unlock is ready and in use")
    work_dir = TMP / "page"
    work_dir.mkdir()
    report = work_dir / "Quarterly report.docx"
    report.write_text("x")
    a = start("hold", report)
    b = start("window", report)

    def shows_list():
        c.handle_message(f"unlock\n{report}")
        check(until(lambda: getattr(c.window, "unlock_page", None) is not None, 5),
              "\"What's using this?\" opens the Unlock page")
        page = c.window.unlock_page
        check(c.window.current_page() == "Unlock", f"the window shows it ({c.window.current_page()})")
        check(until(lambda: page.found is not None and not page.busy, 20), "the page has its answer")
        pids = {r.prog.pid for r in page.rows}
        check({a.pid, b.pid} <= pids, f"the page lists both programs ({sorted(pids)})")
        c.window.resize(1100, 900)
        until(lambda: False, 0.6)
        name = f"unlock-{LANG}-{LOOK}.png"
        check(c.window.grab().save(str(OUT / name)), f"picture saved: {name}")
    step("the page lists them", shows_list)

    def closes_from_page():
        page = c.window.unlock_page
        row = next(r for r in page.rows if r.prog.pid == b.pid)
        check(row.close_btn is not None, "the window helper's row has Close")
        row.close_btn.click()
        check(until(lambda: not page.busy, 15), "closing finishes")
        check(gone(b, 3), "the window helper was closed from the page")
        check([r.prog.pid for r in page.rows] == [a.pid], "only the other one is listed now")
        row = next(r for r in page.rows if r.prog.pid == a.pid)
        check(row.close_btn is not None, "the helper without a window has Close too")
        row.close_btn.click()
        check(until(lambda: not page.busy, 15), "asking it to close finishes")
        row = next((r for r in page.rows if r.prog.pid == a.pid), None)
        check(row is not None and row.force_btn is not None and row.state == "no_window",
              "it has no window, so now it offers Force close, with a warning")
        name = f"unlock-{LANG}-{LOOK}-force.png"
        check(c.window.grab().save(str(OUT / name)), f"picture saved: {name}")
        page._confirm_force = lambda prog: True  # (the "Anything not saved is lost" question, answered yes)
        row.force_btn.click()
        check(until(lambda: not page.busy, 15), "forcing finishes")
        check(gone(a, 3), "the helper without a window was forced closed from the page")
        check(not page.rows and page.delete_btn.isVisible(),
              "afterwards: nothing is using it, and Delete it is offered")
        check(page.status.text() == i18n.tr("Nothing is using it right now. You can move or delete it."),
              f"it says so ({page.status.text()!r})")
        name = f"unlock-{LANG}-{LOOK}-free.png"
        check(c.window.grab().save(str(OUT / name)), f"picture saved: {name}")
        page.delete_btn.click()
        check(until(lambda: not page.busy, 15) and not report.exists(), "Delete it sends it to the Recycle Bin")
    step("closing from the page", closes_from_page)

    if LANG != "en":
        return

    def key(root):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{root}\shell\{shellmenu.UNLOCK_VERB}\command") as k:
                return winreg.QueryValueEx(k, "")[0]
        except OSError:
            return None

    def wait_menu():
        until(lambda: c.menu_timer.isActive(), 1.0)
        return until(lambda: not c.menu_timer.isActive() and not c.menu_busy, 60)

    def right_click():
        c._write_menu()
        check(wait_menu(), "the right-click menu was written")
        for root in (shellmenu.ANY_ROOT, shellmenu.FOLDER_ROOT):
            command = key(root) or ""
            check("--unlock" in command and '"%1"' in command, f"{root}: \"What's using this?\" is there ({command})")
        c.settings.context_menu = False  # the Converter's menu off: Unlock's entry stays
        c.update_shell_menu(0)
        check(wait_menu(), "the menu was written again")
        check(key(shellmenu.ANY_ROOT) and key(shellmenu.FOLDER_ROOT),
              "it stays when the Converter's right-click menu is switched off")
        c.set_tool("unlock", False)
        check(wait_menu(), "removing the tool rewrites the menu")
        check(key(shellmenu.ANY_ROOT) is None and key(shellmenu.FOLDER_ROOT) is None,
              "removing Unlock in the Store takes \"What's using this?\" away")
        check(c.settings.unlock_menu_signature == "off", "and it's remembered as off")
        c.set_tool("unlock", True)
        check(wait_menu() and key(shellmenu.ANY_ROOT) is not None, "adding it back puts it back")
        shellmenu.remove()  # what uninstalling Corefold does
        check(key(shellmenu.ANY_ROOT) is None and key(shellmenu.FOLDER_ROOT) is None,
              "uninstalling Corefold removes it too")
    step("the right-click entry", right_click)
    try:
        shellmenu.remove()  # (the PC is left as it was)
    except Exception:
        pass
    c.quit()


def main():
    if PART == "all":
        step("finding and closing", core)
        cleanup()
        for lang, look in (("en", "dark"), ("ar", "light")):
            r = subprocess.run([sys.executable, __file__, "page", str(OUT), lang, look], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=130)
            print(f"---- the page in {lang} ({look})", flush=True)
            print(r.stdout.rstrip(), flush=True)
            if r.stderr.strip():
                print(r.stderr.rstrip()[-3000:], flush=True)
            for line in r.stdout.splitlines():
                if line.startswith("FAIL"):
                    problems.append(f"{lang}: {line[6:]}")
            check(r.returncode in (0, 1), f"the {lang} page part ran to its end (exit {r.returncode})")
    elif PART == "core":
        step("finding and closing", core)
    else:
        step("the page", page_part)
    cleanup()
    print(f"newtest_unlock{'' if PART == 'all' else ' ' + PART}: {len(problems)} problem(s)", flush=True)
    for p in problems:
        print("  - " + p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
