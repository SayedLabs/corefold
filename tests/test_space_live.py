"""Rakiza Core Space: finding files by name on the map, the map following the disk as it changes, and moving
a file to the Recycle Bin with what goes with it."""
import os
import shutil
import struct
import sys
import time
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
    (root / "Many").mkdir()
    (root / "Videos" / "Trip to Oman.MP4").write_bytes(b"v" * 3000)
    (root / "Videos" / "Trips" / "trip-notes.txt").write_bytes(b"t" * 50)
    (root / "Disks" / "win.iso").write_bytes(b"i" * 9000)
    for i in range(space.TOP_FILES + 20):  # (more than the map gives a box of their own)
        (root / "Many" / f"part{i:03}.dat").write_bytes(b"d" * (i + 1))
    (root / "Many" / "hidden gem.txt").write_bytes(b"g")  # (the smallest: never one of the biggest)
    return root


def _same(a: space.Scan, b: space.Scan):
    """Two maps of the same disk say the same (sizes, counts, kinds, types, lists)."""
    assert a.root.size == b.root.size
    assert (a.files, a.folders) == (b.files, b.folders)
    assert {k: v for k, v in a.kinds.items() if v} == {k: v for k, v in b.kinds.items() if v}
    assert a.types == b.types
    assert a.biggest == b.biggest
    assert a.biggest_type == b.biggest_type
    assert {k: v for k, v in a.biggest_kind.items() if v} == {k: v for k, v in b.biggest_kind.items() if v}

    def walk(x, y):
        assert (x.name, x.size, x.files, x.folders) == (y.name, y.size, y.files, y.folders)
        assert x.own_files() == y.own_files()
        assert [c.name for c in x.children] == [c.name for c in y.children]
        for cx, cy in zip(x.children, y.children):
            walk(cx, cy)
    walk(a.root, b.root)


def _read_everything(result: space.Scan) -> bool:
    folders, jobs, todo = {}, [], [result.root]
    while todo:
        f = todo.pop()
        todo += f.children
        folders[str(f.path)] = f
        jobs.append((str(f.path), {c.name for c in f.children}))
    return space.apply_changes(result, space.read_again(jobs), folders)


# ---- every file, not only the biggest

def test_every_file_is_kept_but_the_map_boxes_only_the_biggest(tmp_path):
    root = _tree(tmp_path)
    result = space.scan(root, workers=2)
    many = next(c for c in result.root.children if c.name == "Many")
    assert len(many.own_files()) == space.TOP_FILES + 21
    assert len(many.big) == space.TOP_FILES and many.big[0] == (space.TOP_FILES + 20, f"part{space.TOP_FILES + 19:03}.dat")
    assert many.rest_count == 21 and many.rest == sum(range(1, 21)) + 1
    assert many.own_size() == many.size


def test_find_looks_through_every_name(tmp_path):
    root = _tree(tmp_path)
    result = space.scan(root, workers=2)
    rows, total = space.find(result, "GEM")
    assert total == 1 and rows == [(1, str(root / "Many" / "hidden gem.txt"), None)]
    rows, total = space.find(result, "trip")               # files and folders, biggest first
    assert [Path(p).name for _, p, _ in rows] == ["Trip to Oman.MP4", "Trips", "trip-notes.txt"]
    assert rows[1][2] is not None and rows[1][2].name == "Trips"
    rows, _ = space.find(result, "oman trip")              # every word, any order
    assert [Path(p).name for _, p, _ in rows] == ["Trip to Oman.MP4"]
    rows, total = space.find(result, "*.iso")
    assert total == 1 and Path(rows[0][1]).name == "win.iso"
    rows, total = space.find(result, "part0??.dat", limit=5)
    assert total == 68 and len(rows) == 5 and rows[0][0] == 68
    assert space.find(result, "   ") == ([], 0)
    assert space.find(result, "nothing like this") == ([], 0)


# ---- following the disk

