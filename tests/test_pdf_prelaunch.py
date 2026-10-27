"""Corefold PDF, checked before the first launch: a booby-trapped PDF can't keep Remove hidden info busy for
ever, bookmarks' and XFA forms' scripts are found and taken out, photos never carry where they were taken
into a PDF, Cover takes out hidden words (a scan made searchable) and the page's old small picture too, a
locked PDF's smaller, numbered, watermarked or signed copy stays locked."""
import io
import threading
import zlib
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject,
                           StreamObject, TextStringObject)

from corefold import converters, pdfedit, pdfstamp, pdftools
from corefold.archives import NeedPassword
from corefold.core import Ctx, Options


def _ctx(tmp_path: Path, **params) -> Ctx:
    options = Options()
    options.output_folder = tmp_path / "out"
    options.output_folder.mkdir(exist_ok=True)
    return Ctx(options, lambda f: None, threading.Event(), params)


def _in_time(fn, seconds: float = 20):
    got = {}

    def go():
        try:
            got["value"] = fn()
        except Exception as e:  # (handed to the test)
            got["error"] = e
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), "still busy: it would never finish"
    if "error" in got:
        raise got["error"]
    return got["value"]


def _blank(path: Path, writer=None) -> Path:
    w = writer or PdfWriter()
    if not len(w.pages):
        w.add_blank_page(300, 400)
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def _action(kind: str, **more) -> DictionaryObject:
    d = DictionaryObject({NameObject("/S"): NameObject(kind)})
    for k, v in more.items():
        d[NameObject("/" + k)] = v
    return d


# ----------------------------------------------------------------------------- Remove hidden info

def test_an_action_that_leads_back_to_itself_is_no_trap(tmp_path):
    w = PdfWriter()
    w.add_blank_page(200, 200)
    act = _action("/GoTo", D=ArrayObject([w.pages[0].indirect_reference, NameObject("/Fit")]))
    ref = w._add_object(act)
    act[NameObject("/Next")] = ref
    w._root_object[NameObject("/OpenAction")] = ref
    src = _blank(tmp_path / "loop.pdf", w)
    found = _in_time(lambda: pdftools.hidden_info(src))
    assert found["scripts"] == 0  # (going to its own first page, again and again: no harm, only a trap)
    out = _in_time(lambda: pdftools.remove_hidden(src, _ctx(tmp_path))).outputs[0]
    assert len(PdfReader(str(out)).pages) == 1


def test_bookmark_and_xfa_scripts_are_found_and_taken_out(tmp_path):
    w = PdfWriter()
    w.add_blank_page(200, 200)
    mark = w.add_outline_item("Click me", 0).get_object()
    mark[NameObject("/A")] = w._add_object(_action("/JavaScript", JS=TextStringObject("app.alert('BOOKMARKJS')")))
    w.add_outline_item("Plain", 0)
    xfa = DecodedStreamObject()
    xfa.set_data(b"<xdp:xdp><template><script>XFASCRIPT()</script></template></xdp:xdp>")
    w._root_object[NameObject("/AcroForm")] = DictionaryObject({NameObject("/Fields"): ArrayObject(),
                                                                NameObject("/XFA"): w._add_object(xfa)})
    src = _blank(tmp_path / "scripts.pdf", w)
    assert pdftools.hidden_info(src)["scripts"] == 2
    out = pdftools.remove_hidden(src, _ctx(tmp_path, remove=["scripts"])).outputs[0]
    data = out.read_bytes()
    assert b"BOOKMARKJS" not in data and b"XFASCRIPT" not in data
    titles = [o.title for o in PdfReader(str(out)).outline]
    assert titles == ["Click me", "Plain"]  # (the bookmarks themselves stay)
    assert pdftools.hidden_info(out)["scripts"] == 0


@pytest.mark.parametrize("action", ["/URI", "/GoToR", "/Launch"])
def test_what_opens_by_itself_goes_and_going_to_a_page_stays(tmp_path, action):
    w = PdfWriter()
    w.add_blank_page(200, 200)
    w._root_object[NameObject("/OpenAction")] = w._add_object(
        _action(action, URI=TextStringObject("https://tracker.example/open"), F=TextStringObject("\\\\host\\share\\x.pdf")))
    src = _blank(tmp_path / "opens.pdf", w)
    assert pdftools.hidden_info(src)["scripts"] == 1
    out = pdftools.remove_hidden(src, _ctx(tmp_path, remove=["scripts"])).outputs[0]
    assert "/OpenAction" not in PdfReader(str(out)).trailer["/Root"]
    w = PdfWriter()
    w.add_blank_page(200, 200)
    w._root_object[NameObject("/OpenAction")] = ArrayObject([w.pages[0].indirect_reference, NameObject("/Fit")])
    src = _blank(tmp_path / "page.pdf", w)
    out = pdftools.remove_hidden(src, _ctx(tmp_path, remove=["scripts"])).outputs[0]
    assert "/OpenAction" in PdfReader(str(out)).trailer["/Root"]


