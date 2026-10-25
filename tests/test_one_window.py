"""Everything shows inside Corefold's window: questions, dialogs, menus, dropdown lists, tips and moving a
widget on the Home page never open a window of their own (not even one that flashes for a moment)."""
import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QHelpEvent, QIcon, QImage, QWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

from corefold import homelist
from corefold import settings as settings_mod


class Windows(QObject):
    """Notes every window that's shown (a Qt widget that is a window, or a bare QWindow such as a drag picture)."""

    def __init__(self, main: QWidget):
        super().__init__()
        self.main = main  # (Corefold's own window may show, of course: it's the one everything goes in)
        self.seen: list[str] = []

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Show and obj is not self.main and obj is not self.main.windowHandle():
            if isinstance(obj, QWidget) and obj.isWindow():
                if type(obj).__name__ == "Toast" and (obj is not self.main.c.toast or not self.main.isVisible()):
                    return False  # (a message while Corefold is in the tray: in the screen's corner, as it should;
                    # or one of an earlier test's Corefold, whose timers are still going)
                import traceback
                where = [f"{f.name}:{f.lineno}" for f in traceback.extract_stack()[:-1] if "corefold" in f.filename]
                self.seen.append(f"{type(obj).__name__} '{obj.windowTitle() or obj.text() if hasattr(obj, 'text') else ''}' "
                                 f"from {' < '.join(reversed(where[-4:]))}")
            elif isinstance(obj, QWindow) and self.main.isVisible() and \
                    not any(w.windowHandle() is obj for w in QApplication.topLevelWidgets()):  # (a widget's: above)
                self.seen.append(f"{type(obj).__name__} (a window of Qt's own)")
        return False


@pytest.fixture
def window(qtbot, monkeypatch):
    from corefold import app as A
    from corefold import cloudlocal, homewidgets
    monkeypatch.setattr(cloudlocal, "find", lambda: [])
    homewidgets.forget_looks()
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = True  # (the sheets' and widgets' own movement is part of what's checked)
    s.tools_off = []
    s.home_widgets = homelist.default()
    s.save()
    c = A.Controller(A.QApplication.instance(), QIcon())
    c.apply_tools()
    w = c.window
    w.resize(1100, 760)
    c.show_window("Home")
    qtbot.waitExposed(w)
    w.activateWindow()
    qtbot.wait(50)
    if not w.isActiveWindow():  # (a screen-less test run can't always make it the one in use, as clicking does)
        monkeypatch.setattr(type(c.toast), "_home", lambda _t: w if w.isVisible() else None)
    spy = Windows(w)
    QApplication.instance().installEventFilter(spy)
    w.spy = spy
    yield w
    QApplication.instance().removeEventFilter(spy)
    from shiboken6 import delete
    c.tray.hide()
    for timer in c.findChildren(QTimer):
        timer.stop()
    if c._window is not None:
        c._window.close()
        delete(c._window)
    delete(c)


def answer_soon(qtbot, fn, ms=350):
    QTimer.singleShot(ms, fn)


def top_sheet():
    from corefold import sheet
    layers = sheet.open_sheets()
    return layers[-1] if layers else None


def test_a_question_is_a_sheet_in_the_window(window, qtbot):
    from corefold import sheet
    from corefold import tools as all_tools
    seen = {}

    def press_cancel():
        layer = top_sheet()
        seen["layer"] = layer is not None and layer.host is window
        seen["blocked"] = sheet._blocked(window.nav)  # (the window behind it can't be clicked meanwhile)
        QTest.keyClick(QApplication.focusWidget() or window, Qt.Key_Escape)
    answer_soon(qtbot, press_cancel)
    assert window._remove_tool(all_tools.BY_ID["organizer"]) is False  # (Esc: Cancel, nothing switched off)
    assert seen == {"layer": True, "blocked": True}
    qtbot.waitUntil(lambda: not window.findChildren(sheet._Layer), timeout=2000)  # (gone, faded out)

    def press_yes():
        layer = top_sheet()
        yes = next(b for b in layer.dlg.findChildren(type(window.home_edit)) if b.objectName() == "primary")
        yes.click()
    answer_soon(qtbot, press_yes)
    assert sheet.question(window, "Delete?", "For good?") == QMessageBox.Yes
    assert not window.spy.seen


def test_a_dialog_from_the_tray_opens_the_window_with_it_inside(window, qtbot, tmp_path):
    from corefold import dialogs
    picture = tmp_path / "p.png"
    QImage(64, 48, QImage.Format_RGB32).save(str(picture))
    window.hide()
    seen = {}

    def ok():
        layer = top_sheet()
        seen["inside"] = layer is not None and layer.host is window and window.isVisible()
        layer.dlg.ok.click()
    answer_soon(qtbot, ok, 600)
    answer = dialogs.ask("resize", [picture], QIcon(), True)
    assert seen == {"inside": True} and answer
    assert not window.spy.seen


