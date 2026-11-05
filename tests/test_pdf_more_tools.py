"""Rakiza Core PDF's new tools: Flatten (a filled-in form, notes and stamps made part of the page), Repair (a
damaged PDF written again whole), Page size (every page made A4, Letter…, what's on it fitted inside) and Trim
edges (the empty white edges around what's on the pages cut away)."""
import threading
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import FreeText, Link
from pypdf.generic import (ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, RectangleObject,
                           StreamObject, TextStringObject)

from corefold import pdftools
from corefold import settings as settings_mod
from corefold.core import Ctx, Options


def _ctx(tmp_path: Path, **params) -> Ctx:
    options = Options()
    options.output_folder = tmp_path / "out"
    options.output_folder.mkdir(exist_ok=True)
    return Ctx(options, lambda f: None, threading.Event(), params)


def _form(path: Path, filled: str = "Ahmad") -> Path:
    """A one-page form with a text box that has something typed in it (with how it looks) and a note."""
    w = PdfWriter()
    page = w.add_blank_page(400, 400)
    look = StreamObject()
    look._data = f"/Tx BMC BT /Helv 12 Tf 0 g 2 6 Td ({filled}) Tj ET EMC".encode()
    font = w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                           NameObject("/Subtype"): NameObject("/Type1"),
                                           NameObject("/BaseFont"): NameObject("/Helvetica")}))
    look.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                 NameObject("/BBox"): ArrayObject([FloatObject(v) for v in (0, 0, 250, 22)]),
                 NameObject("/Resources"): DictionaryObject({NameObject("/Font"): DictionaryObject(
                     {NameObject("/Helv"): font})})})
    field = w._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject("name"),
        NameObject("/V"): TextStringObject(filled), NameObject("/F"): NumberObject(4),
        NameObject("/Rect"): ArrayObject([FloatObject(v) for v in (50, 330, 300, 352)]),
        NameObject("/DA"): TextStringObject("/Helv 12 Tf 0 g"), NameObject("/P"): page.indirect_reference,
        NameObject("/AP"): DictionaryObject({NameObject("/N"): w._add_object(look)})}))
    page[NameObject("/Annots")] = ArrayObject([field])
    w._root_object[NameObject("/AcroForm")] = DictionaryObject({
        NameObject("/Fields"): ArrayObject([field]), NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g"),
        NameObject("/DR"): DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): font})})})
    w.add_annotation(0, FreeText(text="Checked", rect=(50, 200, 200, 230)))
    w.add_metadata({"/Title": "My form"})
    w.write(str(path))
    return path


def _text(path: Path, password: str = "") -> str:
    doc = pdfium.PdfDocument(str(path), password=password or None)
    try:
        return doc[0].get_textpage().get_text_range()
    finally:
        doc.close()


def test_flatten_makes_the_filled_in_form_part_of_the_page(tmp_path):
    src = _form(tmp_path / "form.pdf")
    assert "Ahmad" not in _text(src)  # (only in the form's box, not on the page)
    result = pdftools.flatten(src, _ctx(tmp_path))
    out = result.outputs[0]
    assert out.name == "form (flattened).pdf"
    reader = PdfReader(str(out))
    assert "/AcroForm" not in reader.trailer["/Root"]
    assert not [a for a in reader.pages[0].get("/Annots") or [] if a.get_object().get("/Subtype") == "/Widget"]
    assert "Ahmad" in _text(out)  # (now written on the page itself)
    assert reader.metadata.title == "My form"


def test_flatten_keeps_the_password(tmp_path):
    src = _form(tmp_path / "form.pdf")
    w = PdfWriter(clone_from=PdfReader(str(src)))
    w.encrypt("pw", "pw", algorithm="AES-256")
    locked = tmp_path / "locked.pdf"
    w.write(str(locked))
    out = pdftools.flatten(locked, _ctx(tmp_path, password="pw")).outputs[0]
    reader = PdfReader(str(out))
    assert reader.is_encrypted and reader.decrypt("pw")
    assert "Ahmad" in _text(out, "pw")


def test_flatten_says_when_theres_nothing_to_flatten(tmp_path):
    w = PdfWriter()
    w.add_blank_page(200, 200)
    src = tmp_path / "plain.pdf"
    w.write(str(src))
    result = pdftools.flatten(src, _ctx(tmp_path))
    assert not result.outputs and "nothing to flatten" in result.note
    assert not list((tmp_path / "out").iterdir())


def test_repair_writes_a_cut_off_pdf_again_whole(tmp_path):
    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(300, 300)
    good = tmp_path / "good.pdf"
    w.write(str(good))
    data = good.read_bytes()
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(data[:data.rindex(b"xref")])  # (its table of where everything is, and the end: gone)
    result = pdftools.repair(broken, _ctx(tmp_path))
    out = result.outputs[0]
    assert out.name == "broken (repaired).pdf"
    reader = PdfReader(str(out), strict=True)
    assert len(reader.pages) == 3
    assert "3" in result.note


