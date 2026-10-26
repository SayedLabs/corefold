"""Unlock and a folder a program is "in": a Command Prompt opened in the folder, or a program started
from inside it, holds no file there (Restart Manager doesn't see it), yet Windows won't delete the
folder. Unlock finds those programs too, and closing them frees the folder."""
import shutil
import subprocess
import sys
import time

import pytest

from corefold import unlock


def test_a_program_in_the_folder_is_found(monkeypatch, tmp_path):
    monkeypatch.setattr(unlock, "files_of", lambda paths: ([], False))
    monkeypatch.setattr(unlock, "process_names", lambda: {50: "cmd.exe", 60: "notepad.exe", 4: "System"})
    monkeypatch.setattr(unlock, "_exe_of", lambda pid: "C:\\Windows\\System32\\" + {50: "cmd.exe", 60: "notepad.exe"}[pid])
    here = str(tmp_path / "Inner")
    monkeypatch.setattr(unlock, "current_folder", lambda pid: here if pid == 50 else "C:\\Users\\me")
    for fn in ("_started", "windows_of"):
        monkeypatch.setattr(unlock, fn, lambda *a, **k: 0)
    monkeypatch.setattr(unlock, "_can_close", lambda pid: True)
    found = unlock.find([tmp_path])
    assert [(p.pid, p.name, p.closable) for p in found.programs] == [(50, "cmd", True)]
    assert unlock.find([tmp_path / "Other"]).programs == []  # (not a folder: nothing to be "in")


def test_a_folder_with_the_same_start_is_another_folder():
    assert unlock._inside("C:\\Photos\\2026", ("c:\\photos\\",))
    assert not unlock._inside("C:\\Photos 2026", ("c:\\photos\\",))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own folders and programs")
def test_on_windows(tmp_path):
    folder = tmp_path / "held"
    (folder / "inner").mkdir(parents=True)
    (folder / "a.txt").write_text("x")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=folder / "inner",
                            creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        time.sleep(0.5)
        assert unlock.current_folder(proc.pid).rstrip("\\").lower() == str(folder / "inner").lower()
        found = unlock.find([folder])
        held = [p for p in found.programs if p.pid == proc.pid]
        assert held and held[0].closable, [(p.pid, p.name) for p in found.programs]
        assert unlock.force_close(held[0])
        shutil.rmtree(folder)  # (free now)
    finally:
        proc.kill()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own Command Prompt")
@pytest.mark.parametrize("cmd", ["C:\\Windows\\System32\\cmd.exe", "C:\\Windows\\SysWOW64\\cmd.exe"])
def test_a_command_prompt_in_the_folder(tmp_path, cmd):
    """The usual one, both kinds: 64-bit, and 32-bit (which keeps its own copy of its folder)."""
    import os
    if not os.path.exists(cmd):
        pytest.skip("no 32-bit Windows on this PC")
    folder = tmp_path / "work"
    folder.mkdir()
    proc = subprocess.Popen([cmd, "/k"], cwd=folder, stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        time.sleep(0.8)
        with pytest.raises(OSError):
            folder.rmdir()  # (Windows says no, though no file is open there)
        held = [p for p in unlock.find([folder]).programs if p.pid == proc.pid]
        assert held and held[0].name.lower() == "cmd"
        assert unlock.force_close(held[0])
        time.sleep(0.3)
        folder.rmdir()
    finally:
        proc.kill()
