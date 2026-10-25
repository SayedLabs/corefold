"""Makes the pictures the README and the website show (docs/screenshots/), on a real Windows PC.

Used by .github/workflows/readme-shots.yml. Run: python scripts/readme_shots.py <folder> <en|ar|...> <dark|light>
Every picture is named <what>-<language>-<look>.png. It uses a fresh, empty settings folder with a
little made-up history (so the Home page isn't all zeros), so it never touches your own settings.
"""
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "readme-shots").resolve()
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
LOOK = sys.argv[3] if len(sys.argv) > 3 else "dark"
OUT.mkdir(parents=True, exist_ok=True)

TMP = Path(tempfile.mkdtemp(prefix="corefold-readme-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
HOME = TMP / "home"  # (a made-up user folder: the Cloud page finds the pretend cloud folders in it)
HOME.mkdir()
os.environ["USERPROFILE"] = os.environ["HOME"] = str(HOME)
for _name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
    os.environ.pop(_name, None)
sys.path.insert(0, str(ROOT))

import corefold  # noqa: E402

if corefold.__version__ == "dev":  # (the sidebar shows it: "Version dev" looks unfinished in a picture)
    corefold.__version__ = os.environ.get("COREFOLD_SHOW_VERSION", "1.11.0")

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

from corefold import settings as st  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.appearance = LOOK
s.language = LANG
s.check_updates = False
s.animations = False   # every picture shows the finished screen, not half of an animation
s.quick_told = True

# ---- made-up files for the Files tools to show: PDFs, an archive, a messy Downloads folder, cloud folders
FILES = TMP / "files"
FILES.mkdir()


def make_pdf(path: Path, title: str, colors: list[str]):
    pages = []
    for k, color in enumerate(colors):
        im = Image.new("RGB", (620, 820), "white")
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, 620, 150), fill=color)
        d.text((40, 60), f"{title}  {k + 1}", fill="white", font_size=40)
        for y in range(200, 760, 34):
            d.rounded_rectangle((40, y, 580 - (y * 37) % 160, y + 14), 6, fill="#d8dde3")
        if k % 2 == 0:
            d.rounded_rectangle((40, 470, 300, 680), 10, fill=color)
        pages.append(im)
    pages[0].save(path, save_all=True, append_images=pages[1:])


make_pdf(FILES / "Report 2026.pdf", "Report", ["#0a7768", "#127a9b", "#0a7768", "#5b4bb3", "#0a7768"])
make_pdf(FILES / "Invoice.pdf", "Invoice", ["#c2410c", "#b45309", "#c2410c"])

import zipfile  # noqa: E402

ARCHIVE = FILES / "Holiday photos.zip"
with zipfile.ZipFile(ARCHIVE, "w") as z:
    for n in ("Beach.jpg", "Old town.jpg", "Sunset.jpg", "Dinner.jpg"):
        z.writestr(f"Holiday photos/{n}", os.urandom(40_000))
    z.writestr("Holiday photos/Videos/Boat trip.mp4", os.urandom(90_000))
    z.writestr("Holiday photos/Plan.docx", os.urandom(12_000))
    z.writestr("Holiday photos/Tickets.pdf", os.urandom(20_000))
s.recent_archives = [str(ARCHIVE)]

MESS = HOME / "Downloads"
for n in ("IMG_2041.JPG", "Screenshot 2026-10-21 101533.png", "holiday.heic", "contract.pdf", "notes.txt",
          "budget.xlsx", "Presentation.pptx", "song.mp3", "voice memo.m4a", "clip.mp4", "setup-tool.exe",
          "photos.zip", "Cairo.ttf", "CV.docx"):
    (MESS / n).parent.mkdir(parents=True, exist_ok=True)
    (MESS / n).write_bytes(os.urandom(2000))
(HOME / "Desktop").mkdir()
(HOME / "Desktop" / "todo.txt").write_text("milk")


