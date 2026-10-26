"""PDF editor fonts: a changed line keeps its font (found on the PC or among the fonts Corefold brings,
by the many names PDFs give fonts), Arabic and English on one line keep their own fonts, and every font
that writes English or Arabic is offered for new text."""
import sys

import pytest

from corefold import pdfedit, pdffonts


@pytest.fixture(autouse=True)
def _fonts(qapp):
    pdffonts.ready()


@pytest.mark.parametrize("pdf_name, family", [
    ("ABCDEF+Cairo-SemiBold", "Cairo"),
    ("Tajawal-Bold", "Tajawal"),
    ("XYZABC+Almarai-ExtraBold", "Almarai"),
    ("ElMessiri-SemiBold", "El Messiri"),
    ("MarkaziText-Medium", "Markazi Text"),
    ("Amiri-Regular", "Markazi Text"),
    ("IBMPlexSansArabic-Medium", "IBM Plex Sans Arabic"),
    ("NotoNaskhArabic-Bold", "Markazi Text"),
    ("DroidArabicKufi", "Cairo"),
    ("Roboto-Light", "Roboto"),
    ("OpenSans-Bold", "Open Sans"),
    ("Montserrat-Black", "Montserrat"),
    ("ABCDEF+Poppins-Medium", "Poppins"),
])
def test_brought_fonts_found_by_pdf_names(pdf_name, family):
    assert pdffonts.find(pdfedit.clean_family(pdf_name)[0]) == family


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own fonts")
@pytest.mark.parametrize("pdf_name, family", [
    ("ABCDEF+TimesNewRomanPS-BoldMT", "Times New Roman"),
    ("ArialMT", "Arial"),
    ("Helvetica-Bold", "Arial"),
    ("CourierNewPSMT", "Courier New"),
    ("Calibri-Light", "Calibri"),
    ("SegoeUI-Semibold", "Segoe UI"),
    ("TraditionalArabic", "Traditional Arabic"),
])
def test_windows_fonts_found_by_pdf_names(pdf_name, family):
    assert pdffonts.find(pdfedit.clean_family(pdf_name)[0]) == family


def test_unknown_font_gets_the_closest_look():
    assert pdffonts.find("Zedfold Grotesk") == ""
    serif, _ = pdffonts.for_line({"font": "Zedfold Serif", "text": "Hello", "serif": True})
    sans, _ = pdffonts.for_line({"font": "Zedfold Grotesk", "text": "Hello"})
    assert serif in [pdffonts.find(n) for n in pdffonts.FALLBACK["serif"]]
    assert sans in [pdffonts.find(n) for n in pdffonts.FALLBACK["sans"]]
    # Arabic words: a font that has Arabic letters
    ar, _ = pdffonts.for_line({"font": "Zedfold Grotesk", "text": "مرحبا بكم"})
    assert pdffonts.covers(ar, "مرحبا بكم")


def test_arabic_and_english_keep_their_own_fonts():
    d = {"font": "Roboto", "font_en": "Roboto", "font_ar": "Cairo", "text": "Total المجموع 25"}
    assert pdffonts.for_line(d) == ("Roboto", "Cairo")
    # all Arabic (numbers too): one font
    assert pdffonts.for_line({"font": "Cairo", "font_ar": "Cairo", "text": "المجموع 25"}) == ("Cairo", "")


def test_arabic_words_drawn_in_their_font():
    d = {"font": "Roboto", "font_ar": "Cairo", "size": 12, "text": "Total المجموع here"}
    doc = pdfedit.text_document(d)
    fams = {}
    block = doc.begin()
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        fams[frag.text().strip()] = frag.charFormat().font().family()
        it += 1
    assert fams.get("المجموع") == "Cairo"
    assert fams.get("Total") == "Roboto"


def test_every_english_and_arabic_font_offered():
    fonts = pdffonts.offered()
    assert len(fonts) == len(set(fonts))
    for f in ("Cairo", "Tajawal", "Markazi Text", "Roboto", "Open Sans"):
        assert f in fonts
    assert "Almarai Bold" not in fonts  # (the bold button gives it)
    if sys.platform == "win32":
        for f in ("Arial", "Times New Roman", "Calibri", "Segoe UI", "Tahoma", "Traditional Arabic"):
            assert f in fonts
        assert len(fonts) > 40


def test_a_pdfs_fonts_read_back(tmp_path):
    """A PDF written in Roboto with its Arabic words in Cairo: the editor opens the line in both again."""
    import pypdfium2 as pdfium
    from PySide6.QtCore import QSizeF
    from PySide6.QtGui import QFont, QPageSize, QPainter, QPdfWriter
    from corefold.pdftools import PDFIUM
    path = tmp_path / "two fonts.pdf"
    w = QPdfWriter(str(path))
    w.setPageSize(QPageSize(QSizeF(400, 200), QPageSize.Point))
    w.setResolution(72)
    p = QPainter(w)
    for fam, x, text in (("Roboto", 20, "Total"), ("Cairo", 80, "المجموع")):
        f = QFont(fam)
        f.setPointSizeF(18)
        p.setFont(f)
        p.drawText(x, 60, text)
    f = QFont("Markazi Text")
    f.setPointSizeF(18)
    p.setFont(f)
    p.drawText(20, 140, "نص عربي")
    p.end()
    with PDFIUM:
        doc = pdfium.PdfDocument(str(path))
        try:
            lines = pdfedit.read_layout(doc, 0)["lines"]
        finally:
            doc.close()
    mixed = next(d for d in lines if "Total" in d["text"])
    assert pdffonts.for_line(mixed) == ("Roboto", "Cairo")
    arabic = next(d for d in lines if "عربي" in d["text"])
    assert pdffonts.for_line(arabic) == ("Markazi Text", "")


@pytest.mark.parametrize("family", ["Cairo", "Tajawal", "Almarai", "IBM Plex Sans Arabic", "El Messiri",
                                    "Markazi Text", "Roboto", "Open Sans", "Montserrat", "Poppins"])
def test_brought_fonts_stay_searchable(tmp_path, family):
    """Words saved in a font Corefold brings can still be found and copied (Arabic too)."""
    import pypdfium2 as pdfium
    from PySide6.QtCore import QSizeF
    from PySide6.QtGui import QFont, QPageSize, QPainter, QPdfWriter
    path = tmp_path / "words.pdf"
    w = QPdfWriter(str(path))
    w.setPageSize(QPageSize(QSizeF(400, 100), QPageSize.Point))
    w.setResolution(72)
    p = QPainter(w)
    for bold, y in ((False, 40), (True, 80)):
        f = QFont(family)
        f.setPointSizeF(18)
        f.setBold(bold)
        p.setFont(f)
        p.drawText(20, y, "Hello مرحبا بكم")
    p.end()
    doc = pdfium.PdfDocument(str(path))
    try:
        text = doc[0].get_textpage().get_text_range()
    finally:
        doc.close()
    assert text.count("Hello") == 2 and text.count("مرحبا") == 2, repr(text)
