"""Corefold PDF's editor can change everything on a page: text inside groups (form XObjects), turned text,
drawn shapes and colour gradients, the PDF's own notes, highlights and links, form boxes marked read only,
hidden words over a scan, and text in fonts that don't say which letters they have. And changing one thing
leaves everything else on the page exactly as it was (PDFium alone loses gradients, patterns and Type 3 text
when it writes a page again)."""
import threading
import zlib
from pathlib import Path
from unittest.mock import MagicMock

import pypdfium2 as pdfium
import pytest
from PIL import ImageChops
from PySide6.QtCore import QPointF
from pypdf import PdfReader, PdfWriter

from corefold import pdfcontent, pdfedit
from corefold.core import Ctx, Options


@pytest.fixture(autouse=True)
def _qt(qapp):
    """(the new copy's text and form boxes are drawn by Qt)"""
    return qapp


# ----------------------------------------------------------------------------- hand-made PDFs

def _build(objects: dict[int, bytes]) -> bytes:
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + objects[num] + b"\nendobj\n"
    xref = len(out)
    n = max(objects) + 1
    out += f"xref\n0 {n}\n0000000000 65535 f \n".encode()
    for i in range(1, n):
        out += (f"{offsets[i]:010d} 00000 n \n" if i in offsets else "0000000000 65535 f \n").encode()
    out += f"trailer\n<< /Size {n} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _stream(data: bytes, extra: str = "") -> bytes:
    return f"<< /Length {len(data)} {extra}>>\nstream\n".encode() + data + b"\nendstream"


_TYPE3 = (b"<< /Type /Font /Subtype /Type3 /FontBBox [0 0 8 8] /FontMatrix [0.1 0 0 0.1 0 0] "
          b"/CharProcs << /a 11 0 R /b 12 0 R /c 13 0 R >> /Encoding << /Type /Encoding /Differences [97 /a /b /c] >> "
          b"/FirstChar 97 /LastChar 99 /Widths [10 10 10] /Resources << >> >>")


@pytest.fixture
def tricky(tmp_path) -> Path:
    """A page with: text in a group, turned text, a filled box and a line, a gradient, a Type 3 font, hidden
    words over a scanned picture, a picture in a group, a highlight, a note with its pop-up, a link, typed-in
    text from another app, and a read-only form box."""
    content = b"""
q /Fm1 Do Q
BT /F1 18 Tf 1 0 0 1 72 700 Tm (Top level line) Tj ET
BT /F1 14 Tf 0 1 -1 0 550 300 Tm (Turned words here) Tj ET
q 0.2 0.4 0.8 rg 72 560 150 40 re f Q
q 1 0 0 RG 3 w 260 560 m 400 600 l S Q
BT /F3 24 Tf 1 0 0 1 72 500 Tm (abc) Tj ET
q 400 470 120 40 re W n /Sh1 sh Q
q 200 0 0 60 300 380 cm /Im1 Do Q
BT 3 Tr /F1 12 Tf 1 0 0 1 305 410 Tm (Scanned words) Tj ET
q /Fm2 Do Q
"""
    w, h = 40, 12
    pix = bytearray([230, 230, 220] * (w * h))
    for y in range(4, 8):
        for x in range(4, 36):
            pix[(y * w + x) * 3:(y * w + x) * 3 + 3] = b"\x20\x20\x20"
    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [30 0 R] /DA (/Helv 0 Tf 0 g) "
           b"/DR << /Font << /Helv 5 0 R >> >> >> >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
           b"/Resources << /Font << /F1 5 0 R /F3 6 0 R >> /XObject << /Fm1 8 0 R /Fm2 9 0 R /Im1 10 0 R >> "
           b"/Shading << /Sh1 14 0 R >> >> /Annots [20 0 R 21 0 R 25 0 R 23 0 R 24 0 R 30 0 R] >>",
        4: _stream(content),
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        6: _TYPE3,
        8: _stream(b"BT /F1 16 Tf 1 0 0 1 72 650 Tm (Text inside a group) Tj ET\n0 0.6 0 rg 72 620 80 20 re f\n",
                   "/Type /XObject /Subtype /Form /BBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> "),
        9: _stream(b"q 60 0 0 40 450 640 cm /Im1 Do Q\n",
                   "/Type /XObject /Subtype /Form /BBox [0 0 612 792] /Resources << /XObject << /Im1 10 0 R >> >> "),
        10: _stream(zlib.compress(bytes(pix)), f"/Type /XObject /Subtype /Image /Width {w} /Height {h} "
                                               f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "),
        11: _stream(b"10 0 0 0 8 8 d1 0 0 8 8 re f"),
        12: _stream(b"10 0 0 0 8 8 d1 0 0 m 8 8 l 8 0 l f"),
        13: _stream(b"10 0 0 0 8 8 d1 4 4 m 8 4 l 4 8 l f"),
        14: b"<< /ShadingType 2 /ColorSpace /DeviceRGB /Coords [400 0 520 0] /Function << /FunctionType 2 "
            b"/Domain [0 1] /C0 [1 0 0] /C1 [0 0 1] /N 1 >> >>",
        20: b"<< /Type /Annot /Subtype /Highlight /Rect [70 695 200 720] /QuadPoints [70 720 200 720 70 695 200 695] "
            b"/C [1 1 0] /Contents (old highlight) >>",
        21: b"<< /Type /Annot /Subtype /Text /Rect [500 740 520 760] /Contents (An old note) /C [1 0.8 0] "
            b"/Popup 25 0 R >>",
        25: b"<< /Type /Annot /Subtype /Popup /Rect [520 660 640 760] /Parent 21 0 R >>",
        23: b"<< /Type /Annot /Subtype /FreeText /Rect [72 300 300 330] /Contents (Old typed comment) "
            b"/DA (/Helv 12 Tf 0 0 1 rg) >>",
        24: b"<< /Type /Annot /Subtype /Link /Rect [72 260 200 280] /Border [0 0 0] "
            b"/A << /S /URI /URI (https://example.com/old) >> >>",
        30: b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (locked) /Ff 1 /V (Fixed value) /Rect [300 200 500 220] "
            b"/DA (/Helv 12 Tf 0 g) /P 3 0 R >>",
    }
    out = tmp_path / "tricky.pdf"
    out.write_bytes(_build(objs))
    return out


