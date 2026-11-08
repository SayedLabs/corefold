"""Rakiza Core PDF's editor: Fill in (a tick, a cross, a dot, today's date and your initials put on a form that
isn't a real one, with a click) and stamps (Approved, Received with the date, your own words…), saved as real
drawing and text in the new PDF."""
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pypdfium2 as pdfium
from pypdf import PdfReader

from corefold import pdfedit, pdfeditor
from corefold.core import Ctx, Options
from corefold.settings import Settings


def _pdf(path: Path) -> Path:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QFont, QPageSize, QPainter, QPdfWriter
    w = QPdfWriter(str(path))
    w.setPageSize(QPageSize(QPageSize.A4))
    w.setResolution(72)
    p = QPainter(w)
    p.setFont(QFont("Arial", 12))
    p.drawText(QRectF(50, 50, 450, 40), Qt.TextWordWrap, "Name: ____________   Agree: [   ]")
    p.end()
    return path


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
    try:
        qtbot.waitUntil(lambda: bool(ed.pages) and ed.saved is not None, timeout=15000)
    except Exception:  # (what the editor was waiting for, to say why)
        from pdfdiag import stall_report
        raise AssertionError(stall_report(ed)) from None
    return ed, page


def _save(src: Path, ed, tmp_path: Path) -> Path:
    options = Options()
    options.output_folder = tmp_path / "out"
    options.output_folder.mkdir(exist_ok=True)
    edits = ed.model()["items"]
    return pdfedit.save_edits(src, Ctx(options, lambda f: None, threading.Event(), {"edits": edits})).outputs[0]


def _ink(path: Path) -> int:
    """How many dark pixels the first page has."""
    doc = pdfium.PdfDocument(str(path))
    im = doc[0].render(scale=1).to_pil().convert("L")
    doc.close()
    return sum(1 for v in im.tobytes() if v < 128)


def test_tick_and_cross_strokes():
    tick = pdfeditor.fill_strokes("check", 100, 100, 12)
    assert len(tick) == 1 and len(tick[0]) == 3
    cross = pdfeditor.fill_strokes("cross", 100, 100, 12)
    assert cross == [[[94, 94], [106, 106]], [[106, 94], [94, 106]]]


def test_a_stamp_is_sized_by_its_words(qapp):
    w1, h1 = pdfedit.stamp_size("OK")
    w2, h2 = pdfedit.stamp_size("CONFIDENTIAL")
    w3, h3 = pdfedit.stamp_size("PAID", "8/11/2026 10:00")
    assert w2 > w1 and h1 == h2 and h3 > h1


def test_fill_in_and_stamps_are_saved(qapp, qtbot, tmp_path):
    src = _pdf(tmp_path / "form.pdf")
    before = _ink(src)
    ed, page = _editor(qtbot, src)
    p0 = ed.pages[0]
    from PySide6.QtCore import QPointF
    ed.set_tool("fill")
    assert ed.kind_btns["check"].isVisible() and not ed.kind_btns["highlight"].isVisible()
    for kind, x in (("check", 300), ("cross", 330), ("dot", 360), ("date", 120)):
        ed.set_kind(kind)
        ed.new_fill(p0, QPointF(x, 140))
    # initials: asked for once, then kept
    from corefold import sheet
    asked = []
    real = sheet.get_text
    sheet.get_text = lambda *a, **k: asked.append(a) or ("M.S.", True)
    try:
        ed.set_kind("initials")
        ed.new_fill(p0, QPointF(120, 200))
        ed.new_fill(p0, QPointF(120, 240))
    finally:
        sheet.get_text = real
    assert len(asked) == 1 and page.c.settings.pdf_initials == "M.S."
    ed.set_tool("stamp")
    assert ed.kind_btns["approved"].isVisible()
    ed.set_kind("received")
    ed.new_stamp(p0, QPointF(400, 500))
    items = ed.model()["items"]
    kinds = [d["kind"] for d in items]
    assert kinds.count("ink") == 2 and kinds.count("ellipse") == 1 and kinds.count("stamp") == 1
    texts = [d["text"] for d in items if d["kind"] == "text"]
    assert "M.S." in texts and len(texts) == 3
    stamp = next(d for d in items if d["kind"] == "stamp")
    assert stamp["text"] == "RECEIVED" and stamp["date"] and stamp["w"] > 60
    out = _save(src, ed, tmp_path)
    words = PdfReader(out).pages[0].extract_text()
    assert "RECEIVED" in words and "M.S." in words  # (real text, found and copied in any PDF app)
    assert _ink(out) > before + 200  # (the marks are drawn)
    ed.close_renderer()
    ed.close()


def test_your_own_stamp_asks_for_its_words(qapp, qtbot, tmp_path):
    src = _pdf(tmp_path / "form.pdf")
    ed, page = _editor(qtbot, src)
    from PySide6.QtCore import QPointF

    from corefold import sheet
    real = sheet.get_text
    sheet.get_text = lambda *a, **k: ("مدفوع بالكامل", True)
    try:
        ed.set_tool("stamp")
        ed.set_kind("custom")
        ed.new_stamp(ed.pages[0], QPointF(300, 300))
    finally:
        sheet.get_text = real
    stamp = next(d for d in ed.model()["items"] if d["kind"] == "stamp")
    assert stamp["text"] == "مدفوع بالكامل" and page.c.settings.pdf_stamp_words == "مدفوع بالكامل"
    out = _save(src, ed, tmp_path)
    doc = pdfium.PdfDocument(str(out))
    try:
        text = doc[0].get_textpage().get_text_range()
    finally:
        doc.close()
    assert "مدفوع" in pdfedit.arabic_letters(text), repr(text)  # (Arabic words stay searchable)
    ed.close_renderer()
    ed.close()
