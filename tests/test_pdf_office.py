"""Rakiza Core PDF to Excel and PDF to PowerPoint, written by hand: a table comes out as a table (numbers as
numbers, an Arabic table right to left), and every page as a slide."""
import threading
import zipfile
from pathlib import Path
from xml.dom import minidom

import pytest

from corefold import pdfoffice
from corefold.core import Ctx, Options


def _table_pdf(path: Path, rows: list[list[str]], rtl: bool = False) -> Path:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QFont, QPageSize, QPainter, QPdfWriter
    w = QPdfWriter(str(path))
    w.setPageSize(QPageSize(QPageSize.A4))
    w.setResolution(72)
    p = QPainter(w)
    p.setFont(QFont("Arial", 11))
    xs = [60, 220, 340, 460]
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            x = 595 - xs[c] - 100 if rtl else xs[c]
            p.drawText(QRectF(x, 60 + r * 22, 100, 18), (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignVCenter,
                       text)
    p.end()
    return path


def _ctx(tmp_path: Path) -> Ctx:
    options = Options()
    options.output_folder = tmp_path / "out"
    options.output_folder.mkdir(exist_ok=True)
    return Ctx(options, lambda f: None, threading.Event(), {})


def _parts_parse(path: Path):
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith((".xml", ".rels")):
                minidom.parseString(z.read(name))  # (every part is well-formed XML)
        return set(z.namelist())


def test_numbers_and_words():
    assert pdfoffice.number("1,234.50") == 1234.5
    assert pdfoffice.number("(12)") == -12
    assert pdfoffice.number("15%") == 0.15
    assert pdfoffice.number("١٢٣٫٥") == 123.5
    assert pdfoffice.number("0551234567") is None  # (a phone keeps its 0)
    assert pdfoffice.number("Total") is None and pdfoffice.number("12 kg") is None


def test_a_table_comes_out_as_a_table(qapp, tmp_path):
    rows = [["Item", "Count", "Price", "Total"], ["Paper A4", "3", "12.50", "37.50"],
            ["Pens", "10", "1.25", "12.50"]]
    src = _table_pdf(tmp_path / "invoice.pdf", rows)
    out = pdfoffice.to_excel(src, _ctx(tmp_path)).outputs[0]
    assert out.suffix == ".xlsx"
    assert "xl/worksheets/sheet1.xml" in _parts_parse(out)
    openpyxl = pytest.importorskip("openpyxl")
    ws = openpyxl.load_workbook(out).active
    got = [[c.value for c in r] for r in ws.iter_rows()]
    assert got[0] == ["Item", "Count", "Price", "Total"]
    assert got[1] == ["Paper A4", 3, 12.5, 37.5] and got[2] == ["Pens", 10, 1.25, 12.5]


def test_an_arabic_table_reads_right_to_left(qapp, tmp_path):
    rows = [["الصنف", "العدد"], ["ورق", "٣"], ["أقلام", "١٠"]]
    src = _table_pdf(tmp_path / "ar.pdf", rows, rtl=True)
    out = pdfoffice.to_excel(src, _ctx(tmp_path)).outputs[0]
    with zipfile.ZipFile(out) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert 'rightToLeft="1"' in sheet
    openpyxl = pytest.importorskip("openpyxl")
    ws = openpyxl.load_workbook(out).active
    got = [[c.value for c in r] for r in ws.iter_rows()]
    assert got[0][0] == "الصنف" and got[1] == ["ورق", 3] and got[2] == ["أقلام", 10]


def test_a_scan_has_no_words_to_put_in_a_sheet(qapp, tmp_path):
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(200, 200)
    w.write(tmp_path / "blank.pdf")
    with pytest.raises(RuntimeError):
        pdfoffice.to_excel(tmp_path / "blank.pdf", _ctx(tmp_path))


def test_every_page_is_a_slide(qapp, tmp_path):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QFont, QPageSize, QPainter, QPdfWriter
    src = tmp_path / "talk.pdf"
    w = QPdfWriter(str(src))
    w.setPageSize(QPageSize(QPageSize.A4))
    w.setResolution(72)
    p = QPainter(w)
    p.setFont(QFont("Arial", 20))
    for n in range(3):
        if n:
            w.newPage()
        p.drawText(QRectF(60, 60, 400, 40), 0, f"Slide number {n + 1}")
    p.end()
    out = pdfoffice.to_powerpoint(src, _ctx(tmp_path)).outputs[0]
    names = _parts_parse(out)
    assert {"ppt/slides/slide3.xml", "ppt/media/page1.jpeg"} <= names
    pptx = pytest.importorskip("pptx")
    pres = pptx.Presentation(str(out))
    assert len(pres.slides) == 3
    assert abs(pres.slide_width / pres.slide_height - 595 / 842) < 0.01
