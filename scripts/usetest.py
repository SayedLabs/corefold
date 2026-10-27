"""Uses every tool the way a person does, with the kind of files people really have (phone photos, a long
1080p video, a 4K film, a big archive, a whole drive), on a real Windows PC: how long each thing takes, and
whether the window ever stops answering while it works (a freeze a person would notice).

Run: python scripts/usetest.py <folder> [--lang en|ar|fr|es|tr] [--look dark|light] [--only a,b] [--heavy]
Prints one line per check ("ok", "SLOW" or "FAIL", with the time it took) and "usetest: N problem(s)".
--heavy: the Files tools only, with far more than most people have (30,000 loose downloads, an archive of
200,000 files and one over 4 GB, a PDF of 2,000 pages, 400 photos at once, a 200 MP picture, a 1 GB video, a
cloud folder of 20,000 pictures): how long, how much memory, any freeze, and that no file is lost or changed.
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
ap.add_argument("--heavy", action="store_true")
ARGS = ap.parse_args()
OUT = Path(ARGS.out).resolve()
OUT.mkdir(parents=True, exist_ok=True)
TAG = f"{ARGS.lang}-{ARGS.look}" + ("-heavy" if ARGS.heavy else "")
TMP = Path(tempfile.mkdtemp(prefix="corefold-use-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
os.environ.setdefault("COREFOLD_WAKE_ORGANIZER", "1")  # (File Organizer rests in a built Corefold: its kept code is tried here)
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

from PySide6.QtCore import QEvent, QEventLoop, QObject, QPoint, Qt, QTimer  # noqa: E402
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
        seen: dict[str, int] = {}  # where the window's thread was, sampled all through the freeze
        while True:
            time.sleep(0.02)
            gap = time.perf_counter() - self.beat
            if stuck_since is None:
                if gap > FREEZE and not self.paused and self.label:
                    stuck_since, label = self.beat, self.label
                    where = _where(sys._current_frames().get(self.main))
                    seen = {where: 1}
            elif gap < FREEZE:  # answering again
                took = self.beat - stuck_since if self.beat > stuck_since else gap
                took = max(took, 0)
                most, n = max(seen.items(), key=lambda kv: kv[1])
                if most != where:  # (the first look can catch only where it began)
                    where += f"  ·  mostly ({n} of {sum(seen.values())} looks): {most}"
                self.stalls.append((label, took, where))
                self.worst[label] = max(self.worst.get(label, 0), took)
                stuck_since = None
            elif self.paused:
                stuck_since = None
            else:
                here = _where(sys._current_frames().get(self.main))
                seen[here] = seen.get(here, 0) + 1


def _where(frame) -> str:
    """The last 4 of Corefold's calls on that stack. Read straight from the frames: traceback's way reads
    the source files too, and done all through a freeze that held Python long enough to make it longer."""
    if frame is None:
        return "?"
    st_ = []  # (newest first)
    while frame is not None:
        st_.append((frame.f_code.co_filename.replace("\\", "/"), frame.f_lineno, frame.f_code.co_name))
        frame = frame.f_back
    mine = [f for f in st_ if "corefold" in f[0].split("/scripts/")[0] and "/scripts/" not in f[0]]
    return " < ".join(f"{Path(name).name}:{line} {fn}" for name, line, fn in (mine or st_)[:4])


watch = Watch()
held: list[tuple[str, float, str]] = []


def _held_watch():
    """Notices when no Python thread could run for a while (one held Python's lock in a long C call, like
    loading a DLL), and notes where every thread was right after: the one that held it shows up."""
    me = threading.get_ident()
    last = time.perf_counter()
    while True:
        time.sleep(0.005)
        now = time.perf_counter()
        if now - last > 0.3:
            frames = sys._current_frames()
            names = {t.ident: t.name for t in threading.enumerate()}
            where = " | ".join(f"{names.get(i, i)}: {_where(f)}" for i, f in frames.items() if i != me)
            held.append((watch.label, now - last, where))
        last = now


threading.Thread(target=_held_watch, daemon=True).start()


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
        wait_ms(10)


def wait_ms(ms):
    """Lets the app run for a moment. (Not QTest.qWait: it keeps Python's lock the whole time, so the app's
    own helper threads hardly ran and a scan in the Space page took five times as long as it really does.)"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def until(cond, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        wait_ms(15)
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
    wait_ms(30)
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
    from corefold import sheet
    top = sheet.waiting()  # (questions, dialogs and menus show inside the window now)
    if top is not None:
        say(f"note  a dialog came up: {type(top).__name__} {sheet.dismiss(top)[:120]}")
        return
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
    """Makes a test file. A full FFmpeg on PATH comes first: Corefold's own one can't write every
    kind (HEVC, for one), only read it."""
    import shutil
    from corefold.converters import find_tool
    exe = shutil.which("ffmpeg") or find_tool("ffmpeg")
    subprocess.run([exe, "-v", "error", "-y", *map(str, args)], check=True)


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
        grab(c.window, f"page-{name}") if name in ("Files", "General", "Converter", "Archive", "Space",
                                                    "Quick", "Clipboard", "Capture", "Player", "Viewer", "Apps") else None
    check(not slow, f"every page opens in under 0.4 s ({len(names)} pages; slow: {', '.join(slow) or 'none'})")
    for p in slow:
        problems.append(f"slow page: {p}")
    ap = getattr(c.window, "apps_page", None)
    if ap is not None:  # (the Uninstaller's icons are made on a thread now: they still come)
        end = time.monotonic() + 20
        while time.monotonic() < end and not (ap.loaded and any(
                not ap.tree.topLevelItem(i).icon(0).isNull() for i in range(ap.tree.topLevelItemCount()))):
            settle(0.2)
        with_icon = sum(1 for i in range(ap.tree.topLevelItemCount()) if not ap.tree.topLevelItem(i).icon(0).isNull())
        check(with_icon > 0, f"the Uninstaller's list shows the apps' icons ({with_icon} of "
                             f"{ap.tree.topLevelItemCount()})")
    c.window.open_page("Space")
    c.window.open_page("Files")
    settle(0.2)
    QTest.mouseClick(c.window.windowHandle(), Qt.MouseButton.BackButton, Qt.KeyboardModifier.NoModifier,
                     c.window.stack.geometry().center())  # (through the window, the way a real click comes)
    settle(0.2)
    check(c.window.current_page() == "Space", f"the mouse's back button goes back a page ({c.window.current_page()})")
    c.window.open_page("Home")
    # (every window is restyled and drawn again: 1-2 s on GitHub's PCs, which draw without a graphics card)
    timed("switch dark/light", lambda: c.window.toggle_dark(), 2.0, profile="theme")
    grab(c.window, "home-switched")
    timed("switch it back", lambda: c.window.toggle_dark(), 2.0)


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
            wait_ms(40)
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
        timed(f"play a {label}", lambda p=path: c.open_player(p), limit, wait=playing,
              profile="player" if win is None else "")
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
    for _i, ms in enumerate((60000, 10000, 80000, 30000, 45000)):
        timed(f"jump to {ms // 1000} s", lambda m=ms: win._seek_to(m), 0.8,
              wait=lambda m=ms: abs(win.player.position() - m) < 2500)
    timed("click along the bar 10 times quickly", lambda: [(win._seek_to(5000 * k), wait_ms(60)) for k in range(10)],
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
        timed("Save in the editor (written on a thread)", ed.save, 1.0, wait=lambda: not ed.dirty)
        ed.close()


def space():
    w = c.window
    w.open_page("Space")
    pg = w.space_page
    if not check(pg is not None, "the Space page opens"):
        return
    settle(0.5)
    grab(w, "space-drives")
    root = Path(os.environ.get("SystemRoot", "C:/Windows"))
    use0 = _times()
    threading.Timer(30, _stacks, ("space-30s",)).start()  # (where the scan's threads are, if it's slow)
    # (the first look at a disk waits for the disk: 50-300 s on GitHub's PCs, with the app hardly busy)
    timed(f"look through {root} (hundreds of thousands of files)", lambda: pg.start_scan(root), 400,
          wait=lambda: pg.scan is not None and pg.job is None, wait_s=450)
    _say_times("the scan in the page", use0)
    if pg.job is not None:  # (stopped, so the rest can go on)
        say(f"note  still looking: {pg.busy_line.text()}")
        pg._stop()
        until(lambda: pg.job is None, 30)
    from corefold import space as sp
    stop = threading.Event()
    threading.Timer(60, stop.set).start()
    use0 = _times()
    with setup():
        r = sp.scan(root, stop)
    _say_times("the scan on its own", use0)
    say(f"note  the same again, on its own (the disk remembers part of it now): {r.files:,} files, {r.folders:,} "
        f"folders in {r.seconds:.1f} s ({r.files / max(r.seconds, 0.1):,.0f} a second){' (stopped)' if r.cancelled else ''}")
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
    use0 = _times()
    timed("look for copies in a folder of 1,530 files + 800 pictures", lambda: pg.find_dupes([S]), 20,
          wait=lambda: pg.job is None, wait_s=120)
    _say_times("looking for copies", use0)
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
        wait_ms(250)
    for i in range(15):
        img = QImage(1920, 1080, QImage.Format.Format_RGB32)
        img.fill(QColor(10 * i, 120, 200))
        cb.setImage(img)
        wait_ms(700)
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
        # (the window is made again without the tool: about a second on GitHub's PCs)
        timed(f"switch off {t.id}", lambda i=t.id: (c.set_tool(i, False), qapp.processEvents()), 1.5,
              profile="store-remove" if t.id == "viewer" else "")
        timed(f"switch {t.id} back on", lambda i=t.id: (c.set_tool(i, True), qapp.processEvents()), 1.5)
    w = c.window
    for sec in all_tools.SECTIONS:
        timed(f"open the {sec.id} section", lambda n=sec.page: (w.open_page(n), qapp.processEvents()), 0.4)
    grab(w, "store")


def _times():
    """(this app's processor seconds, the whole PC's busy and total processor seconds, now)."""
    import ctypes
    from ctypes import wintypes as wt
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = wt.HANDLE
    k.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
    k.GetSystemTimes.argtypes = [ctypes.POINTER(wt.FILETIME)] * 3

    def sec(f):
        return (f.dwHighDateTime << 32 | f.dwLowDateTime) / 1e7
    a, b, ker, usr = (wt.FILETIME() for _ in range(4))
    k.GetProcessTimes(k.GetCurrentProcess(), ctypes.byref(a), ctypes.byref(b), ctypes.byref(ker), ctypes.byref(usr))
    idle_, sk, su = (wt.FILETIME() for _ in range(3))
    k.GetSystemTimes(ctypes.byref(idle_), ctypes.byref(sk), ctypes.byref(su))
    total = sec(sk) + sec(su)  # (kernel time includes idle time)
    return sec(ker) + sec(usr), total - sec(idle_), total, time.perf_counter()


def _say_times(what, before):
    mine, busy, total, t = (x - y for x, y in zip(_times(), before))
    cores = os.cpu_count() or 1
    say(f"note  {what}: {t:.0f} s, this app used {mine / max(t, 0.01) * 100:.0f}% of one core, the whole PC was "
        f"{busy / max(total, 0.01) * 100:.0f}% busy ({cores} cores)")


def _stacks(name):
    lines = []
    for th in threading.enumerate():
        frame = sys._current_frames().get(th.ident)
        if frame is not None and th.ident != threading.main_thread().ident:
            lines.append(f"--- {th.name}")
            lines += [f"  {Path(f.filename).name}:{f.lineno} {f.name}" for f in traceback.extract_stack(frame)[-6:]]
    (OUT / f"{TAG}-stacks-{name}.txt").write_text("\n".join(lines), "utf-8")


def idle():
    """Left alone in the tray: hardly any processor and not much memory."""
    for top in qapp.topLevelWidgets():
        if alive(top) and top.isVisible() and top.isWindow() and type(top).__name__ != "Toast":
            top.hide()
    settle(8)
    # clean-ups still waiting from earlier steps (a popup closed a minute ago is let go of, then memory goes
    # back to Windows) happen now, once, so the 15 s below measure Corefold doing nothing
    for timer, run in ((c.free_popups_timer, c._free_popups), (c.light_tidy_timer, c.light_tidy),
                       (c.tidy_timer, c.tidy_up)):
        if timer.isActive():
            timer.stop()
            run()
    settle(2)
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
    k.OpenThread.restype = wt.HANDLE
    k.GetThreadTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4

    def thread_cpu():
        out = {}
        for th in threading.enumerate():
            hth = k.OpenThread(0x0800, False, th.native_id or 0)
            if hth:
                a, b, ker, usr = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
                k.GetThreadTimes(hth, ctypes.byref(a), ctypes.byref(b), ctypes.byref(ker), ctypes.byref(usr))
                k.CloseHandle(hth)
                out[th.name] = out.get(th.name, 0) + ((ker.dwHighDateTime << 32 | ker.dwLowDateTime)
                                                      + (usr.dwHighDateTime << 32 | usr.dwLowDateTime)) / 1e7
        return out

    class Count(QObject):  # which objects get timer events on the window's thread while idle
        def __init__(self):
            super().__init__()
            self.n: dict[str, int] = {}

        def eventFilter(self, obj, e):
            if e.type() == QEvent.Type.Timer:
                key = type(obj).__name__ + (f" {obj.objectName()}" if obj.objectName() else "")
                self.n[key] = self.n.get(key, 0) + 1
            return False
    watch.timer.stop()  # (the test's own heartbeat isn't the app's work)
    th0 = thread_cpu()
    c0, t0 = cpu(), time.perf_counter()
    wait_ms(15000)  # (one wait: settle() wakes up the test itself 100 times a second)
    used = (cpu() - c0) / (time.perf_counter() - t0) * 100
    th1 = thread_cpu()
    busy = sorted(((th1[n] - th0.get(n, 0), n) for n in th1), reverse=True)[:6]
    say("note  idle, processor time by thread in 15 s: " + ", ".join(f"{n} {t:.2f} s" for t, n in busy)
        + f" (all Python threads {sum(t for t, _ in busy):.2f} s of {used * 0.15:.2f} s)")
    counter = Count()
    qapp.installEventFilter(counter)
    wait_ms(5000)
    qapp.removeEventFilter(counter)
    say("note  idle, timers on the window's thread in 5 s: "
        + ", ".join(f"{n} ×{v}" for n, v in sorted(counter.n.items(), key=lambda x: -x[1])[:10]))
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



# ---------------------------------------------------------------------------- heavy use of the Files tools

H = TMP / "heavy"


class Memory:
    """How much memory Corefold takes while a step runs (looked at ten times a second, on its own thread)."""

    def __init__(self):
        self.peak = 0
        self.ok = os.name == "nt"
        if self.ok:
            import ctypes
            from ctypes import wintypes as wt

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD)] + [(n, ctypes.c_size_t) for n in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                    "PrivateUsage")]
            self.pmc = PMC()
            self.pmc.cb = ctypes.sizeof(PMC)
            k = ctypes.windll.kernel32
            k.GetCurrentProcess.restype = wt.HANDLE
            ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]
            self.h, self.get, self.ctypes = k.GetCurrentProcess(), ctypes.windll.psapi.GetProcessMemoryInfo, ctypes
            threading.Thread(target=self._look, daemon=True).start()

    def now(self) -> int:
        if not self.ok:
            return 0
        self.get(self.h, self.ctypes.byref(self.pmc), self.pmc.cb)
        return self.pmc.PrivateUsage

    def _look(self):
        while True:
            self.peak = max(self.peak, self.now())
            time.sleep(0.1)

    def start(self):
        self.peak = self.now()

    def say(self, what: str, limit_mb: int | None = None):
        if not self.ok:
            return
        mb = self.peak / 2**20
        say(f"note  memory while {what}: at most {mb:.0f} MB")
        if limit_mb and mb > limit_mb:
            check(False, f"memory while {what}: {mb:.0f} MB (more than {limit_mb} MB)")


