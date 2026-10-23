"""Clicks through the newer tools' windows with the real Qt on a real Windows PC (the Viewer, emails,
the Store) and saves pictures of them.

Used by .github/workflows/screenshots.yml. Run: python scripts/uitest.py <folder>
Prints one line per check and "uitest: N problem(s)"."""
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "uitest").resolve()
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-uitest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from corefold import settings as st  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.check_updates = False
s.known_tools = ["converter", "uninstaller", "archive", "viewer"]
s.viewer_confirm_delete = False
s.save()

from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402

qapp = QApplication(sys.argv)
from corefold import app as A  # noqa: E402

c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
problems = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)


def settle(seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)


def until(cond, seconds=10):
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


def viewer():
    folder = TMP / "Holiday"
    folder.mkdir()
    for i, color in enumerate(((220, 60, 60), (60, 160, 90), (60, 90, 220))):
        im = Image.new("RGB", (900, 600), color)
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, 449, 599), fill=(255, 255, 255))  # white left half
        d.text((470, 280), f"photo {i + 1}", fill=(0, 0, 0))
        im.save(folder / f"photo-{i + 1}.jpg", quality=92)
    c.open_viewer(folder / "photo-2.jpg")
    win = c.viewers[-1]
    check(type(win).__name__ == "ViewerWindow", "a picture opens in the Viewer")
    check(until(lambda: not win.view.image.isNull()), "…and shows up")
    check(win.counter.text().startswith("2"), f"…as 2 of 3 ({win.counter.text()})")
    win.resize(1100, 720)
    settle(0.5)
    win.grab().save(str(OUT / "viewer-window.png"))
    t0 = time.time()
    win.go(1)
    check(until(lambda: not win.view.image.isNull()) and time.time() - t0 < 1.0, f"next picture fast ({time.time() - t0:.2f} s)")
    check(win.current().name == "photo-3.jpg", "→ goes to the next one")
    win.rotate(90)
    img = win.view.image
    check(img.width() == 600 and img.height() == 900, "turned right on screen")
    top = img.pixelColor(300, 20)
    check(top.red() > 200 and top.green() > 200, f"…the white half is on top ({top.name()})")
    check(win.b_save.isVisible(), "Save shows up after a change")
    check(win.save(), "Save works")
    with Image.open(folder / "photo-3.jpg") as saved:
        px = saved.convert("RGB").getpixel((300, 20))
        check(saved.size == (600, 900) and px[0] > 200 and px[1] > 200, f"the file is turned the same way {saved.size} {px}")
    win.view.start_crop()
    win._crop_ratio("square")
    win.apply_crop()
    check(win.view.image.width() == win.view.image.height(), "square crop")
    win.edits = []  # (not saving the crop)
    win.show_current()
    until(lambda: not win.view.image.isNull())
    win.toggle_info()
    settle(0.3)
    win.grab().save(str(OUT / "viewer-details.png"))
    check(win.info.isVisible(), "details panel")
    win.toggle_info()
    before = len(win.files)
    win.delete()
    check(len(win.files) == before - 1 and not (folder / "photo-3.jpg").exists(), "Delete sends it to the Recycle Bin")
    win.close()
    settle()


def email():
    from email.message import EmailMessage
    e = EmailMessage()
    e["Subject"] = "Quarterly report"
    e["From"] = "Sara <sara@example.com>"
    e["To"] = "team@example.com"
    e["Date"] = "Thu, 22 Oct 2026 10:00:00 +0300"
    e.set_content("Hello team")
    e.add_alternative('<h2>Hello team</h2><p>Here is the report. <img src="cid:logo1"> '
                      '<img src="https://tracker.example.com/pixel.png"></p><p><a href="https://example.com/real">'
                      'example</a></p><script>alert(1)</script>', subtype="html")
    logo = TMP / "logo.png"
    Image.new("RGB", (60, 30), (10, 120, 100)).save(logo)
    e.get_payload()[1].add_related(logo.read_bytes(), maintype="image", subtype="png", cid="<logo1>")
    e.add_attachment(b"%PDF-1.4\n%fake", maintype="application", subtype="pdf", filename="report.pdf")
    path = TMP / "Quarterly report.eml"
    path.write_bytes(bytes(e))
    c.open_viewer(path)
    win = c.viewers[-1]
    check(type(win).__name__ == "MailWindow", "an email opens in its window")
    text = win.body.toPlainText()
    check("Here is the report" in text and "alert" not in text, "the email shows, without its script")
    check(win.body.blocked >= 1 and win.note.isVisible(), "pictures from the internet are blocked")
    check(len(win.m.visible_attachments()) == 1, "one attachment (the logo is part of the email)")
    win.resize(900, 700)
    win.show()
    settle(0.6)
    win.grab().save(str(OUT / "mail-window.png"))
    pdf = TMP / "report.pdf"
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(pdf), ""))
    win.save_pdf()
    check(pdf.exists() and pdf.stat().st_size > 1000, f"Save as PDF ({pdf.stat().st_size if pdf.exists() else 0} bytes)")
    win.close()
    settle()


def store():
    c.show_window("Store")
    w = c.window
    w.resize(1100, 720)
    settle(0.5)
    w.grab().save(str(OUT / "store.png"))
    c.set_tool("viewer", False)
    check("Viewer" not in c.window.nav_rows, "removing the Viewer")
    c.set_tool("viewer", True)
    check("Viewer" in c.window.nav_rows, "adding it back")
    c.window.open_page("Viewer")
    settle(0.5)
    c.window.grab().save(str(OUT / "viewer-page.png"))
    c.window.open_page("ViewerSettings")
    settle(0.3)
    c.window.grab().save(str(OUT / "viewer-settings.png"))


for name, fn in (("viewer", viewer), ("email", email), ("store", store)):
    step(name, fn)
(OUT / "uitest.txt").write_text("\n".join(problems) or "none", "utf-8")
print(f"uitest: {len(problems)} problem(s)", flush=True)
import faulthandler  # noqa: E402
faulthandler.disable()
os._exit(0)
