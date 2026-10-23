"""Uses every tool the way a person does, with the kind of files people really have (phone photos, a long
1080p video, a 4K film, a big archive, a whole drive), on a real Windows PC: how long each thing takes, and
whether the window ever stops answering while it works (a freeze a person would notice).

Run: python scripts/usetest.py <folder> [--lang en|ar|fr|es|tr] [--look dark|light] [--only a,b]
Prints one line per check ("ok", "SLOW" or "FAIL", with the time it took) and "usetest: N problem(s)".
Every freeze is listed with what the app was doing at that moment (in <folder>/usetest-<tag>.txt), and
pictures of each tool are saved in the folder."""
import argparse
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("out", nargs="?", default="usetest")
ap.add_argument("--lang", default="en")
ap.add_argument("--look", default="dark")
ap.add_argument("--only", default="")
ARGS = ap.parse_args()
OUT = Path(ARGS.out).resolve()
OUT.mkdir(parents=True, exist_ok=True)
TAG = f"{ARGS.lang}-{ARGS.look}"
TMP = Path(tempfile.mkdtemp(prefix="corefold-use-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(int(os.environ.get("USE_STUCK", "2400")), exit=True)

from corefold import settings as st  # noqa: E402
from corefold import tools as all_tools  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.check_updates = False
s.language = ARGS.lang
s.appearance = ARGS.look
s.known_tools = sorted(t.id for t in all_tools.ready())
s.tools_off = []
s.viewer_confirm_delete = False
s.reveal_after = False
s.quick_menu = True
s.quick_wheel = True
s.output_folder = str(TMP / "Converted")
s.save()

from PySide6.QtCore import QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMenu, QMessageBox  # noqa: E402

from corefold import i18n  # noqa: E402
i18n.set_language(ARGS.lang)
qapp = QApplication(sys.argv)
qapp.setQuitOnLastWindowClosed(False)
if i18n.is_rtl():
    qapp.setLayoutDirection(Qt.RightToLeft)
from corefold import app as A  # noqa: E402

A.setup_logging()
QDesktopServices.openUrl = staticmethod(lambda url: True)
if hasattr(os, "startfile"):
    os.startfile = lambda *a, **k: None
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")

lines: list[str] = []
problems: list[str] = []
errors: list[str] = []


def _hook(t, e, tb):
    text = "".join(traceback.format_exception(t, e, tb))
    errors.append(text)
    print("ERROR " + text, flush=True)


sys.excepthook = _hook


def say(line: str):
    print(line, flush=True)
    lines.append(line)


def check(ok, what):
    say(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        problems.append(what)
    return ok


# ---------------------------------------------------------------------------- freezes

FREEZE = 0.25  # the window not answering this long is something a person sees


class Watch:
    """A heartbeat on the window's thread, and a second thread that notices when it stops: then it notes
    what the window's thread was doing (the stack), so a freeze says where it comes from."""

    def __init__(self):
        self.beat = time.perf_counter()
        self.label = ""
        self.paused = 1
        self.stalls: list[tuple[str, float, str]] = []
        self.worst: dict[str, float] = {}
        self.main = threading.main_thread().ident
        self.timer = QTimer()
        self.timer.setInterval(5)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        threading.Thread(target=self._watch, daemon=True).start()

    def _tick(self):
        self.beat = time.perf_counter()

    def _watch(self):
        stuck_since, where, label = None, "", ""
        while True:
            time.sleep(0.02)
            gap = time.perf_counter() - self.beat
            if stuck_since is None:
                if gap > FREEZE and not self.paused and self.label:
                    stuck_since, label = self.beat, self.label
                    frame = sys._current_frames().get(self.main)
                    where = _where(frame)
            elif gap < FREEZE:  # answering again
                took = self.beat - stuck_since if self.beat > stuck_since else gap
                took = max(took, 0)
                self.stalls.append((label, took, where))
                self.worst[label] = max(self.worst.get(label, 0), took)
                stuck_since = None
            elif self.paused:
                stuck_since = None


def _where(frame) -> str:
    if frame is None:
        return "?"
    st_ = traceback.extract_stack(frame)
    mine = [f for f in st_ if "corefold" in f.filename.replace("\\", "/").split("/scripts/")[0]
            and "/scripts/" not in f.filename.replace("\\", "/")]
    pick = (mine or st_)[-4:]
    return " < ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in reversed(pick))


watch = Watch()


@contextmanager
def setup():
    """Making test files: not the app's time."""
    watch.paused += 1
    try:
        yield
    finally:
        watch.beat = time.perf_counter()
        watch.paused -= 1


def settle(seconds=0.25):
    end = time.time() + seconds
    while time.time() < end:
        QTest.qWait(10)


def until(cond, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        QTest.qWait(15)
        try:
            if cond():
                return True
        except RuntimeError:  # (a window closed meanwhile)
            return False
    return False


def timed(what, fn, limit, wait=None, wait_s=None, profile=""):
    """Runs one thing a person does (a click), then waits for its result if `wait` is given. 'SLOW' when it
    took longer than `limit`; any freeze while it ran is listed separately. With `profile`, where the time
    went is saved in <folder>/profile-<profile>.txt."""
    watch.label = what
    t0 = time.perf_counter()
    try:
        if profile:
            import cProfile
            import io
            import pstats
            prof = cProfile.Profile()
            result = prof.runcall(fn)
            out = io.StringIO()
            stats = pstats.Stats(prof, stream=out)
            stats.sort_stats("cumulative").print_stats(45)
            stats.sort_stats("tottime").print_stats(30)
            (OUT / f"{TAG}-profile-{profile}.txt").write_text(out.getvalue(), "utf-8")
        else:
            result = fn()
        done = True if wait is None else until(wait, wait_s or max(limit * 4, 10))
    except Exception:
        watch.label = ""
        check(False, f"{what}: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()
        return None
    took = time.perf_counter() - t0
    QTest.qWait(30)
    watch.label = ""
    frozen = watch.worst.pop(what, 0)
    if not done:
        say(f"FAIL  {took:7.2f} s  {what}: didn't finish")
        problems.append(f"{what}: didn't finish in {took:.0f} s")
    else:
        ok = took <= limit
        say(f"{'ok  ' if ok else 'SLOW'}  {took:7.2f} s  (limit {limit} s)  {what}"
            + (f"   · window froze {frozen:.2f} s" if frozen else ""))
        if not ok:
            problems.append(f"slow: {what} ({took:.1f} s, limit {limit} s)")
    if frozen > 1.0:
        problems.append(f"froze {frozen:.1f} s: {what}")
    return result


def grab(w, name):
    try:
        settle(0.3)
        w.grab().save(str(OUT / f"{TAG}-{name}.png"))
    except Exception:
        pass


def _close_dialogs():
    for top in (qapp.activeModalWidget(), qapp.activePopupWidget()):
        if top is None:
            continue
        text = top.text() if isinstance(top, QMessageBox) else ""
        say(f"note  a dialog came up: {type(top).__name__} {top.windowTitle()!r}: {text[:120]}")
        if isinstance(top, QMenu):
            top.close()
        elif isinstance(top, QDialog):
            top.reject()
        else:
            top.close()


_closer = QTimer()
_closer.timeout.connect(_close_dialogs)
_closer.start(800)


def step(name, fn):
    if ARGS.only and name not in ARGS.only.split(","):
        return
    before = len(errors)
    say(f"----- {name}")
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()
    settle(0.3)
    check(len(errors) == before, f"{name}: no errors behind the clicks ({len(errors) - before})")


def alive(w) -> bool:
    try:
        w.objectName()
        return True
    except RuntimeError:
        return False


def ffmpeg(*args):
    from corefold.converters import find_tool
    subprocess.run([find_tool("ffmpeg"), "-v", "error", "-y", *map(str, args)], check=True)


# ---------------------------------------------------------------------------- test files

S = TMP / "files"


def make_files():
    from PIL import Image, ImageDraw, ImageFilter
    S.mkdir()
    phone = S / "Phone photos"
    phone.mkdir()
    noise = Image.effect_noise((1008, 756), 60).convert("RGB").resize((4032, 3024))
    for i in range(20):  # phone photos: 12 MP, ~3-5 MB each
        im = Image.new("RGB", (4032, 3024), (40 + i * 9, 120, 200 - i * 8))
        im = Image.blend(im, noise, 0.35)
        ImageDraw.Draw(im).text((200, 200), f"IMG_{i:04}", fill=(255, 255, 255))
        im.save(phone / f"IMG_{i:04}.jpg", quality=92)
    big = Image.blend(Image.new("RGB", (8000, 6000), (90, 160, 90)), noise.resize((8000, 6000)), 0.3)
    big.save(S / "panorama-48mp.png", optimize=False)
    big.save(S / "panorama-48mp.jpg", quality=90)
    big.resize((4000, 3000)).save(S / "photo.webp", quality=85)
    big.resize((4000, 3000)).save(S / "photo.tif")
    try:
        import pi_heif
        pi_heif.register_heif_opener()
        big.resize((4032, 3024)).save(S / "iphone.heic", quality=80)
    except Exception as e:
        say(f"note  no HEIC test file ({e})")
    frames = [Image.new("RGB", (800, 600), (i * 8, 50, 255 - i * 8)).filter(ImageFilter.BLUR) for i in range(30)]
    frames[0].save(S / "animation.gif", save_all=True, append_images=frames[1:], duration=60, loop=0)
    many = S / "Holiday (800 pictures)"
    many.mkdir()
    small = noise.resize((1600, 1200))
    for i in range(800):
        (many / f"pic-{i:04}.jpg").write_bytes(b"")
    small.save(many / "pic-0000.jpg", quality=80)
    data = (many / "pic-0000.jpg").read_bytes()
    for i in range(1, 800):
        (many / f"pic-{i:04}.jpg").write_bytes(data)
    # video and sound
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=1920x1080:d=90:r=30", "-f", "lavfi", "-i", "sine=f=440:d=90",
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", S / "Trip 1080p.mp4")
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=3840x2160:d=20:r=30", "-f", "lavfi", "-i", "sine=f=300:d=20",
           "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-tag:v", "hvc1", "-c:a", "aac",
           S / "Film 4K HEVC.mkv")
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=1280x720:d=15:r=30", "-f", "lavfi", "-i", "sine=f=500:d=15",
           "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "1M", "-c:a", "libopus",
           S / "Clip.webm")
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=1280x720:d=10:r=60", "-c:v", "libx264", "-preset", "ultrafast",
           "-pix_fmt", "yuv420p", S / "Phone video.mov")
    ffmpeg("-f", "lavfi", "-i", "sine=f=330:d=240", "-ac", "2", S / "Long song.wav")
    ffmpeg("-f", "lavfi", "-i", "sine=f=330:d=200", "-b:a", "192k", S / "Song.mp3")
    ffmpeg("-f", "lavfi", "-i", "sine=f=220:d=180", S / "Song.flac")
    # documents
    pages = [Image.new("RGB", (1240, 1754), "white") for _ in range(40)]
    for i, p in enumerate(pages):
        d = ImageDraw.Draw(p)
        for y in range(100, 1700, 40):
            d.text((100, y), f"Page {i + 1} line {y} " * 6, fill=(0, 0, 0))
    pages[0].save(S / "Report 40 pages.pdf", save_all=True, append_images=pages[1:], resolution=150)
    # a folder with many files, to pack and to look for copies
    work = S / "Project"
    for i in range(1500):
        d = work / f"part-{i // 100:02}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"file-{i:04}.txt").write_bytes((f"line {i}\n" * 40).encode())
        if i % 50 == 0:
            (d / f"blob-{i:04}.bin").write_bytes(os.urandom(2_000_000))
    import zipfile
    with zipfile.ZipFile(S / "Big archive 20000 files.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(20000):
            z.writestr(f"src/module-{i // 1000:02}/file-{i}.txt", f"content {i}\n" * 20)
        z.writestr("readme.txt", "hello")


# ---------------------------------------------------------------------------- the app

c = None


def start():
    global c
    c = timed("Corefold starts (tray icon ready)", lambda: A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png"))),
              4)
    settle(1)
    w = timed("the window opens the first time", lambda: (c.show_window("Home"), c.window)[1], 2.5,
              wait=lambda: c.window.isVisible(), profile="window")
    w.resize(1100, 720)
    settle(0.5)
    grab(w, "home")
    timed("the window opens again (closed, then opened from the tray)",
          lambda: (w.hide(), settle(0.2), watch.__setattr__("beat", time.perf_counter()), c.show_window("Home")), 1)
    from corefold import window as W
    names = []
    for name in W.PAGES:
        owner = W.NAV_OWNER.get(name, name)
        if owner in getattr(c.window, "nav_rows", []):
            names.append(name)
    slow = []
    for name in names:
        t0 = time.perf_counter()
        watch.label = f"open the {name} page"
        c.window.open_page(name)
        qapp.processEvents()
        took = time.perf_counter() - t0
        watch.label = ""
        if took > 0.4:
            slow.append(f"{name} {took:.2f} s")
        say(f"{'ok  ' if took <= 0.4 else 'SLOW'}  {took:7.2f} s  open the {name} page")
        grab(c.window, f"page-{name}") if name in ("Store", "General", "Converter", "Archive", "Space",
                                                    "Quick", "Clipboard", "Capture", "Player", "Viewer", "Apps") else None
    check(not slow, f"every page opens in under 0.4 s ({len(names)} pages; slow: {', '.join(slow) or 'none'})")
    for p in slow:
        problems.append(f"slow page: {p}")
    c.window.open_page("Home")
    timed("switch dark/light", lambda: c.window.toggle_dark(), 0.8, profile="theme")
    grab(c.window, "home-switched")
    timed("switch it back", lambda: c.window.toggle_dark(), 0.8)


def search():
    w = c.window
    w.open_page("Home")
    settle(0.2)
    timed("Ctrl + K opens the search", lambda: QTest.keyClick(w, Qt.Key_K, Qt.ControlModifier), 0.4,
          wait=lambda: w.palette is not None and w.palette.isVisible())
    pal = w.palette
    if pal is None:
        return
    for text in ("c", "co", "con", "conv", "convert", "dark", "space", "zzzz"):
        timed(f"typing {text!r} in the search", lambda t=text: (pal.search.setText(t), qapp.processEvents()), 0.15)
    pal.search.setText("dark")
    settle(0.2)
    grab(pal, "search")
    QTest.keyClick(pal.search, Qt.Key_Escape)
    settle(0.2)


def quick():
    q = c.quick
    if not check(q is not None, "the quick menu is running"):
        return
    timed("the quick menu opens", q.toggle_menu, 0.35, wait=lambda: q.menu is not None and q.menu.isVisible())
    grab(q.menu, "quick-menu") if q.menu is not None else None
    timed("…and closes", q.toggle_menu, 0.3, wait=lambda: not q.menu.isVisible())
    for i in range(5):
        timed(f"the quick menu again ({i + 2})", q.toggle_menu, 0.2, wait=lambda: q.menu.isVisible())
        q.toggle_menu()
        until(lambda: not q.menu.isVisible(), 2)
    spot = QGuiApplication.primaryScreen().availableGeometry().center()
    timed("the quick wheel opens", lambda: q.open_wheel_at(spot), 0.35, wait=lambda: q.wheel is not None and q.wheel.isVisible())
    grab(q.wheel, "quick-wheel") if q.wheel is not None else None
    if q.wheel is not None:
        q.wheel.hide()


def _tool_for(files, *keys):
    from corefold import converters as cv
    cv.warm_up(files)
    pk = cv.pick(files)
    for k in keys:
        for slot in pk.slots:
            t = slot.tool
            if slot.ok and t.run is not None and (t.key == k or (t.target and k == f"to:{t.target}")):
                return t
    say(f"note  not offered for {files[0].name}: {keys} (offered: {[sl.tool.key for sl in pk.slots if sl.ok]})")
    return None


def run(what, files, keys, limit, params=None, expect=1):
    t = _tool_for(files, *keys) if isinstance(keys, tuple) else _tool_for(files, keys)
    if t is None:
        check(False, f"{what}: the wheel doesn't offer it")
        return
    done = {}

    def finished(job_id, result, errs, cancelled):
        done["r"] = (result, errs, cancelled)
    c.signals.finished.connect(finished)
    try:
        timed(what, lambda: c.start_job(t, files, params or {}), limit, wait=lambda: "r" in done,
              wait_s=max(limit * 4, 60))
    finally:
        c.signals.finished.disconnect(finished)
    if "r" in done:
        result, errs, cancelled = done["r"]
        outs = [p for p in result.outputs if Path(p).exists()]
        check(not errs and len(outs) >= expect, f"…{len(outs)} file(s) made{(', errors: ' + str(errs[:2])) if errs else ''}")


def converter():
    phone = sorted((S / "Phone photos").iterdir())
    w = c.window
    w.open_page("Converter")
    timed("drop 20 phone photos on the window (options show)", lambda: w.show_files(phone), 0.8)
    grab(w, "converter-dropped")
    from corefold import converters as cv
    timed("work out the wheel for 20 photos", lambda: cv.pick(phone), 0.3)
    timed("work out the wheel for a 1080p video", lambda: (cv.warm_up([S / "Trip 1080p.mp4"]),
                                                           cv.pick([S / "Trip 1080p.mp4"])), 1.0)
    # the wheel itself (what Shift + drag shows)
    timed("the wheel opens for 20 photos", lambda: c.wheel.popup_at_cursor(phone),
          0.35, wait=lambda: c.wheel.isVisible(), wait_s=3)
    grab(c.wheel, "wheel")
    c.wheel.hide()
    run("20 phone photos → PNG", phone, ("image:png",), 40, expect=20)
    run("20 phone photos → WebP", phone, ("image:webp",), 30, expect=20)
    run("20 phone photos → smaller", phone, ("image:small",), 25, expect=20)
    run("20 phone photos → one PDF", phone, ("image:pdf",), 30)
    run("48 MP picture → JPG", [S / "panorama-48mp.png"], ("image:jpg",), 6)
    if (S / "iphone.heic").exists():
        run("iPhone HEIC → JPG", [S / "iphone.heic"], ("image:jpg",), 4)
    run("90 s 1080p video → smaller", [S / "Trip 1080p.mp4"], ("video:small",), 120)
    run("90 s 1080p video → MP3", [S / "Trip 1080p.mp4"], ("video:mp3",), 15)
    run("90 s 1080p video → GIF", [S / "Trip 1080p.mp4"], ("video:gif",), 90)
    run("4K HEVC film → MP4", [S / "Film 4K HEVC.mkv"], ("video:mp4",), 150)
    run("phone .mov → MP4", [S / "Phone video.mov"], ("video:mp4",), 30)
    run("4 min WAV → MP3", [S / "Long song.wav"], ("audio:mp3",), 15)
    run("FLAC → MP3", [S / "Song.flac"], ("audio:mp3",), 15)
    run("40-page PDF → pictures", [S / "Report 40 pages.pdf"], ("pdf:png", "pdf:images"), 40)
    run("40-page PDF → smaller", [S / "Report 40 pages.pdf"], ("pdf:compress",), 40)
    run("folder of 1,530 files → ZIP", [S / "Project"], ("pack:zip",), 20)
    run("folder of 1,530 files → 7z", [S / "Project"], ("pack:7z",), 40)
    # stopping a long one halfway
    t = _tool_for([S / "Film 4K HEVC.mkv"], "video:small")
    if t is not None:
        done = {}
        c.signals.finished.connect(lambda *a: done.setdefault("r", a))
        c.start_job(t, [S / "Film 4K HEVC.mkv"], {})
        settle(3)
        timed("stop a long conversion (Cancel)", c.cancel_all, 3, wait=lambda: "r" in done)
        check(not c.jobs, "…nothing is left running")


def archive():
    big = S / "Big archive 20000 files.zip"
    timed("open a ZIP with 20,000 files (window shows)", lambda: c.open_browser(big), 4,
          wait=lambda: any(alive(b) and b.isVisible() for b in c.browsers))
    b = next((b for b in c.browsers if alive(b)), None)
    if b is None:
        return
    b.resize(1000, 650)
    grab(b, "archive")
    timed("search in it", lambda: (b.search.setText("file-19999"), qapp.processEvents()), 0.6)
    b.search.setText("")
    settle(0.3)
    timed("open one file inside", lambda: (b.tree.setCurrentItem(b.tree.topLevelItem(b.tree.topLevelItemCount() - 1)),
                                           b._open(b.tree.currentItem())), 3, wait=lambda: not c.jobs)
    dest = TMP / "extracted"
    dest.mkdir()
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(dest))
    timed("Extract to… (all 20,000 files)", b._extract_to, 25,
          wait=lambda: not c.jobs and any(dest.rglob("readme.txt")), wait_s=120)
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
    b.close()
    settle(0.3)
    sev = TMP / "Project.7z"
    with setup():
        from corefold import archives as ar
        from corefold.core import Ctx, Options
        ar.pack_into([S / "Project"], sev, Ctx(Options()), "7z")
    timed("open a 7z (1,530 files)", lambda: c.open_browser(sev), 3,
          wait=lambda: any(alive(b) and b.isVisible() and b.src == sev for b in c.browsers))
    for b in c.browsers:
        if alive(b):
            b.close()
    settle(0.3)


def viewer():
    pics = [("12 MP phone photo", sorted((S / "Phone photos").iterdir())[0], 1.2),
            ("48 MP PNG", S / "panorama-48mp.png", 3), ("48 MP JPG", S / "panorama-48mp.jpg", 2),
            ("WebP", S / "photo.webp", 1.2), ("TIFF", S / "photo.tif", 1.5), ("animated GIF", S / "animation.gif", 1.2)]
    if (S / "iphone.heic").exists():
        pics.append(("iPhone HEIC", S / "iphone.heic", 2))
    for label, path, limit in pics:
        def shown(p=path):
            v = [x for x in c.viewers if alive(x) and hasattr(x, "view")]
            return bool(v) and v[-1].current() == p and not v[-1].view.image.isNull() and v[-1].isVisible()
        timed(f"open a {label} in the Viewer", lambda p=path: c.open_viewer(p), limit, wait=shown)
    win = next(x for x in c.viewers if alive(x) and hasattr(x, "view"))
    win.resize(1100, 720)
    grab(win, "viewer")
    c.open_viewer(S / "panorama-48mp.jpg")
    until(lambda: not win.view.image.isNull())
    for i in range(6):
        timed(f"zoom in ({i + 1})", lambda: (win.view.step_zoom(True), win.view.repaint()), 0.15)
    timed("zoom back to fit", lambda: (win.view.fit_view(), win.view.repaint()), 0.15)
    timed("turn a 48 MP picture", lambda: (win.rotate(90), qapp.processEvents()), 1.5)
    win.edits = []
    win.show_current()
    until(lambda: not win.view.image.isNull())
    folder = sorted((S / "Holiday (800 pictures)").iterdir())
    timed("open a picture in a folder of 800", lambda: c.open_viewer(folder[400]), 1.5,
          wait=lambda: win.current() == folder[400] and not win.view.image.isNull())
    check(len(win.files) == 800, f"…all 800 are in the list ({len(win.files)})")

    def hold_arrow():  # like holding → down: a new picture every 40 ms
        for _ in range(30):
            win.go(1)
            QTest.qWait(40)
    timed("hold → for 30 pictures (keeps up, no freeze)", hold_arrow, 2.5,
          wait=lambda: win.current() == folder[430] and not win.view.image.isNull())
    timed("Home → first picture", lambda: QTest.keyClick(win, Qt.Key_Home), 1,
          wait=lambda: win.current() == folder[0])
    timed("full screen", win.toggle_full, 0.8, wait=lambda: win.isFullScreen())
    grab(win, "viewer-full")
    timed("leave full screen", win.toggle_full, 0.8, wait=lambda: not win.isFullScreen())
    timed("details panel", win.toggle_info, 0.5)
    grab(win, "viewer-info")
    win.toggle_info()
    win.close()
    settle(0.3)


def player():
    from PySide6.QtMultimedia import QMediaPlayer
    vids = [("90 s 1080p MP4", S / "Trip 1080p.mp4", 3), ("4K HEVC MKV", S / "Film 4K HEVC.mkv", 4),
            ("VP9 WebM", S / "Clip.webm", 3), ("60 fps phone MOV", S / "Phone video.mov", 3),
            ("MP3 song", S / "Song.mp3", 2), ("FLAC song", S / "Song.flac", 2)]
    win = None
    for label, path, limit in vids:
        def playing(p=path):
            ps = [x for x in getattr(c, "players", []) if alive(x)]
            return bool(ps) and ps[-1].current() == p and ps[-1].player.playbackState() == QMediaPlayer.PlayingState \
                and ps[-1].player.position() > 200
        timed(f"play a {label}", lambda p=path: c.open_player(p), limit, wait=playing)
        win = next((x for x in getattr(c, "players", []) if alive(x)), None)
        if win is not None and win.player.errorString():
            check(False, f"…{label}: {win.player.errorString()}")
        if win is not None and label == "4K HEVC MKV":
            win.resize(1100, 680)
            grab(win, "player-4k")
    if win is None:
        return
    win.open(S / "Trip 1080p.mp4")
    until(lambda: win.player.position() > 200, 5)
    for i, ms in enumerate((60000, 10000, 80000, 30000, 45000)):
        timed(f"jump to {ms // 1000} s", lambda m=ms: win._seek_to(m), 0.8,
              wait=lambda m=ms: abs(win.player.position() - m) < 2500)
    timed("click along the bar 10 times quickly", lambda: [(win._seek_to(5000 * k), QTest.qWait(60)) for k in range(10)],
          1.5, wait=lambda: abs(win.player.position() - 45000) < 3000)
    timed("pause", win.toggle, 0.3, wait=lambda: win.player.playbackState() != QMediaPlayer.PlayingState)
    timed("play", win.toggle, 0.5, wait=lambda: win.player.playbackState() == QMediaPlayer.PlayingState)
    timed("full screen", win.toggle_full, 0.8, wait=lambda: win.isFullScreen())
    grab(win, "player-full")
    timed("leave full screen", win.toggle_full, 0.8, wait=lambda: not win.isFullScreen())
    timed("mini player", win.toggle_mini, 0.8)
    grab(win, "player-mini")
    win.toggle_mini()
    timed("next file in the folder", lambda: win.go(1), 3, wait=lambda: win.player.position() > 200)
    win.close()
    settle(0.5)


def capture():
    cap = c.capture
    if not check(cap is not None, "Capture is running"):
        return
    timed("screenshot: the screen freezes for picking", cap.screenshot, 0.6,
          wait=lambda: cap.picker is not None and cap.picker.snips and cap.picker.snips[0].isVisible())
    if cap.picker and cap.picker.snips:
        grab(cap.picker.snips[0], "capture-pick")
        snip = cap.picker.snips[0]
        snip.start, snip.end = snip.rect().topLeft() + QPoint(50, 50), snip.rect().bottomRight() - QPoint(50, 50)
        timed("pick an area: the editor opens", lambda: snip._finish(snip.selection()), 1.2,
              wait=lambda: any(e.isVisible() for e in cap.editors))
    ed = next((e for e in cap.editors if e.isVisible()), None)
    if ed is not None:
        grab(ed, "capture-editor")
        timed("Save in the editor", ed.save, 1.0)
        ed.close()
    c.settings.cap_countdown = False
    if hasattr(cap, "record"):
        from corefold.capture import Recorder, new_name, videos_folder
        scr = QGuiApplication.primaryScreen().geometry()
        rec = Recorder(scr, new_name(videos_folder(), ".mp4", "Recording"))
        timed("start recording the whole screen", rec.start, 2)
        settle(6)
        timed("stop recording (the video is saved)", rec.stop, 6)
        size = rec.out.stat().st_size if rec.out.exists() else 0
        check(size > 20000, f"a 6-second full-screen recording ({size // 1024} KB) {rec.error}")


def space():
    w = c.window
    w.open_page("Space")
    pg = w.space_page
    if not check(pg is not None, "the Space page opens"):
        return
    settle(0.5)
    grab(w, "space-drives")
    root = Path(os.environ.get("SystemRoot", "C:/Windows"))
    timed(f"look through {root} (hundreds of thousands of files)", lambda: pg.start_scan(root), 90,
          wait=lambda: pg.scan is not None and pg.job is None, wait_s=150)
    if pg.job is not None:  # (stopped, so the rest can go on)
        say(f"note  still looking: {pg.busy_line.text()}")
        pg._stop()
        until(lambda: pg.job is None, 30)
    import cProfile
    import io
    import pstats
    from corefold import space as sp
    stop = threading.Event()
    threading.Timer(40, stop.set).start()
    prof = cProfile.Profile()
    with setup():
        r = prof.runcall(sp.scan, root, stop)
    out = io.StringIO()
    pstats.Stats(prof, stream=out).sort_stats("tottime").print_stats(25)
    (OUT / f"{TAG}-profile-scan.txt").write_text(out.getvalue(), "utf-8")
    say(f"note  scan on its own: {r.files:,} files, {r.folders:,} folders in {r.seconds:.1f} s "
        f"({r.files / max(r.seconds, 0.1):,.0f} a second){' (stopped)' if r.cancelled else ''}")
    settle(0.5)
    grab(w, "space-map")
    if pg.scan is not None:
        say(f"note  {pg.scan.files:,} files in {pg.scan.seconds:.1f} s")
        sub = max(pg.scan.root.children, key=lambda f: f.size) if getattr(pg.scan.root, "children", None) else None
        if sub is not None:
            timed("open the biggest folder in the map", lambda: (pg.open_folder(sub), qapp.processEvents()), 0.4)
            timed("back up", lambda: (pg._up(), qapp.processEvents()), 0.4)
        timed("make the window bigger (the map is drawn again)", lambda: (w.resize(1400, 900), qapp.processEvents()), 0.5)
        w.resize(1100, 720)
    pg.show_section("clean")
    timed("look for junk", lambda: None, 30, wait=lambda: pg.job is None, wait_s=120)
    grab(w, "space-clean")
    pg.show_section("dupes")
    timed("look for copies in a folder of 1,530 files + 800 pictures", lambda: pg.find_dupes([S]), 20,
          wait=lambda: pg.job is None, wait_s=120)
    grab(w, "space-dupes")


def clipboard():
    clip = c.clip
    if not check(clip is not None, "the Clipboard is running"):
        return
    cb = QGuiApplication.clipboard()
    from PySide6.QtGui import QColor, QImage
    from corefold import clipboard as A_clip
    t0 = time.perf_counter()
    label = "copying 60 texts and 15 pictures, one after another"
    before = clip.history.count()
    watch.label = label
    for i in range(60):  # as fast as a person copies (Ctrl + C, switch, Ctrl + C …)
        cb.setText(f"Copied text number {i} — some words to search in, https://example.com/{i}")
        QTest.qWait(250)
    for i in range(15):
        img = QImage(1920, 1080, QImage.Format.Format_RGB32)
        img.fill(QColor(10 * i, 120, 200))
        cb.setImage(img)
        QTest.qWait(700)
    until(lambda: clip.history.count() >= before + 75, 10)
    watch.label = ""
    frozen = watch.worst.pop(label, 0)
    check(clip.history.count() >= before + 75, f"every copy is kept ({clip.history.count() - before} of 75, "
                                               f"{time.perf_counter() - t0:.1f} s)")
    check(frozen < 0.25, f"copying doesn't hold the app up (longest {frozen:.2f} s)")
    with setup():
        for i in range(300):  # a long history, for the lists below
            clip.history.add(A_clip.Item(kind="text", text=f"Older copy {i} with words", digest=f"old-{i}",
                                         size=24))
    timed("open the clipboard list with 300+ copies", lambda: c.show_clipboard(), 0.5,
          wait=lambda: clip.popup is not None and clip.popup.isVisible())
    pop = clip.popup
    if pop is not None:
        grab(pop, "clip-popup")
        for text in ("c", "co", "number 2", "zzzz", ""):
            timed(f"search the clipboard: {text!r}", lambda t=text: (pop.search.setText(t), qapp.processEvents()), 0.2)
        pop.hide()
    c.show_window("Clipboard")
    timed("the Clipboard page with 300+ copies", lambda: (c.window.open_page("Clipboard"), qapp.processEvents()), 0.6,
          profile="clip-page")
    grab(c.window, "clip-page")


def store():
    w = c.window
    for t in all_tools.ready():
        timed(f"Store: remove {t.id}", lambda i=t.id: (c.set_tool(i, False), qapp.processEvents()), 1.0,
              profile="store-remove" if t.id == "viewer" else "")
        timed(f"Store: add {t.id} back", lambda i=t.id: (c.set_tool(i, True), qapp.processEvents()), 1.5)
    w = c.window
    w.open_page("Store")
    grab(w, "store")


def idle():
    """Left alone in the tray: hardly any processor and not much memory."""
    for top in qapp.topLevelWidgets():
        if alive(top) and top.isVisible() and top.isWindow() and type(top).__name__ != "Toast":
            top.hide()
    settle(8)
    import ctypes
    from ctypes import wintypes as wt
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = wt.HANDLE
    k.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]
    h = k.GetCurrentProcess()

    def cpu():
        a, b, ker, usr = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
        k.GetProcessTimes(h, ctypes.byref(a), ctypes.byref(b), ctypes.byref(ker), ctypes.byref(usr))
        return ((ker.dwHighDateTime << 32 | ker.dwLowDateTime) + (usr.dwHighDateTime << 32 | usr.dwLowDateTime)) / 1e7
    watch.timer.stop()  # (the test's own heartbeat isn't the app's work)
    c0, t0 = cpu(), time.perf_counter()
    settle(15)
    used = (cpu() - c0) / (time.perf_counter() - t0) * 100
    watch.timer.start()
    check(used < 1.5, f"idle in the tray: {used:.2f}% of one processor core")

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD)] + [(n, ctypes.c_size_t) for n in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
            "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
            "PrivateUsage")]
    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
    say(f"note  memory after using everything and leaving it idle: {pmc.WorkingSetSize / 2**20:.0f} MB in use, "
        f"{pmc.PrivateUsage / 2**20:.0f} MB private")


