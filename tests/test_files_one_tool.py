"""Corefold Files is one tool: its features down the side of its pages, each with a few words under its name.
The one picked opens beside them, a feature switched off isn't listed (or loaded), and nothing of a feature is
made before it's opened."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon

from corefold import settings as settings_mod
from corefold import tools as all_tools

FEATURES = ["Converter", "PDF", "Archives", "Organizer", "Unlock", "Cloud"]


@pytest.fixture
def window(qtbot):
    from corefold import app as A
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = False
    s.tools_off = []
    s.last_page = "Home"
    s.save()
    c = A.Controller(A.QApplication.instance(), QIcon())
    c.apply_tools()
    w = c.window
    w.resize(1040, 700)
    w.show()
    qtbot.waitExposed(w)
    yield w
    from PySide6.QtCore import QTimer
    from shiboken6 import delete
    c.tray.hide()
    for timer in c.findChildren(QTimer):
        timer.stop()
    if c._window is not None:
        c._window.close()
        delete(c._window)
    delete(c)


def test_every_files_tool_has_a_few_words_for_the_list():
    sec = all_tools.SECTION_BY_PAGE["Files"]
    assert sec.one_tool == "Corefold Files"
    for tool_id in sec.tools:
        tool = all_tools.BY_ID[tool_id]
        if tool.ready:
            assert tool.short and len(tool.short) <= 40, tool.id


def test_the_files_pages_list_the_features_beside_them(window):
    w = window
    w.open_page("Files")
    side = w.feature_side
    assert side.isVisible() and not w.section_strip.isVisible()
    assert list(side.rows) == ["Files", *FEATURES]
    assert side.rows["Files"].property("current")
    for name in FEATURES[1:]:  # (the Converter's page is always made: other parts of the window use it)
        assert not w.built(name), name  # (nothing of a feature is made before it's opened)
    side.rows["PDF"].picked.emit("PDF")
    assert w.current_page() == "PDF" and w.built("PDF")
    assert side.rows["PDF"].property("current") and not side.rows["Files"].property("current")
    assert not w.built("Cloud")


def test_the_list_goes_with_the_files_pages(window):
    w = window
    w.open_page("Archives")
    assert w.feature_side.isVisible()
    for name in ("Home", "History", "General", "Media"):
        w.open_page(name)
        assert not w.feature_side.isVisible(), name
    w.open_page("Cloud")
    assert w.feature_side.isVisible() and w.feature_side.rows["Cloud"].property("current")


def test_a_row_opens_with_the_keyboard(window, qtbot):
    w = window
    w.open_page("Files")
    row = w.feature_side.rows["Unlock"]
    row.setFocus()
    qtbot.keyClick(row, Qt.Key_Return)
    assert w.current_page() == "Unlock"


def test_a_feature_switched_off_leaves_the_list(window):
    w = window
    w.open_page("Organizer")
    w.c.set_tool("organizer", False)
    assert w.current_page() == "Files"
    assert "Organizer" not in w.feature_side.rows and w.feature_side.more is not None
    w.feature_side.more.click()
    assert w.current_page() == "Files"
    w.c.set_tool("organizer", True)
    w.open_page("PDF")
    assert "Organizer" in w.feature_side.rows and w.feature_side.more is None


def test_the_sidebar_makes_room_for_the_list(window, monkeypatch):
    from corefold.window import MainWindow
    w = window
    wide = {"px": 1040}  # (as wide as the window opens the first time; the test screen may be smaller)
    monkeypatch.setattr(MainWindow, "width", lambda self: wide["px"])
    w.open_page("Home")
    w._fit_nav()
    assert not w._compact_now()
    w.open_page("Files")
    assert w._compact_now()  # (icons only, so the features and the page beside them have room)
    w.open_page("Home")
    assert not w._compact_now()
    wide["px"] = 1400
    w.open_page("PDF")
    assert not w._compact_now()
    wide["px"] = 800
    w._fit_nav()
    assert w._compact_now()


def test_a_files_page_says_only_the_features_name(window):
    from PySide6.QtWidgets import QLabel
    w = window
    w.open_page("PDF")
    titles = [lab.text() for lab in w.stack.currentWidget().findChildren(QLabel) if lab.objectName() == "h1"]
    assert titles[:1] == ["PDF"]


def test_the_pdf_editor_gets_the_room(window, tmp_path, qtbot):
    from pypdf import PdfWriter
    w = window
    doc = tmp_path / "two.pdf"
    wr = PdfWriter()
    wr.add_blank_page(300, 400)
    wr.add_blank_page(300, 400)
    wr.write(str(doc))
    w.open_page("PDF")
    before = w.feature_side.narrow  # (icons only already if the test screen is small)
    w.pdf_page.open_editor(doc)  # (it looks at the PDF first, in the background)
    qtbot.waitUntil(lambda: w.pdf_page.editor is not None and w.pdf_page.stack.currentWidget() is w.pdf_page.editor,
                    timeout=10000)
    assert w.feature_side.narrow  # (icons only while editing: the editor's own list of pages has room)
    w.pdf_page.editor.close_editor(ask=False)
    assert w.feature_side.narrow == before
