"""Rakiza Core PDF's reader: PDFs open in tabs to read, with find, select and copy, links, bookmarks at the side,
two pages, dark pages, full screen, and each PDF opening again at the page it was left on."""
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link

from corefold import pdfreader
from corefold.settings import Settings


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


def _with_links_and_marks(src: Path, out: Path) -> Path:
    w = PdfWriter(clone_from=PdfReader(src))
    w.add_annotation(0, Link(rect=(40, 760, 300, 800), target_page_index=2))
    w.add_annotation(0, Link(rect=(40, 600, 300, 640), url="https://example.com/"))
    first = w.add_outline_item("Start", 0)
    w.add_outline_item("Inside", 1, parent=first)
    w.add_outline_item("End", 2)
    w.write(out)
    return out


def _words(src: Path, index: int) -> dict:
    from corefold import pdftools
    with pdftools.PDFIUM:
        doc = pdftools.open_pdfium(src)
        try:
            return pdfreader.page_words(doc, index)
        finally:
            doc.close()


# ---- what doesn't need a window

def test_pages_one_under_the_other_or_two_side_by_side():
    sizes = [(100, 200), (100, 200), (120, 180)]
    one = pdfreader.layout(sizes)
    assert one == [(-50, 0), (-50, 200 + pdfreader.GAP), (-60, 400 + 2 * pdfreader.GAP)]
    two = pdfreader.layout(sizes, two=True)
    assert two[0] == (-pdfreader.GAP / 2 - 100, 0) and two[1] == (pdfreader.GAP / 2, 0)
    assert two[2] == (-60, 200 + pdfreader.GAP)  # (the last one, alone, in the middle)
    cover = pdfreader.layout(sizes, two=True, cover=True)
    assert cover[0][1] == 0 and cover[1][1] == cover[2][1] == 200 + pdfreader.GAP  # (the cover alone, then 2 and 3)
    rtl = pdfreader.layout(sizes, two=True, rtl=True)
    assert rtl[0] == (pdfreader.GAP / 2, 0) and rtl[1] == (-pdfreader.GAP / 2 - 100, 0)  # (the first on the right)


def test_an_arabic_book_reads_right_to_left(tmp_path):
    from pypdf.generic import NameObject
    w = PdfWriter()
    w.add_blank_page(100, 100)
    w.create_viewer_preferences()
    w._root_object["/ViewerPreferences"][NameObject("/Direction")] = NameObject("/R2L")
    w.write(tmp_path / "ar.pdf")
    assert pdfreader.reads_right_to_left(tmp_path / "ar.pdf")
    w = PdfWriter()
    w.add_blank_page(100, 100)
    w.write(tmp_path / "en.pdf")
    assert not pdfreader.reads_right_to_left(tmp_path / "en.pdf")


def test_letters_words_and_lines():
    words = {"text": "ab cd\r\nef", "boxes": [(0, 0, 5, 10), (5, 0, 5, 10), (10, 0, 3, 10), (13, 0, 5, 10),
                                               (18, 0, 5, 10), None, None, (0, 20, 5, 10), (5, 20, 5, 10)]}
    assert pdfreader.char_at(words, 7, 5) == 1
    assert pdfreader.char_at(words, 100, 5) == -1
    assert pdfreader.char_at(words, 100, 5, near=True) == 5  # (past the end of the line: after its last letter)
    assert pdfreader.word_around(words, 3) == (3, 5)
    rects = pdfreader.line_rects(words, 0, 9)
    assert len(rects) == 2 and rects[0].width() == 23 and rects[1].top() == 20
    assert pdfreader.text_between(words, 0, 9) == "ab cd\nef"


def test_find_counts_line_breaks_as_spaces_and_case():
    text = "Hello world\r\nhello World"
    assert pdfreader.find_in(text, "hello", False) == [0, 13]
    assert pdfreader.find_in(text, "hello", True) == [13]
    assert pdfreader.find_in(text, "world hello", False) == []  # (a line break isn't one space)
    assert pdfreader.find_in(text, "", False) == []


def test_only_web_links_are_opened():
    assert pdfreader.safe_address("https://example.com/a")
    assert pdfreader.safe_address("mailto:me@example.com")
    for bad in ("file:///C:/Windows/System32/calc.exe", "javascript:alert(1)", "C:\\x.exe", "\\\\server\\share",
                "https://a\r\nb"):
        assert not pdfreader.safe_address(bad)


