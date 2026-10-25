"""Corefold File Organizer, tried for real on a Windows PC: a messy folder is made (every kind of file,
odd names, hidden and system files, shortcuts, half-finished downloads, a file a program has open, a
name that's already taken, a project folder), then planned, sorted, sorted again, and undone, and every
file is checked by its contents: nothing may be lost, changed, replaced or left half-moved. Then the page
is clicked through like a person would, in the language and look given.

Run: python scripts/organizetest.py <folder> [en|ar|fr|es|tr] [dark|light]
Prints one line per check and "organizetest: N problem(s)"; pictures of the page go in the folder."""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "organizetest").resolve()
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
LOOK = sys.argv[3] if len(sys.argv) > 3 else "dark"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-organizetest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(900, exit=True)  # stuck somewhere: say where, and stop

from corefold import i18n  # noqa: E402
from corefold import organizer as org  # noqa: E402
from corefold import settings as st  # noqa: E402
from corefold import tools as all_tools  # noqa: E402

WINDOWS = os.name == "nt"
problems = []
errors = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)


def step(name, fn):
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()


def attrib(path: Path, *flags):
    if WINDOWS:
        subprocess.run(["attrib", *flags, str(path)], check=True, capture_output=True)


def make_shortcut(link: Path, target: Path):
    if not WINDOWS:
        link.write_bytes(b"lnk")
        return
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}'); "
              f"$s.TargetPath = '{target}'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True)


def fill(path: Path, n: int = 0):
    """A file with contents of its own (so every file can be told apart by them)."""
    path.write_bytes(hashlib.sha256(f"{path.name}-{n}-{time.time_ns()}".encode()).digest() * (1 + n % 50))


def contents(folder: Path) -> dict[str, str]:
    """Every file under folder, by its contents: hash -> where it is (relative)."""
    out = {}
    for dirpath, _dirs, files in os.walk(folder):
        for f in files:
            p = Path(dirpath) / f
            out[hashlib.sha256(p.read_bytes()).hexdigest()] = str(p.relative_to(folder))
    return out


class Holder:
    """A program that has a file open the way Word does (others may read it, nobody may move it)."""

    def __init__(self, path: Path):
        self.path = path
        self.h = None
        if WINDOWS:
            import ctypes
            from ctypes import wintypes
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateFileW.restype = wintypes.HANDLE
            k.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
            self.k = k
            self.h = k.CreateFileW(str(path), 0x80000000 | 0x40000000, 1 | 2, None, 3, 0, None)  # (no share-delete)

    def close(self):
        if self.h is not None:
            self.k.CloseHandle(self.h)
            self.h = None


# ----------------------------------------------------------------------------- the messy folder

MESS = TMP / "Downloads"
EXPECT = {  # file: kind it goes to
    "IMG_2041.JPG": "pictures", "Screenshot 2026-10-21 101533.png": "pictures", "holiday.heic": "pictures",
    "صورة العائلة.jpg": "pictures", "dup.jpg": "pictures", "logo.SVG": "pictures",
    "lecture.mp4": "videos", "clip.MKV": "videos", "song.mp3": "music", "voice.m4a": "music",
    "invoice-october.pdf": "documents", "CV final v3.docx": "documents", "budget.xlsx": "documents",
    "slides.pptx": "documents", "notes.txt": "documents", "a.b.c.report.pdf": "documents", "تقرير.pdf": "documents",
    "read-only.pdf": "documents", "photos.zip": "archives", "backup.7z": "archives", "ubuntu.iso": "archives",
    "ChromeSetup.exe": "installers", "7z2601-x64.msi": "installers", "Cairo.ttf": "fonts",
    "x" * 180 + ".pdf": "documents",
}
STAY = {  # file: why it stays
    "data.xyz": "unknown", "README": "unknown", "Corefold.lnk": "shortcut", "site.url": "shortcut",
    "big.iso.crdownload": "downloading", "movie.mkv": "downloading", "movie.mkv.part": "downloading",
}
if WINDOWS:
    STAY["open in word.docx"] = "in_use"
