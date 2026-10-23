"""Saves a picture of every screen, so the design can be checked on a real Windows PC.

Used by .github/workflows/screenshots.yml. Run: python scripts/screenshots.py <folder> [dark|light] [en|ar|...]
It uses a fresh, empty settings folder, so it never touches your own settings.
"""
import os
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "screenshots").resolve()
APPEARANCE = sys.argv[2] if len(sys.argv) > 2 else "dark"
LANG = sys.argv[3] if len(sys.argv) > 3 else "en"
PREFIX = f"{LANG}-{APPEARANCE}"
OUT.mkdir(parents=True, exist_ok=True)

TMP = Path(tempfile.mkdtemp(prefix="corefold-shots-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))

import faulthandler  # noqa: E402

_log = open(OUT / f"{LANG}-{APPEARANCE}-log.txt", "w", encoding="utf-8")
faulthandler.enable(_log)
sys.excepthook = lambda t, e, tb: (_log.write("".join(traceback.format_exception(t, e, tb))), _log.flush())

from PIL import Image, ImageDraw  # noqa: E402

from corefold import settings as st  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.appearance = APPEARANCE
s.language = LANG
s.check_updates = False
s.save()

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from corefold import i18n  # noqa: E402

i18n.set_language(LANG)
qapp = QApplication(sys.argv)
if i18n.is_rtl():
    qapp.setLayoutDirection(Qt.RightToLeft)

from corefold import app as A  # noqa: E402
from corefold.i18n import tr  # noqa: E402

icon = QIcon(str(ROOT / "assets" / "icon.png"))
c = A.Controller(qapp, icon)
problems = []


def settle(seconds: float = 0.25):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)


def save(widget, name: str, wait: float = 0.3):
    try:
        settle(wait)
        widget.grab().save(str(OUT / f"{PREFIX}-{name}.png"))
    except Exception:
        problems.append(f"{name}: {traceback.format_exc()}")


# sample files the screens can show
photo = TMP / "holiday-photo.jpg"
img = Image.new("RGB", (1200, 800), (60, 110, 140))
d = ImageDraw.Draw(img)
d.rectangle((0, 520, 1200, 800), fill=(30, 70, 90))
d.ellipse((820, 120, 960, 260), fill=(250, 235, 190))
img.save(photo)
archive = TMP / "documents.zip"
with zipfile.ZipFile(archive, "w") as z:
    for i in range(12):
        z.writestr(f"Reports/report-{i + 1}.pdf", "x" * (1000 * (i + 1)))
    z.writestr("photo.jpg", photo.read_bytes())
    z.writestr("notes.txt", "hello")

# ---- the main window, every page
w = c.window
w.resize(1100, 720)
w.show()
settle(1.0)
from corefold.window import PAGES  # noqa: E402

for i, page in enumerate(PAGES):
    w.open_page(page)
    save(w, f"window-{i}-{page.lower()}", wait=6.0 if page == "Apps" else 0.6)
w.open_page("Home")
try:
    w.show_files([photo])
except Exception:
    problems.append(f"show_files: {traceback.format_exc()}")
save(w, "window-0-home-with-file", wait=2.0)
try:  # the search (Ctrl + K): everything, then a few letters typed
    w.open_page("Home")
    settle(0.3)
    w.open_palette()
    save(w, "window-search", wait=0.5)
    w.palette.search.setText("so")
    save(w, "window-search-typed", wait=0.4)
    w.palette.close_palette()
except Exception:
    problems.append(f"search: {traceback.format_exc()}")
try:  # the sidebar with icons only
    w.set_nav_compact(True)
    w.open_page("Space")
    save(w, "window-sidebar-icons-only", wait=1.0)
    w.set_nav_compact(False)
    settle(0.5)
except Exception:
    problems.append(f"sidebar: {traceback.format_exc()}")

# ---- the wheel, in every style
from corefold.converters import catalog  # noqa: E402
from corefold.settings import WHEEL_SIZES, WHEEL_STYLES  # noqa: E402
from corefold.wheel import POP, Item, Look, paint_wheel  # noqa: E402

tools = catalog()["image"]
items = [Item(t.label, True, False, t.key.split(":")[1]) for t in tools[:9]]
R = WHEEL_SIZES["medium"]
for style in WHEEL_STYLES:
    for labels in ("both", "text"):
        try:
            side = int(2 * (R + POP) + 30)
            pm = QPixmap(side, side)
            pm.fill(QColor("#202124") if c.theme.dark else QColor("#d9dde0"))
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.TextAntialiasing)
            paint_wheel(p, QPointF(side / 2, side / 2), R, items, 1, "holiday-photo.jpg",
                        tr("Convert to {format}", format="JPG"), c.theme, Look(style, labels, "fill", "plain", 1.0),
                        "", 0, (0, 1))
            p.end()
            pm.save(str(OUT / f"{PREFIX}-wheel-{style}-{labels}.png"))
        except Exception:
            problems.append(f"wheel {style}: {traceback.format_exc()}")

# ---- popups
try:
    c.toast.start(1, tr("Convert to PNG"), "holiday-photo.jpg")
    save(c.toast, "toast-progress")
    c.toast.finish(1, tr("Done"), "holiday-photo.png · 3.2 MB → 410 KB", photo, 60000)
    save(c.toast, "toast-done")
    c.toast.hide()
except Exception:
    problems.append(f"toast: {traceback.format_exc()}")

try:
    c.menu.popup(c.menu.pos())
    save(c.menu, "tray-menu")
    c.menu.hide()
except Exception:
    problems.append(f"tray menu: {traceback.format_exc()}")

try:
    from corefold.tour import Tour
    t = Tour(icon, c.settings.shortcut_label, c.theme.accent)
    t.show()
    save(t, "tour")
    t.close()
except Exception:
    problems.append(f"tour: {traceback.format_exc()}")

# ---- the small windows (Resize…, Password…, …)
from corefold import dialogs  # noqa: E402
from corefold.dialogs2 import register  # noqa: E402

register(dialogs.DIALOGS)
extras = {"rename": {"ext": "png"}}
for kind, cls in dialogs.DIALOGS.items():
    try:
        dlg = cls([photo], icon, c.theme.dark, **extras.get(kind, {}))
        dlg.show()
        save(dlg, f"dialog-{kind}", wait=0.8)
        dlg.close()
        dlg.deleteLater()
    except Exception:
        problems.append(f"dialog {kind}: {traceback.format_exc().splitlines()[-1]}")

# ---- the archive window
try:
    from corefold import archives
    from corefold.browser import ArchiveBrowser
    b = ArchiveBrowser(c, archive, "", archives.list_entries(archive, ""), icon)
    b.show()
    save(b, "archive-window", wait=1.0)
    b.close()
except Exception:
    problems.append(f"archive window: {traceback.format_exc()}")

(OUT / f"{PREFIX}-problems.txt").write_text("\n\n".join(problems) or "none", "utf-8")
print(f"{PREFIX}: {len(list(OUT.glob(PREFIX + '-*.png')))} pictures, {len(problems)} problem(s)")
for line in problems:
    print(line)
import faulthandler  # noqa: E402
faulthandler.disable()  # (a quick exit isn't a crash worth reporting)
os._exit(0)  # don't wait for the tray and timers
