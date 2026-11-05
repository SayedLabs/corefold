"""Space's fast look (mftscan): an NTFS drive's own table of files, read straight from the disk. Checked on a small
NTFS image made with mkntfs/ntfs-3g (tests/data/ntfs-small.img.gz), and on Windows against the real C: drive."""
import ctypes
import gzip
import os
import sys
from pathlib import Path

import pytest

from corefold import mftscan, space

DATA = Path(__file__).parent / "data" / "ntfs-small.img.gz"


@pytest.fixture
def image(tmp_path) -> str:
    out = tmp_path / "small.img"
    out.write_bytes(gzip.decompress(DATA.read_bytes()))
    return str(out)


def _find(folder, *names):
    for name in names:
        folder = next(c for c in folder.children if c.name == name)
    return folder


def test_reads_every_file_with_its_size(image):
    items = mftscan.read_table(image)
    scan = mftscan.build(Path("T:\\"), items)
    assert scan.root.size == 319609  # (du -sb of what was put on the image)
    assert scan.files == 125         # (126 names, one of them a second hard link to tiny.txt: counted once)
    assert scan.folders == 8
    assert _find(scan.root, "Program Files").size == 300002 or _find(scan.root, "Program Files").size == 300000
    leaf = _find(scan.root, "Users", "أحمد", "Documents", "deep")
    assert leaf.size == 12345 and leaf.files == 1
    many = _find(scan.root, "many")
    assert many.files == 121
    assert any(c.name == "a very long folder name that goes past the short dos name" for c in many.children)
    assert scan.biggest[0][0] == 300000 and scan.biggest[0][1].endswith("big.dat")
    assert not any(c.name.startswith("$") for c in scan.root.children)  # (NTFS's own files aren't shown)


def test_listing_file_round_trip(image, tmp_path):
    items = mftscan.read_table(image)
    out = tmp_path / "x.listing"
    mftscan.write_listing(items, out)
    assert mftscan.read_listing(out) == items
    out.write_bytes(b"not one")
    with pytest.raises(ValueError):
        mftscan.read_listing(out)


def test_not_ntfs_is_refused(tmp_path):
    f = tmp_path / "zero.img"
    f.write_bytes(bytes(1 << 16))
    with pytest.raises(mftscan.NotNtfs):
        mftscan.read_table(str(f))


def test_drive_path():
    assert mftscan.drive_path(Path("C:\\")) == "\\\\.\\C:"
    assert mftscan.drive_path(Path("C:\\Users")) is None


def test_helper_refuses_bad_drive(tmp_path):
    assert mftscan.helper_scan("C:\\..", tmp_path / "x") == {"error": "bad drive"}
    assert mftscan.helper_scan("", tmp_path / "x") == {"error": "bad drive"}


def test_helper_kind_is_known():
    from corefold import uninstall
    assert "--mft-scan" in uninstall.HELPER_KINDS
    main = (Path(__file__).parent.parent / "main.py").read_text("utf-8")
    assert '"--mft-scan"' in main


def test_fast_look_falls_back_when_not_ntfs(tmp_path):
    assert mftscan.fast_scan(tmp_path) is None


def _admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


@pytest.mark.skipif(sys.platform != "win32", reason="a real NTFS drive")
def test_real_drive_matches_the_ordinary_look(tmp_path):
    """The table of the Windows drive, read for real (CI runs as administrator), against an ordinary look at a
    folder made for the test."""
    if not _admin():
        pytest.skip("needs an administrator")
    root = Path(os.environ.get("SystemDrive", "C:") + "\\")
    assert mftscan.is_ntfs(root)
    folder = tmp_path / "fastlook"
    (folder / "a" / "b").mkdir(parents=True)
    (folder / "a" / "one.bin").write_bytes(b"1" * 5000)
    (folder / "a" / "b" / "two.bin").write_bytes(b"2" * 70000)
    for i in range(50):
        (folder / f"f{i}.txt").write_bytes(b"x" * i)
    big = folder / "big.bin"
    with open(big, "wb") as f:
        f.write(os.urandom(3 << 20))
    items = mftscan.read_table(mftscan.drive_path(root))
    scan = mftscan.build(root, items)
    node = scan.root
    for part in folder.relative_to(root).parts:
        node = next(c for c in node.children if c.name.lower() == part.lower())
    usual = space.scan(folder)
    assert node.size == usual.root.size == 5000 + 70000 + sum(range(50)) + (3 << 20)
    assert node.files == usual.root.files == 53
    assert scan.files > 10000 and scan.root.size > 1 << 30
    windows = next(c for c in scan.root.children if c.name.lower() == "windows")
    assert windows.size > 1 << 30


@pytest.mark.skipif(sys.platform != "win32", reason="the administrator copy")
def test_helper_writes_a_listing(tmp_path):
    if not _admin():
        pytest.skip("needs an administrator")
    out = tmp_path / "c.listing"
    answer = mftscan.helper_scan(os.environ.get("SystemDrive", "C:")[0], out)
    assert answer.get("listing") == str(out), answer
    assert len(mftscan.read_listing(out)) == answer["count"] > 10000
    with pytest.raises(FileExistsError):  # (never into a file that's already there)
        mftscan.helper_scan(os.environ.get("SystemDrive", "C:")[0], out)


def test_setting_is_off_at_first():
    from corefold.settings import Settings
    assert Settings().space_fast_look is False


def _listing(rows):
    """A Listing from (record, folder record, is a folder, size, attributes, name)."""
    return mftscan.Listing([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows],
                           [r[4] for r in rows], [1.7e9] * len(rows), [r[5] for r in rows])


def test_extended_attributes_are_not_the_cloud():
    """On the disk, 0x40000 means "has extended attributes" (many of Windows' own files): such a file is counted.
    Only offline and recall-on-data files (only in the cloud) are left out."""
    items = _listing([(5, 5, True, 0, 0x16, "."), (20, 5, True, 0, 0x10, "Windows"),
                      (21, 20, False, 1000, 0x40020, "ntdll.dll"), (22, 20, False, 50, 0x20, "a.txt"),
                      (23, 20, False, 7000, 0x401020, "cloud.docx"), (24, 5, True, 0, 0x410, "Link"),
                      (25, 24, False, 99, 0x20, "behind the link.txt"), (26, 999, False, 5, 0x20, "lost.txt")])
    scan = mftscan.build(Path("T:\\"), items)
    assert scan.files == 2 and scan.cloud == 1 and scan.root.size == 1050
    assert [c.name for c in scan.root.children] == ["Windows"]
    windows = scan.root.children[0]
    assert windows.names.split("\n") == ["ntdll.dll", "a.txt"] and list(windows.sizes) == [1000, 50]
    assert scan.kinds["program"] == 1000 and scan.types[".dll"] == (1000, 1)


def test_listing_keeps_odd_names(tmp_path):
    items = _listing([(5, 5, True, 0, 0x16, "."), (30, 5, False, 1, 0x20, "ملف 😀.txt"), (31, 5, False, 2, 0x20, "")])
    out = tmp_path / "x.listing"
    mftscan.write_listing(items, out)
    assert mftscan.read_listing(out) == items
