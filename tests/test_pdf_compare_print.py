"""Corefold PDF: Compare two PDFs (pages side by side with what looks different marked, and every word taken out
or put in), Print (Windows' printers, through Qt) and PDF to Word (made by the Word on the PC)."""
import sys
import threading
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw

from corefold import converters, pdfmore
from corefold.core import Ctx, Options


def _ctx(tmp_path: Path, **params) -> Ctx:
    options = Options()
    options.output_folder = tmp_path / "out"
    options.output_folder.mkdir(exist_ok=True)
    return Ctx(options, lambda f: None, threading.Event(), params)


def _pdf(path: Path, pages: list[str]) -> Path:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QFont, QPageSize, QPainter, QPdfWriter
    w = QPdfWriter(str(path))
    w.setPageSize(QPageSize(QPageSize.A4))
    w.setResolution(72)
    p = QPainter(w)
    p.setFont(QFont("Arial", 14))
    for n, text in enumerate(pages):
        if n:
            w.newPage()
        p.drawText(QRectF(50, 50, 450, 700), Qt.TextWordWrap, text)
    p.end()
    return path


def test_word_changes_finds_out_in_and_swapped_words():
    old = [["one", "two", "three"], ["four", "five"]]
    new = [["one", "TWO", "three"], ["four", "five", "six"]]
    changes = pdfmore.word_changes(old, new)
    assert ("swap", 1, 1, "two  →  TWO") in changes
    assert any(c[0] == "in" and c[3] == "six" and c[2] == 2 for c in changes)
    assert pdfmore.word_changes(old, old) == []


def test_changed_boxes_marks_only_what_differs():
    a = Image.new("RGB", (300, 400), "white")
    b = a.copy()
    assert pdfmore.changed_boxes(a, b) == []
    ImageDraw.Draw(b).rectangle((200, 300, 240, 330), fill="black")
    boxes = pdfmore.changed_boxes(a, b)
    assert len(boxes) == 1
    x, y, w, h = boxes[0]
    assert x <= 200 and y <= 300 and x + w >= 240 and y + h >= 330 and w < 80 and h < 80


def test_compare_two_pdfs_makes_a_report(qapp, tmp_path):
    a = _pdf(tmp_path / "old.pdf", ["Hello world this is page one.", "Page two stays the same."])
    b = _pdf(tmp_path / "new.pdf", ["Hello world this is page ONE.", "Page two stays the same.", "A new page"])
    res = pdfmore.compare_pdfs([a, b], _ctx(tmp_path))
    assert len(res.outputs) == 1
    out = res.outputs[0]
    assert out.name == "old vs new (compared).pdf"
    doc = pdfium.PdfDocument(str(out))
    text = "\n".join(doc[i].get_textpage().get_text_bounded() for i in range(len(doc)))
    assert "ONE." in text and "A new page".split()[0] in text
    assert len(doc) == 4  # the summary, page 1, page 3 (only in the new one) and the words
    doc.close()
    assert a.exists() and b.exists()


def test_compare_same_pdf_says_so(qapp, tmp_path):
    a = _pdf(tmp_path / "a.pdf", ["Same words."])
    b = _pdf(tmp_path / "b.pdf", ["Same words."])
    res = pdfmore.compare_pdfs([a, b], _ctx(tmp_path))
    assert "look the same" in res.note


def test_compare_needs_two(tmp_path):
    with pytest.raises(RuntimeError):
        pdfmore.compare_pdfs([tmp_path / "a.pdf"], _ctx(tmp_path))


def test_page_numbers():
    assert pdfmore.page_numbers("", 3) == [0, 1, 2]
    assert pdfmore.page_numbers("2-3", 5) == [1, 2]
    with pytest.raises(ValueError):
        pdfmore.page_numbers("9", 3)


def test_print_dialog_builds_and_checks_pages(qapp, tmp_path):
    from corefold import pdfdialogs
    from PySide6.QtGui import QIcon
    dlg = pdfdialogs.PrintDialog([tmp_path / "a.pdf"], QIcon(), False, count=3)
    p = dlg.params()
    assert p["copies"] == 1 and p["pages"] == "" and not p["gray"]
    if dlg.printer.count():
        dlg.pages.setText("7")
        assert not dlg.ok.isEnabled()
        dlg.pages.setText("2-3")
        assert dlg.ok.isEnabled()
    else:
        assert not dlg.ok.isEnabled()  # no printer: nothing to press
    dlg.deleteLater()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own PDF printer")
