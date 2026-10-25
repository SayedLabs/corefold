"""Old ZIPs keep their names in the code page of the PC that made them (Arabic Windows: 720, some
programs 1256), without the UTF-8 mark. Corefold works out which one and reads Arabic names right, with
7-Zip and without it, and the ZIPs it makes itself always mark their names UTF-8."""
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from corefold import archives
from corefold.core import Ctx, Options

NAMES = ["تقرير المبيعات.txt", "صور العائلة/الصيف 2024.txt", "ملاحظات.txt"]


def _ctx(**params) -> Ctx:
    return Ctx(Options(), lambda f: None, threading.Event(), params)


def old_zip(path: Path, names: list[str], cp: str) -> Path:
    """A ZIP as an old program writes it: names in code page `cp`, no UTF-8 mark (bit 11)."""
    raws = [n.encode(cp) for n in names]
    keys = [(chr(65 + i) * len(r)).encode() for i, r in enumerate(raws)]
    assert len(set(keys)) == len(keys)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for key, name in zip(keys, names):
            zf.writestr(key.decode(), f"inside {name}")
    data = path.read_bytes()
    for key, raw in zip(keys, raws):  # (same length: the headers stay right)
        assert data.count(key) == 2
        data = data.replace(key, raw)
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("cp,expected", [("cp720", 720), ("cp1256", 1256)])
def test_the_code_page_is_worked_out(tmp_path, cp, expected):
    src = old_zip(tmp_path / f"{cp}.zip", NAMES, cp)
    assert archives.detect_zip_codepage(src) == expected


def test_utf8_without_the_mark_and_plain_names(tmp_path):
    src = old_zip(tmp_path / "u.zip", NAMES, "utf-8")
    assert archives.detect_zip_codepage(src) == archives.UTF8_CP
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as zf:
        zf.writestr("a.txt", "x")
        zf.writestr("مرحبا.txt", "y")  # (Python marks this one UTF-8)
    assert archives.detect_zip_codepage(plain) is None
    assert not archives.has_legacy_names(plain)


@pytest.mark.parametrize("with_7z", [False, True])
@pytest.mark.parametrize("cp", ["cp720", "cp1256"])
def test_arabic_names_come_out_right(tmp_path, monkeypatch, cp, with_7z):
    if with_7z and (not archives.find_7z() or sys.platform != "win32"):
        pytest.skip("7-Zip reads code pages on Windows only")
    if not with_7z:
        monkeypatch.setattr(archives, "find_7z", lambda: None)
    src = old_zip(tmp_path / "old.zip", NAMES, cp)
    listed = {e.path for e in archives.list_entries(src) if not e.is_dir}
    assert listed == set(NAMES)
    dest = tmp_path / "out"
    archives.extract(src, _ctx(dest=str(dest)), "folder")
    got = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
    assert {g.split("/", 1)[1] for g in got} == set(NAMES)
    assert archives.read_member(src, NAMES[0]) == f"inside {NAMES[0]}".encode()


def test_a_picked_code_page_wins_until_cleared(tmp_path, monkeypatch):
    monkeypatch.setattr(archives, "find_7z", lambda: None)
    src = old_zip(tmp_path / "old.zip", NAMES, "cp720")
    archives.set_zip_codepage(src, 1256)
    try:
        assert {e.path for e in archives.list_entries(src)} != set(NAMES)
    finally:
        archives.set_zip_codepage(src, None)
    assert {e.path for e in archives.list_entries(src) if not e.is_dir} == set(NAMES)


def test_a_big_table_of_contents_is_read_quickly(tmp_path):
    import time
    src = tmp_path / "many.zip"
    with zipfile.ZipFile(src, "w") as zf:
        for i in range(20000):
            zf.writestr(f"f{i}.txt", "")
    t = time.perf_counter()
    assert archives.detect_zip_codepage(src) is None
    assert time.perf_counter() - t < 1.0


def test_zips_made_by_corefold_mark_names_utf8(tmp_path):
    if not archives.find_7z():
        pytest.skip("no 7-Zip here")
    folder = tmp_path / "مجلد"
    folder.mkdir()
    (folder / "ملف.txt").write_text("x")
    out = tmp_path / "made.zip"
    archives.pack_into([folder], out, _ctx(), "zip")
    with zipfile.ZipFile(out) as zf:
        assert all(i.flag_bits & 0x800 for i in zf.infolist())