mem = Memory()


class Holder:
    """A program that has a file open the way Word does (others may read it, nobody may move it)."""

    def __init__(self, path: Path):
        self.h = None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateFileW.restype = wintypes.HANDLE
            k.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
            self.k = k
            self.h = k.CreateFileW(str(path), 0x80000000 | 0x40000000, 1 | 2, None, 3, 0, None)

    def close(self):
        if self.h is not None:
            self.k.CloseHandle(self.h)
            self.h = None


def _fingerprint(folder: Path) -> dict[str, int]:
    """Every file under folder (relative path, lower case) -> its size and first bytes."""
    import zlib
    out = {}
    for p in folder.rglob("*"):
        if p.is_file():
            with open(p, "rb") as fh:
                out[str(p.relative_to(folder)).lower()] = zlib.crc32(fh.read(64)) ^ p.stat().st_size
    return out


def _wait_job(start, limit, what):
    """start() begins a job on the controller: waits for its end, timed. (result, errors, cancelled) or None."""
    done = {}

    def finished(job_id, result, errs, cancelled):
        done["r"] = (result, errs, cancelled)
    c.signals.finished.connect(finished)
    try:
        timed(what, start, limit, wait=lambda: "r" in done, wait_s=max(limit * 3, 120))
    finally:
        c.signals.finished.disconnect(finished)
    return done.get("r")


