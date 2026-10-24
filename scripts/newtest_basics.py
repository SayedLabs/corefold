"""Checks the everyday settings each tool should have (as other apps do), on a real Windows PC:
the Uninstaller's right-click menu and saving its list; which copies Space ticks; after converting, keep the original's date and send the original to the Recycle Bin; after extracting, open the folder and send the archive (all its parts) to the Recycle Bin; the Viewer
going round a folder; the PDF page saying where new files go.

Run from the repository's folder: python scripts/newtest_basics.py
Everything happens in a temporary folder (the user's real settings are left alone). Prints one line
per check and "newtest_basics: N problem(s)"."""
import os
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="corefold-basicstest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(280, exit=True)  # stuck somewhere: say where, and stop

problems = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)
    return ok


def step(name, fn):
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()


def main():
    from corefold import settings as st
    from corefold import tools as all_tools
    s = st.Settings()
    s.first_run, s.tour_done, s.check_updates = False, True, False
    s.known_tools = sorted(t.id for t in all_tools.ready())
    s.save()

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    qapp = QApplication(sys.argv)
    from corefold import app as A
    from corefold import archives as ar
    from corefold import winutil
    from corefold.converters import catalog
    revealed = []
    winutil.reveal_in_explorer = lambda p: revealed.append(Path(p))  # (no Explorer window on the test PC)
    c = A.Controller(qapp, QIcon())
    c.show_window("ArchiveSettings")
    w = c.window

    def wait(cond, secs=30):
        end = time.time() + secs
        while time.time() < end and not cond():
            qapp.processEvents()
            time.sleep(0.05)
        return cond()

    def parts():
        box = TMP / "parts"
        box.mkdir()
        for n in ("a.7z.001", "a.7z.002", "ab.7z.001", "b.part1.rar", "b.part2.rar", "bb.part1.rar", "c.zip", "c.z01",
                  "cz.z01", "x.txt"):
            (box / n).write_text("x")
        names = lambda p: [q.name for q in ar.archive_parts(box / p)]  # noqa: E731
        check(names("a.7z.002") == ["a.7z.001", "a.7z.002"], f"a split 7z is all its numbered parts ({names('a.7z.002')})")
        check(names("b.part1.rar") == ["b.part1.rar", "b.part2.rar"], f"a split RAR is all its parts ({names('b.part1.rar')})")
        check(names("c.zip") == ["c.z01", "c.zip"], f"a split ZIP is the .zip and its .z01 … ({names('c.zip')})")
        check(names("x.txt") == ["x.txt"], "a file that isn't split is only itself")
    step("an archive's parts", parts)

    def extract_settings():
        w.extract_reveal.setChecked(True)
        w.extract_recycle.setChecked(True)
        check(c.settings.extract_reveal and c.settings.extract_recycle, "the two Archive settings are kept")
        box = TMP / "extract"
        box.mkdir()
        z = box / "photos.zip"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("one.txt", "1")
            f.writestr("two.txt", "2")
        tool = next(t for t in catalog(False)["archive"] if t.key == "archive:extract")
        c.start_job(tool, [z], {})
        check(wait(lambda: not z.exists()), "the archive goes to the Recycle Bin once it's extracted")
        check((box / "photos" / "one.txt").is_file(), "…after everything came out")
        check(wait(lambda: bool(revealed), 5) and revealed[-1] == box / "photos",
              f"the folder with what came out is shown ({revealed})")

        bad = box / "broken.zip"
        bad.write_bytes(b"PK\x03\x04 this is not really a zip")
        revealed.clear()
        c.start_job(tool, [bad], {})
        wait(lambda: False, 4)
        check(bad.exists(), "an archive that couldn't be extracted stays where it is")
        check(not revealed, "…and no folder is opened")

        w.extract_reveal.setChecked(False)
        w.extract_recycle.setChecked(False)
        z2 = box / "keep.zip"
        with zipfile.ZipFile(z2, "w") as f:
            f.writestr("k.txt", "k")
        c.start_job(tool, [z2], {})
        check(wait(lambda: (box / "k.txt").is_file()), "with both off, it's extracted…")
        wait(lambda: False, 1)
        check(z2.exists() and not revealed, "…and the archive stays, nothing opened")

        from PySide6.QtWidgets import QFileDialog
        elsewhere = TMP / "picked folder"
        elsewhere.mkdir()
        QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(elsewhere))
        to = next((t for t in catalog(False)["archive"] if t.key == "archive:to"), None)
        if check(to is not None, "archives have \"Extract to…\""):
            c.tool_chosen(to, [z2])
            check(wait(lambda: (elsewhere / "k.txt").is_file()), "\"Extract to…\" puts it in the folder picked")
            check(not (box / "k (2).txt").exists(), "…and not next to the archive")
    step("after extracting", extract_settings)

    def convert_settings():
        from PySide6.QtGui import QColor, QImage
        w.open_page("ConvOptions")
        qapp.processEvents()
        w.keep_dates.setChecked(True)
        w.convert_recycle.setChecked(True)
        check(c.settings.keep_dates and c.settings.convert_recycle, "the two Converter settings are kept")
        box = TMP / "convert"
        box.mkdir()
        src = box / "holiday.png"
        img = QImage(60, 40, QImage.Format_RGB32)
        img.fill(QColor("#a53"))
        img.save(str(src))
        old = time.time() - 3 * 365 * 86400
        os.utime(src, (old, old))
        tool = next(t for t in catalog(False)["image"] if t.key == "image:jpg")
        c.start_job(tool, [src], {})
        made = box / "holiday.jpg"
        check(wait(lambda: not src.exists()), "the original goes to the Recycle Bin once it's converted")
        check(made.is_file(), "…after the new file is there")
        check(made.is_file() and abs(made.stat().st_mtime - old) < 3,
              "the new file has the original's \"Date modified\"")

        w.keep_dates.setChecked(False)
        w.convert_recycle.setChecked(False)
        src2 = box / "keep.png"
        img.save(str(src2))
        os.utime(src2, (old, old))
        c.start_job(tool, [src2], {})
        check(wait(lambda: (box / "keep.jpg").is_file()), "with both off, it's converted…")
        wait(lambda: False, 1)
        check(src2.exists() and (box / "keep.jpg").stat().st_mtime > old + 86400,
              "…the original stays, and the new file has today's date")
    step("after converting", convert_settings)

    def uninstaller_list():
        import csv
        from PySide6.QtWidgets import QFileDialog, QMenu
        w.open_page("Apps")
        page = w.apps_page
        page.ensure_loaded()
        if not check(wait(lambda: page.loaded and page.tree.topLevelItemCount() > 0, 120), "the list of apps loads"):
            return
        out = TMP / "apps.csv"
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(out), ""))
        page.save_list()
        rows = list(csv.reader(out.open(encoding="utf-8-sig"))) if out.is_file() else []
        shown = sum(1 for i in range(page.tree.topLevelItemCount()) if not page.tree.topLevelItem(i).isHidden())
        check(len(rows) == shown + 1 and rows[0][0] == "Name", f"\"Save the list…\" saves every app shown "
                                                                 f"({len(rows) - 1} of {shown})")
        seen = []

        class Menu(QMenu):  # (shows nothing: only says what it would offer)
            def exec(self, *_):
                seen.extend(x.text() for x in self.actions() if x.text())
        from corefold import appspage
        appspage.QMenu, real = Menu, appspage.QMenu
        try:
            page._menu(page.tree.visualItemRect(page.tree.topLevelItem(0)).center())
        finally:
            appspage.QMenu = real
        check("Uninstall" in seen and any(t.startswith("Search the web") for t in seen) and "Copy the name" in seen,
              f"right-clicking an app offers Uninstall, a web search and copying its name ({seen})")
    step("the Uninstaller's list", uninstaller_list)

    def space_copies():
        from PySide6.QtWidgets import QMenu
        from corefold import spacepage
        box = TMP / "copies"
        box.mkdir()
        now = time.time()
        for n, age in (("old.bin", 300), ("middle.bin", 200), ("new.bin", 100)):
            (box / n).write_bytes(b"same bytes " * 12000)  # (Space only compares files over 100 KB)
            os.utime(box / n, (now - age * 86400, now - age * 86400))
        w.open_page("Space")
        qapp.processEvents()
        page = w.space_page
        page.find_dupes([box])
        if not check(wait(lambda: page.dupe_tree.topLevelItemCount() == 1, 60), "the three copies are found"):
            return
        head = page.dupe_tree.topLevelItem(0)
        ticked = lambda: [Path(p).name for p, _ in page._ticked()]  # noqa: E731
        check(sorted(ticked()) == ["middle.bin", "new.bin"], f"to begin with the oldest copy stays ({ticked()})")
        page._dupe_keep("newest")
        check(sorted(ticked()) == ["middle.bin", "old.bin"], f"\"Keep the newest copy of each\" ({ticked()})")
        page._dupe_keep(None)
        check(ticked() == [] and not page.dupe_remove.isEnabled(), "\"Untick everything\"")
        page._dupe_keep(None, head.child(1))
        check(sorted(ticked()) == ["new.bin", "old.bin"], f"\"Keep this one\" ticks the other copies ({ticked()})")
        seen = []

        class Menu(QMenu):
            def exec(self, *_):
                seen.extend(x.text() for x in self.actions() if x.text())
        spacepage.QMenu, real = Menu, spacepage.QMenu
        try:
            page._dupe_menu(page.dupe_tree.visualItemRect(head.child(0)).center())
        finally:
            spacepage.QMenu = real
        check(any(t.startswith("Keep this one") for t in seen) and "Copy the path" in seen,
              f"right-clicking a copy offers to keep it ({seen})")
    step("copies found by Space", space_copies)

    def viewer_loop():
        from PySide6.QtGui import QColor, QImage
        box = TMP / "pictures"
        box.mkdir()
        for n in ("a.png", "b.png", "c.png"):
            img = QImage(40, 30, QImage.Format_RGB32)
            img.fill(QColor("#3a7"))
            img.save(str(box / n))
        c.settings.viewer_loop = False
        c.open_viewer(box / "c.png")
        v = c.viewers[-1] if c.viewers else None
        if not check(v is not None, "the Viewer opens"):
            return
        wait(lambda: v.current().name == "c.png", 5)
        v.go(1)
        check(v.current().name == "c.png" and not v.b_next.isEnabled(), "without going round, the last picture is the end")
        c.settings.viewer_loop = True
        v.show_current()
        v.go(1)
        check(v.current().name == "a.png", "going round: after the last comes the first")
        v.go(-1)
        check(v.current().name == "c.png", "…and before the first comes the last")

        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtTest import QTest

        def press(button, where=None, kind=QEvent.MouseButtonPress, held=None):
            where = where or QPointF(v.view.width() / 2, v.view.height() / 2)
            ev = QMouseEvent(kind, where, v.view.mapToGlobal(where), button,
                             held if held is not None else button, Qt.NoModifier)
            qapp.sendEvent(v.view, ev)
        press(Qt.ForwardButton)
        check(v.current().name == "a.png", f"the mouse's forward button goes to the next picture ({v.current().name})")
        press(Qt.BackButton)
        check(v.current().name == "c.png", "…and its back button to the one before")
        v.copy_path()
        check(qapp.clipboard().text() == str(v.current()), "Ctrl+Shift+C copies the picture's path")
        dragged = []
        v.drag_out = lambda: dragged.append(v.current())
        v.view.fit_view()
        mid = QPointF(v.view.width() / 2, v.view.height() / 2)
        press(Qt.LeftButton, mid)
        press(Qt.NoButton, mid + QPointF(40, 0), QEvent.MouseMove, Qt.LeftButton)
        press(Qt.LeftButton, mid + QPointF(40, 0), QEvent.MouseButtonRelease, Qt.NoButton)
        check(dragged == [v.current()], f"dragging the picture takes its file out of the window ({dragged})")
        QTest.qWait(50)
        v.close()
    step("the Viewer going round", viewer_loop)

    def pdf_where():
        from corefold.pdfpage import PdfPage
        c.settings.save_mode = "folder"
        c.settings.output_folder = str(TMP / "My results")
        w.open_page("PDF")
        qapp.processEvents()
        page = w.findChildren(PdfPage)[0]
        check("My results" in page.where.text(), f"the PDF page says the new files go to the chosen folder ({page.where.text()})")
        c.settings.save_mode = "beside"
        w.open_page("Home")
        w.open_page("PDF")
        qapp.processEvents()
        check("My results" not in page.where.text(), "…and next to the original when that's the choice")
    step("where PDF results go", pdf_where)

    def scan_unhide():
        from corefold import scan
        from corefold.scanpage import ScanPage
        scan.ignore("startup-hidden|C:\\nothing\\here.exe")
        w.open_page("Scan")
        qapp.processEvents()
        page = w.findChildren(ScanPage)[0]
        b = page.unhide_btn
        check(b.isVisible() and "1" in b.text(), f"Scan offers to show what was hidden ({b.text()!r})")
        b.click()
        check(not scan.load_ignored() and not b.isEnabled(), "…and forgets every \"Don't show this again\"")
    step("hidden scan findings", scan_unhide)

    def player_setting():
        w.open_page("PlayerSettings")
        qapp.processEvents()
        w.player_pause_minimized.setChecked(True)
        check(c.settings.player_pause_minimized, "the Player keeps \"pause a video while minimised\"")
    step("the Player setting", player_setting)


step("the test", main)
print(f"newtest_basics: {len(problems)} problem(s)")
for p in problems:
    print("  - " + p)
sys.stdout.flush()
os._exit(1 if problems else 0)
