"""Rakiza Core Space, much better: the map drawn down to the files, the list beside it with columns that open in
place, how far along a look is, Windows' files with two names counted once, "See what fills this" in File
Explorer, and photos that look alike."""
import os
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QHBoxLayout

from corefold import settings as settings_mod
from corefold import shellmenu, space


def _deep(tmp_path: Path) -> Path:
    """Folders five deep, each with files: the old map stopped three levels down."""
    root = tmp_path / "drive"
    here = root
    for depth in range(5):
        here = here / f"level{depth}"
        here.mkdir(parents=True)
        for i in range(3):
            (here / f"file{i}.bin").write_bytes(b"x" * (2000 + 100 * i))
    (root / "Videos").mkdir()
    (root / "Videos" / "clip.mp4").write_bytes(b"v" * 30000)
    (root / "readme.txt").write_bytes(b"r" * 500)
    return root


# ----------------------------------------------------------------------------- the look itself

def test_files_with_two_names_in_the_component_store_count_once(tmp_path, monkeypatch):
    root = tmp_path / "Windows"
    (root / "System32").mkdir(parents=True)
    (root / "WinSxS" / "amd64_thing").mkdir(parents=True)
    real = root / "System32" / "thing.dll"
    real.write_bytes(b"d" * 5000)
    os.link(real, root / "WinSxS" / "amd64_thing" / "thing.dll")
    (root / "WinSxS" / "amd64_thing" / "only-here.dll").write_bytes(b"o" * 700)   # (one name: counted)
    monkeypatch.setattr(space, "_LINK_DIRS", (os.path.normcase(str(root / "WinSxS")),))
    result = space.scan(root, workers=2)
    assert result.root.size == 5700 and result.linked == 5000
    assert result.files == 2
    sxs = next(c for c in result.root.children if c.name == "WinSxS")
    assert sxs.size == 700
    # and the same when the folder is read again (the map following the disk)
    folders = {str(f.path): f for f in (result.root, sxs, sxs.children[0])}
    jobs = [(p, {c.name for c in f.children}) for p, f in folders.items()]
    space.apply_changes(result, space.read_again(jobs), folders)
    assert result.root.size == 5700


def test_folders_know_when_something_inside_last_changed(tmp_path):
    root = _deep(tmp_path)
    old = time.time() - 400 * 86400
    for p in root.rglob("*"):
        if p.is_file():
            os.utime(p, (old, old))
    new = time.time() - 60
    deepest = root / "level0" / "level1" / "level2" / "level3" / "level4" / "file0.bin"
    os.utime(deepest, (new, new))
    result = space.scan(root, workers=2)
    assert abs(result.root.newest - new) < 300            # (float32: to within a few minutes)
    level0 = next(c for c in result.root.children if c.name == "level0")
    assert abs(level0.newest - new) < 300
    videos = next(c for c in result.root.children if c.name == "Videos")
    assert abs(videos.newest - old) < 300
    assert abs(videos.own_times()["clip.mp4"] - old) < 300
    # a file written later: the folders around it are that new too
    (root / "Videos" / "fresh.mp4").write_bytes(b"f" * 10)
    folders = {str(videos.path): videos}
    space.apply_changes(result, space.read_again([(str(videos.path), set())]), folders)
    assert videos.newest > new - 300 and result.root.newest >= videos.newest


def test_how_much_a_look_will_find(tmp_path):
    root = _deep(tmp_path)
    history = tmp_path / "history.json"
    assert space.expected_size(root, history) == 0              # (a folder never looked at: not known)
    result = space.scan(root, workers=2)
    space.remember(result, history)
    assert space.expected_size(root, history) == result.root.size
    anchor = Path(Path(root).anchor)
    assert space.expected_size(anchor, tmp_path / "none.json") > 0   # (a whole drive: the room used on it)