def make_heavy_files():
    from PIL import Image, ImageDraw
    H.mkdir()
    exts = [".jpg", ".png", ".pdf", ".docx", ".mp3", ".mp4", ".zip", ".txt", ".xlsx", ".mkv", ".ttf", ".psd"]
    dl = H / "Downloads"
    dl.mkdir()
    for i in range(30000):  # (each file's contents its own, so a mix-up shows)
        (dl / f"file {i:05}{exts[i % len(exts)]}").write_bytes(f"{i} {exts[i % len(exts)]}".encode() * (1 + i % 7))
    # archives: 200,000 files in folders, 100,000 in one folder, and one file over 4 GB
    import zipfile
    with zipfile.ZipFile(H / "node_modules 200000 files.zip", "w", zipfile.ZIP_STORED) as z:
        for i in range(200000):
            z.writestr(f"node_modules/pkg-{i // 400:03}/lib/file-{i}.js", f"module {i}\n")
    with zipfile.ZipFile(H / "Scans 100000 in one folder.zip", "w", zipfile.ZIP_STORED) as z:
        for i in range(100000):
            z.writestr(f"scan-{i}.txt", f"scan {i}\n")
    chunk = bytes(range(256)) * 4096  # 1 MB, a pattern (so the unpacked file can be checked)
    with zipfile.ZipFile(H / "Backup over 4 GB.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        with z.open("disk image.img", "w", force_zip64=True) as fh:
            for _ in range(4400):
                fh.write(chunk)
        z.writestr("readme.txt", "backup")
    # PDFs: one of 2,000 pages (each a scanned-looking picture), and 40 of 50 pages to merge
    from pypdf import PdfReader, PdfWriter
    page = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(page)
    for y in range(100, 1700, 36):
        d.text((90, y), "The quick brown fox jumps over the lazy dog. 0123456789 " * 3, fill=(20, 20, 20))
    page.save(H / "one page.pdf", resolution=150)
    one = PdfReader(H / "one page.pdf").pages[0]
    w = PdfWriter()
    for _ in range(2000):
        w.add_page(one)
    with open(H / "Book 2000 pages.pdf", "wb") as fh:
        w.write(fh)
    (H / "Chapters").mkdir()
    for k in range(40):
        w = PdfWriter()
        for _ in range(50):
            w.add_page(one)
        with open(H / "Chapters" / f"chapter {k + 1:02}.pdf", "wb") as fh:
            w.write(fh)
    # 400 phone photos (12 MP), a 200 MP picture, a 1 GB video
    photos = H / "Camera"
    photos.mkdir()
    shot = Image.effect_noise((1008, 756), 60).convert("RGB").resize((4032, 3024))
    shot.save(photos / "IMG_0000.jpg", quality=92)
    data = (photos / "IMG_0000.jpg").read_bytes()
    for i in range(1, 400):
        (photos / f"IMG_{i:04}.jpg").write_bytes(data)
    Image.linear_gradient("L").resize((16000, 12500)).convert("RGB").save(H / "Gigapixel 200 MP.png", compress_level=1)
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=1920x1080:d=40:r=30", "-f", "lavfi", "-i", "sine=f=440:d=40",
           "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "60M", "-pix_fmt", "yuv420p", "-c:a", "aac",
           H / "part.mp4")
    ffmpeg("-stream_loop", "3", "-i", H / "part.mp4", "-c", "copy", H / "Wedding 1 GB.mp4")
    (H / "part.mp4").unlink()
    # a cloud folder on this PC: 20,000 pictures in one folder, and a project of 3,000 files to copy
    od = H / "OneDrive"
    (od / "Camera Roll").mkdir(parents=True)
    for i in range(20000):
        (od / "Camera Roll" / f"IMG_{i:05}.jpg").write_bytes(b"\xff\xd8" + i.to_bytes(4, "little") * (1 + i % 50))
    for i in range(3000):
        part = od / "Project" / f"part-{i // 300:02}"
        part.mkdir(parents=True, exist_ok=True)
        (part / f"file-{i:04}.bin").write_bytes(os.urandom(200 + i % 5000))
    (H / "Dropbox").mkdir()
    # a folder of 20,000 files with one a program has open
    big = H / "Shared folder"
    big.mkdir()
    for i in range(20000):
        (big / f"doc {i:05}.txt").write_bytes(b"x")


