"""The second full check of every tool (after 2.0.1): what it found, so it stays fixed."""
import os
from pathlib import Path

from corefold import backup


def _w(p: Path, text: str, when: float | None = None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    if when is not None:
        os.utime(p, (when, when))


def test_backup_to_a_drive_that_also_has_a_folder_to_keep(tmp_path):
    """The backup goes in "<PC> backup" on the drive: another folder on that drive isn't inside the backup (it was
    refused as if it were), but a folder inside the backup itself still is, and a backup inside a kept folder."""
    drive = tmp_path / "D"
    photos = drive / "Photos"
    photos.mkdir(parents=True)
    assert backup.problem([str(photos)], str(drive)) == ""
    inside = drive / f"{backup.pc_name()} backup" / "Photos"
    inside.mkdir(parents=True)
    assert backup.problem([str(inside)], str(drive)) == "A folder to keep safe can't be inside the backup"
    assert backup.problem([str(drive)], str(drive)) == "The backup can't go inside a folder it keeps safe"
    assert backup.problem([str(drive)], str(photos)) == "The backup can't go inside a folder it keeps safe"
    # and it really backs up, without copying the backup into itself
    _w(photos / "a.jpg", "a", 1_700_000_000)
    plan = backup.Plan(sources=[str(photos)], dest=str(drive))
    s = backup.run(plan, now=1_800_000_000)
    assert s.copied == 1 and not s.problem
    assert (backup.home(plan) / "Photos" / "a.jpg").read_text() == "a"


def test_backup_clears_a_half_copy_left_by_a_run_that_never_finished(tmp_path):
    docs = tmp_path / "Docs"
    _w(docs / "a.txt", "a", 1_700_000_000)
    dest = tmp_path / "E"
    dest.mkdir()
    plan = backup.Plan(sources=[str(docs)], dest=str(dest))
    backup.run(plan, now=1_800_000_000)
    base = backup.home(plan) / "Docs"
    half = base / "deep" / "big.mov"
    half = half.with_name(half.name + backup.PARTIAL)
    _w(half, "half")  # (the PC went off in the middle of a copy)
    backup.run(plan, now=1_800_090_000)
    assert not half.exists() and not (base / "deep").exists()
    assert (base / "a.txt").read_text() == "a" and (docs / "a.txt").read_text() == "a"


def test_backup_takes_out_folders_left_empty_at_every_level(tmp_path):
    docs = tmp_path / "Docs"
    _w(docs / "a" / "b" / "c" / "x.txt", "x", 1_700_000_000)
    _w(docs / "keep.txt", "k", 1_700_000_000)
    dest = tmp_path / "E"
    dest.mkdir()
    plan = backup.Plan(sources=[str(docs)], dest=str(dest))
    backup.run(plan, now=1_800_000_000)
    (docs / "a" / "b" / "c" / "x.txt").unlink()
    backup.run(plan, now=1_800_090_000)
    base = backup.home(plan) / "Docs"
    assert not (base / "a").exists()  # (not only c: b and a were left empty too)
    (old,) = backup.versions(plan)
    assert (old / "Docs" / "a" / "b" / "c" / "x.txt").read_text() == "x"


def test_check_reads_a_pasted_line_whose_file_name_has_md5_or_sha256_in_it():
    from corefold import checker
    code = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    for line in (f"{code}  ubuntu-md5-check.iso", f"{code} *tool-sha256.exe", f"SHA256 (app-sha1.exe) = {code}",
                 f"md5sum-helper.zip {code}", f"SHA-256: {code.upper()}", code):
        assert checker.clean(line) == code, line


def test_check_reads_a_list_of_codes_saved_as_utf16(tmp_path):
    """Windows PowerShell 5's ">" saves what certutil or any command printed as UTF-16: such a list read as nothing."""
    import hashlib
    from corefold import checker
    (tmp_path / "setup.exe").write_bytes(b"hello")
    code = hashlib.sha256(b"hello").hexdigest()
    (tmp_path / "SHA256SUMS").write_bytes(f"﻿{code}  setup.exe\r\n".encode("utf-16-le"))
    (tmp_path / "setup.exe.sha256").write_bytes(
        f"﻿SHA256 hash of setup.exe:\r\n{code}\r\nCertUtil: -hashfile command completed successfully.\r\n"
        .encode("utf-16-le"))
    for name in ("SHA256SUMS", "setup.exe.sha256"):
        assert [(e.name, e.state) for e in checker.check_list(tmp_path / name)] == [("setup.exe", checker.SAME)]


class _QuickRegistry:
    """Just enough of winreg for the whole right-click menu (a few thousand keys), quick to go through."""
    HKEY_CURRENT_USER = "HKCU"
    REG_SZ, REG_DWORD, KEY_READ, KEY_WRITE = 1, 4, 1, 2

    def __init__(self):
        self.keys: dict[str, dict] = {}
        self.subs: dict[str, set] = {}

    class _Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _add(self, path):
        path = path.lower()
        while path not in self.keys:
            self.keys[path] = {}
            parent, _, name = path.rpartition("\\")
            self.subs.setdefault(parent, set()).add(name)
            if not parent:
                break
            path = parent
        return path

    def CreateKey(self, _root, path):  # noqa: N802
        self._add(path)
        return self._Key(path.lower())

    def OpenKey(self, _root, path, *a):  # noqa: N802
        if path.lower() not in self.keys:
            raise OSError(2, "not there")
        return self._Key(path.lower())

    def SetValueEx(self, key, name, _r, _t, value):  # noqa: N802
        self.keys[key.path][name] = value

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in self.keys[key.path]:
            raise OSError(2, "no value")
        return self.keys[key.path][name], 1

    def EnumKey(self, key, i):  # noqa: N802
        subs = sorted(self.subs.get(key.path, ()))
        if i >= len(subs):
            raise OSError(259, "no more")
        return subs[i]

    def DeleteKey(self, _root, path):  # noqa: N802
        path = path.lower()
        if self.subs.get(path):
            raise OSError(5, "has keys")
        self.keys.pop(path, None)
        parent, _, name = path.rpartition("\\")
        self.subs.get(parent, set()).discard(name)

    def put(self, path, values):
        self._add(path)
        self.keys[path.lower()].update(values)

    def drop_tree(self, path):
        path = path.lower()
        for name in list(self.subs.get(path, ())):
            self.drop_tree(path + "\\" + name)
        self.DeleteKey(None, path)


def _menu_settings(**changes):
    from corefold import settings as settings_mod
    s = settings_mod.Settings()
    s.context_menu = True
    s.tools_off = []
    for k, v in changes.items():
        setattr(s, k, v)
    return s


def _mine(*args):
    return '"C:\\Program Files\\Rakiza Core\\RakizaCore.exe" ' + " ".join(args)


def _dev(*args):
    return '"C:\\rea-work\\.venv\\Scripts\\pythonw.exe" "C:\\rea-work\\src\\main.py" ' + " ".join(args)


def test_the_every_file_entry_another_copy_wrote_is_written_again(monkeypatch):
    """Found on the owner's PC: "every file" (*) still started a copy run from its code after 2.0.1. Its entry is
    one of a few among more than a thousand, and the look at a sample of them missed it."""
    import sys
    from corefold import shellmenu
    reg = _QuickRegistry()
    monkeypatch.setitem(sys.modules, "winreg", reg)
    verbs = shellmenu.plan(_menu_settings(), _mine)
    shellmenu.write(verbs, _mine, "")
    assert shellmenu.in_registry(verbs, _mine, others=True)
    # the other copy, with the wheel style, wrote one plain entry for every file
    base = rf"{shellmenu.ANY_ROOT}\shell\{shellmenu.VERB}".lower()
    reg.drop_tree(base)
    reg.put(base, {"MUIVerb": "Convert with Rakiza Core"})
    reg.put(base + r"\command", {"": _dev("--convert", '"%1"')})
    assert not shellmenu.in_registry(verbs, _mine, others=True)
    shellmenu.write(verbs, _mine, "")
    assert shellmenu.in_registry(verbs, _mine, others=True)
    assert not any(_dev("").strip() in str(v) for v in reg.keys.values())


def test_an_entry_another_copy_wrote_where_this_one_has_none_is_taken_out(monkeypatch):
    import sys
    from corefold import shellmenu
    reg = _QuickRegistry()
    monkeypatch.setitem(sys.modules, "winreg", reg)
    verbs = shellmenu.plan(_menu_settings(context_menu=False), _mine)  # (the menu switched off here)
    assert not shellmenu.strays(verbs)
    stray = rf"{shellmenu.FOLDER_ROOT}\shell\{shellmenu.VERB}".lower()
    reg.put(stray, {"MUIVerb": "Rakiza Core"})
    reg.put(stray + r"\command", {"": _dev("--convert", '"%1"')})
    assert shellmenu.strays(verbs) and shellmenu.strays([])
    assert not shellmenu.in_registry(verbs, _mine, others=True)
    shellmenu.remove(unlock=False)
    assert not shellmenu.strays([])
    # the Unlock entry's own look doesn't count the menu's entries as strays (it's written on its own)
    shellmenu.write(shellmenu.plan(_menu_settings(), _mine), _mine, "")
    shellmenu.sync_unlock(True, "", _mine, "")
    assert shellmenu.in_registry(shellmenu.unlock_plan(_mine), _mine)
