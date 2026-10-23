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

from PySide6.QtCore import QPoint, QPointF, QRect  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
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
    from corefold.capview import Canvas, PinnedPicture, Shape, draw_shape
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


# ----------------------------------------------------------------------------- Space

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


# ----------------------------------------------------------------------------- Player

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
    win.close()
    app.processEvents()


if __name__ == "__main__" and len(sys.argv) > 1:
    for name in sys.argv[1:]:
        step(name, globals()[name])
else:
    step("videos", make_videos)
    for name, fn in (("video tools", video_tools), ("fingerprint", fingerprint), ("passwords", passwords),
                     ("capture", capture), ("loopback", loopback), ("clipboard", clipboard), ("viewer", viewer),
                     ("space", space), ("space programs", space_programs), ("player", player)):
        step(name, fn)
print(f"featuretest: {len(problems)} problem(s)")
sys.exit(1 if problems else 0)
