"""The sidebar: Home, the sections in use (the open one with its tools right under it), one row for the
sections resting for now, and History, Settings and About at the bottom (Updates is with About)."""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QIcon, QMouseEvent

from corefold import settings as settings_mod
from corefold import tools as all_tools
from corefold.navbar import BADGE_ROLE, DOT, KIND_ROLE, OPEN_ROLE


@pytest.fixture
def window(qtbot):
    from corefold import app as A
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = False
    s.tools_off = []
    s.section_order = []
    s.nav_compact = False
    s.last_page = "Home"
    s.save()
    c = A.Controller(A.QApplication.instance(), QIcon())
    c.apply_tools()
    w = c.window
    w._nav_tight = lambda width: False  # (the names show, whatever room the test screen has)
    w.resize(1040, 700)
    w.show()
    qtbot.waitExposed(w)
    w._fit_nav(animate=False)
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


def shown(w) -> list:
    return [w.nav_rows[r] for r in range(w.nav.count()) if not w.nav.isRowHidden(r)]


def test_the_rows_in_order(window):
    w = window
    rows = shown(w)
    assert rows[0] == "Home" and rows[1] is None  # (a line under Home)
    assert rows[-3:] == ["History", "General", "About"] and rows[-4] is None  # (the room above them)
    assert "Updates" not in w.nav_rows
    for sec in all_tools.SECTIONS:
        assert (sec.page in w.nav_rows) != all_tools.section_paused(sec), sec.id
    assert ("Soon" in w.nav_rows) == any(all_tools.section_paused(sec) for sec in all_tools.SECTIONS)
    # on Home, every section is shut: none of the tools show
    assert not any(w.nav.item(r).data(KIND_ROLE) == "tool" for r in range(w.nav.count()) if not w.nav.isRowHidden(r))


def test_bottom_rows_sit_at_the_bottom(window):
    w = window
    about = w.nav.visualItemRect(w.nav.item(w.nav_rows.index("About")))
    assert w.nav.viewport().height() - about.bottom() < 6


def test_files_is_one_row_with_its_tools_in_its_workspace(window):
    """(Files' tools are in its workspace: no long list of them in the sidebar; its row is lit on any of them.)"""
    w = window
    files_row = w.nav_rows.index("Files")
    assert w.nav.item(files_row).data(KIND_ROLE) == "page"
    for name in ("Converter", "PDF", "Archives", "Rename", "Check", "Backup", "Unlock"):
        assert name not in w.nav_rows, name
        w.open_page(name)
        assert w.current_page() == name and w.nav.currentRow() == files_row, name
    w.nav.setCurrentRow(w.nav_rows.index("Home"))
    w.nav.setCurrentRow(files_row)
    assert w.current_page() == "Files"


def test_a_tool_lights_its_own_row_under_its_open_section(window):
    w = window
    w.open_page("Space")
    row = w.nav_rows.index("Space")
    assert w.current_page() == "Space" and w.nav.currentRow() == row and not w.nav.isRowHidden(row)
    computer = w.nav.item(w.nav_rows.index("Computer"))
    assert computer.data(OPEN_ROLE)
    # a click on another tool's row opens it; on the section's row, its start page
    w.nav.setCurrentRow(w.nav_rows.index("Apps"))
    assert w.current_page() == "Apps"
    w.nav.setCurrentRow(w.nav_rows.index("Computer"))
    assert w.current_page() == "Computer"
    # another section: the Computer tools go
    w.open_page("PDF")
    assert w.nav.isRowHidden(w.nav_rows.index("Space"))
    assert not computer.data(OPEN_ROLE)
    assert not w.section_strip.isVisible()


def test_icons_only_shows_the_sections_and_the_bars(window):
    w = window
    w.open_page("Space")
    w.set_nav_compact(True, animate=False)
    assert all(w.nav.isRowHidden(r) for r in range(w.nav.count()) if w.nav.item(r).data(KIND_ROLE) == "tool")
    assert w.nav.currentRow() == w.nav_rows.index("Computer") and w.current_page() == "Space"
    assert w.section_strip.isVisible()
    w.open_page("PDF")
    assert not w.section_strip.isVisible() and w.current_page() == "PDF"
    assert w.nav.currentRow() == w.nav_rows.index("Files")
    w.set_nav_compact(False, animate=False)
    assert w.nav.currentRow() == w.nav_rows.index("Files")