def _ctx(tmp_path: Path, **params) -> Ctx:
    options = Options()
    options.output_folder = tmp_path / "out"
    options.output_folder.mkdir(exist_ok=True)
    return Ctx(options, lambda f: None, threading.Event(), params)


def _layout(path: Path, index: int = 0) -> dict:
    doc = pdfium.PdfDocument(str(path))
    try:
        with pdfedit.PDFIUM:
            return pdfedit.read_layout(doc, index)
    finally:
        doc.close()


def _picture(path_or_bytes, scale: float = 1.0):
    doc = pdfium.PdfDocument(str(path_or_bytes) if isinstance(path_or_bytes, Path) else path_or_bytes)
    try:
        return doc[0].render(scale=scale, may_draw_forms=False).to_pil().convert("RGB")
    finally:
        doc.close()


def _save(src: Path, tmp_path: Path, edits: list[dict], **params) -> Path:
    return pdfedit.save_edits(src, _ctx(tmp_path, edits=edits, **params)).outputs[0]


def _same(a, b, box) -> bool:
    """Two pictures of a page are the same inside box (left, top, right, bottom in points at scale 1)."""
    return ImageChops.difference(a.crop(box), b.crop(box)).getbbox() is None


def _line(lay: dict, words: str) -> dict:
    return next(d for d in lay["lines"] if d["text"] == words)


# ----------------------------------------------------------------------------- what's found on the page

def test_everything_on_the_page_is_found(tricky):
    lay = _layout(tricky)
    inside = _line(lay, "Text inside a group")
    assert inside["objs"] == ["0/0"]  # (the first piece of the page's first piece, a group)
    assert inside["rect"][0] == pytest.approx(72, abs=1)
    turned = _line(lay, "Turned words here")
    assert turned["turn"] == pytest.approx(-90, abs=0.5) and turned["origin"] == pytest.approx([550, 492], abs=1)
    hidden = _line(lay, "Scanned words")
    assert hidden["hidden"]
    assert {d["obj"] for d in lay["images"]} == {7, "9/0"}
    shapes = {d["kind"] for d in lay["shapes"]}
    assert shapes == {"path", "gradient"}
    blue = next(d for d in lay["shapes"] if d.get("fill") == "#3366cc")
    assert blue["rect"] == pytest.approx([72, 192, 150, 40], abs=0.5)
    red = next(d for d in lay["shapes"] if d.get("color") == "#ff0000")
    assert red["width"] == pytest.approx(3)
    green = next(d for d in lay["shapes"] if d.get("fill") == "#009900")
    assert green["obj"] == "0/1"
    kinds = {d["kind"]: d for d in lay["annots"]}
    assert set(kinds) == {"highlight", "note", "freetext", "link"}  # (not the pop-up, not the form box)
    assert kinds["note"]["text"] == "An old note" and kinds["note"]["obj"] == "a1"
    assert kinds["link"]["url"] == "https://example.com/old"


