"""Walks through Corefold the way someone new to it would, and saves a picture of every step.

The first start (language, which tools, the tour), Home, the Files section and each Files tool: first empty,
then with a file in it (a photo in the Converter, a PDF on the board, a ZIP opened, a messy Downloads folder,
a file to unlock, a cloud folder). Each step is timed; one that takes too long is marked SLOW.

Run:  python scripts/newusertest.py <folder> [en|ar|...] [dark|light] [--root <a copy of Corefold>]
--root walks through another copy (a "before" picture next to the "after" one). It uses a fresh, empty
settings folder and a made-up home folder, so it never touches your own files or settings.
"""
import os
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

args = [a for a in sys.argv[1:]]
root_at = args.index("--root") if "--root" in args else -1
ROOT = Path(args[root_at + 1]).resolve() if root_at >= 0 else Path(__file__).resolve().parent.parent
if root_at >= 0:
    del args[root_at:root_at + 2]
OUT = Path(args[0] if args else "newuser").resolve()
LANG = args[1] if len(args) > 1 else "en"
LOOK = args[2] if len(args) > 2 else "dark"
P = f"{LANG}-{LOOK}"
OUT.mkdir(parents=True, exist_ok=True)

TMP = Path(tempfile.mkdtemp(prefix="corefold-newuser-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
os.environ.setdefault("COREFOLD_WAKE_ORGANIZER", "1")  # (File Organizer rests in a built Corefold: its kept code is tried here)

import faulthandler  # noqa: E402

_log = open(OUT / f"{P}-log.txt", "w", encoding="utf-8")
faulthandler.enable(_log)

from corefold import settings as st  # noqa: E402

s = st.Settings()
s.appearance = LOOK
s.language = LANG
s.check_updates = False
s.save()
from corefold import tools as all_tools  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont, QFontMetrics, QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from corefold import i18n  # noqa: E402

i18n.set_language(LANG)
qapp = QApplication(sys.argv)
if i18n.is_rtl():
    qapp.setLayoutDirection(Qt.RightToLeft)
icon = QIcon(str(ROOT / "assets" / "icon.png"))
problems: list[str] = []
times: list[tuple[str, float, float]] = []

# A PC that just started waits for Windows to read its fonts the first time text is drawn (any app does, once):
# done here, so the steps below don't count it.
from corefold.atomic import tokens as _tokens  # noqa: E402

for _fam in {_tokens.TEXT_FAMILY, _tokens.FONT_DISPLAY.strip('"')}:
    for _bold in (False, True):
        _f = QFont(_fam, 11)
        _f.setBold(_bold)
        QFontMetrics(_f).horizontalAdvance("Corefold مرحبا 0123")


def settle(seconds: float = 0.3):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)


def save(widget, name: str, wait: float = 0.4):
    try:
        settle(wait)
        widget.grab().save(str(OUT / f"{P}-{name}.png"))
    except Exception:
        problems.append(f"{name}: {traceback.format_exc()}")


def timed(what: str, fn, limit: float, done=None, most: float = 20.0):
    """fn, then until done() (if given); its time against limit (seconds)."""
    t0 = time.perf_counter()
    try:
        fn()
        qapp.processEvents()
        if done is not None:
            end = time.time() + most
            while not done() and time.time() < end:
                qapp.processEvents()
                time.sleep(0.005)
    except Exception:
        problems.append(f"{what}: {traceback.format_exc()}")
    times.append((what, time.perf_counter() - t0, limit))


def step(name: str, fn):
    try:
        fn()
    except Exception:
        problems.append(f"{name}: {traceback.format_exc()}")


# ---- the first start: the language, which tools, the short tour
from corefold import welcome  # noqa: E402

welcome._style(qapp, s)
picker = welcome.LanguagePicker(icon, s, LANG)
picker.show()
save(picker, "00-language")
picker.close()

from corefold import app as A  # noqa: E402

s.first_run = False  # (what main() does on the first start: the tools offered now aren't "New")
s.known_tools = sorted({tool.id for tool in all_tools.available()})
s.save()
c = A.Controller(qapp, icon)
w = c.window
w.resize(1040, 700)
w.show()
settle(1.0)

from corefold.store import ToolPicker  # noqa: E402

tools_q = ToolPicker(icon, c.theme.dark)
tools_q.show()
save(tools_q, "01-which-tools")
tools_q.close()

from corefold.tour import Tour  # noqa: E402

tour = Tour(icon, c.settings.shortcut_label, c.theme.accent)
tour.show()
for i in range(tour.stack.count()):
    tour.go(i - tour.stack.currentIndex())
    save(tour, f"02-tour-{i + 1}", 0.25)
tour.close()


# ---- each page, empty, as it first opens
def open_page(name: str, shot: str, limit: float = 0.4):
    timed(f"open {name}", lambda: w.open_page(name), limit)
    save(w, shot, 0.6)


open_page("Home", "10-home")
open_page("Files", "11-files")
for i, name in enumerate(["Converter", "PDF", "Archives", "Organizer", "Unlock", "Cloud"]):
    open_page(name, f"2{i}-{name.lower()}")
for i, name in enumerate(["ConvOptions", "WheelLook", "Formats", "ArchiveSettings"]):
    open_page(name, f"3{i}-settings-{name.lower()}")

# ---- then with files in them
from PIL import Image, ImageDraw  # noqa: E402

H = TMP / "home"
for folder in ("Downloads", "Desktop", "OneDrive/Photos"):
    (H / folder).mkdir(parents=True, exist_ok=True)
photo = H / "holiday-photo.jpg"
img = Image.new("RGB", (1600, 1000), (60, 110, 140))
d = ImageDraw.Draw(img)
d.rectangle((0, 650, 1600, 1000), fill=(30, 70, 90))
d.ellipse((1100, 150, 1280, 330), fill=(250, 235, 190))
img.save(photo)
sheets = [Image.new("RGB", (595, 842), (255, 255, 255)) for _ in range(6)]
for i, sheet in enumerate(sheets):
    dd = ImageDraw.Draw(sheet)
    dd.text((60, 60), f"Page {i + 1}", fill=(0, 0, 0))
    for y in range(120, 760, 22):
        dd.line((60, y, 535 - (y * 7) % 160, y), fill=(150, 150, 150), width=3)
pdf = H / "Report 2026.pdf"
sheets[0].save(pdf, save_all=True, append_images=sheets[1:])
zipped = H / "documents.zip"
with zipfile.ZipFile(zipped, "w") as z:
    for i in range(12):
        z.writestr(f"Reports/report-{i + 1}.pdf", "x" * (1000 * (i + 1)))
    z.writestr("photo.jpg", photo.read_bytes())
    z.writestr("notes.txt", "hello")
for name in ("invoice.pdf", "setup.exe", "song.mp3", "IMG_2041.jpg", "IMG_2042.jpg", "notes.txt", "budget.xlsx",
             "clip.mp4", "old.zip", "letter.docx"):
    (H / "Downloads" / name).write_bytes(b"x" * 2000)
for name in ("beach.jpg", "family.jpg"):
    (H / "OneDrive" / "Photos" / name).write_bytes(photo.read_bytes())
(H / "OneDrive" / "CV.docx").write_bytes(b"x" * 30000)


def converter():
    w.open_page("Converter")
    settle(0.3)
    timed("Converter: a photo dropped (its options show)", lambda: w.show_files([photo]), 0.5)
    save(w, "40-converter-photo", 1.0)


def pdf_board():
    w.open_page("PDF")
    settle(0.3)
    pg = w.pdf_page
    timed("PDF: a 6-page PDF on the board", lambda: pg.open_board([pdf]), 1.0,
          lambda: not pg.reading and not pg.pending and pg.board.count() == 6 and len(pg.pixmaps) >= 6)
    save(w, "41-pdf-board", 1.0)
    pg.board.item(1).setSelected(True)
    save(w, "41-pdf-board-picked", 0.3)


def archive():
    w.open_page("Archives")
    settle(0.3)
    timed("Archive: a ZIP opened", lambda: c.open_browser(zipped), 1.5,
          lambda: any(b.isVisible() for b in c.browsers))
    b = next((b for b in c.browsers if b.isVisible()), None)
    if b is not None:
        b.resize(960, 600)
        save(b, "42-archive-window", 1.0)
        b.close()


def organizer():
    from corefold import organizerpage as OP
    OP.known_folder = lambda key: {"downloads": H / "Downloads", "desktop": H / "Desktop"}.get(key)
    w.open_page("Organizer")
    pg = w.organizer_page
    timed("Organizer: a messy Downloads planned", lambda: pg.set_folder(H / "Downloads"), 2.0,
          lambda: not pg.loading and pg.plan is not None)
    save(w, "43-organizer-plan", 0.8)


def unlock():
    w.open_page("Unlock")
    pg = w.unlock_page
    timed("Unlock: a file checked", lambda: pg.check([pdf]), 5.0, lambda: not getattr(pg, "busy", False))
    save(w, "44-unlock-result", 2.0)


def cloud():
    from corefold import cloudlocal
    cloudlocal.add_folder(H / "OneDrive")
    w.open_page("Cloud")
    pg = w.cloud_page
    pg.look_for_folders()
    timed("Cloud: a cloud folder listed", lambda: None, 5.0,
          lambda: pg.found is not None and pg.files.topLevelItemCount() >= 2)
    save(w, "45-cloud-folder", 1.0)


for name, fn in (("converter", converter), ("pdf", pdf_board), ("archive", archive), ("organizer", organizer),
                 ("unlock", unlock), ("cloud", cloud)):
    step(name, fn)

slow = [t for t in times if t[1] > t[2]]
lines = [f"{'SLOW' if took > limit else 'ok  '}  {what}: {took * 1000:.0f} ms (limit {limit * 1000:.0f})"
         for what, took, limit in times]
report = "\n".join(lines) + "\n\n" + ("\n\n".join(problems) or "no problems")
(OUT / f"{P}-times.txt").write_text(report, "utf-8")
print(f"{P}: {len(list(OUT.glob(P + '-*.png')))} pictures, {len(slow)} slow, {len(problems)} problem(s)")
print(report)
faulthandler.disable()
os._exit(0)  # (don't wait for the tray and its timers)
