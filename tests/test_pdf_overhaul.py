"""Corefold PDF's editor, made fuller: the PDF's own paragraphs are found (wrapped lines that belong together,
lined up and spaced as they are) and can be changed whole; words are found exactly where they are on a line
(so every place some words are can be covered); and the new copy gets links, blank pages, see-through
things, turned pictures, filled shapes and lined-up, spaced text. A picture of the PDF's own can be kept as
a file."""
import threading
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image
from pypdf import PdfReader

from corefold import pdfedit
from corefold.core import Ctx, Options

SANS = "DejaVu Sans"


def _ctx(tmp_path: Path, **params) -> Ctx:
    options = Options()
    options.output_folder = tmp_path / "out"
    options.output_folder.mkdir(exist_ok=True)
    return Ctx(options, lambda f: None, threading.Event(), params)


@pytest.fixture
def para_pdf(qapp, tmp_path) -> Path:
    from PySide6.QtCore import QMarginsF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter, QTextDocument, \
        QTextOption
    out = tmp_path / "paragraphs.pdf"
    w = QPdfWriter(str(out))
    w.setResolution(72)
    w.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(0, 0, 0, 0)))
    p = QPainter(w)
    f = QFont(SANS)
    f.setPixelSize(14)
    p.setFont(f)
    p.drawText(60, 70, "Name: Ahmad")
    p.drawText(60, 90, "Phone: 555 1234")
    for k, align in enumerate((Qt.AlignJustify, Qt.AlignLeft, Qt.AlignHCenter)):
        doc = QTextDocument()
        doc.setDefaultFont(f)
        doc.setTextWidth(300)
        opt = QTextOption()
        opt.setAlignment(align)
        opt.setWrapMode(QTextOption.WordWrap)
        doc.setDefaultTextOption(opt)
        doc.setPlainText("The quick brown fox jumps over the lazy dog while the small cat watches from the warm "
                         "window and the birds sing in the old tall tree near the river.")
        p.save()
        p.translate(60, 130 + k * 150)
        doc.drawContents(p)
        p.restore()
    p.drawText(60, 620, "Code: SECRET-42 (keep it)")
    img = QImage(80, 60, QImage.Format_RGB32)
    img.fill(QColor("orange"))
    p.drawImage(QRectF(60, 660, 80, 60), img)
    p.end()
    return out


def _layout(path: Path) -> dict:
    doc = pdfium.PdfDocument(str(path))
    try:
        with pdfedit.PDFIUM:
            return pdfedit.read_layout(doc, 0)
    finally:
        doc.close()


def test_paragraphs_found_with_their_look(para_pdf):
    lay = _layout(para_pdf)
    paras = lay["paras"]
    assert [p["align"] for p in paras] == ["justify", "left", "center"]
    for p in paras:
        assert len(p["lines"]) == 4
        assert 1.1 < p["spacing"] < 1.4
        texts = [lay["lines"][k]["text"] for k in p["lines"]]
        assert texts[0].startswith("The quick") and texts[-1].endswith("river.")
    # a form's short lines one under the other aren't a paragraph
    name = next(d for d in lay["lines"] if d["text"].startswith("Name"))
    assert "para" not in name


def test_words_are_where_they_are(para_pdf):
    lay = _layout(para_pdf)
    code = next(d for d in lay["lines"] if d["text"].startswith("Code"))
    starts = [code["text"][a:b] for a, b, _x0, _x1 in code["words"]]
    assert starts == ["Code:", "SECRET-42", "(keep", "it)"]
    _a, _b, x0, x1 = code["words"][1]
    assert x0 > code["rect"][0] + 30 and x1 - x0 > 50


def test_line_text_spans_follow_arabic_reading_order():
    # three words shown right to left: reading order starts at the right
    chars = [("c", 0, 5), ("b", 10, 15), ("ا", 20, 25)]
    spans = []
    text, rtl = pdfedit.line_text(chars, 10, spans)
    assert len(spans) == 3 and all(text[a:b] for a, b, _l, _r in spans)
    assert [text[a:b] for a, b, _l, _r in spans] == text.split(" ")


