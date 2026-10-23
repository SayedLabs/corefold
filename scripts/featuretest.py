"""Checks of what 1.7.7 added to the tools: Capture (the PC's sound, timer, pin, steps, GIF), Clipboard
(edit, change, apps it ignores), Viewer (rename, move, flip, sort), Player (playlist, picture fit),
Converter (crop video, subtitles, sound, join, SHA-256), Archive (remembered passwords) and Space
(more caches).

Prints one line per check and "featuretest: N problem(s)". Needs FFmpeg (and 7-Zip for the
password part). Run: python scripts/featuretest.py
Everything happens in a temporary folder."""
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="corefold-featuretest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name != "nt":  # (a quick try on Linux: Windows' parts are pretend ones)
    import ctypes
    from unittest import mock
    ctypes.windll = mock.MagicMock()
    for name in ("winreg", "winsound"):
        sys.modules[name] = mock.MagicMock()

from PySide6.QtCore import QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from corefold.core import Ctx, Options  # noqa: E402

app = QApplication.instance() or QApplication([])
problems = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)


def ctx(**params):
    return Ctx(Options(), params=params)


def step(name, fn):
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")


def ffmpeg(*args):
    from corefold.converters import find_tool
    subprocess.run([find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *args], check=True,
                   capture_output=True)


class FakeController:
    """Just enough of the app for the windows to open."""

    def __init__(self):
        from corefold.settings import Settings
        from PySide6.QtGui import QIcon

        from corefold.theme import build_theme
        self.settings = Settings()
        self.theme = build_theme("dark", "teal")
        self.icon = QIcon()
        self.toasts = []
        self.toast = self
        self.capture = None
        self.jobs = []

    def notify(self, title, detail, *a, **k):
        self.toasts.append(title)
        return True

    def say(self, title, detail, *a, **k):
        self.toasts.append(title)

    def tell(self, title, detail, *a, **k):
        self.toasts.append(title)

    def settings_changed(self, *a, **k):
        pass

    def start_job(self, tool, files, *a, **k):
        self.jobs.append((tool.key, files))


# ----------------------------------------------------------------------------- Converter: video and sound

VID = TMP / "videos"


def make_videos():
    VID.mkdir(parents=True, exist_ok=True)
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=640x360:rate=30,pad=640:480:0:60:black", "-f", "lavfi", "-i",
           "sine=f=440", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(VID / "bars.mp4"))
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           str(VID / "quiet.mp4"))
    ffmpeg("-f", "lavfi", "-i", "sine=f=300", "-t", "2", str(VID / "a.mp3"))
    ffmpeg("-f", "lavfi", "-i", "sine=f=500", "-t", "2", str(VID / "b.mp3"))
    # an Arabic subtitle file saved the old Windows way (Windows-1256), like many downloaded ones
    (VID / "bars.srt").write_bytes("1\r\n00:00:00,500 --> 00:00:02,000\r\nمرحبا\r\n".encode("cp1256"))


def video_tools():
    from corefold import videotools as vt
    from corefold.converters import catalog, probe
    keys = {t.key for kind in catalog().values() for t in kind}
    for k in ("video:crop", "video:subtitles", "video:srt", "video:sound", "video:join", "audio:join",
              "files:hash"):
        check(k in keys, f"the wheel has {k}")
    check(vt.black_bars(VID / "bars.mp4") == (640, 360, 0, 60), "black bars are found")
    out = vt.crop_video(VID / "bars.mp4", ctx(how="bars")).outputs[0]
    info = probe(out)
    check((info["width"], info["height"]) == (640, 360), "crop: the black bars are gone")
    for how, size in (("square", (480, 480)), ("vertical", (270, 480)), ("wide", (640, 360))):
        info = probe(vt.crop_video(VID / "bars.mp4", ctx(how=how)).outputs[0])
        check((info["width"], info["height"]) == size, f"crop to {how}: {info['width']}x{info['height']}")
    try:
        vt.crop_video(VID / "quiet.mp4", ctx(how="bars"))
        check(False, "a video without bars says so")
    except RuntimeError:
        check(True, "a video without bars says so")
    check(vt.subtitles_next_to(VID / "bars.mp4") == VID / "bars.srt", "the subtitle file next to it is found")
    burned = vt.add_subtitles(VID / "bars.mp4", ctx(how="burn")).outputs[0]
    check(burned.suffix == ".mp4" and probe(burned)["duration"] > 2, "subtitles drawn into the picture")
    track = vt.add_subtitles(VID / "bars.mp4", ctx(subtitles=str(VID / "bars.srt"), how="track")).outputs[0]
    check(vt.subtitle_tracks(track) == ["subrip"], "subtitles added as a track (MKV)")
    check(vt.check_has_subtitles([VID / "bars.mp4"]) is not None and vt.check_has_subtitles([track]) is None,
          "SRT is offered only for videos with subtitles inside")
    srt = vt.save_subtitles(track, ctx()).outputs[0]
    check("مرحبا" in srt.read_text("utf-8-sig"), "the subtitles come out again as .srt (Arabic intact)")
    for ext in (".m4a", ".wav", ".flac"):
        out = vt.video_sound(VID / "bars.mp4", ctx(format=ext)).outputs[0]
        check(out.suffix == ext and out.stat().st_size > 1000, f"the sound of a video as {ext}")
    check(vt.check_join([VID / "bars.mp4"]) is not None, "join needs 2 or more files")
    check(vt.check_join([VID / "bars.mp4", VID / "a.mp3"]) is not None, "join won't mix videos and sound")
    joined = vt.join_media([VID / "bars.mp4", VID / "quiet.mp4"], ctx()).outputs[0]
    info = probe(joined)
    check(abs(info["duration"] - 5) < 0.5 and info["audio"] and info["width"] == 640,
          f"two videos joined (one without sound): {info['duration']:.1f} s")
    joined = vt.join_media([VID / "a.mp3", VID / "b.mp3"], ctx()).outputs[0]
    check(joined.suffix == ".mp3" and abs(probe(joined)["duration"] - 4) < 0.5, "two sound files joined")


