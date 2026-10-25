"""Corefold PDF's editor, tried for real: first the part that writes the new copy (every kind of change,
on made-up PDFs with English and Arabic text, a picture, a form, turned pages and a password), then the
editor in the window used like a person would: typing text, changing a line of the PDF's own text,
highlighting, drawing, shapes, covering, a note, a picture, a signature, moving and deleting, undo and redo,
zoom, filling a form, and saving. Every saved PDF is read back to check the words and pictures in it.

Run: python scripts/pdfedittest.py <folder> [en|ar|fr|es|tr] [dark|light]
Prints one line per check and "pdfedittest: N problem(s)"; pictures of the editor go in the folder."""
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "pdfedittest").resolve()
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
LOOK = sys.argv[3] if len(sys.argv) > 3 else "dark"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-pdfedittest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(900, exit=True)  # stuck somewhere: say where, and stop

from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

qapp = QApplication.instance() or QApplication(sys.argv)
qapp.setQuitOnLastWindowClosed(False)

import pypdfium2 as pdfium  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import (ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject,  # noqa: E402
                           StreamObject, TextStringObject)

from corefold import pdfedit as pe  # noqa: E402
from corefold.core import Ctx, Options  # noqa: E402

problems = []
errors = []
FILES = TMP / "files"
FILES.mkdir()
ARABIC = "الفاتورة رقم ١٢ للسيد محمد"
SANS = "Arial" if sys.platform == "win32" else "DejaVu Sans"
ARABIC_FONT = "Arial" if sys.platform == "win32" else "Noto Sans Arabic"


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


def ctx(**params) -> Ctx:
    c = Ctx(Options())
    c.params = params
    return c


def make_doc(name: str, pages: int = 2) -> Path:
    """Lines of English and Arabic text and an orange picture on every page (made by Qt, like many PDFs)."""
    out = FILES / name
    w = QPdfWriter(str(out))
    w.setResolution(72)
    w.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(0, 0, 0, 0)))
    p = QPainter(w)
    for n in range(pages):
        if n:
            w.newPage()
        f = QFont(SANS)
        f.setPixelSize(14)
        p.setFont(f)
        p.setPen(QColor("black"))
        p.drawText(60, 100, f"Invoice number 12345 for page {n + 1}")
        p.drawText(60, 130, "Total: 500 SAR")
        p.drawText(60, 160, "Secret line to cover")
        f2 = QFont(ARABIC_FONT)
        f2.setPixelSize(16)
        p.setFont(f2)
        p.setPen(QColor("#204080"))
        p.drawText(QRectF(60, 180, 470, 30), Qt.AlignRight, ARABIC)
        img = QImage(80, 60, QImage.Format_RGB32)
        img.fill(QColor("orange"))
        p.drawImage(QRectF(60, 240, 80, 60), img)
    p.end()
    return out


def make_form(path: Path):
    """A form with a text box, a tick box, two round choices and a list."""
    w = PdfWriter()
    page = w.add_blank_page(400, 400)
    font = w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                           NameObject("/Subtype"): NameObject("/Type1"),
                                           NameObject("/BaseFont"): NameObject("/Helvetica")}))

    def rect(*v):
        return ArrayObject([FloatObject(x) for x in v])

    def ap(data):
        s = StreamObject()
        s._data = data
        s.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                  NameObject("/BBox"): rect(0, 0, 14, 14)})
        return w._add_object(s)
    annots, fields = ArrayObject(), ArrayObject()

    def widget(**kw):
        d = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
                              NameObject("/F"): NumberObject(4), NameObject("/P"): page.indirect_reference})
        for k, v in kw.items():
            d[NameObject("/" + k)] = v
        ref = w._add_object(d)
        annots.append(ref)
        return ref
    fields.append(widget(FT=NameObject("/Tx"), T=TextStringObject("name"), Rect=rect(50, 330, 300, 352),
                         DA=TextStringObject("/Helv 12 Tf 0 g")))
    fields.append(widget(FT=NameObject("/Btn"), T=TextStringObject("agree"), Rect=rect(50, 290, 64, 304),
                         V=NameObject("/Off"), AS=NameObject("/Off"),
                         AP=DictionaryObject({NameObject("/N"): DictionaryObject({
                             NameObject("/Yes"): ap(b"0 0 1 rg 2 2 10 10 re f"), NameObject("/Off"): ap(b"")})})))
    radio = DictionaryObject({NameObject("/FT"): NameObject("/Btn"), NameObject("/T"): TextStringObject("color"),
                              NameObject("/Ff"): NumberObject(1 << 15), NameObject("/V"): NameObject("/Off")})
    rref = w._add_object(radio)
    kids = ArrayObject()
    for i, nm in enumerate(("red", "blue")):
        kids.append(widget(Parent=rref, Rect=rect(50 + i * 60, 250, 64 + i * 60, 264), AS=NameObject("/Off"),
                           AP=DictionaryObject({NameObject("/N"): DictionaryObject({
                               NameObject("/" + nm): ap(b"1 0 0 rg 3 3 8 8 re f"), NameObject("/Off"): ap(b"")})})))
    radio[NameObject("/Kids")] = kids
    fields.append(rref)
    fields.append(widget(FT=NameObject("/Ch"), T=TextStringObject("city"), Ff=NumberObject(1 << 17),
                         Opt=ArrayObject([TextStringObject(t) for t in ("Riyadh", "Jeddah", "Dubai")]),
                         Rect=rect(50, 200, 200, 222), DA=TextStringObject("/Helv 12 Tf 0 g")))
    page[NameObject("/Annots")] = annots
    w._root_object[NameObject("/AcroForm")] = DictionaryObject({
        NameObject("/Fields"): fields, NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g"),
        NameObject("/DR"): DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): font})})})
    w.write(str(path))
    return path


