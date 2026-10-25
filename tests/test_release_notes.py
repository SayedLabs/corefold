"""What's new and the Version history: the notes kept with Corefold match its releases, are shown as plain
text only, and "What's new" comes once after an update (never after a new install)."""
import re
from unittest import mock

import pytest

from corefold import releasenotes, updater
from corefold import settings as settings_mod


def test_every_kept_version_has_its_date_and_words():
    history = releasenotes.history()
    assert len(history) >= 19
    versions = [n.version for n in history]
    assert versions == sorted(versions, key=releasenotes.version_key, reverse=True)
    for notes in history:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", notes.date), notes.version
        assert not notes.empty(), notes.version
    assert releasenotes.kept("1.5.4").tag == "1.5.4"  # (that release's tag has no "v")
    assert [(n.version, n.date, n.tag) for n in releasenotes.heads()] == \
        [(n.version, n.date, n.tag) for n in history]  # (the quick list says the same)
    assert releasenotes.release_page("SayedLabs/corefold", releasenotes.kept("1.10.0")) == \
        "https://github.com/SayedLabs/corefold/releases/tag/v1.10.0"


def test_notes_are_plain_text_whatever_the_release_page_holds():
    body = ("## Fixes\n- <img src=x onerror=alert(1)>Safer **files** [here](http://evil.example)\n"
            "<script>bad()</script>\n- &lt;b&gt; stays words\n-\n* Another\n")
    notes = releasenotes.parse(body, "9.9.9", "2030-01-02")
    lines = [ln for p in notes.parts for ln in p.lines]
    assert notes.parts[0].heading == "Fixes"
    assert lines[0] == "Safer files here"
    assert all("<" not in ln or ln == "<b> stays words" for ln in lines)
    assert "http" not in " ".join(lines) and "script" not in " ".join(p.heading for p in notes.parts).lower()
    assert len(releasenotes.parse("- x\n" * 50_000, "1.0.0").parts[0].lines) < 10_000  # (never a whole book)


def test_a_language_without_its_own_notes_reads_english():
    assert releasenotes.kept("1.10.0", "fr").parts == releasenotes.kept("1.10.0").parts
    assert releasenotes.kept("../1.10.0") is None and releasenotes.kept("1.10.0/..") is None


def test_the_update_check_brings_the_notes_and_day():
    data = {"tag_name": "v9.0.0", "html_url": "https://github.com/SayedLabs/corefold/releases/tag/v9.0.0",
            "published_at": "2030-05-06T07:08:09Z", "body": "New\n- A thing",
            "assets": []}
    info = updater.parse_release(data, "SayedLabs/corefold")
    assert info.published == "2030-05-06" and info.notes == "New\n- A thing"
    data["html_url"] = "https://evil.example/x"
    assert updater.parse_release(data, "SayedLabs/corefold").page_url.startswith("https://github.com/SayedLabs/")


@pytest.fixture
def controller(qtbot, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from shiboken6 import delete

    from corefold import app as A
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = False
    s.save()
    c = A.Controller(A.QApplication.instance(), QIcon())
    yield c
    c.tray.hide()
    for timer in c.findChildren(QTimer):
        timer.stop()
    if c._window is not None:
        c._window.close()
        delete(c._window)
    delete(c)


def test_whats_new_comes_once_after_an_update(controller, monkeypatch, qtbot):
    from corefold import app as A
    monkeypatch.setattr(A, "__version__", "1.10.0")
    c = controller
    c.settings.notes_shown = "1.9.0"
    c.updated_from("1.9.0")
    assert c.whats_new_waiting
    c.show_window("Home")
    qtbot.waitUntil(lambda: getattr(c, "whats_new", None) is not None and c.whats_new.isVisible(), timeout=3000)
    assert c.settings.notes_shown == "1.10.0"
    c.whats_new.close()
    c.updated_from("1.9.0")  # (started again: already shown)
    assert not c.whats_new_waiting


def test_an_update_that_didnt_finish_is_said(controller, monkeypatch):
    from corefold import app as A
    c = controller
    monkeypatch.setattr(A, "__version__", "1.10.0")
    said = []
    monkeypatch.setattr(A.QTimer, "singleShot", lambda ms, fn, *a: said.append(fn))
    c.settings.update_to = "1.11.0"
    c.check_update_finished()
    assert c.settings.update_to == "" and len(said) == 1
    c.settings.update_to = "1.10.0"
    c.check_update_finished()
    assert c.settings.update_to == "" and len(said) == 1


def test_the_updates_page_shows_whats_coming_and_the_history(controller):
    c = controller
    w = c.window
    c.update_info = updater.UpdateInfo("9.0.0", "https://github.com/SayedLabs/corefold/releases/tag/v9.0.0",
                                       "Corefold-Setup-9.0.0.exe", "https://x", 1, "https://y",
                                       published="2030-05-06", notes="Fixes\n- <b>Bold</b> fix")
    c.update_state = "available"
    with mock.patch.object(c, "updates_supported", return_value=True):
        w.open_page("Updates")
        w.refresh_updates()
    from PySide6.QtWidgets import QLabel
    texts = [lab.text() for lab in w.stack.currentWidget().findChildren(QLabel)]
    assert "Bold fix" in texts and "Fixes" in texts
    assert all(lab.textFormat() == lab.textFormat().PlainText
               for lab in w.stack.currentWidget().findChildren(QLabel) if lab.text() == "Bold fix")
    assert len(w.version_history.versions()) >= 19