def graphics_card():
    from corefold import converters as cv
    args = cv.VIDEO_ARGS[".mp4"]
    fast = cv.with_gpu(args, "h264_nvenc")
    check(fast is not None and "libx264" not in fast and "h264_nvenc" in fast and "-crf" not in fast,
          "the video is handed to the graphics card")
    check(cv.with_gpu(["-c:v", "libx264", "-pass", "1"], "h264_nvenc") is None, "…not for two passes")
    enc = cv.gpu_encoder()
    print("(graphics card encoder here:", enc or "none", ")")
    out = TMP / "gpu.mp4"
    real = cv.gpu_encoder
    try:  # a graphics card that says it can but then fails: the usual way follows by itself
        cv.gpu_encoder = lambda: "h264_nvenc" if not enc else enc
        cv.run_ffmpeg(VID / "bars.mp4", out, args, Ctx(Options(gpu=True)))
    finally:
        cv.gpu_encoder = real
    check(out.exists() and cv.media_duration(out) > 2, "a video is made either way")


def fingerprint():
    import hashlib

    from corefold import moretools as mt
    f = TMP / "download.bin"
    f.write_bytes(os.urandom(300_000))
    r = mt.file_hash([f], ctx())
    check(r.clipboard == hashlib.sha256(f.read_bytes()).hexdigest(), "SHA-256 of a file is copied")
    check(mt.check_files_only([TMP]) is not None, "SHA-256 isn't offered for folders")


# ----------------------------------------------------------------------------- Archive: passwords

def passwords():
    from corefold import archives as ar, passwords as pw
    if not ar.find_7z():
        print("(no 7-Zip: password part skipped)")
        return
    src = TMP / "pw"
    src.mkdir()
    (src / "big.txt").write_text("x" * 200_000)
    (src / "small.txt").write_text("hello")
    out = ar.pack_into([src], TMP / "locked.7z", ctx(), fmt="7z", password="sesame")[0]
    zipped = ar.pack_into([src], TMP / "locked.zip", ctx(), fmt="zip", password="sesame")[0]
    for f in (out, zipped):
        check(ar.password_works(f, "sesame") and not ar.password_works(f, "wrong"),
              f"a password is tried quickly ({f.suffix})")
    check(pw.load() == [], "no passwords are saved to begin with")
    pw.add("one")
    pw.add("sesame")
    pw.add("one")
    check(pw.load() == ["one", "sesame"], "passwords are remembered once each, newest first")
    raw = pw.FILE.read_bytes()
    check(b"sesame" not in raw, "the saved passwords aren't readable in the file")
    check(pw.find(out) == "sesame", "a remembered password opens the archive")
    pw.forget_all()
    check(pw.load() == [] and pw.find(out) is None, "Forget the saved passwords")


# ----------------------------------------------------------------------------- Capture

def capture():
    from corefold import capture as cap
    from corefold.capview import Canvas, PinnedPicture, Shape
    exe = Path("ffmpeg")
    plain = cap.record_command(exe, QRect(0, 0, 400, 300), TMP / "r.mp4", 30, "", True, "")
    check("-c:a" not in plain, "recording without sound has no sound track")
    pc = cap.record_command(exe, QRect(0, 0, 400, 300), TMP / "r.mp4", 30, "", True, r"\\.\pipe\x")
    check(r"\\.\pipe\x" in pc and "-c:a" in pc and "amix" not in " ".join(pc), "the PC's sound is recorded")
    both = cap.record_command(exe, QRect(0, 0, 400, 300), TMP / "r.mp4", 30, "Mic (USB)", True, r"\\.\pipe\x")
    check("amix" in " ".join(both) and "[a]" in both, "the microphone and the PC's sound mixed in one track")
    img = QImage(300, 200, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor("#ffffff"))
    canvas = Canvas(img)
    canvas.tool = "step"
    for x in (40, 120, 200):
        canvas._add(Shape("step", QColor("#e5484d"), 4, [QPointF(x, 100)], str(sum(
            1 for s in canvas.shapes if s.kind == "step") + 1)))
    check([s.text for s in canvas.shapes] == ["1", "2", "3"], "steps are numbered 1, 2, 3")
    out = canvas.result()
    check(out.pixelColor(48, 100) != QColor("#ffffff"), "a step is drawn in the picture")
    c = FakeController()
    pin = PinnedPicture(c, out)
    pin.show()
    app.processEvents()
    w = pin.width()
    from PySide6.QtCore import QPoint as _P
    from PySide6.QtGui import QWheelEvent
    ev = QWheelEvent(QPointF(10, 10), QPointF(10, 10), _P(0, 0), _P(0, 120), Qt_NoButton(), Qt_NoMod(),
                     Qt_Phase(), False)
    pin.wheelEvent(ev)
    check(pin.width() > w, "the pinned picture grows with the mouse wheel")
    pin.close()
    app.processEvents()
    done = []
    from corefold.capview import Countdown
    cd = Countdown(1, lambda: done.append(1))
    cd.show()
    t = time.monotonic()
    while not done and time.monotonic() - t < 4:
        app.processEvents()
        time.sleep(0.05)
    check(done == [1], "the screenshot timer counts down, then takes it")