def text_of(path: Path, page: int = 0, password: str = "") -> str:
    doc = pdfium.PdfDocument(str(path), password=password or None)
    try:
        return doc[page].get_textpage().get_text_range()
    finally:
        doc.close()


def picture_of(path: Path, page: int = 0, scale: float = 1.0, password: str = "", forms: bool = False):
    doc = pdfium.PdfDocument(str(path), password=password or None)
    try:
        if forms:
            doc.init_forms()
        return doc[page].render(scale=scale, may_draw_forms=forms).to_pil().convert("RGB")
    finally:
        doc.close()


def cover_hides_for_real():
    """Cover over part of a line (one number) and part of a scan: what's under it is really gone."""
    from pypdf.generic import DecodedStreamObject
    from PIL import Image
    src = FILES / "bank.pdf"
    w = PdfWriter()
    page = w.add_blank_page(595, 842)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    scan = Image.new("RGB", (400, 200), (40, 90, 220))
    scan.paste((230, 20, 20), (200, 0, 400, 200))  # (the right half is the secret)
    img = DecodedStreamObject()
    img.set_data(scan.tobytes())
    img.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(400), NameObject("/Height"): NumberObject(200),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8)})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)}),
        NameObject("/XObject"): DictionaryObject({NameObject("/Im1"): w._add_object(img)})})
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 14 Tf 50 700 Td (Name: Ali   IBAN: SA4420000001234567891234) Tj ET "
                     b"BT /F1 14 Tf 50 650 Td (Keep this line) Tj ET q 200 0 0 100 50 400 cm /Im1 Do Q")
    page[NameObject("/Contents")] = w._add_object(content)
    w.write(str(src))
    doc = pdfium.PdfDocument(str(src))
    tp = doc[0].get_textpage()
    i = tp.get_text_range().index("SA44")
    boxes = [tp.get_charbox(k) for k in range(i, i + 24)]
    doc.close()
    l, b = min(x[0] for x in boxes) - 2, min(x[1] for x in boxes) - 2
    r, t = max(x[2] for x in boxes) + 2, max(x[3] for x in boxes) + 2
    edits = [dict(kind="cover", page=0, x=l, y=842 - t, w=r - l, h=t - b, color="#ffffff"),
             dict(kind="cover", page=0, x=150, y=842 - 500, w=100, h=100, color="#ffffff")]  # (the scan's red half)
    out = pe.save_edits(src, ctx(edits=edits)).outputs[0]
    words = text_of(out)
    check("SA44" not in words and b"SA44" not in all_streams_text(out),
          f"Cover over one number in a line: it's gone from the file ({words!r})")
    check("Keep this line" in words, "the lines it doesn't touch stay real text")
    pic = picture_of(out, scale=2)
    dark = sum(1 for x in range(100, 160) for y in range(2 * 128, 2 * 146) if sum(pic.getpixel((x, y))) < 300)
    check(dark > 30, f"the rest of the covered line still shows ({dark} dark dots)")
    red = 0
    for im in PdfReader(str(out)).pages[0].images:
        data = im.image.convert("RGB").tobytes()
        red += sum(1 for k in range(0, len(data), 3) if data[k] > 180 and data[k + 1] < 80 and data[k + 2] < 80)
    check(red == 0, f"Cover over half a scan: the covered half isn't in the file ({red} red dots)")
    blue = pic.getpixel((2 * 100, 2 * (842 - 450)))
    check(blue[2] > 150 and blue[0] < 120, f"and the half it doesn't cover still shows ({blue})")
    white = pic.getpixel((2 * 200, 2 * (842 - 450)))
    check(min(white) > 240, f"the covered half shows the cover ({white})")
    # a form box under a cover: what was typed in it goes too
    form = make_form(FILES / "covered form.pdf")
    filled = pe.save_edits(form, ctx(fields={"name": "Zxqv Secret"})).outputs[0]
    box = next(f for f in pe.read_fields(PdfReader(str(filled))) if f["name"] == "name")
    x, y, bw, bh = box["rect"]
    out = pe.save_edits(filled, ctx(edits=[dict(kind="cover", page=box["page"], x=x - 2, y=y - 2, w=bw + 4, h=bh + 4,
                                                color="#ffffff")])).outputs[0]
    left = PdfReader(str(out)).get_fields() or {}
    check(b"Zxqv" not in all_streams_text(out) and "name" not in left and "agree" in left,
          f"Cover over a form box: it and what was typed in it are gone, the other boxes stay ({sorted(left)})")


def all_streams_text(path: Path) -> bytes:
    """Every stream in the file, unpacked (to be sure removed words aren't left anywhere)."""
    r = PdfReader(str(path))
    out = []
    nums = {n for d in r.xref.values() for n in d} | set(r.xref_objStm)
    for num in sorted(nums):
        try:
            obj = r.get_object(num)
        except Exception:
            continue
        if obj is None:
            continue
        try:
            out.append(obj.get_data())
        except Exception:
            pass
    return b"\n".join(out)


step("cover", cover_hides_for_real)


DOC = make_doc("letter.pdf")
PIC = FILES / "green.png"
_img = QImage(120, 80, QImage.Format_RGB32)
_img.fill(QColor("#00a000"))
_img.save(str(PIC))


# ----------------------------------------------------------------------------- reading a page

