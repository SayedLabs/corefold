"""Space's list with many folders opened inside each other: the rows step in less as they go deeper so the
names keep room, nothing of a row is drawn over the Size column, and a folder opened deeper than that is
opened on the map instead (the list starts from it)."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidgetItem

from corefold import space
from tests.test_space_better import _open
from tests.test_space_better import page  # noqa: F401  (the page fixture, used by name)


def _chain(tmp_path: Path, depth: int) -> Path:
    root = tmp_path / "C"
    here = root
    for i in range(depth):
        here = here / f"a-folder-with-a-long-name-{i}"
        here.mkdir(parents=True)
        (here / f"file-{i}.bin").write_bytes(b"x" * (1000 + i))
    return root


def _clean(text: str) -> str:
    return text.strip("\u2066\u2067\u2068\u2069\u200e\u200f")


def _open_all(page, qtbot, limit: int = 60):  # noqa: F811
    """Opens the folder rows one after another, the way someone clicks down the arrows."""
    for _ in range(limit):
        rows = [r for r in page._all_rows() if not r.isExpanded() and r.childIndicatorPolicy() == QTreeWidgetItem.ShowIndicator
                and (r.data(0, Qt.UserRole) or (None, None))[1] is not None]
        if not rows:
            return
        rows[0].setExpanded(True)
        qtbot.wait(5)


def test_deep_rows_step_in_less_and_keep_room_for_names(page, qtbot, tmp_path):  # noqa: F811
    root = _chain(tmp_path, 8)
    _open(page, root)
    page.resize(1300, 760)
    qtbot.wait(20)
    items = page.items
    assert items.indentation() == items.STEP                 # (nothing open: the usual step)
    _open_all(page, qtbot)
    depth = items.deepest_open()
    assert depth == 8
    assert page.here is page.scan.root                       # (it fitted: nothing was opened on the map)
    step = items.indentation()
    assert items.LEAST <= step < items.STEP                  # (stepping in less, to fit)
    assert depth * step + items.NAME_ROOM <= items.header().sectionSize(0)
    # closing them again: back to the usual step
    for r in list(page._all_rows()):
        r.setExpanded(False)
    assert items.indentation() == items.STEP


def test_nothing_of_a_deep_row_is_drawn_over_the_size(page, qtbot, tmp_path):  # noqa: F811
    root = _chain(tmp_path, 8)
    _open(page, root)
    page.resize(1300, 760)
    _open_all(page, qtbot)
    items = page.items
    items.setIndentation(30)   # (stepped in past the name column on purpose, the way it was before)
    qtbot.wait(20)
    deepest = max(page._all_rows(), key=lambda r: len(str((r.data(0, Qt.UserRole) or ("",))[0] or "")))
    items.scrollToItem(deepest)
    qtbot.wait(20)
    tip = deepest.toolTip(0)
    for col in range(1, items.columnCount()):
        deepest.setText(col, "")          # (so anything drawn right of the name on that row is spill-over)
    items.setCurrentItem(None)
    qtbot.wait(20)
    rect = items.visualItemRect(deepest)
    head = items.header()
    name_right = head.sectionViewportPosition(0) + head.sectionSize(0)
    shot = items.viewport().grab().toImage()
    background = shot.pixelColor(items.viewport().width() - 3, rect.top() + 2)
    marked = 0
    for x in range(name_right + 1, items.viewport().width() - 2):
        for y in range(rect.top() + 1, rect.bottom()):
            if shot.pixelColor(x, y) != background:
                marked += 1
    assert marked == 0
    assert _clean(tip).endswith(Path(str(deepest.data(0, Qt.UserRole)[0])).name)


def test_a_folder_opened_too_deep_opens_on_the_map(page, qtbot, tmp_path):  # noqa: F811
    root = _chain(tmp_path, 40)
    _open(page, root)
    page.resize(1300, 760)
    _open_all(page, qtbot, 80)
    qtbot.wait(20)
    assert page.here is not page.scan.root                    # (it went too deep: the list starts further down)
    items = page.items
    assert items.deepest_open() * items.LEAST + items.NAME_ROOM <= items.header().sectionSize(0) + items.LEAST
    names = [_clean(items.topLevelItem(i).text(0)) for i in range(items.topLevelItemCount())]
    assert any(n.startswith("a-folder-with-a-long-name-") for n in names)
    assert isinstance(page.here, space.Folder)