def h_organizer():
    from PySide6.QtCore import Qt as _Qt
    from corefold import organizer as org
    from corefold import organizerpage as OP
    dl = H / "Downloads"
    held = Holder(dl / "file 00003.docx")
    with setup():
        before = _fingerprint(dl)
    OP.known_folder = lambda key: {"downloads": dl, "desktop": H / "Desktop"}.get(key)
    w = c.window
    mem.start()
    timed("Organizer: open the page", lambda: w.open_page("Organizer"), 0.6)
    pg = w.organizer_page
    timed("Organizer: 30,000 loose files planned and listed", lambda: None, 30,
          wait=lambda: not pg.loading and pg.plan is not None and pg.plan.folder == dl, wait_s=180)
    if pg.plan is None:
        return
    settle(1)  # (the rest of the list comes in meanwhile)
    grab(w, "heavy-organizer")
    moving = len(pg.plan.moving())
    check(moving == 30000 - 1, f"Organizer: {moving} of 30,000 to sort (the one a program has open stays)")
    head = pg.tree.topLevelItem(0)
    kind = len(pg.plan.by_kind().get(head.data(0, _Qt.UserRole), []))
    timed(f"Organizer: untick a kind of {kind:,} files", lambda: (head.setCheckState(0, _Qt.Unchecked),
                                                                  qapp.processEvents()), 0.5)
    check(len(pg.plan.moving()) == moving - kind, f"…they're left out ({len(pg.plan.moving())} to sort)")
    timed("Organizer: tick it again", lambda: (head.setCheckState(0, _Qt.Checked), qapp.processEvents()), 0.5)
    timed("Organizer: sort 29,999 files", pg.sort, 90, wait=lambda: not pg.busy and not pg.loading, wait_s=400)
    with setup():
        loose = [p for p in dl.iterdir() if p.is_file()]
        check([p.name for p in loose] == ["file 00003.docx"], f"Organizer: only the open file is still loose "
                                                              f"({len(loose)})")
        last = org.last_sort()
        check(last is not None and len(last["moves"]) == moving, "…and every move can be undone")
    timed("Organizer: undo 29,999 files", pg.undo, 90, wait=lambda: not pg.busy and not pg.loading, wait_s=400)
    held.close()
    with setup():
        after = _fingerprint(dl)
        check(after == before, f"Organizer: after Undo every file is back, unchanged ({len(after)} of {len(before)}; "
                               f"missing {len(set(before) - set(after))}, changed "
                               f"{sum(1 for k in before if k in after and after[k] != before[k])})")
        check(not [p for p in dl.iterdir() if p.is_dir()], "…and the folders it made are gone")
    mem.say("sorting 30,000 files", 900)