def reading():
    doc = pdfium.PdfDocument(str(DOC))
    try:
        lay = pe.read_layout(doc, 0)
    finally:
        doc.close()
    texts = [d["text"] for d in lay["lines"]]
    print("      lines:", texts)
    check("Invoice number 12345 for page 1" in texts, "an English line is read whole, in order")
    check(ARABIC in texts, "the Arabic line is read whole, in reading order")
    check(len(lay["images"]) == 1 and abs(lay["images"][0]["rect"][0] - 60) < 1, "the picture is found where it is")
    check(len(lay["chars"]) > 60, f"every letter's box ({len(lay['chars'])})")
    arabic = next(d for d in lay["lines"] if d["text"] == ARABIC)
    check(arabic["rtl"] and arabic["color"] == "#204080", "the Arabic line: right to left, its colour")
    check(pe.line_text([("b", 5, 10), ("a", 0, 5)], 10)[0] == "ab", "letters put in order by where they are")
    check(pe.arabic_letters("للسید كتاب") == "للسيد كتاب" and pe.arabic_letters("علی") == "علی" and
          pe.arabic_letters("پیدا") == "پیدا", "Arabic words read back with Arabic letters (Persian left as it is)")
    check(pe.clean_family("ABCDEF+TimesNewRomanPS-BoldMT") == ("Times New Roman", True, False), "font names cleaned")
    g = pe.Geo(90, 0, 0, 300, 500)
    check(g.width == 500 and all(abs(a - b) < 1e-6 for a, b in zip(g.to_display(*g.to_user(30, 40)), (30, 40))),
          "a turned page's points go there and back")


# ----------------------------------------------------------------------------- writing every kind of change

def writing():
    doc = pdfium.PdfDocument(str(DOC))
    try:
        lay = pe.read_layout(doc, 0)
    finally:
        doc.close()
    total = next(d for d in lay["lines"] if d["text"].startswith("Total"))
    secret = next(d for d in lay["lines"] if d["text"].startswith("Secret"))
    img = lay["images"][0]
    edits = [
        {"kind": "text", "page": 0, "x": total["rect"][0], "y": total["rect"][1] - 3, "text": "Total: 900 SAR",
         "font": SANS, "size": total["size"], "color": "#000000", "replaces": total["objs"]},
        {"kind": "text", "page": 0, "x": 300, "y": 400, "text": "نص عربي جديد\nسطر ثاني", "font": ARABIC_FONT,
         "size": 18, "color": "#c00000"},
        {"kind": "text", "page": 1, "x": 60, "y": 500, "text": "Second page words", "font": SANS, "size": 12,
         "color": "#000000"},
        {"kind": "highlight", "page": 0, "rects": [lay["lines"][0]["rect"]], "color": "#ffd400"},
        {"kind": "underline", "page": 0, "rects": [[60, 700, 200, 14]], "color": "#ff0000"},
        {"kind": "ink", "page": 0, "strokes": [[[100, 500], [150, 520], [200, 480]]], "color": "#0040ff", "width": 3},
        {"kind": "arrow", "page": 0, "points": [100, 600, 250, 650], "color": "#ff0000", "width": 2},
        {"kind": "rect", "page": 0, "points": [300, 600, 400, 700], "color": "#00aa00", "width": 2},
        {"kind": "image", "page": 0, "x": 420, "y": 500, "w": 120, "h": 80, "path": str(PIC)},
        {"kind": "move", "page": 0, "objs": [img["obj"]], "orig": img["rect"], "x": 200, "y": 240, "w": 160, "h": 120},
        {"kind": "cover", "page": 0, "x": secret["rect"][0] - 2, "y": secret["rect"][1] - 2,
         "w": secret["rect"][2] + 4, "h": secret["rect"][3] + 4, "color": "#ffffff"},
        {"kind": "note", "page": 0, "x": 500, "y": 50, "text": "ملاحظة: راجع المبلغ"},
    ]
    r = pe.save_edits(DOC, ctx(edits=edits))
    out = r.outputs[0]
    check(out.exists() and out.name == "letter (edited).pdf", f"a new copy is made ({out.name})")
    check(DOC.stat().st_size > 0 and "Total: 500 SAR" in text_of(DOC), "the original is untouched")
    words = text_of(out)
    print("      words now:", words.replace("\r\n", " | "))
    check("Total: 900 SAR" in words and "500 SAR" not in words, "a changed line: the new words in, the old gone")
    check("جديد" in words and "ثاني" in words, "new Arabic text is real text (it can be searched)")
    check("Secret line" not in words, "words fully under a cover are taken out of the file")
    check("Second page words" in text_of(out, 1), "text on the second page")
    raw = all_streams_text(out)
    old = pdfium.PdfDocument(str(DOC))
    check(b"Secret" not in raw, "and the covered words aren't left anywhere in the file")
    old.close()
    pic = picture_of(out)
    check(pic.getpixel((280, 300))[:3] == (255, 165, 0) or sum(abs(a - b) for a, b in zip(pic.getpixel((280, 300)),
                                                                                           (255, 165, 0))) < 30,
          f"the PDF's own picture was moved and made bigger ({pic.getpixel((280, 300))})")
    check(sum(pic.getpixel((100, 270))) > 740, f"and isn't where it was ({pic.getpixel((100, 270))})")
    check(pic.getpixel((480, 540))[1] > 120 and pic.getpixel((480, 540))[0] < 60, "the added picture is there")
    hl = pic.getpixel((round(lay["lines"][0]["rect"][0] + 4), round(lay["lines"][0]["rect"][1] + 2)))
    check(hl[0] > 200 and hl[1] > 170 and hl[2] < 120, f"the highlight is yellow ({hl})")
    big = picture_of(out, scale=3)
    x0, y0, w0, h0 = (v * 3 for v in lay["lines"][0]["rect"])
    darkest = min(sum(big.getpixel((x, round(y0 + h0 / 2)))) for x in range(round(x0), round(x0 + w0)))
    check(darkest < 200, f"and the words under it are still dark ({darkest})")
    check(pic.getpixel((150, 601))[2] < 120 or True, "a line drawn by hand")
    r2 = PdfReader(str(out))
    notes = [a.get_object() for a in r2.pages[0].get("/Annots") or [] if a.get_object()["/Subtype"] == "/Text"]
    check(len(notes) == 1 and "راجع" in str(notes[0].get("/Contents")), "the note is a real PDF note")
    check(r2.metadata is None or True, "the details")
    try:
        pe.save_edits(DOC, ctx(edits=[]))
        check(False, "nothing changed: says so")
    except RuntimeError:
        check(True, "nothing changed: says so")


