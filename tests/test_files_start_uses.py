"""A file dropped on Corefold Files shows everything Files can do with it, not only the Converter's
buttons: a PDF also gets Corefold PDF's own uses (edit it, arrange its pages, make it searchable…), and
anything can be asked "What's using this?" (Corefold Unlock). A use that's switched off isn't offered.
PDFs dropped with pictures can become one PDF, in the order they came."""
import pytest
from PIL import Image
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QLabel, QPushButton

from corefold import converters
from corefold import settings as settings_mod
from corefold.core import Ctx, Options


def _pdf(path, pages=1, size=(300, 400)):
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(*size)
    with open(path, "wb") as fh:
        w.write(fh)
    return path


@pytest.fixture
def files(tmp_path):
    Image.new("RGB", (640, 480), (200, 40, 40)).save(tmp_path / "a photo.jpg")
    Image.new("RGBA", (300, 300), (0, 0, 0, 0)).save(tmp_path / "logo.png")
    _pdf(tmp_path / "letter.pdf", pages=2)
    (tmp_path / "notes.txt").write_text("x")
    return tmp_path


def test_one_pdf_only_for_pdfs_with_pictures(files):
    keys = lambda paths: [s.tool.key for s in converters.pick(paths).slots]  # noqa: E731
    assert "files:pdf" in keys([files / "letter.pdf", files / "a photo.jpg"])
    assert "files:pdf" not in keys([files / "a photo.jpg", files / "logo.png"])  # (pictures have their own PDF)
    assert "files:pdf" not in keys([files / "letter.pdf", files / "notes.txt"])
    assert "files:pdf" not in keys([files / "letter.pdf"])


def test_pdfs_and_pictures_in_the_order_they_came(files):
    from pypdf import PdfReader
    order = [files / "a photo.jpg", files / "letter.pdf", files / "logo.png"]
    out = converters.pdf_and_pictures(order, Ctx(Options())).outputs[0]
    pages = PdfReader(str(out)).pages
    assert len(pages) == 4  # the photo, the letter's two pages, the logo
    assert [round(float(p.mediabox.width)) for p in pages][1:3] == [300, 300]  # (the letter, in the middle)
    assert out.name == "a photo merged.pdf"


@pytest.fixture
def panel(qtbot):
    from corefold import app as A
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = False
    s.tools_off = []
    s.save()
    c = A.Controller(A.QApplication.instance(), QIcon())
    c.apply_tools()
    yield c
    from PySide6.QtCore import QTimer
    from shiboken6 import delete
    c.tray.hide()
    for timer in c.findChildren(QTimer):
        timer.stop()
    if c._window is not None:
        c._window.close()
        delete(c._window)
    delete(c)


def _buttons(c):
    return [b.text() for b in c.window.panel.findChildren(QPushButton) if b.isVisibleTo(c.window.panel)]


def test_a_pdf_gets_every_use(panel, files, monkeypatch):
    c = panel
    c.window.show_files([files / "letter.pdf"])
    texts = _buttons(c)
    for wanted in ("Edit content", "Arrange pages", "Make searchable", "Remove hidden info", "What's using this?"):
        assert wanted in texts, texts
    sections = [lab.text() for lab in c.window.panel.findChildren(QLabel) if lab.objectName() == "section"]
    assert "MORE TOOLS" in sections
    opened = []
    monkeypatch.setattr(type(c.window), "open_page", lambda w, name: opened.append(name))
    page = type("Page", (), {"run": lambda self, key, f: opened.append((key, [p.name for p in f]))})()
    monkeypatch.setattr(c.window, "pdf_page", page, raising=False)
    next(b for b in c.window.panel.findChildren(QPushButton) if b.text() == "Edit content").click()
    assert opened == ["PDF", ("edit", ["letter.pdf"])]


def test_several_pdfs_and_other_files(panel, files):
    c = panel
    c.window.show_files([files / "letter.pdf", _pdf(files / "b.pdf")])
    texts = _buttons(c)
    assert "Make searchable" in texts and "Edit content" not in texts  # (editing is one PDF at a time)
    c.window.show_files([files / "notes.txt"])
    texts = _buttons(c)
    assert "What's using this?" in texts and "Make searchable" not in texts


def test_a_use_switched_off_isnt_offered(panel, files):
    c = panel
    c.settings.tools_off = ["unlock", "pdf"]
    c.window.show_files([files / "letter.pdf"])
    texts = _buttons(c)
    assert "What's using this?" not in texts and "Edit content" not in texts


def test_one_pdf_isnt_in_the_right_click_menu():
    from corefold import shellmenu
    s = settings_mod.Settings()
    for ext in ("*", "", ".pdf", ".jpg"):
        assert "files:pdf" not in [i.key for i in shellmenu.items_for(ext, s)], ext
