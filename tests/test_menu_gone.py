"""A menu whose row goes while it's open (the list filled again under it, as Startup's does when its
icons arrive) is left alone: nothing runs, nothing is said to the menu that's gone."""
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QMenu, QWidget

from corefold import sheet


def test_a_menu_whose_row_went_while_open_does_nothing(qtbot, monkeypatch):
    row = QWidget()
    m = QMenu(row)
    ran = []
    m.addAction("Show in folder", lambda: ran.append(1))

    def pick(at, rows, **kw):
        row.deleteLater()  # (the list is filled again while the menu is open)
        qtbot.waitUntil(lambda: not sheet._alive(m), timeout=2000)
        return rows[0]
    monkeypatch.setattr(sheet, "pick", pick)
    assert sheet.menu(m, QPoint(10, 10)) is None
    sheet.forget(m)  # (already gone: no error)
    assert ran == []


def test_a_menu_still_there_runs_what_was_picked(qtbot, monkeypatch):
    row = QWidget()
    qtbot.addWidget(row)
    m = QMenu(row)
    ran = []
    act = m.addAction("Show in folder", lambda: ran.append(1))
    monkeypatch.setattr(sheet, "pick", lambda at, rows, **kw: rows[0])
    assert sheet.menu(m, QPoint(10, 10)) is act
    sheet.forget(m)
    assert ran == [1]
