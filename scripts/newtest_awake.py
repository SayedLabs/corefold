"""Checks Corefold Awake on a real Windows PC: keeping the PC awake really shows in "powercfg /requests"
(and goes away), a timed period ends by itself, the tray menu, removing the tool, and Windows' own
shutdown timer (planned an hour ahead and always cancelled again). Then the page is built in English
(dark) and Arabic (light), and pictures of it are saved in out/newtest_awake/.

Run from the repo root: python scripts/newtest_awake.py
Prints one line per check and "newtest_awake: N problem(s)"; exits 1 if there were any."""
import os
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "newtest_awake"
OUT.mkdir(parents=True, exist_ok=True)
PAGE = len(sys.argv) > 1 and sys.argv[1] == "--page"
LANG = sys.argv[2] if PAGE and len(sys.argv) > 2 else "en"
LOOK = sys.argv[3] if PAGE and len(sys.argv) > 3 else "dark"
TMP = Path(tempfile.mkdtemp(prefix="corefold-awaketest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(120 if PAGE else 280, exit=True)  # stuck somewhere: say where, and stop

from corefold import settings as st  # noqa: E402
from corefold import tools as all_tools  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.check_updates = False
s.language = LANG
s.appearance = LOOK
s.known_tools = [t.id for t in all_tools.ready()]
s.quick_told = True
s.save()

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

qapp = QApplication(sys.argv[:1])
from corefold import i18n  # noqa: E402
i18n.set_language(LANG)
if i18n.is_rtl():
    qapp.setLayoutDirection(Qt.RightToLeft)
from corefold import app as A  # noqa: E402
from corefold import awake  # noqa: E402

problems = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)


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
        time.sleep(0.02)
    return False


def step(name, fn):
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()
    settle(0.1)


c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
settle(0.5)

# ----------------------------------------------------------------------------- the real thing

ME = {os.path.basename(p).lower() for p in (sys.executable, getattr(sys, "_base_executable", "")) if p}


def requests() -> dict[str, list[str]]:
    """powercfg /requests, as section -> its lines (DISPLAY, SYSTEM, AWAYMODE, EXECUTION, ...)."""
    r = subprocess.run(["powercfg", "/requests"], capture_output=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    text = (r.stdout + r.stderr).decode("oem", errors="replace")
    out: dict[str, list[str]] = {}
    section = None
    for line in text.splitlines():
        line = line.strip()
        if line.endswith(":") and line[:-1].isupper():
            section = line[:-1]
            out[section] = []
        elif section and line:
            out[section].append(line)
    if not out:
        print("powercfg said:", text.strip()[:500])
    return out


def mine(lines: list[str]) -> bool:
    return any("[PROCESS]" in line and any(line.lower().endswith(name) for name in ME) for line in lines)


def holds(section: str) -> bool:
    return mine(requests().get(section, []))


def main_checks():
    print("this process:", ", ".join(sorted(ME)))
    tool = c.awake
    check(tool is not None, "Awake is running while the tool is in use")
    if tool is None:
        return
    keep_menu = tool.menu.menuAction()
    check(keep_menu in c.menu.actions(), "the tray menu has Keep awake")
    req = requests()
    check("SYSTEM" in req and "DISPLAY" in req, f"powercfg /requests can be read ({', '.join(req) or 'nothing'})")
    check(not mine(req.get("SYSTEM", [])), "nothing is asked of Windows before it's switched on")

    def on_off():
        c.settings.awake_screen = False
        tool.keep()
        check(tool.on and tool.end is None, "Until I turn it off: on, with no end")
        req = requests()
        check(mine(req.get("SYSTEM", [])), "on: powercfg lists a SYSTEM request from this process")
        check(not mine(req.get("DISPLAY", [])), "on, screen may turn off: no DISPLAY request")
        check("\n" in c.tray.toolTip() and tool.status() in c.tray.toolTip(),
              f"the tray's tooltip says it's staying awake ({c.tray.toolTip()!r})")
        tool.set_screen(True)
        req = requests()
        check(mine(req.get("DISPLAY", [])), "Keep the screen on too: a DISPLAY request as well")
        check(mine(req.get("SYSTEM", [])), "... and still the SYSTEM one")
        tool.set_screen(False)
        check(not holds("DISPLAY"), "screen option off again: the DISPLAY request is gone")
        tool.let_sleep()
        req = requests()
        check(not mine(req.get("SYSTEM", [])) and not mine(req.get("DISPLAY", [])),
              "off: no request from this process any more")
        check("\n" not in c.tray.toolTip(), "the tooltip no longer mentions it")
    step("on and off", on_off)

    def timed():
        c.toast.hide()
        tool.keep(datetime.now() + timedelta(seconds=2))
        check(tool.on and tool.end is not None, "for 2 seconds: on, with an end")
        check(holds("SYSTEM"), "for 2 seconds: the SYSTEM request is there")
        ended = until(lambda: not tool.on, 8)
        check(ended, "the 2 seconds end by themselves")
        check(not holds("SYSTEM"), "... and the request is cleared")
        check(c.toast.isVisible(), "... and a message says so")
        c.toast.hide()
    step("timed", timed)

    def until_a_time():
        later = awake.next_time((datetime.now() + timedelta(minutes=30)).strftime("%H:%M"))
        tool.keep_until(later.strftime("%H:%M"))
        check(tool.on and tool.end == later, f"until a time: ends at {later:%H:%M}")
        past = awake.next_time((datetime.now() - timedelta(minutes=5)).strftime("%H:%M"))
        check((past - datetime.now()).total_seconds() > 23 * 3600, f"a time already past means tomorrow ({past})")
        tool.let_sleep()
    step("until a time", until_a_time)

    def tray_and_toggle():
        tool._tray_chosen(60)
        left = tool.seconds_left() or 0
        check(tool.on and 3500 < left <= 3600, f"tray: 1 hour ({left:.0f} s left)")
        tool._fill_tray_menu()
        ticked = [a.text() for a in tool.choice_actions if a.isChecked()]
        check(ticked == [i18n.tr("1 hour")], f"tray: 1 hour is ticked ({ticked})")
        tool._tray_chosen("on")
        tool._fill_tray_menu()
        ticked = [a.text() for a in tool.choice_actions if a.isChecked()]
        check(tool.end is None and ticked == [i18n.tr("Until I turn it off")], f"tray: until I turn it off ({ticked})")
        tool._tray_chosen("off")
        tool._fill_tray_menu()
        ticked = [a.text() for a in tool.choice_actions if a.isChecked()]
        check(not tool.on and ticked == [i18n.tr("Off")], f"tray: off ({ticked})")
        check(not holds("SYSTEM"), "tray: off clears the request")
        c.settings.awake_mode = "for"
        c.settings.awake_minutes = 30
        tool.toggle()
        left = tool.seconds_left() or 0
        check(tool.on and 1750 < left <= 1800, f"toggle: on for the 30 minutes chosen last ({left:.0f} s left)")
        tool.toggle()
        check(not tool.on and not holds("SYSTEM"), "toggle: off again")
    step("tray and toggle", tray_and_toggle)

    def shutdown_timer():
        at = datetime.now() + timedelta(seconds=3600)
        try:
            code = tool.plan_shutdown(at)
            check(code == 0, f"shutdown in an hour: planned (code {code})")
            planned = tool.planned_shutdown()
            check(planned is not None and abs((planned - at).total_seconds()) < 5,
                  f"... and remembered ({c.settings.shutdown_at})")
            check(st.Settings.load().shutdown_at == c.settings.shutdown_at, "... on disk, for after a restart")
            code = tool.plan_shutdown(at + timedelta(minutes=5))
            check(code == 0, f"planning again replaces our own (code {code})")
            code = tool.cancel_shutdown()
            check(code == 0, f"cancelled (code {code})")
            check(tool.planned_shutdown() is None and not c.settings.shutdown_at, "... and forgotten")
            code, said = awake.run_shutdown("/a")
            check(code == awake.ERROR_NO_SHUTDOWN_IN_PROGRESS,
                  f"Windows has no shutdown planned any more (shutdown /a again: {code} {said!r})")
            # one planned by something else: asked before it's replaced
            code, said = awake.run_shutdown("/s", "/t", "3600")
            check(code == 0, f"(a shutdown planned outside Corefold: {code} {said!r})")
            code = tool.plan_shutdown(at)
            check(code == awake.ERROR_SHUTDOWN_IS_SCHEDULED, f"another one planned already: Windows says so ({code})")
            check(tool.planned_shutdown() is None, "... and nothing is remembered")
            code = tool.plan_shutdown(at, replace=True)
            check(code == 0 and tool.planned_shutdown() is not None, f"replace=True replaces it (code {code})")
            code = tool.cancel_shutdown()
            check(code == 0, f"cancelled again (code {code})")
        finally:
            code, said = awake.run_shutdown("/a")  # never leave the PC to turn itself off
            print(f"(shutdown /a at the end: {code} {said!r})")
        check(code == awake.ERROR_NO_SHUTDOWN_IN_PROGRESS, "nothing is left planned")
        # remembered ones that can't be there any more are forgotten
        c.settings.shutdown_at = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
        c.settings.shutdown_planned = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
        check(tool.planned_shutdown() is None and not c.settings.shutdown_at, "a time that's past is forgotten")
        boot = awake.booted_at()
        c.settings.shutdown_at = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
        c.settings.shutdown_planned = (boot - timedelta(minutes=10)).isoformat(timespec="seconds")
        check(tool.planned_shutdown() is None and not c.settings.shutdown_at,
              f"one planned before Windows last started (at {boot:%H:%M}) is forgotten")
    step("shutdown timer", shutdown_timer)

    def removed():
        tool.keep()
        check(holds("SYSTEM"), "(on again before removing the tool)")
        c.set_tool("awake", False)
        settle(0.5)
        check(c.awake is None, "removed in the Store: Awake isn't running")
        check(keep_menu not in c.menu.actions(), "... the tray menu has no Keep awake")
        check(not holds("SYSTEM"), "... and the PC can sleep again")
        c.set_tool("awake", True)
        settle(0.5)
        check(c.awake is not None and c.awake.menu.menuAction() in c.menu.actions(), "added again: back in the tray")
    step("removed", removed)

    def quitting():
        c.awake.keep()
        check(holds("SYSTEM"), "(on again before quitting)")
        c.quit()
        settle(0.2)
        check(not holds("SYSTEM"), "quitting Corefold lets the PC sleep again")
    step("quitting", quitting)


def page_checks():
    """The page in one language and look, with pictures."""
    tool = c.awake
    check(tool is not None, f"[{LANG}] Awake is running")
    c.show_window("Awake")
    w = c.window
    w.resize(1100, 860)
    settle(0.8)
    pg = w.awake_page
    check(pg is not None and w.current_page() == "Awake", f"[{LANG}] the page is built and shown")
    if pg is None or tool is None:
        return
    check(pg.state_title.text() == tool.status() and not tool.on,
          f"[{LANG}] off to begin with: {pg.state_title.text()}")
    w.grab().save(str(OUT / f"awake-off-{LANG}-{LOOK}.png"))
    box = pg.until.edit
    check(box.valueFromText(box.textFromValue(18 * 60 + 35)) == 18 * 60 + 35,
          f"[{LANG}] the time box reads its own text back ({box.textFromValue(18 * 60 + 35)})")
    pg.mode_pills.pick("for")
    pg.duration.pills.pick(120)
    check(c.settings.awake_mode == "for" and c.settings.awake_minutes == 120, f"[{LANG}] For a while, 2 hours: kept")
    pg.power.click()
    settle(0.3)
    left = tool.seconds_left() or 0
    check(tool.on and 7100 < left <= 7200, f"[{LANG}] Keep awake: on for 2 hours ({left:.0f} s left)")
    check(pg.state_title.text() == tool.status() and pg.power.text() == i18n.tr("Let it sleep"),
          f"[{LANG}] the page says so: {pg.state_title.text()}")
    check(holds("SYSTEM"), f"[{LANG}] and Windows has the request")
    pg.screen.click()
    settle(0.3)
    check(c.settings.awake_screen and holds("DISPLAY"), f"[{LANG}] Keep the screen on too: switched on")
    # a planned shutdown as the page shows it (only remembered here: nothing is asked of Windows)
    c.settings.shutdown_at = (datetime.now() + timedelta(hours=1, minutes=20)).isoformat(timespec="seconds")
    c.settings.shutdown_planned = datetime.now().isoformat(timespec="seconds")
    pg.refresh()
    settle(0.3)
    check(pg.cancel_btn.isVisible() and not pg.plan_box.isVisible(), f"[{LANG}] a planned shutdown shows Cancel")
    w.grab().save(str(OUT / f"awake-on-{LANG}-{LOOK}.png"))
    c.settings.shutdown_at = c.settings.shutdown_planned = ""
    pg.mode_pills.pick("until")
    pg.off_pills.pick("at")
    pg.duration.pills.pick("custom")
    pg.refresh()
    settle(0.3)
    check(pg.until.isVisible() and pg.off_at.isVisible() and not pg.duration.isVisible(),
          f"[{LANG}] Until a time and At a time show their time boxes")
    w.resize(760, 760)
    settle(0.5)
    w.grab().save(str(OUT / f"awake-small-{LANG}-{LOOK}.png"))
    pg.power.click()
    settle(0.3)
    check(not tool.on and not holds("SYSTEM"), f"[{LANG}] Let it sleep: off, and the request is gone")


if PAGE:
    step("page", page_checks)
    c.quit()
else:
    step("main", main_checks)
    for lang, look in (("en", "dark"), ("ar", "light")):
        try:
            r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--page", lang, look],
                               capture_output=True, timeout=150, cwd=str(ROOT))
            said = (r.stdout + r.stderr).decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired as e:
            said = ((e.stdout or b"") + (e.stderr or b"")).decode("utf-8", errors="replace") + "\nFAIL  timed out"
        print(said.rstrip(), flush=True)
        for line in said.splitlines():
            if line.startswith("FAIL"):
                problems.append(line[6:])
        if "ok    " not in said:
            check(False, f"the page in {lang} {look} didn't run")
    pics = sorted(p.name for p in OUT.glob("awake-*.png"))
    check(len(pics) >= 6, f"pictures saved in out/newtest_awake: {', '.join(pics)}")

print(f"newtest_awake{' page ' + LANG if PAGE else ''}: {len(problems)} problem(s)", flush=True)
faulthandler.cancel_dump_traceback_later()
sys.exit(1 if problems else 0)