with setup():
    t0 = time.perf_counter()
    make_files()
    say(f"note  test files made in {time.perf_counter() - t0:.0f} s")
watch.paused = 0

step("start", start)
for name, fn in (("search", search), ("quick", quick), ("converter", converter), ("archive", archive),
                 ("viewer", viewer), ("player", player), ("capture", capture), ("space", space),
                 ("clipboard", clipboard), ("store", store), ("idle", idle)):
    step(name, fn)
    for top in qapp.topLevelWidgets():  # nothing a step left open gets in the next one's way
        if alive(top) and top.isVisible() and top.isWindow() and type(top).__name__ not in ("MainWindow", "Toast"):
            top.hide()

say("")
say(f"freezes (the window didn't answer for more than {FREEZE} s):")
for label, took, where in sorted(watch.stalls, key=lambda x: -x[1])[:60]:
    say(f"  {took:5.2f} s  {label}  ·  {where}")
log_file = st.LOG_DIR / "corefold.log"
if log_file.exists():
    text = log_file.read_text("utf-8", "replace")
    (OUT / f"{TAG}-log.txt").write_text(text, "utf-8")
    bad = [line for line in text.splitlines() if " ERROR " in line or "Traceback" in line]
    check(not bad, f"the log has no errors ({len(bad)}: {bad[:3]})")
(OUT / f"usetest-{TAG}.txt").write_text("\n".join(lines + ["", "problems:"] + problems + ["", "errors:"] + errors),
                                        "utf-8")
print(f"usetest {TAG}: {len(problems)} problem(s)", flush=True)
faulthandler.disable()
os._exit(0)
