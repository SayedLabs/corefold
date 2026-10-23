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
sys.path.insert(0, str(ROOT))

import corefold  # noqa: E402

if corefold.__version__ == "dev":  # (the sidebar shows it: "Version dev" looks unfinished in a picture)
    corefold.__version__ = os.environ.get("COREFOLD_SHOW_VERSION", "1.8.0")

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
s.quick_links = [{"id": "docs", "name": "Documents", "target": str(Path.home() / "Documents")},
                 {"id": "web", "name": "GitHub", "target": "https://github.com"}]
s.save()
st.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
(st.CONFIG_DIR / "history.json").write_text(json.dumps({
    "items": [], "stats": {"files": 1284, "jobs": 312, "bytes_saved": 7_340_032_000, "since": time.time() - 86400 * 60}}),
    "utf-8")

from PySide6.QtCore import QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QLinearGradient, QPainter, QPixmap, QRadialGradient  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

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


# ---- a calm desktop to show the overlays on (instead of the test PC's own windows)

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


class Backdrop(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setGeometry(QGuiApplication.primaryScreen().geometry())

    def paintEvent(self, _e):
        p = QPainter(self)
        paint_backdrop(p, self.rect())
        p.end()


def screen_crop(what: str, box: QRect):
    settle(0.6)
    pm = QGuiApplication.primaryScreen().grabWindow(0, box.x(), box.y(), box.width(), box.height())
    raw = RAW / f"{what}.png"
    pm.save(str(raw))
    framed(raw, name(what), radius=16)


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


step("home", home)
step("search", search)
step("store", page("Store", "store"))
step("apps", page("Apps", "apps", 15.0))


def space_map():  # a real map, of a folder every PC has
    w.open_page("Space")
    settle(0.5)
    sp = w.stack.currentWidget()
    sp.start_scan(Path(os.environ.get("ProgramFiles(x86)", sys.prefix)))
    end = time.time() + 300
    while sp.job is not None and time.time() < end:
        settle(0.5)
    if sp.job is not None:
        sp._stop()
    grab(w, "space", 3.0)


step("space", space_map)
step("viewer", page("Viewer", "viewer"))
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

# ---- the quick menu and the quick wheel, over the calm desktop

if os.name == "nt" and c.quick is not None:
    back = Backdrop()
    back.show()
    settle(0.5)
    geo = QGuiApplication.primaryScreen().geometry()

    def quick_menu():
        c.quick.toggle_menu()
        settle(1.0)
        screen_crop("quick-menu", geo)
        c.quick.menu.hide()

    def quick_wheel():
        center = geo.center()
        c.quick.open_wheel_at(center)
        settle(0.6)
        import ctypes  # (the wheel lights up the slice under the mouse: point at the top one)
        side = c.quick.wheel.width()
        ctypes.windll.user32.SetCursorPos(center.x(), center.y() - side // 3)
        settle(0.6)
        half = side // 2 + 60
        screen_crop("quick-wheel", QRect(center.x() - half, center.y() - half, 2 * half, 2 * half))
        c.quick.wheel.hide()

    step("quick-menu", quick_menu)
    step("quick-wheel", quick_wheel)
    back.hide()

(OUT / f"problems-{LANG}-{LOOK}.txt").write_text("\n\n".join(problems) or "none", "utf-8")
print(f"{LANG}-{LOOK}: {len(list(OUT.glob(f'*-{LANG}-{LOOK}.png')))} pictures, {len(problems)} problem(s)")
for line in problems:
    print(line)
