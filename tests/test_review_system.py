"""The app's own parts (settings, history, names of new files, quitting) checked after the 1.10 review."""
import gc
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtWidgets import QWidget

from corefold import core, history, report, settings, shellmenu, trust
from corefold.core import Ctx, Options, unique_path


def test_a_name_is_free_again_once_its_job_is_over(tmp_path):
    ctx = Ctx(Options())
    first = ctx.out_path(tmp_path / "x.jpg", "photo.jpg")
    assert first.name == "photo.jpg"
    # while the job runs, a second job never gets the same name (nothing is written yet)
    assert unique_path(tmp_path / "photo.jpg").name == "photo (2).jpg"
    del ctx
    gc.collect()
    # the job is over (cancelled, or its file deleted since): the next one gets the plain name again
    other = Ctx(Options())
    assert other.out_path(tmp_path / "x.jpg", "photo.jpg").name == "photo.jpg"


def test_the_file_cache_answers_even_when_cleared_meanwhile(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")

    def fn():
        core.clear_cache()  # (another thread tidying up right then)
        return 42
    assert core._cached("test", f, fn) == 42


def test_settings_that_cant_be_read_are_never_written_over(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings.time, "sleep", lambda s: None)
    monkeypatch.setattr("corefold.settings_backup.config_dir", lambda: tmp_path)
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"language": "fr", "first_run": False}), "utf-8")
    real = Path.read_text

    def locked(self, *a, **k):
        if self == target:
            raise PermissionError("in use")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", locked)
    s = settings.Settings.load()
    assert settings.UNREADABLE and not s.first_run  # (not a first start: no questions)
    s.save()
    monkeypatch.setattr(Path, "read_text", real)
    assert json.loads(target.read_text("utf-8"))["language"] == "fr"  # the person's own settings are still there
    s = settings.Settings.load()
    assert not settings.UNREADABLE and s.language == "fr"


def test_history_that_cant_be_read_is_kept(tmp_path, monkeypatch):
    path = tmp_path / "history.json"
    path.write_text(json.dumps({"items": [{"when": 1.0, "tool": "Old", "inputs": [], "outputs": []}],
                                "stats": {"jobs": 5}}), "utf-8")
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError()))
    h = history.History(path)
    out = tmp_path / "b.png"
    out.write_bytes(b"x")
    h.record("New", [tmp_path / "a.png"], [out], 0, 10)
    monkeypatch.setattr(Path, "read_text", real)
    data = json.loads(path.read_text("utf-8"))
    assert [e["tool"] for e in data["items"]] == ["Old"]  # nothing written over it while it couldn't be read
    h.record("Newer", [tmp_path / "a.png"], [out], 0, 10)
    data = json.loads(path.read_text("utf-8"))
    assert [e["tool"] for e in data["items"]] == ["Newer", "New", "Old"] and data["stats"]["jobs"] == 7


def test_space_saved_only_counts_smaller_files_of_the_same_kind(tmp_path):
    h = history.History(tmp_path / "history.json")
    small = tmp_path / "small.jpg"
    small.write_bytes(b"x" * 10)
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x" * 10)
    h.record("Smaller", [tmp_path / "big.jpg"], [small], 0, 1000)
    assert h.stats.bytes_saved == 990
    h.record("Song from a video", [tmp_path / "film.mp4"], [song], 0, 10**9)
    h.record("Some failed", [tmp_path / "a.jpg", tmp_path / "b.jpg"], [small], 1, 1000)
    assert h.stats.bytes_saved == 990


def test_report_hides_a_user_name_with_a_space():
    text = report.mask(r"C:\Users\John Smith\Documents\x.txt", ["John Smith"])
    assert "John" not in text and "Smith" not in text


def test_a_broken_download_note_doesnt_stop_a_scan(tmp_path, monkeypatch):
    if sys.platform != "win32":
        monkeypatch.setattr(trust.sys, "platform", "win32")
    exe = tmp_path / "setup.exe"
    exe.write_bytes(b"MZ")
    with open(str(exe) + ":Zone.Identifier", "wb") as fh:
        fh.write(b"\xff\xfe" + "[ZoneTransfer]\r\nZoneId=3\r\n".encode("utf-16-le") + b"\x00")  # (odd length)
    origin = trust.downloaded_from(str(exe))
    assert origin is not None and origin.zone == 3


def test_each_kind_of_document_gets_its_own_right_click_menu(monkeypatch):
    asked = []
    monkeypatch.setattr(shellmenu, "items_for", lambda ext, s: asked.append(ext) or [])
    shellmenu.plan(settings.Settings(), lambda *a: " ".join(a))
    assert {".doc", ".odt", ".xlsx", ".pptx"} <= set(asked)  # (Word, LibreOffice, Excel, PowerPoint)


class _Stubborn(QWidget):
    """A window with unsaved work whose person picks Cancel."""
    def closeEvent(self, e):
        e.ignore()


def _controller(**extra):
    base = dict(_window=None, save_timer=mock.MagicMock(isActive=lambda: False), cancels={}, pool=mock.MagicMock(),
                quick_pool=mock.MagicMock(), clip=None, capture=None, colors=None, quick=None, awake=None, peek=None,
                tray=mock.MagicMock(), server=None, app=mock.MagicMock(), session_ending=False)
    base.update(extra)
    return SimpleNamespace(**base)


def test_cancel_on_unsaved_work_keeps_corefold_running(qtbot):
    from corefold.app import Controller
    w = _Stubborn()
    qtbot.addWidget(w)
    w.show()
    c = _controller()
    c.pdf_work_ok = lambda win: True
    assert not Controller.unsaved_work_ok(c)
    assert w.isVisible()


def test_quitting_never_waits_on_a_window(qtbot):
    from corefold.app import Controller
    w = _Stubborn()
    qtbot.addWidget(w)
    w.show()
    c = _controller()
    Controller.quit(c)
    # (Qt only asks the windows still shown: one that said no would turn the quit down after everything stopped)
    assert not w.isVisible() and c.app.quit.called and c.session_ending


def test_unsaved_pdf_work_is_asked_about(qtbot):
    from corefold.app import Controller
    page = QWidget()
    qtbot.addWidget(page)
    page.editor = None
    page.unsaved = lambda: True
    page.close_board = lambda ask=True: False  # (the person picks Cancel)
    assert not Controller.pdf_work_ok(None, SimpleNamespace(pdf_page=page))
    page.close_board = lambda ask=True: True
    assert Controller.pdf_work_ok(None, SimpleNamespace(pdf_page=page))
