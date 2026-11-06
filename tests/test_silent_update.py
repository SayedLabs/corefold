"""Updates without Windows' question each time: what Rakiza Core reads from the update task (it gives it nothing)."""
import os
import sys
import time

from corefold import updater


def _frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "RakizaCore.exe"))
    (tmp_path / "_update").mkdir()
    return tmp_path / "_update" / "status.txt"


def test_from_source_there_is_no_task(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert updater.silent_status_path() is None
    assert updater.silent_status(0) is None
    assert updater.start_silent_update() is False


def test_status_is_read_next_to_rakiza_core(monkeypatch, tmp_path):
    status = _frozen(monkeypatch, tmp_path)
    assert updater.silent_status_path() == status.resolve()
    since = time.time()
    assert updater.silent_status(since) is None  # (nothing said yet)
    status.write_text("downloading 0.250", "utf-8")
    assert updater.silent_status(since) == "downloading 0.250"
    assert updater.silent_progress(updater.silent_status(since)) == 0.25


def test_what_it_said_last_time_is_ignored(monkeypatch, tmp_path):
    status = _frozen(monkeypatch, tmp_path)
    status.write_text("ready", "utf-8")
    old = time.time() - 3600
    os.utime(status, (old, old))
    assert updater.silent_status(time.time()) is None


def test_progress():
    assert updater.silent_progress("downloading 0") == 0.0
    assert updater.silent_progress("downloading 7") == 1.0
    assert updater.silent_progress("downloading x") is None
    assert updater.silent_progress("ready") is None
    assert updater.silent_progress(None) is None