def test_a_read_only_form_box_can_be_filled(tricky, tmp_path):
    fields = pdfedit.read_fields(PdfReader(str(tricky)))
    assert fields and fields[0]["name"] == "locked" and fields[0]["locked"]
    out = _save(tricky, tmp_path, [], fields={"locked": "New value"})
    assert PdfReader(str(out)).get_fields()["locked"]["/V"] == "New value"


# ----------------------------------------------------------------------------- changing one thing, keeping the rest

def test_deleting_a_shape_keeps_gradients_and_type3_text(tricky, tmp_path):
    before = _picture(tricky)
    blue = next(d for d in _layout(tricky)["shapes"] if d.get("fill") == "#3366cc")
    out = _save(tricky, tmp_path, [{"kind": "remove", "page": 0, "objs": [blue["obj"]]}])
    after = _picture(out)
    assert after.getpixel((140, 210)) == (255, 255, 255)  # (the box is gone)
    assert _same(before, after, (390, 270, 530, 330))     # (the gradient is still there)
    assert _same(before, after, (60, 270, 150, 300))      # (and the Type 3 letters)
    assert _same(before, after, (60, 60, 300, 180))       # (and the text and the group)
    assert after.getpixel((420, 300)) != (0, 0, 0)        # (not a black box instead of the gradient)


def test_text_inside_a_group_is_changed_and_the_rest_of_the_group_stays(tricky, tmp_path):
    before = _picture(tricky)
    lay = _layout(tricky)
    inside = _line(lay, "Text inside a group")
    x, y, w, h = inside["rect"]
    out = _save(tricky, tmp_path, [{"kind": "text", "page": 0, "text": "Changed words", "x": x, "y": y - 2,
                                    "size": 16, "font": "DejaVu Sans", "color": "#000000",
                                    "replaces": inside["objs"]}])
    text = " ".join(PdfReader(str(out)).pages[0].extract_text().split())
    assert "Changed words" in text and "Text inside a group" not in text
    after = _picture(out)
    assert _same(before, after, (70, 150, 160, 175))  # (the group's green box)
    assert _same(before, after, (440, 105, 520, 160))  # (the other group's picture)
    assert _same(before, after, (60, 270, 150, 300))


def test_a_picture_inside_a_group_moves(tricky, tmp_path):
    out = _save(tricky, tmp_path, [{"kind": "move", "page": 0, "objs": ["9/0"], "orig": [450, 112, 60, 40],
                                    "x": 450, "y": 40, "w": 60, "h": 40}])
    lay = _layout(out)
    rects = [d["rect"] for d in lay["images"]]
    assert any(r == pytest.approx([450, 40, 60, 40], abs=0.5) for r in rects)
    assert not any(r == pytest.approx([450, 112, 60, 40], abs=0.5) for r in rects)


def test_a_shape_gets_another_colour(tricky, tmp_path):
    lay = _layout(tricky)
    red = next(d for d in lay["shapes"] if d.get("color") == "#ff0000")
    green = next(d for d in lay["shapes"] if d.get("fill") == "#009900")
    out = _save(tricky, tmp_path, [{"kind": "restyle", "page": 0, "objs": [red["obj"]], "color": "#00aa00"},
                                   {"kind": "restyle", "page": 0, "objs": [green["obj"]], "fill": "#ff00ff"}])
    after = _layout(out)
    assert any(d.get("color") == "#00aa00" for d in after["shapes"])
    assert any(d.get("fill") == "#ff00ff" for d in after["shapes"])
    assert not any(d.get("color") == "#ff0000" for d in after["shapes"])