def Qt_NoButton():
    from PySide6.QtCore import Qt
    return Qt.NoButton


def Qt_NoMod():
    from PySide6.QtCore import Qt
    return Qt.NoModifier


def Qt_Phase():
    from PySide6.QtCore import Qt
    return Qt.NoScrollPhase


def recording_pause():
    if os.name != "nt":
        print("(recording the screen needs Windows)")
        return
    from corefold.capture import Recorder
    from corefold.converters import media_duration
    out = TMP / "rec" / "paused.mp4"
    rec = Recorder(QRect(0, 0, 320, 240), out, 15)
    check(rec.start(), f"recording starts {rec.error}")
    time.sleep(2.5)
    check(rec.pause() and rec.paused and rec.running(), "pause")
    time.sleep(2)
    seconds = rec.seconds()
    check(rec.resume() and not rec.paused, "carry on")
    time.sleep(2.5)
    ok = rec.stop()
    check(ok and out.exists(), f"the parts are joined into one file {rec.error}")
    check(not list(out.parent.glob("*.part*")), "…and the parts are gone")
    if ok:
        d = media_duration(out)
        check(3.5 < d < 7.5, f"…without the paused time ({d:.1f} s, {seconds:.1f} s before the pause)")


def loopback():
    if os.name != "nt":
        return
    from corefold.loopback import Loopback, available
    print("the PC's sound can be recorded here:", available())
    lb = Loopback()
    ok = lb.start()
    if not ok:  # (GitHub's PCs have no sound card: it must fail cleanly)
        check(bool(lb.error), f"no speakers: says why ({lb.error})")
        return
    lb.stop()
    check(True, "the PC's sound starts and stops")


# ----------------------------------------------------------------------------- Clipboard

def clipboard():
    from corefold import clipboard as cb
    check(cb.change_text("Hello  World", "upper") == "HELLO  WORLD", "UPPERCASE")
    check(cb.change_text("hello wORLD", "title") == "Hello World", "Capital Letters")
    check(cb.change_text("  a   b \n\n\n c  ", "spaces") == "a b\n\nc", "Remove extra spaces")
    check(cb.change_text("a\nb\n  c", "one_line") == "a b c", "Join into one line")
    check(cb.change_text('{"b":1,"a":[1,2]}', "json").startswith('{\n  "b": 1'), "Tidy JSON")
    check(cb.change_text("not json", "json") == "not json", "Tidy JSON leaves other text alone")
    item = cb.text_item("https://example.com")
    check(item.kind == "link" and item.digest, "edited text becomes a copy to paste")

    class S:
        clip_ignore = 'MyBank.exe, notes ; "C:\\x\\y.exe"'
    check(cb.ignored_apps(S()) == {"mybank.exe", "notes.exe", "c:\\x\\y.exe"}, "apps the clipboard ignores")


def saved_texts():
    from types import SimpleNamespace

    from corefold import clipboard as cb
    fake = SimpleNamespace(history=cb.History(TMP / "clip"), changed=SimpleNamespace(emit=lambda: None))
    first = cb.Clipboard.save_text(fake, "My address, 12 Street")
    fake.history.add(cb.text_item("copied later"))
    items = fake.history.items()
    check(items[0].id == first.id and items[0].pinned, "a saved text is pinned at the top")
    second = cb.Clipboard.save_text(fake, "My new address", instead_of=first)
    texts = [i.text for i in fake.history.items()]
    check("My new address" in texts and "My address, 12 Street" not in texts and second.pinned,
          "changing a saved text replaces it")
    fake.history.close()


def clip_list():
    from types import SimpleNamespace

    from corefold import clipboard as cb
    from corefold.clipview import ClipList
    hist = cb.History(TMP / "clip-list")
    for t in ("first", "second", "third"):
        hist.add(cb.text_item(t))
    clip = SimpleNamespace(history=hist)
    c = FakeController()
    lst = ClipList(clip, c.theme, 56, click_uses=True, controller=c)
    lst.fill(hist.items())
    got = []
    lst.text_used.connect(got.append)
    lst._change(lst.item(0).data(Qt.UserRole), "upper")
    check(got == ["THIRD"], f"Change it, from the list ({got})")
    lst.item(2).setSelected(True)
    lst.item(0).setSelected(True)
    picked = [i.text for i in lst.picked_texts()]
    check(sorted(picked) == ["first", "third"], f"Ctrl+click picks several ({picked})")
    lst._use_together(lst.picked_texts())
    check(got[-1] in ("first\nthird", "third\nfirst"), "…pasted together, one per line")
    for name in ("_edit", "_resave", "_read_text", "_save_picture"):
        check(callable(getattr(lst, name, None)), f"the list's menu can {name.strip('_')}")
    hist.close()


# ----------------------------------------------------------------------------- Viewer

