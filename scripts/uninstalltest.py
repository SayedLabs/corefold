"""The Uninstaller following real uninstallers on a real Windows PC: pretend apps in this user's part of
the registry, each with a small uninstaller that behaves like a real kind (quick, copies itself and quits,
has a window, hangs, fails, says it's done but isn't, leaves a program running). Also times how long the
list of apps takes, runs the uninstall window from start to end, and checks the leftovers each clean-up
depth offers (with services, scheduled tasks and files in use), and that all of it can be put back.

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


def leftovers():
    """Leftovers at each depth: what's offered and ticked, programs still running from its folder,
    files in use, services and scheduled tasks; then all of it put back."""
    import shutil
    import subprocess
    name = "CfDeepApp"
    local, roam = Path(os.environ["LOCALAPPDATA"]), Path(os.environ["APPDATA"])
    inst = local / "Programs" / name
    (inst / "bin").mkdir(parents=True)
    for i in range(5):
        (inst / "bin" / f"part{i}.dat").write_text("x" * 1000)
    (roam / name).mkdir(parents=True)
    (roam / name / "settings.ini").write_text("x")
    vendor = local / "SomeVendorX" / name                      # deep: another vendor's folder, level 2
    vendor.mkdir(parents=True)
    (local / "SomeVendorX" / "OtherThing").mkdir()
    temp = Path(os.environ["TEMP"]) / name                     # deep: temporary files, level 2
    temp.mkdir(exist_ok=True)
    docs = Path(os.environ["USERPROFILE"]) / "Documents" / name  # deep: the person's own folder, level 1
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "my save.dat").write_text("precious")
    key = un.UNINSTALL + "\\" + name
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, name)
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, str(inst))
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, f'"{inst / "uninstall.exe"}"')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"Software\\SomeVendorX\\{name}") as k:  # deep, level 2
        winreg.SetValueEx(k, "x", 0, winreg.REG_SZ, "1")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{un.APP_PATHS}\\cfdeepapp.exe") as k:  # level 3
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(inst / "cfdeepapp.exe"))
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"Software\\Classes\\{name}.Doc\\shell\\open\\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'"{inst / "cfdeepapp.exe"}" "%1"')  # a file type, level 2
    task = "\\" + name + "Update"
    svc = name + "Svc"
    subprocess.run(["schtasks", "/Create", "/TN", task, "/TR", f'"{inst / "updater.exe"}"', "/SC", "DAILY",
                    "/ST", "09:00", "/F"], capture_output=True)
    subprocess.run(["sc.exe", "create", svc, "binPath=", str(inst / "service.exe"), "start=", "demand"],
                   capture_output=True)
    # a program of the app still running from its folder, keeping a file there in use
    base = Path(sys.base_prefix)
    for f in ["pythonw.exe", *[p.name for p in base.glob("python3*.dll")], "vcruntime140.dll", "vcruntime140_1.dll"]:
        if (base / f).exists():
            shutil.copy2(base / f, inst / f)
    env = dict(os.environ, PYTHONHOME=str(base))
    proc = subprocess.Popen([str(inst / "pythonw.exe"), "-c", "import time; f = open('bin/log.txt', 'a'); "
                             "time.sleep(600)"], cwd=str(inst), env=env)
    time.sleep(2)
    # and a file Corefold itself holds open (in use by "something else" that can't be closed)
    held = open(roam / name / "in use.txt", "w")

    apps = un.list_apps(include_store=False)
    app = next(a for a in apps if a.name == name)
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)  # as if its uninstaller ran
    start = time.monotonic()
    left = un.find_leftovers(app, [a for a in apps if a is not app])
    print(f"  found {len(left)} in {time.monotonic() - start:.1f}s:")
    for i in left:
        print(f"    {i.level} {'deep ' if i.deep else ''}{i.kind:8} {i.reason:9} {i.label()}")

    def has(kind, path_end, level=None, deep=None):
        for i in left:
            if i.kind == kind and os.path.normcase(i.path).endswith(os.path.normcase(path_end)):
                return (level is None or i.level == level) and (deep is None or i.deep == deep)
        return False
    check(has("folder", str(inst), 3, False), "its install folder: sure")
    check(has("folder", str(roam / name), 3, False), "its settings folder: sure")
    check(has("folder", str(vendor), 2, True), "a folder under another vendor's: deep, most likely")
    check(has("folder", str(temp), 2, True), "its temporary files: deep, most likely")
    check(has("folder", str(docs), 1, True), "its folder in Documents: deep, never ticked")
    check(has("key", f"Software\\SomeVendorX\\{name}", 2, True), "a registry key under another vendor: deep")
    check(has("key", f"{un.APP_PATHS}\\cfdeepapp.exe", 3), "its App Paths entry: sure")
    check(has("key", f"Software\\Classes\\{name}.Doc", 2), "its file type: most likely")
    check(has("task", task, 3), "its scheduled task: sure")
    check(has("service", svc, 3), "its Windows service: sure")
    check(not any("OtherThing" in i.path for i in left), "another program's folder isn't offered")
    check(all(un.item_allowed(dict(i.__dict__)) for i in left), "everything offered passes the safety checks")
    safe = [i for i in left if un.shown_at(i, "safe")]
    normal = [i for i in left if un.shown_at(i, "normal")]
    deep = [i for i in left if un.shown_at(i, "deep")]
    check(all(i.level == 3 for i in safe) and len(safe) < len(normal) < len(deep),
          f"each depth offers more: safe {len(safe)}, normal {len(normal)}, deep {len(deep)}")
    check(not any(un.ticked_at(i, "deep") for i in left if i.reason == "userfiles"),
          "the person's own folders are never ticked, even when deep")
    for bad in ({"kind": "service", "path": "HKLM\\SYSTEM\\CurrentControlSet\\Services\\Dnscache"},
                {"kind": "task", "path": "\\Microsoft\\Windows\\Defrag\\ScheduledDefrag"},
                {"kind": "key", "path": "HKCU\\Software\\Classes\\.txt"},
                {"kind": "key", "path": "HKCU\\Software\\Classes\\CLSID"},
                {"kind": "key", "path": "HKLM\\Software\\Classes\\exefile"}):
        check(not un.item_allowed(bad) or (bad["kind"] == "service" and not un._service_ok(bad["path"])),
              f"refused: {bad['kind']} {bad['path']}")

    running = un.programs_in([i.path for i in left if i.kind == "folder"])
    check(any(pid == proc.pid for pid, _ in running), f"the program still running from its folder is seen {running}")
    chosen = [i for i in left if un.ticked_at(i, "deep")]
    folder = un.new_backup([name])
    res = un.remove(chosen, folder, [name], close_running=True)
    print(f"  removed {len(res['done'])}, failed {[f['path'] for f in res['failed']]}")
    check(proc.poll() is not None, "the running program was closed first")
    check(not inst.exists(), "its install folder is gone (nothing in use any more)")
    partial = [d for d in res["done"] if d.get("left")]
    check(not (roam / name / "settings.ini").exists() and (roam / name / "in use.txt").exists() and len(partial) == 1,
          "a folder with a file in use: everything else is moved, the file in use stays")
    check(any(f["path"] == str(roam / name) for f in res["failed"]), "…and it's tried again (as administrator)")
    check(docs.exists() and (docs / "my save.dat").exists(), "its folder in Documents is kept (not ticked)")
    check(not un.key_exists("HKLM", f"{un.SERVICES}\\{svc}", 0), "its service is removed")
    check(subprocess.run(["schtasks", "/Query", "/TN", task], capture_output=True).returncode != 0,
          "its scheduled task is removed")
    check(not un.key_exists("HKCU", f"Software\\Classes\\{name}.Doc", 0), "its file type is removed")
    more = un.remove(un.items_from(res["failed"]), folder, [name], admin=True)  # what the admin copy does
    later = sum(int(d.get("on_restart") or 0) for d in more["done"])
    check(later == 1, f"the file still in use is left for Windows to remove at restart ({later})")
    held.close()
    r = un.restore(folder)
    print(f"  put back {r['done']}, failed {[f.get('path') for f in r['failed']]}")
    check(not r["failed"], "everything is put back")
    check((inst / "bin" / "part0.dat").exists() and (roam / name / "settings.ini").exists() and vendor.exists(),
          "the folders are back (the one with a file in use too)")
    check(un.key_exists("HKLM", f"{un.SERVICES}\\{svc}", 0), "the service is back")
    check(subprocess.run(["schtasks", "/Query", "/TN", task], capture_output=True).returncode == 0,
          "the scheduled task is back")
    check(un.key_exists("HKCU", f"Software\\Classes\\{name}.Doc", 0), "the file type is back")
    # tidy up
    subprocess.run(["schtasks", "/Delete", "/TN", task, "/F"], capture_output=True)
    subprocess.run(["sc.exe", "delete", svc], capture_output=True)
    for k in (f"Software\\Classes\\{name}.Doc\\shell\\open\\command", f"Software\\Classes\\{name}.Doc\\shell\\open",
              f"Software\\Classes\\{name}.Doc\\shell", f"Software\\Classes\\{name}.Doc",
              f"{un.APP_PATHS}\\cfdeepapp.exe", f"Software\\SomeVendorX\\{name}", "Software\\SomeVendorX"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, k)
        except OSError:
            pass
    shutil.rmtree(docs, ignore_errors=True)
    shutil.rmtree(temp, ignore_errors=True)


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


ALL = {"kinds": kinds, "listing": listing, "window": window, "leftovers": leftovers}
for name in sys.argv[1:] or ALL:  # or just some: python scripts/uninstalltest.py window
    step(name, ALL[name])
clean_up()
print(f"uninstalltest: {len(problems)} problem(s)", flush=True)
import faulthandler  # noqa: E402
faulthandler.disable()
os._exit(1 if problems else 0)  # like uitest: skips Qt's tidy-up at exit, which can crash on the runner