def _browser_for(path: Path, limit: float):
    timed(f"Archive: open {path.name}", lambda: c.open_browser(path), limit,
          wait=lambda: any(alive(b) and b.isVisible() and b.src == path for b in c.browsers), wait_s=300)
    return next((b for b in c.browsers if alive(b) and b.src == path), None)


def h_archive():
    from corefold.browser import ArchiveBrowser  # noqa: F401  (loaded before the clock starts)
    mem.start()
    nm = H / "node_modules 200000 files.zip"
    b = _browser_for(nm, 12)
    if b is None:
        return
    b.resize(1000, 650)
    for text in ("file-199999", "f", "fi", "file", "js", "zzz"):
        timed(f"Archive: search {text!r} in 200,000 files", lambda t=text: (b.search.setText(t), settle(0.3)), 1.2)
    grab(b, "heavy-archive-search")
    timed("Archive: clear the search", lambda: (b.search.setText(""), settle(0.3)), 1.2)
    timed("Archive: sort by size", lambda: b._sort_by(1), 1.0)
    timed("Archive: sort by name again", lambda: b._sort_by(0), 1.0)
    timed("Archive: select all", b.tree.selectAll, 1.0)
    b.tree.clearSelection()
    dest = H / "unpacked node_modules"
    dest.mkdir()
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(dest))
    timed("Archive: Extract to… (200,000 files)", b._extract_to, 300,
          wait=lambda: not c.jobs, wait_s=900)
    with setup():
        got = sum(1 for _ in dest.rglob("*.js"))
        check(got == 200000, f"…all 200,000 files are there ({got})")
        shutil_rm(dest)
    dest.mkdir()
    b._extract_to()
    settle(2)
    timed("Archive: stop extracting part-way (Cancel)", c.cancel_all, 4, wait=lambda: not c.jobs)
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
    with setup():
        shutil_rm(dest)
    b.close()
    settle(0.5)
    flat = H / "Scans 100000 in one folder.zip"
    b = _browser_for(flat, 10)
    if b is not None:
        timed("Archive: scroll to the end of 100,000 rows", lambda: (b.tree.scrollToBottom(), settle(0.2)), 0.6)
        timed("Archive: search 'scan-99999'", lambda: (b.search.setText("scan-99999"), settle(0.3)), 1.2)
        settle(4)  # (the rest of the rows come in meanwhile)
        found = [b.tree.topLevelItem(i) for i in range(b.tree.topLevelItemCount())
                 if not b.tree.topLevelItem(i).isHidden()]
        from corefold.browser import PATH_ROLE
        check([i.data(0, PATH_ROLE) for i in found] == ["scan-99999.txt"],
              f"…it's found, alone ({len(found)} shown)")
        b.search.setText("")
        settle(0.4)
        timed("Archive: sort 100,000 files by size", lambda: b._sort_by(1), 1.5)
        timed("Archive: select all 100,000", b.tree.selectAll, 2.5)
        check(len(b._selected_paths()) == 100000, f"…every one is selected ({len(b._selected_paths())})")
        b.close()
        settle(0.5)
    big = H / "Backup over 4 GB.zip"
    b = _browser_for(big, 4)
    if b is not None:
        dest = H / "unpacked backup"
        dest.mkdir()
        QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(dest))
        timed("Archive: extract a 4.3 GB file", b._extract_to, 240, wait=lambda: not c.jobs, wait_s=900)
        QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
        with setup():
            img = next(dest.rglob("disk image.img"), dest / "disk image.img")
            ok = img.is_file() and img.stat().st_size == 4400 << 20
            if ok:
                chunk = bytes(range(256)) * 4096
                with open(img, "rb") as fh:
                    for at in (0, 2100 << 20, 4399 << 20):  # (the start, the middle and the end, past 4 GB)
                        fh.seek(at)
                        ok = ok and fh.read(1 << 20) == chunk
            check(ok, f"…it's whole and unchanged ({img.stat().st_size if img.exists() else 0} bytes)")
            shutil_rm(dest)
        b.close()
    mem.say("the big archives", 1500)


