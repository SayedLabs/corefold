"""Checks for the administrator helper's plan: undoing a clean-up takes back only Corefold's own deletions at
restart, and the helper stops when its plan or the backup's list changed after the administrator prompt."""
import json
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from corefold import uninstall as un


def test_only_corefolds_deletions_come_out_of_windows_list():
    app = r"C:\Program Files\Acme"
    entries = [r"\??\C:\Windows\WinSxS\Temp\a.tmp", "",  # Windows Update's own
               "\\??\\" + app + r"\acme.dll", "",  # Corefold's
               "\\??\\" + app + r"\acme.dll", "",  # the same file, asked for again by someone else
               "\\??\\" + app + r"\setup.exe", r"!\??\C:\Program Files\Acme\setup.new",  # a rename: never
               "*1\\??\\" + app, "",  # Corefold's: the folder once it's empty (as Windows 11 writes it)
               ""]  # (the list's closing empty string)
    kept, dropped = un.drop_pending(entries, [app + r"\ACME.dll", app, app + r"\setup.exe"])
    assert dropped == 2
    assert kept == [r"\??\C:\Windows\WinSxS\Temp\a.tmp", "", "\\??\\" + app + r"\acme.dll", "",
                    "\\??\\" + app + r"\setup.exe", r"!\??\C:\Program Files\Acme\setup.new"]


def test_a_list_that_isnt_pairs_is_left_alone():
    entries = [r"\??\C:\a", "", r"\??\C:\b"]
    assert un.drop_pending(entries, [r"C:\a"]) == (entries, 0)


class FakeRegistry:
    """Windows' list of what to do at restart, without needing administrator."""
    HKEY_LOCAL_MACHINE, KEY_QUERY_VALUE, KEY_SET_VALUE, KEY_WOW64_64KEY, REG_MULTI_SZ = 1, 2, 4, 8, 7

    def __init__(self, entries):
        self.values = {"PendingFileRenameOperations": (entries, 7)} if entries is not None else {}

    @contextmanager
    def OpenKey(self, root, path, _reserved, access):
        assert (root, path) == (1, un.SESSION_MANAGER) and access & self.KEY_SET_VALUE
        yield self

    def QueryValueEx(self, _k, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name]

    def SetValueEx(self, _k, name, _reserved, kind, value):
        self.values[name] = (value, kind)

    def DeleteValue(self, _k, name):
        del self.values[name]


def test_cancelling_keeps_the_others_and_removes_an_emptied_list(monkeypatch):
    reg = FakeRegistry([r"\??\C:\Apps\Acme\a.dll", "", r"\??\C:\Windows\x.tmp", "", ""])
    monkeypatch.setattr(un, "_winreg", lambda: reg)
    assert un.cancel_on_restart([r"C:\Apps\Acme\a.dll"]) == 1
    assert reg.values["PendingFileRenameOperations"] == ([r"\??\C:\Windows\x.tmp", ""], 7)
    reg = FakeRegistry([r"\??\C:\Apps\Acme\a.dll", ""])
    monkeypatch.setattr(un, "_winreg", lambda: reg)
    assert un.cancel_on_restart([r"C:\Apps\Acme\a.dll"]) == 1 and reg.values == {}
    reg = FakeRegistry(None)
    monkeypatch.setattr(un, "_winreg", lambda: reg)
    assert un.cancel_on_restart([r"C:\Apps\Acme\a.dll"]) == 0 and reg.values == {}


def _backup(tmp_path, items) -> Path:
    folder = tmp_path / "backup"
    folder.mkdir()
    (folder / "backup.json").write_text(json.dumps({"apps": ["Acme"], "time": 1, "items": items}), "utf-8")
    return folder


def test_a_cleanup_writes_down_what_windows_deletes_at_restart(tmp_path, monkeypatch):
    src = tmp_path / "Acme"
    src.mkdir()
    (src / "acme.dll").write_text("in use")
    monkeypatch.setattr(un, "item_allowed", lambda item: True)
    monkeypatch.setattr(un, "_safe_folder", lambda path: True)
    monkeypatch.setattr(un, "_move", lambda a, b: (_ for _ in ()).throw(PermissionError("in use")))
    monkeypatch.setattr(un, "_move_files", lambda a, b: [str(src / "acme.dll")])
    monkeypatch.setattr(un, "remove_on_restart", lambda paths: list(paths) + [str(src)])
    folder = tmp_path / "backup"
    folder.mkdir()
    item = un.Leftover(kind="folder", path=str(src), reason="install", level=3)
    out = un.remove([item], folder, ["Acme"], admin=True)
    assert out["done"][0]["on_restart"] == 1
    saved = json.loads((folder / "backup.json").read_text("utf-8"))["items"]
    assert saved[0]["pending"] == [str(src / "acme.dll"), str(src)] and saved[0]["stored"] == ""


