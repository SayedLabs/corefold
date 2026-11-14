"""Rakiza Core PDF: Find and cover (phone numbers, emails, ID numbers… found and really taken out), form boxes
made in the editor (real fields any PDF app fills in), and a header and footer with a colour behind the pages."""
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pypdfium2 as pdfium
from pypdf import PdfReader

from corefold import pdfedit, pdfeditor, pdfreader, pdfstamp
from corefold.core import Ctx, Options
from corefold.settings import Settings


def _pdf(path: Path, pages: list[str]) -> Path:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QFont, QPageSize, QPainter, QPdfWriter
    w = QPdfWriter(str(path))
    w.setPageSize(QPageSize(QPageSize.A4))
    w.setResolution(72)
    p = QPainter(w)
    p.setFont(QFont("Arial", 12))
    for n, text in enumerate(pages):
        if n:
            w.newPage()
        p.drawText(QRectF(50, 50, 450, 700), Qt.TextWordWrap, text)
    p.end()
    return path


def _ctx(tmp_path: Path, params: dict) -> Ctx:
    options = Options()
    options.output_folder = tmp_path / "out"
    options.output_folder.mkdir(exist_ok=True)
    return Ctx(options, lambda f: None, threading.Event(), params)


def _editor(qtbot, src: Path):
    page = MagicMock()
    page.c.settings = Settings()
    page.c.settings.save = MagicMock()
    page.c.theme.muted = "#777777"
    page.c.theme.text = "#111111"
    ed = pdfeditor.PdfEditor(page)
    ed.resize(1100, 760)
    ed.show()
    ed.open(src)
    qtbot.waitUntil(lambda: bool(ed.pages) and ed.saved is not None, timeout=15000)
    return ed, page


def _text(path: Path) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(path).pages)


def test_private_things_are_found():
    t = ("Call +966 55 123 4567 or ٠٥٥١٢٣٤٥٦٧, mail a.b@x.co, ID 1012345678, card 4111 1111 1111 1111, "
         "IBAN SA03 8000 0000 6080 1016 7519. Price 12.50, year 2026, v1.2.3, 08/11/2026. Secret: Ahmed")
    found = [t[a:b] for a, b in pdfreader.private_spans(t, list(pdfreader.PRIVATE_KINDS), ["ahmed"])]
    assert found == ["+966 55 123 4567", "٠٥٥١٢٣٤٥٦٧", "a.b@x.co", "1012345678", "4111 1111 1111 1111",
                     "SA03 8000 0000 6080 1016 7519", "Ahmed"]
    assert pdfreader.private_spans(t, ["email"]) == [(t.index("a.b@"), t.index("a.b@") + 8)]
    assert pdfreader.private_spans("nothing here 12.50 2026", list(pdfreader.PRIVATE_KINDS)) == []
    # a name of one's own where the page breaks the line between its words (PDFium's line break is CR LF)
    t = "Signed by Ahmed\r\nAl Saud on 08/11/2026"
    assert [t[a:b] for a, b in pdfreader.private_spans(t, [], ["ahmed al  saud"])] == ["Ahmed\r\nAl Saud"]


def test_find_and_cover_takes_them_out(qapp, qtbot, tmp_path):
    src = _pdf(tmp_path / "a.pdf", ["Name: Sara. Mail sara@example.com", "Nothing here", "Phone 0551234567 ok"])
    found = pdfreader.find_private(src, "", ["email", "phone"])
    assert [p for p, _ in found] == [0, 2] and all(len(b) == 1 for _, b in found)
    ed, page = _editor(qtbot, src)
    page.c.tell = MagicMock()
    assert ed.add_found_covers(found) == 2
    ed.set_tool("cover")
    assert ed.find_section.isVisibleTo(ed)
    out = pdfedit.save_edits(src, _ctx(tmp_path, {"edits": ed.model()["items"]})).outputs[0]
    text = _text(out)
    assert "sara@example.com" not in text and "0551234567" not in text  # (taken out, not only hidden)
    assert "Name" in text and "Nothing" in text and "Sara" in text
    ed.close_renderer()
    ed.close()


def test_form_boxes_are_real_fields(qapp, qtbot, tmp_path):
    from PySide6.QtCore import QPointF
    src = _pdf(tmp_path / "f.pdf", ["Name:            Agree:"])
    ed, _page = _editor(qtbot, src)
    ed.set_tool("form")
    assert ed.kind_btns["formtext"].isVisible()
    ed.new_form_box(ed.pages[0], QPointF(120, 60))
    ed.new_form_box(ed.pages[0], QPointF(120, 100))
    ed.set_kind("formcheck")
    ed.new_form_box(ed.pages[0], QPointF(300, 60))
    boxes = [d for d in ed.model()["items"] if d["kind"] == "formbox"]
    assert [b["name"] for b in boxes] == ["Text 1", "Text 2", "Tick 1"] and boxes[2]["w"] == boxes[2]["h"]
    out = pdfedit.save_edits(src, _ctx(tmp_path, {"edits": ed.model()["items"]})).outputs[0]
    fields = PdfReader(out).get_fields()
    assert set(fields) == {"Text 1", "Text 2", "Tick 1"}
    assert fields["Text 1"]["/FT"] == "/Tx" and fields["Tick 1"]["/FT"] == "/Btn"
    ed.close_renderer()
    ed.close()
    # and they can be filled in: the editor opens them as the PDF's own form
    ed2, _ = _editor(qtbot, out)
    assert {f["name"] for f in ed2.field_list} == {"Text 1", "Text 2", "Tick 1"}
    ed2.close_renderer()
    ed2.close()


def test_header_footer_and_background(qapp, tmp_path):
    src = _pdf(tmp_path / "report.pdf", ["Cover", "Inside one", "Inside two"])
    out = pdfstamp.header_footer(src, _ctx(tmp_path, {
        "header": "{name}", "footer": "Page {page} of {pages}", "header_place": "l", "footer_place": "r",
        "size": 10, "skip": 1, "background": "#e8f0fe"})).outputs[0]
    pages = [" ".join(p.extract_text().split()) for p in PdfReader(out).pages]
    assert "Page" not in pages[0] and "Page 1 of 2" in pages[1] and "Page 2 of 2" in pages[2]
    assert "report" in pages[1]
    doc = pdfium.PdfDocument(str(out))
    try:
        im = doc[0].render(scale=0.5).to_pil().convert("RGB")
    finally:
        doc.close()
    r, g, b = im.getpixel((im.width - 3, im.height // 2))
    assert b > r + 10  # (light blue behind the page)
    assert out.name == "report (header and footer).pdf"
    assert not list((tmp_path / "out").glob("*edited*"))  # (nothing half made left next to it)