def test_a_turned_line_is_changed_and_stays_turned(qapp, tricky, tmp_path):
    lay = _layout(tricky)
    turned = _line(lay, "Turned words here")
    ox, oy = turned["origin"]
    pivot = [pdfedit.TEXT_MARGIN, 14.0]
    out = _save(tricky, tmp_path, [{"kind": "text", "page": 0, "text": "New turned", "size": 14,
                                    "font": "DejaVu Sans", "color": "#000000", "turn": turned["turn"],
                                    "pivot": pivot, "x": ox - pivot[0], "y": oy - pivot[1],
                                    "replaces": turned["objs"]}])
    again = _layout(out)
    new = _line(again, "New turned")
    assert new["turn"] == pytest.approx(-90, abs=1)
    assert new["origin"][0] == pytest.approx(ox, abs=2.5) and new["origin"][1] == pytest.approx(oy, abs=2.5)
    assert not any(d["text"] == "Turned words here" for d in again["lines"])


def test_hidden_words_over_a_scan_are_painted_over(qapp, tricky, tmp_path):
    lay = _layout(tricky)
    hidden = _line(lay, "Scanned words")
    x, y, w, h = hidden["rect"]
    out = _save(tricky, tmp_path, [{"kind": "text", "page": 0, "text": "New words", "x": x, "y": y - 2, "size": 12,
                                    "font": "DejaVu Sans", "color": "#202020", "replaces": hidden["objs"],
                                    "patch": [x - 1, y - 1, w + 2, h + 2, "#e6e6dc"]}])
    text = " ".join(PdfReader(str(out)).pages[0].extract_text().split())
    assert "New words" in text and "Scanned words" not in text
    after = _picture(out, 2)
    # (the scan's dark bar is painted over in the paper's colour where the new words aren't)
    px = after.getpixel((round((x + w - 3) * 2), round((y + h / 2) * 2)))
    assert px[0] > 180


# ----------------------------------------------------------------------------- annotations

def test_annotations_move_change_and_go(tricky, tmp_path):
    out = _save(tricky, tmp_path, [
        {"kind": "move", "page": 0, "objs": ["a0"], "orig": [70, 72, 130, 25], "x": 70, "y": 500, "w": 130, "h": 25},
        {"kind": "restyle", "page": 0, "objs": ["a4"], "url": "example.org/new"},
        {"kind": "remove", "page": 0, "objs": ["a1"]},
        {"kind": "restyle", "page": 0, "objs": ["a3"], "text": "Typed again"},
    ])
    annots = [a.get_object() for a in PdfReader(str(out)).pages[0]["/Annots"]]
    subs = [str(a["/Subtype"]) for a in annots]
    assert "/Text" not in subs and "/Popup" not in subs  # (the note went with its pop-up)
    hi = next(a for a in annots if a["/Subtype"] == "/Highlight")
    assert [float(v) for v in hi["/Rect"]] == pytest.approx([70, 267, 200, 292])
    assert [float(v) for v in hi["/QuadPoints"]] == pytest.approx([70, 292, 200, 292, 70, 267, 200, 267])
    link = next(a for a in annots if a["/Subtype"] == "/Link")
    assert link["/A"]["/URI"] == "https://example.org/new"
    assert next(a for a in annots if a["/Subtype"] == "/FreeText")["/Contents"] == "Typed again"
    assert "/Widget" in subs


def test_a_link_can_go_to_a_page(tricky, tmp_path):
    out = _save(tricky, tmp_path, [{"kind": "restyle", "page": 0, "objs": ["a4"], "target": 0, "url": ""}])
    reader = PdfReader(str(out))
    link = next(a.get_object() for a in reader.pages[0]["/Annots"] if a.get_object()["/Subtype"] == "/Link")
    assert "/A" not in link and link["/Dest"][0].get_object() == reader.pages[0].get_object()


def test_a_link_that_starts_a_program_isnt_made(tricky, tmp_path):
    out = _save(tricky, tmp_path, [{"kind": "restyle", "page": 0, "objs": ["a4"], "url": "file:///C:/x.exe"}])
    link = next(a.get_object() for a in PdfReader(str(out)).pages[0]["/Annots"]
                if a.get_object()["/Subtype"] == "/Link")
    assert link["/A"]["/URI"] == "https://example.com/old"


# ----------------------------------------------------------------------------- the editor's own picture

