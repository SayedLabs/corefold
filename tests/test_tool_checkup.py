"""What the full check of every tool (before 2.0.1) found in Rename, Check and Backup, kept from coming back:
an undo that gives every name back (swapped names, a chain of names, a change of case), a new name that's only
".jpg", a whole line pasted from a list of codes, one file of a list that can't be read, and a backup drive that's
nearly full."""
import os
from pathlib import Path
from unittest import mock

from corefold import backup, checker, renamer
from corefold.moretools import undo_rename


def _files(folder: Path, *names: str) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for n in names:
        (folder / n).write_text(n)
        out.append(folder / n)
    return out


def test_undo_gives_swapped_names_back(tmp_path):
    a, b = _files(tmp_path, "a.txt", "b.txt")
    t = tmp_path / "t"
    a.rename(t)
    b.rename(a)
    t.rename(b)  # (a ↔ b, as the renaming does it)
    assert undo_rename([(b, a), (a, b)]) == 2
    assert a.read_text() == "a.txt" and b.read_text() == "b.txt"


def test_undo_gives_a_chain_of_names_back(tmp_path):
    a, b = _files(tmp_path, "1.jpg", "2.jpg")
    c = tmp_path / "3.jpg"
    b.rename(c)
    a.rename(b)  # (1 → 2, 2 → 3: numbers moved up by one)
    assert undo_rename([(b, a), (c, b)]) == 2
    assert a.read_text() == "1.jpg" and b.read_text() == "2.jpg" and not c.exists()


def test_undo_gives_a_change_of_case_back(tmp_path):
    (old,) = _files(tmp_path, "photo.jpg")
    new = tmp_path / "Photo.jpg"
    old.rename(new)
    assert undo_rename([(new, old)]) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["photo.jpg"]


def test_undo_leaves_a_name_another_file_took(tmp_path):
    a, = _files(tmp_path, "a.txt")
    b = tmp_path / "b.txt"
    a.rename(b)
    a.write_text("someone else's")  # (a new file got the old name since)
    assert undo_rename([(b, a)]) == 0
    assert a.read_text() == "someone else's" and b.read_text() == "a.txt"


def test_a_new_name_with_nothing_before_the_extension_cant_be_used(tmp_path):
    files = _files(tmp_path, "photo.jpg", "photo 2.jpg")
    rows = renamer.plan(files, {"find": "photo"})
    by_old = {r.old.name: r for r in rows}
    assert by_old["photo.jpg"].state == renamer.EMPTY
    assert by_old["photo 2.jpg"].state == renamer.OK and by_old["photo 2.jpg"].new.name == "2.jpg"
    assert renamer.pairs(rows) == []


def test_a_whole_line_from_a_list_of_codes_can_be_pasted():
    code = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    for pasted in (code, code.upper(), f"SHA256: {code}", f"{code}  setup.exe", f"{code} *setup.exe",
                   f"SHA256 (setup.exe) = {code}", f"  {code}\n"):
        assert checker.clean(pasted) == code, pasted
    assert checker.clean("setup.exe") == "" and checker.clean("not a code at all") == ""
    assert checker.matches({"sha256": code}, f"{code}  setup.exe") == ("sha256", True)


def test_a_list_goes_on_when_one_of_its_files_cant_be_read(tmp_path):
    a, b = _files(tmp_path, "a.bin", "b.bin")
    lines = [f"{checker.fingerprints(f, algos=('sha256',))['sha256']}  {f.name}" for f in (a, b)]
    lst = tmp_path / "SHA256SUMS"
    lst.write_text("\n".join(lines + ["0" * 64 + "  gone.bin"]))
    real = checker.fingerprints

    def locked(path, *args, **kwargs):
        if path.name == "a.bin":
            raise PermissionError(13, "Access is denied", str(path))
        return real(path, *args, **kwargs)

    with mock.patch.object(checker, "fingerprints", side_effect=locked):
        got = {e.name: e.state for e in checker.check_list(lst)}
    assert got == {"a.bin": checker.UNREADABLE, "b.bin": checker.SAME, "gone.bin": checker.MISSING}


def test_the_check_page_says_what_went_wrong_in_its_own_words():
    from corefold import checkpage
    denied = PermissionError(13, "Access is denied", "C:\\Users\\me\\a.iso")
    denied.winerror = 5
    said = checkpage._why(denied)
    assert "Access is denied" not in said and "Errno" not in said and said


def _w(p: Path, size: int):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)


def test_a_file_too_big_for_the_drive_doesnt_stop_the_others(tmp_path, monkeypatch):
    docs = tmp_path / "Docs"
    _w(docs / "big.vhdx", 5000)
    _w(docs / "small.txt", 10)
    dest = tmp_path / "E"
    dest.mkdir()
    plan = backup.Plan(sources=[str(docs)], dest=str(dest))
    monkeypatch.setattr(backup, "ROOM", 100)
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda _p: type("U", (), {"free": 1000})())
    s = backup.run(plan)
    assert s.copied == 1 and s.problem == "The backup drive is full"
    assert [Path(f).name for f, _why in s.failed] == ["big.vhdx"]
    assert (backup.home(plan) / "Docs" / "small.txt").exists()


