"""Remove hidden info: a PDF's author, program, notes, attached files, scripts and programs' private data
are found and taken out, the title and author can be typed, and the pages, web links and form boxes stay
as they were. A locked PDF stays locked."""
import threading
import warnings
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link, Text
from pypdf.generic import (ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject,
                           create_string_object)

from corefold import pdftools
from corefold.core import Ctx, Options


def _ctx(**params) -> Ctx:
    return Ctx(Options(), lambda f: None, threading.Event(), params)


def dirty(path: Path, password: str = "") -> Path:
    """A PDF with a bit of everything hidden in it."""
    Image.new("RGB", (600, 800), "white").save(path, "PDF", resolution=72, save_all=True,
                                               append_images=[Image.new("RGB", (600, 800), "white")])
    w = PdfWriter(clone_from=PdfReader(str(path)))
    w.metadata = {"/Title": "Q3 report", "/Author": "Ahmed Ali", "/Creator": "Microsoft Word",
                  "/Producer": "Secret PDF maker 2.0", "/Keywords": "internal"}
    w.add_attachment("salaries.xlsx", b"secret numbers")
    with warnings.catch_warnings():  # (pypdf calls it old, but it's how other programs add one)
        warnings.simplefilter("ignore", DeprecationWarning)
        w.add_js("app.alert('hi');")
    w.add_annotation(0, Text(rect=(50, 700, 80, 730), text="Fix this before sending"))
    w.add_annotation(0, Link(rect=(50, 50, 200, 70), url="https://example.com"))
    page = w.pages[1]
    page[NameObject("/PieceInfo")] = DictionaryObject({NameObject("/Word"): DictionaryObject()})
    page[NameObject("/AA")] = DictionaryObject({NameObject("/O"): DictionaryObject(
        {NameObject("/S"): NameObject("/JavaScript"), NameObject("/JS"): TextStringObject("app.beep(0)")})})
    js_link = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Link"),
        NameObject("/Rect"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]),
        NameObject("/A"): DictionaryObject({NameObject("/S"): NameObject("/Launch"),
                                            NameObject("/F"): create_string_object("cmd.exe")})})
    page[NameObject("/Annots")] = ArrayObject([w._add_object(js_link)])
    if password:
        w.encrypt(user_password=password, owner_password=password, algorithm="AES-256")
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def test_finds_what_is_hidden(tmp_path):
    found = pdftools.hidden_info(dirty(tmp_path / "a.pdf"))
    assert found["title"] == "Q3 report" and found["author"] == "Ahmed Ali"
    names = dict(found["details"])
    assert names["Creator"] == "Microsoft Word" and names["Producer"] == "Secret PDF maker 2.0"
    assert found["notes"] == 1
    assert found["attachments"] == 1
    assert found["scripts"] >= 3  # (the document's script, the page's open action, the Launch link)
    assert found["private"] >= 1


def test_takes_it_all_out_and_keeps_the_rest(tmp_path):
    src = dirty(tmp_path / "a.pdf")
    before = src.read_bytes()
    result = pdftools.remove_hidden(src, _ctx(title="Q3 report", author=""))
    out = result.outputs[0]
    assert out.name == "a (cleaned).pdf" and src.read_bytes() == before  # (the original isn't touched)
    found = pdftools.hidden_info(out)
    assert found["details"] == [("Title", "Q3 report")]  # (no pypdf, no Word, no author)
    assert (found["notes"], found["attachments"], found["scripts"], found["private"]) == (0, 0, 0, 0)
    reader = PdfReader(str(out))
    assert len(reader.pages) == 2
    links = [a.get_object() for a in reader.pages[0]["/Annots"]]
    assert [a["/Subtype"] for a in links] == ["/Link"] and links[0]["/A"]["/URI"] == "https://example.com"
    assert "/Annots" not in reader.pages[1]  # (the link that started a program is gone)
    assert b"secret numbers" not in out.read_bytes() and b"Ahmed" not in out.read_bytes()


def test_only_what_was_picked(tmp_path):
    out = pdftools.remove_hidden(dirty(tmp_path / "a.pdf"), _ctx(remove=["notes"])).outputs[0]
    found = pdftools.hidden_info(out)
    assert found["notes"] == 0 and found["attachments"] == 1 and found["author"] == "Ahmed Ali"


def test_new_title_and_author(tmp_path):
    out = pdftools.remove_hidden(dirty(tmp_path / "a.pdf"), _ctx(remove=[], title="Report",
                                                                  author="Sayed")).outputs[0]
    meta = PdfReader(str(out)).metadata
    assert meta["/Title"] == "Report" and meta["/Author"] == "Sayed"


def test_locked_pdf_stays_locked(tmp_path):
    src = dirty(tmp_path / "a.pdf", password="pw")
    from corefold.archives import NeedPassword
    with pytest.raises(NeedPassword):
        pdftools.hidden_info(src)
    out = pdftools.remove_hidden(src, _ctx(password="pw")).outputs[0]
    reader = PdfReader(str(out))
    assert reader.is_encrypted and reader.decrypt("pw")
    assert pdftools.hidden_info(out, "pw")["attachments"] == 0
