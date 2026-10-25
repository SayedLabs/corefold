"""Corefold PDF, tried for real: every tool on made-up PDFs (each page a colour of its own, so the order
and turns of the pages in every result can be checked by looking at them), then the page in the window
clicked like a person would: cards, the page board, turning, deleting, undo, typing pages, merging a
locked PDF with others, saving, the big preview, pictures dropped on the page, and the Store.

Run: python scripts/pdftest.py <folder> [en|ar|fr|es|tr] [dark|light]
Prints one line per check and "pdftest: N problem(s)"; pictures of the page go in the folder."""
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "pdftest").resolve()
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
LOOK = sys.argv[3] if len(sys.argv) > 3 else "dark"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-pdftest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(900, exit=True)  # stuck somewhere: say where, and stop

from PIL import Image, ImageDraw  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from corefold import pdftools as pt  # noqa: E402
from corefold import settings as st  # noqa: E402
from corefold import tools as all_tools  # noqa: E402
from corefold.archives import NeedPassword  # noqa: E402
from corefold.core import Ctx, Options  # noqa: E402

problems = []
errors = []
FILES = TMP / "files"
FILES.mkdir()
COLORS = [(230, 60, 60), (60, 160, 70), (60, 90, 220), (240, 200, 40), (150, 70, 190), (40, 190, 190),
          (240, 130, 30), (120, 120, 120), (250, 120, 180), (90, 60, 30), (20, 20, 90), (200, 240, 200)]


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


def make_pdf(name: str, colors: list[int], size=(600, 800)) -> Path:
    """Page k is filled with COLORS[colors[k]], with a white mark in its top-left corner (to see turns)."""
    pages = []
    for c in colors:
        im = Image.new("RGB", size, COLORS[c])
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, size[0] // 4, size[1] // 8), fill="white")
        d.text((size[0] // 2, size[1] // 2), f"{name} {c}", fill="black")
        pages.append(im)
    out = FILES / name
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:], resolution=72)
    return out


def ctx(**params) -> Ctx:
    c = Ctx(Options())
    c.params = params
    return c