def test_resting_sections_and_updates(window):
    w = window
    resting = [sec for sec in all_tools.SECTIONS if all_tools.section_paused(sec)]
    if resting:
        w.open_page(resting[0].page)
        assert w.current_page() == "Soon" and w.nav.currentRow() == w.nav_rows.index("Soon")
    w.open_page("Updates")
    assert w.current_page() == "Updates" and w.nav.currentRow() == w.nav_rows.index("About")
    w.c.update_waiting = lambda: True
    w.c.update_info = type("Info", (), {"version": "9.9.9"})()
    w.show_update_marks()
    assert w.nav.item(w.nav_rows.index("About")).data(BADGE_ROLE) == DOT
    w.open_page("About")
    assert w.about_update_btn.isVisible() and "9.9.9" in w.about_update_title.text()


def test_sections_move_from_their_menu(window):
    w = window
    order = [r for r in w.nav_rows if r in all_tools.SECTION_BY_PAGE]
    w.open_page("Space")
    w._move_section(order[0], 1)
    after = [r for r in w.nav_rows if r in all_tools.SECTION_BY_PAGE]
    assert after[:2] == [order[1], order[0]]
    assert w.current_page() == "Space" and w.nav.currentRow() == w.nav_rows.index("Space")
    assert w.c.settings.section_order[:2] == [all_tools.SECTION_BY_PAGE[p].id for p in after[:2]]


def test_a_tool_switched_off_leaves_the_sidebar(window):
    w = window
    w.c.set_tool("uninstaller", False)
    assert "Apps" not in w.nav_rows
    w.c.set_tool("uninstaller", True)
    assert "Apps" in w.nav_rows


def test_tab_with_names_showing_goes_from_the_heading_into_the_page(window, qtbot):
    """(Over a Files tool's page: the way back to Files, then its Settings, then the page.)"""
    from PySide6.QtWidgets import QApplication
    w = window
    w.activateWindow()
    w.open_page("Converter")
    w.lang_btn.setFocus()
    qtbot.keyClick(w.lang_btn, Qt.Key_Tab)
    assert QApplication.focusWidget() is w.one_head.gear
    qtbot.keyClick(w.one_head.gear, Qt.Key_Tab)
    assert QApplication.focusWidget() is w.one_head.crumb
    qtbot.keyClick(w.one_head.crumb, Qt.Key_Tab)
    assert w.stack.currentWidget().isAncestorOf(QApplication.focusWidget())


def test_an_open_section_shuts_and_opens_again(window, qtbot):
    """(2.0.1: a section that's open could only be shut by going to another page.)"""
    w = window
    w.open_page("Computer")
    computer_row = w.nav_rows.index("Computer")
    computer = w.nav.item(computer_row)
    assert computer.data(OPEN_ROLE) and not w.nav.isRowHidden(w.nav_rows.index("Space"))
    # its row clicked again: it shuts, the page stays
    rect = w.nav.visualItemRect(computer)
    qtbot.mouseClick(w.nav.viewport(), Qt.LeftButton, pos=rect.center())
    assert not computer.data(OPEN_ROLE) and w.nav.isRowHidden(w.nav_rows.index("Space"))
    assert w.current_page() == "Computer" and w.nav.currentRow() == computer_row
    # and again: it opens
    qtbot.mouseClick(w.nav.viewport(), Qt.LeftButton, pos=rect.center())
    assert computer.data(OPEN_ROLE) and not w.nav.isRowHidden(w.nav_rows.index("Space"))


def test_the_arrow_opens_and_shuts_without_leaving_the_page(window, qtbot):
    w = window
    w.open_page("Space")
    computer = w.nav.item(w.nav_rows.index("Computer"))
    rect = w.nav.visualItemRect(computer)
    arrow = rect.center()
    arrow.setX(rect.right() - 14)
    qtbot.mouseClick(w.nav.viewport(), Qt.LeftButton, pos=arrow)
    # shut while one of its tools is open: the section's row is lit for it
    assert not computer.data(OPEN_ROLE) and w.nav.isRowHidden(w.nav_rows.index("Space"))
    assert w.current_page() == "Space" and w.nav.currentRow() == w.nav_rows.index("Computer")
    qtbot.mouseClick(w.nav.viewport(), Qt.LeftButton, pos=arrow)
    assert computer.data(OPEN_ROLE) and w.nav.currentRow() == w.nav_rows.index("Space")
    # another section's arrow: it opens (one at a time), still on PDF
    other = w.nav.item(w.nav_rows.index("Files"))
    if other.data(KIND_ROLE) == "section":
        r2 = w.nav.visualItemRect(other)
        p2 = r2.center()
        p2.setX(r2.right() - 14)
        qtbot.mouseClick(w.nav.viewport(), Qt.LeftButton, pos=p2)
        assert other.data(OPEN_ROLE) and not computer.data(OPEN_ROLE) and w.current_page() == "Space"
        assert w.nav.currentRow() == w.nav_rows.index("Computer")


