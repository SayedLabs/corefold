"""Clicks through the newer tools' windows with the real Qt on a real Windows PC (the Viewer, emails,
the Clipboard, the Store) and saves pictures of them.

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
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(420, exit=True)  # stuck somewhere: say where, and stop

from PIL import Image, ImageDraw  # noqa: E402

from corefold import settings as st  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.check_updates = False
s.known_tools = ["converter", "uninstaller", "archive", "viewer", "clipboard", "space", "capture"]
s.viewer_confirm_delete = False
s.save()

from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402

qapp = QApplication(sys.argv)
from corefold import app as A  # noqa: E402

c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
problems = []
dialogs = []


def _close_dialogs():
    """A question nobody is there to answer would stop the checks: note it and close it."""
    from PySide6.QtWidgets import QMessageBox
    box = qapp.activeModalWidget()
    if box is not None:
        text = box.text() if isinstance(box, QMessageBox) else ""
        dialogs.append(f"{box.windowTitle()}: {text}")
        print(f"note  a dialog came up: {box.windowTitle()}: {text}", flush=True)
        box.reject()


from PySide6.QtCore import QTimer  # noqa: E402
_closer = QTimer()
_closer.timeout.connect(_close_dialogs)
_closer.start(1500)


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
    check(win.body.blocked >= 1 and not win.note.isHidden(), "pictures from the internet are blocked")
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


def copy_private(text):
    """Copies like a password manager does: marked so clipboard tools don't keep it."""
    import ctypes
    from ctypes import wintypes as wt
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    k.GlobalAlloc.restype = wt.HGLOBAL
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [wt.HGLOBAL]
    k.GlobalUnlock.argtypes = [wt.HGLOBAL]
    u.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
    u.RegisterClipboardFormatW.restype = wt.UINT

    def block(data: bytes):
        h = k.GlobalAlloc(0x0042, len(data))
        ctypes.memmove(k.GlobalLock(h), data, len(data))
        k.GlobalUnlock(h)
        return h
    for _ in range(20):
        if u.OpenClipboard(None):
            break
        time.sleep(0.05)
    u.EmptyClipboard()
    u.SetClipboardData(13, block((text + "\0").encode("utf-16-le")))  # CF_UNICODETEXT
    u.SetClipboardData(u.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing"), block(b"\0"))
    u.CloseClipboard()


def press(*vks):
    import ctypes
    u = ctypes.windll.user32
    for vk in vks:
        u.keybd_event(vk, 0, 0, 0)
        time.sleep(0.03)
    for vk in reversed(vks):
        u.keybd_event(vk, 0, 2, 0)
        time.sleep(0.03)


def clipboard():
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtGui import QColor, QGuiApplication, QImage
    from PySide6.QtWidgets import QLineEdit
    until(lambda: c.clip is not None, 3)
    clip = c.clip
    check(clip is not None, "the Clipboard is running")
    if clip is None:
        return
    check(clip.hotkey.ok, "its shortcut (Win + Alt + V) works")
    cb = QGuiApplication.clipboard()
    cb.setText("Hello from the test")
    check(until(lambda: clip.history.count() >= 1, 3), "a copied text is kept")
    first = clip.history.items()[0] if clip.history.count() else None
    check(first is not None and first.text == "Hello from the test" and first.kind == "text", "…as text")
    check(first is not None and first.app != "", f"…with the app it came from ({first.app if first else ''})")
    n = clip.history.count()
    copy_private("s3cret-password")
    settle(0.8)
    check(clip.history.count() == n and not clip.history.items("s3cret"), "a password marked private isn't kept")
    img = QImage(640, 360, QImage.Format.Format_RGB32)
    img.fill(QColor(30, 140, 200))
    cb.setImage(img)
    check(until(lambda: any(i.kind == "image" for i in clip.history.items()), 6), "a copied picture is kept")
    pics = [i for i in clip.history.items() if i.kind == "image"]
    check(bool(pics) and clip.history.image_path(pics[0]).exists() and clip.history.thumb_path(pics[0]).exists(),
          "…with its small preview")
    photo = TMP / "notes.txt"
    photo.write_text("notes", "utf-8")
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(photo))])
    cb.setMimeData(md)
    check(until(lambda: clip.history.items()[0].kind == "files", 3), "copied files are kept")
    cb.setText("https://example.com/report")
    check(until(lambda: clip.history.items()[0].kind == "link", 3), "a copied link is a link")
    count = clip.history.count()
    cb.setText("Hello from the test")
    until(lambda: clip.history.items()[0].text == "Hello from the test", 3)
    check(clip.history.count() == count, "the same copy isn't kept twice, it moves to the top")

    box = QLineEdit()
    box.setWindowTitle("Paste here")
    box.resize(420, 40)
    box.show()
    box.raise_()
    box.activateWindow()
    settle(0.6)
    link = clip.history.items("example.com")[0]
    clip.paste(link, int(box.winId()))
    check(until(lambda: box.text() == "https://example.com/report", 3), f"picking one pastes it where you were typing "
                                                                         f"({box.text()!r})")
    check(QGuiApplication.clipboard().text() == "https://example.com/report", "…and it's on the clipboard")
    check(clip.history.count() == count, "…without being kept again")

    box.setFocus()
    settle(0.3)
    press(0x5B, 0x12, 0x56)  # Win + Alt + V
    opened = until(lambda: clip.popup is not None and clip.popup.isVisible(), 3)
    check(opened, "Win + Alt + V opens the clipboard")
    if not opened:
        c.show_clipboard()
        settle(0.5)
    pop = clip.popup
    check(pop is not None and pop.list.count() >= 4, f"the popup lists the copies ({pop.list.count() if pop else 0})")
    if pop is not None:
        settle(0.4)
        pop.grab().save(str(OUT / "clip-popup.png"))
        pop.search.setText("hello")
        settle(0.4)
        check(pop.list.count() == 1, "searching in the popup")
        pop.hide()
    box.close()
    c.show_window("Clipboard")
    w = c.window
    w.resize(1100, 720)
    settle(0.6)
    w.grab().save(str(OUT / "clip-page.png"))
    check(w.clip_page is not None and w.clip_page.list.count() >= 4, "the Clipboard page lists the copies")
    w.open_page("ClipboardSettings")
    settle(0.3)
    w.grab().save(str(OUT / "clip-settings.png"))
    c.settings.clip_shortcut = "ctrl+`"
    c.settings_changed()
    check(clip.hotkey.ok and clip.hotkey.key == "ctrl+`", "changing the shortcut")
    c.settings.clip_shortcut = "win+alt+v"
    c.settings_changed()
    c.set_tool("clipboard", False)
    check(c.clip is None, "removing the Clipboard stops it")
    c.set_tool("clipboard", True)
    check(c.clip is not None and c.clip.history.count() >= 4, "adding it back keeps the history")


