"""The Uninstaller following real uninstallers on a real Windows PC: pretend apps in this user's part of
the registry, each with a small uninstaller that behaves like a real kind (quick, copies itself and quits,
has a window, hangs, fails, says it's done but isn't, leaves a program running). Also times how long the
list of apps takes, and runs the uninstall window from start to end.

Used by .github/workflows/bughunt.yml (one line per check, then "uninstalltest: N problem(s)").
Run: python scripts/uninstalltest.py
"""
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="corefold-uninstalltest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

import winreg  # noqa: E402

from corefold import uninstall as un  # noqa: E402

problems = []
PYW = str(Path(sys.executable).with_name("pythonw.exe"))
FAKE = TMP / "fake_uninstaller.py"
FAKE.write_text(r'''
import subprocess, sys, time, winreg
mode, key = sys.argv[1], sys.argv[2]
UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"

def gone():
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL + "\\" + key)

if mode == "quick":
    gone()
elif mode == "spawn":      # like NSIS: starts a copy of itself, quits straight away; the copy does the work
    subprocess.Popen([sys.executable, __file__, "child", key])
elif mode == "child":
    time.sleep(4)
    gone()
elif mode == "window":     # a window the user follows
    import tkinter
    root = tkinter.Tk()
    root.title("Pretend uninstaller")
    root.geometry("300x120")
    root.after(4000, root.destroy)
    root.mainloop()
    gone()
elif mode == "hang":       # no window, does nothing
    time.sleep(3600)
elif mode == "fail":
    sys.exit(3)
elif mode == "notreally":  # says it's done, but the app is still installed
    pass
elif mode == "leaveopen":  # done, but leaves a program running (like a "sorry to see you go" page)
    gone()
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
''', "utf-8")


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)


def step(name, fn):
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")


def pretend(name: str, mode: str, exe: str | None = None) -> un.App:
    key = "CfTest" + name
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, un.UNINSTALL + "\\" + key) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, key)
        cmd = f'"{exe}"' if exe else f'"{PYW}" "{FAKE}" {mode} {key}'
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, cmd)
    app = next(a for a in un.list_apps(include_store=False) if a.name == key)
    return app


def run(app, stuck_s=60.0, finish_s=20.0, during=None):
    """Runs its uninstaller like the window does: (outcome, states heard, seconds)."""
    control = un.Control(threading.Event())
    states = []

    def heard(s):
        states.append(s)
        if during:
            during(s, control)
    start = time.monotonic()
    outcome = un.run_uninstaller(app, False, control, heard, stuck_s=stuck_s, finish_s=finish_s)
    return outcome, states, time.monotonic() - start


def kinds():
    out, states, secs = run(pretend("Quick", "quick"))
    check(out.result == "removed" and secs < 10, f"a quick uninstaller: {out.result} in {secs:.1f}s {states}")

    out, states, secs = run(pretend("Spawn", "spawn"))
    check(out.result == "removed" and "working" in states and secs < 20,
          f"one that starts a copy of itself and quits: {out.result} in {secs:.1f}s {states}")

    out, states, secs = run(pretend("Window", "window"))
    check(out.result == "removed" and "window" in states, f"one with a window: {out.result}, {states}")

    out, states, secs = run(pretend("Fail", "fail"), finish_s=3)
    check(out.result == "failed" and out.code == 3 and secs < 15,
          f"one that fails: {out.result} (code {out.code}) in {secs:.1f}s")

    out, states, secs = run(pretend("NotReally", "notreally"), finish_s=3)
    check(out.result == "still" and secs < 15, f"one that closes but the app stays: {out.result} in {secs:.1f}s")

    out, states, secs = run(pretend("LeaveOpen", "leaveopen"))
    check(out.result == "removed" and secs < 40,
          f"one that leaves a program running: {out.result} in {secs:.1f}s (not waited for)")

    def stop_when_stuck(s, control):
        if s == "stuck":
            control.stop.set()
    out, states, secs = run(pretend("Hang", "hang"), stuck_s=5, during=stop_when_stuck)
    check("stuck" in states and out.result == "stopped" and secs < 30,
          f"one that hangs: seen as stuck, then stopped: {out.result} in {secs:.1f}s {states}")

    out, states, secs = run(pretend("Missing", "", exe=str(TMP / "nowhere" / "uninst.exe")))
    check(out.result == "missing" and secs < 3, f"one whose uninstaller is missing: {out.result}")

    def skip_it(s, control):
        if s == "working":
            control.skip.set()
    out, states, secs = run(pretend("Skip", "hang"), during=skip_it)
    check(out.result == "still" and secs < 15, f'"the uninstaller is done" stops the wait: {out.result} in {secs:.1f}s')