def test_web_address():
    assert pdfedit.web_address("example.com/help") == "https://example.com/help"
    assert pdfedit.web_address("https://a.org") == "https://a.org"
    assert pdfedit.web_address("me@mail.com") == "mailto:me@mail.com"
    for bad in ("javascript:alert(1)", "file:///C:/x.exe", "run.exe", "not a place", "", "https://", "a b.com"):
        assert pdfedit.web_address(bad) == ""


def test_saved_paragraph_links_blank_page_and_looks(qapp, para_pdf, tmp_path):
    lay = _layout(para_pdf)
    para = lay["paras"][0]
    objs = sorted({o for k in para["lines"] for o in lay["lines"][k]["objs"]})
    x, y, w, h = para["rect"]
    pic = tmp_path / "red.png"
    Image.new("RGB", (40, 20), "red").save(pic)
    code = next(d for d in lay["lines"] if d["text"].startswith("Code"))
    _a, _b, cx0, cx1 = code["words"][1]
    edits = [
        {"kind": "text", "page": 0, "font": SANS, "size": 14, "color": "#000000", "x": x - 1, "y": y - 2,
         "width": w + 2, "align": "justify", "spacing": para["spacing"], "replaces": objs, "underline": True,
         "opacity": 0.6, "text": "A brand new paragraph that is long enough to wrap onto more than one line."},
        {"kind": "image", "page": 0, "path": str(pic), "x": 400, "y": 600, "w": 20, "h": 40, "turn": 90},
        {"kind": "rect", "page": 0, "points": [300, 700, 400, 760], "color": "#d32f2f", "width": 2,
         "fill": "#ffe066", "opacity": 0.5},
        {"kind": "cover", "page": 0, "x": cx0 - 0.6, "y": code["rect"][1] - 0.6, "w": cx1 - cx0 + 1.2,
         "h": code["rect"][3] + 1.2, "color": "#000000"},
        {"kind": "link", "page": 0, "x": 60, "y": 60, "w": 100, "h": 14, "url": "example.com"},
        {"kind": "link", "page": 0, "x": 60, "y": 80, "w": 100, "h": 14, "target": 1},
        {"kind": "link", "page": 0, "x": 60, "y": 100, "w": 100, "h": 14, "url": "javascript:alert(1)"},
        {"kind": "text", "page": 1, "font": SANS, "size": 20, "x": 50, "y": 50, "text": "On the blank page"},
    ]
    res = pdfedit.save_edits(para_pdf, _ctx(tmp_path, edits=edits, blanks=[[595, 842]], order=[0, 1]))
    out = res.outputs[0] if hasattr(res, "outputs") else res.files[0]
    reader = PdfReader(str(out))
    assert len(reader.pages) == 2
    doc = pdfium.PdfDocument(str(out))
    try:
        first = doc[0].get_textpage().get_text_range()
        second = doc[1].get_textpage().get_text_range()
    finally:
        doc.close()
    assert "brand new paragraph" in first
    assert "SECRET-42" not in first and "Code:" in first and "(keep it)" in first
    assert "On the blank page" in second
    links = [a.get_object() for a in reader.pages[0]["/Annots"] if a.get_object()["/Subtype"] == "/Link"]
    uris = [a["/A"]["/URI"] for a in links if "/A" in a]
    assert uris == ["https://example.com"]  # (javascript: is never made into a link)
    dests = [a["/Dest"] for a in links if "/Dest" in a]
    assert len(dests) == 1 and dests[0][0].get_object() == reader.pages[1].get_object()
    # the turned picture is drawn standing up in its box (taller than wide)
    page = pdfium.PdfDocument(str(out))
    try:
        img = page[0].render(scale=1).to_pil().convert("RGB")
    finally:
        page.close()
    reds = [(px, py) for px in range(390, 430) for py in range(590, 650)
            if img.getpixel((px, py))[0] > 200 and max(img.getpixel((px, py))[1:]) < 80]
    assert reds and max(py for _, py in reds) - min(py for _, py in reds) > 30


