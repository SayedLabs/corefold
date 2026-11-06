"""Backup: only what changed is copied, nothing on the PC changes, a changed or deleted file's last copy is kept in
Older versions, and the backup is a plain folder."""
import os
import time
from pathlib import Path

from corefold import backup


def _w(p: Path, text: str, when: float | None = None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    if when is not None:
        os.utime(p, (when, when))


def _plan(tmp_path, *sources) -> backup.Plan:
    dest = tmp_path / "E"
    dest.mkdir(exist_ok=True)
    return backup.Plan(sources=[str(s) for s in sources], dest=str(dest))


def test_first_and_second_run(tmp_path):
    docs = tmp_path / "PC" / "Documents"
    _w(docs / "a.txt", "one", 1_700_000_000)
    _w(docs / "sub" / "b.txt", "two", 1_700_000_000)
    plan = _plan(tmp_path, docs)
    s = backup.run(plan, now=1_800_000_000)
    root = backup.home(plan)
    assert s.copied == 2 and not s.failed and not s.problem
    assert (root / "Documents" / "sub" / "b.txt").read_text() == "two"
    assert backup.run(plan, now=1_800_000_100).copied == 0  # (nothing changed: nothing copied)
    _w(docs / "a.txt", "one, changed", 1_750_000_000)
    (docs / "sub" / "b.txt").unlink()
    s = backup.run(plan, now=1_800_003_600)
    assert s.copied == 1 and s.kept_old == 2
    assert (root / "Documents" / "a.txt").read_text() == "one, changed"
    assert not (root / "Documents" / "sub").exists()
    (old,) = backup.versions(plan)
    assert (old / "Documents" / "a.txt").read_text() == "one"
    assert (old / "Documents" / "sub" / "b.txt").read_text() == "two"
    assert (docs / "a.txt").read_text() == "one, changed"  # (the PC is never touched)


def test_older_versions_are_pruned(tmp_path):
    docs = tmp_path / "Docs"
    _w(docs / "a.txt", "1")
    plan = _plan(tmp_path, docs)
    plan.keep_days = 30
    old = backup.home(plan) / backup.OLDER / "2020-01-01 10-00"
    _w(old / "Docs" / "x.txt", "x")
    keep = backup.home(plan) / backup.OLDER / "not ours"
    keep.mkdir(parents=True)
    backup.run(plan)
    assert not old.exists() and keep.exists()


def test_problems(tmp_path):
    docs = tmp_path / "Docs"
    docs.mkdir()
    assert backup.problem([], str(tmp_path)) == "Add the folders to keep safe"
    assert backup.problem([str(docs)], "") == "Choose where the backup goes"
    assert backup.problem([str(docs)], str(tmp_path / "gone")) == "The backup drive isn't connected"
    assert backup.problem([str(docs)], str(docs / "inside")) == "The backup drive isn't connected"
    (docs / "inside").mkdir()
    assert backup.problem([str(docs)], str(docs / "inside")) == "The backup can't go inside a folder it keeps safe"
    assert backup.problem([str(tmp_path)], str(docs)).startswith("The backup can't")
    assert backup.problem([str(docs)], str(tmp_path / "Docs2")) == "The backup drive isn't connected"


def test_same_names_and_missing_sources(tmp_path):
    a, b = tmp_path / "x" / "Photos", tmp_path / "y" / "Photos"
    _w(a / "1.jpg", "a")
    _w(b / "1.jpg", "b")
    plan = _plan(tmp_path, a, b, tmp_path / "gone")
    s = backup.run(plan)
    names = sorted(p.name for p in backup.home(plan).iterdir())
    assert len(names) == 2 and all(n.startswith("Photos (") for n in names)
    assert s.missing == [str(tmp_path / "gone")] and s.copied == 2


def test_due_and_the_saved_plans(tmp_path):
    docs = tmp_path / "Docs"
    docs.mkdir()
    plan = _plan(tmp_path, docs)
    assert backup.due(plan, now=time.time())
    backup.finished(plan, backup.Summary(copied=3), now=1000.0)
    backup.home(plan).mkdir()
    assert not backup.due(plan, now=1000.0 + 3600) and backup.due(plan, now=1000.0 + 25 * 3600)
    backup.finished(plan, backup.Summary(problem="The backup drive is full"), now=99999.0)
    assert plan.last == 1000.0 and plan.last_problem  # (stopped early: tried again next time)
    again = backup.load(backup.dump([plan]))
    assert again == [plan]
    assert backup.load("not json") == [] and backup.load('[{"sources": [], "nonsense": 1}]')[0].sources == []


def test_a_full_drive_stops(tmp_path, monkeypatch):
    docs = tmp_path / "Docs"
    _w(docs / "a.txt", "1")
    plan = _plan(tmp_path, docs)
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda _p: type("U", (), {"free": 10})())
    s = backup.run(plan)
    assert s.problem == "The backup drive is full" and s.copied == 0
    assert not list(backup.home(plan).rglob("*" + backup.PARTIAL))


