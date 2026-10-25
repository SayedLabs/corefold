"""Corefold PDF: what its tools write (forms, bookmarks, deleted pages, passwords, page boxes, pictures) and
a few things the editor must keep straight (undo of a moved piece, the arrows, unsaved work)."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject,
                           NumberObject, RectangleObject, TextStringObject)

from corefold import core, pdfedit, pdfstamp, pdftools


class _Options:
    rename_base = ""
    rename_start = 1

    def __init__(self, folder: Path):
        self.output_folder = folder


def _ctx(tmp_path: Path, **params) -> core.Ctx:
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return core.Ctx(_Options(out), params=params)


def _font(w):
    return w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                           NameObject("/Subtype"): NameObject("/Type1"),
                                           NameObject("/BaseFont"): NameObject("/Helvetica")}))


def _pdf(path: Path, pages: int = 3, size=(300, 400), form: bool = False, choice: bool = False) -> Path:
    """Pages with the words PAGE<n>SECRET on them, a bookmark to the second page, and maybe a form."""
    w = PdfWriter()
    font = _font(w)
    for i in range(pages):
        p = w.add_blank_page(*size)
        s = DecodedStreamObject()
        s.set_data(f"BT /F1 12 Tf 10 100 Td (PAGE{i}SECRET) Tj ET".encode())
        p[NameObject("/Contents")] = w._add_object(s)
        p[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject(
            {NameObject("/F1"): font})})
    if pages > 1:
        w.add_outline_item("Second", 1)
    if form or choice:
        box = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/T"): TextStringObject("name"), NameObject("/P"): w.pages[0].indirect_reference,
            NameObject("/Rect"): ArrayObject([FloatObject(10), FloatObject(300), FloatObject(200), FloatObject(340)]),
            NameObject("/DA"): TextStringObject("/Helv 20 Tf 0 g")})
        if choice:
            box[NameObject("/FT")] = NameObject("/Ch")
            box[NameObject("/Ff")] = NumberObject(1 << 17)  # (a drop-down list)
            box[NameObject("/Opt")] = ArrayObject([ArrayObject([TextStringObject("US"),
                                                                TextStringObject("United States")]),
                                                   ArrayObject([TextStringObject("FR"), TextStringObject("France")])])
            box[NameObject("/V")] = TextStringObject("US")
        else:
            box[NameObject("/FT")] = NameObject("/Tx")
        ref = w._add_object(box)
        w.pages[0][NameObject("/Annots")] = ArrayObject([ref])
        w._root_object[NameObject("/AcroForm")] = DictionaryObject({
            NameObject("/Fields"): ArrayObject([ref]), NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g"),
            NameObject("/DR"): DictionaryObject({NameObject("/Font"): DictionaryObject(
                {NameObject("/Helv"): _font(w)})})})
        if form:
            w.update_page_form_field_values(w.pages[0], {"name": "HELLO"}, auto_regenerate=False)
            w.set_need_appearances_writer(False)
    w.write(path)
    return path


def _secrets(path: Path) -> set[str]:
    """The PAGE<n>SECRET words anywhere in the file, even on pages no longer in it."""
    import re
    import zlib
    found = set()
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", path.read_bytes(), re.S):
        data = m.group(1)
        try:
            data = zlib.decompress(data)
        except zlib.error:
            pass
        found.update(x.decode() for x in re.findall(rb"PAGE\dSECRET", data))
    return found


# ----------------------------------------------------------------------------- the board's tools (pypdf, pdfium)

def test_arranged_pages_keep_the_form_and_bookmarks(tmp_path):
    src = _pdf(tmp_path / "a.pdf", form=True)
    res = pdftools.save_arranged([src], _ctx(tmp_path, pages=[(0, 1, 0), (0, 0, 90), (0, 2, 0)], how="one",
                                               name="arranged"))
    r = PdfReader(res.outputs[0])
    assert [p.extract_text().strip() for p in r.pages] == ["PAGE1SECRET", "PAGE0SECRET", "PAGE2SECRET"]
    assert [p.rotation for p in r.pages] == [0, 90, 0]
    assert "name" in (r.get_fields() or {})
    assert [(o.title, r.get_destination_page_number(o)) for o in r.outline] == [("Second", 0)]


def test_merge_of_two_pdfs_in_mixed_order(tmp_path):
    a, b = _pdf(tmp_path / "a.pdf", pages=2), _pdf(tmp_path / "b.pdf", pages=2)
    res = pdftools.save_arranged([a, b], _ctx(tmp_path, pages=[(1, 0, 0), (0, 1, 0), (1, 1, 0), (0, 0, 0)],
                                                how="one", name="merged"))
    r = PdfReader(res.outputs[0])
    assert [p.extract_text().strip() for p in r.pages] == ["PAGE0SECRET", "PAGE1SECRET", "PAGE1SECRET",
                                                          "PAGE0SECRET"]


def test_pages_of_a_locked_pdf_stay_locked(tmp_path):
    plain = _pdf(tmp_path / "plain.pdf")
    w = PdfWriter(clone_from=PdfReader(plain))
    w.encrypt(user_password="pw", owner_password="pw", algorithm="AES-256")
    locked = tmp_path / "locked.pdf"
    w.write(locked)
    res = pdftools.save_arranged([locked], _ctx(tmp_path, pages=[(0, 2, 0), (0, 0, 0)], how="one", name="x",
                                                  passwords={str(locked): "pw"}))
    r = PdfReader(res.outputs[0])
    assert r.is_encrypted and r.decrypt("pw") and len(r.pages) == 2
    res = pdftools.split(locked, _ctx(tmp_path, password="pw"))
    parts = sorted(res.outputs[0].iterdir())
    assert parts and all(PdfReader(p).is_encrypted for p in parts)


def test_pictures_to_pdf_keeps_a_png_exactly(tmp_path):
    im = Image.new("RGB", (120, 80), "white")
    d = ImageDraw.Draw(im)
    for x in range(0, 120, 3):
        d.line([(x, 0), (x, 80)], fill=(255, 0, 0) if x % 2 else (0, 0, 0))
    im.save(tmp_path / "sharp.png")
    res = pdftools.pictures_to_pdf([tmp_path / "sharp.png"], _ctx(tmp_path, size="a4", margin="small"))
    page = PdfReader(res.outputs[0]).pages[0]
    assert page.images[0].image.convert("RGB").tobytes() == im.tobytes()
    assert round(float(page.mediabox.width)) == 842  # (wider than tall: A4 on its side)


def test_a_jpg_photo_goes_in_as_it_is(tmp_path):
    Image.new("RGB", (64, 48), (10, 120, 200)).save(tmp_path / "photo.jpg", quality=80)
    res = pdftools.pictures_to_pdf([tmp_path / "photo.jpg"], _ctx(tmp_path))
    page = PdfReader(res.outputs[0]).pages[0]
    xo = page["/Resources"]["/XObject"]["/Im0"].get_object()
    assert xo["/Filter"] == "/DCTDecode" and xo._data == (tmp_path / "photo.jpg").read_bytes()


def test_pdf_to_pictures_shows_what_is_typed_in_the_form(tmp_path):
    src = _pdf(tmp_path / "form.pdf", pages=1, form=True)
    res = pdftools.to_pictures(src, _ctx(tmp_path, format="png", dpi=72))
    with Image.open(res.outputs[0]) as im:
        box = im.convert("L").crop((10, 400 - 340, 200, 400 - 300))  # (the form box, from the top)
        assert box.getextrema()[0] < 100  # (its words are drawn)


@pytest.mark.parametrize("rotate", [0, 90])
def test_page_numbers_are_inside_the_cropbox(tmp_path, rotate):
    w = PdfWriter()
    p = w.add_blank_page(600, 800)
    p.cropbox = RectangleObject((50, 100, 550, 700))
    p[NameObject("/Rotate")] = NumberObject(rotate)
    w.write(tmp_path / "c.pdf")
    res = pdfstamp.page_numbers(tmp_path / "c.pdf", _ctx(tmp_path, place="bc"))
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(res.outputs[0]))
    try:
        im = doc[0].render(scale=2).to_pil().convert("L")
    finally:
        doc.close()
    ink = im.point(lambda v: 255 if v < 160 else 0).getbbox()
    assert ink is not None and ink[3] > im.height * 0.8  # (seen, near the bottom of the page as shown)


# ----------------------------------------------------------------------------- the editor's saving (needs Qt)

def test_a_page_deleted_in_the_editor_is_gone_from_the_file(qapp, tmp_path):
    src = _pdf(tmp_path / "s.pdf", form=True)
    res = pdfedit.save_edits(src, _ctx(tmp_path, edits=[], order=[0, 2]))
    out = res.outputs[0]
    assert _secrets(out) == {"PAGE0SECRET", "PAGE2SECRET"}
    assert len(PdfReader(out).pages) == 2


def test_drawings_land_where_they_were_put_on_an_odd_sized_page(qapp, tmp_path):
    src = _pdf(tmp_path / "odd.pdf", pages=1, size=(610, 790))
    edits = [{"kind": "rect", "page": 0, "points": [100, 50, 140, 90], "color": "#ff0000", "width": 1,
              "fill": "#ff0000"}]
    res = pdfedit.save_edits(src, _ctx(tmp_path, edits=edits))
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(res.outputs[0]))
    try:
        im = doc[0].render(scale=1).to_pil().convert("RGB")
    finally:
        doc.close()
    r, g, _ = im.split()
    red = ImageChops.multiply(r.point(lambda v: 255 if v > 200 else 0), g.point(lambda v: 255 if v < 100 else 0))
    assert red.getbbox() == (100, 50, 140, 90)


def test_a_choice_keeps_its_own_value(qapp, tmp_path):
    src = _pdf(tmp_path / "ch.pdf", pages=1, choice=True)
    fields = pdfedit.read_fields(PdfReader(src))
    assert fields[0]["value"] == "United States"
    res = pdfedit.save_edits(src, _ctx(tmp_path, edits=[], fields={"name": "France"}))
    assert PdfReader(res.outputs[0]).get_fields()["name"]["/V"] == "FR"


# ----------------------------------------------------------------------------- the editor itself

@pytest.fixture
def editor(qtbot, tmp_path):
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QHBoxLayout

    from corefold import pdfpage
    from corefold.pdfeditor import PdfEditor
    c = SimpleNamespace(theme=SimpleNamespace(muted="#777777", text="#111111", dark=False), icon=QIcon(),
                        settings=SimpleNamespace(save_mode="beside", output_folder=""), tell=lambda *a: None,
                        start_job=lambda *a, **k: None, show_window=lambda *a: None)
    page = pdfpage.PdfPage(c, QHBoxLayout())
    qtbot.addWidget(page)
    page.editor = PdfEditor(page)
    page.editor.open(_pdf(tmp_path / "e.pdf"))
    qtbot.waitUntil(lambda: bool(page.editor.pages) and page.editor.saved is not None, timeout=5000)
    yield page
    page.close_renderer()


def test_undo_keeps_a_moved_piece_visible(editor):
    from PySide6.QtGui import QPixmap
    ed = editor.editor
    pic = QPixmap(10, 10)
    ed.add_item({"kind": "move", "page": 0, "objs": [0], "orig": [10, 10, 40, 12], "x": 50, "y": 50, "w": 40,
                 "h": 12}, pixmap=pic)
    ed.add_item({"kind": "cover", "page": 0, "x": 0, "y": 0, "w": 20, "h": 20, "color": "#ffffff"})
    ed.undo()
    moved = [i for i in ed.pages[0].childItems() if getattr(i, "kind", "") == "move"]
    assert len(moved) == 1 and moved[0].pixmap is not None


def test_arrows_leave_the_pdfs_own_pieces_alone(editor, qtbot):
    from PySide6.QtCore import Qt

    from corefold.pdfeditor import OldPiece
    ed = editor.editor
    piece = OldPiece(ed, ed.pages[0], "image", {"obj": 0, "rect": [10, 10, 20, 20]})
    piece.setSelected(True)
    qtbot.keyClick(ed.view, Qt.Key_Right)
    assert (piece.pos().x(), piece.pos().y()) == (10, 10)


def test_the_page_tells_about_unsaved_editor_work(editor):
    ed = editor.editor
    assert not editor.unsaved()
    ed.add_item({"kind": "cover", "page": 0, "x": 0, "y": 0, "w": 20, "h": 20, "color": "#ffffff"})
    assert editor.unsaved()
    ed.close_editor(ask=False)
    assert not editor.unsaved()


def test_changes_count_as_saved_only_once_the_save_worked(editor):
    ed = editor.editor
    jobs = []
    ed.c.start_job = lambda tool, files, params, then=None: jobs.append(then)
    ed.add_item({"kind": "cover", "page": 0, "x": 0, "y": 0, "w": 20, "h": 20, "color": "#ffffff"})
    ed.save()
    assert editor.unsaved() and not ed.save_btn.isEnabled()  # (still being written)
    jobs[0](False)  # the save failed (no room, no permission, stopped)
    assert editor.unsaved() and ed.save_btn.isEnabled()
    ed.save()
    jobs[1](True)
    assert not editor.unsaved()