def shutil_rm(p: Path):
    import shutil
    shutil.rmtree(p, ignore_errors=True)


def h_pdf():
    from pypdf import PdfReader
    w = c.window
    mem.start()
    w.open_page("PDF")
    pg = w.pdf_page
    book = H / "Book 2000 pages.pdf"
    timed("PDF: open a 2,000-page PDF on the board", lambda: pg.open_board([book]), 2,
          wait=lambda: not pg.reading and not pg.pending and pg.board.count() == 2000, wait_s=60)
    timed("PDF: every page's small picture drawn", lambda: None, 150,
          wait=lambda: len(pg.pixmaps) >= 2000 and not pg.jobs, wait_s=600)
    grab(w, "heavy-pdf")
    timed("PDF: scroll to the end", lambda: (pg.board.scrollToBottom(), settle(0.2)), 0.6)
    timed("PDF: select all 2,000 pages", lambda: (pg.board.selectAll(), qapp.processEvents()), 0.5)
    timed("PDF: turn 2,000 pages", lambda: (pg.turn(90), qapp.processEvents()), 1.5)
    timed("PDF: undo", lambda: (pg.undo(), qapp.processEvents()), 1.0)
    pg.board.clearSelection()
    for r in range(0, 2000, 2):
        pg.board.item(r).setSelected(True)
    timed("PDF: delete 1,000 pages", lambda: (pg.remove(), qapp.processEvents()), 1.0)
    check(pg.board.count() == 1000, f"…1,000 are left ({pg.board.count()})")
    r = _wait_job(lambda: pg.save("one"), 120, "PDF: save the 1,000 pages as a new PDF")
    if r:
        outs = [p for p in r[0].outputs if Path(p).suffix == ".pdf"]
        check(len(outs) == 1 and len(PdfReader(outs[0]).pages) == 1000 and not r[1],
              f"…it has 1,000 pages ({[len(PdfReader(p).pages) for p in outs]}, errors {r[1][:2]})")
    pg.close_board(ask=False)
    settle(0.5)
    chapters = sorted((H / "Chapters").iterdir())
    timed("PDF: 40 PDFs of 50 pages on the board", lambda: pg.open_board(chapters, "merge"), 2,
          wait=lambda: not pg.reading and not pg.pending and pg.board.count() == 2000, wait_s=120)
    timed("PDF: every page's small picture drawn (40 files)", lambda: None, 150,
          wait=lambda: len(pg.pixmaps) >= 2000 and not pg.jobs, wait_s=600)
    r = _wait_job(lambda: pg.save("one"), 120, "PDF: merge 40 PDFs into one")
    if r:
        outs = [p for p in r[0].outputs if Path(p).suffix == ".pdf"]
        check(len(outs) == 1 and len(PdfReader(outs[0]).pages) == 2000 and not r[1],
              f"…it has 2,000 pages ({[len(PdfReader(p).pages) for p in outs]}, errors {r[1][:2]})")
    pg.close_board(ask=False)
    mem.say("2,000-page PDFs on the board", 1200)
    mem.start()
    run("PDF: make the 2,000-page PDF smaller", [book], ("pdf:compress",), 240)
    mem.say("making a 2,000-page PDF smaller", 1500)