def cloud_folder(root: Path, label: str):
    (root / "Photos" / "2026").mkdir(parents=True, exist_ok=True)
    (root / "Photos" / "2026" / "beach.jpg").write_bytes(os.urandom(3000))
    (root / "Documents").mkdir()
    (root / f"{label} report.docx").write_bytes(os.urandom(5000))
    (root / "notes.txt").write_text("hello from " + label)


cloud_folder(HOME / "OneDrive", "OneDrive")
os.environ["OneDriveConsumer"] = str(HOME / "OneDrive")
cloud_folder(HOME / "Dropbox", "Dropbox")
(TMP / "local" / "Dropbox").mkdir(parents=True)
(TMP / "local" / "Dropbox" / "info.json").write_text(json.dumps({"personal": {
    "path": str(HOME / "Dropbox"), "host": 1, "is_team": False, "subscription_type": "Basic"}}))
(TMP / "local" / "Google" / "DriveFS").mkdir(parents=True)
cloud_folder(HOME / "My Drive", "Google")
cloud_folder(HOME / "iCloudDrive", "iCloud")

s.save()
st.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
(st.CONFIG_DIR / "history.json").write_text(json.dumps({
    "items": [], "stats": {"files": 1284, "jobs": 312, "bytes_saved": 7_340_032_000, "since": time.time() - 86400 * 60}}),
    "utf-8")

from PySide6.QtCore import QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPixmap, QRadialGradient  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from corefold import i18n  # noqa: E402

i18n.set_language(LANG)
qapp = QApplication(sys.argv)
if i18n.is_rtl():
    qapp.setLayoutDirection(Qt.RightToLeft)

from corefold import app as A  # noqa: E402
from corefold.i18n import tr  # noqa: E402

c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
DARK = c.theme.dark
problems = []
RAW = TMP / "raw"
RAW.mkdir()


def settle(seconds: float = 0.3):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)


def name(what: str) -> Path:
    return OUT / f"{what}-{LANG}-{LOOK}.png"


def step(what: str, fn):
    try:
        fn()
        print(f"ok    {what}", flush=True)
    except Exception:
        problems.append(f"{what}: {traceback.format_exc()}")
        print(f"FAIL  {what}", flush=True)


# ---- framing: rounded corners and a soft shadow, on a see-through background (fits a light or dark page)