def test_undoing_takes_back_only_deletions_inside_the_folder_put_back(tmp_path, monkeypatch):
    target = tmp_path / "Acme"
    target.mkdir()
    (target / "acme.dll").write_text("stayed behind")
    folder = _backup(tmp_path, [
        {"kind": "folder", "path": str(target), "stored": "files/1/Acme", "pending": [
            str(target / "acme.dll"), str(target), r"C:\Windows\System32\drivers\etc\hosts"]},
        {"kind": "folder", "path": str(tmp_path / "Only"), "stored": "", "pending": [str(tmp_path / "Only" / "x")]}])
    stored = folder / "files" / "1" / "Acme"
    stored.mkdir(parents=True)
    (stored / "readme.txt").write_text("moved")
    monkeypatch.setattr(un, "item_allowed", lambda item: True)
    cancelled = []
    monkeypatch.setattr(un, "cancel_on_restart", lambda paths: cancelled.append(paths) or len(paths))

    out = un.restore(folder)  # (not as administrator: those wait for it, untouched)
    assert out["done"] == 0 and len(out["failed"]) == 2 and not cancelled
    assert not (target / "readme.txt").exists() and stored.exists()

    out = un.restore(folder, admin=True)
    assert out["done"] == 2 and not out["failed"]
    assert cancelled == [[str(target / "acme.dll"), str(target)], [str(tmp_path / "Only" / "x")]]
    assert (target / "readme.txt").read_text() == "moved" and (target / "acme.dll").exists()


def _helper_home(tmp_path):
    home = tmp_path / "Corefold"
    (home / "helper").mkdir(parents=True)
    folder = home / un.BACKUP_DIR.name / "2026 Acme"
    folder.mkdir(parents=True)
    (folder / "backup.json").write_text(json.dumps({"apps": ["Acme"], "items": []}), "utf-8")
    return home, folder


def _run_through_helper(monkeypatch, home, change=None):
    """run_elevated as it is, with the administrator copy run here; change(plan) acts after the prompt."""
    monkeypatch.setattr(un, "BACKUP_DIR", home / un.BACKUP_DIR.name)
    monkeypatch.setattr(un, "_let_questions_show", lambda: None)
    monkeypatch.setattr(un, "wait_process", lambda handle, cancel=None: 0)
    codes = []

    def shell_run(exe, args, admin=False, show=1, cwd=None):
        argv = re.findall(r'"([^"]*)"', args)
        if change:
            change(Path(argv[argv.index("--restore") + 1]))
        codes.append(un.helper_main(argv))
        return SimpleNamespace()
    monkeypatch.setattr(un, "shell_run", shell_run)
    return codes


def test_the_helper_acts_on_the_plan_written_before_the_prompt(tmp_path, monkeypatch):
    home, folder = _helper_home(tmp_path)
    codes = _run_through_helper(monkeypatch, home)
    calls = []
    monkeypatch.setattr(un, "restore", lambda f, admin=False, manifest=None: calls.append((f, manifest)) or
                        {"done": 1, "failed": []})
    assert un.run_elevated(["Corefold.exe"], "restore", {"folder": str(folder)}) == {"done": 1, "failed": []}
    assert codes == [0] and calls == [(folder, un.manifest_digest(folder))]
    assert not list((home / "helper").iterdir())


def test_the_helper_stops_when_its_plan_changed_after_the_prompt(tmp_path, monkeypatch):
    home, folder = _helper_home(tmp_path)
    other = home / un.BACKUP_DIR.name / "someone else's"

    def swap(plan):
        payload = json.loads(plan.read_text("utf-8"))
        plan.write_text(json.dumps(dict(payload, folder=str(other))), "utf-8")
    codes = _run_through_helper(monkeypatch, home, swap)
    calls = []
    monkeypatch.setattr(un, "restore", lambda *a, **k: calls.append(a) or {"done": 1, "failed": []})
    assert un.run_elevated(["Corefold.exe"], "restore", {"folder": str(folder)}) == {"error": "no result"}
    assert codes == [2] and not calls


def test_the_helper_stops_when_the_backups_list_changed_after_the_prompt(tmp_path, monkeypatch):
    home, folder = _helper_home(tmp_path)
    monkeypatch.setattr(un, "item_allowed", lambda item: True)
    imported = []
    monkeypatch.setattr(un, "_reg_exe", lambda *a, **k: imported.append(a) or True)

    def edit(_plan):
        (folder / "backup.json").write_text(json.dumps({"items": [
            {"kind": "key", "path": r"HKLM\Software\Acme", "stored": "registry/x.reg"}]}), "utf-8")
    codes = _run_through_helper(monkeypatch, home, edit)
    out = un.run_elevated(["Corefold.exe"], "restore", {"folder": str(folder)})
    assert codes == [0] and "changed" in out["error"] and not imported


def test_a_plan_without_its_fingerprint_is_refused(tmp_path, monkeypatch):
    home, folder = _helper_home(tmp_path)
    plan = home / "helper" / "restore-1.json"
    plan.write_text(json.dumps({"folder": str(folder), "manifest": un.manifest_digest(folder)}), "utf-8")
    monkeypatch.setattr(un, "restore", lambda *a, **k: pytest.fail("acted on a plan it couldn't check"))
    assert un.helper_main(["--restore", str(plan)]) == 2
    assert un.helper_main(["--restore", str(plan), "--plan-sha256", "0" * 64]) == 2