def h_converter():
    photos = sorted((H / "Camera").iterdir())
    w = c.window
    w.open_page("Converter")
    timed("Converter: drop 400 photos on the window", lambda: w.show_files(photos), 1.0)
    from corefold import converters as cv
    timed("Converter: work out the wheel for 400 photos", lambda: cv.pick(photos), 0.5)
    mem.start()
    # (only the processor's speed counts here: WebP takes 1.5 s for each of these grainy 12 MP photos on one core,
    # and the PCs the tests run on have two real cores)
    before = _times()
    run("Converter: 400 phone photos → WebP", photos, ("image:webp",), 700, expect=400)
    _say_times("400 photos → WebP", before)
    mem.say("400 photos at once", 1500)
    mem.start()
    run("Converter: 200 MP picture → JPG", [H / "Gigapixel 200 MP.png"], ("image:jpg",), 90)
    mem.say("a 200 MP picture", 1600)  # (the picture itself is 600 MB)
    run("Converter: 1 GB video → MP3", [H / "Wedding 1 GB.mp4"], ("video:mp3",), 120)
    t = _tool_for([H / "Wedding 1 GB.mp4"], "video:small")
    if t is not None:
        done = {}
        before = set((TMP / "Converted").rglob("*")) if (TMP / "Converted").exists() else set()
        c.signals.finished.connect(lambda *a: done.setdefault("r", a))
        c.start_job(t, [H / "Wedding 1 GB.mp4"], {})
        settle(5)
        timed("Converter: stop making a 1 GB video smaller (Cancel)", c.cancel_all, 5, wait=lambda: "r" in done)
        settle(1)
        left = set((TMP / "Converted").rglob("*")) - before if (TMP / "Converted").exists() else set()
        check(not [p for p in left if p.is_file()], f"…no half-made video is left behind ({[p.name for p in left]})")
    run("Converter: folder of 30,000 files → ZIP", [H / "Downloads"], ("pack:zip",), 180)


def h_cloud():
    import hashlib
    from PySide6.QtCore import Qt as _Qt
    from corefold import cloud, cloudlocal
    with setup():
        cloudlocal.add_folder(H / "OneDrive")
        cloudlocal.add_folder(H / "Dropbox")
    w = c.window
    mem.start()
    timed("Cloud: open the page", lambda: c.show_window("Cloud"), 1.0,
          wait=lambda: getattr(w, "cloud_page", None) is not None and w.cloud_page.found is not None, wait_s=60)
    page = w.cloud_page
    until(lambda: len(page.accounts) >= 2, 30)
    accs = {Path(a.local).name: a for a in page.accounts if a.local}
    if not check({"OneDrive", "Dropbox"} <= set(accs), f"Cloud: both folders are there ({list(accs)})"):
        return
    page.account_list.setCurrentRow(next(i for i, a in enumerate(page.accounts) if a is accs["OneDrive"]))
    until(lambda: page.files.topLevelItemCount() >= 2, 20)

    def names():
        return [page.files.topLevelItem(i).data(0, _Qt.UserRole).name for i in range(page.files.topLevelItemCount())]
    item = next((page.files.topLevelItem(i) for i in range(page.files.topLevelItemCount())
                 if page.files.topLevelItem(i).data(0, _Qt.UserRole).name == "Camera Roll"), None)
    if item is None:
        check(False, f"Cloud: the Camera Roll folder is shown ({names()})")
        return
    timed("Cloud: open a folder of 20,000 pictures", lambda: page._double_click(item, 0), 6,
          wait=lambda: page.files.topLevelItemCount() >= 20000, wait_s=120)
    grab(w, "heavy-cloud")
    timed("Cloud: sort them by size", lambda: page.files._sort_by(1), 0.8)
    first = page.files.topLevelItem(0).data(0, _Qt.UserRole)
    check(first is not None and first.size == min(e.size for _a, e in page.shown),
          f"…smallest first ({first.size if first else None})")
    timed("Cloud: and by name again", lambda: page.files._sort_by(0), 0.8)
    timed("Cloud: select all 20,000", page.files.selectAll, 1.0)
    with setup():  # (checking makes the rows still to come at once: the test's time, not a click's)
        picked = len(page.files.picked()[1])
    check(picked == 20000, f"…every one ({picked})")
    page.files.clearSelection()
    timed("Cloud: back up", w.go_back, 0.8, wait=lambda: page.files.topLevelItemCount() < 100)
    project = [e for e in cloud.client(accs["OneDrive"]).list(None) if e.name == "Project"]
    timed("Cloud: copy a project of 3,000 files to another cloud folder",
          lambda: (page.copy_to(accs["OneDrive"], project, accs["Dropbox"], None), settle(0.3)), 120,
          wait=lambda: not page._working(), wait_s=400)
    with setup():
        def sums(root):
            return {str(p.relative_to(root)): hashlib.md5(p.read_bytes()).hexdigest() for p in root.rglob("*")
                    if p.is_file()}
        a, b = sums(H / "OneDrive" / "Project"), sums(H / "Dropbox" / "Project")
        check(a == b and len(a) == 3000, f"…every file arrived, unchanged ({len(b)} of {len(a)})")
    mem.say("a cloud folder of 20,000 pictures", 900)


