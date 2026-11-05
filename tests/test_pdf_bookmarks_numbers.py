"""Rakiza Core PDF: the bookmarks (the list of chapters on the side), and page numbers in Arabic digits."""
import threading
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from corefold import pdfmore, pdfstamp
from corefold.core import Ctx, Options


def _ctx(folder: Path, **params) -> Ctx:
    return Ctx(Options(output_folder=folder), lambda f: None, threading.Event(), params)


def _pdf(path: Path, pages=5, marks=True, password="") -> Path:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(300, 400)
    if marks:
        one = w.add_outline_item("One", 0)
        w.add_outline_item("One, part a", 1, parent=one)
        w.add_outline_item("Two", 3)
    if password:
        w.encrypt(password, algorithm="AES-256")
    w.write(path)
    return path


def test_read_the_bookmarks(tmp_path):
    marks, pages, other = pdfmore.read_bookmarks(_pdf(tmp_path / "a.pdf"))
    assert marks == [[0, "One", 0], [1, "One, part a", 1], [0, "Two", 3]] and pages == 5 and other == 0
    assert pdfmore.read_bookmarks(_pdf(tmp_path / "b.pdf", marks=False))[0] == []


def test_save_a_new_list(tmp_path):
    src = _pdf(tmp_path / "a.pdf")
    new = [[0, "الفصل الأول", 0], [1, "قسم", 2], [2, "Deeper", 3], [0, "Last", 99], [5, "Too deep", 4]]
    out = pdfmore.write_bookmarks(src, _ctx(tmp_path, marks=new)).outputs[0]
    assert out.name == "a (bookmarks).pdf"
    marks, _, _ = pdfmore.read_bookmarks(out)
    # (a page past the end is the last page; a level that skips one goes under the one before)
    assert marks == [[0, "الفصل الأول", 0], [1, "قسم", 2], [2, "Deeper", 3], [0, "Last", 4], [1, "Too deep", 4]]
    assert PdfReader(out).page_mode == "/UseOutlines"
    assert pdfmore.read_bookmarks(src)[0][0] == [0, "One", 0]  # (the original stays as it is)


def test_take_them_all_away(tmp_path):
    out = pdfmore.write_bookmarks(_pdf(tmp_path / "a.pdf"), _ctx(tmp_path, marks=[])).outputs[0]
    assert pdfmore.read_bookmarks(out)[0] == []


def test_a_locked_pdf_stays_locked(tmp_path):
    src = _pdf(tmp_path / "a.pdf", password="secret")
    marks, _, _ = pdfmore.read_bookmarks(src, "secret")
    out = pdfmore.write_bookmarks(src, _ctx(tmp_path, marks=marks[:1], password="secret")).outputs[0]
    r = PdfReader(out)
    assert r.is_encrypted and r.decrypt("secret")
    assert pdfmore.read_bookmarks(out, "secret")[0] == [[0, "One", 0]]


def test_the_dialog_gives_back_the_list(qtbot, tmp_path):
    from PySide6.QtGui import QIcon

    from corefold.pdfdialogs import BookmarksDialog
    marks = [[0, "One", 0], [1, "One, part a", 1], [0, "Two", 3]]
    dlg = BookmarksDialog([tmp_path / "a.pdf"], QIcon(), False, marks, 5, other=2)
    qtbot.addWidget(dlg)
    assert dlg.params() == {"marks": marks}
    dlg.tree.setCurrentItem(dlg.tree.topLevelItem(1))
    dlg._add()
    dlg.page.setValue(5)
    assert dlg.params()["marks"][-1] == [0, "Page 4", 4]
    dlg.tree.setCurrentItem(dlg.tree.topLevelItem(0))
    dlg._remove()
    assert dlg.params()["marks"] == [[0, "Two", 3], [0, "Page 4", 4]]


def test_arabic_digits():
    assert pdfstamp.number_text(12, 20, "ar") == "١٢"
    assert pdfstamp.number_text(3, 10, "ar_of") == "٣ / ١٠"
    assert pdfstamp.number_text(3, 10, "of") == "3 / 10"


def test_page_numbers_in_arabic_digits(qapp, tmp_path):
    src = _pdf(tmp_path / "a.pdf", pages=3, marks=False)
    out = pdfstamp.page_numbers(src, _ctx(tmp_path, style="ar", place="bc", skip=1)).outputs[0]
    r = PdfReader(out)
    assert len(r.pages) == 3
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(out))
    try:
        def ink(i):
            import numpy as np
            return int((np.asarray(doc[i].render(scale=1).to_pil().convert("L")) < 128).sum())
        assert ink(0) == 0           # (the cover has no number)
        assert ink(1) > 5 and ink(2) > 5
    finally:
        doc.close()