def test_old_copies_make_room_before_a_full_drive_is_filled(tmp_path, monkeypatch):
    docs = tmp_path / "Docs"
    _w(docs / "a.txt", 10)
    dest = tmp_path / "E"
    dest.mkdir()
    plan = backup.Plan(sources=[str(docs)], dest=str(dest), keep_days=30)
    old = backup.home(plan) / backup.OLDER / "2020-01-01 10-00"
    _w(old / "Docs" / "huge.bin", 50)

    def usage(_p):
        return type("U", (), {"free": 10 if old.exists() else 10_000})()

    monkeypatch.setattr(backup, "ROOM", 100)
    monkeypatch.setattr(backup.shutil, "disk_usage", usage)
    s = backup.run(plan)
    assert not old.exists() and s.copied == 1 and not s.problem
    assert os.path.exists(backup.home(plan) / "Docs" / "a.txt")


def test_a_backup_by_itself_only_goes_to_the_drive_that_has_it(tmp_path):
    docs = tmp_path / "Docs"
    _w(docs / "a.txt", 10)
    dest = tmp_path / "E"
    dest.mkdir()
    plan = backup.Plan(sources=[str(docs)], dest=str(dest))
    assert backup.due(plan, now=10**10)  # (the first one: wherever it's set to go)
    backup.finished(plan, backup.run(plan), now=1000.0)
    assert backup.due(plan, now=1000.0 + 25 * 3600)
    import shutil
    shutil.rmtree(backup.home(plan))  # (another USB drive got E:)
    assert not backup.due(plan, now=1000.0 + 25 * 3600)


def test_space_and_the_uninstaller_say_what_went_wrong_in_their_own_words(qapp):
    from corefold import appspage, spacepage
    denied = PermissionError(13, "Access is denied", "C:\\Users\\me\\a.iso")
    denied.winerror = 5
    for page in (spacepage, appspage):
        said = page._plain_error(denied)
        assert said and "Access is denied" not in said and "Errno" not in said


def test_numpy_doesnt_set_memory_aside_for_every_core():
    # (OpenBLAS's buffers for each core: ~750 MB committed on a 16-core PC while Rakiza Core sat idle)
    text = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    assert 'os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")' in text
    assert text.index("OPENBLAS_NUM_THREADS") < text.index("from corefold")


class _FakeRegistry:
    """Just enough of winreg for the right-click menu: keys with a default value."""
    HKEY_CURRENT_USER = "HKCU"
    REG_SZ, REG_DWORD, KEY_READ, KEY_WRITE = 1, 4, 1, 2

    def __init__(self):
        self.keys: dict[str, dict] = {}

    class _Key:
        def __init__(self, reg, path):
            self.reg, self.path = reg, path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def CreateKey(self, _root, path):  # noqa: N802
        self.keys.setdefault(path.lower(), {})
        return self._Key(self, path.lower())

    def OpenKey(self, _root, path, *a):  # noqa: N802
        if path.lower() not in self.keys:
            raise OSError(2, "not there")
        return self._Key(self, path.lower())

    def SetValueEx(self, key, name, _r, _t, value):  # noqa: N802
        self.keys[key.path][name] = value

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in self.keys[key.path]:
            raise OSError(2, "no value")
        return self.keys[key.path][name], 1

    def EnumKey(self, key, i):  # noqa: N802
        prefix = key.path + "\\"
        subs = sorted({k[len(prefix):].split("\\")[0] for k in self.keys if k.startswith(prefix)})
        if i >= len(subs):
            raise OSError(259, "no more")
        return subs[i]

    def DeleteKey(self, _root, path):  # noqa: N802
        self.keys.pop(path.lower(), None)


def test_a_right_click_entry_another_copy_wrote_over_is_written_again(monkeypatch):
    import sys
    from corefold import shellmenu
    reg = _FakeRegistry()
    monkeypatch.setitem(sys.modules, "winreg", reg)

    def mine(*args):
        return '"C:\\Program Files\\Rakiza Core\\RakizaCore.exe" ' + " ".join(args)

    sig = shellmenu.sync_unlock(True, "", mine, "")
    key = shellmenu.unlock_plan(mine)[0]
    command_key = rf"{key.root}\shell\{key.name}\command".lower()
    assert reg.keys[command_key][""] == mine("--unlock", '"%1"')
    assert shellmenu.in_registry(shellmenu.unlock_plan(mine), mine)
    # a copy run from its code writes its own command over it (the settings still hold the signature)
    reg.keys[command_key][""] = '"C:\\dev\\pythonw.exe" "C:\\dev\\main.py" --unlock "%1"'
    assert not shellmenu.in_registry(shellmenu.unlock_plan(mine), mine)
    assert shellmenu.sync_unlock(True, sig, mine, "") == sig
    assert reg.keys[command_key][""] == mine("--unlock", '"%1"')


def test_a_helpers_message_arriving_while_rakiza_core_closes_isnt_an_error(qapp):
    from PySide6.QtNetwork import QLocalSocket
    from corefold.app import Inbox
    conn = QLocalSocket()
    inbox = Inbox(conn)
    got = []
    inbox.message.connect(got.append)
    import shiboken6
    shiboken6.delete(conn)  # (the connection and its reader go, as when the window's objects are deleted on quitting)
    inbox._check()   # (what was still queued for it then: no "Internal C++ object already deleted")
    inbox._finish()
    assert inbox.done and got == []