HIDDEN = ["desktop.ini", "~$CV final v3.docx", "secret.jpg", "system.pdf"]


def wipe(folder: Path):
    """Removes a test folder, read-only and hidden files too."""
    if not folder.exists():
        return
    if WINDOWS:
        subprocess.run(["attrib", "-r", "-h", "-s", str(folder / "*"), "/s", "/d"], capture_output=True)
    shutil.rmtree(folder, onerror=lambda fn, p, _e: (os.chmod(p, 0o666), fn(p)))


def videos_file() -> str:
    """A FILE named like the Videos folder (in this language): the folder must get another name."""
    return org.folder_name("videos")


def make_mess():
    MESS.mkdir()
    for i, name in enumerate([*EXPECT, *STAY]):
        if name.endswith(".lnk"):
            make_shortcut(MESS / name, Path(sys.executable))
        else:
            fill(MESS / name, i)
    for i, name in enumerate(HIDDEN):
        fill(MESS / name, 100 + i)
    attrib(MESS / "secret.jpg", "+h")
    attrib(MESS / "system.pdf", "+s", "+h")
    attrib(MESS / "desktop.ini", "+h", "+s")
    attrib(MESS / "~$CV final v3.docx", "+h")
    attrib(MESS / "read-only.pdf", "+r")
    (MESS / "Pictures").mkdir()
    fill(MESS / "Pictures" / "dup.jpg", 300)         # (the loose dup.jpg must not replace it)
    fill(MESS / videos_file(), 301)                   # (a FILE called Videos: videos go somewhere else)
    (MESS / "Project").mkdir()
    fill(MESS / "Project" / "main.py", 302)          # (folders are never touched)
    fill(MESS / "Project" / "photo.jpg", 303)


