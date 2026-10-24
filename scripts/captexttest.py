"""Capture's "Select text", tried like a person would: a made-up screenshot with English, Arabic and mixed
lines is read with Windows' own OCR, then words are picked with the mouse (dragging over a few words,
across lines, right to left in Arabic, double- and triple-clicks, Ctrl+A, Esc) and copied.

Run: python scripts/captexttest.py <folder> [en|ar|fr|es|tr] [dark|light]
Prints one line per check and "captexttest: N problem(s)"; pictures of the editor go in the folder.
Off Windows (no OCR there) the words' places come from where the test drew them."""
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "captexttest").resolve()
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
LOOK = sys.argv[3] if len(sys.argv) > 3 else "dark"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-captexttest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen" if os.name != "nt" else "windows")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
if os.name != "nt":  # (a quick try on Linux: Windows' parts are pretend ones)
    import ctypes
    from unittest import mock
    ctypes.windll = mock.MagicMock()
    for name in ("winreg", "winsound"):
        sys.modules[name] = mock.MagicMock()
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(600, exit=True)

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QIcon, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from corefold import i18n  # noqa: E402

i18n.set_language(LANG)
qapp = QApplication.instance() or QApplication(sys.argv)
if i18n.is_rtl():
    qapp.setLayoutDirection(Qt.RightToLeft)
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


