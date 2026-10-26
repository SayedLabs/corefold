"""Checks how Corefold tells people a new version is out, on a real Windows PC, with GitHub's answer
made up (nothing is downloaded): the corner message once a day, the Windows notification when that one
is busy, the line at the top of the tray menu and in its tip, the "New" mark on the Updates row and the
line on the Home page, and that all of it goes once the version isn't waiting any more. It runs in
English (dark) and Arabic (light), and pictures are saved in out/newtest_updates/.

Run from the repo root: python scripts/newtest_updates.py
Prints one line per check and "newtest_updates: N problem(s)"; exits 1 if there were any."""
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "newtest_updates"
OUT.mkdir(parents=True, exist_ok=True)
ONE = len(sys.argv) > 1 and sys.argv[1] == "--one"
LANG = sys.argv[2] if ONE and len(sys.argv) > 2 else "en"
LOOK = sys.argv[3] if ONE and len(sys.argv) > 3 else "dark"
try:  # (the Arabic run's lines, on a console that isn't UTF-8)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

if not ONE:  # each language in a fresh process (settings and language are read once)
    problems = []
    for lang, look in (("en", "dark"), ("ar", "light")):
        try:
            r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--one", lang, look],
                               capture_output=True, timeout=150, cwd=str(ROOT))
            said = (r.stdout + r.stderr).decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired as e:
            said = ((e.stdout or b"") + (e.stderr or b"")).decode("utf-8", errors="replace") + "\nFAIL  timed out"
        print(said.rstrip(), flush=True)
        problems += [line[6:] for line in said.splitlines() if line.startswith("FAIL")]
        if "ok    " not in said:
            problems.append(f"{lang} {look} didn't run")
    print(f"newtest_updates: {len(problems)} problem(s)", flush=True)
    sys.exit(1 if problems else 0)

