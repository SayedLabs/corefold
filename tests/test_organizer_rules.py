"""File Organizer: the person's own rules come first (words in the name, or types), files can go into a
folder for each year (and month; a photo by the day it was taken), and Undo still puts everything back
and takes away every folder it made, the inner ones too."""
import os
import time
from pathlib import Path

import pytest
from PIL import Image

from corefold import organizer


@pytest.fixture(autouse=True)
def own_state(tmp_path, monkeypatch):
    monkeypatch.setattr(organizer, "_state_path", lambda: tmp_path / "state" / "organizer.json")
    monkeypatch.setattr(organizer, "_journal_path", lambda: tmp_path / "state" / "organizer-journal.jsonl")
    monkeypatch.setattr(organizer, "refused", lambda folder, known=False: "")  # (Windows' temp folder is in AppData)


def _file(folder: Path, name: str, when: float | None = None) -> Path:
    p = folder / name
    p.write_bytes(b"x" * 10)
    if when is not None:
        os.utime(p, (when, when))
    return p


def _photo(folder: Path, name: str, taken: str) -> Path:
    p = folder / name
    im = Image.new("RGB", (8, 8), "red")
    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = taken
    im.save(p, exif=exif)
    return p


def _rule(match, text, folder, rid="1"):
    return organizer.Rule(rid, match, text, folder)


def test_name_and_type_rules_come_first(tmp_path):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    for n in ("Invoice March.pdf", "فاتورة الكهرباء.pdf", "notes.pdf", "logo.psd", "poster.ai", "song.mp3"):
        _file(folder, n)
    organizer.set_rules([_rule("name", "invoice, فاتورة", "Invoices", "a"),
                         _rule("type", "psd, .AI, *.fig", "Designs", "b")])
    p = organizer.plan(folder, check_in_use=False)
    where = {i.path.name: i.target.relative_to(folder).parts[0] for i in p.moving()}
    assert where["Invoice March.pdf"] == where["فاتورة الكهرباء.pdf"] == "Invoices"
    assert where["logo.psd"] == where["poster.ai"] == "Designs"
    assert where["notes.pdf"] == organizer.folder_name("documents")
    assert where["song.mp3"] == organizer.folder_name("music")
    assert [i.kind for i in p.moving()][:4] == ["rule:a", "rule:a", "rule:b", "rule:b"]  # (rules first)
    assert organizer.kind_name("rule:a") == "Invoices"
    res = organizer.sort(p)
    assert res.moved == 6 and (folder / "Invoices" / "Invoice March.pdf").exists()
    organizer.undo()
    assert sorted(os.listdir(folder)) == sorted(["Invoice March.pdf", "فاتورة الكهرباء.pdf", "notes.pdf", "logo.psd",
                                                 "poster.ai", "song.mp3"])


def test_a_rule_can_catch_files_corefold_does_not_sort(tmp_path):
    folder = tmp_path / "Desktop"
    folder.mkdir()
    _file(folder, "model.blend")
    assert not organizer.plan(folder, check_in_use=False).moving()
    organizer.set_rules([_rule("type", "blend", "3D")])
    [item] = organizer.plan(folder, check_in_use=False).moving()
    assert item.target == folder / "3D" / "model.blend"


def test_rule_to_a_folder_elsewhere(tmp_path):
    folder, elsewhere = tmp_path / "Downloads", tmp_path / "Work" / "Bills"
    folder.mkdir()
    _file(folder, "bill-01.pdf")
    organizer.set_rules([_rule("name", "bill", str(elsewhere))])
    p = organizer.plan(folder, check_in_use=False)
    assert p.moving()[0].target == elsewhere / "bill-01.pdf"
    organizer.sort(p)
    assert (elsewhere / "bill-01.pdf").exists()
    organizer.undo()
    assert (folder / "bill-01.pdf").exists() and not (tmp_path / "Work").exists()


def test_unsafe_rule_folders_are_ignored(tmp_path):
    for bad in ("..", "../up", "a/../../b", "", "con:x", "a?b"):
        assert not organizer.safe_folder(bad), bad
    assert organizer.safe_folder("Invoices") and organizer.safe_folder(str(tmp_path / "x"))
    organizer.set_rules([_rule("name", "x", "../escape")])
    assert organizer.rules() == []


def test_by_year_and_month_with_photo_dates(tmp_path):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    jan_2023 = time.mktime((2023, 1, 15, 12, 0, 0, 0, 0, -1))
    _file(folder, "report.pdf", jan_2023)
    _photo(folder, "holiday.jpg", "2019:07:04 10:30:00")  # (taken in 2019, whatever the file's own time)
    organizer.set_date_mode("month")
    p = organizer.plan(folder, check_in_use=False)
    where = {i.path.name: i.target.relative_to(folder).parts for i in p.moving()}
    assert where["report.pdf"][1:3] == ("2023", organizer.month_name(1))
    assert where["holiday.jpg"][1:3] == ("2019", organizer.month_name(7))
    assert organizer.month_name(7).startswith("07")
    organizer.sort(p)
    assert (folder / where["holiday.jpg"][0] / "2019" / organizer.month_name(7) / "holiday.jpg").exists()
    organizer.undo()  # (every folder it made goes again: months, years, then the kind's folder)
    assert sorted(os.listdir(folder)) == ["holiday.jpg", "report.pdf"]


def test_by_year_uses_a_month_folder_already_there(tmp_path):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    when = time.mktime((2024, 10, 2, 12, 0, 0, 0, 0, -1))
    _file(folder, "a.pdf", when)
    old = folder / organizer.folder_name("documents") / "2024" / "10 أكتوبر"
    old.mkdir(parents=True)
    organizer.set_date_mode("month")
    assert organizer.plan(folder, check_in_use=False).moving()[0].target == old / "a.pdf"
    organizer.set_date_mode("year")
    assert organizer.plan(folder, check_in_use=False).moving()[0].target.parent.name == "2024"
    organizer.set_date_mode("nonsense")
    assert organizer.date_mode() == ""
