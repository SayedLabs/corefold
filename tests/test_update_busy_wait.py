"""An update found while a game is full screen is checked again once later, however many checks find it in the
meantime (each one used to start a 15-minute chain of its own that never ended: "update available" twice every
15 minutes in a log)."""
from types import SimpleNamespace

from PySide6.QtCore import QTimer

from corefold import app as appmod


def _one_shot():
    t = QTimer()
    t.setSingleShot(True)
    return t


def _controller(qtbot, calls):
    return SimpleNamespace(
        settings=SimpleNamespace(auto_update=True, check_updates=True, update_told=""),
        update_info=None, update_state="checking", update_error="", update_retries=0,
        update_busy_wait=_one_shot(), update_retell=_one_shot(),
        can_self_install=lambda: True,
        start_update=lambda: calls.append("start"),
        _refresh_updates_page=lambda: None, _show_update_marks=lambda: None,
        _tell_update=lambda: calls.append("tell"),
    )


def _info(version="9.9.9"):
    return SimpleNamespace(version=version)


def test_checks_that_find_it_again_while_full_screen_keep_one_wait(qtbot, monkeypatch):
    monkeypatch.setattr(appmod.winutil, "user_busy", lambda: True)
    started = []
    monkeypatch.setattr(appmod.QTimer, "singleShot", lambda *a, **k: started.append(a))
    calls = []
    c = _controller(qtbot, calls)
    for _ in range(3):  # (the check at start, the 6-hourly one, one after being offline)
        c.update_state = "checking"
        appmod.Controller._update_checked(c, _info(), "", False)
    assert c.update_busy_wait.isActive()
    assert started == []  # (no loose one-shot timers: the one wait is restarted, never added to)
    assert calls == []  # (nothing installed in the middle of the game)


def test_after_the_game_it_installs(qtbot, monkeypatch):
    calls = []
    c = _controller(qtbot, calls)
    monkeypatch.setattr(appmod.winutil, "user_busy", lambda: False)
    appmod.Controller._update_checked(c, _info(), "", False)
    assert calls == ["start"] and not c.update_busy_wait.isActive()


def test_found_again_is_logged_once(qtbot, monkeypatch, caplog):
    monkeypatch.setattr(appmod.winutil, "user_busy", lambda: True)
    c = _controller(qtbot, [])
    with caplog.at_level("INFO"):
        for _ in range(3):
            appmod.Controller._update_checked(c, _info(), "", False)
        appmod.Controller._update_checked(c, _info("9.9.10"), "", False)
    found = [r.getMessage() for r in caplog.records if "update available" in r.getMessage()]
    assert found == ["update available: 9.9.9", "update available: 9.9.10"]