def listing():
    t = time.monotonic()
    apps = un.list_apps(include_store=False)
    t1 = time.monotonic() - t
    t = time.monotonic()
    store = un.store_apps()
    t2 = time.monotonic() - t
    t = time.monotonic()
    un.usage()
    programs = un.startup_programs()
    for a in apps:
        un.starts_with_windows(a, programs)
    t3 = time.monotonic() - t
    print(f"  programs: {len(apps)} in {t1:.2f}s, Store apps: {len(store)} in {t2:.2f}s, usage: {t3:.2f}s")
    check(t1 < 5 and t3 < 5, "programs and usage are listed quickly")

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from corefold.appspage import AppsPage
    for round_ in ("first", "second"):
        page = AppsPage(None)
        start = time.monotonic()
        page.reload()
        first = None
        while time.monotonic() - start < 120:
            app.processEvents()
            if page.loaded and first is None:
                first = time.monotonic() - start
            if page.loaded and page.phase == "":
                break
            time.sleep(0.02)
        total = time.monotonic() - start
        stores = sum(1 for a in page.apps if a.store)
        print(f"  {round_} visit: list shown after {first or -1:.2f}s ({stores} Store apps), "
              f"everything after {total:.2f}s; status: {page.status.text()}")
        check(first is not None and first < 6, f"{round_} visit: the list shows within a few seconds")
        if round_ == "second" and store:
            check(stores > 0 and first < 6, "second visit: Store apps show straight away (remembered)")
        page.cancel.set()
        page.deleteLater()


def window():
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    qapp = QApplication.instance() or QApplication([])
    from corefold.appspage import UninstallDialog
    apps = [pretend("DlgOk", "spawn"), pretend("DlgFail", "fail")]
    dlg = UninstallDialog(None, apps, apps, False, QIcon())
    dlg.show()
    seen, clock = [], set()
    dlg.signals.state.connect(lambda s, d: seen.append(s if s != "progress" else d))
    dlg.start()
    start = time.monotonic()
    while dlg.stack.currentIndex() == 1 and time.monotonic() - start < 120:
        qapp.processEvents()
        if dlg.clock.text():
            clock.add(dlg.clock.text())
        time.sleep(0.05)
    page = dlg.stack.currentIndex()
    print(f"  states: {seen}")
    check(page in (2, 3), f"the window gets to the end (page {page})")
    check(len(clock) >= 2, f"the time so far ticks ({sorted(clock)[:3]}…)")
    shown = dlg.left_problems.text() if page == 2 else dlg.done_detail.text()
    check("DlgFail" in shown and "3" in shown, f"what went wrong is shown: {shown!r}")
    dlg.cancel.set()
    dlg.close()


def clean_up():
    k32 = un._dll("kernel32")
    for pid, _, name in un.processes():  # what the pretend uninstallers left running
        if name.lower() == "pythonw.exe":
            h = k32.OpenProcess(un.TERMINATE, False, pid)
            if h:
                k32.TerminateProcess(h, 1)
                k32.CloseHandle(h)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, un.UNINSTALL) as k:
        names = []
        i = 0
        while True:
            try:
                names.append(winreg.EnumKey(k, i))
            except OSError:
                break
            i += 1
    for n in names:
        if n.startswith("CfTest"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, un.UNINSTALL + "\\" + n)
            except OSError:
                pass


for name, fn in (("kinds", kinds), ("listing", listing), ("window", window)):
    step(name, fn)
clean_up()
print(f"uninstalltest: {len(problems)} problem(s)", flush=True)
import faulthandler  # noqa: E402
faulthandler.disable()
os._exit(1 if problems else 0)  # like uitest: skips Qt's tidy-up at exit, which can crash on the runner