def viewer():
    from PIL import Image

    from corefold import viewer as v
    folder = TMP / "photos"
    folder.mkdir()
    now = time.time()
    for i, (name, size) in enumerate((("b.jpg", 40), ("a.jpg", 80), ("c.png", 20))):
        Image.new("RGB", (size, size), (200, 10 * i, 10)).save(folder / name)
        os.utime(folder / name, (now - i * 100, now - i * 100))
    names = lambda sort: [p.name for p in v.pictures_in(folder, sort)]  # noqa: E731
    check(names("name") == ["a.jpg", "b.jpg", "c.png"], "pictures by name")
    check(names("newest") == ["b.jpg", "a.jpg", "c.png"], "pictures newest first")
    check(names("oldest") == ["c.png", "a.jpg", "b.jpg"], "pictures oldest first")
    check(names("biggest")[0] == "a.jpg", "pictures biggest first")
    im = Image.new("RGB", (4, 2), (0, 0, 0))
    im.putpixel((0, 0), (255, 0, 0))
    flipped = v.apply_edits(im, [("flip", "h")])
    check(flipped.getpixel((3, 0)) == (255, 0, 0), "flip left to right")
    flipped = v.apply_edits(im, [("flip", "v")])
    check(flipped.getpixel((0, 1)) == (255, 0, 0), "flip upside down")
    q = QImage(4, 2, QImage.Format_RGB32)
    q.fill(QColor("#000000"))
    q.setPixelColor(0, 0, QColor("#ff0000"))
    check(v._flipped(q, "h").pixelColor(3, 0) == QColor("#ff0000"), "flip on screen")


def viewer_strip():
    from corefold.viewer import ViewerWindow, small_picture
    folder = TMP / "photos"
    check(not small_picture(folder / "a.jpg").isNull(), "a small picture for the strip")
    c = FakeController()
    c.settings.viewer_strip = True
    win = ViewerWindow(c, folder / "b.jpg", c.icon)
    win.show()
    for _ in range(40):
        app.processEvents()
        time.sleep(0.05)
    check(win.strip.count() == len(win.files) == 3, f"the strip has every picture ({win.strip.count()})")
    check(win.strip.currentRow() == win.index, "…with the one shown picked")
    filled = sum(not win.strip.item(i).icon().isNull() for i in range(win.strip.count()))
    check(filled == 3, f"…and their small pictures ({filled})")
    win.strip.itemClicked.emit(win.strip.item(2))
    check(win.index == 2, "clicking one in the strip shows it")
    win.toggle_strip()
    check(win.strip.isHidden() and not c.settings.viewer_strip, "T hides the strip")
    win.close()
    app.processEvents()