def test_photos_that_look_alike(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw
    monkeypatch.setattr(space, "SIMILAR_MIN", 1000)   # (these test pictures are small)

    def picture(seed: int) -> Image.Image:
        import random
        rnd = random.Random(seed)
        im = Image.new("RGB", (800, 600), (rnd.randint(0, 255),) * 3)
        d = ImageDraw.Draw(im)
        for _ in range(30):
            x, y = rnd.randint(0, 700), rnd.randint(0, 500)
            d.ellipse((x, y, x + rnd.randint(30, 260), y + rnd.randint(30, 260)),
                      fill=tuple(rnd.randint(0, 255) for _ in range(3)))
        return im
    a = picture(1)
    a.save(tmp_path / "beach.jpg", quality=95)
    a.resize((400, 300)).save(tmp_path / "beach small.jpg", quality=70)   # (resized and recompressed)
    picture(2).save(tmp_path / "city.jpg", quality=90)
    Image.new("RGB", (800, 600), (255, 255, 255)).save(tmp_path / "blank.png")     # (flat: never "alike")
    Image.new("RGB", (800, 600), (250, 250, 250)).save(tmp_path / "blank2.png")
    groups = space.find_similar_photos([tmp_path])
    assert len(groups) == 1
    names = sorted(Path(r[0]).name for r in groups[0])
    assert names == ["beach small.jpg", "beach.jpg"]
    assert all(r[3] > 0 and r[4][:2] == b"\xff\xd8" for r in groups[0])   # (pixels, and a small JPEG)


def test_see_what_fills_this_in_file_explorer():
    from corefold import ipc
    s = settings_mod.Settings()
    cmd = lambda *a: "corefold.exe " + " ".join(a)  # noqa: E731
    mine = [v for v in shellmenu.plan(s, cmd) if v.name == "CorefoldSpace"]
    assert {v.root for v in mine} == {shellmenu.FOLDER_ROOT, shellmenu.DRIVE_ROOT}
    assert all("--space" in v.command and v.single for v in mine)
    assert "CorefoldSpace" in shellmenu.VERBS                 # (taken away with the rest)
    s.tools_off = list(s.tools_off) + ["space"]
    assert not [v for v in shellmenu.plan(s, cmd) if v.name == "CorefoldSpace"]
    s = settings_mod.Settings()
    s.context_menu = False                                     # (no Rakiza Core in the right-click menu at all)
    assert not [v for v in shellmenu.plan(s, cmd) if v.name == "CorefoldSpace"]
    assert ipc.message_for(["--space", "D:\\Photos"]) == b"space\nD:\\Photos"


# ----------------------------------------------------------------------------- the page

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
    p.resize(1300, 800)
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
    return page.scan


def test_map_goes_down_to_the_files(page, qtbot, tmp_path):
    root = _deep(tmp_path)
    _open(page, root)
    qtbot.wait(50)
    m = page.treemap
    assert max(b.level for b in m.boxes) >= 4           # (the old map stopped at three levels)
    deepest = max(m.boxes, key=lambda b: b.level)
    i = m._at(deepest.rect.center())
    assert m.boxes[i] is deepest                         # (the box under the mouse is the innermost one)
    assert "%" in m.tip_text(deepest)
    m.grab()                                             # (drawn once, into a picture kept for the mouse)
    assert m._pix is not None


def test_list_columns_open_in_place_and_ring_on_the_map(page, qtbot, tmp_path):
    from corefold.spacepage import COLUMNS
    root = _deep(tmp_path)
    _open(page, root)
    page._fit_columns()
    items = page.items
    assert items.columnCount() == len(COLUMNS)
    level0 = next(items.topLevelItem(i) for i in range(items.topLevelItemCount())
                  if items.topLevelItem(i).text(0) == "level0")
    assert level0.text(3) == "15"                       # (the files inside it, sub-folders too)
    assert level0.text(4)                               # (when something in it last changed)
    assert level0.childIndicatorPolicy() == level0.ChildIndicatorPolicy.ShowIndicator
    level0.setExpanded(True)
    names = sorted(level0.child(i).text(0) for i in range(level0.childCount()))
    assert names == ["file0.bin", "file1.bin", "file2.bin", "level1"]
    items.setCurrentItem(level0)
    assert page.treemap.selected[0] is not None and page.treemap.selected[0].name == "level0"
    clip = next(it for it in page._all_rows() if it.text(0) == "Videos")
    clip.setExpanded(True)
    items.setCurrentItem(clip.child(0))
    assert page.treemap.selected[1] == "clip.mp4"
    # put in order by name, then by size again (the arrow shows which)
    items.sortItems(0, Qt.AscendingOrder)
    first = [items.topLevelItem(i).text(0) for i in range(items.topLevelItemCount())]
    assert first == sorted(first, key=str.lower)
    items.header().setSortIndicator(1, Qt.DescendingOrder)
    assert items.headerItem().text(1).startswith("↓")


def test_clicking_a_file_on_the_map_picks_its_row(page, qtbot, tmp_path):
    root = _deep(tmp_path)
    _open(page, root)
    box = next(b for b in page.treemap.boxes if b.level == 0 and b.name == "readme.txt")
    qtbot.mouseClick(page.treemap, Qt.LeftButton, pos=box.rect.center().toPoint())
    assert page.items.currentItem() is not None and page.items.currentItem().text(0) == "readme.txt"


def test_delete_asks_and_never_takes_what_windows_needs(page, qtbot, tmp_path, monkeypatch):
    from corefold import spacepage
    root = _deep(tmp_path)
    _open(page, root)
    asked = []
    monkeypatch.setattr(spacepage, "_confirm", lambda *a: asked.append(a) or False)
    row = next(it for it in page._all_rows() if it.text(0) == "readme.txt")
    page.items.setCurrentItem(row)
    page._remove_picked()
    assert asked and (root / "readme.txt").exists()     # (asked first; said no: nothing moved)
    told = []
    monkeypatch.setattr(page.c, "tell", lambda *a: told.append(a))
    monkeypatch.setattr(space, "protected", lambda p: True)
    asked.clear()
    page._remove_picked()
    assert told and not asked


def test_a_look_shows_how_far_along_it_is(page, qtbot, tmp_path):
    page._expect = 1000
    page._scan_started = time.monotonic() - 10
    page._scan_progress((50, 500, "C:\\Users"))
    assert page.spinner.fraction == pytest.approx(0.5)
    assert page.spinner.text == "50%"
    assert "min" in page.busy_eta.text() or "minute" in page.busy_eta.text()


def test_see_what_fills_this_opens_the_map(page, qtbot, tmp_path):
    root = _deep(tmp_path)
    page.show_section("dupes")
    page.look_at(root)
    qtbot.waitUntil(lambda: page.scan is not None and page.job is None, timeout=10000)
    assert page.section == "map" and page.scan.root.path == root


def test_similar_photos_on_the_page_tick_nothing(page, qtbot, tmp_path, monkeypatch):
    rows = [(str(tmp_path / "a.jpg"), 3000, 1.0, 1200, b""), (str(tmp_path / "b.jpg"), 2000, 2.0, 600, b"")]
    page._similar_found([rows])
    head = page.dupe_tree.topLevelItem(0)
    assert head.childCount() == 2
    assert all(head.child(i).checkState(0) == Qt.Unchecked for i in range(2))
    assert "biggest picture" in head.child(0).text(0)
    assert "2" in page.dupe_note.text()


def test_zoom_into_a_folder_and_back(page, qtbot, tmp_path, monkeypatch):
    from corefold.atomic import tokens
    monkeypatch.setattr(tokens, "animations", True)
    root = _deep(tmp_path)
    s = _open(page, root)
    page.treemap.grab()
    level0 = next(c for c in s.root.children if c.name == "level0")
    page.open_folder(level0)
    assert page.treemap._zoom is not None and page.treemap._zoom[-1] == "in"
    page.treemap.grab()                                    # (a frame of it is drawn)
    qtbot.waitUntil(lambda: page.treemap._zoom is None, timeout=3000)
    page._up()
    assert page.treemap._zoom is not None and page.treemap._zoom[-1] == "out"
    qtbot.waitUntil(lambda: page.treemap._zoom is None, timeout=3000)
    assert page.here is s.root