def plan_and_sort():
    make_mess()
    before = contents(MESS)
    holder = Holder(MESS / "open in word.docx") if WINDOWS else None
    try:
        p = org.plan(MESS, known=True)
        check(not p.refused, "Downloads can be sorted")
        got = {i.path.name: i.kind for i in p.items if i.kind}
        wrong = {n: (got.get(n), k) for n, k in EXPECT.items() if got.get(n) != k}
        check(not wrong, f"every file goes to the right kind of folder {list(wrong.items())[:4]}")
        stays = {i.path.name: i.why for i in p.staying()}
        wrong = {n: (stays.get(n), w) for n, w in STAY.items() if stays.get(n) != w}
        check(not wrong, f"what stays, stays for the right reason {list(wrong.items())[:4]}")
        listed = {i.path.name for i in p.items}
        check(not listed & set(HIDDEN), f"hidden and system files aren't even listed {sorted(listed & set(HIDDEN))}")
        check(not listed & {"Project", "Pictures"}, "folders aren't listed")
        check(stays.get(videos_file()) == "unknown", "the file called Videos (no extension) stays")
        dup = next(i for i in p.items if i.path.name == "dup.jpg")
        check(dup.target.name == "dup (2).jpg" and dup.renamed, f"a name already taken gets a new one ({dup.target.name})")
        check(p.folders["pictures"].name == "Pictures" and "pictures" not in p.new_folders,
              "the Pictures folder that's already there is used")
        check(p.folders["videos"].name.startswith(org.folder_name("videos") + " (") and "videos" in p.new_folders,
              f"a file with a folder's name: the folder gets another name ({p.folders['videos'].name})")
        check(not (MESS / "Documents").exists() and len(contents(MESS)) == len(before), "planning changed nothing")
        # the person leaves one file out
        left = next(i for i in p.items if i.path.name == "budget.xlsx")
        left.on = False
        r = org.sort(p)
        check(r.moved == len(EXPECT) - 1 and not r.failed, f"sorted {r.moved} of {len(EXPECT) - 1}, failed {r.failed}")
        for name, _kind in EXPECT.items():
            if name == "budget.xlsx":
                continue
            item = next(i for i in p.items if i.path.name == name)
            if not item.target.is_file() or item.path.exists():
                check(False, f"{name} is in {item.target.parent.name} (and not where it was)")
        check((MESS / "budget.xlsx").is_file(), "the file left out stayed")
        for name in STAY:
            check((MESS / name).is_file(), f"{name} stayed")
        for name in HIDDEN:
            if not (MESS / name).is_file():
                check(False, f"{name} (hidden) stayed")
        check((MESS / "Project" / "photo.jpg").is_file(), "nothing inside folders moved")
        after = contents(MESS)
        check(set(after) == set(before) and len(after) == len(before),
              f"every file is still there, unchanged ({len(after)} of {len(before)})")
        if WINDOWS:
            import stat
            ro = MESS / org.folder_name("documents") / "read-only.pdf"
            check(ro.is_file() and not os.stat(ro).st_mode & stat.S_IWRITE, "a read-only file moved and is still read-only")
        again = org.plan(MESS, known=True)
        moving = [i.path.name for i in again.moving()]
        check(moving == ["budget.xlsx"], f"looking again: only the file left out is still loose {moving}")
        check(org.last_sort() is not None and len(org.last_sort()["moves"]) == r.moved, "the sort is kept for Undo")
    finally:
        if holder:
            holder.close()
    # after the sort: something new takes an old name, one sorted file is deleted, one is open
    fill(MESS / "invoice-october.pdf", 400)
    (MESS / org.folder_name("music") / "song.mp3").unlink()
    gone_hash = None
    for h, rel in before.items():
        if rel == "song.mp3":
            gone_hash = h
    held = Holder(MESS / org.folder_name("fonts") / "Cairo.ttf") if WINDOWS else None
    u = org.undo()
    if WINDOWS:
        check(len(u.failed) == 1 and org.last_sort() is not None and len(org.last_sort()["moves"]) == 1,
              f"undo with a file open: that one is kept for another Undo ({u.failed})")
        held.close()
        u2 = org.undo()
        check(u2.back == 1 and not u2.failed and org.last_sort() is None, "pressing Undo again puts it back too")
        check((MESS / org.folder_name("fonts")).exists() is False, "and its folder is removed then")
    check(u.gone == 1, f"undo: the deleted file is counted as gone ({u.gone})")
    check(u.renamed == 1 and (MESS / "invoice-october (2).pdf").is_file(),
          "undo: a file whose name was taken again comes back with a new name")
    for name in EXPECT:
        if name in ("song.mp3", "budget.xlsx"):
            continue
        if not (MESS / name).is_file() and name != "invoice-october.pdf":
            check(False, f"undo: {name} is back")
    check((MESS / "Pictures" / "dup.jpg").is_file() and (MESS / "Pictures").is_dir(),
          "undo: the Pictures folder that was already there stays, with its own file")
    check((MESS / videos_file()).is_file(), "undo: the file called Videos is untouched")
    made = [org.folder_name(k) for k in ("documents", "music", "archives", "installers")]
    check(not any((MESS / m).exists() for m in made), "undo: the folders it made are gone again")
    after = contents(MESS)
    want = set(before) - {gone_hash}
    check(want <= set(after), f"undo: every file is back, unchanged ({len(want & set(after))} of {len(want)})")


