"""Text, Markdown, web pages and tables (CSV) become PDFs without Office: Arabic stays joined and right
to left, a web page's own pictures come along, and nothing is loaded from another computer."""
import pypdfium2 as pdfium
from PIL import Image

from corefold import converters
from corefold.core import Options, category


def _text(pdf):
    doc = pdfium.PdfDocument(str(pdf))
    try:
        out = []
        for page in doc:
            tp = page.get_textpage()
            out.append(tp.get_text_bounded())
            tp.close()
        return out
    finally:
        doc.close()


def _pdf(src):
    tool = next(t for t in converters.catalog()["document"] if t.key == "document:pdf")
    assert tool.check([src]) is None  # (no Office needed)
    result, errors = converters.run_job(tool, [src], Options())
    assert not errors, errors
    return result.outputs[0]


def test_text_files_are_documents(tmp_path):
    for name in ("a.txt", "b.md", "c.html", "d.htm", "e.csv", "f.markdown"):
        assert category(tmp_path / name) == "document"


def test_plain_text_and_arabic(qapp, tmp_path):
    (tmp_path / "notes.txt").write_text("Shopping list\nمرحبا بالعالم\n" + "line\n" * 200, encoding="utf-8")
    pages = _text(_pdf(tmp_path / "notes.txt"))
    words = pages[0].replace("\r\n", "")  # (Qt writes no spaces into the page's text, and splits a line here and there)
    arabic = "مرحبا" in words or "مرحبا"[::-1] in words  # (written in the order it's seen, on Windows)
    assert len(pages) >= 2 and "Shoppinglist" in words and arabic


def test_markdown_and_a_web_page_with_its_picture(qapp, tmp_path):
    (tmp_path / "readme.md").write_text("# Title\n\nSome **bold** words.\n", encoding="utf-8")
    assert "Title" in _text(_pdf(tmp_path / "readme.md"))[0]
    Image.new("RGB", (200, 100), (200, 30, 30)).save(tmp_path / "logo.png")
    (tmp_path / "page.html").write_text("<h1>Hello</h1><img src='logo.png'>"
                                        "<img src='\\\\server\\share\\x.png'><img src='http://example.com/x.png'>",
                                        encoding="utf-8")
    out = _pdf(tmp_path / "page.html")
    assert "Hello" in _text(out)[0]
    doc = pdfium.PdfDocument(str(out))
    try:
        assert sum(1 for o in doc[0].get_objects() if o.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE) == 1
    finally:
        doc.close()


def test_a_table(qapp, tmp_path):
    (tmp_path / "sales.csv").write_text("Name;Total\nAhmed;12\n<b>x</b>;3\n", encoding="utf-8")
    text = _text(_pdf(tmp_path / "sales.csv"))[0]
    assert "Ahmed" in text and "<b>x</b>" in text  # (cells are text, never HTML)


def test_office_files_still_need_office(monkeypatch, tmp_path):
    monkeypatch.setattr(converters, "office_app_for", lambda f: None)
    monkeypatch.setattr(converters, "find_libreoffice", lambda: None)
    monkeypatch.setattr(converters, "_cached", lambda key, p, fn: fn())
    tool = next(t for t in converters.catalog()["document"] if t.key == "document:pdf")
    assert tool.check([tmp_path / "a.docx"]) and tool.check([tmp_path / "a.txt", tmp_path / "b.docx"])
    assert tool.check([tmp_path / "a.txt"]) is None