def until(cond, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


class FakeController:
    """Just enough of the app for the editor to open."""

    def __init__(self):
        from corefold.settings import Settings
        from corefold.theme import build_theme
        self.settings = Settings()
        self.theme = build_theme(LOOK, "teal")
        self.icon = QIcon()
        self.toasts = []
        self.toast = self
        self.capture = None

    def notify(self, title, detail, *a, **k):
        self.toasts.append((title, detail))
        return True

    def tell(self, title, detail, *a, **k):
        self.toasts.append((title, detail))


# ----------------------------------------------------------------------------- the made-up screenshot

LINES = [
    ("ltr", "Meeting notes for Monday"),
    ("ltr", "Send the invoice to Sara before noon"),
    ("rtl", "اجتماع الفريق يوم الأحد"),
    ("rtl", "أرسل الملف إلى Corefold قبل الظهر"),
    ("ltr", "Order number 48213 ships today"),
]


def make_picture():
    """White page, dark text (like a screenshot of a document). Returns the picture and, for each line,
    the words in reading order with where each one was drawn."""
    ratio = 1
    img = QImage(900 * ratio, 420 * ratio, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor("#ffffff"))
    p = QPainter(img)
    p.setRenderHint(QPainter.TextAntialiasing)
    f = QFont("Segoe UI" if os.name == "nt" else "Noto Sans")
    f.setPixelSize(30)
    p.setFont(f)
    p.setPen(QColor("#1d1f21"))
    fm = QFontMetricsF(f)
    space = fm.horizontalAdvance(" ")
    boxes = []
    y = 40.0
    for direction, text in LINES:
        words = text.split(" ")
        widths = [fm.horizontalAdvance(w) for w in words]
        x = 40.0 if direction == "ltr" else 900 - 40.0
        row = []
        # the words are drawn one by one where a person would expect them; an English word inside an
        # Arabic line keeps its own place (it's one word, so its own direction doesn't matter here)
        for w, width in zip(words, widths):
            left = x if direction == "ltr" else x - width
            p.drawText(QPointF(left, y + fm.ascent()), w)
            row.append((w, left, y + fm.ascent() - fm.capHeight() - 2, width, fm.capHeight() + fm.descent() + 4))
            x = left + width + space if direction == "ltr" else left - space
        boxes.append(row)
        y += 70
    p.end()
    return img, boxes


def fake_lines(boxes):
    from corefold.extras import OcrLine, OcrWord
    import random
    out = []
    for row in boxes:
        words = [OcrWord(*w) for w in row]
        random.shuffle(words)  # (the order Windows gives isn't trusted: the places decide)
        out.append(OcrLine(words))
    return out


# ----------------------------------------------------------------------------- checks

def reading_order():
    from corefold.extras import OcrLine, OcrWord, lines_text
    ar = OcrLine([OcrWord("الثاني", 100, 0, 60, 20), OcrWord("الأول", 200, 0, 60, 20)])
    check(ar.rtl and ar.text == "الأول الثاني", "an Arabic line is read right to left")
    mixed = OcrLine([OcrWord("Hello", 10, 0, 50, 20), OcrWord("World", 70, 0, 50, 20),
                     OcrWord("قال", 200, 0, 40, 20), OcrWord("ثم", 260, 0, 30, 20), OcrWord("لي", 300, 0, 30, 20)])
    check(mixed.text == "لي ثم قال Hello World", f"English words inside Arabic keep their own order ({mixed.text})")
    nums = OcrLine([OcrWord("456", 10, 0, 40, 20), OcrWord("123", 60, 0, 40, 20), OcrWord("رقم", 120, 0, 40, 20)])
    check(nums.text == "رقم 123 456", f"numbers in an Arabic line ({nums.text})")
    en = OcrLine([OcrWord("two", 80, 0, 40, 20), OcrWord("one", 10, 0, 40, 20)])
    check(not en.rtl and en.text == "one two", "an English line is read left to right")
    cjk = OcrLine([OcrWord("日本", 10, 0, 40, 20), OcrWord("語", 60, 0, 20, 20)])
    check(cjk.text == "日本語", "Japanese words are joined without spaces")
    check(lines_text([en, ar]) == "one two\nالأول الثاني", "lines are joined with line breaks")
    from corefold.extras import merge_reads
    en_read = [OcrLine([OcrWord("Hello", 10, 0, 50, 20)]), OcrLine([OcrWord("lgjl", 10, 40, 80, 20)])]
    ar_read = [OcrLine([OcrWord("Hel1o", 10, 0, 50, 20)]), OcrLine([OcrWord("مرحبا", 10, 40, 80, 20)]),
               OcrLine([OcrWord("وداعا", 10, 80, 80, 20)])]
    merged = merge_reads([(False, en_read), (True, ar_read)])
    check([ln.text for ln in merged] == ["Hello", "مرحبا", "وداعا"],
          f"with two readers, each line comes from the one that reads its letters ({[ln.text for ln in merged]})")
    from corefold.extras import ocr_pieces
    f, strips = ocr_pieces(400, 300, 2600)
    check(f > 1 and strips == [(0, round(300 * f))], f"a small picture is read bigger ({f:.2f}x)")
    f, strips = ocr_pieces(1920, 6000, 2600)
    check(len(strips) >= 3 and strips[-1][1] == round(6000 * f) and all(b - a <= 2600 for a, b in strips)
          and all(strips[i + 1][0] < strips[i][1] for i in range(len(strips) - 1)),
          f"a very tall picture is read in overlapping strips ({len(strips)})")


def real_ocr(img, boxes):
    """Windows reads the made-up screenshot: every English word must be found."""
    from corefold.extras import ocr_available, read_lines
    if not ocr_available():
        print("note  no Windows OCR here: the words' places come from the drawing", flush=True)
        return None
    import io

    from PIL import Image
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    t0 = time.time()
    lines = read_lines(Image.open(io.BytesIO(bytes(data))))
    took = time.time() - t0
    got = [ln.text for ln in lines]
    print("note  Windows read: " + " | ".join(got), flush=True)
    check(took < 5, f"reading the words took {took:.2f} s")
    english = [t for d, t in LINES if d == "ltr"]
    check(all(any(t.lower() in g.lower() for g in got) for t in english), "every English line is read whole")
    arabic = [t for d, t in LINES if d == "rtl"]
    if any(ln.rtl for ln in lines):
        check(any(arabic[0] in g for g in got), f"the Arabic line is read right to left ({got})")
    else:
        print("note  this PC has no Arabic text reading", flush=True)
    # each word's box is where it was drawn
    drawn = {w: QRectF(x, y, wd, h) for row in boxes for w, x, y, wd, h in row}
    off = 0
    for ln in lines:
        for w in ln.words:
            r = drawn.get(w.text)
            if r is not None and not r.adjusted(-8, -8, 8, 8).contains(QPointF(w.x + w.w / 2, w.y + w.h / 2)):
                off += 1
    check(off == 0, f"every word read is where it is in the picture ({off} off)")
    return lines


def editor(img, boxes, lines):
    from corefold.capview import CaptureEditor, TextLayer
    from corefold.theme import stylesheet
    c = FakeController()
    qapp.setStyleSheet(stylesheet(c.theme))
    ed = CaptureEditor(c, img)
    ed.resize(1040, 640)
    ed.show()
    settle(0.4)
    cv = ed.canvas
    check(ed.tool_buttons["select"].isVisible(), "the Select text button is in the bar")
    real = lines is not None
    if not real:
        lines = fake_lines(boxes)
        ed.read_words = lambda: cv.set_text(TextLayer(fake_lines(boxes)))  # (no OCR here)
    ed.tool_buttons["select"].click()
    check(cv.selecting(), "Select text is on")
    check(until(lambda: cv.text is not None, 20), "the words are read when Select text is turned on")
    check(bool(cv.text) and len(cv.text.lines) >= 4, f"lines found: {len(cv.text.lines) if cv.text else 0}")
    settle(0.2)
    ed.grab().save(str(OUT / f"{LANG}-{LOOK}-1-select-on.png"))

    def where(word: str, nth: int = 0) -> QPoint:
        """A word's middle on the screen (in the editor's canvas)."""
        found = [w for ln in cv.text.lines for w in ln.words if w.text == word]
        w = found[nth]
        r = cv.to_widget(QRectF(w.x, w.y, w.w, w.h))
        return r.center().toPoint()

    def drag(a: QPoint, b: QPoint):
        QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, a)
        for k in range(1, 9):
            QTest.mouseMove(cv, a + (b - a) * (k / 8))
            qapp.processEvents()
        QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, b)
        settle(0.15)

    # a few words in the middle of an English line
    drag(where("the"), where("to"))
    check(cv.picked_text() == "the invoice to", f"dragging over three words picks just them ({cv.picked_text()!r})")
    check(ed.pill.isVisible(), "a small Copy button shows next to them")
    ed.grab().save(str(OUT / f"{LANG}-{LOOK}-2-three-words.png"))
    QTest.mouseClick(ed.pill.findChildren(type(ed.b_text))[0], Qt.LeftButton)
    settle(0.1)
    check(QGuiApplication.clipboard().text() == "the invoice to", "Copy puts only those words on the clipboard")
    # dragging backwards picks the same
    drag(where("to"), where("the"))
    check(cv.picked_text() == "the invoice to", "dragging right to left over them picks the same")
    # across lines
    drag(where("for"), where("invoice"))
    check(cv.picked_text() == "for Monday\nSend the invoice", f"dragging across two lines ({cv.picked_text()!r})")
    # Arabic: from the first word (on the right) to the third
    ar = LINES[2][1].split(" ")
    if not any(ln.rtl for ln in cv.text.lines):
        print("note  no Arabic words were read on this PC: the Arabic part is left out", flush=True)
        ar = None
    if ar:
        arabic_picks(cv, ed, ar, drag, where)
    english_more(cv, ed, drag, where, real)