def test_the_editors_picture_hides_and_recolours(tricky):
    lay = _layout(tricky)
    red = next(d for d in lay["shapes"] if d.get("color") == "#ff0000")
    doc = pdfium.PdfDocument(str(tricky))
    try:
        with pdfedit.PDFIUM:
            plain = pdfedit.render(doc, 0, 1.0)
            changed = pdfedit.render(doc, 0, 1.0, hide=("0/0", "a1"), paint={red["obj"]: {"color": "#00ff00"}})
            again = pdfedit.render(doc, 0, 1.0)
    finally:
        doc.close()
    from PIL import Image
    a, b, c = (Image.frombuffer("RGBX", (r[1], r[2]), r[0], "raw", "RGBX", r[3], 1).convert("RGB")
               for r in (plain, changed, again))
    assert a.tobytes() == c.tobytes()  # (the PDF itself isn't changed)
    assert ImageChops.difference(a, b).crop((70, 128, 210, 146)).getbbox() is not None  # (the group's words)
    assert ImageChops.difference(a, b).crop((498, 30, 522, 54)).getbbox() is not None   # (the note)
    g = b.getpixel((330, 211))
    assert g[1] > 200 and g[0] < 80


# ----------------------------------------------------------------------------- words read from a picture

class _Word:
    def __init__(self, text, x, y, w, h):
        self.text, self.x, self.y, self.w, self.h = text, x, y, w, h


class _OcrLine:
    def __init__(self, words, rtl=False):
        self.words, self.rtl, self.angle = words, rtl, 0.0

    @property
    def text(self):
        return " ".join(w.text for w in self.words)


def test_a_font_without_letters_gets_the_words_read(tmp_path):
    font = (b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding << /Type /Encoding "
            b"/Differences [65 /g1 /g2 /g3 /g4 /g5 /g6 /g7] >> >>")
    objs = {1: b"<< /Type /Catalog /Pages 2 0 R >>", 2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
            4: _stream(b"BT /F1 0 Tf 20 TL /F1 16 Tf 1 0 0 1 20 100 Tm <0102030405> Tj ET"), 5: font}
    # (letter codes with no name PDFium knows: they come out as nothing it can name)
    src = tmp_path / "noletters.pdf"
    src.write_bytes(_build(objs))
    lay = _layout(src)
    assert lay["lines"] and lay["lines"][0]["unknown"]
    x, y, w, h = lay["lines"][0]["rect"]
    read = pdfedit.ocr_lines([_OcrLine([_Word("Hello", x * 4, (y - 13) * 4, 40 * 4, 16 * 4)])], 4.0)
    assert pdfedit.with_read_words(lay, read) == []
    assert lay["lines"][0]["read"] == "Hello"


def test_a_scans_words_become_lines_to_change():
    lay = {"size": (600, 800), "lines": [], "images": [{"obj": 0, "rect": [0, 0, 600, 800]}]}
    assert pdfedit.needs_reading(lay)
    read = pdfedit.ocr_lines([_OcrLine([_Word("Total", 400, 400, 200, 60), _Word("500", 640, 400, 120, 60)]),
                              _OcrLine([_Word("مرحبا", 400, 800, 200, 60)], rtl=True)], 4.0)
    made = pdfedit.with_read_words(lay, read)
    assert [d["text"] for d in made] == ["Total 500", "مرحبا"]
    assert made[0]["scan"] and made[0]["rect"] == pytest.approx([100, 100, 90, 15])
    assert made[1]["rtl"] and made[0]["objs"] != made[1]["objs"]
    assert not pdfedit.needs_reading({"size": (600, 800), "lines": [{"text": "x", "rect": [0, 0, 5, 5]}],
                                      "images": lay["images"]})


# ----------------------------------------------------------------------------- the page's content, cut up

def test_cutting_a_page_draws_it_the_same(tricky, tmp_path):
    writer = PdfWriter(clone_from=PdfReader(str(tricky)))
    assert pdfcontent.cut_page(writer, writer.pages[0])
    assert len(writer.pages[0]["/Contents"]) > 5
    out = tmp_path / "cut.pdf"
    writer.write(str(out))
    assert _picture(tricky, 2).tobytes() == _picture(out, 2).tobytes()


def test_settings_are_set_again_after_each_piece():
    from pypdf.generic import FloatObject, NameObject, TextStringObject
    ops = [([], b"BT"), ([NameObject("/F1"), FloatObject(12)], b"Tf"), ([FloatObject(0)], b"g"),
           ([FloatObject(72), FloatObject(700)], b"Td"), ([TextStringObject("Hello")], b"Tj"), ([], b"ET"),
           ([], b"BT"), ([FloatObject(72), FloatObject(680)], b"Td"), ([TextStringObject("World")], b"Tj"), ([], b"ET")]
    streams = pdfcontent.cut(ops)
    assert streams is not None
    flat = [op for part in streams for _, op in part]
    assert flat.count(b"Tj") == 2
    # (the font set inside the first text is set again before the second one, in case PDFium writes the first
    # again without it)
    first = next(n for n, part in enumerate(streams) if any(op == b"Tj" for _, op in part))
    assert any(op == b"Tf" for _, op in streams[first + 1]) and not any(op == b"Tj" for _, op in streams[first + 1])