def look(pdf: Path, password: str = "") -> list[tuple[int, str]]:
    """Each page of a PDF: (its colour's number, where its white mark is: "tl" "tr" "bl" "br")."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf), password=password or None)
    out = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            im = page.render(scale=0.25).to_pil().convert("RGB")
            page.close()
            w, h = im.size
            mid = im.getpixel((w // 3, h * 2 // 3))
            best = min(range(len(COLORS)), key=lambda k: sum(abs(a - b) for a, b in zip(COLORS[k], mid)))
            corners = {"tl": (2, 2), "tr": (w - 3, 2), "bl": (2, h - 3), "br": (w - 3, h - 3)}
            mark = next((k for k, xy in corners.items() if min(im.getpixel(xy)) > 235), "?")
            out.append((best, mark))
    finally:
        doc.close()
    return out


def colors_of(pdf: Path, password: str = "") -> list[int]:
    return [c for c, _ in look(pdf, password)]


A = B = C = LOCKED = BIG = None


# ----------------------------------------------------------------------------- the tools on their own

def files():
    global A, B, C, LOCKED, BIG
    A = make_pdf("alpha.pdf", [0, 1, 2, 3, 4])
    B = make_pdf("beta.pdf", [5, 6, 7])
    C = make_pdf("gamma wide.pdf", [8, 9], size=(800, 600))
    r = pt.lock(make_pdf("secret src.pdf", [10, 11]), ctx(new_password="pässwörd 1"))
    LOCKED = r.outputs[0]
    BIG = FILES / "photos.jpg"
    Image.effect_noise((3200, 2400), 90).convert("RGB").save(BIG, quality=95)
    check(colors_of(A) == [0, 1, 2, 3, 4], "the made-up PDFs can be read back by their colours")


def ranges():
    check(pt.parse_ranges("1-3, 5", 5) == [[0, 1, 2], [4]], "pages typed: 1-3, 5")
    check(pt.parse_ranges("4-", 6) == [[3, 4, 5]], "pages typed: 4- (to the end)")
    check(pt.parse_ranges("3-1", 5) == [[2, 1, 0]], "pages typed backwards: 3-1")
    check(pt.parse_ranges("٢ - ٣،٥", 5) == [[1, 2], [4]], "Arabic digits and comma work")
    for bad in ("", "abc", "0", "6", "2-9", "--"):
        try:
            pt.parse_ranges(bad, 5)
            check(False, f"{bad!r} is refused")
        except ValueError as e:
            check(bool(str(e)), f"{bad!r} is refused with a reason ({e})")


def tools():
    r = pt.save_arranged([A, B], ctx(pages=[(1, 2, 0), (0, 0, 90), (0, 4, 180), (1, 0, 270)], how="one",
                                     name="mixed"))
    got = look(r.outputs[0])
    check([c for c, _ in got] == [7, 0, 4, 5], f"pages from two PDFs, in a new order ({got})")
    check([m for _, m in got] == ["tl", "tr", "br", "bl"], "and each turned as asked")
    r = pt.save_arranged([A, B], ctx(pages=[(0, 1, 0), (1, 1, 0)], how="each", name="each"))
    made = sorted(r.outputs[0].glob("*.pdf"))
    check([colors_of(p) for p in made] == [[1], [6]], "each page as its own PDF")
    r = pt.save_arranged([A], ctx(pages=[(0, 2, 90)], how="images", name="pic", format="jpg", dpi=100))
    out = r.outputs[0]
    with Image.open(out) as im:
        check(out.suffix == ".jpg" and im.width > im.height, f"one page as a JPG, turned ({out.name})")
    for mode, extra, want in (("each", {}, [[0], [1], [2], [3], [4]]), ("every", {"every": 2}, [[0, 1], [2, 3], [4]]),
                              ("ranges", {"ranges": "1-2, 5, 3-4"}, [[0, 1], [4], [2, 3]])):
        r = pt.split(A, ctx(mode=mode, **extra))
        made = sorted(r.outputs[0].glob("*.pdf"), key=lambda p: p.stat().st_mtime_ns)
        check(sorted(colors_of(p) for p in made) == sorted(want), f"split, {mode}: {[colors_of(p) for p in made]}")
    r = pt.split(make_pdf("one.pdf", [3]), ctx(mode="each"))
    check(not r.outputs and r.note, "splitting a one-page PDF says there's nothing to split")
    r = pt.extract(A, ctx(ranges="5, 1-2"))
    check(colors_of(r.outputs[0]) == [4, 0, 1], "take out pages 5, 1-2 (in that order)")
    r = pt.remove_pages(A, ctx(ranges="2-4"))
    check(colors_of(r.outputs[0]) == [0, 4], "delete pages 2-4")
    try:
        pt.remove_pages(A, ctx(ranges="1-5"))
        check(False, "deleting every page is refused")
    except RuntimeError:
        check(True, "deleting every page is refused")
    r = pt.pictures_to_pdf([BIG, BIG], ctx(size="picture"))
    pics = r.outputs[0]
    sizes = {lvl: None for lvl in pt.COMPRESS_LEVELS}
    for lvl in sizes:
        r = pt.compress(pics, ctx(level=lvl))
        sizes[lvl] = r.outputs[0].stat().st_size if r.outputs else None
    check(all(sizes.values()) and sizes["light"] > sizes["normal"] > sizes["strong"],
          f"compress: three levels, each smaller ({pics.stat().st_size} → {sizes})")
    r = pt.compress(A, ctx(level="normal"))
    check(not r.outputs and r.note, "a PDF that can't get smaller says so, and leaves no file")
    for size, want in (("a4", (842, 595)), ("letter", (792, 612))):
        r = pt.pictures_to_pdf([BIG], ctx(size=size, margin="big", name=f"on {size}"))
        page = PdfReader(r.outputs[0]).pages[0]
        got = (round(float(page.mediabox.width)), round(float(page.mediabox.height)))
        check(got == want, f"a wide picture on {size} gets a page on its side {got}")
    r = pt.to_pictures(A, ctx(ranges="2-3", format="png", dpi=72))
    check(sorted(p.suffix for p in r.outputs[0].iterdir()) == [".png", ".png"], "PDF to pictures: pages 2-3 as PNG")
    r = pt.lock(A, ctx(new_password="x y z", print=False, copy=False))
    rd = PdfReader(r.outputs[0])
    check(rd.is_encrypted and not rd.decrypt("") and rd.decrypt("x y z"), "lock: needs the password")
    check(not rd.user_access_permissions & 4 if hasattr(rd, "user_access_permissions") else True,
          "lock: printing not allowed when unticked")
    try:
        pt.unlock(LOCKED, ctx(password="wrong"))
        check(False, "unlock with a wrong password is refused")
    except NeedPassword:
        check(True, "unlock with a wrong password asks again")
    r = pt.unlock(LOCKED, ctx(password="pässwörd 1"))
    check(not PdfReader(r.outputs[0]).is_encrypted and colors_of(r.outputs[0]) == [10, 11], "unlock with the password")
    r = pt.compress(LOCKED, ctx(level="normal", password="pässwörd 1"))
    check(bool(r.note), "a locked PDF can be compressed once its password is given")


def big():
    many = make_pdf("many pages.pdf", [k % len(COLORS) for k in range(300)], size=(300, 400))
    t0 = time.time()
    r = pt.save_arranged([many, A], ctx(pages=[(0, i, 0) for i in range(300)] + [(1, 0, 0)], how="one", name="big"))
    took = time.time() - t0
    check(len(PdfReader(r.outputs[0]).pages) == 301 and took < 20, f"300 + 1 pages merged in {took:.1f} s")


# ----------------------------------------------------------------------------- the page, clicked like a person

def page():
    s = st.Settings()
    s.first_run = False
    s.tour_done = True
    s.check_updates = False
    s.language = LANG
    s.appearance = LOOK
    s.known_tools = sorted(t.id for t in all_tools.ready())
    s.tools_off = ["pdf"]
    s.reveal_after = False
    s.save()
    from PySide6.QtCore import QMimeData, Qt, QTimer, QUrl
    from PySide6.QtGui import QDropEvent, QIcon
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox
    from corefold import i18n
    i18n.set_language(LANG)
    qapp = QApplication.instance() or QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    if i18n.is_rtl():
        qapp.setLayoutDirection(Qt.RightToLeft)
    from corefold import app as A_
    A_.setup_logging()

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

    # file pickers answer with what's set here; other windows are answered by the test
    picked: dict = {"files": []}
    QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([str(p) for p in picked["files"]], ""))
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(picked["files"][0]) if picked["files"] else "", ""))
    answers: dict = {"dialog": None}
    real_exec = QDialog.exec

    def dialog_exec(dlg):
        fill = answers["dialog"]
        if fill is None:
            return real_exec(dlg)
        answers["dialog"] = None
        answers["seen"] = type(dlg).__name__
        dlg.show()
        settle(0.2)
        dlg.grab().save(str(OUT / f"dialog-{type(dlg).__name__}-{LANG}-{LOOK}.png"))
        ok = fill(dlg)
        dlg.hide()
        return 1 if ok else 0
    QDialog.exec = dialog_exec

    c = A_.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
    c.ask_password = lambda files, wrong=False: "pässwörd 1"
    c.show_window("Home")
    w = c.window
    w.resize(1100, 740)
    settle(0.6)
    check(not w.shows("PDF"), "switched off: not shown")
    check("corefold.pdfpage" not in sys.modules, "switched off: its page isn't loaded")
    c.set_tool("pdf", True)
    settle(0.6)
    w = c.window
    check(w.shows("PDF"), "switched on: shown in its section")
    w.resize(1100, 740)
    w.open_page("PDF")
    settle(0.6)
    pg = w.pdf_page
    check(pg is not None and w.current_page() == "PDF", "the page opens")
    if pg is None:
        return
    w.grab().save(str(OUT / f"pdf-start-{LANG}-{LOOK}.png"))
    check(len(pg.cards) >= 9 and all(card.isVisible() for card in pg.cards.values()), f"{len(pg.cards)} cards")

    def outputs_since(t0: float) -> list[Path]:
        return sorted((p for p in FILES.rglob("*") if p.is_file() and p.stat().st_mtime > t0),
                      key=lambda p: p.stat().st_mtime)

    def card(key: str, files: list[Path], fill=None):
        picked["files"] = files
        answers["dialog"] = fill
        t0 = time.time() - 0.01
        QTest.mouseClick(pg.cards[key], Qt.LeftButton)
        settle(0.3)
        until(lambda: not c.jobs, 60)
        settle(0.3)
        return outputs_since(t0)

    # a card that asks a question: Split, every 2 pages
    def split_every(dlg):
        dlg.group.buttons()[1].setChecked(True)
        dlg.every.setValue(2)
        return True
    made = card("split", [A], split_every)
    check(sorted(colors_of(p) for p in made) == [[0, 1], [2, 3], [4]], f"Split card: every 2 pages ({len(made)} files)")
    made = card("compress", [LOCKED], lambda dlg: True)
    check(answers.get("seen") == "CompressDialog", "Compress card: asks how much")
    def lock_abc(dlg):
        dlg.p1.setText("abc")
        dlg.p2.setText("abc")
        dlg.allow_copy.setChecked(False)
        return dlg.ok.isEnabled()
    made = card("lock", [B], lock_abc)
    check(len(made) == 1 and PdfReader(made[0]).is_encrypted, "Lock card: a locked copy")
    locked_b = made[0] if made else None
    if locked_b:
        c.ask_password = lambda files, wrong=False: "abc"
        made = card("unlock", [locked_b])
        check(len(made) == 1 and not PdfReader(made[0]).is_encrypted, "Unlock card: asks the password, unlocks")
        c.ask_password = lambda files, wrong=False: "pässwörd 1"
    made = card("unlock", [A])
    check(not made, "Unlock card on a PDF without a password: nothing made, says so")
    made = card("pictures", [BIG], lambda dlg: (dlg.size.setCurrentIndex(dlg.size.findData("a4")), True)[-1])
    check(len(made) == 1 and made[0].suffix == ".pdf", "Pictures to PDF card")
    made = card("topictures", [B], lambda dlg: (dlg.ranges.setText("2"), True)[-1])
    check(len(made) == 1 and made[0].suffix == ".png", "PDF to pictures card: page 2")
    made = card("split", [A], lambda dlg: False)
    check(not made, "Cancel makes nothing")

    # the board: two PDFs and a locked one, picked with the Merge card
    picked["files"] = [A, B, LOCKED]
    QTest.mouseClick(pg.cards["merge"], Qt.LeftButton)
    check(until(lambda: not pg.reading and not pg.pending and len(pg.pixmaps) >= 10, 30),
          f"merge: all 10 pages and their pictures come up ({len(pg.pixmaps)})")
    check(pg.board.count() == 10 and len(pg.files) == 3, "including the locked one (its password was asked)")
    check(not (pg.banner or w.one_head).isVisible(), "the board gets the banner's room")
    settle(0.4)
    w.grab().save(str(OUT / f"pdf-board-{LANG}-{LOOK}.png"))
    board = pg.board

    def click_page(row: int, mods=Qt.NoModifier):
        rect = board.visualItemRect(board.item(row))
        board.scrollToItem(board.item(row))
        settle(0.05)
        rect = board.visualItemRect(board.item(row))
        QTest.mouseClick(board.viewport(), Qt.LeftButton, mods, rect.center())
        settle(0.05)
    click_page(1)
    click_page(2, Qt.ControlModifier)
    check(len(board.selectedItems()) == 2, "clicking two pages selects them")
    QTest.mouseClick(pg.tool_btns["right"], Qt.LeftButton)
    settle(0.2)
    check([t for _, _, t in pg.plan()[:3]] == [0, 90, 90], "Turn right turns just those")
    QTest.mouseClick(pg.tool_btns["delete"], Qt.LeftButton)
    settle(0.2)
    check(board.count() == 8 and pg.plan()[1] == (0, 3, 0), "Delete removes them")
    QTest.mouseClick(pg.tool_btns["undo"], Qt.LeftButton)
    settle(0.2)
    check(board.count() == 10 and pg.plan()[1] == (0, 1, 90), "Undo brings them back, still turned")
    QTest.keyClick(board, Qt.Key_Z, Qt.ControlModifier)
    settle(0.2)
    check(pg.plan()[1] == (0, 1, 0), "Ctrl + Z undoes the turn")
    pg.pick.clear()
    QTest.keyClicks(pg.pick, "9-10, 1")
    QTest.keyClick(pg.pick, Qt.Key_Return)
    settle(0.2)
    check(sorted(board.row(i) for i in board.selectedItems()) == [0, 8, 9], "typing 9-10, 1 selects those pages")
    QTest.keyClicks(pg.pick, "x")
    QTest.keyClick(pg.pick, Qt.Key_Return)
    settle(0.1)
    check(bool(pg.info.text()), "a page list that makes no sense: the page says why")
    # move the last page (from the locked PDF) to the front, the way the list moves it when dragged
    last = board.takeItem(9)
    board.insertItem(0, last)
    pg._changed()
    settle(0.1)
    check(pg.plan()[0] == (2, 1, 0), "a page moved to the front")
    # a page shown big, then closed
    QTimer.singleShot(600, lambda: getattr(pg, "preview_dialog", None) and (
        pg.preview_dialog.grab().save(str(OUT / f"pdf-preview-{LANG}-{LOOK}.png")),
        QTest.keyClick(pg.preview_dialog, Qt.Key_Right), pg.preview_dialog.accept()))
    board.clearSelection()
    click_page(0)
    QTest.mouseClick(pg.tool_btns["view"], Qt.LeftButton)
    settle(0.3)
    check(getattr(pg, "preview_dialog", 1) is None, "See it big opens the page and closes again")
    t0 = time.time() - 0.01
    QTest.mouseClick(pg.save_btn, Qt.LeftButton)
    check(until(lambda: not c.jobs, 60), "Merge and save: done")
    settle(0.3)
    made = [p for p in outputs_since(t0) if p.suffix == ".pdf"]
    want = [11, 0, 1, 2, 3, 4, 5, 6, 7, 10]
    check(len(made) == 1 and colors_of(made[0]) == want, f"the saved PDF has the pages in the board's order "
                                                         f"({colors_of(made[0]) if made else None})")
    check(len(made) == 1 and not PdfReader(made[0]).is_encrypted, "and it opens without a password")
    # selected pages, each as its own PDF
    board.clearSelection()
    click_page(1)
    click_page(3, Qt.ControlModifier)
    t0 = time.time() - 0.01
    pg.save("each", only_selected=True)
    until(lambda: not c.jobs, 60)
    settle(0.3)
    made = [p for p in outputs_since(t0) if p.suffix == ".pdf"]
    check(sorted(colors_of(p) for p in made) == [[0], [2]], f"Save selected pages, each as its own PDF ({len(made)})")
    # pictures dropped on the page are made into a PDF; PDFs dropped on the board are added
    count = board.count()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(C))])
    drop = QDropEvent(board.viewport().rect().center(), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    board.dropEvent(drop)
    check(until(lambda: board.count() == count + 2, 15), "a PDF dropped on the board adds its pages")
    # closing with changes not saved asks first
    asked = {}
    real_box = QMessageBox.exec

    def box_exec(box):
        asked["text"] = box.text()
        box.grab().save(str(OUT / f"pdf-close-ask-{LANG}-{LOOK}.png"))
        box.setProperty("x", 1)
        for b in box.buttons():
            if box.buttonRole(b) == QMessageBox.RejectRole:
                b.click()
                return 0
        return 0
    QMessageBox.exec = box_exec
    try:
        QTest.mouseClick(pg.close_btn, Qt.LeftButton)
        settle(0.2)
        check(bool(asked) and pg.stack.currentIndex() == 1, f"closing with changes asks first (Cancel: stays) "
                                                            f"({bool(asked)}, {pg.stack.currentIndex()})")
    finally:
        QMessageBox.exec = real_box
    for size in ((760, 520), (1500, 900)):
        w.resize(*size)
        settle(0.4)
        right = pg.save_btn.mapTo(w, pg.save_btn.rect().bottomRight())
        left = pg.save_btn.mapTo(w, pg.save_btn.rect().topLeft())
        check(0 <= left.x() and right.x() <= w.width() and right.y() <= w.height(),
              f"at {size[0]}×{size[1]} the save button is inside the window ({left.x()},{right.x()},{right.y()} in "
              f"{w.width()}×{w.height()})")
        w.grab().save(str(OUT / f"pdf-board-{size[0]}-{LANG}-{LOOK}.png"))
    w.resize(1100, 740)
    pg.close_board(ask=False)
    settle(0.2)
    check(pg.stack.currentIndex() == 0 and (pg.banner or w.one_head).isVisible() and not pg.files,
          "closing goes back to the cards")
    # pictures dropped anywhere on the page: asks, then makes a PDF
    answers["dialog"] = lambda dlg: True
    t0 = time.time() - 0.01
    pg.add_files([BIG])
    until(lambda: not c.jobs, 60)
    settle(0.3)
    check(any(p.suffix == ".pdf" for p in outputs_since(t0)), "pictures dropped on the page become a PDF")
    # Esc / back from the board
    pg.add_files([A])
    until(lambda: not pg.reading, 15)
    settle(0.2)
    check(w.go_back() and pg.stack.currentIndex() == 0, "Back from an untouched board goes straight to the cards")
    for small in ((760, 520),):
        w.resize(*small)
        settle(0.4)
        w.grab().save(str(OUT / f"pdf-start-{small[0]}-{LANG}-{LOOK}.png"))
    c.set_tool("pdf", False)
    settle(0.5)
    check(not c.window.shows("PDF"), "switched off again: not shown")
    check(not errors, f"no errors behind any click ({len(errors)})")
    QDialog.exec = real_exec


for name, fn in (("files", files), ("ranges", ranges), ("tools", tools), ("big", big), ("page", page)):
    step(name, fn)
(OUT / f"pdftest-{LANG}.txt").write_text("\n".join(problems) or "none", "utf-8")
print(f"pdftest: {len(problems)} problem(s)", flush=True)
faulthandler.disable()
from testexit import finish  # (Qt can crash in Windows' tidy-up at exit, after every check passed)
finish(1 if problems else 0)
