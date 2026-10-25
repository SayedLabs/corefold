"""Checks for fixes in Startup, Scan and Empty folders (the review after 1.10.0)."""
import os

from corefold import emptyfolders, scan, scanpage, startup


def _open_everywhere(monkeypatch):
    """The test folders sit in Temp, which is inside AppData on Windows: looked in anyway here."""
    monkeypatch.setattr(emptyfolders, "_off_limits", lambda: [])
    monkeypatch.setattr(emptyfolders, "off_limits", lambda _path: False)


def _offered(root, **options):
    return sorted(os.path.relpath(i.path, root) for i in emptyfolders.find([root], new_seconds=0, **options).items)


def test_empty_folders_in_a_programs_folder_are_left_alone(tmp_path, monkeypatch):
    _open_everywhere(monkeypatch)
    game = tmp_path / "Games" / "Game"
    (game / "saves").mkdir(parents=True)
    (game / "data" / "cache").mkdir(parents=True)
    (game / "Game.exe").write_bytes(b"MZ")
    (tmp_path / "Games" / "Old").mkdir()
    assert _offered(tmp_path, keep=[]) == [os.path.join("Games", "Old")]
    # (in your own folders, a program lying there doesn't keep the empty folders next to it)
    assert _offered(tmp_path, keep=[tmp_path / "Games"]) == [os.path.join("Games", "Game", "data"),
                                                             os.path.join("Games", "Game", "saves"),
                                                             os.path.join("Games", "Old")]


def test_a_folder_windows_customised_is_not_offered(tmp_path, monkeypatch):
    _open_everywhere(monkeypatch)
    monkeypatch.setattr(emptyfolders, "_attrs", lambda _st: 0x1)   # (read-only, as Windows marks them)
    roll = tmp_path / "Pictures" / "Camera Roll"
    roll.mkdir(parents=True)
    (roll / "desktop.ini").write_text("[.ShellClassInfo]")
    (tmp_path / "Pictures" / "Empty").mkdir()
    assert _offered(tmp_path, keep=[]) == [os.path.join("Pictures", "Empty")]
    assert not emptyfolders.still_empty(roll, [])
    assert emptyfolders.still_empty(tmp_path / "Pictures" / "Empty", [])


def test_hosts_lines_that_block_a_site_are_good_to_know(tmp_path, monkeypatch):
    hosts = tmp_path / "hosts"
    hosts.write_text("0.0.0.0 www.youtube.com\n127.0.0.1 facebook.com\n6.6.6.6 paypal.com\n::1 localhost\n")
    monkeypatch.setattr(scan, "hosts_path", lambda: hosts)
    checks = scan.Checks()
    checks.hosts()
    levels = {f.detail["site"]: f.level for f in checks.findings}
    assert levels == {"www.youtube.com": "info", "facebook.com": "info", "paypal.com": "warn"}
    blocked = next(f for f in checks.findings if f.detail["site"] == "www.youtube.com")
    assert "blocks" in scanpage.finding_text({"kind": "hosts", "path": str(hosts), "detail": blocked.detail})


def test_defenders_notes_are_only_read_since_the_scan_began():
    # ([datetime]'1970-01-01'.AddSeconds() calls AddSeconds on the text: every note ever would come back)
    script = scan.DETECTIONS_SCRIPT % 1_700_000_000
    assert "'1970-01-01'.AddSeconds" not in script
    assert "[datetime]::new(1970, 1, 1, 0, 0, 0, [DateTimeKind]::Utc)).AddSeconds(1700000000)" in script


def test_a_32bit_line_naming_system32_is_found_in_syswow64(tmp_path, monkeypatch):
    windows = tmp_path / "Windows"
    (windows / "SysWOW64").mkdir(parents=True)
    (windows / "System32").mkdir()
    (windows / "SysWOW64" / "helper.exe").write_bytes(b"MZ")
    monkeypatch.setenv("SystemRoot", str(windows))
    asked = str(windows / "System32" / "helper.exe")
    assert startup._as_32bit(asked) == str(windows / "SysWOW64" / "helper.exe")
    gone = str(windows / "System32" / "nothing.exe")
    assert startup._as_32bit(gone) == gone
    assert startup.is_gone(startup._as_32bit(asked)) is False
