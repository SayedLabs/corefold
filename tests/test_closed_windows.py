"""A closed Archive or picture window is let go of at once: it was kept in the list of open windows until the
next one opened, with everything it showed (300,000 entries of a big archive), and every full look of Python's
collector went through all of it."""
import gc
import weakref

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QWidget

from corefold import app as A


class Keeper(QObject):
    _let_go_when_closed = A.Controller._let_go_when_closed
    _forget_closed = A.Controller._forget_closed

    def __init__(self):
        super().__init__()
        self.browsers, self.viewers = [], []


class Entry:
    pass


class Shown(QWidget):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.entries = [Entry() for _ in range(1000)]


def test_a_closed_window_is_let_go_of(qtbot):
    keeper = Keeper()
    archive, picture, other = Shown(), Shown(), Shown()
    keeper.browsers += [archive, other]
    keeper.viewers.append(picture)
    for w in (archive, picture, other):
        keeper._let_go_when_closed(w)
    gone = weakref.ref(archive.entries[0])
    archive.close()
    picture.close()
    del archive, picture
    qtbot.waitUntil(lambda: keeper.browsers == [other] and not keeper.viewers, timeout=2000)
    gc.collect()
    assert gone() is None  # (what it showed went with it)
    other.close()
    qtbot.waitUntil(lambda: not keeper.browsers, timeout=2000)


def test_the_lists_kept_for_unpacking_stay_small(tmp_path, monkeypatch):
    from corefold import archives as ar
    monkeypatch.setattr(ar, "_listed", {})
    sizes = {"big.zip": 200_000, "flat.zip": 100_000, "small.zip": 10, "tiny.zip": 5}
    monkeypatch.setattr(ar, "_list_entries", lambda src, pw="": [ar.Entry(str(i)) for i in range(sizes[src.name])])
    for name in sizes:
        (tmp_path / name).write_bytes(b"x")
    ar.list_entries(tmp_path / "big.zip")
    assert sum(map(len, ar._listed.values())) == 200_000  # (the newest is kept, however big: unpacking it reads it)
    ar.list_entries(tmp_path / "flat.zip")
    assert [k[0] for k in ar._listed] == [str(tmp_path / "flat.zip")]
    ar.list_entries(tmp_path / "small.zip")
    ar.list_entries(tmp_path / "tiny.zip")
    assert [k[0] for k in ar._listed] == [str(tmp_path / "small.zip"), str(tmp_path / "tiny.zip")]
    ar.forget_list(tmp_path / "small.zip")  # (its window closed)
    assert [k[0] for k in ar._listed] == [str(tmp_path / "tiny.zip")]
    assert len(ar.list_entries(tmp_path / "tiny.zip")) == 5