def h_unlock():
    from corefold import unlock, work
    big = H / "Shared folder"
    held = Holder(big / "doc 00010.txt")
    out = {}
    timed("Unlock: who has a file open in a folder of 20,000", lambda: work.run(
        lambda _p: unlock.find([big]), lambda _: None, lambda r: out.setdefault("r", r)), 60,
        wait=lambda: "r" in out, wait_s=300)
    r = out.get("r")
    if isinstance(r, Exception):
        check(False, f"Unlock: {r}")
    elif r is not None:
        me = [p for p in r.programs if p.pid == os.getpid()]
        check(bool(me) or os.name != "nt", f"…this program is found holding it ({[p.name for p in r.programs]}, "
                                            f"{r.files} files asked about, capped: {r.capped})")
    held.close()


from importlib.metadata import version as _version  # noqa: E402
say("note  versions: " + ", ".join(f"{n} {_version(n)}" for n in ("PySide6-Essentials", "Pillow", "pypdfium2", "pypdf")))
with setup():
    # The first text drawn on a PC that just started waits for Windows to read its fonts (any app does, once;
    # 3-13 s on GitHub's fresh PCs). Done here, so the app's own times below don't count it.
    # (with the fonts the app uses: Windows 11 itself uses them all day, so on a person's PC they're ready)
    from PySide6.QtGui import QFont, QFontMetrics
    from corefold.atomic import tokens as _tokens
    t0 = time.perf_counter()
    for _fam in {_tokens.TEXT_FAMILY, _tokens.FONT_DISPLAY.strip('"'), "Segoe UI"}:
        for _bold in (False, True):
            _f = QFont(_fam, 11)
            _f.setBold(_bold)
            QFontMetrics(_f).horizontalAdvance("Corefold مرحبا ✓ → 123")
    say(f"note  the first text on this PC (Windows reading its fonts): {time.perf_counter() - t0:.1f} s")
    t0 = time.perf_counter()
    make_heavy_files() if ARGS.heavy else make_files()
    say(f"note  test files made in {time.perf_counter() - t0:.0f} s")
watch.paused = 0


def _quiet(what, most_s=120):
    """Waits (up to most_s) for the PC to be quiet first: after 200,000 files are made, Windows' virus checker
    reads them for minutes, and the times after it would measure that, not Corefold."""
    if os.name != "nt":
        return
    start, first, others = time.perf_counter(), None, 0.0
    while True:
        before = _times()
        settle(1)
        mine, busy, total, _t = (x - y for x, y in zip(_times(), before))
        others = max(0.0, busy - mine) / max(total, 0.01) * 100
        first = others if first is None else first
        if others < 20 or time.perf_counter() - start > most_s:
            break
    say(f"note  before {what}: other programs kept the PC {first:.0f}% busy; waited {time.perf_counter() - start:.0f} s"
        f" (then {others:.0f}%)")


def start_light():
    global c
    c = timed("Corefold starts (tray icon ready)", lambda: A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png"))),
              4)
    settle(1)
    timed("the window opens", lambda: (c.show_window("Home"), c.window)[1], 2.5, wait=lambda: c.window.isVisible())
    c.window.resize(1100, 720)


if ARGS.heavy:
    STEPS = (("organizer", h_organizer), ("archive", h_archive), ("pdf", h_pdf), ("converter", h_converter),
             ("cloud", h_cloud), ("unlock", h_unlock), ("idle", idle))
    _quiet("start")  # (the window's first paint reads files too, and waited 1-2 s behind the virus checker)
    step("start", start_light)
else:
    STEPS = (("search", search), ("quick", quick), ("space", space), ("converter", converter), ("archive", archive),
             ("viewer", viewer), ("player", player), ("capture", capture),
             ("clipboard", clipboard), ("store", store), ("idle", idle))
    step("start", start)
for name, fn in STEPS:
    if ARGS.heavy and (not ARGS.only or name in ARGS.only.split(",")):
        _quiet(name)
    step(name, fn)
    for top in qapp.topLevelWidgets():  # nothing a step left open gets in the next one's way
        if alive(top) and top.isVisible() and top.isWindow() and type(top).__name__ not in ("MainWindow", "Toast"):
            top.hide()

say("")
say(f"freezes (the window didn't answer for more than {FREEZE} s):")
for label, took, where in sorted(watch.stalls, key=lambda x: -x[1])[:60]:
    say(f"  {took:5.2f} s  {label}  ·  {where}")
say("")
say("Python held up (no thread could run for more than 0.3 s):")
for label, took, where in sorted(held, key=lambda x: -x[1])[:30]:
    say(f"  {took:5.2f} s  {label}  ·  {where[:900]}")
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
from testexit import finish  # (Qt can crash in Windows' tidy-up at exit, after every check passed)
finish(0)
