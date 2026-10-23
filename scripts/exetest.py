"""Clicks through a built Corefold.exe from the outside, with the real mouse, the way a person does:
adds every tool from the Store and opens it, and saves a picture of the whole screen after each click.

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
        wins = [w for w in Desktop(backend="uia").windows() if w.window_text() == "Corefold" and w.is_visible()
                and w.rectangle().width() > 600]  # (not a toast)
        if wins:
            return wins[0]
        time.sleep(0.5)
    return None


def shot(name, win):
    """The window as it is on the screen; returns how much of it is plain white."""
    time.sleep(1.5)
    shot_no[0] += 1
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


proc = subprocess.Popen([str(EXE), "--page", "Store"])
try:
    win = window()
    if check(win is not None, "Corefold.exe opens its window on the Store"):
        win.set_focus()
        win.maximize()  # (every Add button on the screen at once, the way most people keep it)
        check(shot("store", win) < 0.3, "the Store isn't white")
        for tool in all_tools.ready():
            if tool.id in all_tools.ORIGINAL:
                continue
            win = window()
            store = win
            cards = [c for c in store.descendants(control_type="Text") if c.window_text() == tool.name]
            add = None
            if cards:
                parent = cards[0].parent()
                while parent is not None and add is None:
                    found = [b for b in parent.descendants(control_type="Button") if b.window_text() == "Add"]
                    add = found[0] if found else None
                    parent = parent.parent() if add is None else parent
            if not check(add is not None, f"an Add button for {tool.id}"):
                continue
            r, wr = add.rectangle(), win.rectangle()
            if wr.top < (r.top + r.bottom) // 2 < wr.bottom - 10:
                click(add)
            else:
                add.invoke()  # (below the bottom of the window)
            worst = 0.0
            end = time.time() + 1.0
            while time.time() < end:  # the moments right after the click
                img = ImageGrab.grab(bbox=(wr.left, wr.top, wr.right, wr.bottom), all_screens=True)
                worst = max(worst, sum(1 for v in img.convert("L").resize((80, 50)).getdata() if v > 245) / 4000)
            check(worst < 0.3, f"adding {tool.id} doesn't flash a white window (whitest moment {worst:.2f})")
            win = window()
            check(win is not None, f"after adding {tool.id} the window is there")
            if win is None:
                break
            check(shot(f"added-{tool.id}", win) < 0.3, f"after adding {tool.id} the window isn't white")
            nav = [t for t in win.descendants(control_type="ListItem") if t.window_text() == tool.nav]
            if check(bool(nav), f"{tool.id} is in the sidebar"):
                click(nav[0])
                check(shot(f"open-{tool.id}", win) < 0.3, f"{tool.id}'s page isn't white")
                store_row = [t for t in win.descendants(control_type="ListItem") if t.window_text() == "Store"]
                if store_row:
                    click(store_row[0])
                    time.sleep(0.8)
finally:
    log = Path(os.environ["LOCALAPPDATA"]) / "Corefold" / "corefold.log"
    if log.exists():
        (OUT / "exe-log.txt").write_text(log.read_text("utf-8", "replace"), "utf-8")
    proc.kill()
print(f"exetest: {len(problems)} problem(s)", flush=True)