def test_dark_pages_keep_colours():
    import numpy as np
    w, h = 3, 1
    px = np.array([[[255, 255, 255, 255], [0, 0, 0, 255], [220, 30, 30, 255]]], np.uint8)
    out = np.frombuffer(pdfreader.darken(px.tobytes(), w, h, w * 4), np.uint8).reshape(h, w, 4)
    white, black, red = (tuple(int(v) for v in out[0, k, :3]) for k in range(3))
    assert max(white) <= pdfreader.DARK_LOW + 2  # (paper is dark)
    assert min(black) >= pdfreader.DARK_HIGH - 2  # (words are light)
    assert red[0] > red[1] + 40 and red[0] > red[2] + 40  # (red stays red)


def test_the_words_and_links_of_a_page(qapp, tmp_path):
    src = _with_links_and_marks(_pdf(tmp_path / "a.pdf", ["Hello reader, see https://example.org now", "Two",
                                                          "Three"]), tmp_path / "b.pdf")
    words = _words(src, 0)
    assert "Hello reader" in words["text"]
    i = words["text"].index("Hello")
    x, y, bw, bh = words["boxes"][i]
    assert 40 < x < 70 and 40 < y < 80 and bw > 0 and bh > 0  # (top left, y down, in points)
    assert pdfreader.char_at(words, x + bw / 2, y + bh / 2) == i
    kinds = {link["to"] for link in words["links"]}
    assert ("page", 2) in kinds and ("uri", "https://example.com/") in kinds
    assert any(k == "uri" and v.startswith("https://example.org") for k, v in kinds)  # (written in the text)
    page_link = next(link for link in words["links"] if link["to"] == ("page", 2))
    lx, ly, lw, lh = page_link["rect"]
    assert abs(lx - 40) < 1 and abs(ly - (842 - 800)) < 1 and abs(lw - 260) < 1


# ---- the reader itself

def _reader(tmp_path):
    from PySide6.QtWidgets import QWidget
    page = MagicMock()
    page.c.settings = Settings()
    page.c.settings.save = MagicMock()
    page.c.theme.muted = "#777777"
    page.c.theme.text = "#111111"
    page.passwords = {}
    r = pdfreader.PdfReader(page)
    host = QWidget()
    host.resize(1000, 760)
    r.setParent(host)
    r.resize(1000, 760)
    host.show()
    return r, page, host


def _wait_open(qtbot, r, pages: int):
    qtbot.waitUntil(lambda: r.doc is not None and r.doc.ready and len(r.doc.pages) == pages, timeout=10000)


def test_open_read_find_copy_and_close(qapp, qtbot, tmp_path):
    a = _with_links_and_marks(_pdf(tmp_path / "a.pdf", ["Alpha page one", "Beta page two", "Gamma page three"]),
                              tmp_path / "doc.pdf")
    b = _pdf(tmp_path / "other.pdf", ["Other"])
    r, page, host = _reader(tmp_path)
    r.open(a)
    _wait_open(qtbot, r, 3)
    assert r.tabs.count() == 1 and r.mark_tree.topLevelItemCount() == 2  # (Start with Inside under it, End)
    qtbot.waitUntil(lambda: r.doc.pages[0].pm is not None, timeout=10000)  # (the page in sight is drawn)
    r.open(b)
    _wait_open(qtbot, r, 1)
    assert r.tabs.count() == 2
    r.open(a)  # (open already: its tab is shown)
    assert r.tabs.count() == 2 and r.doc.src == a
    # Find: every page's words are read, then the matches counted
    r.show_find()
    r.find_box.setText("page")
    qtbot.waitUntil(lambda: len(r.doc.matches) == 3, timeout=10000)
    assert r.found_label.text()
    r.next_match(1)
    assert r.doc.match_at == 1
    r.hide_find()
    assert not r.doc.matches
    # select a word and copy it
    words = r.doc.words[1]
    i = words["text"].index("Beta")
    r.set_selection((1, i), (1, i + 3))
    assert r.selected_text() == "Beta"
    from PySide6.QtGui import QGuiApplication
    r.copy()
    assert QGuiApplication.clipboard().text() == "Beta"
    # across pages
    j = r.doc.words[2]["text"].index("Gamma")
    r.set_selection((1, i), (2, j + 4))
    assert r.selected_text().startswith("Beta page two") and r.selected_text().endswith("Gamma")
    # a link to a page goes there
    r.follow({"to": ("page", 2), "rect": (0, 0, 1, 1)})
    qtbot.waitUntil(lambda: r.current_page() == 2, timeout=3000)
    # zoom and two pages
    r.set_zoom(2.0)
    assert r.doc.zoom == 2.0 and r.doc.fit == "" and r.zoom_btn.text() == "200%"
    r.zoom_step(1)
    assert r.doc.zoom == 2.5
    r.set_fit("width")
    assert r.doc.fit == "width"
    r.toggle_two()
    assert r.doc.two and r.doc.pages[1].pos().y() == r.doc.pages[0].pos().y()
    r.toggle_dark()
    assert r.dark and page.c.settings.pdf_dark_pages
    qtbot.waitUntil(lambda: any(p.pm_key and p.pm_key[1] for p in r.doc.pages), timeout=10000)
    r.toggle_dark()
    # closing remembers the page
    r.go_to(2)
    qtbot.waitUntil(lambda: r.current_page() == 2, timeout=3000)
    r.close_reader()
    assert r.tabs.count() == 0 and not r.docs
    place = page.c.settings.pdf_places[os.path.normcase(str(a))]
    assert place[0] == 2 and place[3] is True
    r.open(a)
    _wait_open(qtbot, r, 3)
    assert r.doc.start_page == 2 and r.doc.two
    qtbot.waitUntil(lambda: r.current_page() == 2, timeout=3000)
    r.close_reader()
    host.close()