def test_text_document_lines_up_and_spaces(qapp):
    from PySide6.QtCore import Qt
    d = {"text": "one two three four five six seven eight nine ten", "font": SANS, "size": 12, "width": 80,
         "align": "center", "spacing": 2.0}
    doc = pdfedit.text_document(d)
    block = doc.begin().blockFormat()
    assert block.alignment() & Qt.AlignHCenter
    assert abs(block.lineHeight() - 24.0) < 0.01
    single = pdfedit.text_document(dict(d, spacing=None))
    assert doc.size().height() > single.size().height() + 10


def test_save_picture(para_pdf, tmp_path):
    lay = _layout(para_pdf)
    obj = lay["images"][0]["obj"]
    out = tmp_path / "kept.png"
    pdfedit.save_picture(para_pdf, "", 0, obj, out)
    im = Image.open(out)
    assert im.size == (80, 60) and im.convert("RGB").getpixel((40, 30))[0] > 200
    with pytest.raises(RuntimeError):
        pdfedit.save_picture(para_pdf, "", 0, 0, tmp_path / "not.png")


def test_an_arabic_paragraph_is_found_lined_up_right_and_saved_that_way(qapp, tmp_path):
    from PySide6.QtCore import QMarginsF, Qt
    from PySide6.QtGui import QFont, QPageLayout, QPageSize, QPainter, QPdfWriter, QTextDocument, QTextOption
    src = tmp_path / "arabic.pdf"
    w = QPdfWriter(str(src))
    w.setResolution(72)
    w.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(0, 0, 0, 0)))
    p = QPainter(w)
    doc = QTextDocument()
    f = QFont(SANS)
    f.setPixelSize(14)
    doc.setDefaultFont(f)
    o = QTextOption()
    o.setTextDirection(Qt.RightToLeft)
    o.setAlignment(Qt.AlignRight | Qt.AlignAbsolute)
    doc.setDefaultTextOption(o)
    doc.setPlainText("هذه فقرة عربية طويلة نكتبها لنختبر كيف يجد المحرر الفقرات العربية التي تلتف على عدة أسطر "
                     "في الصفحة ونرى هل تبقى محاذاتها إلى اليمين كما هي بعد التعديل والحفظ")
    doc.setTextWidth(320)
    p.translate(200, 100)
    doc.drawContents(p)
    p.end()
    lay = _layout(src)
    assert len(lay["paras"]) == 1
    para = lay["paras"][0]
    assert len(para["lines"]) >= 3 and para["align"] == "right"
    x, y, width, h = para["rect"]
    objs = sorted({o for k in para["lines"] for o in lay["lines"][k]["objs"]})
    edits = [{"kind": "text", "page": 0, "font": SANS, "size": 14, "color": "#000000", "x": x - 1, "y": y - 2,
              "width": width + 2, "align": "right", "spacing": para["spacing"], "replaces": objs,
              "text": "فقرة جديدة قصيرة في سطرين تقريبًا لأننا نكتب كلمات كثيرة بعض الشيء هنا كي تلتف"}]
    out = pdfedit.save_edits(src, _ctx(tmp_path, edits=edits)).outputs[0]
    new = _layout(out)
    lines = [d for d in new["lines"] if d["rect"][1] < y + h + 20]
    assert len(lines) >= 2 and all(d.get("rtl") for d in lines)
    rights = [d["rect"][0] + d["rect"][2] for d in lines]
    assert max(rights) - min(rights) < 3  # (every line ends at the same right edge)
    assert abs(max(rights) - (x + width)) < 4
    text = " ".join(d["text"] for d in lines)
    assert "فقرة جديدة" in text and "هذه فقرة" not in text