def test_changes_read_again_match_a_new_look(tmp_path):
    root = _tree(tmp_path)
    result = space.scan(root, workers=2)
    (root / "Videos" / "Trip to Oman.MP4").write_bytes(b"v" * 7000)          # grew
    (root / "Many" / "part000.dat").unlink()                                  # gone
    shutil.rmtree(root / "Disks")                                             # a folder gone
    (root / "New" / "Deep").mkdir(parents=True)                               # new folders, files in them
    (root / "New" / "Deep" / "film.mkv").write_bytes(b"f" * 20000)
    (root / "Videos" / "Trips" / "trip-notes.txt").rename(root / "Videos" / "Trips" / "notes.md")
    assert _read_everything(result)
    _same(result, space.scan(root, workers=2))
    assert not _read_everything(result)                                       # nothing more to do


def test_forget_takes_it_off_exactly(tmp_path):
    root = _tree(tmp_path)
    result = space.scan(root, workers=2)
    disks = next(c for c in result.root.children if c.name == "Disks")
    shutil.rmtree(root / "Disks")
    space.forget(result, root / "Disks", disks)
    (root / "Many" / "hidden gem.txt").unlink()
    space.forget(result, root / "Many" / "hidden gem.txt", None)
    _same(result, space.scan(root, workers=2))


def test_windows_list_of_changes_names_the_folders():
    def record(name, nxt):
        raw = name.encode("utf-16-le")
        body = struct.pack("<III", nxt, 1, len(raw)) + raw
        return body + b"\0" * ((-len(body)) % 4)
    first = record("Users\\Sam\\a.txt", 0)
    data = record("Users\\Sam\\a.txt", len(first)) + record("top.txt", 0)
    assert space.changed_folders(data) == {os.path.dirname("Users\\Sam\\a.txt"), ""}


def test_folder_at_finds_the_nearest_folder(tmp_path):
    root = _tree(tmp_path)
    result = space.scan(root, workers=2)
    assert space.folder_at(result, "").name == str(root)
    assert space.folder_at(result, os.path.join("Videos", "Trips")).name == "Trips"
    assert space.folder_at(result, os.path.join("Videos", "Not there", "x")).name == "Videos"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows tells what changes")
def test_windows_tells_what_changes(tmp_path):
    root = _tree(tmp_path)
    w = space.Watch(root)
    assert w.start()
    try:
        time.sleep(0.3)
        (root / "Videos" / "Trips" / "new.txt").write_bytes(b"x")
        (root / "Fresh").mkdir()
        deadline = time.time() + 10
        seen: set = set()
        while time.time() < deadline and not {os.path.join("Videos", "Trips"), ""} <= seen:
            changed, lost = w.take()
            assert not lost
            seen |= changed
            time.sleep(0.1)
        assert {os.path.join("Videos", "Trips"), ""} <= seen
    finally:
        w.stop()
    deadline = time.time() + 5
    while w._thread.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    assert not w._thread.is_alive()


class FakeWatch:
    """Stands in for Windows telling what changed."""

    def __init__(self):
        self.alive, self.failed = True, False
        self.changed, self.lost = set(), False

    def take(self):
        out = (self.changed, self.lost)
        self.changed, self.lost = set(), False
        return out

    def note(self, folders, lost=False):
        self.lost = self.lost or lost
        self.changed |= set(folders)

    def stop(self):
        self.alive = False


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
    p.resize(1200, 800)
    p.show()
    yield p
    from PySide6.QtCore import QTimer
    from shiboken6 import delete
    c.tray.hide()
    for timer in c.findChildren(QTimer):
        timer.stop()
    delete(c)


def _open(page, root):
    page.root = root
    page._scanned(space.scan(root, workers=2))
    if page.scan.watch is None:
        page.scan.watch = FakeWatch()
    page._show_live()
    return page.scan


