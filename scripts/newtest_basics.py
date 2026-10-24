"""Checks the everyday things each tool should do (as other apps do), on a real Windows PC:
- the Uninstaller's right-click menu and saving its list, and its app updates (winget); which copies
  Space ticks;
- after converting: keep the original's date, send the original to the Recycle Bin;
- after extracting: open the folder, send the archive (all its parts) to the Recycle Bin; Extract to…;
- the Viewer going round a folder, its mouse side buttons, copying the path and dragging a picture out;
- File Organizer keeping a folder tidy by itself (and its Undo);
- the PDF page saying where new files go; Scan's hidden findings; a Player setting;
- the Clipboard's "paste as plain text" shortcut, pressed for real.

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
faulthandler.dump_traceback_later(420, exit=True)  # stuck somewhere: say where, and stop

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


def set_dates(path: Path, when: float):
    """A file that arrived then: its dates, and on Windows the date it was made (os.utime can't set that)."""
    os.utime(path, (when, when))
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wintypes.HANDLE
    h = k32.CreateFileW(str(path), 0x100, 0, None, 3, 0x80, None)  # FILE_WRITE_ATTRIBUTES, OPEN_EXISTING
    ft = wintypes.FILETIME()
    value = int(when * 10_000_000) + 116444736000000000
    ft.dwLowDateTime, ft.dwHighDateTime = value & 0xFFFFFFFF, value >> 32
    k32.SetFileTime(wintypes.HANDLE(h), ctypes.byref(ft), None, None)
    k32.CloseHandle(wintypes.HANDLE(h))


def _try(fn):
    try:
        return fn()
    except Exception as e:
        return e


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

    def app_updates():
        from PySide6.QtCore import Qt
        from corefold import appspage, appupdates as au
        sample = ("\r   - \r   \\ \rName              Id                Version  Available Source\n"
                  "----------------------------------------------------------------\n"
                  "Microsoft Edge    Microsoft.Edge    118.0.1  119.0.2   winget\n"
                  "A long app name…  Some.LongId…      1.0      2.0       winget\n"
                  "2 upgrades available.\n")
        got = au.parse(sample)
        check([(u.name, u.id, u.version, u.available) for u in got] == [("Microsoft Edge", "Microsoft.Edge", "118.0.1",
                                                                         "119.0.2")],
              f"winget's table is read (a cut-off id is left out) ({got})")
        if au.winget():
            import threading
            t0, box = time.time(), []
            threading.Thread(target=lambda: box.append(_try(au.check)), daemon=True).start()
            if wait(lambda: bool(box), 100):
                real = box[0]
                check(isinstance(real, list) and all(u.id and u.available for u in real),
                      f"this PC's apps with a newer version, from winget in {time.time() - t0:.0f} s: "
                      f"{[(u.id, u.version, u.available) for u in real][:8] if isinstance(real, list) else real!r}")
            else:
                print("note  winget took over 100 s (it may be setting itself up): not waited for", flush=True)
        else:
            print("note  no winget on this PC: only the table and the window are checked", flush=True)
        check(w.apps_page.b_updates.isVisible(), "the Uninstaller has an Updates… button")
        fake = [au.Update("Cf App One", "Cf.One", "1.0", "1.1"), au.Update("Cf App Two", "Cf.Two", "2.0", "3.0")]
        real_check, real_update = au.check, au.update
        au.check = lambda: list(fake)
        au.update = lambda u: (u.id == "Cf.One", "" if u.id == "Cf.One" else "Installer failed with exit code: 1603")
        try:
            d = appspage.UpdatesDialog(w.windowIcon())
            d.show()
            check(wait(lambda: not d.busy, 10) and d.tree.topLevelItemCount() == 2, "the updates are listed, ticked")
            d.update_ticked()
            check(wait(lambda: not d.busy, 10), "updating finishes")
            states = [d.tree.topLevelItem(i).text(3) for i in range(2)]
            check(states == ["Updated", "Couldn't update"] and "1603" in d.tree.topLevelItem(1).toolTip(3)
                  and d.changed, f"each one says how it went ({states}, {d.status.text()!r})")
            check(d.b_update.isEnabled(), "the one that failed can be tried again")
            d.close()
        finally:
            au.check, au.update = real_check, real_update
    step("app updates", app_updates)

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

    def keep_tidy():
        from corefold import organizer
        box = TMP / "Messy downloads"
        box.mkdir()
        old = time.time() - 3 * 86400
        for n in ("holiday.jpg", "letter.pdf", "song.mp3"):
            (box / n).write_bytes(b"x" * 100)
            set_dates(box / n, old)
        (box / "today.jpg").write_bytes(b"x" * 100)
        w.open_page("Organizer")
        qapp.processEvents()
        page = w.organizer_page
        page.set_folder(box)
        wait(lambda: not page.loading, 20)
        page.auto.setChecked(True)
        check(c.settings.organize_auto_folder == str(box) and c.organize_timer is not None,
              "ticking \"Keep this folder tidy by itself\" keeps it, and Corefold looks at it every hour")
        c.tidy_folder(now=True)
        check(wait(lambda: (box / "Pictures" / "holiday.jpg").exists() and getattr(c, "_tidy_job", 1) is None, 30),
              "the files that came days ago go into their folders")
        check((box / "today.jpg").exists(), "…and one that came today waits for tomorrow")
        check(c.settings.organize_auto_last > time.time() - 60, "the time it was tidied is kept (once a day)")
        c._untidy()
        check(wait(lambda: (box / "holiday.jpg").exists() and (box / "song.mp3").exists(), 30),
              "Undo puts them back")
        before = c.settings.organize_auto_last
        c.tidy_folder()
        check(getattr(c, "_tidy_job", None) is None and c.settings.organize_auto_last == before,
              "not again on the same day")
        page.auto.setChecked(False)
        check(not c.settings.organize_auto_folder and c.organize_timer is None, "unticking stops it")
        check(organizer.refused(box) == "", "(a folder Corefold may sort)")
    step("keeping a folder tidy", keep_tidy)

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

    def paste_plain():
        from PySide6.QtCore import QMimeData
        from PySide6.QtWidgets import QTextEdit
        check(wait(lambda: c.clip is not None, 10), "the Clipboard is running")
        key = c.clip.plain_key
        check(c.settings.clip_plain_key == "ctrl+alt+win+v" and (key.ok or os.name != "nt"),
              f"\"Paste as plain text\" listens for Ctrl + Alt + Win + V (ok={key.ok})")
        md = QMimeData()
        md.setText("Bold words")
        md.setHtml("<b style='color:red'>Bold words</b>")
        qapp.clipboard().setMimeData(md)
        qapp.processEvents()
        box = QTextEdit()
        box.setWindowTitle("Corefold paste test")
        box.resize(400, 200)
        box.show()
        box.raise_()
        box.activateWindow()
        box.setFocus()
        wait(lambda: box.hasFocus(), 3)
        front = False
        if os.name == "nt":
            import ctypes
            u = ctypes.windll.user32
            u.SetForegroundWindow(int(box.winId()))
            front = wait(lambda: u.GetForegroundWindow() == int(box.winId()), 3)
            if front:
                for vk in (0x11, 0x12, 0x5B, 0x56):   # Ctrl, Alt, Win, V: pressed the way a person does
                    u.keybd_event(vk, 0, 0, 0)
                for vk in (0x56, 0x5B, 0x12, 0x11):
                    u.keybd_event(vk, 0, 2, 0)
        if not front:
            print("      (the test window isn't in front: the shortcut is called directly)")
            c.clip.paste_plain()
        if os.name == "nt":
            check(wait(lambda: "Bold words" in box.toPlainText(), 8),
                  f"the shortcut pastes the copied words ({box.toPlainText()!r})")
            html = box.toHtml()
            check("font-weight:700" not in html and "#ff0000" not in html,
                  "…without their bold or colour")
        md = qapp.clipboard().mimeData()
        check(md.text() == "Bold words" and not md.hasHtml(), "…and what's copied is now the plain words")
        box.close()
        box.deleteLater()
    step("pasting as plain text", paste_plain)


step("the test", main)
print(f"newtest_basics: {len(problems)} problem(s)")
for p in problems:
    print("  - " + p)
sys.stdout.flush()
os._exit(1 if problems else 0)