def test_repair_refuses_what_isnt_a_pdf_at_all(tmp_path):
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"not a pdf at all" * 10)
    with pytest.raises((RuntimeError, ValueError, OSError)):
        pdftools.repair(junk, _ctx(tmp_path))
    assert not [p for p in (tmp_path / "out").iterdir()]


def test_page_size_fits_every_page_on_a4_and_moves_the_links(tmp_path):
    w = PdfWriter()
    w.add_blank_page(300, 400)    # small, upright
    w.add_blank_page(1000, 500)   # wide: becomes landscape A4
    w.add_annotation(0, Link(rect=(0, 0, 300, 400), url="https://example.com"))
    src = tmp_path / "mixed.pdf"
    w.write(str(src))
    out = pdftools.page_size(src, _ctx(tmp_path, size="a4")).outputs[0]
    assert out.name == "mixed (A4).pdf"
    reader = PdfReader(str(out))
    a, b = reader.pages
    assert (round(float(a.mediabox.width)), round(float(a.mediabox.height))) == (595, 842)
    assert (round(float(b.mediabox.width)), round(float(b.mediabox.height))) == (842, 595)
    rect = [float(v) for v in a["/Annots"][0].get_object()["/Rect"]]
    scale = min(595.28 / 300, 841.89 / 400)
    assert rect[0] == pytest.approx((595.28 - 300 * scale) / 2, abs=0.5)
    assert rect[2] - rect[0] == pytest.approx(300 * scale, abs=0.5)  # (the link still covers the whole page)


def test_page_size_keeps_a_margin_and_a_turned_page(tmp_path):
    w = PdfWriter()
    page = w.add_blank_page(400, 300)
    page.rotate(90)  # (shown upright: 300 wide, 400 tall)
    src = tmp_path / "turned.pdf"
    w.write(str(src))
    out = pdftools.page_size(src, _ctx(tmp_path, size="letter", margin="big")).outputs[0]
    page = PdfReader(str(out)).pages[0]
    assert page.rotation % 360 == 0
    assert (round(float(page.mediabox.width)), round(float(page.mediabox.height))) == (612, 792)


def test_page_size_keeps_what_was_outside_the_page_hidden(tmp_path):
    """A print PDF's crop marks (or what Trim edges hid) sit outside the page's visible box: when the page is
    made A4 they must not show up in the room left around it."""
    w = PdfWriter()
    page = w.add_blank_page(700, 700)
    stream = StreamObject()
    stream._data = b"0 g 0 0 40 700 re f 300 300 50 50 re f"  # (a mark outside the box, and a square inside)
    page[NameObject("/Contents")] = w._add_object(stream)
    page.cropbox = RectangleObject([50, 100, 650, 600])
    src = tmp_path / "print.pdf"
    w.write(str(src))
    out = pdftools.page_size(src, _ctx(tmp_path, size="a4")).outputs[0]
    doc = pdfium.PdfDocument(str(out))
    try:
        img = doc[0].render(scale=1).to_pil().convert("L")
    finally:
        doc.close()
    assert img.size[0] > img.size[1]  # (wide: landscape A4, with room left and right)
    assert img.crop((0, 0, 60, img.height)).getextrema()[0] > 128  # (the mark stays hidden)
    assert img.getextrema()[0] < 128  # (the square inside is still there)


def test_page_size_turns_a_turned_pages_links_and_notes_with_it(tmp_path):
    """A page shown turned (its /Rotate) is turned for real: a link still covers the same spot, and a note looks
    just as it did, in the same place."""
    w = PdfWriter()
    page = w.add_blank_page(400, 300)
    page.rotate(90)  # (shown upright: 300 wide, 400 tall)
    stream = StreamObject()
    stream._data = b"0 g 0 0 60 60 re f"
    page[NameObject("/Contents")] = w._add_object(stream)
    look = StreamObject()
    look._data = b"0 g 0 0 30 80 re f"  # (a tall bar at the left of a wide note)
    look.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                 NameObject("/BBox"): ArrayObject([FloatObject(v) for v in (0, 0, 120, 80)])})
    note = w._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Square"),
        NameObject("/Rect"): ArrayObject([FloatObject(v) for v in (200, 150, 320, 230)]),
        NameObject("/F"): NumberObject(4), NameObject("/AP"): DictionaryObject({NameObject("/N"): w._add_object(look)})}))
    page[NameObject("/Annots")] = ArrayObject([note])
    w.add_annotation(0, Link(rect=(0, 0, 100, 300), url="https://example.com"))  # (the top quarter, as shown)
    src = tmp_path / "turned.pdf"
    w.write(str(src))
    out = pdftools.page_size(src, _ctx(tmp_path, size="letter")).outputs[0]
    page = PdfReader(str(out)).pages[0]
    link = [a.get_object() for a in page["/Annots"] if a.get_object()["/Subtype"] == "/Link"][0]
    scale = min(612 / 300, 792 / 400)
    left = (612 - 300 * scale) / 2
    assert [round(float(v)) for v in link["/Rect"]] == [round(left), round(792 - 100 * scale), round(612 - left), 792]

    def dark(path, crop=None):
        doc = pdfium.PdfDocument(str(path))
        try:
            img = doc[0].render(scale=1).to_pil().convert("L")
        finally:
            doc.close()
        if crop:
            img = img.crop(crop).resize((300, 400))
        return [v < 128 for v in img.tobytes()]
    before = dark(src)
    after = dark(out, (round(left), 0, round(612 - left), 792))
    assert sum(before) == 60 * 60 + 30 * 80
    assert sum(a != b for a, b in zip(before, after)) < 100  # (the page and its note look as they did)


