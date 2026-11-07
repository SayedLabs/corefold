"""The second full check of every tool (after 2.0.1): what it found, so it stays fixed."""
import os
from pathlib import Path

from corefold import backup


def _w(p: Path, text: str, when: float | None = None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    if when is not None:
        os.utime(p, (when, when))


def test_backup_to_a_drive_that_also_has_a_folder_to_keep(tmp_path):
    """The backup goes in "<PC> backup" on the drive: another folder on that drive isn't inside the backup (it was
    refused as if it were), but a folder inside the backup itself still is, and a backup inside a kept folder."""
    drive = tmp_path / "D"
    photos = drive / "Photos"
    photos.mkdir(parents=True)
    assert backup.problem([str(photos)], str(drive)) == ""
    inside = drive / f"{backup.pc_name()} backup" / "Photos"
    inside.mkdir(parents=True)
    assert backup.problem([str(inside)], str(drive)) == "A folder to keep safe can't be inside the backup"
    assert backup.problem([str(drive)], str(drive)) == "The backup can't go inside a folder it keeps safe"
    assert backup.problem([str(drive)], str(photos)) == "The backup can't go inside a folder it keeps safe"
    # and it really backs up, without copying the backup into itself
    _w(photos / "a.jpg", "a", 1_700_000_000)
    plan = backup.Plan(sources=[str(photos)], dest=str(drive))
    s = backup.run(plan, now=1_800_000_000)
    assert s.copied == 1 and not s.problem
    assert (backup.home(plan) / "Photos" / "a.jpg").read_text() == "a"


def test_backup_clears_a_half_copy_left_by_a_run_that_never_finished(tmp_path):
    docs = tmp_path / "Docs"
    _w(docs / "a.txt", "a", 1_700_000_000)
    dest = tmp_path / "E"
    dest.mkdir()
    plan = backup.Plan(sources=[str(docs)], dest=str(dest))
    backup.run(plan, now=1_800_000_000)
    base = backup.home(plan) / "Docs"
    half = base / "deep" / "big.mov"
    half = half.with_name(half.name + backup.PARTIAL)
    _w(half, "half")  # (the PC went off in the middle of a copy)
    backup.run(plan, now=1_800_090_000)
    assert not half.exists() and not (base / "deep").exists()
    assert (base / "a.txt").read_text() == "a" and (docs / "a.txt").read_text() == "a"


def test_backup_takes_out_folders_left_empty_at_every_level(tmp_path):
    docs = tmp_path / "Docs"
    _w(docs / "a" / "b" / "c" / "x.txt", "x", 1_700_000_000)
    _w(docs / "keep.txt", "k", 1_700_000_000)
    dest = tmp_path / "E"
    dest.mkdir()
    plan = backup.Plan(sources=[str(docs)], dest=str(dest))
    backup.run(plan, now=1_800_000_000)
    (docs / "a" / "b" / "c" / "x.txt").unlink()
    backup.run(plan, now=1_800_090_000)
    base = backup.home(plan) / "Docs"
    assert not (base / "a").exists()  # (not only c: b and a were left empty too)
    (old,) = backup.versions(plan)
    assert (old / "Docs" / "a" / "b" / "c" / "x.txt").read_text() == "x"