def space_tool():
    import os as _os
    root = TMP / "Space"
    for folder, files in (("Videos/Trips", {"beach.mp4": 3_000_000, "city.mov": 1_200_000}),
                          ("Photos", {f"IMG_{i:04}.jpg": 90_000 + i * 900 for i in range(60)}),
                          ("Documents", {"report.pdf": 400_000, "notes.docx": 80_000}),
                          ("Backups", {"old.zip": 2_000_000})):
        (root / folder).mkdir(parents=True, exist_ok=True)
        for name, size in files.items():
            (root / folder / name).write_bytes(_os.urandom(min(size, 4096)) * (size // 4096 + 1))
    (root / "Photos" / "copy of IMG_0040.jpg").write_bytes((root / "Photos" / "IMG_0040.jpg").read_bytes())
    c.show_window("Space")
    w = c.window
    w.resize(1100, 720)
    settle(0.5)
    pg = w.space_page
    check(pg is not None, "the Space page opens")
    w.grab().save(str(OUT / "space-drives.png"))
    t0 = time.time()
    pg.start_scan(root)
    check(until(lambda: pg.scan is not None, 20), f"looking through a folder ({time.time() - t0:.2f} s)")
    settle(0.6)
    w.grab().save(str(OUT / "space-map.png"))
    check(pg.scan is not None and pg.scan.files == 66, f"every file counted ({pg.scan.files if pg.scan else 0})")
    check(len(pg.treemap.boxes) >= 4, f"the map has boxes ({len(pg.treemap.boxes)})")
    from corefold import space as sp
    d = sp.drives()
    check(bool(d) and d[0].total > 0, f"the drives ({', '.join(x.path for x in d)})")
    import threading as _th
    for workers in (6, 1):  # (the second one has the disk's cache on its side)
        stop = _th.Event()
        _th.Timer(15, stop.set).start()
        big = sp.scan(Path(_os.environ.get("ProgramFiles", "C:/Program Files")), stop, workers=workers)
        rate = big.files / max(0.1, big.seconds)
        check(big.files > 1000, f"a big folder, {workers} at a time: {big.files:,} files in {big.seconds:.1f} s "
                                f"({rate:,.0f} files a second{', stopped after 15 s' if big.cancelled else ''})")
    pg.show_section("clean")
    check(until(lambda: pg.job is None, 60), "looking for junk")
    settle(0.4)
    w.grab().save(str(OUT / "space-clean.png"))
    check(any(j.key == "temp" for j in pg.junk), f"temporary files found ({[j.key for j in pg.junk]})")
    pg.show_section("dupes")
    pg.find_dupes([root])
    check(until(lambda: pg.job is None, 30), "looking for duplicates")
    settle(0.4)
    w.grab().save(str(OUT / "space-dupes.png"))
    check(pg.dupe_tree.topLevelItemCount() == 1, f"the copied photo is found ({pg.dupe_tree.topLevelItemCount()})")


def capture_tool():
    from PySide6.QtCore import QPoint, QPointF, QRect
    from PySide6.QtGui import QColor, QGuiApplication
    until(lambda: c.capture is not None, 3)
    cap = c.capture
    check(cap is not None, "Capture is running")
    if cap is None:
        return
    check(cap.shot_key.ok and cap.rec_key.ok, "its shortcuts (Ctrl + Print Screen, Ctrl + Shift + Print Screen) work")
    # picking an area: the frozen screen comes up, Esc gives up
    cap.screenshot()
    check(until(lambda: cap.picker is not None and cap.picker.snips, 3), "a screenshot freezes the screen")
    if cap.picker and cap.picker.snips:
        settle(0.4)
        cap.picker.snips[0].grab().save(str(OUT / "capture-pick.png"))
        snip = cap.picker.snips[0]
        snip.start, snip.end = snip.rect().topLeft() + QPoint(100, 100), snip.rect().topLeft() + QPoint(500, 380)
        snip._finish(snip.selection())
    check(until(lambda: any(e.isVisible() for e in cap.editors), 3), "picking an area opens the editor")
    ed = next((e for e in cap.editors if e.isVisible()), None)
    if ed is None:
        return
    check(ed.canvas.base.width() >= 400, f"the picture is the area picked ({ed.canvas.base.width()} px)")
    check(ed.saved is not None and ed.saved.exists(), "…saved in Pictures\\Screenshots")
    check(not QGuiApplication.clipboard().image().isNull(), "…and copied")
    from corefold.capview import Shape
    cv = ed.canvas
    cv._add(Shape("arrow", QColor("#e5484d"), 4, [QPointF(20, 20), QPointF(200, 120)]))
    cv._add(Shape("box", QColor("#0090ff"), 4, [QPointF(220, 40), QPointF(360, 160)]))
    cv._add(Shape("blur", QColor("#000"), 4, [QPointF(10, 150), QPointF(150, 260)]))
    cv._add(Shape("text", QColor("#e5484d"), 4, [QPointF(30, 200)], "Look here"))
    cv._add(Shape("marker", QColor("#f5a524"), 4, [QPointF(200, 220), QPointF(300, 225), QPointF(380, 222)]))
    ed.resize(900, 600)
    settle(0.5)
    ed.grab().save(str(OUT / "capture-editor.png"))
    out = cv.result()
    check(out.size() == cv.base.size(), "the marks are drawn into the picture")
    w0 = cv.base.width()
    cv.crop(QRect(0, 0, 200, 150))
    check(cv.base.width() == 200, "crop")
    cv.undo()
    check(cv.base.width() == w0 and len(cv.shapes) == 5, "undo the crop")
    cv.undo()
    check(len(cv.shapes) == 4, "undo a mark")
    cv.redo()
    check(len(cv.shapes) == 5, "redo")
    check(ed.save() and ed.saved.exists(), "Save")
    ed.close()
    settle(0.2)
    # recording a few seconds
    c.settings.cap_countdown = False
    from corefold.capture import Recorder, new_name, videos_folder
    rec = Recorder(QRect(0, 0, 640, 360), new_name(videos_folder(), ".mp4", "Recording"))
    check(rec.start(), f"recording starts {rec.error}")
    t0 = time.time()
    while time.time() - t0 < 3:
        qapp.processEvents()
        time.sleep(0.05)
    ok = rec.stop()
    size = rec.out.stat().st_size if rec.out.exists() else 0
    check(ok and size > 5000, f"a 3-second recording is saved ({size} bytes) {rec.error}")
    c.show_window("Capture")
    settle(0.5)
    c.window.grab().save(str(OUT / "capture-page.png"))
    c.window.open_page("CaptureSettings")
    settle(0.8)
    c.window.grab().save(str(OUT / "capture-settings.png"))
    try:
        rec.out.unlink()
    except OSError:
        pass


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


for name, fn in (("viewer", viewer), ("email", email), ("clipboard", clipboard), ("space", space_tool),
                 ("capture", capture_tool), ("store", store)):
    step(name, fn)
(OUT / "uitest.txt").write_text("\n".join(problems) or "none", "utf-8")
print(f"uitest: {len(problems)} problem(s)", flush=True)
import faulthandler  # noqa: E402
faulthandler.disable()
os._exit(0)
