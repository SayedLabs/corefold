"""A big archive's contents (what's in which folder, the first folder in order) are worked out with the
listing, away from the window, and a search goes through 100,000 rows a part at a time: both held the
window 0.33-0.4 s."""
from pathlib import Path
from unittest import mock

import pytest

from corefold import archives as ar


@pytest.fixture
def make(qtbot):
    from corefold import browser as B
    c = mock.MagicMock()
    c.theme.dark = False
    c.settings.archive_preview = False

    def make(entries, contents=None):
        b = B.ArchiveBrowser(c, Path("big.zip"), "", entries, B.QIcon(), contents)
        qtbot.addWidget(b)
        return b
    return make


def _shown(b) -> list[str]:
    from corefold.browser import PATH_ROLE
    top = [b.tree.topLevelItem(i) for i in range(b.tree.topLevelItemCount())]
    return [item.data(0, PATH_ROLE) for item in top if not item.isHidden()]


def test_contents_are_worked_out_before_the_window_gets_them(make):
    from corefold.browser import Contents
    entries = [ar.Entry("docs/", is_dir=True, modified="2026-10-01 09:00:00"),
               ar.Entry("docs/report-10.pdf", 7), ar.Entry("docs/report-2.pdf", 5),
               ar.Entry("old.zip", 3, encrypted=True), ar.Entry("b.txt", 1), ar.Entry("a.txt", 2)]
    contents = Contents(entries)
    assert contents.count == 5 and contents.size == 18
    assert contents.locked and contents.nested  # (a locked file; an archive inside)
    assert contents.dir_time == {"docs": "2026-10-01 09:00"}
    assert [spec[1] for _key, spec in contents.top] == ["docs", "a.txt", "b.txt", "old.zip"]
    assert [spec[1] for _key, spec in contents.keyed("docs", 0, True)] == ["docs/report-2.pdf",
                                                                             "docs/report-10.pdf"]
    with mock.patch.object(Contents, "__init__", side_effect=AssertionError("worked out again")):
        b = make(entries, contents)
    assert b.contents is contents
    assert _shown(b) == ["docs", "a.txt", "b.txt", "old.zip"]
    assert b.nested.isVisibleTo(b)


def test_a_search_goes_through_the_rows_a_part_at_a_time(make, qtbot):
    from corefold import browser as B
    b = make([ar.Entry(f"scan-{i}.txt", 1) for i in range(6000)])
    b.finish_rows()
    clock = iter(range(10 ** 9))
    with mock.patch.object(B.time, "perf_counter", side_effect=lambda: next(clock)):  # (every look at the
        b._filter("scan-5999")                                                       # clock: time's up)
        assert b._looking is not None, "every row was gone through in one step"
        qtbot.waitUntil(lambda: b._looking is None, timeout=10_000)
    assert _shown(b) == ["scan-5999.txt"]

    b._filter("scan-1")  # a search that's still going when the rows are sorted finishes first
    b._sort_by(1)
    assert b._looking is None
    assert len(_shown(b)) == 1111 and all(p.startswith("scan-1") for p in _shown(b))

    clock = iter(range(10 ** 9))
    with mock.patch.object(B.time, "perf_counter", side_effect=lambda: next(clock)):
        b._filter("")
        looking = b._looking
        b.set_entries(b.entries, b.contents)  # (read again meanwhile: the old search stops)
        assert b._looking is None
        b._look_on(looking)  # (its next step finds it isn't wanted any more)
    assert len(_shown(b)) == 1500  # (the first rows; the rest follow)
