"""Corefold Files is one tool: one heading over all its pages (its name, a line about the use that's open,
and a bar of its uses), and a start page to drop any file on or pick a use, each with what it's for. A use
switched off has no tab (nor is it loaded), and nothing of a use is made before it's opened."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon

from corefold import settings as settings_mod
from corefold import tools as all_tools

FEATURES = ["Converter", "PDF", "Archives", "Organizer", "Unlock"]


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


def test_every_files_use_has_a_name_and_a_few_words():
    sec = all_tools.SECTION_BY_PAGE["Files"]
    assert sec.one_tool == "Corefold Files"
    for tool_id in sec.tools:
        tool = all_tools.BY_ID[tool_id]
        if tool.ready:
            assert tool.use and len(tool.use) <= 12, tool.id
            assert tool.short and len(tool.short) <= 40, tool.id


def test_one_heading_over_the_files_pages(window):
    from corefold.onetool import UseCard
    w = window
    w.open_page("Files")
    head = w.one_head
    assert head.isVisible() and not w.section_strip.isVisible()
    assert list(head.tabs) == ["Files", *FEATURES]
    assert head.tabs["Files"].isChecked()
    cards = w.stack.currentWidget().findChildren(UseCard)
    assert [c.tool.page for c in cards] == FEATURES
    for name in FEATURES[1:]:  # (the Converter's page is always made: other parts of the window use it)
        assert not w.built(name), name  # (nothing of a use is made before it's opened)
    head.tabs["PDF"].click()
    assert w.current_page() == "PDF" and w.built("PDF")
    assert head.tabs["PDF"].isChecked() and not head.tabs["Files"].isChecked()
    assert head.line.text() == all_tools.BY_ID["pdf"].short
    assert not w.built("Unlock")
    cards[-1].open_use()
    assert w.current_page() == "Unlock"


def test_a_use_has_no_heading_of_its_own(window):
    from corefold.atomic.widgets import GlowPanel
    from PySide6.QtWidgets import QLabel
    w = window
    for name in FEATURES:
        w.open_page(name)
        page = w.stack.currentWidget()
        assert not [g for g in page.findChildren(GlowPanel) if g.isVisible()], name
        assert not [lab for lab in page.findChildren(QLabel) if lab.objectName() == "h1" and lab.isVisible()], name


def test_the_heading_goes_with_the_files_pages(window):
    w = window
    w.open_page("Archives")
    assert w.one_head.isVisible()
    for name in ("Home", "History", "General", "Media"):
        w.open_page(name)
        assert not w.one_head.isVisible(), name
    w.open_page("Unlock")
    assert w.one_head.isVisible() and w.one_head.tabs["Unlock"].isChecked()


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


def test_a_card_opens_with_the_keyboard(window, qtbot):
    from corefold.onetool import UseCard
    w = window
    w.open_page("Files")
    card = next(c for c in w.stack.currentWidget().findChildren(UseCard) if c.tool.page == "Unlock")
    card.setFocus()
    qtbot.keyClick(card, Qt.Key_Return)
    assert w.current_page() == "Unlock"


def test_settings_on_the_heading(window, qtbot):
    from corefold.window import ToolSwitchCard
    w = window
    w.open_page("Converter")
    w.one_head.gear.click()
    assert w.current_page() == all_tools.BY_ID["converter"].settings  # (the open use's own settings)
    w.open_page("Unlock")
    assert not w.one_head.gear.isVisible()  # (it has none)
    w.open_page("Files")
    w.one_head.gear.click()  # (the start page: which uses are switched on)
    qtbot.waitUntil(lambda: bool(w.stack.currentWidget().findChildren(ToolSwitchCard)), timeout=2000)
    w.open_page("PDF")
    w.open_page("Files")
    qtbot.waitUntil(lambda: not w.stack.currentWidget().findChildren(ToolSwitchCard), timeout=2000)


def test_a_use_switched_off_leaves_the_bar(window):
    w = window
    w.open_page("Organizer")
    w.c.set_tool("organizer", False)
    assert w.current_page() == "Files"
    assert "Organizer" not in w.one_head.tabs
    w.c.set_tool("organizer", True)
    w.open_page("PDF")
    assert "Organizer" in w.one_head.tabs


def test_the_bar_fits_a_narrow_window(window):
    w = window
    w.open_page("PDF")
    w.one_head.fit(360)
    named = [b for b in w.one_head.tabs.values() if b.text()]
    assert named == [w.one_head.tabs["PDF"]]  # (the use that's open keeps its name)
    w.one_head.fit(2000)
    assert all(b.text() for b in w.one_head.tabs.values())


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


def test_tab_goes_from_the_top_bar_to_the_heading_then_the_page(window, qtbot):
    from PySide6.QtWidgets import QApplication
    w = window
    for name in ("Converter", "PDF", "Files"):  # (the Converter's page is made before the heading; the others after)
        w.open_page(name)
        w.lang_btn.setFocus()
        qtbot.keyClick(w.lang_btn, Qt.Key_Tab)
        if w.one_head.gear.isVisible():  # (PDF has no settings of its own)
            assert QApplication.focusWidget() is w.one_head.gear, name
            qtbot.keyClick(w.one_head.gear, Qt.Key_Tab)
        assert QApplication.focusWidget() is w.one_head.tabs[name], name  # (the use that's open, not the first)
        qtbot.keyClick(w.one_head.tabs[name], Qt.Key_Tab)
        assert w.stack.currentWidget().isAncestorOf(QApplication.focusWidget()), name
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key_Backtab, Qt.ShiftModifier)
        assert QApplication.focusWidget() is w.one_head.tabs[name], name


def test_arrows_go_along_the_bar_of_uses(window, qtbot):
    from PySide6.QtWidgets import QApplication
    w = window
    w.open_page("Converter")
    w.one_head.tabs["Converter"].setFocus()
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key_Right)
    assert QApplication.focusWidget() is w.one_head.tabs["PDF"] and w.current_page() == "PDF"
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key_Left)
    assert QApplication.focusWidget() is w.one_head.tabs["Converter"] and w.current_page() == "Converter"