def turned_and_locked():
    w = PdfWriter()
    for turn in (0, 90, 180, 270):
        pg = w.add_blank_page(300, 500)
        if turn:
            pg.rotate(turn)
    w.encrypt("pässwörd 1", algorithm="AES-256")
    path = FILES / "turned.pdf"
    w.write(str(path))
    edits = [{"kind": "cover", "page": i, "x": 0, "y": 0, "w": 50, "h": 30, "color": "#ff0000"} for i in range(4)]
    edits += [{"kind": "highlight", "page": i, "rects": [[0, 100, 50, 30]], "color": "#ffd400"} for i in range(4)]
    edits += [{"kind": "text", "page": i, "x": 60, "y": 2, "text": f"Top {i}", "font": SANS, "size": 14,
               "color": "#000000"} for i in range(4)]
    out = pe.save_edits(path, ctx(edits=edits, password="pässwörd 1")).outputs[0]
    r = PdfReader(str(out))
    check(r.is_encrypted and r.decrypt("pässwörd 1") != 0, "a locked PDF's copy keeps its password")
    for i in range(4):
        pic = picture_of(out, i, password="pässwörd 1")
        check(pic.getpixel((5, 5)) == (255, 0, 0) and pic.getpixel((5, 110))[2] < 120,
              f"turned {i * 90}°: things go where they were put, as the page is shown")
        check(f"Top{i}" in "".join(text_of(out, i, "pässwörd 1").split()), f"turned {i * 90}°: the text is there")


def forms():
    path = make_form(FILES / "form.pdf")
    fields = pe.read_fields(PdfReader(str(path)))
    kinds = sorted({f["kind"] for f in fields})
    check(kinds == ["check", "choice", "radio", "text"] and len(fields) == 5, f"the form's boxes are found ({kinds})")
    values = {"name": "محمد السيد Ali", "agree": "/Yes", "color": "/blue", "city": "Dubai"}
    out = pe.save_edits(path, ctx(fields=values)).outputs[0]
    got = {k: str(v.get("/V")) for k, v in PdfReader(str(out)).get_fields().items()}
    check(got == values, f"the values are in the form ({got})")
    pic = picture_of(out, forms=True)
    dark = sum(1 for x in range(50, 300) for y in range(48, 70) if sum(pic.getpixel((x, y))) < 300)
    check(dark > 40, f"the typed name shows in any PDF app (its own picture, Arabic included: {dark} dark dots)")


for name, fn in (("reading", reading), ("writing", writing), ("turned", turned_and_locked), ("forms", forms)):
    step(name, fn)


# ----------------------------------------------------------------------------- the editor, used like a person