def test_left_and_right_keys_shut_and_open(window, qtbot):
    w = window
    w.open_page("Computer")
    computer = w.nav.item(w.nav_rows.index("Computer"))
    w.nav.setFocus()
    qtbot.keyClick(w.nav, Qt.Key_Left)
    assert not computer.data(OPEN_ROLE)
    qtbot.keyClick(w.nav, Qt.Key_Right)
    assert computer.data(OPEN_ROLE)


def test_sections_are_dragged_into_a_new_order(window, qtbot):
    w = window
    order = [r for r in w.nav_rows if r in all_tools.SECTION_BY_PAGE]
    assert len(order) >= 2
    w.open_page("Space")  # (the first section open: its tools come along with it)
    w._move_section("Computer", -order.index("Computer"))  # (the section with tools under it goes first)
    order = [r for r in w.nav_rows if r in all_tools.SECTION_BY_PAGE]
    first = all_tools.SECTION_BY_PAGE[order[0]]
    assert w.nav.item(w.nav_rows.index(order[0])).data(KIND_ROLE) == "section", first.id
    src = w.nav.visualItemRect(w.nav.item(w.nav_rows.index(order[0]))).center()
    vp = w.nav.viewport()
    qtbot.mousePress(vp, Qt.LeftButton, pos=src)

    def move(pt):
        ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(pt), vp.mapToGlobal(QPointF(pt)), Qt.NoButton,
                         Qt.LeftButton, Qt.NoModifier)
        w.nav.mouseMoveEvent(ev)

    move(src + QPoint(0, 12))
    assert w.nav.drag_row >= 0
    # (its tools folded away while it's carried: the second section is right under it now)
    row = next(r for r in range(w.nav.count()) if w.nav.item(r).data(Qt.UserRole) == order[1])
    second = w.nav.visualItemRect(w.nav.item(row))
    move(QPoint(src.x(), second.bottom() - 2))
    qtbot.mouseRelease(vp, Qt.LeftButton, pos=QPoint(src.x(), second.bottom() - 2))
    after = [r for r in w.nav_rows if r in all_tools.SECTION_BY_PAGE]
    assert after[:2] == [order[1], order[0]]
    assert w.c.settings.section_order[:2] == [all_tools.SECTION_BY_PAGE[p].id for p in after[:2]]
    # the page stays, its tools are back under their section (moved with it), and nothing was opened
    assert w.current_page() == "Space" and w.nav.currentRow() == w.nav_rows.index("Space")
    pdf = w.nav_rows.index("Space")
    assert not w.nav.isRowHidden(pdf) and w.nav_rows.index(order[0]) < pdf
    assert all(w.nav.item(r).data(KIND_ROLE) == "tool" for r in range(w.nav_rows.index(order[0]) + 1, pdf + 1))
    # remembered: a new window has them in this order
    from corefold import tools as T
    assert [s.page for s in T.sections(w.c.settings) if not T.section_paused(s)][:2] == after[:2]


def test_folding_moves_smoothly_and_ends_tidy(window, qtbot):
    """With animations on, the tools fold out over a few frames, and end at their own height (or hidden)."""
    from corefold.navbar import FOLD_MS, FULL_ROLE
    w = window
    w.c.settings.animations = True
    w.nav.animate = True
    w.open_page("Computer")
    qtbot.wait(FOLD_MS + 150)
    files_row = w.nav_rows.index("Computer")
    pdf = w.nav.item(w.nav_rows.index("Space"))
    full = pdf.data(FULL_ROLE)
    w._fold_section(files_row, 0)
    assert w.nav.folding()
    # (on its way in: the rows go from the bottom up, so the section's first tool is cut last. With its 8
    # tools that starts at exactly half the time (OutCubic 0.5 = 7/8 folded), so a fixed wait of half the
    # time read it a frame early or late by chance; it is watched until it's part cut instead)
    qtbot.waitUntil(lambda: w.nav.folding() and 0 < pdf.sizeHint().height() < full, timeout=FOLD_MS * 4)
    qtbot.waitUntil(lambda: not w.nav.folding(), timeout=2000)
    assert w.nav.isRowHidden(w.nav_rows.index("Space")) and pdf.sizeHint().height() == full
    w._fold_section(files_row, 1)
    qtbot.waitUntil(lambda: not w.nav.folding(), timeout=2000)
    assert not w.nav.isRowHidden(w.nav_rows.index("Space")) and pdf.sizeHint().height() == full
    # the last rows stay at the bottom after it
    about = w.nav.visualItemRect(w.nav.item(w.nav_rows.index("About")))
    assert w.nav.viewport().height() - about.bottom() < 6