def test_print_to_windows_pdf_printer(qapp, tmp_path):
    names, _ = pdfmore.printers()
    printer = next((n for n in names if "Microsoft Print to PDF" in n), None)
    if not printer:
        pytest.skip("no Microsoft Print to PDF on this PC")
    src = _pdf(tmp_path / "doc.pdf", ["Page one", "Page two", "Page three"])
    made = tmp_path / "printed.bin"  # (not .pdf: Qt would then write a PDF itself instead of printing)
    res = pdfmore.print_pdf(src, _ctx(tmp_path, printer=printer, copies=1, pages="1, 3", to_file=str(made)))
    assert "2" in res.note
    for _ in range(100):  # (the printer writes the file a moment after the job is handed over)
        if made.exists() and made.stat().st_size > 1000:
            break
        threading.Event().wait(0.2)
    assert made.read_bytes()[:4] == b"%PDF"
    doc = pdfium.PdfDocument(str(made))
    assert len(doc) == 2
    doc.close()


def test_pdf_to_word_is_on_the_wheel_and_dimmed_without_word(tmp_path, monkeypatch):
    tool = next(t for t in converters.catalog()["pdf"] if t.key == "pdf:word")
    assert tool.target == ".docx"
    monkeypatch.setattr(converters, "word_installed", lambda: False)
    assert converters.check_word([tmp_path / "a.pdf"]) == "Needs Microsoft Word"
    with pytest.raises(RuntimeError):
        converters.pdf_to_word(tmp_path / "a.pdf", _ctx(tmp_path))


def test_pdf_to_word_script_leaves_words_settings_alone():
    s = converters.PDF_TO_WORD_SCRIPT
    assert "HKCU" not in s and "ItemProperty" not in s  # (the box is answered instead: officewatch)
    assert "SaveAs2($env:CF_OUT, 16)" in s


def _box_answered(reflow, uia=None):
    """A real message box (OK / Cancel) shown by a new process, and what the watch pressed on it."""
    import subprocess
    import sys

    from corefold import officewatch
    exe = Path(sys.executable).name.lower()
    watch = officewatch.Watch(reflow=tuple(r.replace("*", exe) for r in reflow), names=(exe,), uia=uia)
    try:
        r = subprocess.run([sys.executable, "-c", "import ctypes; print(ctypes.windll.user32.MessageBoxW("
                            "0, 'Word will now convert your PDF', 'Microsoft Word', 1))"],
                           capture_output=True, text=True, timeout=30)
    finally:
        watch.stop()
    return r.stdout.strip(), watch


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' message boxes")
def test_word_conversion_box_is_answered_ok():
    pressed, watch = _box_answered(("*",))
    assert pressed == "1" and watch.answered == 1 and not watch.asked


def test_office_draws_its_own_boxes_too():
    # (Office's own "NUIDialog" boxes have no button windows: they're pressed through UI Automation. Checked by
    # hand with Word's real "caused a serious error" box: turned down in about a second)
    from corefold import officewatch
    assert "NUIDialog" in officewatch.BOX_CLASSES and "#32770" in officewatch.BOX_CLASSES


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' message boxes")
def test_other_office_boxes_are_cancelled_and_said():
    pressed, watch = _box_answered(())
    assert pressed == "2" and watch.asked


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' message boxes")
def test_a_word_the_person_opened_is_left_alone():
    """A Word started while Corefold converts, with a window on screen, is the person's: its "Save your changes?"
    must never be answered for them (No would throw their changes away)."""
    import subprocess

    from corefold import officewatch
    exe = Path(sys.executable).name.lower()
    watch = officewatch.Watch(reflow=(), names=(exe,))
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            subprocess.run([sys.executable, "-c", "import tkinter, ctypes; r = tkinter.Tk(); r.title('Document1'); "
                            "r.update(); print(ctypes.windll.user32.MessageBoxW(0, 'Save your changes?', "
                            "'Microsoft Word', 3))"], capture_output=True, text=True, timeout=6)
    finally:
        watch.stop()
    assert not watch.asked and watch.answered == 0
