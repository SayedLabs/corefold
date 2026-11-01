"""Corefold Space: what takes the room by file type, and the map saved as a spreadsheet."""
import csv
from pathlib import Path

import pytest
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QHBoxLayout

from corefold import settings as settings_mod
from corefold import space


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "drive"
    (root / "Videos" / "Trips").mkdir(parents=True)
    (root / "Disks").mkdir()
    (root / "Videos" / "a.MP4").write_bytes(b"v" * 3000)
    (root / "Videos" / "Trips" / "b.mp4").write_bytes(b"v" * 5000)
    (root / "Disks" / "win.iso").write_bytes(b"i" * 9000)
    (root / "README").write_bytes(b"r" * 10)
    (root / "backup.2024-05-01").write_bytes(b"b" * 20)
    return root


def test_type_of():
    assert space.type_of("Clip.MP4") == ".mp4"
    assert space.type_of("archive.tar.gz") == ".gz"
    assert space.type_of("README") == ""
    assert space.type_of(".gitignore") == ""
    assert space.type_of("name.") == ""
    assert space.type_of("backup.2024-05-01") == ""         # (not an extension)
    assert space.type_of("Report v2.final copy") == ""
    assert space.type_of("x.averyveryverylongext") == ""


def test_types_add_up_and_remember_their_biggest_files(tmp_path):
    root = _tree(tmp_path)
    result = space.scan(root, workers=2)
    assert list(result.types) == [".iso", ".mp4", ""]       # biggest first
    assert result.types[".mp4"] == (8000, 2)
    assert result.types[".iso"] == (9000, 1)
    assert result.types[""] == (30, 2)                       # README and backup.2024-05-01
    assert sum(size for size, _ in result.types.values()) == result.root.size
    assert [Path(p).name for _, p in result.biggest_type[".mp4"]] == ["b.mp4", "a.MP4"]


def test_only_the_biggest_types_are_kept(tmp_path, monkeypatch):
    root = tmp_path / "many"
    root.mkdir()
    for i in range(12):
        (root / f"f.t{i}").write_bytes(b"x" * (i + 1))
    monkeypatch.setattr(space, "KEEP_TYPES", 5)
    monkeypatch.setattr(space, "TOP_TYPE", 1)
    result = space.scan(root, workers=2)
    assert list(result.types) == [".t11", ".t10", ".t9", ".t8", ".t7"]
    assert set(result.biggest_type) == set(result.types)


def test_save_csv_lists_folders_then_what_is_in_them(tmp_path):
    root = _tree(tmp_path)
    result = space.scan(root, workers=2)
    out = tmp_path / "map.csv"
    rows = space.save_csv(result, out, ["Path", "Type", "Bytes", "Size", "Files", "Folders", "Share"],
                          "Folder", "File", lambda n: f"{n} B", min_size=1000)
    with open(out, newline="", encoding="utf-8-sig") as f:
        table = list(csv.reader(f))
    assert len(table) == rows + 1 and table[0][0] == "Path"
    paths = [Path(r[0]) for r in table[1:]]
    # the drive, then its biggest folder (Disks), then what's in it, then Videos and its files and folder
    assert paths[0] == root and table[1][1] == "Folder" and table[1][6] == ""
    assert paths[1:3] == [root / "Disks", root / "Disks" / "win.iso"]
    assert table[3][1] == "File" and table[3][2] == "9000" and table[3][3] == "9000 B" and table[3][6] == "100.0"
    assert paths[3:] == [root / "Videos", root / "Videos" / "a.MP4", root / "Videos" / "Trips",
                         root / "Videos" / "Trips" / "b.mp4"]
    assert root / "README" not in paths                      # (smaller than min_size)
    videos = table[1 + paths.index(root / "Videos")]
    assert videos[2:6] == ["8000", "8000 B", "2", "1"]


@pytest.fixture
def page(qtbot):
    from corefold import app as A
    from corefold.spacepage import SpacePage
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = False
    s.save()
    c = A.Controller(A.QApplication.instance(), QIcon())
    p = SpacePage(c, QHBoxLayout())
    qtbot.addWidget(p)
    yield p
    from PySide6.QtCore import QTimer
    from shiboken6 import delete
    c.tray.hide()
    for timer in c.findChildren(QTimer):
        timer.stop()
    delete(c)


def test_file_types_list_and_its_biggest_files(page, tmp_path):
    from corefold.spacepage import TYPE
    root = _tree(tmp_path)
    page.root = root
    page._scanned(space.scan(root, workers=2))
    page._list_mode("types")
    names = [page.items.topLevelItem(i).text(0) for i in range(page.items.topLevelItemCount())]
    assert names == [".iso   ·   1 file", ".mp4   ·   2 files", "No extension   ·   2 files"]
    mp4 = page.items.topLevelItem(1)
    assert mp4.data(0, TYPE) == ".mp4"
    page._list_open(mp4)                                     # (a double-click)
    assert page.list_mode == "big" and page.big_type == ".mp4"
    assert page.mode_box.currentText() == "Biggest: .mp4"
    files = [page.items.topLevelItem(i).text(0) for i in range(page.items.topLevelItemCount())]
    assert files == ["b.mp4", "a.MP4"]
    page._mode_picked(page.modes.index("big"))               # every kind again
    assert page.big_type is None and page.items.topLevelItemCount() == 5


def test_save_button_writes_the_map(page, qtbot, tmp_path, monkeypatch):
    from corefold import spacepage
    root = _tree(tmp_path)
    page.root = root
    page._scanned(space.scan(root, workers=2))
    out = tmp_path / "saved.csv"
    monkeypatch.setattr(spacepage.QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    told = []
    monkeypatch.setattr(page.c.toast, "notify", lambda *a, **k: told.append(a))
    page.save_btn.click()
    qtbot.waitUntil(lambda: bool(told), timeout=5000)
    assert told[0][1] == "saved.csv"
    assert out.read_text("utf-8-sig").splitlines()[0].startswith("Path,Type,Size in bytes")