def viewer_more():
    import io

    import numpy as np
    from PIL import Image

    from corefold import viewer as v
    a = np.zeros((4, 4, 3), np.uint8)
    a[..., :] = (100, 60, 30)
    check(v.adjust_array(a, light=50)[0, 0, 0] > 100, "Light makes it brighter")
    check(v.adjust_array(a, colour=-100)[0, 0].std() < 1, "Colour all the way down: grey")
    dull = np.tile(np.linspace(90, 160, 64, dtype=np.uint8)[None, :, None], (8, 1, 3))
    fixed = v.adjust_array(dull, auto=True)
    check(fixed.min() < 10 and fixed.max() > 245, "Auto stretches a dull photo")
    im = Image.new("RGB", (8, 8), (100, 100, 100))
    check(v.apply_edits(im, [("adjust", (0, 0, 0, False))]).getpixel((0, 0)) == (100, 100, 100),
          "nothing changed: the same picture")
    q = QImage(8, 8, QImage.Format_RGB32)
    q.fill(QColor(100, 100, 100))
    check(v._adjusted_qimage(q, (50, 0, 0, False)).pixelColor(0, 0).red() == 125, "the preview matches the saved file")
    check(v.apply_edits(im, [("adjust", (50, 0, 0, False))]).getpixel((0, 0))[0] == 125, "…(saved)")
    # a camera file: a small header, then its full-size preview (and a smaller one first, like real ones)
    small, big = io.BytesIO(), io.BytesIO()
    Image.new("RGB", (160, 120), (0, 0, 255)).save(small, "JPEG")
    Image.new("RGB", (1200, 800), (255, 0, 0)).save(big, "JPEG")
    raw = TMP / "photos" / "IMG_0001.CR2"
    raw.write_bytes(b"II*\x00" + b"\x00" * 500 + small.getvalue() + b"\x00" * 100 + big.getvalue())
    pic = v.load_picture(raw)
    check((pic.width(), pic.height()) == (1200, 800), f"a camera RAW file shows its full preview ({pic.width()})")
    svg = TMP / "photos" / "logo.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><rect width="40" height="20" '
                   'fill="#00ff00"/></svg>', "utf-8")
    pic = v.load_picture(svg)
    check(pic.width() > 40 and pic.pixelColor(pic.width() // 2, pic.height() // 2).green() > 200, "an SVG drawing")
    check(v.is_picture(raw) and v.is_picture(svg), "…both open in the Viewer")
    c = FakeController()
    win = v.ViewerWindow(c, TMP / "photos" / "a.jpg", c.icon)
    win.show()
    for _ in range(30):
        app.processEvents()
        time.sleep(0.03)
    win.toggle_compare()
    for _ in range(30):
        app.processEvents()
        time.sleep(0.03)
    check(win.view2.isVisible() and not win.view2.image.isNull(), "X: the next picture beside this one")
    win.toggle_compare()
    check(win.view2.isHidden(), "…and X again goes back")
    win.toggle_adjust()
    win.sliders["light"].setValue(60)
    win._adjust_preview()
    win.apply_adjust()
    check(win.edits and win.edits[-1][0] == "adjust", "Light and colour is a change to save")
    win.edits = []
    win.close()
    app.processEvents()


# ----------------------------------------------------------------------------- Space

def space_kinds():
    from corefold import space as sp
    root = TMP / "kinds"
    root.mkdir()
    (root / "movie.mp4").write_bytes(b"x" * 9000)
    (root / "sub").mkdir()
    (root / "sub" / "clip.mkv").write_bytes(b"x" * 5000)
    (root / "photo.jpg").write_bytes(b"x" * 3000)
    result = sp.scan(root)
    videos = [Path(p).name for _, p in result.biggest_kind["video"]]
    check(videos == ["movie.mp4", "clip.mkv"], f"the biggest videos, from every folder ({videos})")
    check([Path(p).name for _, p in result.biggest_kind["picture"]] == ["photo.jpg"], "…and pictures apart")


def space_more():
    from corefold import space as sp
    root = TMP / "drive"
    (root / "Old").mkdir(parents=True)
    (root / "Games").mkdir()
    old = root / "Old" / "backup.zip"
    with open(old, "wb") as fh:
        fh.truncate(3 << 20)
    two_years = time.time() - 2 * 365 * 86400
    os.utime(old, (two_years, two_years))
    with open(root / "Games" / "fresh.bin", "wb") as fh:
        fh.truncate(3 << 20)
    history = TMP / "space-history.json"
    first = sp.scan(root)
    check([Path(p).name for _, p, _u in first.old] == ["backup.zip"], f"big files unused for a year ({first.old})")
    sp.remember(first, history)
    check(not first.grew and not first.since, "the first look has nothing to compare with")
    with open(root / "Games" / "update.pak", "wb") as fh:
        fh.truncate(80 << 20)
    second = sp.scan(root)
    sp.remember(second, history)
    grew = [(Path(p).name, d >> 20) for d, p in second.grew]
    check(second.since > 0 and grew and grew[0][0] == "Games", f"what grew since the last look ({grew})")
    check(len(grew) == 1, "…only where it happened (not the drive too)")
    check(sp.admin_path("winupdate").name == "Download" and sp.admin_path("winold").name == "Windows.old",
          "Windows' own leftovers are fixed places")
    row = sp.Junk("winold", "x", "y", [root / "Games"], 1, 1, admin=True)
    check(sp.clean(row) == 0 and (root / "Games").exists(), "…only the administrator copy removes them")
    import json

    from corefold import uninstall as un
    helper = TMP / "Corefold" / "helper"
    helper.mkdir(parents=True)
    plan = helper / "space-clean-test.json"
    plan.write_text(json.dumps({"keys": ["evil", str(root)]}), "utf-8")
    check(un.helper_main(["--space-clean", str(plan)]) == 0, "the administrator copy takes a Space plan")
    answer = json.loads(plan.with_suffix(".result.json").read_text("utf-8"))
    check(answer == {"freed": 0} and (root / "Games").exists(), "…and cleans nothing it wasn't made to")
    stray = TMP / "plan.json"
    stray.write_text(json.dumps({"keys": ["winold"]}), "utf-8")
    check(un.helper_main(["--space-clean", str(stray)]) == 2, "…nor a plan from anywhere else")


def space():
    from corefold import space as sp
    local, roaming = Path(os.environ["LOCALAPPDATA"]), Path(os.environ["APPDATA"])
    for base, rel in ((roaming, "discord/Cache/Cache_Data"), (roaming, "Slack/Code Cache/js"),
                      (local, "D3DSCache/abc"), (local, "Microsoft/Windows/Explorer")):
        (base / rel).mkdir(parents=True, exist_ok=True)
    (roaming / "discord" / "Cache" / "Cache_Data" / "f_0001").write_bytes(b"x" * 50_000)
    (roaming / "discord" / "Local Storage").mkdir(parents=True)
    (roaming / "discord" / "Local Storage" / "keep.ldb").write_bytes(b"signed in")
    (roaming / "Slack" / "Code Cache" / "js" / "x").write_bytes(b"x" * 20_000)
    (local / "D3DSCache" / "abc" / "shader").write_bytes(b"x" * 30_000)
    (local / "Microsoft" / "Windows" / "Explorer" / "thumbcache_256.db").write_bytes(b"x" * 10_000)
    found = {j.key: j for j in sp.find_junk()}
    check("app:discord.exe" in found and found["app:discord.exe"].size >= 50_000, "Discord's cache is found")
    check("app:slack.exe" in found, "Slack's cache is found")
    check("gpu" in found and not found["gpu"].ticked, "graphics card caches are found (not ticked)")
    check("thumbs" in found and not found["thumbs"].ticked, "picture previews are found (not ticked)")
    sp.clean(found["app:discord.exe"])
    check(not (roaming / "discord" / "Cache" / "Cache_Data").exists() and
          (roaming / "discord" / "Cache").is_dir(), "cleaning empties the cache folder (it stays)")
    check((roaming / "discord" / "Local Storage" / "keep.ldb").exists(), "…and the sign-in stays")


def space_programs():
    from corefold import uninstall as un
    base = TMP / "Programs"
    (base / "Foo" / "bin").mkdir(parents=True, exist_ok=True)
    (base / "Foo Tools").mkdir(exist_ok=True)
    apps = [un.App(id="foo", name="Foo", location=str(base / "Foo")),
            un.App(id="foot", name="Foo Tools", location=str(base / "Foo Tools")),
            un.App(id="all", name="Everything", location=str(base.anchor))]
    check(un.app_at(base / "Foo", apps).name == "Foo", "Space knows which program a folder is")
    check(un.app_at(base / "Foo" / "bin", apps).name == "Foo", "…and a folder inside it")
    check(un.app_at(base / "Foo Tools", apps).name == "Foo Tools", "…not mixed up with a similar name")
    check(un.app_at(base, apps) is None, "…and a shared folder isn't anyone's")


def shortcut_program():
    from corefold import shellmenu
    from corefold import uninstall as un
    from corefold.settings import Settings
    base = TMP / "Programs"
    (base / "Bar").mkdir(parents=True, exist_ok=True)
    exe = base / "Bar" / "bar.exe"
    exe.write_bytes(b"MZ")
    apps = [un.App(id="bar", name="Bar", icon=f'"{exe}",0'),
            un.App(id="foo", name="Foo", location=str(base / "Foo"))]
    check(getattr(un.app_for_shortcut(exe, apps), "name", None) == "Bar",
          "a shortcut's program, found from its icon when it doesn't say where it's installed")
    check(un.app_for_shortcut(base / "Foo" / "bin" / "x.exe", apps).name == "Foo", "…or from where it's installed")
    check(un.app_for_shortcut(Path(os.environ.get("WINDIR", "C:\\Windows")) / "notepad.exe", apps) is None,
          "…and Windows' own programs aren't anyone's")
    store = un.App(id="news", name="News", store="Microsoft.BingNews_4.1_x64__8wekyb3d8bbwe",
                   family="Microsoft.BingNews_8wekyb3d8bbwe", location=str(base / "WindowsApps" / "Microsoft.BingNews_4.1"))
    apps.append(store)
    check(getattr(un.app_for_program(str(Path(store.location) / "News.exe"), apps), "name", None) == "News",
          "a window's program: a Store app, from the folder it runs from")
    check(getattr(un.app_for_program(str(exe), apps), "name", None) == "Bar", "…an installed program")
    check(un.app_for_program("", apps) is None, "…and nothing for no window")
    from corefold import appspage
    check(un.is_windows_extra(store) and not un.is_windows_extra(apps[0]) and
          appspage._shown(store, "extras") and not appspage._shown(apps[0], "extras"),
          "\"Extras that came with Windows\" shows only Windows' own extra apps")
    hunter = appspage.HunterButton()
    got = []
    hunter.found.connect(got.append)
    hunter.found.emit("")
    check(got == [""] and hunter.text(), "the \"Drag onto a window\" button")
    s = Settings()
    s.tools_off = []
    verbs = shellmenu.plan(s, lambda *a: " ".join(a))
    lnk = [v for v in verbs if v.name == "CorefoldUninstall"]
    check(len(lnk) == 1 and lnk[0].single and "--uninstall" in lnk[0].command,
          "\"Uninstall with Corefold\" on shortcuts")
    s.tools_off = ["uninstaller"]
    check(not any(v.name == "CorefoldUninstall" for v in shellmenu.plan(s, lambda *a: " ".join(a))),
          "…not while the Uninstaller is off")


def browser_extensions():
    import json as _json

    from corefold import appspage
    from corefold import extensions as ex
    local, roaming = TMP / "ext-local", TMP / "ext-roaming"
    prof = local / "Google" / "Chrome" / "User Data" / "Profile 1"
    (prof.parent / "Local State").parent.mkdir(parents=True, exist_ok=True)
    (prof.parent / "Local State").write_text(_json.dumps({"profile": {"info_cache": {"Profile 1": {"name": "Work"}}}}))

    def add(ext_id, version, manifest, locales=None):
        folder = prof / "Extensions" / ext_id / version
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "manifest.json").write_text(_json.dumps(manifest), encoding="utf-8")
        for lang, msgs in (locales or {}).items():
            (folder / "_locales" / lang).mkdir(parents=True, exist_ok=True)
            (folder / "_locales" / lang / "messages.json").write_text("\ufeff" + _json.dumps(msgs), encoding="utf-8")
    add("a" * 32, "1.0_0", {"name": "Old", "version": "1.0"})
    add("a" * 32, "1.10_0", {"name": "__MSG_appName__", "version": "1.10", "default_locale": "en",
                             "content_scripts": [{"matches": ["<all_urls>"]}]},
        {"en": {"appname": {"message": "Ad Stopper"}}})
    add("b" * 32, "2.0", {"name": "Notes", "version": "2.0", "permissions": ["storage"]})
    add("c" * 32, "1.0", {"name": "Browser part", "version": "1.0"})
    (prof / "Preferences").write_text(_json.dumps({"extensions": {"settings": {
        "b" * 32: {"state": 0}, "c" * 32: {"location": 5}}}}))
    ff = roaming / "Mozilla" / "Firefox" / "Profiles" / "x1y2.default-release"
    ff.mkdir(parents=True, exist_ok=True)
    (ff / "extensions.json").write_text(_json.dumps({"addons": [
        {"id": "u@o", "type": "extension", "location": "app-profile", "version": "3", "active": True,
         "defaultLocale": {"name": "uBlock"}, "userPermissions": {"origins": ["<all_urls>"]}},
        {"id": "sys@m", "type": "extension", "location": "app-system-defaults", "defaultLocale": {"name": "System"}}]}))
    found = ex.find(local, roaming)
    names = {e.name: e for e in found}
    check(set(names) == {"Ad Stopper", "Notes", "uBlock"},
          f"the browsers' extensions, without the browsers' own parts ({sorted(names)})")
    ad = names.get("Ad Stopper")
    check(ad is not None and ad.version == "1.10" and ad.all_sites and ad.profile == "Work",
          "…the newest copy, its name in its own language, the profile, and that it sees every website")
    check(names.get("Notes") is not None and not names["Notes"].on and not names["Notes"].all_sites,
          "…a turned-off one")
    check(names.get("uBlock") is not None and names["uBlock"].all_sites and names["uBlock"].browser == "Firefox",
          "…Firefox's")
    if ad is not None:
        cmd = ex.page_command(ad, "chrome.exe")
        check(cmd == ["chrome.exe", "--profile-directory=Profile 1", f"chrome://extensions/?id={'a' * 32}"],
              "Remove in the browser opens its extensions page on it")
    old = os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA")
    os.environ["LOCALAPPDATA"], os.environ["APPDATA"] = str(local), str(roaming)
    try:
        dlg = appspage.ExtensionsDialog(QIcon())
        check(dlg.tree.topLevelItemCount() == 3 and not dlg.b_remove.isEnabled(), "the Browser extensions window")
        dlg.tree.topLevelItem(0).setSelected(True)
        check(dlg.b_remove.isEnabled(), "…an extension picked")
        dlg.close()
    finally:
        for key, value in zip(("LOCALAPPDATA", "APPDATA"), old):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def tar_formats():
    from corefold import archives as ar
    src = TMP / "tarme"
    (src / "inner").mkdir(parents=True, exist_ok=True)
    (src / "a.txt").write_text("hello " * 1000)
    (src / "inner" / "b.txt").write_text("world")
    for fmt in ar.TAR_FORMATS:
        out = ar.pack_into([src], TMP / f"made.{fmt}", ctx(), fmt=fmt, level="max")[0]
        names = sorted(e.path for e in ar.list_entries(out) if not e.is_dir)
        check(names == ["tarme/a.txt", "tarme/inner/b.txt"], f"make a .{fmt} ({names})")
        check("No problems" in ar.test_archive(out, ctx()).note, "…test it")
        dest = TMP / f"un-{fmt}"
        res = ar.extract(out, ctx(dest=str(dest)))
        check((res.outputs[0] / "inner" / "b.txt").read_text() == "world", "…and unpack it")
        one = ar.extract(out, ctx(dest=str(TMP / f"one-{fmt}"), members=["tarme/inner/b.txt"]))
        got = sorted(str(f.relative_to(one.outputs[0])) for f in one.outputs[0].rglob("*") if f.is_file())
        check(got == [os.path.join("inner", "b.txt")], f"…or only one file from it ({got})")
    from corefold.dialogs import PackDialog
    dlg = PackDialog([src / "a.txt"], QIcon(), True)
    dlg.format.setCurrentIndex(dlg.format.findData("tar.zst"))
    dlg.password.setText("x")
    check(not dlg.password.isEnabled() and not dlg.split.isEnabled() and dlg.params()["password"] == "" and
          dlg.params()["format"] == "tar.zst", "Make an archive offers TAR (no password or parts for it)")
    dlg.close()
    import zstandard
    single = TMP / "notes.txt.zst"
    single.write_bytes(zstandard.ZstdCompressor().compress(b"just one file"))
    entries = ar.list_entries(single)
    check([(e.path, e.size) for e in entries] == [("notes.txt", 13)], "a single .zst file shows what's in it")
    res = ar.extract(single, ctx(dest=str(TMP / "unzst")))
    check(res.outputs[0].read_bytes() == b"just one file", "…and unpacks")
    broken = TMP / "broken.tar.zst"
    broken.write_bytes(zstandard.ZstdCompressor().compress(b"x" * 5000)[:-8])
    try:
        ar.test_archive(broken, ctx())
        check(False, "a damaged .tar.zst is found")
    except RuntimeError:
        check(True, "a damaged .tar.zst is found")


# ----------------------------------------------------------------------------- PDF

def pdf_stamps():
    import pypdfium2 as pdfium
    from pypdf import PdfWriter

    from corefold import pdfstamp as ps
    src = TMP / "doc.pdf"
    w = PdfWriter()
    w.add_blank_page(595, 842)
    w.add_blank_page(842, 595)
    w.add_blank_page(595, 842).rotate(90)
    with open(src, "wb") as fh:
        w.write(fh)
    out = TMP / "pdf-out"
    out.mkdir()
    res = ps.page_numbers(src, Ctx(Options(output_folder=out), params={"place": "br", "style": "of", "skip": 1}))
    pdf = pdfium.PdfDocument(str(res.outputs[0]))
    texts = [pdf[i].get_textpage().get_text_range().strip() for i in range(len(pdf))]
    check(texts == ["", "1 / 2", "2 / 2"], f"page numbers, the cover left out ({texts})")
    pdf.close()
    res = ps.watermark_pdf(src, Ctx(Options(output_folder=out), params={
        "text": "Draft", "logo": "", "position": "c", "size": "large", "opacity": 60}))
    pdf = pdfium.PdfDocument(str(res.outputs[0]))
    check(len(pdf) == 3, "the watermarked PDF keeps every page")
    for i in range(len(pdf)):
        im = pdf[i].render(scale=0.5).to_pil().convert("L")
        w_, h_ = im.size
        middle = im.crop((w_ // 3, h_ // 3, 2 * w_ // 3, 2 * h_ // 3))
        check(middle.getextrema()[0] < 200, f"page {i + 1}: the watermark is in the middle")
        check(im.crop((0, 0, w_ // 6, h_ // 6)).getextrema()[0] > 250, f"page {i + 1}: …and not in the corner")
    pdf.close()
    # a signature photographed on white paper: only the ink goes on the page
    from PIL import Image
    sig = Image.new("RGB", (400, 150), (250, 250, 248))
    sig.paste((20, 30, 110), (40, 60, 360, 90))
    sig.save(TMP / "sig.jpg")
    cut = ps.signature_picture(TMP / "sig.jpg")
    check(abs(cut.width - 320) < 12 and cut.getpixel((0, 0))[3] > 200, f"the signature is cut to its ink ({cut.size})")
    res = ps.sign_pdf(src, Ctx(Options(output_folder=out), params={"signature": str(TMP / "sig.jpg"), "pages": "last",
                                                                 "place": "br", "size": "medium"}))
    pdf = pdfium.PdfDocument(str(res.outputs[0]))
    last = pdf[2].render(scale=0.5).to_pil().convert("L")
    w_, h_ = last.size
    check(last.crop((w_ // 2, h_ * 3 // 4, w_, h_)).getextrema()[0] < 120, "signed at the bottom right of the last page")
    check(last.crop((0, 0, w_, h_ // 2)).getextrema()[0] > 250, "…and the paper isn't on the page")
    first = pdf[0].render(scale=0.5).to_pil().convert("L")
    check(first.getextrema()[0] > 250, "…the other pages aren't signed")
    pdf.close()


# ----------------------------------------------------------------------------- Player

def chapters():
    from corefold.player import chapters_of
    meta = TMP / "chapters.txt"
    meta.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1500\ntitle=Opening\n"
                    "[CHAPTER]\nTIMEBASE=1/1000\nSTART=1500\nEND=3000\ntitle=The end\n", "utf-8")
    ffmpeg("-i", str(VID / "bars.mp4"), "-i", str(meta), "-map_metadata", "1", "-map_chapters", "1", "-c", "copy",
           str(VID / "chapters.mkv"))
    found = chapters_of(VID / "chapters.mkv")
    check([(round(ms / 100), t) for ms, t in found] == [(0, "Opening"), (15, "The end")], f"chapters ({found})")
    check(chapters_of(VID / "quiet.mp4") == [], "…none in a file without them")


def player():
    try:
        from corefold.player import FIT_MODES, PlayerWindow
    except ImportError as e:
        print("(no Qt Multimedia here:", e, ")")
        return
    c = FakeController()
    try:
        win = PlayerWindow(c, VID / "bars.mp4")
    except Exception:
        print("(the player window can't open here:", traceback.format_exc().splitlines()[-1], ")")
        return
    win.show()
    app.processEvents()
    check(len(win.files) >= 2, f"the player lists the folder ({len(win.files)} files)")
    win.toggle_playlist()
    check(win.playlist.isVisible() and win.playlist.count() == len(win.files), "the playlist shows")
    win.cycle_fit()
    check(win.fit == FIT_MODES[1][0], "Z: fill the window")
    win.a_shuffle.setChecked(True)
    win._shuffle(True)
    check(win.files[0] == win.current() and win.index == 0, "shuffle keeps the file that's playing first")
    if (VID / "chapters.mkv").exists():
        win.open(VID / "chapters.mkv")
        for _ in range(60):
            app.processEvents()
            time.sleep(0.05)
            if win.seek.chapters:
                break
        check(len(win.seek.chapters) == 2, "the seek bar shows the chapters")
        win.chapter_step(1)
        check(win.seek.value() >= 1400 or win.player.position() >= 1400, "Ctrl+Right: the next chapter")
    win.open_link("not a link")
    check(not win.link, "a pasted text that isn't a link isn't played")
    win.close()
    app.processEvents()


if __name__ == "__main__" and len(sys.argv) > 1:
    for name in sys.argv[1:]:
        step(name, globals()[name])
else:
    step("videos", make_videos)
    for name, fn in (("video tools", video_tools), ("graphics card", graphics_card), ("fingerprint", fingerprint), ("passwords", passwords), ("tar formats", tar_formats),
                     ("capture", capture), ("recording pause", recording_pause), ("loopback", loopback),
                     ("clipboard", clipboard), ("clip list", clip_list), ("viewer", viewer),
                     ("space", space), ("space programs", space_programs), ("chapters", chapters), ("player", player),
                     ("saved texts", saved_texts), ("viewer strip", viewer_strip), ("viewer more", viewer_more), ("space kinds", space_kinds),
                     ("space more", space_more),
                     ("shortcut program", shortcut_program), ("browser extensions", browser_extensions),
                     ("pdf stamps", pdf_stamps)):
        step(name, fn)
print(f"featuretest: {len(problems)} problem(s)")
sys.exit(1 if problems else 0)