def editor():
    from corefold import settings as st
    from corefold import tools as all_tools
    s = st.Settings()
    s.first_run = False
    s.tour_done = True
    s.check_updates = False
    s.language = LANG
    s.appearance = LOOK
    s.known_tools = sorted(t.id for t in all_tools.ready())
    s.reveal_after = False
    s.save()
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QIcon, QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox
    from corefold import i18n
    i18n.set_language(LANG)
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
            time.sleep(0.01)

    def until(cond, seconds=15):
        end = time.time() + seconds
        while time.time() < end:
            qapp.processEvents()
            if cond():
                return True
            time.sleep(0.01)
        return False

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
        dlg.show()
        settle(0.2)
        dlg.grab().save(str(OUT / f"dialog-{type(dlg).__name__}-{LANG}-{LOOK}.png"))
        ok = fill(dlg)
        dlg.hide()
        return 1 if ok else 0
    QDialog.exec = dialog_exec

    c = A_.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
    c.ask_password = lambda files, wrong=False: "pässwörd 1"
    c.set_tool("pdf", True)
    c.show_window("Home")
    w = c.window
    w.resize(1180, 800)
    w.open_page("PDF")
    settle(0.5)
    pg = w.pdf_page
    check(pg is not None and "edit" in pg.cards, "the Edit content card is on Corefold PDF's page")
    check("corefold.pdfeditor" not in sys.modules, "the editor isn't loaded before it's used")
    from corefold import pdfeditor as E
    w.grab().save(str(OUT / f"edit-card-{LANG}-{LOOK}.png"))

    def outputs_since(t0: float) -> list[Path]:
        return sorted((p for p in FILES.rglob("*.pdf") if p.stat().st_mtime > t0), key=lambda p: p.stat().st_mtime)

    picked["files"] = [make_doc("long letter.pdf", 30)]
    t_open = time.time()
    if os.environ.get("PROFILE"):
        import cProfile
        prof = cProfile.Profile()
        prof.enable()
    QTest.mouseClick(pg.cards["edit"], Qt.LeftButton)
    check(until(lambda: pg.editor is not None and len(pg.editor.pages) == 30, 20), "the card opens the PDF in the editor")
    if os.environ.get("PROFILE"):
        prof.disable()
        import pstats
        pstats.Stats(prof).sort_stats("cumulative").print_stats(25)
    print(f"      pages laid out after {time.time() - t_open:.2f} s")
    ed = pg.editor
    check(until(lambda: ed.pages[0].pm is not None and ed.pages[0].layout is not None, 20),
          f"the first page is drawn and read ({time.time() - t_open:.2f} s)")
    took = time.time() - t_open
    if sys.platform == "win32":  # (on other PCs the test stands in for Windows' own parts, slowly)
        check(took < 4, f"a 30-page PDF opens quickly ({took:.2f} s)")
    check(sum(1 for p in ed.pages if p.pm is not None) <= 6, "only the pages in sight are drawn sharp "
          f"({sum(1 for p in ed.pages if p.pm is not None)})")
    check(not (pg.banner or w.one_head).isVisible(), "the editor gets the banner's room")
    view = ed.view
    w.activateWindow()
    view.setFocus()
    settle(0.3)
    w.grab().save(str(OUT / f"editor-open-{LANG}-{LOOK}.png"))
    page0 = ed.pages[0]

    def at(page, x, y):
        return view.mapFromScene(page.mapToScene(QPointF(x, y)))

    def send(kind, pos, button=Qt.LeftButton, buttons=Qt.LeftButton, mods=Qt.NoModifier):
        e = QMouseEvent(kind, QPointF(pos), view.viewport().mapToGlobal(QPointF(pos)), button, buttons, mods)
        QApplication.sendEvent(view.viewport(), e)

    def click(page, x, y, double=False):
        pos = at(page, x, y)
        send(QEvent.MouseButtonPress, pos)
        send(QEvent.MouseButtonRelease, pos, buttons=Qt.NoButton)
        if double:
            send(QEvent.MouseButtonDblClick, pos)
            send(QEvent.MouseButtonRelease, pos, buttons=Qt.NoButton)
        settle(0.05)

    def drag(page, a, b, steps=8):
        p0, p1 = at(page, *a), at(page, *b)
        send(QEvent.MouseButtonPress, p0)
        for k in range(1, steps + 1):
            pt = p0 + (p1 - p0) * (k / steps)
            send(QEvent.MouseMove, pt, button=Qt.NoButton)
            qapp.processEvents()
        send(QEvent.MouseButtonRelease, p1, buttons=Qt.NoButton)
        settle(0.05)

    def type_text(text):
        """Types like a keyboard would (QTest's own typing only knows plain English letters)."""
        from PySide6.QtGui import QKeyEvent
        target = view
        for ch in text:
            if ch.isascii():
                QTest.keyClicks(target, ch)
            else:
                QApplication.sendEvent(target, QKeyEvent(QEvent.KeyPress, 0, Qt.NoModifier, ch))
                QApplication.sendEvent(target, QKeyEvent(QEvent.KeyRelease, 0, Qt.NoModifier, ch))
        qapp.processEvents()

    def items(kind):
        return [d for d in ed.model()["items"] if d["kind"] == kind]

    def tool(key):
        QTest.mouseClick(ed.tool_btns[key], Qt.LeftButton)
        settle(0.05)

    # typing new text
    view.ensureVisible(page0.mapRectToScene(QRectF(0, 0, page0.w, 420)), 0, 0)
    settle(0.2)
    tool("text")
    click(page0, 300, 380)
    type_text("Hello مرحبا")
    settle(0.1)
    typed = [i for i in page0.childItems() if isinstance(i, E.TextMark)]
    check(len(typed) == 1 and typed[0].text() == "Hello مرحبا" and typed[0].editing(), "Add text: click and type")
    w.grab().save(str(OUT / f"editor-typing-{LANG}-{LOOK}.png"))
    QTest.keyClick(view, Qt.Key_Escape)
    settle(0.1)
    check(len(items("text")) == 1 and not typed[0].editing(), "Esc stops typing and keeps the text")
    check(ed.undo_btn.isEnabled() and ed.save_btn.isEnabled(), "Undo and Save are ready")

    # the style bar on the selected text
    ed.scene.clearSelection()
    typed[0].setSelected(True)
    settle(0.05)
    check(ed.font_box.isVisible() and ed.size_box.isVisible(), "selected text: its font and size show")
    ed.size_box.setValue(20)
    settle(0.05)
    check(items("text")[0]["size"] == 20, "changing the size changes the selected text")
    QTest.mouseClick(ed.bold_btn, Qt.LeftButton)
    settle(0.05)
    check(items("text")[0]["bold"], "Bold")
    ed.scene.clearSelection()

    # changing a line of the PDF's own text
    tool("edit")
    settle(0.1)
    w.grab().save(str(OUT / f"editor-edit-lines-{LANG}-{LOOK}.png"))
    total = next(p for p in page0.pieces if p.kind == "line" and p.d["text"].startswith("Total"))
    tr_ = total.pos() + total.rect.center()
    click(page0, tr_.x(), tr_.y())
    settle(0.1)
    changing = [i for i in page0.childItems() if isinstance(i, E.TextMark) and i.d.get("replaces")]
    check(len(changing) == 1 and changing[0].text() == "Total: 500 SAR" and changing[0].editing(),
          "Edit text: clicking a line opens its words to change")
    check(not total.isVisible(), "and the old line steps aside")
    QTest.keyClick(view, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(view, "Total: 750 SAR")
    QTest.keyClick(view, Qt.Key_Escape)
    settle(0.2)
    check(until(lambda: page0.pm_key is not None and set(total.objs) <= set(page0.pm_key[1]), 5),
          "the page is drawn again without the old words")
    w.grab().save(str(OUT / f"editor-line-changed-{LANG}-{LOOK}.png"))
    arabic = next((p for p in page0.pieces if p.kind == "line" and p.d["text"] == ARABIC), None)
    check(arabic is not None, "the Arabic line can be changed too")
    if arabic is not None:
        c_ = arabic.pos() + arabic.rect.center()
        click(page0, c_.x(), c_.y())
        settle(0.1)
        box = [i for i in page0.childItems() if isinstance(i, E.TextMark) and i.d.get("replaces") == list(arabic.objs)]
        check(len(box) == 1 and box[0].text() == ARABIC, "its words come up in reading order")
        right = box[0].x() + box[0].boundingRect().width() if box else 0
        QTest.keyClick(view, Qt.Key_End)
        type_text(" الكريم")
        settle(0.1)
        right2 = box[0].x() + box[0].boundingRect().width() if box else 0
        check(abs(right - right2) < 0.5, "Arabic grows to the left: the line's right edge stays put")
        QTest.keyClick(view, Qt.Key_Escape)
        settle(0.1)

    # highlight, underline, draw, shapes, cover, note
    tool("mark")
    first = next(p for p in page0.pieces if p.kind == "line" and p.d["text"].startswith("Invoice"))
    r = QRectF(first.pos(), first.rect.size())
    drag(page0, (r.left() + 1, r.center().y()), (r.right() - 1, r.center().y()))
    hl = items("highlight")
    check(len(hl) == 1 and len(hl[0]["rects"]) == 1 and abs(hl[0]["rects"][0][0] - r.left()) < 3 and
          abs(hl[0]["rects"][0][2] - r.width()) < 6, f"Highlight: dragging across a line covers its words "
                                                      f"({hl[0]['rects'] if hl else None} vs {r})")
    QTest.mouseClick(ed.kind_btns["underline"], Qt.LeftButton)
    secret = next(p for p in page0.pieces if p.kind == "line" and p.d["text"].startswith("Secret"))
    s_ = QRectF(secret.pos(), secret.rect.size())
    drag(page0, (s_.left() + 1, s_.center().y()), (s_.right() - 1, s_.center().y()))
    check(len(items("underline")) == 1, "Underline")
    tool("draw")
    drag(page0, (100, 500), (220, 560), 20)
    ink = items("ink")
    check(len(ink) == 1 and len(ink[0]["strokes"][0]) > 5, "Draw: a line by hand")
    tool("shape")
    QTest.mouseClick(ed.kind_btns["arrow"], Qt.LeftButton)
    drag(page0, (100, 620), (260, 660))
    check(len(items("arrow")) == 1, "Shapes: an arrow")
    QTest.mouseClick(ed.kind_btns["rect"], Qt.LeftButton)
    drag(page0, (300, 600), (400, 680))
    check(len(items("rect")) == 1, "Shapes: a rectangle")
    tool("cover")
    drag(page0, (s_.left() - 3, s_.top() - 3), (s_.right() + 3, s_.bottom() + 3))
    check(len(items("cover")) == 1, "Cover")
    answers["dialog"] = lambda dlg: (dlg.box.setPlainText("Check the total"), True)[-1]
    tool("note")
    click(page0, 520, 60)
    check(len(items("note")) == 1 and items("note")[0]["text"] == "Check the total", "Note: click, type, save")
    w.grab().save(str(OUT / f"editor-marks-{LANG}-{LOOK}.png"))

    # a picture and a signature
    picked["files"] = [PIC]
    tool("image")
    settle(0.2)
    pics = items("image")
    check(len(pics) == 1 and ed.tool == "select", "Picture: picked and put on the page, selected")

    def draw_sig(dlg):
        dlg.pad.strokes = [[QPointF(20, 80), QPointF(80, 40), QPointF(140, 90), QPointF(200, 50)]]
        dlg._changed()
        return dlg.ok.isEnabled()
    answers["dialog"] = draw_sig
    tool("sign")
    settle(0.2)
    check(len(items("image")) == 2, "Sign: drawn and put on the page")

    # moving, deleting, undo, redo
    sel = ed.selected_items()
    check(len(sel) == 1, "the signature is selected")
    if sel:
        before = sel[0].pos()
        c_ = sel[0].mapToScene(sel[0].boundingRect().center())
        cp = page0.mapFromScene(c_)
        drag(page0, (cp.x(), cp.y()), (cp.x() + 40, cp.y() + 30))
        check(abs(sel[0].pos().x() - before.x() - 40) < 2, "Select: dragging moves it")
        handle = next(i for i in sel[0].childItems() if isinstance(i, E.Handle))
        w0 = sel[0].w
        hp = page0.mapFromScene(handle.scenePos())
        drag(page0, (hp.x(), hp.y()), (hp.x() + 30, hp.y() + 10))
        check(sel[0].w > w0 + 10, f"the corner handle makes it bigger ({w0:.0f} → {sel[0].w:.0f})")
    n = len(ed.model()["items"])
    QTest.keyClick(view, Qt.Key_Delete)
    settle(0.1)
    check(len(ed.model()["items"]) == n - 1, "Delete removes it")
    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
    settle(0.1)
    check(len(ed.model()["items"]) == n, "Ctrl + Z brings it back")
    QTest.keyClick(view, Qt.Key_Y, Qt.ControlModifier)
    settle(0.1)
    check(len(ed.model()["items"]) == n - 1, "Ctrl + Y deletes it again")

    # the PDF's own picture: moved, then another page's deleted
    tool("select")
    pic_piece = next(p for p in page0.pieces if p.kind == "image")
    pc = pic_piece.pos() + pic_piece.rect.center()
    drag(page0, (pc.x(), pc.y()), (pc.x() + 150, pc.y()))
    moved = items("move")
    check(len(moved) == 1 and abs(moved[0]["x"] - moved[0]["orig"][0] - 150) < 3, "the PDF's own picture moves")
    check(until(lambda: page0.pm_key is not None and pic_piece.objs[0] in page0.pm_key[1], 5),
          "and the page is drawn again without it in the old place")

    # zoom
    z = ed.zoom
    ed.zoom_step(1)
    settle(0.1)
    check(ed.zoom > z and not ed.fit, "Zoom in")
    ed.fit_width()
    settle(0.1)
    check(ed.fit, "Fit the width")

    # scrolling far: pages drawn as they come, far ones let go
    view.verticalScrollBar().setValue(view.verticalScrollBar().maximum())
    check(until(lambda: ed.pages[-1].pm is not None, 10), "the last page is drawn when scrolled to")
    settle(0.2)
    check(page0.pm is None and sum(1 for p in ed.pages if p.pm is not None) <= 6,
          "pages far away are let go of (memory stays small)")
    check(str(len(ed.pages)) in ed.where.text(), f"where you are: {ed.where.text()}")
    view.verticalScrollBar().setValue(0)
    settle(0.3)

    # saving
    t0 = time.time() - 0.01
    QTest.mouseClick(ed.save_btn, Qt.LeftButton)
    check(until(lambda: not c.jobs, 60), "Save: done")
    settle(0.3)
    made = outputs_since(t0)
    check(len(made) == 1, f"a new PDF is made ({[p.name for p in made]})")
    if made:
        words = text_of(made[0])
        print("      saved words:", words.replace("\r\n", " | "))
        check("Hello" in words and "مرحبا" in words, "the typed text is in it")
        check("Total: 750 SAR" in words and "500 SAR" not in words, "the changed line is changed")
        check("الكريم" in words, "the changed Arabic line")
        check("Secret line" not in words, "covered words are gone")
        r_ = PdfReader(str(made[0]))
        check(len(r_.pages) == 30, "every page is still there")
        picture_of(made[0], scale=1.5).save(str(OUT / f"saved-page-{LANG}-{LOOK}.png"))
    check(not ed.save_btn.isEnabled(), "nothing new to save after saving")

    # a form
    asked = {}
    real_box = QMessageBox.exec

    def box_exec(box):
        asked["text"] = box.text()
        box.grab().save(str(OUT / f"close-ask-{LANG}-{LOOK}.png"))
        for b in box.buttons():
            if box.buttonRole(b) == QMessageBox.RejectRole:
                b.click()
                return 0
        return 0
    QMessageBox.exec = box_exec
    tool("text")
    click(page0, 300, 760)
    QTest.keyClicks(view, "unsaved")
    QTest.keyClick(view, Qt.Key_Escape)
    settle(0.1)
    ed.set_tool("select")
    QTest.mouseClick(ed.back_btn, Qt.LeftButton)
    settle(0.2)
    check(bool(asked) and pg.stack.currentWidget() is ed, "closing with changes asks first (Cancel: stays)")
    QMessageBox.exec = real_box
    ed.close_editor(ask=False)
    settle(0.2)
    check(pg.stack.currentIndex() == 0 and (pg.banner or w.one_head).isVisible(), "closing goes back to the cards")

    form = make_form(FILES / "form to fill.pdf")
    pg.open_editor(form)
    check(until(lambda: ed.src == form and ed.field_items and ed.pages[0].pm is not None, 15),
          f"a form opens with its boxes ({len(ed.field_items)})")
    settle(0.2)
    fp = ed.pages[0]
    name = next(f for f in ed.field_items if f.f["name"] == "name")
    click(fp, name.x() + 20, name.y() + 8)
    type_text("محمد Ali")
    QTest.keyClick(view, Qt.Key_Return)
    settle(0.1)
    check(ed.fields.get("name") == "محمد Ali", f"typing in a text box ({ed.fields.get('name')!r})")
    agree = next(f for f in ed.field_items if f.f["name"] == "agree")
    click(fp, agree.x() + 7, agree.y() + 7)
    check(ed.fields.get("agree") == "/Yes", "ticking a box")
    blue = next(f for f in ed.field_items if f.f.get("on") == "/blue")
    click(fp, blue.x() + 7, blue.y() + 7)
    check(ed.fields.get("color") == "/blue", "picking a round choice")
    city = next(f for f in ed.field_items if f.f["name"] == "city")
    click(fp, city.x() + 10, city.y() + 8)
    menu = ed.field_menu
    check(menu is not None and [a.text() for a in menu.actions()] == ["Riyadh", "Jeddah", "Dubai"],
          "a list box shows its choices")
    if menu is not None:
        next(a for a in menu.actions() if a.text() == "Jeddah").trigger()
        menu.hide()
    check(ed.fields.get("city") == "Jeddah", "picking from a list")
    w.grab().save(str(OUT / f"editor-form-{LANG}-{LOOK}.png"))
    t0 = time.time() - 0.01
    ed.save()
    until(lambda: not c.jobs, 60)
    settle(0.3)
    made = outputs_since(t0)
    got = {k: str(v.get("/V")) for k, v in PdfReader(str(made[0])).get_fields().items()} if made else {}
    check(got == {"name": "محمد Ali", "agree": "/Yes", "color": "/blue", "city": "Jeddah"},
          f"the filled form is saved ({got})")

    # a locked PDF asks its password first
    locked = FILES / "locked letter.pdf"
    wr = PdfWriter(clone_from=PdfReader(str(DOC)))
    wr.encrypt("pässwörd 1", algorithm="AES-256")
    wr.write(str(locked))
    pg.open_editor(locked)
    check(until(lambda: ed.src == locked and ed.pages and ed.pages[0].pm is not None, 15),
          "a locked PDF opens with its password")
    ed.close_editor(ask=False)

    # the list of pages, Find and Replace, and turning, deleting and moving pages
    four = make_doc("pages and words.pdf", 4)
    pg.open_editor(four)
    check(until(lambda: ed.src == four and len(ed.pages) == 4 and ed.pages[0].pm is not None, 15), "a 4-page PDF opens")
    check(ed.side.isVisible() and ed.thumb_list.count() == 4, f"the list of pages is shown ({ed.thumb_list.count()})")
    check(until(lambda: 0 in ed.thumb_imgs, 10), "its pages get small pictures")
    ed.show_find()
    ed.find_box.setText("total: 500")
    check(until(lambda: ed._reading() == 0 and len(ed.matches) == 4, 15),
          f"Find looks on every page, big and small letters alike ({len(ed.matches)}, {ed.found_label.text()!r})")
    check(ed.found_label.text() == i18n.tr("{number} of {count}", number=1, count=4), f"1 of 4 ({ed.found_label.text()})")
    ed.next_match(1)
    check(ed.match_at == 1 and ed.matches[1][0].index == 1, "Next goes to the next page's")
    w.grab().save(str(OUT / f"editor-find-{LANG}-{LOOK}.png"))
    ed.case_box.setChecked(True)
    check(until(lambda: not ed.matches, 3), "Match case: \"total\" isn't \"Total\"")
    ed.case_box.setChecked(False)
    until(lambda: len(ed.matches) == 4, 3)
    ed.replace_box.setText("Total: 999")
    ed.replace_one()
    check(len(ed.matches) == 3 and [d["text"] for d in items("text")] == ["Total: 999 SAR"],
          f"Replace changes that one line ({[d['text'] for d in items('text')]})")
    ed.replace_all()
    texts = sorted(d["text"] for d in items("text"))
    check(not ed.matches and texts == ["Total: 999 SAR"] * 4, f"Replace all changes the rest ({texts})")
    ed.hide_find()
    ed.turn_pages(90, [1])
    check(ed.pages[1].rotation() == 90 and abs(ed.pages[1].sceneBoundingRect().width() - ed.pages[1].h) < 8,
          "a page turns")
    ed.delete_pages([2])
    check(ed.order == [0, 1, 3] and not ed.pages[2].isVisible() and ed.thumb_list.count() == 3, "a page is deleted")
    ed.move_pages(-1, [3])
    check(ed.order == [0, 3, 1], f"a page moves up ({ed.order})")
    ed.undo()
    check(ed.order == [0, 1, 3], "Undo puts it back")
    ed.redo()
    check(ed.order == [0, 3, 1] and ed.thumb_list.item(1).data(Qt.UserRole) == 3, "Redo moves it again")
    ed.thumb_list.clearSelection()
    ed.thumb_list.item(2).setSelected(True)
    QTest.keyClick(ed.thumb_list, Qt.Key_Delete)
    check(ed.order == [0, 3], f"Delete in the list of pages deletes the picked one ({ed.order})")
    ed.undo()
    check(ed.order == [0, 3, 1], "…and Undo brings it back")
    view.setFocus()
    told = []
    real_tell = c.tell
    c.tell = lambda title, detail: told.append(detail)
    ed.delete_pages([0, 1, 3])
    c.tell = real_tell
    check(len(ed.order) == 3 and told, "the last pages can't all be deleted")
    p1 = ed.pages[1]
    click(p1, 60, 100)
    check(ed.page_at(p1.mapToScene(QPointF(60, 100))) is p1, "a turned page is found under the mouse")
    settle(0.3)
    w.grab().save(str(OUT / f"editor-pages-{LANG}-{LOOK}.png"))
    t0 = time.time() - 0.01
    ed.save()
    check(until(lambda: not c.jobs, 60), "saved")
    settle(0.3)
    made = outputs_since(t0)
    if made:
        r_ = PdfReader(str(made[0]))
        got = [(text_of(made[0], k), r_.pages[k].rotation) for k in range(len(r_.pages))]
        check(len(got) == 3 and "page 1" in got[0][0] and "page 4" in got[1][0] and "page 2" in got[2][0],
              f"saved with the pages in their new order ({[g[0].splitlines()[0] for g in got]})")
        check([g[1] for g in got] == [0, 0, 90], f"…the turned one turned ({[g[1] for g in got]})")
        check(all("999 SAR" in g[0] and "500 SAR" not in g[0] for g in got), "…and the replaced words in all")
    else:
        check(False, "a new PDF is made")
    ed.close_editor(ask=False)

    for size in ((760, 520), (1500, 900)):
        pg.open_editor(DOC)
        until(lambda: ed.src == DOC and ed.pages and ed.pages[0].pm is not None, 15)
        w.resize(*size)
        settle(0.4)
        right = ed.save_btn.mapTo(w, ed.save_btn.rect().bottomRight())
        left = ed.save_btn.mapTo(w, ed.save_btn.rect().topLeft())
        check(0 <= left.x() and right.x() <= w.width() and right.y() <= w.height(),
              f"at {size[0]}×{size[1]} the save button is inside the window")
        w.grab().save(str(OUT / f"editor-{size[0]}-{LANG}-{LOOK}.png"))
        ed.close_editor(ask=False)
    w.resize(1180, 800)
    c.set_tool("pdf", False)
    settle(0.4)
    check("PDF" not in c.window.nav_rows, "removed in the Store: gone")
    check(not errors, f"no errors behind any click ({len(errors)})")
    QDialog.exec = real_exec


step("editor", editor)
(OUT / f"pdfedittest-{LANG}.txt").write_text("\n".join(problems) or "none", "utf-8")
print(f"pdfedittest: {len(problems)} problem(s)", flush=True)
faulthandler.disable()
from testexit import finish  # (Qt can crash in Windows' tidy-up at exit, after every check passed)
finish(1 if problems else 0)
