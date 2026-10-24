"""Capture's save folder, tried in the real window: Settings → Capture shows where screenshots go,
Change… picks another folder, screenshots land there (made when it isn't there yet), Open opens it, the
Capture page lists what's in it, Discard / Save / Reset work, and "Use the usual folder" goes back to
Pictures\\Screenshots. Also: settings from before recording was taken out of Capture still load.

Run: python scripts/capfoldertest.py <folder> [en|ar|fr|es|tr] [dark|light]
Prints one line per check and "capfoldertest: N problem(s)"; pictures of the settings page go in the folder."""
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "capfoldertest").resolve()
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
LOOK = sys.argv[3] if len(sys.argv) > 3 else "dark"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-capfoldertest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
if os.name != "nt":  # (a quick try on Linux: Windows' parts are pretend ones)
    import ctypes
    from unittest import mock
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    ctypes.windll = mock.MagicMock()
    for name in ("winreg", "winsound"):
        sys.modules[name] = mock.MagicMock()
    import corefold.shellmenu as _sm
    _sm.write = _sm.remove = mock.MagicMock()
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(600, exit=True)

from corefold import settings as st  # noqa: E402
from corefold import tools as all_tools  # noqa: E402

import json  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.check_updates = False
s.language = LANG
s.appearance = LOOK
s.known_tools = sorted(t.id for t in all_tools.ready())
s.save()
# settings from 1.8.0, when Capture still recorded videos
_old = json.loads((st.CONFIG_DIR / "settings.json").read_text("utf-8"))
_old.update({"cap_record_shortcut": "win+shift+r", "cap_fps": 60, "cap_mic": "Mic (USB)", "cap_pc_sound": False,
             "cap_cursor": False, "cap_countdown": False,
             "quick_menu_items": ["screenshot", "record", "clipboard", "record_gif"],
             "quick_wheel_items": ["record", "screenshot"]})
(st.CONFIG_DIR / "settings.json").write_text(json.dumps(_old), "utf-8")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton  # noqa: E402

from corefold import i18n  # noqa: E402

i18n.set_language(LANG)
qapp = QApplication.instance() or QApplication(sys.argv)
qapp.setQuitOnLastWindowClosed(False)
if i18n.is_rtl():
    qapp.setLayoutDirection(Qt.RightToLeft)
problems = []
errors = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)


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


def until(cond, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.05)
    return False


opened = []
QDesktopServices.openUrl = staticmethod(lambda url: opened.append(Path(url.toLocalFile())) or True)
answer = {"folder": ""}
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: answer["folder"])


def click(win, text: str, nth: int = 0):
    """Clicks the nth button with this text on the page that's showing."""
    page = win.stack.currentWidget() if hasattr(win, "stack") else win
    found = [b for b in page.findChildren(QPushButton) if b.text() == text and b.isVisible()]
    if len(found) <= nth:
        check(False, f"there's a button {text!r} ({len(found)} found)")
        return False
    found[nth].click()
    settle(0.2)
    return True