def _boxed(path: Path, pages) -> Path:
    """Pages with a black box drawn at (x, y, w, h) on each (or nothing, for None)."""
    w = PdfWriter()
    for spot in pages:
        page = w.add_blank_page(600, 800)
        if spot:
            x, y, bw, bh = spot
            stream = StreamObject()
            stream._data = f"0 g {x} {y} {bw} {bh} re f".encode()
            page[NameObject("/Contents")] = w._add_object(stream)
    w.write(str(path))
    return path


def test_trim_cuts_the_empty_edges_with_a_little_room(tmp_path):
    src = _boxed(tmp_path / "scan.pdf", [(100, 200, 300, 400), None])
    result = pdftools.trim(src, _ctx(tmp_path, margin="small", same=False))
    out = result.outputs[0]
    assert out.name == "scan (trimmed).pdf" and "1" in result.note
    a, b = PdfReader(str(out)).pages
    box = [float(v) for v in a.cropbox]
    assert box == pytest.approx([100 - 18, 200 - 18, 400 + 18, 600 + 18], abs=2.5)
    assert [float(v) for v in b.cropbox] == [0, 0, 600, 800]  # (an empty page is left as it is)
    assert [float(v) for v in a.mediabox] == [0, 0, 600, 800]  # (only hidden: nothing taken out)


def test_trim_cuts_every_page_the_same_and_keeps_a_turned_page_right(tmp_path):
    src = _boxed(tmp_path / "two.pdf", [(100, 100, 100, 100), (300, 500, 100, 100)])
    out = pdftools.trim(src, _ctx(tmp_path, margin="none", same=True)).outputs[0]
    a, b = PdfReader(str(out)).pages
    assert [float(v) for v in a.cropbox] == pytest.approx([100, 100, 400, 600], abs=2.5)
    assert [float(v) for v in b.cropbox] == pytest.approx([float(v) for v in a.cropbox])
    w = PdfWriter(clone_from=PdfReader(str(_boxed(tmp_path / "t.pdf", [(100, 200, 300, 400)]))))
    w.pages[0].rotate(90)
    turned = tmp_path / "turned.pdf"
    w.write(str(turned))
    page = PdfReader(str(pdftools.trim(turned, _ctx(tmp_path, margin="none")).outputs[0])).pages[0]
    assert [float(v) for v in page.cropbox] == pytest.approx([100, 200, 400, 600], abs=2.5)


def test_trim_says_when_theres_nothing_to_cut(tmp_path):
    src = _boxed(tmp_path / "full.pdf", [(0, 0, 600, 800), None])
    result = pdftools.trim(src, _ctx(tmp_path))
    assert not result.outputs and "no empty edges" in result.note


@pytest.fixture
def window(qtbot):
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from shiboken6 import delete

    from corefold import app as A
    s = settings_mod.Settings.load()
    s.first_run, s.tour_done, s.animations, s.tools_off, s.last_page = False, True, False, [], "Home"
    s.save()
    c = A.Controller(A.QApplication.instance(), QIcon())
    c.apply_tools()
    w = c.window
    w.resize(1040, 700)
    w.show()
    qtbot.waitExposed(w)
    yield w
    c.tray.hide()
    for timer in c.findChildren(QTimer):
        timer.stop()
    if c._window is not None:
        c._window.close()
        delete(c._window)
    delete(c)


def test_the_board_mixes_two_sided_scans_and_reverses(window, tmp_path, qtbot):
    fronts, backs = tmp_path / "fronts.pdf", tmp_path / "backs.pdf"
    for path in (fronts, backs):
        w = PdfWriter()
        for _ in range(3):
            w.add_blank_page(300, 400)
        w.write(str(path))
    window.open_page("PDF")
    pg = window.pdf_page
    pg.open_board([fronts, backs], "merge")
    qtbot.waitUntil(lambda: pg.board.count() == 6, timeout=10000)
    pg._order_menu()
    assert all(a.isEnabled() for a in pg.mix_acts)
    pg.mix(True)  # (the backs were scanned last page first)
    assert pg.plan() == [(0, 0, 0), (1, 2, 0), (0, 1, 0), (1, 1, 0), (0, 2, 0), (1, 0, 0)]
    pg.undo()
    pg.mix(False)
    assert pg.plan() == [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (0, 2, 0), (1, 2, 0)]
    pg.board.clearSelection()
    for r in (0, 1, 2):
        pg.board.item(r).setSelected(True)
    pg.reverse()  # (with pages selected: just those swap places)
    assert pg.plan()[:4] == [(0, 1, 0), (1, 0, 0), (0, 0, 0), (1, 1, 0)]