def folders_not_sorted():
    home = Path.home()
    cases = [(Path(os.environ.get("SystemDrive", "C:") + "\\") if WINDOWS else Path("/"), "drive"),
             (home, "home"), (Path(os.environ.get("APPDATA", str(TMP))), "system")]
    if WINDOWS:
        cases += [(Path(os.environ["SystemRoot"]), "system"), (Path(os.environ["SystemRoot"]) / "System32", "system"),
                  (Path(os.environ["ProgramFiles"]), "system"), (Path(os.environ["ProgramData"]), "system"),
                  (home / "AppData" / "Local" / "Temp", "system")]
    for folder, why in cases:
        if folder.is_dir():
            got = org.refused(folder)
            check(got == why, f"{folder} isn't sorted ({got or 'allowed!'})")
    proj = TMP / "code"
    proj.mkdir()
    (proj / ".git").mkdir()
    fill(proj / "a.png")
    check(org.refused(proj) == "program", "a project folder isn't sorted")
    p = org.plan(proj)
    check(p.refused and not p.items and org.sort(p).moved == 0 and (proj / "a.png").is_file(),
          "and sorting it does nothing")
    app = TMP / "portable app"
    app.mkdir()
    for n in ("app.exe", "Qt6Core.dll", "readme.pdf"):
        fill(app / n)
    check(org.refused(app) == "program", "a program's own folder isn't sorted")
    dl = TMP / "dl-with-dll"
    dl.mkdir()
    fill(dl / "downloaded.dll")
    fill(dl / "a.png")
    check(not org.refused(dl, known=True), "Downloads with a downloaded .dll in it can still be sorted")
    check(org.refused(TMP / "not-there") == "missing", "a folder that isn't there says so")


def languages():
    """Sorted in English once, then in Arabic: the English folders are used again (not a second set)."""
    f = TMP / "Desktop"
    f.mkdir()
    fill(f / "one.png", 1)
    fill(f / "one.pdf", 2)
    i18n.set_language("en")
    org.sort(org.plan(f, known=True))
    fill(f / "two.png", 3)
    fill(f / "two.mp3", 4)
    i18n.set_language("ar")
    p = org.plan(f, known=True)
    check(p.folders["pictures"].name == "Pictures", f"in Arabic, the Pictures folder made in English is used "
                                                    f"({p.folders['pictures'].name})")
    check(p.folders["music"].name == "الموسيقى" and "\u200f" not in p.folders["music"].name,
          f"a new folder gets its Arabic name, with no invisible marks in it ({p.folders['music'].name!r})")
    org.sort(p)
    check((f / "Pictures" / "two.png").is_file() and (f / "الموسيقى" / "two.mp3").is_file(), "and they're sorted there")
    check(org.undo().back == 2, "undo puts back only the last sort")
    check((f / "Pictures" / "one.png").is_file(), "the sort before it stays sorted")
    i18n.set_language(LANG)