def test_a_see_through_group_isnt_put_into_the_page(tmp_path):
    objs = {1: b"<< /Type /Catalog /Pages 2 0 R >>", 2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents 4 0 R "
               b"/Resources << /XObject << /Fm1 5 0 R >> /ExtGState << /G1 << /ca 0.5 >> >> >> >>",
            4: _stream(b"q /G1 gs /Fm1 Do Q"),
            5: _stream(b"1 0 0 rg 0 0 50 50 re f 0 0 1 rg 25 25 50 50 re f",
                       "/Type /XObject /Subtype /Form /BBox [0 0 100 100] ")}
    src = tmp_path / "seethrough.pdf"
    src.write_bytes(_build(objs))
    writer = PdfWriter(clone_from=PdfReader(str(src)))
    assert not pdfcontent.inline_groups(writer, writer.pages[0], {0})
    # (and a piece in it is still taken out: PDFium takes it out of its group instead)
    before = _picture(src, 2)
    out = _save(src, tmp_path, [{"kind": "remove", "page": 0, "objs": ["0/1"]}])
    after = _picture(out, 2)
    assert after.getpixel((120, 460)) == (255, 255, 255)  # (the blue box is gone…)
    assert after.getpixel((20, 580)) == pytest.approx(before.getpixel((20, 580)), abs=3)  # (…the red one is as it was)


def test_a_group_put_into_the_page_draws_the_same(tmp_path):
    objs = {1: b"<< /Type /Catalog /Pages 2 0 R >>", 2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents 4 0 R "
               b"/Resources << /Font << /F1 6 0 R >> /XObject << /Fm1 5 0 R >> >> >>",
            4: _stream(b"q 0 0 100 100 re W n q 2 0 0 2 10 10 cm /Fm1 Do Q Q 1 0 0 rg 200 200 50 50 re f"),
            5: _stream(b"0 0 1 rg 0 0 80 80 re f BT /F1 10 Tf 5 5 Td (Inside) Tj ET /Fm2 Do",
                       "/Type /XObject /Subtype /Form /BBox [0 0 30 30] /Resources << /Font << /F1 6 0 R >> "
                       "/XObject << /Fm2 7 0 R >> >> "),
            6: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            7: _stream(b"0 1 0 rg 10 10 10 10 re f", "/Type /XObject /Subtype /Form /BBox [0 0 100 100] "
                                                    "/Matrix [1 0 0 1 2 2] ")}
    src = tmp_path / "groups.pdf"
    src.write_bytes(_build(objs))
    writer = PdfWriter(clone_from=PdfReader(str(src)))
    assert pdfcontent.inline_groups(writer, writer.pages[0], {0})
    out = tmp_path / "inlined.pdf"
    writer.write(str(out))
    assert _picture(src, 2).tobytes() == _picture(out, 2).tobytes()
    # a piece two groups deep is deleted, and the rest is drawn as it was
    before = _picture(src, 2)
    saved = _save(src, tmp_path, [{"kind": "remove", "page": 0, "objs": ["0/2/0"]}])
    after = _picture(saved, 2)
    assert ImageChops.difference(before, after).getbbox() is not None
    assert _same(before, after, (0, 0, 600, 400))  # (the red box, far from it)


# ----------------------------------------------------------------------------- in the editor

def _open(qtbot, src: Path):
    from corefold import pdfeditor
    ed = pdfeditor.PdfEditor(MagicMock())
    ed.resize(1100, 800)
    ed.show()
    ed.open(src)
    qtbot.waitUntil(lambda: bool(ed.pages) and ed.pages[0].layout is not None, timeout=10000)
    return ed