def framed(src: Path, dest: Path, radius: int = 14, margin: int = 36):
    im = Image.open(src).convert("RGBA")
    w, h = im.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius, fill=255)
    im.putalpha(mask)
    canvas = Image.new("RGBA", (w + 2 * margin, h + 2 * margin), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((margin, margin + 10, margin + w, margin + h + 10), radius,
                                            fill=(0, 0, 0, 120 if DARK else 70))
    shadow = shadow.filter(ImageFilter.GaussianBlur(16))
    canvas.alpha_composite(shadow)
    edge = Image.new("RGBA", canvas.size, (0, 0, 0, 0))  # a hairline, so a light window shows on a white page
    ImageDraw.Draw(edge).rounded_rectangle((margin - 1, margin - 1, margin + w, margin + h), radius + 1,
                                          outline=(255, 255, 255, 28) if DARK else (0, 0, 0, 34), width=1)
    canvas.alpha_composite(edge)
    canvas.alpha_composite(im, (margin, margin))
    canvas.save(dest, optimize=True)


def grab(widget, what: str, wait: float = 0.6):
    settle(wait)
    raw = RAW / f"{what}.png"
    widget.grab().save(str(raw))
    framed(raw, name(what))


# ---- a calm background for the wheel

def paint_backdrop(p: QPainter, r: QRect):
    g = QLinearGradient(QPointF(r.topLeft()), QPointF(r.bottomRight()))
    if DARK:
        g.setColorAt(0, QColor("#0d1b1e"))
        g.setColorAt(0.55, QColor("#12302f"))
        g.setColorAt(1, QColor("#1b2140"))
    else:
        g.setColorAt(0, QColor("#dff3ef"))
        g.setColorAt(0.55, QColor("#cfe6f3"))
        g.setColorAt(1, QColor("#e6defa"))
    p.fillRect(r, g)
    glow = QRadialGradient(QPointF(r.width() * 0.25, r.height() * 0.2), r.width() * 0.6)
    glow.setColorAt(0, QColor(60, 199, 178, 70 if DARK else 90))
    glow.setColorAt(1, QColor(60, 199, 178, 0))
    p.fillRect(r, glow)


# ---- the main window

w = c.window
w.resize(1100, 720)
w.show()
settle(1.2)


def home():
    w.open_page("Home")
    grab(w, "home", 1.0)


def search():
    w.open_page("Home")
    settle(0.3)
    w.open_palette()
    settle(0.3)
    w.palette.search.setText(tr("Converter")[:4])
    grab(w, "search", 0.5)
    w.palette.close_palette()
    settle(0.3)


def page(page_name: str, what: str, wait: float = 0.8):
    def run():
        w.open_page(page_name)
        grab(w, what, wait)
    return run


def pdf_board():
    w.open_page("PDF")
    settle(0.5)
    pg = w.pdf_page
    pg.open_board([FILES / "Report 2026.pdf", FILES / "Invoice.pdf"])
    end = time.time() + 30
    while (pg.reading or pg.pending or len(pg.pixmaps) < 8) and time.time() < end:
        settle(0.3)
    grab(w, "pdf", 0.8)
    pg.close_board(ask=False)


def organizer():
    from corefold import organizerpage as OP
    OP.known_folder = lambda key: {"downloads": MESS, "desktop": HOME / "Desktop"}.get(key)
    w.open_page("Organizer")
    settle(0.3)
    pg = w.organizer_page
    end = time.time() + 30
    while (pg.loading or pg.plan is None) and time.time() < end:
        settle(0.3)
    grab(w, "organizer", 0.8)


def cloud():
    w.resize(1100, 720)
    w.open_page("Cloud")
    end = time.time() + 20
    while time.time() < end:
        pg = getattr(w, "cloud_page", None)
        if pg is not None and pg.found is not None and pg.files.topLevelItemCount():
            break
        settle(0.3)
    grab(w, "cloud", 1.0)


def archive_window():  # what's inside an archive, in its own window
    c.open_browser(ARCHIVE)
    end = time.time() + 20
    while not [b for b in c.browsers if b.isVisible()] and time.time() < end:
        settle(0.3)
    b = next(b for b in c.browsers if b.isVisible())
    b.resize(980, 620)
    grab(b, "archive-window", 1.0)
    b.close()
    settle(0.3)


step("home", home)
step("search", search)
step("store", page("Files", "store"))
step("pdf", pdf_board)
step("archive", page("Archives", "archive"))
step("archive-window", archive_window)
step("organizer", organizer)
step("unlock", page("Unlock", "unlock"))
step("cloud", cloud)
step("resting", page("Computer", "resting"))  # a section resting for now: what's coming back
step("look", page("Look", "look"))
w.hide()
settle(0.3)

# ---- the Converter wheel, around a photo being dragged


def wheel():
    from corefold.converters import catalog
    from corefold.settings import WHEEL_SIZES
    from corefold.wheel import POP, Item, Look, paint_wheel
    tools = catalog()["image"]
    items = [Item(t.label, True, False, t.key.split(":")[1]) for t in tools[:9]]
    R = WHEEL_SIZES["large"]
    W, H = 760, 560
    pm = QPixmap(W, H)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    paint_backdrop(p, pm.rect())
    paint_wheel(p, QPointF(W / 2, H / 2), R, items, 1, "holiday-photo.jpg",
                tr("Convert to {format}", format="JPG"), c.theme, Look("ring", "both", "fill", "plain", 1.0),
                "", 0, (0, 1))
    p.end()
    raw = RAW / "wheel.png"
    pm.save(str(raw))
    framed(raw, name("wheel"), radius=16)
    assert R + POP < H / 2


step("wheel", wheel)

(OUT / f"problems-{LANG}-{LOOK}.txt").write_text("\n\n".join(problems) or "none", "utf-8")
print(f"{LANG}-{LOOK}: {len(list(OUT.glob(f'*-{LANG}-{LOOK}.png')))} pictures, {len(problems)} problem(s)")
for line in problems:
    print(line)