def test_search_box_lists_and_shows_on_the_map(page, qtbot, tmp_path):
    root = _tree(tmp_path)
    _open(page, root)
    page.find_box.setText("gem")
    qtbot.waitUntil(lambda: page.list_mode == "found", timeout=5000)
    assert page.mode_box.currentText() == "Found: 1"
    item = page.items.topLevelItem(0)
    assert item.text(0).startswith("hidden gem.txt")
    page._list_open(item)                                       # (a double-click: where it is on the map)
    assert page.here.name == "Many"
    assert page.treemap.mark[1] == "hidden gem.txt"
    assert page.list_mode == "found"                           # (the results stay)
    page.find_box.setText("nothing like this")
    qtbot.waitUntil(lambda: page.found is not None and page.found[0] == "nothing like this", timeout=5000)
    assert page.items.topLevelItem(0).text(0) == "Nothing on this map has that in its name."
    page.find_box.clear()
    qtbot.waitUntil(lambda: page.found is None, timeout=5000)
    assert page.list_mode == "here" and "found" not in page.modes


def test_map_follows_the_disk(page, qtbot, tmp_path):
    root = _tree(tmp_path)
    s = _open(page, root)
    page.open_folder(next(c for c in s.root.children if c.name == "Disks"))
    before = page.items.topLevelItemCount()
    (root / "Disks" / "new.iso").write_bytes(b"n" * 4000)
    s.watch.note({"Disks"})
    page._live_tick()
    qtbot.waitUntil(lambda: page.items.topLevelItemCount() == before + 1, timeout=5000)
    assert page.here.size == 13000 and s.root.size == space.scan(root, workers=2).root.size
    # the folder open goes away: the map steps up to the nearest one still there
    shutil.rmtree(root / "Disks")
    s.watch.note({""})
    page._live_tick()
    qtbot.waitUntil(lambda: page.here is s.root, timeout=5000)
    _same(s, space.scan(root, workers=2))


def test_search_follows_the_disk_too(page, qtbot, tmp_path):
    root = _tree(tmp_path)
    s = _open(page, root)
    page.find_box.setText("holiday")
    qtbot.waitUntil(lambda: page.list_mode == "found", timeout=5000)
    (root / "Videos" / "holiday.mp4").write_bytes(b"h" * 10)
    s.watch.note({"Videos"})
    page._live_tick()
    qtbot.waitUntil(lambda: page.found is not None and page.found[2] == 1, timeout=5000)


def test_windows_losing_count_makes_the_map_again_quietly(page, qtbot, tmp_path):
    root = _tree(tmp_path)
    s = _open(page, root)
    page.open_folder(next(c for c in s.root.children if c.name == "Videos"))
    (root / "Videos" / "more.mp4").write_bytes(b"m" * 100)
    s.watch.note((), lost=True)
    page._live_tick()
    qtbot.waitUntil(lambda: page.scan is not s, timeout=10000)
    assert page.here.name == "Videos" and page.scan.watch is not None and s.watch is None
    _same(page.scan, space.scan(root, workers=2))


def test_live_badge_and_maps_let_go_stop_watching(page, tmp_path):
    root = _tree(tmp_path)
    s = _open(page, root)
    assert not page.live_badge.isHidden()
    w = s.watch
    page._drop_map(next(iter(page.kept)))
    assert not w.alive and s.watch is None


# ---- with what goes with it

def test_related_files(tmp_path):
    d = tmp_path / "films"
    d.mkdir()
    for n in ("Movie.mkv", "Movie.srt", "Movie.en.srt", "Movie.nfo", "Movie.mkv.part", "Movie.jpg", "Other.srt",
              "~$Report.docx", "Report.docx"):
        (d / n).write_bytes(b"x" * 10)
    found = {r.path.name: r for r in space.related(d / "Movie.mkv")}
    assert set(found) == {"Movie.srt", "Movie.en.srt", "Movie.nfo", "Movie.mkv.part", "Movie.jpg"}
    assert all(found[n].sure for n in ("Movie.srt", "Movie.en.srt", "Movie.nfo", "Movie.mkv.part"))
    assert not found["Movie.jpg"].sure                       # (only might belong to it: not ticked)
    assert [r.path.name for r in space.related(d / "Report.docx")] == ["~$Report.docx"]