def test_the_page(qtbot, tmp_path):
    from unittest import mock
    from corefold.backuppage import BackupPage, plan_of
    from corefold.settings import Settings
    from corefold.theme import build_theme
    from PySide6.QtWidgets import QHBoxLayout
    c = mock.MagicMock()
    c.settings = Settings()
    c.settings.save = lambda: None
    c.theme = build_theme("dark", "teal")
    c.drive_backup_running.return_value = False
    page = BackupPage(c, QHBoxLayout())
    qtbot.addWidget(page)
    assert not page.go.isEnabled()
    docs = tmp_path / "Docs"
    docs.mkdir()
    page.add_folder(docs)
    page.add_folder(docs)  # (once only)
    drive = tmp_path / "E"
    drive.mkdir()
    page.set_dest(drive)
    assert plan_of(c.settings).sources == [str(docs)] and plan_of(c.settings).dest == str(drive)
    assert page.go.isEnabled() and page.folders.count() == 1
    c.keep_drive_backup.assert_called()
    page.back_up()
    c.run_drive_backup.assert_called_with(now=True)
    (docs / "inside").mkdir()
    page.set_dest(docs / "inside")
    assert not page.go.isEnabled()


def test_a_kept_folder_called_older_versions_never_mixes_with_the_backups_own(tmp_path):
    mine = tmp_path / "PC" / "Older versions"
    _w(mine / "2020-01-01 10-00" / "keep me.txt", "mine")
    plan = _plan(tmp_path, mine)
    plan.keep_days = 1
    assert backup.run(plan, now=1_800_000_000).copied == 1
    name = backup.folder_names(plan.sources)[str(mine)]
    assert name.lower() != backup.OLDER.lower()
    backup.run(plan, now=1_900_000_000)  # (long after: dated Older versions folders are let go)
    assert (backup.home(plan) / name / "2020-01-01 10-00" / "keep me.txt").read_text() == "mine"


def test_a_file_now_only_in_the_cloud_keeps_its_copy(tmp_path, monkeypatch):
    docs = tmp_path / "PC" / "OneDrive"
    _w(docs / "a.txt", "one", 1_700_000_000)
    _w(docs / "b.txt", "two", 1_700_000_000)
    plan = _plan(tmp_path, docs)
    assert backup.run(plan, now=1_800_000_000).copied == 2
    real = os.scandir

    class Entry:
        def __init__(self, e):
            self._e, self.name, self.path = e, e.name, e.path

        def is_dir(self, follow_symlinks=True):
            return self._e.is_dir(follow_symlinks=follow_symlinks)

        def stat(self, follow_symlinks=True):
            st = self._e.stat(follow_symlinks=follow_symlinks)
            if self.name != "b.txt" or "OneDrive" not in self.path or "backup" in self.path:
                return st

            class St:
                st_mode, st_size, st_mtime = st.st_mode, st.st_size, st.st_mtime
                st_file_attributes = 0x400000  # (Windows: only in the cloud, read on open)
            return St()

    class Scan:
        def __init__(self, p):
            self._it = real(p)

        def __enter__(self):
            return (Entry(e) for e in self._it.__enter__())

        def __exit__(self, *a):
            return self._it.__exit__(*a)

        def __iter__(self):
            return (Entry(e) for e in self._it)

        def __next__(self):
            return Entry(next(self._it))

        def close(self):
            self._it.close()
    monkeypatch.setattr(backup.os, "scandir", Scan)
    s = backup.run(plan, now=1_800_003_600)
    assert s.kept_old == 0
    assert (backup.home(plan) / "OneDrive" / "b.txt").read_text() == "two"