def test_menus_lists_and_tips_stay_in_the_window(window, qtbot):
    from corefold import sheet
    from corefold.atomic.widgets import Dropdown
    frame = window.home_board.frames[0]
    menu = QMenu(frame)
    sizes = menu.addMenu("Size")
    sizes.addAction("Small")
    picked = []
    menu.addAction("Move later", lambda: picked.append("later"))

    def pick_second():
        panel = next(p for p in window.findChildren(sheet._Panel) if p.isVisible())
        QTest.keyClick(panel, Qt.Key_Down)
        QTest.keyClick(panel, Qt.Key_Down)
        QTest.keyClick(panel, Qt.Key_Return)
    answer_soon(qtbot, pick_second, 250)
    sheet.menu(menu, frame.mapToGlobal(QPoint(20, 20)))
    assert picked == ["later"]

    box = Dropdown(window.home_board)
    box.addItems(["One", "Two", "Three"])
    box.show()
    chosen = []
    box.activated.connect(chosen.append)

    def pick_last():
        panel = next(p for p in window.findChildren(sheet._Panel) if p.isVisible())
        QTest.keyClick(panel, Qt.Key_End)
        QTest.keyClick(panel, Qt.Key_Return)
    answer_soon(qtbot, pick_last, 250)
    box.showPopup()
    assert box.currentIndex() == 2 and chosen == [2]

    window.lang_btn.setToolTip("Language")
    at = window.lang_btn.rect().center()
    QApplication.sendEvent(window.lang_btn, QHelpEvent(QEvent.Type.ToolTip, at, window.lang_btn.mapToGlobal(at)))
    tip = getattr(window, "_sheet_tip", None)
    assert tip is not None and tip.parentWidget() is window and tip.text == "Language"
    assert not window.spy.seen


def test_moving_a_widget_on_home_stays_in_the_window(window, qtbot):
    board = window.home_board
    board.set_editing(True)
    qtbot.wait(300)
    first, second = board.frames[0], board.frames[1]
    start = first.geometry().center()
    end = second.geometry().center() + QPoint(0, second.height() // 3)
    board.lift(first, start)  # (what a press and a move on its cover do)
    for i in range(1, 9):
        board.drag_to(start + (end - start) * i / 8)
        qtbot.wait(15)
    assert board.lifted is first and board.order.index(first) == 1  # (the other one made room)
    board.put_down()
    qtbot.wait(400)
    ids = [f.entry["id"] for f in board.frames]
    assert board.lifted is None and ids.index(first.entry["id"]) > ids.index(second.entry["id"])
    assert [e["id"] for e in window.c.settings.home_widgets][:2] == ids[:2]  # (kept in the new order)
    assert not window.spy.seen


def test_whats_new_is_a_page_inside_the_window(window, qtbot, monkeypatch):
    from corefold import releasenotes, sheet
    notes = releasenotes.Notes("9.9.9", parts=[releasenotes.Part("New", ["Everything shows inside the window."])])
    monkeypatch.setattr(releasenotes, "kept", lambda *_a: notes)
    window.c.show_whats_new()
    qtbot.waitUntil(lambda: top_sheet() is not None, timeout=2000)
    assert top_sheet().host is window
    top_sheet().dlg.accept()
    qtbot.waitUntil(lambda: not sheet.open_sheets(), timeout=2000)
    assert not window.spy.seen


def test_no_page_or_change_shows_a_window_of_its_own(window, qtbot):
    """Every page, changing the Home page, a narrow and a wide window, the other look: nothing in them is
    ever shown as a window of its own (a part shown before it's put in its place is one, for a moment)."""
    from corefold.window import PAGES
    for name in PAGES:
        window.open_page(name)
        qtbot.wait(60)
    window.open_page("Home")
    board = window.home_board
    for on in (True, False, True):
        board.set_editing(on)
        qtbot.wait(60)
    first = board.frames[0]
    board.resize_widget(first.entry["id"], "small")
    board.move_by(board.frames[0].entry["id"], 1)
    board.add("shortcut", target="known:documents")
    board.remove(board.frames[-1].entry["id"])
    qtbot.wait(300)
    board.set_editing(False)
    for width in (720, 1300, 1000):
        window.resize(width, 760)
        qtbot.wait(60)
    window.apply_theme(window.c.theme)
    qtbot.wait(100)
    assert not window.spy.seen
