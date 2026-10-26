"""Archive bombs (a small archive that unpacks to far more than the disk holds) are refused before
unpacking when the archive says how much it holds, and stopped part way when it doesn't (a .tar.gz) or
holds more than it says. Nothing that was unpacked is left behind. The bombs here are small (tens of MB of
zeros); the disk is made to look nearly full instead of filling it."""
import gzip
import io
import tarfile
import threading
import zipfile
from collections import namedtuple
from pathlib import Path

import pytest

from corefold import archives
from corefold.core import Ctx, Options, render_scale

MB = 1024 * 1024
Usage = namedtuple("Usage", "total used free")


def _ctx(**params) -> Ctx:
    return Ctx(Options(), lambda f: None, threading.Event(), params)


def _written(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


@pytest.fixture
def small_disk(monkeypatch, tmp_path):
    """The disk has 8 MB to spare past the last RESCUE_RESERVE (made 16 MB here), and loses what's written
    into tmp_path/out."""
    monkeypatch.setattr(archives, "RESCUE_RESERVE", 16 * MB)
    monkeypatch.setattr(archives._SpaceGuard, "EVERY", 0.01)  # (a fast PC unpacks these zeros in under a second)
    out = tmp_path / "out"
    out.mkdir()

    def usage(path):
        free = 24 * MB - _written(out)
        return Usage(1 << 40, (1 << 40) - free, free)
    monkeypatch.setattr(archives.shutil, "disk_usage", usage)
    return out


def _zip_bomb(path: Path, size: int):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        with zf.open("zeros.bin", "w") as f:
            for _ in range(size // MB):
                f.write(bytes(MB))


def test_a_zip_bomb_is_refused_before_anything_is_unpacked(tmp_path, small_disk, monkeypatch):
    monkeypatch.setattr(archives, "find_7z", lambda: None)
    src = tmp_path / "bomb.zip"
    _zip_bomb(src, 64 * MB)  # (64 MB of zeros in about 64 KB)
    with pytest.raises(RuntimeError, match="archive bomb"):
        archives.extract(src, _ctx(dest=str(small_disk)), "folder")
    assert _written(small_disk) == 0 and not any(small_disk.iterdir())


def test_an_archive_that_doesnt_fit_says_how_much_it_needs(tmp_path, small_disk, monkeypatch):
    monkeypatch.setattr(archives, "find_7z", lambda: None)
    src = tmp_path / "big.zip"
    with zipfile.ZipFile(src, "w", zipfile.ZIP_STORED) as zf:  # (not a bomb: as big packed as unpacked)
        zf.writestr("big.bin", bytes(12 * MB))
    with pytest.raises(RuntimeError, match="needs"):
        archives.extract(src, _ctx(dest=str(small_disk)), "folder")
    assert not any(small_disk.iterdir())


def test_a_tar_gz_bomb_is_stopped_part_way_and_cleaned_up(tmp_path, small_disk):
    src = tmp_path / "bomb.tar.gz"
    data = bytes(256 * MB)  # (one file: it must be stopped inside it, not only between files)
    with gzip.open(src, "wb", compresslevel=1) as gz, tarfile.open(fileobj=gz, mode="w") as tf:
        info = tarfile.TarInfo("zeros.bin")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    del data
    with pytest.raises(RuntimeError, match="disk is nearly full"):
        archives.extract(src, _ctx(dest=str(small_disk)), "folder")
    assert _written(small_disk) == 0 and not any(small_disk.iterdir())


def test_an_archive_that_fits_still_unpacks(tmp_path, small_disk, monkeypatch):
    monkeypatch.setattr(archives, "find_7z", lambda: None)
    src = tmp_path / "fine.zip"
    _zip_bomb(src, 2 * MB)
    archives.extract(src, _ctx(dest=str(small_disk)), "folder")
    assert _written(small_disk) == 2 * MB


@pytest.mark.skipif(not archives.find_7z(), reason="no 7-Zip here")
def test_7zip_refuses_a_bomb_too(tmp_path, small_disk):
    src = tmp_path / "bomb.zip"
    _zip_bomb(src, 64 * MB)
    with pytest.raises(RuntimeError, match="archive bomb"):
        archives.extract(src, _ctx(dest=str(small_disk)), "folder")
    assert not any(small_disk.iterdir())


def test_a_huge_pdf_page_is_drawn_at_most_100_megapixels():
    huge = 14400 * 100  # (a page that says it's 20,000 inches wide and tall)
    scale = render_scale(huge, huge, 150 / 72)
    assert (huge * scale) ** 2 <= 100_000_001
    assert render_scale(595, 842, 600 / 72) == 600 / 72  # (A4 at 600 dpi as asked)
