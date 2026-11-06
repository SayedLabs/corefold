"""Rakiza Core Space: a look at a big drive doesn't hold on to memory (2.0.0 kept 1.3 GB after looking through a
drive of millions of files), and the map is let go a while after Space is left."""
import os
import random
import sys
import tracemalloc
from pathlib import Path

import pytest
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QHBoxLayout

from corefold import settings as settings_mod
from corefold import space


def _tree(root: Path, folders: int = 40, files: int = 60, seed: int = 3) -> Path:
    rnd = random.Random(seed)
    for a in range(folders):
        d = root / f"folder {a:02}" / ("deeper" if a % 4 == 0 else "")
        d.mkdir(parents=True, exist_ok=True)
        for b in range(rnd.randint(0, files)):
            ext = rnd.choice([".jpg", ".TXT", ".mp4", "", ".tar.gz", ".dll"])
            size = rnd.choice([0, 10, 10, 1000, 1000, 3 << 20, rnd.randint(0, 1 << 16)])  # (many the same size)
            p = d / f"file {b:03}{rnd.choice('xyz')}{ext}"
            with open(p, "wb") as f:
                f.truncate(size)
            t = rnd.choice([1.0e9, 1.5e9, 1.7e9])
            os.utime(p, (t, t))
    for name in (".hidden", "no ending", "a.b.c", "..x", "trail."):
        (root / name).write_bytes(b"")
    return root


def _flat(root: Path) -> space.Scan:
    """The same map the way 2.0.0 made it: every file in flat lists, put together by space.finish."""
    top = space.Folder(str(root))
    result = space.Scan(top)
    folders, order = [top], [top]
    where, names, sizes, used, changed = [], [], [], [], []
    todo = [(top, str(root))]
    while todo:
        folder, path = todo.pop()
        dirs, n, s, u, c, _cloud, _linked, _denied = space._read_dir(path)
        at = folders.index(folder)
        where += [at] * len(n)
        names += n
        sizes += s
        used += u
        changed += c
        for name in dirs:
            child = space.Folder(name, folder)
            folder.children.append(child)
            folders.append(child)
            order.append(child)
            todo.append((child, os.path.join(path, name)))
    space.finish(result, folders, where, names, sizes, changed, used)
    space.add_up(order)
    result.folders = top.folders
    return result


def test_a_look_makes_the_same_map_as_before(tmp_path):
    root = _tree(tmp_path / "drive")
    a, b = space.scan(root, workers=4), _flat(root)
    assert (a.files, a.folders, a.root.size) == (b.files, b.folders, b.root.size)
    assert {k: v for k, v in a.kinds.items() if v} == {k: v for k, v in b.kinds.items() if v}
    assert a.types == b.types
    assert a.biggest == b.biggest
    assert a.old == b.old
    assert a.biggest_type == b.biggest_type
    assert {k: v for k, v in a.biggest_kind.items() if v} == {k: v for k, v in b.biggest_kind.items() if v}

    def walk(x, y):
        assert (x.name, x.size, x.files, x.folders, x.newest) == (y.name, y.size, y.files, y.folders, y.newest)
        assert (x.names, list(x.sizes), list(x.times)) == (y.names, list(y.sizes), list(y.times))
        assert sorted(c.name for c in x.children) == sorted(c.name for c in y.children)
        theirs = {c.name: c for c in y.children}
        for c in x.children:
            walk(c, theirs[c.name])
    walk(a.root, b.root)


def test_a_look_keeps_no_python_object_for_each_file(tmp_path):
    root = tmp_path / "many"
    for a in range(30):
        d = root / f"folder {a:02}"
        d.mkdir(parents=True)
        for b in range(1000):
            (d / f"a file with a usual long name {b:04}.jpg").write_bytes(b"")
    space.scan(root, workers=2)  # (what's read once: numpy, the modules)
    tracemalloc.start()
    try:
        result = space.scan(root, workers=4)
        _now, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.files == 30_000
    # every file as Python objects (a name, a size, two times, the lists they're in) took over 370 bytes a file
    # at the peak, and Windows kept it while the map was open; now each folder is put in order as it is read
    assert peak / result.files < 220, f"{peak / result.files:.0f} bytes a file at the peak"


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


def test_the_map_is_let_go_a_while_after_space_is_left(page, tmp_path):
    from corefold import spacepage
    root = _tree(tmp_path / "drive", folders=6, files=10)
    page.root = root
    page._scanned(space.scan(root, workers=2))
    assert page.scan is not None and page.kept
    assert not page._forget_timer.isActive()           # (in sight: kept)
    page.hide()                                          # (another page opened, or the window closed)
    assert page._forget_timer.isActive()
    assert page._forget_timer.interval() == spacepage.FORGET_HIDDEN_AFTER * 1000
    page.show()                                          # (back before then: still there, and kept)
    assert not page._forget_timer.isActive() and page.scan is not None
    page.hide()
    page._forget_old_maps()                              # (the time went by)
    assert page.scan is None and not page.kept
    assert page.drives_box.isVisibleTo(page)            # (the drives, the next time Space opens)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' memory counters")
def test_windows_gets_the_memory_back(tmp_path):
    import ctypes
    import gc
    from ctypes import wintypes as wt

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD)] + [
            (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                           "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                           "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                                           "PrivateUsage")]

    def private() -> int:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), wt.DWORD]
        assert psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return pmc.PrivateUsage

    root = tmp_path / "many"
    for a in range(40):
        d = root / f"folder {a:02}"
        d.mkdir(parents=True)
        for b in range(1500):
            (d / f"a file with a usual long name {b:04}.jpg").write_bytes(b"")
    space.scan(root, workers=2)
    gc.collect()
    before = private()
    result = space.scan(root, workers=4)
    del result
    gc.collect()
    kept = private() - before
    assert kept < 60_000 * 120, f"{kept / 60_000:.0f} bytes a file still held after the map was let go"
