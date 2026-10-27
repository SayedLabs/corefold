"""Booby-trapped archives: nothing ever lands outside the folder it's unpacked into, links never come out,
a TAR with one odd item still unpacks the rest, a .bz2 (which doesn't say its size) isn't taken for a bomb,
and a picture of a few KB that would take GBs to show isn't shown in the preview."""
import bz2
import io
import os
import stat
import sys
import tarfile
import threading
import zipfile
from collections import namedtuple
from pathlib import Path

import pytest

from corefold import archives
from corefold.core import Ctx, Options

MB = 1024 * 1024
Usage = namedtuple("Usage", "total used free")
needs_7z = pytest.mark.skipif(not archives.find_7z(), reason="needs 7-Zip")


def _ctx(**params) -> Ctx:
    return Ctx(Options(), lambda f: None, threading.Event(), params)


def _everything(folder: Path) -> list[str]:
    return sorted(str(p.relative_to(folder)).replace(os.sep, "/") for p in folder.rglob("*"))


def _tar(path: Path, items: list[tarfile.TarInfo], mode: str = "w"):
    with tarfile.open(path, mode) as tf:
        for info in items:
            data = b"x" * info.size if info.isfile() else None
            tf.addfile(info, io.BytesIO(data) if data is not None else None)


def _file(name: str, size: int = 1) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    return info


def _link(name: str, target: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    return info


@pytest.fixture
def box(tmp_path):
    """Where the archives are unpacked, next to a folder that must stay empty."""
    (tmp_path / "outside").mkdir()
    (tmp_path / "out").mkdir()
    return tmp_path


def _nothing_outside(box: Path):
    assert not any((box / "outside").iterdir())
    assert not list(box.glob("evil*")) and not list(box.parent.glob("evil*"))


@pytest.mark.parametrize("seven_zip", [False, pytest.param(True, marks=needs_7z)])
def test_zip_slip_stays_inside(box, monkeypatch, seven_zip):
    if not seven_zip:
        monkeypatch.setattr(archives, "find_7z", lambda: None)
    src = box / "slip.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("../evil1.txt", "x")
        zf.writestr("../../evil2.txt", "x")
        zf.writestr("/evil3.txt", "x")
        zf.writestr("ok/a.txt", "ok")
    archives.extract(src, _ctx(dest=str(box / "out")), "folder")
    _nothing_outside(box)
    assert any(p.endswith("ok/a.txt") for p in _everything(box / "out"))


@needs_7z
def test_a_zip_link_never_leads_out(box):
    src = box / "link.zip"
    with zipfile.ZipFile(src, "w") as zf:
        info = zipfile.ZipInfo("lnk")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "../outside")
        zf.writestr("lnk/evil4.txt", "x")
        zf.writestr("good.txt", "x")
    try:
        archives.extract(src, _ctx(dest=str(box / "out")), "folder")
    except RuntimeError:
        pass  # (7-Zip may refuse the whole archive: fine, as long as nothing got out)
    _nothing_outside(box)
    assert not any(archives.is_link(p) for p in (box / "out").rglob("*"))


def test_a_tar_link_out_is_left_out_and_the_rest_unpacks(box):
    src = box / "links.tar"
    _tar(src, [_link("abs", str(box / "outside")), _file("abs/evil5.txt"),
               _link("up", "../outside"), _file("up/evil6.txt"),
               _file("../evil7.txt"), _file("good.txt")])
    result = archives.extract(src, _ctx(dest=str(box / "out")), "folder")
    _nothing_outside(box)
    assert "good.txt" in [Path(p).name for p in _everything(box / "out")]
    assert "outside the folder" in result.note
    assert not any(archives.is_link(p) for p in (box / "out").rglob("*"))


@pytest.mark.parametrize("name", ["inner.tar.gz", "inner.tar.zst"])
def test_a_tar_link_inside_the_archive_doesnt_stop_it(box, name):
    """A link to a file in the same TAR (common in Linux TARs): the rest unpacks, on Windows too (where only
    an administrator may make links), and no link is left in what comes out."""
    src = box / name
    if name.endswith(".zst"):
        import zstandard
        with open(src, "wb") as fh, zstandard.ZstdCompressor().stream_writer(fh) as zw, \
                tarfile.open(fileobj=zw, mode="w|") as tf:
            for info in (_file("lib/libx.so.1", 10), _link("lib/libx.so", "libx.so.1"), _file("readme.txt")):
                tf.addfile(info, io.BytesIO(b"x" * info.size) if info.isfile() else None)
    else:
        _tar(src, [_file("lib/libx.so.1", 10), _link("lib/libx.so", "libx.so.1"), _file("readme.txt")], "w:gz")
    archives.extract(src, _ctx(dest=str(box / "out")), "folder")
    names = [Path(p).name for p in _everything(box / "out")]
    assert "readme.txt" in names and "libx.so.1" in names
    assert not any(archives.is_link(p) for p in (box / "out").rglob("*"))