def test_related_never_offers_what_windows_or_programs_need(tmp_path, monkeypatch):
    d = tmp_path / "films"
    d.mkdir()
    (d / "Movie.mkv").write_bytes(b"x")
    (d / "Movie.srt").write_bytes(b"x")
    monkeypatch.setattr(space, "protected", lambda p: Path(p).name == "Movie.srt")
    assert space.related(d / "Movie.mkv") == []


def test_related_app_data_folders(tmp_path, monkeypatch):
    from corefold import uninstall as un
    roaming, local = tmp_path / "Roaming", tmp_path / "Local"
    for place in (roaming, local):
        (place / "CoolEditor").mkdir(parents=True)
        (place / "CoolEditor" / "settings.json").write_bytes(b"{}")
        (place / "Logs").mkdir()
    monkeypatch.setattr(space, "_app_data_places", lambda: [roaming, local])
    monkeypatch.setattr(un, "_safe_folder", lambda p: True)
    monkeypatch.setattr(space, "own_windows_folder", lambda p: False)
    found = space.related(roaming / "CoolEditor")
    assert [r.path for r in found] == [local / "CoolEditor"] and not found[0].sure and found[0].why == "app_data"
    assert space.related(roaming / "Logs") == []             # (a name too common to tell)

    class App:
        location = str(local / "CoolEditor")
    assert space.related(roaming / "CoolEditor", [App()]) == []  # (an installed program lives there)


def test_related_sheet_ticks_and_cancels(qtbot, tmp_path, monkeypatch):
    from corefold import relatedview
    d = tmp_path / "films"
    d.mkdir()
    for n in ("Movie.mkv", "Movie.srt", "Movie.jpg"):
        (d / n).write_bytes(b"x" * 10)
    monkeypatch.setattr(relatedview, "find_related", lambda p: space.related(p))
    dlg = relatedview.RelatedDialog(None, d / "Movie.mkv", 10)
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg.go_btn.isEnabled(), timeout=5000)
    assert dlg.tree.topLevelItemCount() == 3
    assert dlg.count.text().startswith("2 items")
    dlg._go()
    assert dlg.result() == relatedview.RelatedDialog.Accepted and dlg.chosen == [d / "Movie.srt"]
    dlg2 = relatedview.RelatedDialog(None, d / "Movie.mkv", 10)
    qtbot.addWidget(dlg2)
    qtbot.waitUntil(lambda: dlg2.go_btn.isEnabled(), timeout=5000)
    dlg2.reject()
    assert dlg2.chosen == [] and dlg2.result() != relatedview.RelatedDialog.Accepted


def test_remove_with_related_files_from_the_map(page, qtbot, tmp_path, monkeypatch):
    from corefold import relatedview
    root = _tree(tmp_path)
    (root / "Videos" / "Trip to Oman.srt").write_bytes(b"s" * 20)
    s = _open(page, root)

    class Picked:
        Accepted = relatedview.RelatedDialog.Accepted

        def __init__(self, _page, path, _size):
            self.chosen = [path.with_suffix(".srt")]

        def deleteLater(self):
            pass
    monkeypatch.setattr(relatedview, "RelatedDialog", Picked)
    monkeypatch.setattr("corefold.sheet.run", lambda dlg: Picked.Accepted)
    said = []
    monkeypatch.setattr(page.c, "say", lambda *a, **k: said.append(a))
    page._remove_related(root / "Videos" / "Trip to Oman.MP4", None)
    qtbot.waitUntil(lambda: bool(said), timeout=10000)
    assert not (root / "Videos" / "Trip to Oman.MP4").exists() and not (root / "Videos" / "Trip to Oman.srt").exists()
    _same(s, space.scan(root, workers=2))
