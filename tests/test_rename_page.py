"""Rename: the rules, what the list says about each new name, and a real rename that can be undone."""
from pathlib import Path

from corefold import moretools, renamer
from corefold.core import Ctx


def _files(tmp_path: Path, *names) -> list[Path]:
    out = []
    for n in names:
        f = tmp_path / n
        f.write_text(n)
        out.append(f)
    return out


def _names(rows):
    return [r.new.name for r in rows]


def test_numbers_follow_explorers_order_and_pad(tmp_path):
    files = _files(tmp_path, "b10.jpg", "b2.jpg", "a.jpg")
    rows = renamer.plan(files, {"name": "new", "base": "Trip", "number": "after", "start": 9})
    assert _names(rows) == ["Trip 09.jpg", "Trip 10.jpg", "Trip 11.jpg"]
    assert [r.old.name for r in rows] == ["a.jpg", "b2.jpg", "b10.jpg"]


def test_find_replace_case_and_extension(tmp_path):
    files = _files(tmp_path, "IMG_0001 copy.JPG")
    rows = renamer.plan(files, {"find": " copy", "replace": "", "case": "lower", "ext": "lower"})
    assert _names(rows) == ["img_0001.jpg"] and rows[0].state == renamer.OK
    rows = renamer.plan(files, {"find": "img", "replace": "Photo"})  # (capitals don't have to match by default)
    assert _names(rows) == ["Photo_0001 copy.JPG"]
    rows = renamer.plan(files, {"find": "img", "replace": "Photo", "match_case": True})
    assert rows[0].state == renamer.SAME


def test_patterns(tmp_path):
    files = _files(tmp_path, "Show S01E02.mkv")
    rows = renamer.plan(files, {"find": r"S(\d+)E(\d+)", "replace": r"\1x\2", "pattern": True})
    assert _names(rows) == ["Show 01x02.mkv"]
    assert renamer.pattern_problem({"find": "(", "pattern": True})
    rows = renamer.plan(files, {"find": "(", "pattern": True})  # (a pattern that can't be read changes nothing)
    assert rows[0].state == renamer.SAME


def test_title_case_and_sentence(tmp_path):
    files = _files(tmp_path, "the BEST day's photo.png")
    assert _names(renamer.plan(files, {"case": "title"})) == ["The Best Day's Photo.png"]
    assert _names(renamer.plan(files, {"case": "sentence"})) == ["The best day's photo.png"]


def test_problems_are_found_before_anything_changes(tmp_path):
    a, b, _c = _files(tmp_path, "a.txt", "b.txt", "taken.txt")
    rows = renamer.plan([a], {"name": "new", "base": "taken"})
    assert rows[0].state == renamer.TAKEN and not renamer.pairs(rows)
    rows = renamer.plan([a, b], {"name": "new", "base": "same"})
    assert {r.state for r in rows} == {renamer.TWICE}
    rows = renamer.plan([a], {"after": "?"})
    assert rows[0].state == renamer.BAD and rows[0].why
    rows = renamer.plan([a], {"name": "new", "base": "CON"})
    assert rows[0].state == renamer.BAD
    rows = renamer.plan([a, b], {"find": "a", "replace": "b"})  # a → b while b → b: b keeps its name
    assert rows[0].state == renamer.TWICE


def test_swapping_names_works(tmp_path):
    """a → b and b → a in one go (through temporary names)."""
    a, b = _files(tmp_path, "1.txt", "2.txt")
    params = {"mode": "rules", "name": "new", "base": "x", "number": "after", "start": 2, "step": 1, "order": "added"}
    ctx = Ctx(None, params=params)
    result = moretools.rename_files([a, b], ctx)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x 2.txt", "x 3.txt"]
    assert moretools.undo_rename(result.undo) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["1.txt", "2.txt"]


def test_date_and_files_in_a_folder(tmp_path):
    import os
    (f,) = _files(tmp_path, "note.txt")
    os.utime(f, (1700000000, 1700000000))
    (tmp_path / "sub").mkdir()
    (tmp_path / ".hidden").write_text("x")
    assert renamer.files_in(tmp_path) == [f]
    facts = {f: renamer.read_facts(f, renamer.needs({"date": "taken"}))}
    rows = renamer.plan([f], {"date": "changed", "date_at": "after"}, facts)
    assert rows[0].new.name.startswith("note 2023-11-")


def test_the_page_shows_the_new_names(qtbot, tmp_path):
    from unittest import mock
    from corefold.renamepage import RenamePage
    from corefold.settings import Settings
    from corefold.theme import build_theme
    from PySide6.QtWidgets import QHBoxLayout
    c = mock.MagicMock()
    c.settings = Settings()
    c.theme = build_theme("dark", "teal")
    page = RenamePage(c, QHBoxLayout())
    qtbot.addWidget(page)
    files = _files(tmp_path, "a.txt", "b.txt")
    page.add([tmp_path])
    assert page.files == files and not page.body.isHidden()
    page.number.setCurrentIndex(page.number.findData("before"))
    page.update_list()
    assert [page.list.topLevelItem(i).text(1) for i in range(2)] == ["1 a.txt", "2 b.txt"]
    assert page.go.isEnabled() and '"number": "before"' in c.settings.rename_rules
    page.after.setText("?")
    page.update_list()
    assert not page.go.isEnabled() and not page.problem.isHidden()