def test_device_names_are_saved_as_files():
    assert archives.windows_safe_name("CON") == "_CON"
    assert archives.windows_safe_name("a/aux.txt") == "a/_aux.txt"
    assert archives.windows_safe_name("nul.tar.gz") == "_nul.tar.gz"
    assert archives.windows_safe_name("COM1") == "_COM1"
    assert archives.windows_safe_name("console.txt") == "console.txt"
    assert archives.windows_safe_name("com10") == "com10"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own device names")
def test_device_names_in_a_tar_come_out_as_files(box):
    src = box / "devices.tar"
    _tar(src, [_file("CON", 3), _file("aux.txt", 3), _file("ok.txt", 3)])
    archives.extract(src, _ctx(dest=str(box / "out")), "folder")
    names = [Path(p).name for p in _everything(box / "out")]
    assert {"_CON", "_aux.txt", "ok.txt"} <= set(names)


def test_drag_out_leaves_no_links(box):
    src = box / "drag.tar"
    _tar(src, [_file("a.txt"), _link("b.txt", "a.txt")])
    folder = archives.extract_for_drag(src, ["a.txt", "b.txt"], "")
    try:
        assert not any(archives.is_link(p) for p in folder.rglob("*"))
    finally:
        archives.remove_drag_folder(folder)


@pytest.fixture
def roomy_disk(monkeypatch, tmp_path):
    """A disk with plenty of room that loses what's written into tmp_path/out; the guard looks often."""
    monkeypatch.setattr(archives._SpaceGuard, "EVERY", 0.01)
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)

    def usage(path):
        free = (1 << 40) - sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
        return Usage(1 << 41, (1 << 41) - free, free)
    monkeypatch.setattr(archives.shutil, "disk_usage", usage)
    return out


def test_a_tar_xz_bomb_stops_at_a_thousand_times_its_size(tmp_path, roomy_disk, monkeypatch):
    """A .tar.xz doesn't say how much it holds: with 1 TB free it used to fill nearly all of it.
    (xz and zstd pack zeros far past 1,000 to 1; gzip can't, so a .tar.gz never trips this.)"""
    monkeypatch.setattr(archives, "_UNKNOWN_MIN", 8 * MB)
    src = tmp_path / "bomb.tar.xz"
    with tarfile.open(src, "w:xz", preset=6) as tf:
        info = tarfile.TarInfo("zeros.bin")
        info.size = 64 * MB

        class Zeros(io.RawIOBase):
            left = info.size

            def readable(self):
                return True

            def readinto(self, b):
                n = min(len(b), self.left)
                b[:n] = bytes(n)
                self.left -= n
                return n
        tf.addfile(info, io.BufferedReader(Zeros()))
    assert src.stat().st_size * 1000 < 64 * MB
    with pytest.raises(RuntimeError, match="thousand times"):
        archives.extract(src, _ctx(dest=str(roomy_disk)), "folder")
    assert not any(roomy_disk.iterdir())


@needs_7z
def test_a_bz2_file_is_listed_and_isnt_taken_for_a_bomb(tmp_path, roomy_disk, monkeypatch):
    """7-Zip lists a .bz2's one file with no name or size: it showed as empty, and unpacking stopped once it
    passed 256 MB ("far more than it says it holds"), so any real .bz2 over 256 MB failed."""
    monkeypatch.setattr(archives, "_LIE_SLACK", 1 * MB)
    src = tmp_path / "data.bin.bz2"
    payload = os.urandom(64 * 1024) * 64  # (4 MB)
    src.write_bytes(bz2.compress(payload))
    entries = archives.list_entries(src)
    assert [e.path for e in entries] == ["data.bin"]
    result = archives.extract(src, _ctx(dest=str(roomy_disk)), "folder")
    out = result.outputs[0]
    found = [p for p in ([out] if out.is_file() else out.rglob("*")) if p.is_file()]
    assert [p.stat().st_size for p in found] == [len(payload)]


def test_a_picture_bomb_isnt_decoded_for_the_preview(qtbot):
    from PIL import Image

    from corefold.browser import preview_of
    raw = io.BytesIO()
    Image.new("1", (12000, 12000)).save(raw, "PNG")  # (144 megapixels in a few hundred KB)
    kind, _why = preview_of("huge.png", raw.getvalue())
    assert kind == "none"
    raw = io.BytesIO()
    Image.new("RGB", (300, 200), "red").save(raw, "PNG")
    assert preview_of("small.png", raw.getvalue())[0] == "picture"