def big_folder():
    f = TMP / "big"
    f.mkdir()
    exts = ["jpg", "png", "pdf", "docx", "mp4", "mp3", "zip", "exe", "xyz"]
    for n in range(3000):
        (f / f"file {n:04}.{exts[n % len(exts)]}").write_bytes(str(n).encode())
    t = time.perf_counter()
    p = org.plan(f)
    t_plan = time.perf_counter() - t
    t = time.perf_counter()
    r = org.sort(p)
    t_sort = time.perf_counter() - t
    t = time.perf_counter()
    u = org.undo()
    t_undo = time.perf_counter() - t
    print(f"3000 files: plan {t_plan:.2f} s, sort {t_sort:.2f} s, undo {t_undo:.2f} s", flush=True)
    check(r.moved == 3000 - 3000 // 9 and u.back == r.moved, f"3,000 files: all sorted and all put back ({r.moved})")
    check(len(list(f.iterdir())) == 3000, "and the folder is as it was")
    check(t_plan < 15 and t_sort < 30 and t_undo < 30, "quick enough")


# ----------------------------------------------------------------------------- the page, clicked like a person

def page():
    s = st.Settings()
    s.first_run = False
    s.tour_done = True
    s.check_updates = False
    s.language = LANG
    s.appearance = LOOK
    s.known_tools = sorted(t.id for t in all_tools.ready())
    s.tools_off = ["organizer"]
    s.save()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    i18n.set_language(LANG)
    qapp = QApplication.instance() or QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    if i18n.is_rtl():
        qapp.setLayoutDirection(Qt.RightToLeft)
    from corefold import app as A
    A.setup_logging()

    def hook(t, e, tb):
        text = "".join(traceback.format_exception(t, e, tb))
        errors.append(text)
        print("ERROR " + text, flush=True)
    sys.excepthook = hook

    def settle(seconds=0.3):
        end = time.time() + seconds
        while time.time() < end:
            qapp.processEvents()
            time.sleep(0.02)

    def until(cond, seconds=15):
        end = time.time() + seconds
        while time.time() < end:
            qapp.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return False

    wipe(MESS)
    wipe(TMP / "Desktop")
    make_mess()
    (TMP / "Desktop").mkdir()
    fill(TMP / "Desktop" / "shot.png", 7)
    before = contents(MESS)
    c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
    c.show_window("Home")
    w = c.window
    w.resize(1100, 740)
    settle(0.6)
    check(not w.shows("Organizer"), "switched off: not shown")
    check("corefold.organizerpage" not in sys.modules, "switched off: its page isn't loaded at all")
    c.set_tool("organizer", True)
    settle(0.6)
    w = c.window
    check(w.shows("Organizer"), "switched on: shown in its section")
    from corefold import organizerpage as OP
    OP.known_folder = lambda key: {"downloads": MESS, "desktop": TMP / "Desktop"}.get(key)
    w.resize(1100, 740)
    w.open_page("Organizer")
    pg = w.organizer_page
    check(pg is not None, "the page opens")
    check(until(lambda: not pg.loading and pg.plan is not None), "the plan comes up")
    check(until(lambda: pg.cards["desktop"].line == OP.to_sort_text(1)),
          f"the Desktop card says what's waiting ({pg.cards['desktop'].line})")
    settle(0.5)
    w.grab().save(str(OUT / f"organizer-{LANG}-{LOOK}.png"))
    moving = len(pg.plan.moving())
    want = len(EXPECT) + ("open in word.docx" in STAY)  # (nothing holds it open this time)
    check(moving == want, f"the page shows {moving} files to sort ({want} expected)")
    check(pg.sort_btn.isEnabled() and str(moving) in pg.sort_btn.text(), f"the Sort button says how many ({pg.sort_btn.text()})")
    heads = [pg.tree.topLevelItem(i) for i in range(pg.tree.topLevelItemCount())]
    check(len(heads) == len({*EXPECT.values()}) + 1, f"one line per folder, and one for what stays ({len(heads)})")
    # leave Fonts out, and one picture
    fonts = next(h for h in heads if h.data(0, Qt.UserRole) == "fonts")
    fonts.setCheckState(0, Qt.Unchecked)
    pics = next(h for h in heads if h.data(0, Qt.UserRole) == "pictures")
    pics.setExpanded(True)
    pics.child(0).setCheckState(0, Qt.Unchecked)
    settle(0.3)
    check(len(pg.plan.moving()) == moving - 2 and str(moving - 2) in pg.sort_btn.text(),
          "leaving a folder and a file out: the count goes down")
    check(org.kinds_off() == {"fonts"}, "the folder left out is remembered")
    w.grab().save(str(OUT / f"organizer-choose-{LANG}-{LOOK}.png"))
    left_pic = pics.child(0).data(0, OP.FILE_ROLE).path
    QTest.mouseClick(pg.sort_btn, Qt.LeftButton)
    check(until(lambda: not pg.busy and not pg.loading, 30), "sorting: done")
    settle(0.4)
    check(not pg.note.isHidden() and pg.problem.isHidden(), f"it says what it did ({pg.note_text.text()!r})")
    check((MESS / "Cairo.ttf").is_file() and left_pic.is_file(), "what was left out stayed")
    check((MESS / org.folder_name("documents") / "notes.txt").is_file(), "the rest is in its folders")
    check(pg.undo_btn.isEnabled() and pg.last.text(), "Undo is there, with what the last sort was")
    still = {i.path: i.on for i in pg.plan.items if i.kind}
    check(still == {left_pic: False, MESS / "Cairo.ttf": False} and not pg.plan.moving() and not pg.sort_btn.isEnabled(),
          f"looking again: the picture and the folder left out are listed, still left out ({still})")
    w.grab().save(str(OUT / f"organizer-sorted-{LANG}-{LOOK}.png"))
    QTest.mouseClick(pg.undo_btn, Qt.LeftButton)
    check(until(lambda: not pg.busy and not pg.loading, 30), "undo: done")
    settle(0.4)
    check(set(contents(MESS)) == set(before), "undo from the page: every file is back where it was")
    check(not pg.undo_btn.isEnabled(), "and there's nothing left to undo")
    w.grab().save(str(OUT / f"organizer-undone-{LANG}-{LOOK}.png"))
    # a folder Windows needs: nothing to sort, and the page says why
    pg.set_folder(Path(os.environ.get("SystemRoot", "/usr")) if WINDOWS else Path("/usr"))
    check(until(lambda: not pg.loading and pg.plan is not None and pg.plan.refused), "Windows' folder: refused")
    settle(0.3)
    check(not pg.problem.isHidden() and not pg.sort_btn.isEnabled(), "it says why, and Sort can't be pressed")
    w.grab().save(str(OUT / f"organizer-refused-{LANG}-{LOOK}.png"))
    QTest.mouseClick(pg.cards["desktop"], Qt.LeftButton)
    check(until(lambda: not pg.loading and pg.plan is not None and pg.plan.folder == TMP / "Desktop"),
          "clicking the Desktop card shows the Desktop")
    check(len(pg.plan.moving()) == 1 and pg.problem.isHidden(), "with its one file")
    QTest.mouseClick(pg.cards["downloads"], Qt.LeftButton)
    check(until(lambda: not pg.loading and pg.plan is not None and pg.plan.folder == MESS), "and back to Downloads")
    pg.tree.expandAll()
    for size in ((760, 520), (1500, 900)):
        w.resize(*size)
        settle(0.5)
        r = pg.sort_btn.mapTo(w, pg.sort_btn.rect().bottomRight())
        l = pg.sort_btn.mapTo(w, pg.sort_btn.rect().topLeft())
        check(0 <= l.x() and r.x() <= w.width() and r.y() <= w.height() and pg.sort_btn.isVisible(),
              f"at {size[0]}×{size[1]} the Sort button is inside the window")
        scroll = pg.layout().itemAt(0).widget()
        check(scroll.widget().width() <= scroll.viewport().width(),
              f"at {size[0]}×{size[1]} nothing sticks out sideways ({scroll.widget().width()} in {scroll.viewport().width()})")
        cut = [card.key for card in pg.cards.values() if card.width() < 120]
        check(not cut, f"at {size[0]}×{size[1]} the folder cards are wide enough {cut}")
        w.grab().save(str(OUT / f"organizer-{size[0]}-{LANG}-{LOOK}.png"))
    w.open_page("Home")
    settle(0.3)
    fill(MESS / "new download.pdf", 9)
    w.open_page("Organizer")
    check(until(lambda: not pg.loading and any(i.path.name == "new download.pdf" for i in pg.plan.moving())),
          "coming back to the page: a new download is in the plan")
    c.set_tool("organizer", False)
    settle(0.5)
    check(not c.window.shows("Organizer"), "switched off again: not shown")
    check(not errors, f"no errors behind any click ({len(errors)})")


i18n.set_language(LANG)
for name, fn in (("plan and sort", plan_and_sort), ("folders not sorted", folders_not_sorted),
                 ("languages", languages), ("big folder", big_folder), ("page", page)):
    step(name, fn)
(OUT / f"organizetest-{LANG}.txt").write_text("\n".join(problems) or "none", "utf-8")
print(f"organizetest: {len(problems)} problem(s)", flush=True)
faulthandler.disable()
from testexit import finish  # (Qt can crash in Windows' tidy-up at exit, after every check passed)
finish(1 if problems else 0)
