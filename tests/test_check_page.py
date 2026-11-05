"""Check: fingerprints, pasted codes, lists of codes (and nothing outside the list's folder), and the page."""
import hashlib
from pathlib import Path

from corefold import checker


def _file(tmp_path: Path, name: str, data: bytes) -> Path:
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    return f


def test_every_fingerprint_in_one_read(tmp_path):
    data = b"corefold" * 100000
    f = _file(tmp_path, "setup.exe", data)
    seen = []
    found = checker.fingerprints(f, seen.append)
    assert found == {a: hashlib.new(a, data).hexdigest() for a in checker.ALGOS}
    assert seen and seen[-1] == 1.0


def test_pasted_codes(tmp_path):
    f = _file(tmp_path, "a.zip", b"hello")
    found = checker.fingerprints(f)
    sha = found["sha256"]
    assert checker.matches(found, sha) == ("sha256", True)
    assert checker.matches(found, "  SHA256: " + sha.upper() + " ") == ("sha256", True)
    spaced = " ".join(sha[i:i + 2] for i in range(0, 64, 2))
    assert checker.matches(found, spaced) == ("sha256", True)
    assert checker.matches(found, found["md5"]) == ("md5", True)
    assert checker.matches(found, "0" * 64) == ("sha256", False)
    assert checker.matches(found, "not a code") == (None, None)
    assert checker.matches(found, "abc") == (None, None)


def test_a_list_of_codes(tmp_path):
    good = _file(tmp_path, "good.iso", b"good")
    _file(tmp_path, "sub/bad.bin", b"changed")
    _file(tmp_path.parent / "outside", "secret.txt", b"x")
    lines = [f"{hashlib.sha256(b'good').hexdigest()}  good.iso",
             f"{hashlib.sha256(b'original').hexdigest()} *sub/bad.bin",
             f"SHA256 (gone.txt) = {hashlib.sha256(b'x').hexdigest()}",
             f"{hashlib.sha256(b'x').hexdigest()}  ../outside/secret.txt",
             "# a comment"]
    sums = _file(tmp_path, "SHA256SUMS", "\n".join(lines).encode())
    assert checker.is_list(sums) and not checker.is_list(good)
    entries = checker.check_list(sums)
    assert [(e.name, e.state) for e in entries] == [("good.iso", checker.SAME), ("sub/bad.bin", checker.DIFFERENT),
                                                    ("gone.txt", checker.MISSING),
                                                    ("../outside/secret.txt", checker.MISSING)]


def test_a_code_alone_is_for_the_file_of_the_same_name(tmp_path):
    _file(tmp_path, "tool.exe", b"tool")
    one = _file(tmp_path, "tool.exe.sha256", hashlib.sha256(b"tool").hexdigest().encode())
    assert [(e.name, e.state) for e in checker.check_list(one)] == [("tool.exe", checker.SAME)]


def test_same_file(tmp_path):
    a = _file(tmp_path, "a", b"1" * 10)
    b = _file(tmp_path, "b", b"1" * 10)
    c = _file(tmp_path, "c", b"1" * 9 + b"2")
    assert checker.same_file(a, b) and not checker.same_file(a, c)


def test_the_page(qtbot, tmp_path):
    from unittest import mock
    from corefold.checkpage import CheckPage
    from corefold.settings import Settings
    from corefold.theme import build_theme
    from PySide6.QtWidgets import QHBoxLayout
    c = mock.MagicMock()
    c.settings = Settings()
    c.theme = build_theme("dark", "teal")
    page = CheckPage(c, QHBoxLayout())
    qtbot.addWidget(page)
    f = _file(tmp_path, "setup.exe", b"setup")
    page.add([f])
    qtbot.waitUntil(lambda: bool(page.found), timeout=5000)
    assert page.codes["sha256"].text() == hashlib.sha256(b"setup").hexdigest()
    page.paste.setText(hashlib.sha256(b"setup").hexdigest())
    assert page.verdict_ok is True
    page.paste.setText("0" * 64)
    assert page.verdict_ok is False
    sums = _file(tmp_path, "setup.exe.sha256", hashlib.sha256(b"setup").hexdigest().encode())
    page.add([sums])
    qtbot.waitUntil(lambda: page.list.topLevelItemCount() == 1, timeout=5000)
    assert not page.list_card.isHidden() and page.file_card.isHidden()
