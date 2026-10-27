"""TEMPORARY (pre-launch look, removed before merging): pictures of the window around the tools, as people get it
(resting sections resting), in one language and look. Run: python scripts/shellshots.py <root> <out> <lang> <look>"""
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
LANG, LOOK = sys.argv[3], sys.argv[4]
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-shell-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
os.environ.pop("COREFOLD_WAKE_RESTING", None)
sys.path.insert(0, str(ROOT))
from corefold import settings as st  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.appearance = LOOK
s.language = LANG
s.check_updates = False
s.save()
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from corefold import i18n  # noqa: E402

i18n.set_language(LANG)
qapp = QApplication(sys.argv)
if i18n.is_rtl():
    qapp.setLayoutDirection(Qt.RightToLeft)
from corefold import app as A  # noqa: E402

c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))


def settle(t=0.4):
    end = time.time() + t
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)


w = c.window
w.resize(1040, 700)
w.show()
settle(2.0)
for page in ("Home", "Files", "General", "Look", "ArchiveSettings", "Shortcuts", "Updates", "About"):
    try:
        w.open_page(page)
        settle(1.0)
        w.grab().save(str(OUT / f"{LANG}-{LOOK}-{page}.png"))
    except Exception:
        traceback.print_exc()
try:
    c.menu.popup(w.mapToGlobal(w.rect().center()))
    settle(0.8)
    c.menu.grab().save(str(OUT / f"{LANG}-{LOOK}-tray.png"))
    c.menu.hide()
except Exception:
    traceback.print_exc()
print("shellshots", LANG, LOOK, "done")
sys.stdout.flush()
os._exit(0)