def arabic_picks(cv, ed, ar, drag, where):
    drag(where(ar[0]), where(ar[2]))
    check(cv.picked_text() == " ".join(ar[:3]), f"Arabic words, dragged right to left ({cv.picked_text()!r})")
    ed.grab().save(str(OUT / f"{LANG}-{LOOK}-3-arabic-words.png"))
    # the English word inside the Arabic line
    drag(where("Corefold"), where("قبل"))
    check(cv.picked_text() == "Corefold قبل", f"an English word inside Arabic ({cv.picked_text()!r})")


def english_more(cv, ed, drag, where, real):
    # double-click: one word; triple-click: the line
    QTest.mouseDClick(cv, Qt.LeftButton, Qt.NoModifier, where("48213"))
    settle(0.05)
    check(cv.picked_text() == "48213", f"double-click picks one word ({cv.picked_text()!r})")
    p = where("ships")
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, p)
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, p)
    QTest.mouseDClick(cv, Qt.LeftButton, Qt.NoModifier, p)
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, p)
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, p)
    settle(0.05)
    check(cv.picked_text() == LINES[4][1], f"triple-click picks the whole line ({cv.picked_text()!r})")
    # Ctrl+C copies the picked words (not the picture)
    QTest.keyClick(ed, Qt.Key_C, Qt.ControlModifier)
    settle(0.05)
    check(QGuiApplication.clipboard().text() == LINES[4][1], "Ctrl+C copies the picked words")
    # Ctrl+A and Esc
    QTest.keyClick(ed, Qt.Key_A, Qt.ControlModifier)
    settle(0.1)
    check(cv.picked_text().count("\n") == len(LINES) - 1, "Ctrl+A picks all the words")
    ed.grab().save(str(OUT / f"{LANG}-{LOOK}-4-all.png"))
    QTest.keyClick(ed, Qt.Key_Escape)
    settle(0.05)
    check(cv.picked_text() == "" and ed.isVisible() and not ed.pill.isVisible(), "Esc lets go of the words "
          "(and keeps the editor open)")
    # a box (Alt+drag, or a drag from a spot with no words) picks just the words inside it, line by line
    def box_of(word):
        w = next(w for ln in cv.text.lines for w in ln.words if w.text == word)
        return cv.to_widget(QRectF(w.x, w.y, w.w, w.h))
    a, b = box_of("notes"), box_of("invoice")
    tl, br = QPoint(round(a.left()) - 2, round(a.top()) - 2), QPoint(round(b.right()) + 2, round(b.bottom()) + 2)
    QTest.mousePress(cv, Qt.LeftButton, Qt.AltModifier, tl)
    for k in range(1, 9):
        QTest.mouseMove(cv, tl + (br - tl) * (k / 8))
        qapp.processEvents()
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.AltModifier, br)
    settle(0.1)
    box = QRectF(QPointF(tl), QPointF(br))
    want = "\n".join(" ".join(w.text for w in ln.words
                              if box.contains(cv.to_widget(QRectF(w.x, w.y, w.w, w.h)).center()))
                     for ln in cv.text.lines[:2])
    check(cv.picked_text() == want and not cv.picked_text().startswith("Meeting") and "Send" not in cv.picked_text(),
          f"a box picks only the words inside it, not whole lines ({cv.picked_text()!r})")
    ed.grab().save(str(OUT / f"{LANG}-{LOOK}-5-box.png"))
    QTest.mouseClick(cv, Qt.LeftButton, Qt.ControlModifier, where("Sara"))
    settle(0.05)
    check(cv.picked_text().endswith("Sara"), f"Ctrl+click adds a word ({cv.picked_text()!r})")
    QTest.mouseClick(cv, Qt.LeftButton, Qt.ControlModifier, where("Sara"))
    settle(0.05)
    check(cv.picked_text() == want, f"Ctrl+click on it again takes it away ({cv.picked_text()!r})")
    QTest.mouseClick(cv, Qt.LeftButton, Qt.ControlModifier, where("notes"))
    settle(0.05)
    check("notes" not in cv.picked_text() and cv.picked_text() != "", f"…any picked word ({cv.picked_text()!r})")
    QTest.keyClick(ed, Qt.Key_Escape)
    settle(0.05)
    # a click away from the words picks nothing
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, cv.to_widget(QRectF(450, 400, 1, 1)).center().toPoint())
    check(cv.picked_text() == "", "a click away from the words picks nothing")
    # Copy text with nothing picked copies all of it
    ed.b_text.click()
    settle(0.1)
    clip = QGuiApplication.clipboard().text()
    check(clip.count("\n") == len(LINES) - 1 and "Monday" in clip, "Copy text with nothing picked copies all the text")
    # drawing tools still draw
    ed.tool_buttons["arrow"].click()
    a, b = QPoint(60, 300), QPoint(300, 340)
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(cv, b)
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, b)
    settle(0.1)
    check(len(cv.shapes) == 1 and cv.text is None, "an arrow is drawn (and the words are read again next time)")
    # a crop: the words are read again, in the cropped picture
    cv.crop(QRect(0, 0, 600, 200))
    ed.tool_buttons["select"].click()
    check(until(lambda: cv.text is not None, 20), "after a crop the words are read again")
    if real:
        check(cv.text and all("Order" not in ln.text for ln in cv.text.lines), "…only what's left in the picture")
    QTest.mouseDClick(cv, Qt.LeftButton, Qt.NoModifier, where("Meeting"))
    check(cv.picked_text() == "Meeting", "…and they can be picked")
    ed.close()
    settle(0.2)


def speed(boxes):
    """Hundreds of words: picking stays quick."""
    from corefold.extras import OcrLine, OcrWord
    from corefold.capview import TextLayer
    lines = [OcrLine([OcrWord(f"w{r}x{k}", 10 + k * 60, 10 + r * 24, 50, 18) for k in range(15)]) for r in range(60)]
    t0 = time.time()
    layer = TextLayer(lines)
    layer.holes()
    for k in range(200):
        layer.word_at(QPointF(k * 4.5, k * 7.0))
    took = time.time() - t0
    check(took < 1.5, f"900 words: lit up and 200 mouse moves in {took:.2f} s")


def main():
    img, boxes = make_picture()
    img.save(str(OUT / "picture.png"))
    step("reading order", reading_order)
    lines = None
    try:
        lines = real_ocr(img, boxes)
    except Exception:
        check(False, f"Windows OCR: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()
    step("the editor", lambda: editor(img, boxes, lines))
    step("speed", lambda: speed(boxes))
    check(not errors, f"no errors ({len(errors)})")
    print(f"captexttest: {len(problems)} problem(s)", flush=True)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