def main():
    from corefold import app as A
    from corefold.capture import default_screenshots_folder, screenshots_folder
    from corefold.i18n import tr
    c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
    check(st.ACTIVE is c.settings, "the app's settings are the ones Capture reads")
    check(c.settings.quick_menu_items == ["screenshot", "clipboard"] and c.settings.quick_wheel_items == ["screenshot"],
          f"settings from 1.8.0 load; recording is gone from the quick menu and wheel ({c.settings.quick_menu_items})")
    c.settings.save()
    saved = json.loads((st.CONFIG_DIR / "settings.json").read_text("utf-8"))
    check(not any(k in saved for k in ("cap_record_shortcut", "cap_fps", "cap_mic")), "…and the recording settings "
          "aren't kept")
    until(lambda: c.capture is not None, 10)
    cap = c.capture
    check(cap is not None and (cap.shot_key.ok or os.name != "nt"), "Capture is on and its shortcut works")
    check(not hasattr(cap, "record") and not hasattr(cap, "rec_key"), "Capture has no recording any more")
    check(screenshots_folder() == default_screenshots_folder(), f"screenshots go to the usual folder to begin with "
          f"({screenshots_folder()})")
    c.show_window("CaptureSettings")
    settle(0.8)
    win = c._window
    check(win.cap_folder_label.toolTip() == str(default_screenshots_folder()), "the page shows where screenshots go")
    check(not win.cap_folder_reset.isVisible(), "…with no “Use the usual folder” while it's the usual one")
    win.grab().save(str(OUT / f"{LANG}-{LOOK}-1-settings.png"))

    pics = TMP / "My shots" / "new"   # (not there yet: made when something is saved)
    answer["folder"] = str(pics)
    click(win, tr("Change…"))
    check(c.settings.cap_folder == os.path.normpath(str(pics)), "Change… picks the screenshots folder")
    check(win.cap_folder_label.toolTip() == str(pics) and win.cap_folder_reset.isVisible(),
          "the page shows the new folder, and “Use the usual folder”")
    win.grab().save(str(OUT / f"{LANG}-{LOOK}-2-settings-changed.png"))

    # a screenshot lands there
    img = QImage(320, 200, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor("#3cc7b2"))
    c.settings.cap_after = "copy"
    cap._picked(img, QRect(0, 0, 320, 200))
    until(lambda: list(pics.glob("Screenshot *.png")), 10)  # (written on a thread)
    shots = list(pics.glob("Screenshot *.png"))
    check(len(shots) == 1, f"a screenshot is saved in the picked folder ({[p.name for p in shots]})")
    from corefold.capview import CaptureEditor
    ed = CaptureEditor(c, img)
    ed.save()
    check(until(lambda: ed.saved is not None, 10) and ed.saved.parent == pics, "the editor's Save uses it too")
    ed.close()
    opened.clear()
    click(win, tr("Open"))
    check(opened and opened[-1] == pics, f"Open opens the screenshots folder ({opened[-1:]})")

    # the Capture page lists what's in the new folder
    c.show_window("Capture")
    settle(0.6)
    win = c._window
    rows = [b.toolTip() for b in win.findChildren(QPushButton) if b.toolTip().startswith(str(TMP))]
    check(len(rows) == 2 and all(r.startswith(str(pics)) for r in rows),
          f"the Capture page lists the screenshots in it ({len(rows)})")
    win.grab().save(str(OUT / f"{LANG}-{LOOK}-3-capture-page.png"))

    # back to the usual folder
    c.show_window("CaptureSettings")
    settle(0.6)
    win = c._window
    click(win, tr("Use the usual folder"))
    check(c.settings.cap_folder == "" and screenshots_folder() == default_screenshots_folder(),
          "“Use the usual folder” goes back to Pictures\\Screenshots")
    answer["folder"] = str(default_screenshots_folder())
    click(win, tr("Change…"))
    check(c.settings.cap_folder == "", "picking the usual folder by hand keeps it as “usual”")
    # Discard, Save, Reset
    answer["folder"] = str(pics)
    click(win, tr("Change…"))
    check(win.save_btn.isVisible(), "a picked folder waits for Save like other settings")
    win.discard_changes()
    settle(0.6)
    win = c._window
    check(c.settings.cap_folder == "" and win.cap_folder_label.toolTip() == str(default_screenshots_folder()),
          "Discard puts the folder back")
    click(win, tr("Change…"))
    win.save_changes()
    again = st.Settings.load()
    check(again.cap_folder == os.path.normpath(str(pics)), "after Save, the picked folder is remembered after a restart")
    win.reset_to_defaults(st.CAPTURE_KEYS)
    settle(0.6)
    check(c.settings.cap_folder == "", "Reset puts the usual folder back")
    finish()


def finish():
    check(not errors, f"no errors ({len(errors)})")
    print(f"capfoldertest: {len(problems)} problem(s)", flush=True)
    os._exit(1 if problems else 0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        print("capfoldertest: 1 problem(s)", flush=True)
        os._exit(1)
