"""Clicks through a built Corefold.exe from the outside, with the real mouse, the way a person does:
switches every tool on in its section and opens it, and saves a picture of the whole screen after each click.

Run: python scripts/exetest.py <Corefold.exe> <folder>   (needs pywinauto and Pillow)
Prints one line per check and "exetest: N problem(s)"."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "exetest").resolve()
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-exetest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
os.environ.pop("COREFOLD_WAKE_RESTING", None)  # (the built app ignores it: its sections rest as they ship)
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from PIL import ImageGrab, ImageStat  # noqa: E402
from pywinauto import Desktop, mouse  # noqa: E402

from corefold import settings as st  # noqa: E402
from corefold import tools as all_tools  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.check_updates = False
s.appearance = "dark"
s.known_tools = sorted(t.id for t in all_tools.ready())
s.tools_off = sorted(t.id for t in all_tools.ready() if t.id not in all_tools.ORIGINAL)
s.save()
problems = []
shot_no = [0]


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)
    return ok


def window():
    for _ in range(40):
        try:
            wins = [w for w in Desktop(backend="uia").windows() if w.window_text() == "Corefold" and w.is_visible()
                    and w.rectangle().width() > 600]  # (not a toast)
        except Exception:  # a window (a toast, a menu) closed while Windows was listing them: look again
            wins = []
        if wins:
            return wins[0]
        time.sleep(0.5)
    return None


def shot(name, win):
    """The window as it is on the screen; returns how much of it is plain white."""
    time.sleep(1.5)
    shot_no[0] += 1
    try:
        r = win.rectangle()
    except Exception:  # the window was made again in the meantime (after adding a tool): the new one
        win = window()
        if win is None:
            return 1.0
        r = win.rectangle()
    img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
    img.save(OUT / f"exe-{shot_no[0]:02}-{name}.png")
    small = img.convert("L").resize((80, 50))
    white = sum(1 for v in small.getdata() if v > 245) / 4000
    print(f"note  {name}: {white:.2f} white, mean {ImageStat.Stat(small).mean[0]:.0f}", flush=True)
    return white


def click(ctrl):
    r = ctrl.rectangle()
    mouse.click(coords=((r.left + r.right) // 2, (r.top + r.bottom) // 2))


proc = subprocess.Popen([str(EXE), "--page", "Files"])
watch = None
if os.name == "nt":  # every window Corefold.exe (and what it starts) shows: none may only flash
    sys.path.insert(0, str(ROOT / "scripts"))
    from flashwatch import FlashWatch
    watch = FlashWatch(proc.pid).start()
try:
    win = window()
    if check(win is not None, "Corefold.exe opens its window on Files"):
        win.set_focus()
        win.maximize()  # (every switch on the screen at once, the way most people keep it)
        check(shot("store", win) < 0.3, "the Files section isn't white")
        rested = set()
        for tool in all_tools.ready():
            if tool.id in all_tools.ORIGINAL:
                continue
            win = window()
            sec = all_tools.SECTION_OF[tool.id]
            row = [t for t in win.descendants(control_type="ListItem") if t.window_text() == sec.name]
            if not check(bool(row), f"the sidebar has {sec.name}"):
                continue
            click(row[0])
            time.sleep(0.8)
            switch = [b for b in win.descendants(control_type="CheckBox") if b.window_text() == tool.nav]
            if not switch and sec.one_tool and all_tools.state(tool.id) == all_tools.AVAILABLE:
                # (one tool: its uses' switches show after ⚙ on its heading)
                gear = [b for b in win.descendants(control_type="Button") if b.window_text() == "Settings"]
                if check(bool(gear), f"{sec.name}'s heading has Settings"):
                    click(gear[0])
                    time.sleep(0.8)
                    switch = [b for b in win.descendants(control_type="CheckBox") if b.window_text() == tool.nav]
            if all_tools.state(tool.id) != all_tools.AVAILABLE:  # (its section rests: shown, never switched on)
                check(not switch, f"{tool.id} can't be switched on while {sec.name} rests")
                if sec.name not in rested:
                    rested.add(sec.name)
                    soon = [t for t in win.descendants() if t.window_text() == "Coming back soon"]
                    check(bool(soon), f"{sec.name} says its tools are coming back soon")
                    check(shot(f"resting-{sec.id}", win) < 0.3, f"{sec.name}'s resting page isn't white")
                continue
            if not check(bool(switch), f"a switch for {tool.id} in {sec.name}"):
                continue
            r, wr = switch[0].rectangle(), win.rectangle()
            if wr.top < (r.top + r.bottom) // 2 < wr.bottom - 10:
                click(switch[0])
            else:
                switch[0].toggle()  # (below the bottom of the window)
            worst = 0.0
            end = time.time() + 1.0
            while time.time() < end:  # the moments right after the click
                img = ImageGrab.grab(bbox=(wr.left, wr.top, wr.right, wr.bottom), all_screens=True)
                worst = max(worst, sum(1 for v in img.convert("L").resize((80, 50)).getdata() if v > 245) / 4000)
            check(worst < 0.3, f"switching on {tool.id} doesn't flash a white window (whitest moment {worst:.2f})")
            time.sleep(0.6)
            win = window()
            check(win is not None, f"after switching on {tool.id} the window is there")
            if win is None:
                break
            check(shot(f"added-{tool.id}", win) < 0.3, f"after switching on {tool.id} the window isn't white")
            win = window() or win  # (the window may have been made again meanwhile)
            wr = win.rectangle()
            if sec.one_tool:  # (its tab in the bar of uses on the tool's heading: not its switch or its card's name)
                tab = [b for b in win.descendants(control_type="Button") if b.window_text() == (tool.use or tool.nav)
                       and b.rectangle().top < wr.top + 300]
            else:  # (its tab, in the strip at the top: not its switch or its name on the card)
                tab = [b for b in win.descendants() if b.window_text() == tool.nav and b.rectangle().top < wr.top + 150
                       and b.element_info.control_type not in ("ListItem", "Text")]
            if check(bool(tab), f"{tool.id} has a tab in {sec.name}"):
                click(tab[0])
                check(shot(f"open-{tool.id}", win) < 0.3, f"{tool.id}'s page isn't white")
    if watch is not None:
        time.sleep(5)
        watch.stop()
        for line in watch.notes():
            print("note  shown: " + line, flush=True)
        flashes = watch.problems()
        for line in flashes:
            check(False, line)
        check(not flashes, f"Corefold.exe flashed no window ({len(watch.events)} shown)")
finally:
    log = Path(os.environ["LOCALAPPDATA"]) / "Corefold" / "corefold.log"
    if log.exists():
        (OUT / "exe-log.txt").write_text(log.read_text("utf-8", "replace"), "utf-8")
    proc.kill()
print(f"exetest: {len(problems)} problem(s)", flush=True)
