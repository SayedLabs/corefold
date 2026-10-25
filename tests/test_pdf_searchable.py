"""Make searchable: a scanned PDF gets its words as hidden text in the right places (Arabic too), the
pages look exactly as before, pages with their own text are left alone, and reading it again doesn't
write the words twice. (Windows' reader is stood in for: it can't run off Windows.)"""
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from corefold import pdfocr
from corefold.core import Ctx, Options
from corefold.extras import OcrLine, OcrWord

# words on the scan, as parts of the picture: (text, left, top, width, height)
LINES = [[("Invoice", 0.10, 0.08, 0.25, 0.04), ("2024", 0.38, 0.08, 0.12, 0.04)],
         [("مرحبا", 0.70, 0.20, 0.18, 0.04), ("بالعالم", 0.45, 0.20, 0.22, 0.04)]]


def _ctx(**params) -> Ctx:
    return Ctx(Options(), lambda f: None, threading.Event(), params)


def fake_reader(im):
    """Windows' reader: the words above, where they are on this picture."""
    W, H = im.size
    return [OcrLine([OcrWord(t, x * W, y * H, w * W, h * H) for t, x, y, w, h in line]) for line in LINES]


@pytest.fixture(autouse=True)
def reader(monkeypatch):
    monkeypatch.setattr(pdfocr, "_read", fake_reader)


def scan(path: Path, pages: int = 1, turn: int = 0) -> Path:
    ims = []
    for _ in range(pages):
        im = Image.new("RGB", (1240, 1754), "white")  # (A4 at 150 dpi)
        d = ImageDraw.Draw(im)
        for line in LINES:
            for _t, x, y, w, h in line:
                d.rectangle([x * 1240, y * 1754, (x + w) * 1240, (y + h) * 1754], fill="black")
        ims.append(im)
    ims[0].save(path, "PDF", resolution=150, save_all=True, append_images=ims[1:])
    if turn:
        from pypdf import PdfReader, PdfWriter
        w = PdfWriter(clone_from=PdfReader(str(path)))
        for p in w.pages:
            p.rotate(turn)
        with open(path, "wb") as fh:
            w.write(fh)
    return path


def _text(pdf: Path) -> str:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    try:
        return "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    finally:
        doc.close()


def _picture(pdf: Path, i: int = 0):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    try:
        return doc[i].render(scale=0.5).to_pil().convert("L")
    finally:
        doc.close()


def test_words_become_searchable_and_the_page_looks_the_same(tmp_path):
    src = scan(tmp_path / "scan.pdf", pages=2)
    result = pdfocr.make_searchable(src, _ctx())
    out = result.outputs[0]
    assert out.name == "scan (searchable).pdf"
    text = _text(out)
    for word in ("Invoice", "2024", "مرحبا", "بالعالم"):
        assert text.count(word) == 2, (word, text)
    from PIL import ImageChops
    assert ImageChops.difference(_picture(src), _picture(out)).getbbox() is None
    assert out.stat().st_size < src.stat().st_size + 20_000  # (a tiny font, not a whole Windows one)


def test_words_land_where_they_are_on_the_page(tmp_path):
    import pypdfium2 as pdfium
    for turn in (0, 90):
        src = scan(tmp_path / f"scan{turn}.pdf", turn=turn)
        out = pdfocr.make_searchable(src, _ctx()).outputs[0]
        doc = pdfium.PdfDocument(str(out))
        page = doc[0]
        tp = page.get_textpage()
        found = tp.search("Invoice").get_next()
        assert found is not None
        l, b, r, t = tp.get_charbox(found[0])
        W, H = page.get_size()
        # the middle of the first letter, as parts of the page as shown (PDFium's boxes are on the unturned page)
        if turn == 90:  # (turned a quarter clockwise: the page's left side is at the top)
            x, y = (b + t) / 2 / W, (l + r) / 2 / H
        else:
            x, y = (l + r) / 2 / W, 1 - (b + t) / 2 / H
        doc.close()
        assert abs(y - 0.10) < 0.03, (turn, x, y)
        assert 0.09 < x < 0.13, (turn, x, y)


def test_pages_with_their_own_text_are_left_alone(tmp_path):
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(595, 842)
    src = tmp_path / "blank.pdf"
    with open(src, "wb") as fh:
        w.write(fh)
    assert pdfocr.make_searchable(src, _ctx()).outputs == []


def test_reading_again_doesnt_write_the_words_twice(tmp_path):
    out = pdfocr.make_searchable(scan(tmp_path / "scan.pdf"), _ctx()).outputs[0]
    assert pdfocr.make_searchable(out, _ctx()).outputs == []  # (already searchable)
    again = pdfocr.make_searchable(out, _ctx(redo=True)).outputs[0]
    assert _text(again).count("Invoice") == 1


def test_a_locked_pdf_stays_locked(tmp_path):
    from pypdf import PdfReader, PdfWriter
    src = scan(tmp_path / "scan.pdf")
    w = PdfWriter(clone_from=PdfReader(str(src)))
    w.encrypt("pw", algorithm="AES-256")
    locked = tmp_path / "locked.pdf"
    with open(locked, "wb") as fh:
        w.write(fh)
    out = pdfocr.make_searchable(locked, _ctx(password="pw")).outputs[0]
    r = PdfReader(str(out))
    assert r.is_encrypted and r.decrypt("pw")


def test_the_hidden_font_is_small_and_knows_every_letter():
    data = pdfocr.hidden_font("abc مرحبا")
    assert len(data) < 2000
    from PIL import ImageFont
    import io
    ImageFont.truetype(io.BytesIO(data), 20)  # (FreeType opens it)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own reader")
def test_windows_really_reads_a_scan(tmp_path, monkeypatch):
    from corefold import extras
    if not extras.ocr_available():
        pytest.skip("no Windows OCR here")
    monkeypatch.setattr(pdfocr, "_read", lambda im: extras.read_lines(im, enlarge=False))
    from PIL import ImageFont
    im = Image.new("RGB", (2480, 3508), "white")  # (A4 at 300 dpi, like a scanner makes)
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 64)
    d.text((240, 300), "Quarterly report for the north office", fill="black", font=font)
    d.text((240, 420), "Total due 4,250 dollars", fill="black", font=font)
    src = tmp_path / "scan.pdf"
    im.save(src, "PDF", resolution=300)
    out = pdfocr.make_searchable(src, _ctx()).outputs[0]
    text = _text(out).lower()
    assert "quarterly" in text and "report" in text and "total" in text, text