# ----------------------------------------------------------------------------- photos' details

def _photo(path: Path, **save) -> Path:
    im = Image.new("RGB", (320, 240), (10, 120, 200))
    for x in range(0, 320, 16):
        im.paste((240, 200, 30), (x, 0, x + 8, 240))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMakerXYZ"
    exif[0x0110] = "ModelSecret"
    exif[0x8825] = {1: "N", 2: (24.0, 42.0, 30.0), 3: "E", 4: (46.0, 40.0, 10.0)}
    im.save(path, "JPEG", quality=90, exif=exif.tobytes(), **save)
    return path


def test_a_photos_details_go_and_its_picture_and_colours_stay(tmp_path):
    from PIL import ImageCms
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    src = _photo(tmp_path / "photo.jpg", icc_profile=icc)
    data = src.read_bytes()
    plain = pdftools.jpeg_without_details(data)
    assert b"ModelSecret" in data and b"ModelSecret" not in plain and b"Exif" not in plain
    a, b = Image.open(io.BytesIO(data)), Image.open(io.BytesIO(plain))
    assert a.tobytes() == b.tobytes()  # (the very same picture: nothing decoded and packed again)
    assert b.info.get("icc_profile") == icc
    assert pdftools.jpeg_without_details(b"not a jpg") == b"not a jpg"
    assert pdftools.jpeg_without_details(data[:40]) == data[:40]  # (cut short: left as it was)


def test_pictures_to_pdf_never_carries_where_a_photo_was_taken(tmp_path):
    src = _photo(tmp_path / "photo.jpg")
    out = pdftools.pictures_to_pdf([src], _ctx(tmp_path)).outputs[0]
    assert b"ModelSecret" not in out.read_bytes()
    out = tmp_path / "out" / "converter.pdf"
    converters.jpegs_to_pdf(converters._plain_jpegs([src]), out, 150, _ctx(tmp_path))
    assert b"ModelSecret" not in out.read_bytes()
    assert len(PdfReader(str(out)).pages) == 1


def test_remove_hidden_info_takes_the_details_out_of_photos_inside(tmp_path):
    data = _photo(tmp_path / "photo.jpg").read_bytes()
    w = PdfWriter()
    page = w.add_blank_page(320, 240)
    pic = StreamObject()
    pic._data = data
    pic.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(320), NameObject("/Height"): NumberObject(240),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8),
                NameObject("/Filter"): NameObject("/DCTDecode")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject(
        {NameObject("/Im0"): w._add_object(pic)})})
    content = DecodedStreamObject()
    content.set_data(b"q 320 0 0 240 0 0 cm /Im0 Do Q")
    page[NameObject("/Contents")] = w._add_object(content)
    src = _blank(tmp_path / "photos.pdf", w)
    assert b"ModelSecret" in src.read_bytes()
    out = pdftools.remove_hidden(src, _ctx(tmp_path, remove=["details"])).outputs[0]
    assert b"ModelSecret" not in out.read_bytes()
    left = PdfReader(str(out)).pages[0].images[0].image
    assert left.size == (320, 240)


# ----------------------------------------------------------------------------- Cover

def _line_pdf(path: Path, mode: int, extras: bool = False) -> Path:
    """One line, "Name JOHN TOPSECRET 12345 end", drawn (mode 0) or hidden like a scan's searchable words
    (mode 3), on a blue page."""
    w = PdfWriter()
    p = w.add_blank_page(300, 400)
    font = w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                           NameObject("/Subtype"): NameObject("/Type1"),
                                           NameObject("/BaseFont"): NameObject("/Helvetica")}))
    s = DecodedStreamObject()
    s.set_data(f"0 0 1 rg 0 0 300 400 re f BT {mode} Tr /F1 12 Tf 10 300 Td (Name JOHN TOPSECRET 12345 end) Tj ET"
               .encode())
    p[NameObject("/Contents")] = w._add_object(s)
    p[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    if extras:
        thumb = StreamObject()
        thumb._data = b"THUMBSECRET" * 10
        p[NameObject("/Thumb")] = w._add_object(thumb)
        p[NameObject("/PieceInfo")] = DictionaryObject({NameObject("/Illustrator"): DictionaryObject(
            {NameObject("/Private"): TextStringObject("PIECESECRET")})})
    return _blank(path, w)


def _cover_word(src: Path, word: str) -> dict:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(src))
    try:
        tp = doc[0].get_textpage()
        i = tp.get_text_range().index(word)
        boxes = [tp.get_charbox(i + k) for k in range(len(word))]
    finally:
        doc.close()
    left, right = min(b[0] for b in boxes), max(b[2] for b in boxes)
    bottom, top = min(b[1] for b in boxes), max(b[3] for b in boxes)
    return dict(kind="cover", page=0, x=left - 1, y=400 - top - 1, w=right - left + 2, h=top - bottom + 2,
                color="#000000")


