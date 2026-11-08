"""Rakiza Core Files is one tool: its page is one workspace (fileworkspace.py), and over each of its tools' pages
a line leads back to it ("Files › Rename"), with the tool's settings. Nothing of a tool is made before it's
opened."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon

from corefold import settings as settings_mod
from corefold import tools as all_tools

FEATURES = ["Converter", "PDF", "Archives", "Organizer", "Unlock", "Rename", "Check", "Backup"]


@pytest.fixture
def window(qtbot):
    from corefold import app as A
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = False
    s.tools_off = []
    s.last_page = "Home"
    s.nav_compact = False
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


def test_every_files_use_has_a_name_and_a_few_words():
    sec = all_tools.SECTION_BY_PAGE["Files"]
    assert sec.one_tool == "Rakiza Core Files"
    for tool_id in sec.tools:
        tool = all_tools.BY_ID[tool_id]
        if tool.ready:
            assert tool.use and len(tool.use) <= 12, tool.id
            assert tool.short and len(tool.short) <= 40, tool.id


def test_the_files_page_is_its_workspace(window):
    from corefold.fileworkspace import FilesWorkspace
    w = window
    w.open_page("Files")
    assert isinstance(w.stack.currentWidget(), FilesWorkspace)
    assert not w.one_head.isVisible() and not w.section_strip.isVisible()  # (it has its own heading)
    for name in FEATURES[1:]:  # (the Converter's page is always made: other parts of the window use it)
        assert not w.built(name), name  # (nothing of a tool is made before it's opened)


def test_a_line_back_to_files_over_each_tool(window):
    from corefold.atomic.widgets import GlowPanel
    from PySide6.QtWidgets import QLabel
    w = window
    for name in [f for f in FEATURES if f != "Organizer"]:  # (resting: not shown)
        w.open_page(name)
        assert w.one_head.isVisible(), name
        tool = all_tools.BY_PAGE[name]
        assert w.one_head.name.text() == (tool.use or tool.nav), name
        page = w.stack.currentWidget()
        assert not [g for g in page.findChildren(GlowPanel) if g.isVisible()], name
        assert not [lab for lab in page.findChildren(QLabel) if lab.objectName() == "h1" and lab.isVisible()], name
    w.one_head.crumb.click()
    assert w.current_page() == "Files"


def test_the_heading_goes_with_the_files_pages(window):
    w = window
    w.open_page("Archives")
    assert w.one_head.isVisible()
    for name in ("Home", "History", "General", "Media", "Files"):
        w.open_page(name)
        assert not w.one_head.isVisible(), name
    w.open_page("Unlock")
    assert w.one_head.isVisible() and w.one_head.name.text() == all_tools.BY_ID["unlock"].use


def test_cloud_has_a_place_of_its_own(window, qtbot):
    from corefold.window import NAV_OWNER
    w = window
    assert "Clouds" in w.nav_rows and NAV_OWNER["Cloud"] == "Clouds"
    w.nav.setCurrentRow(w.nav_rows.index("Clouds"))  # (its row in the sidebar: the Cloud itself, with all the room)
    assert w.current_page() == "Cloud"
    assert not w.one_head.isVisible() and not w.section_strip.isVisible()
    w.c.set_tool("cloud", False)
    assert w.current_page() == "Clouds"  # (switched off: where it's switched on again)
    w.c.set_tool("cloud", True)
    qtbot.waitUntil(lambda: w.current_page() == "Cloud", timeout=2000)


def test_settings_on_the_heading(window):
    w = window
    w.open_page("Converter")
    w.one_head.gear.click()
    assert w.current_page() == all_tools.BY_ID["converter"].settings  # (the open tool's own settings)
    w.open_page("Unlock")
    assert not w.one_head.gear.isVisible()  # (it has none)


def test_the_pdf_editor_gets_the_room(window, tmp_path, qtbot):
    from pypdf import PdfWriter
    w = window
    doc = tmp_path / "two.pdf"
    wr = PdfWriter()
    wr.add_blank_page(300, 400)
    wr.add_blank_page(300, 400)
    wr.write(str(doc))
    w.open_page("PDF")
    assert w.one_head.isVisible()
    w.pdf_page.open_editor(doc)  # (it looks at the PDF first, in the background)
    qtbot.waitUntil(lambda: w.pdf_page.editor is not None and w.pdf_page.stack.currentWidget() is w.pdf_page.editor,
                    timeout=10000)
    assert not w.one_head.isVisible()  # (the editor has its own bar and list of pages)
    w.pdf_page.editor.close_editor(ask=False)
    assert w.one_head.isVisible()


def test_window_made_with_a_files_page_open_has_the_same_tab_order(qtbot):
    """The window can be made with a Files page open (the page last open): the heading is made before the
    top bar then, and Tab still goes from the top bar to the heading and then into the page."""
    from PySide6.QtWidgets import QApplication

    from corefold import app as A
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = False
    s.tools_off = []
    s.last_page = "Converter"
    s.nav_compact = True
    s.save()
    c = A.Controller(A.QApplication.instance(), QIcon())
    c.apply_tools()
    w = c.window
    w.resize(1040, 700)
    w.show()
    qtbot.waitExposed(w)
    w.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is w)
    try:
        assert w.current_page() == "Converter"
        w.lang_btn.setFocus()
        qtbot.keyClick(w.lang_btn, Qt.Key_Tab)
        assert QApplication.focusWidget() is w.one_head.gear
        qtbot.keyClick(w.one_head.gear, Qt.Key_Tab)
        assert QApplication.focusWidget() is w.one_head.crumb
        qtbot.keyClick(w.one_head.crumb, Qt.Key_Tab)
        assert w.stack.currentWidget().isAncestorOf(QApplication.focusWidget())
    finally:
        from PySide6.QtCore import QTimer
        from shiboken6 import delete
        s.last_page = "Home"
        s.nav_compact = False
        s.save()
        c.tray.hide()
        for timer in c.findChildren(QTimer):
            timer.stop()
        if c._window is not None:
            c._window.close()
            delete(c._window)
        delete(c)