TMP = Path(tempfile.mkdtemp(prefix="corefold-updatetest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(120, exit=True)  # stuck somewhere: say where, and stop

from corefold import settings as st  # noqa: E402
from corefold import tools as all_tools  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.check_updates = True
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
from corefold import updater  # noqa: E402
from corefold.i18n import tr  # noqa: E402
from corefold.navbar import BADGE_ROLE  # noqa: E402

problems = []
NEW = "99.1.0"
answer = {"info": updater.UpdateInfo(NEW, "https://github.com/SayedLabs/corefold/releases/latest",
                                     f"Corefold-Setup-{NEW}.exe", None, 0, None), "err": ""}


def fake_check(repo, current, timeout=15):
    if answer["err"]:
        raise updater.UpdateError(answer["err"])
    return answer["info"]


updater.check = fake_check


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + f"[{LANG}] {what}", flush=True)
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
c.updates_supported = lambda: True  # (a copy run from source has no update source)
shown_in_windows = []
c.tray.showMessage = lambda *a: shown_in_windows.append(a[0])
settle(0.5)


def badge():
    for row in range(c.window.nav.count()):
        item = c.window.nav.item(row)
        if item.data(Qt.UserRole) == "Updates":
            return item.data(BADGE_ROLE)
    return "no Updates row"


def run_check(manual=False):
    c.check_updates(manual=manual)
    return until(lambda: c.update_state != "checking")


def found():
    check(not c.act_update.isVisible(), "no update line in the tray menu before anything is found")
    run_check()
    check(c.update_state == "available", f"the new version is found ({c.update_state})")
    title = tr("Corefold {version} is available", version=NEW)
    check(c.toast.isVisible() and c.toast.title.text() == title, "the corner message says so")
    c.toast.grab().save(str(OUT / f"message-{LANG}-{LOOK}.png"))
    check(c.settings.update_told.startswith(NEW + " "), f"it remembers telling ({c.settings.update_told!r})")
    check(c.act_update.isVisible() and NEW in c.act_update.text(), f"tray menu line: {c.act_update.text()!r}")
    check(c.menu.actions()[0] is c.act_update, "the tray line is at the top of the menu")
    check(title in c.tray.toolTip(), "the tray tip says so")
    c.toast.hide()


def once_a_day():
    run_check()
    check(not c.toast.isVisible() and not shown_in_windows, "a second check the same day says nothing again")
    c.settings.update_told = ""
    c.toast.running = True  # a conversion is showing in the corner: Windows' own notification instead
    c.toast.show()
    run_check()
    c.toast.running = False
    c.toast.hide()
    check(shown_in_windows == [tr("Corefold {version} is available", version=NEW)],
          f"Windows notification when the corner is busy ({shown_in_windows})")
    c.toast.hide()
    run_check(manual=True)
    check(c.toast.isVisible(), "Check now always says it")
    c.toast.hide()


def window_marks():
    c.show_window("Home")
    settle(0.6)
    w = c.window
    check(w.update_banner.isVisible(), "the Home page has the new-version line")
    check(NEW in w.update_banner_title.text(), f"Home line: {w.update_banner_title.text()!r}")
    check(badge() == tr("New"), f"the Updates row is marked ({badge()!r})")
    w.grab().save(str(OUT / f"home-{LANG}-{LOOK}.png"))
    w.open_page("Updates")
    settle(0.4)
    check(w.update_now.isVisible(), "the Updates page offers the update")
    w.grab().save(str(OUT / f"updates-{LANG}-{LOOK}.png"))
    w.open_page("Home")
    settle(0.2)


def auto_on_now():
    """A version is waiting and "Install updates by themselves" is turned on: it's installed now, not at the
    next check up to 6 hours later. Turned off and on again while nothing waits: it checks at once."""
    started = []
    c.start_update = lambda: started.append(c.update_info.version)
    c.can_self_install = lambda: True
    w = c.window
    w.open_page("Updates")
    settle(0.3)
    c.settings.auto_update = False
    w.auto_install.blockSignals(True)
    w.auto_install.setChecked(False)
    w.auto_install.blockSignals(False)
    w.auto_install.setChecked(True)
    settle(0.2)
    check(started == [NEW], f"turning on automatic install updates to the waiting version at once ({started})")
    check(c.settings.auto_update, "the switch is saved")
    started.clear()
    w.auto_install.setChecked(False)
    settle(0.1)
    check(not started, "turning it off starts nothing")
    checks = []
    real_check = c.check_updates
    c.check_updates = lambda manual=False: checks.append(manual)
    c.update_info, c.update_state = None, "uptodate"
    w.auto_install.setChecked(True)
    settle(0.1)
    check(checks == [False], f"turned on with nothing waiting: it checks at once ({checks})")
    checks.clear()
    w.auto_check.setChecked(False)
    w.auto_check.setChecked(True)
    settle(0.1)
    check(checks == [False], f"automatic checks turned back on: it checks at once ({checks})")
    c.check_updates = real_check
    w.auto_install.setChecked(False)
    del c.start_update, c.can_self_install
    run_check()  # (the waiting version again, for the next steps)
    w.open_page("Home")
    settle(0.2)


def offline_later():
    answer["err"] = "couldn't reach GitHub (are you online?)"
    run_check()
    check(c.update_state == "available" and c.act_update.isVisible() and c.window.update_banner.isVisible(),
          f"offline at the next check: the version found before still shows ({c.update_state}, "
          f"{c.act_update.isVisible()}, {c.window.update_banner.isVisible()})")
    check(c.update_retry.isActive() and c.update_retry.remainingTime() <= A.UPDATE_RETRY_MS[0] * 60_000,
          f"offline: it checks again in {A.UPDATE_RETRY_MS[0]} minutes, not 6 hours")
    c.update_retry.stop()
    run_check()
    check(c.update_retry.remainingTime() > A.UPDATE_RETRY_MS[0] * 60_000, "still offline: it waits a bit longer")
    answer["err"] = ""
    c.update_retry.stop()
    run_check()
    check(c.update_retries == 0 and not c.update_retry.isActive(), "back online: the waiting is reset")


def language_change():
    c._build_tray_menu()  # (what changing the language does)
    check(c.act_update.isVisible() and NEW in c.act_update.text(), "the tray line survives the menu being remade")


def gone():
    answer["info"] = None
    run_check()
    check(c.update_state == "uptodate", "up to date again")
    check(not c.act_update.isVisible(), "the tray line goes")
    check(tr("Corefold {version} is available", version=NEW) not in c.tray.toolTip(), "the tray tip goes back")
    check(not c.window.update_banner.isVisible(), "the Home line goes")
    check(not badge(), "the Updates row's mark goes")


def switched_off():
    answer["info"] = updater.UpdateInfo("99.2.0", "https://github.com/SayedLabs/corefold/releases/latest",
                                        None, None, 0, None)
    c.settings.check_updates = False
    shown_in_windows.clear()
    run_check()
    check(c.update_state != "available" and not c.toast.isVisible() and not shown_in_windows,
          "with automatic checks off, nothing is checked or said")


for name, fn in (("found", found), ("once a day", once_a_day), ("window", window_marks),
                 ("auto on", auto_on_now), ("offline", offline_later), ("language", language_change), ("gone", gone),
                 ("off", switched_off)):
    step(name, fn)

c.quit()
print(f"newtest_updates {LANG}: {len(problems)} problem(s)", flush=True)
faulthandler.cancel_dump_traceback_later()
sys.exit(1 if problems else 0)