def _words(path: Path) -> str:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(path))
    try:
        return doc[0].get_textpage().get_text_range()
    finally:
        doc.close()


@pytest.mark.parametrize("mode", [0, 3])
def test_cover_takes_out_words_under_it_even_hidden_ones(tmp_path, mode):
    src = _line_pdf(tmp_path / f"line{mode}.pdf", mode)
    assert "TOPSECRET" in _words(src)
    out = pdfedit.save_edits(src, _ctx(tmp_path, edits=[_cover_word(src, "TOPSECRET")])).outputs[0]
    assert "TOPSECRET" not in _words(out)


def test_cover_leaves_hidden_words_it_doesnt_touch(tmp_path):
    src = _line_pdf(tmp_path / "hidden.pdf", 3)
    cover = dict(kind="cover", page=0, x=200, y=300, w=60, h=60, color="#000000")  # (far from the line)
    out = pdfedit.save_edits(src, _ctx(tmp_path, edits=[cover])).outputs[0]
    assert "TOPSECRET" in _words(out)


def test_cover_takes_the_pages_old_small_picture_and_private_copy_too(tmp_path):
    src = _line_pdf(tmp_path / "extras.pdf", 0, extras=True)
    out = pdfedit.save_edits(src, _ctx(tmp_path, edits=[_cover_word(src, "TOPSECRET")])).outputs[0]
    page = PdfReader(str(out)).pages[0]
    assert "/Thumb" not in page and "/PieceInfo" not in page
    data = out.read_bytes()
    assert b"THUMBSECRET" not in data and b"PIECESECRET" not in data


# ----------------------------------------------------------------------------- locked PDFs stay locked

def _locked(path: Path, password: str = "pw123") -> Path:
    im = Image.new("RGB", (1400, 1400))
    im.putdata([((x * 7) % 256, (x * 13) % 256, (x * 3) % 256) for x in range(1400 * 1400)])
    buf = io.BytesIO()
    im.save(buf, "PDF", resolution=150)
    w = PdfWriter(clone_from=PdfReader(buf))
    w.encrypt(user_password=password, owner_password=password, algorithm="AES-256")
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def test_a_locked_pdfs_smaller_copy_is_locked(tmp_path):
    src = _locked(tmp_path / "locked.pdf")
    r = pdftools.compress(src, _ctx(tmp_path, password="pw123", level="strong"))
    out = r.outputs[0]
    reader = PdfReader(str(out))
    assert reader.is_encrypted and not reader.decrypt("") and reader.decrypt("pw123")


@pytest.mark.parametrize("fn, params", [
    (pdfstamp.page_numbers, {}),
    (pdfstamp.watermark_pdf, {"text": "Draft", "logo": "", "position": "c", "size": "medium", "opacity": 40}),
    (pdfstamp.sign_pdf, {"pages": "last", "place": "br", "size": "medium"})])
def test_numbers_watermark_and_signature_keep_the_lock(tmp_path, fn, params):
    src = _locked(tmp_path / "locked.pdf")
    if fn is pdfstamp.sign_pdf:
        sig = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
        sig.paste((0, 0, 0, 255), (20, 40, 280, 60))
        sig.save(tmp_path / "sig.png")
        params = dict(params, signature=str(tmp_path / "sig.png"))
    with pytest.raises(NeedPassword):  # (the app asks for the password, then tries again)
        fn(src, _ctx(tmp_path, **params))
    out = fn(src, _ctx(tmp_path, password="pw123", **params)).outputs[0]
    reader = PdfReader(str(out))
    assert reader.is_encrypted and not reader.decrypt("") and reader.decrypt("pw123")
    assert not list((tmp_path / "out").glob("*.part"))


def test_jpeg_without_details_skips_a_second_picture_inside(tmp_path):
    """Phones put a second picture (depth, preview) after an MPF mark: it never goes into a PDF."""
    data = _photo(tmp_path / "p.jpg").read_bytes()
    mpf = b"MPF\0" + zlib.compress(b"SECONDPICTURE" * 20)
    seg = b"\xff\xe2" + (len(mpf) + 2).to_bytes(2, "big") + mpf
    data = data[:2] + seg + data[2:]
    plain = pdftools.jpeg_without_details(data)
    assert mpf not in plain
    Image.open(io.BytesIO(plain)).load()