def test_a_web_link_asks_first_and_others_are_refused(qapp, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    r, page, host = _reader(tmp_path)
    opened, asked = [], []
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl", lambda url: opened.append(url.toString()))
    monkeypatch.setattr(pdfreader.sheet, "question", lambda *a, **k: asked.append(a) or QMessageBox.Yes)
    r.follow({"to": ("uri", "file:///C:/Windows/System32/calc.exe"), "rect": (0, 0, 1, 1)})
    assert not opened and not asked and page.c.tell.called
    r.follow({"to": ("uri", "https://example.com/"), "rect": (0, 0, 1, 1)})
    assert asked and opened == ["https://example.com/"]
    host.close()


def test_full_screen_turns_pages_and_gives_the_window_back(qapp, qtbot, tmp_path):
    src = _pdf(tmp_path / "a.pdf", ["One", "Two", "Three"])
    r, page, host = _reader(tmp_path)
    r.open(src)
    _wait_open(qtbot, r, 3)
    r.full_screen()
    full = r.full
    assert full is not None and full.page == 0
    qtbot.waitUntil(lambda: full.pm is not None, timeout=10000)
    full.turn(1)
    assert full.page == 1
    full.turn(10)
    assert full.page == 2
    full.end()
    assert r.full is None
    qtbot.waitUntil(lambda: r.current_page() == 2, timeout=3000)
    r.close_reader()
    host.close()


def test_a_damaged_pdf_closes_its_tab(qapp, qtbot, tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7\nnot really")
    r, page, host = _reader(tmp_path)
    r.closed = MagicMock()
    r.open(bad)
    qtbot.waitUntil(lambda: page.c.tell.called, timeout=10000)
    assert r.tabs.count() == 0
    host.close()


@pytest.mark.parametrize("key", ["read"])
def test_the_read_card_is_first(key):
    from corefold import pdfpage
    assert pdfpage.ACTIONS[0][0] == key and key in pdfpage.GROUPS[0][1]


def test_double_clicked_pdfs_come_to_the_reader():
    from corefold import assoc, ipc
    assert assoc.GROUPS["pdf"].flag == "--read" and assoc.GROUPS["pdf"].extensions == (".pdf",)
    assert ipc.message_for(["--read", r"C:\a.pdf"]) == "read\nC:\\a.pdf".encode("utf-8")


# ---- comments

def _with_comments(src: Path, out: Path) -> Path:
    from pypdf.annotations import FreeText, Highlight, Text
    from pypdf.generic import ArrayObject, FloatObject, NameObject, TextStringObject
    w = PdfWriter(clone_from=PdfReader(src))
    note = Text(rect=(50, 700, 70, 720), text="Check this number")
    note[NameObject("/T")] = TextStringObject("Sara")
    note[NameObject("/M")] = TextStringObject("D:20261108093000+03'00'")
    ref = w.add_annotation(0, note)
    reply = Text(rect=(50, 700, 70, 720), text="Done, fixed")
    reply[NameObject("/T")] = TextStringObject("Omar")
    reply[NameObject("/IRT")] = ref.indirect_reference
    w.add_annotation(0, reply)
    quads = ArrayObject([FloatObject(v) for v in (50, 300, 200, 300, 50, 280, 200, 280)])
    mark = Highlight(rect=(50, 280, 200, 300), quad_points=quads)
    mark[NameObject("/Contents")] = TextStringObject("ملاحظة مهمة")
    w.add_annotation(2, mark)
    w.add_annotation(0, FreeText(text="Typed on the page", rect=(50, 100, 250, 130)))
    w.add_annotation(0, Link(rect=(40, 760, 300, 800), target_page_index=2))  # (a link: not a comment)
    w.write(out)
    return out


def test_comments_are_read_with_their_replies(qapp, tmp_path):
    src = _with_comments(_pdf(tmp_path / "a.pdf", ["One", "Two", "Three"]), tmp_path / "c.pdf")
    found = pdfreader.read_comments(src)
    assert [(c["page"], c["kind"]) for c in found] == [(0, "Note"), (0, "Text"), (2, "Highlight")]
    note = found[0]
    assert note["author"] == "Sara" and note["text"] == "Check this number" and note["date"] == "2026-11-08 09:30"
    assert [r["author"] for r in note["replies"]] == ["Omar"]
    assert found[0]["y"] < found[1]["y"]  # (from the top of the page: the note is above the typed words)
    text = pdfreader.comments_text("c.pdf", found)
    assert "Sara" in text and "      Note · Omar" in text and "ملاحظة مهمة" in text and "3" in text
    w = PdfWriter()
    w.add_blank_page(100, 100)
    w.write(tmp_path / "none.pdf")
    assert pdfreader.read_comments(tmp_path / "none.pdf") == []


def test_the_comments_side_goes_to_each_one_and_saves_them(qapp, qtbot, tmp_path, monkeypatch):
    src = _with_comments(_pdf(tmp_path / "a.pdf", ["One", "Two", "Three"]), tmp_path / "c.pdf")
    plain = _pdf(tmp_path / "plain.pdf", ["Nothing"])
    r, page, host = _reader(tmp_path)
    r.open(src)
    _wait_open(qtbot, r, 3)
    assert r.side_notes_btn.isEnabled() and r.notes_list.count() == 4  # (3 comments and the reply)
    r._side_show(2)
    assert r.side.width() == pdfreader.COMMENTS_W
    r._note_clicked(r.notes_list.item(3))
    qtbot.waitUntil(lambda: r.current_page() == 2, timeout=3000)
    out = tmp_path / "notes.txt"
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    r.save_comments()
    assert "Sara" in out.read_text(encoding="utf-8")
    r.open(plain)  # (a PDF with none: back to the pages, and the tab can't be picked)
    _wait_open(qtbot, r, 1)
    assert not r.side_notes_btn.isEnabled() and r.side_stack.currentIndex() == 0
    assert r.side.width() == pdfreader.THUMB_W + 52
    r.close_reader()
    host.close()


def test_measure_in_centimetres_and_inches():
    assert pdfreader.measure_text(72) == "2.54 cm · 1.00 in"


def test_snapshot_measure_and_read_aloud(qapp, qtbot, tmp_path):
    from PySide6.QtCore import QObject, QRectF, Signal
    from PySide6.QtGui import QGuiApplication
    src = _pdf(tmp_path / "a.pdf", ["Alpha page one", "", "Gamma page three"])
    r, page, host = _reader(tmp_path)
    r.open(src)
    _wait_open(qtbot, r, 3)
    # Snapshot: the part dragged over goes to the clipboard as a sharp picture, then the mode ends
    r.set_mode("snap")
    assert r.view.mode == "snap" and r.mode_bar.isVisibleTo(r)
    img = r.snapshot(0, QRectF(40, 40, 144, 72))
    assert img is not None and (img.width(), img.height()) == (400, 200)
    assert not QGuiApplication.clipboard().image().isNull()
    assert r.view.mode == "" and not r.mode_bar.isVisibleTo(r)
    # Measure: a line on the page with its length; Esc ends the mode before closing the reader
    r.set_mode("measure")
    mark = pdfreader.ToolMark(r.doc.pages[0], "measure", pdfreader.QPointF(10, 10))
    mark.set_end(pdfreader.QPointF(10, 82))
    assert mark.length() == 72
    r.view.mark = mark
    assert r.go_back() and r.view.mode == "" and r.view.mark is None and r.docs

    # Read aloud: page after page (the empty one skipped), then it stops by itself
    class Voice(QObject):
        stateChanged = Signal(object)

        def __init__(self):
            super().__init__()
            self.said = []

        def say(self, text):
            from PySide6.QtTextToSpeech import QTextToSpeech
            self.said.append(text)
            self.stateChanged.emit(QTextToSpeech.State.Ready)  # (a voice changed: not the end of a page)
            self.stateChanged.emit(QTextToSpeech.State.Speaking)
            self.stateChanged.emit(QTextToSpeech.State.Ready)

        def state(self):
            from PySide6.QtTextToSpeech import QTextToSpeech
            return QTextToSpeech.State.Ready

        def availableVoices(self):
            return ["one"]

        def stop(self):
            pass

    pytest.importorskip("PySide6.QtTextToSpeech")
    voice = Voice()
    voice.stateChanged.connect(r._speech_state)
    r.speech = voice
    r.read_aloud(0)
    qtbot.waitUntil(lambda: r.speaking is None, timeout=10000)
    assert len(voice.said) == 2 and "Alpha" in voice.said[0] and "Gamma" in voice.said[1]
    assert not r.speak_bar.isVisibleTo(r)
    r.close_reader()
    host.close()
