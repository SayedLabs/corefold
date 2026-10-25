"""Copying and moving between clouds: a folder goes across one file at a time (never the whole folder on
this PC at once), keeps its shape, and Move takes away only what got there whole."""
import threading
from pathlib import Path

from corefold import cloud


def _local(folder: Path) -> cloud.Client:
    folder.mkdir(parents=True, exist_ok=True)
    return cloud.client(cloud.Account("folder", "", extra={"local": str(folder)}))


def test_a_folder_goes_across_one_file_at_a_time(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "Trip" / "Day 1").mkdir(parents=True)
    for name, data in (("Trip/notes.txt", b"n"), ("Trip/Day 1/photo.jpg", b"p" * 5000), ("Trip/Day 1/v.mp4", b"v")):
        (a / name).write_bytes(data)
    src, dst = _local(a), _local(b)
    held, most = [], [0]
    real = dst.upload

    def upload(path, folder, progress=None, cancel=None):  # (how many temporary copies are there right now?)
        held.append(path)
        most[0] = max(most[0], len([p for p in path.parent.iterdir()]))
        return real(path, folder, progress, cancel)
    monkeypatch.setattr(dst, "upload", upload)
    trip = next(e for e in src.list(None) if e.name == "Trip")
    assert cloud.copy_tree(src, trip, dst, None, cancel=threading.Event()) == 3
    assert (b / "Trip" / "Day 1" / "photo.jpg").read_bytes() == b"p" * 5000
    assert (b / "Trip" / "notes.txt").read_bytes() == b"n"
    assert most[0] == 1  # (one file at a time)
    assert not any(p.exists() for p in held)  # (each temporary copy gone once it's up)


def test_a_copy_can_be_stopped(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "x").mkdir(parents=True)
    (a / "x" / "f.txt").write_text("1")
    src, dst = _local(a), _local(b)
    stop = threading.Event()
    stop.set()
    try:
        cloud.copy_tree(src, src.list(None)[0], dst, None, cancel=stop)
        raise AssertionError("it should have stopped")
    except cloud.CloudError:
        pass
    assert not (b / "x" / "f.txt").exists()


def test_select_all_takes_in_the_rows_still_to_come(qtbot):
    """Select all in a huge folder stays quick: the rows still to come are selected as they arrive."""
    from PySide6.QtWidgets import QTreeWidgetItem, QWidget
    from corefold import cloudpage
    page = QWidget()
    qtbot.addWidget(page)
    page._round, page._later = 1, [str(i) for i in range(5, 12)]
    page._add_rows = lambda part: page.files.addTopLevelItems([QTreeWidgetItem([p]) for p in part])
    page.finish_rows = lambda: cloudpage.CloudPage.finish_rows(page)
    page._more_rows = lambda r, c=3: cloudpage.CloudPage._more_rows(page, r, c)
    page.files = cloudpage.Files(page)
    page._add_rows([str(i) for i in range(5)])
    page.files.selectAll()
    assert page.files.all_picked and len(page.files.selectedItems()) == 5
    page._more_rows(1)
    assert len(page.files.selectedItems()) == 8  # (the new rows came in selected)
    page.files.topLevelItem(0).setSelected(False)  # (changed by hand: what comes later isn't picked)
    assert not page.files.all_picked
    page._more_rows(1)
    assert len(page.files.selectedItems()) == 7