def test_the_editor_offers_everything(qtbot, tricky):
    ed = _open(qtbot, tricky)
    try:
        page = ed.pages[0]
        kinds = {p.kind for p in page.pieces}
        assert kinds == {"line", "image", "shape", "annot"}
        ed.set_tool("edit")
        assert all(bool(p.acceptedMouseButtons()) is (p.kind in ("line", "image")) for p in page.pieces)
        ed.set_tool("select")
        assert all(p.acceptedMouseButtons() for p in page.pieces)
        # a shape gets another colour, and the page is drawn with it
        red = next(p for p in page.pieces if p.kind == "shape" and p.d.get("color") == "#ff0000")
        ed.scene.clearSelection()
        red.setSelected(True)
        assert ed.style_key() == "drawn" and ed.color_section.isVisibleTo(ed)
        ed.set_style(color="#2e7d32")
        assert ed.restyled == [{"page": 0, "objs": list(red.objs), "color": "#2e7d32"}]
        assert ed.painted(0) == ((red.objs[0], "#2e7d32", ""),)
        # an old link is sent somewhere else
        link = next(p for p in page.pieces if p.kind == "annot" and p.d["kind"] == "link")
        ed.scene.clearSelection()
        link.setSelected(True)
        assert ed.style_key() == "pdflink" and ed.link_section.isVisibleTo(ed)
        assert ed.link_box.text() == "https://example.com/old"
        ed.link_box.setText("example.org")
        ed._link_typed()
        assert ed.piece_look(link)["url"] == "example.org"
        # the old note is deleted
        note = next(p for p in page.pieces if p.kind == "annot" and p.d["kind"] == "note")
        ed.scene.clearSelection()
        note.setSelected(True)
        ed.delete_selected()
        assert "a1" in ed.hidden(0)
        # typed-in text from another app becomes text that can be changed
        free = next(p for p in page.pieces if p.kind == "annot" and p.d["kind"] == "freetext")
        ed.open_annot(free)
        typing = ed.typing()
        assert typing is not None and typing.text() == "Old typed comment" and typing.d["replaces"] == ["a3"]
        typing.document().setPlainText("New comment")
        typing.end_edit()
        # a turned line opens turned
        turned = next(p for p in page.pieces if p.kind == "line" and p.d.get("turn") is not None)
        item = ed.line_to_text(turned)
        assert item.rotation() == pytest.approx(-90, abs=0.5)
        origin = item.mapToParent(QPointF(*item.d["pivot"]))
        assert (origin.x(), origin.y()) == pytest.approx(tuple(turned.d["origin"]), abs=0.6)
        # hidden words over the scan paint over the old ones
        hidden = next(p for p in page.pieces if p.kind == "line" and p.d.get("hidden"))
        item = ed.line_to_text(hidden)
        assert item.d["patch"][:4] == pytest.approx([hidden.d["rect"][0] - 1, hidden.d["rect"][1] - 1,
                                                     hidden.d["rect"][2] + 2, hidden.d["rect"][3] + 2])
        ed.commit()
        state = ed.model()
        assert state["restyled"] and any(d["objs"] == ["a1"] for d in state["removed"])
        # undo and redo keep the colours and links
        ed.undo()
        ed.redo()
        assert ed.model() == state
    finally:
        ed.close_renderer()
        ed.close()


def test_a_scan_is_read_with_edit_text(qtbot, tmp_path, monkeypatch):
    from PIL import Image
    pic = tmp_path / "scan.png"
    img = Image.new("RGB", (600, 800), (250, 250, 245))
    for x in range(100, 300):
        for y in range(100, 112):
            img.putpixel((x, y), (20, 20, 20))
    img.save(pic)
    src = tmp_path / "scan.pdf"
    img.save(src, "PDF", resolution=72)
    from corefold import extras, pdfeditor
    monkeypatch.setattr(pdfeditor.PdfEditor, "can_read", lambda self: True)
    words = [_OcrLine([_Word("Old", 100 * 300 / 72, 100 * 300 / 72, 90 * 300 / 72, 12 * 300 / 72),
                       _Word("words", 200 * 300 / 72, 100 * 300 / 72, 100 * 300 / 72, 12 * 300 / 72)])]
    monkeypatch.setattr(extras, "read_lines", lambda pic, enlarge=True: words)
    ed = _open(qtbot, src)
    try:
        ed.set_tool("edit")
        page = ed.pages[0]
        qtbot.waitUntil(lambda: any(p.d.get("scan") for p in page.pieces), timeout=10000)
        line = next(p for p in page.pieces if p.d.get("scan"))
        assert line.d["text"] == "Old words"
        item = ed.line_to_text(line)
        assert item.d["patch"][4] != "#000000" and item.d["replaces"] == list(line.objs)
    finally:
        ed.close_renderer()
        ed.close()



