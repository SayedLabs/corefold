"""Corefold installs for the whole PC (Program Files, a folder only administrators can change): the installer
asks Windows for permission itself and starts Corefold again as the person; Corefold waits for the installer
to ask it to close, so a "No" at Windows' prompt leaves it running."""
import subprocess
from pathlib import Path

from corefold import updater

ISS = (Path(__file__).resolve().parent.parent / "installer" / "corefold.iss").read_text("utf-8-sig")


def test_installer_is_for_the_whole_pc():
    assert "PrivilegesRequired=admin" in ISS
    assert "PrivilegesRequired=lowest" not in ISS


def test_corefold_is_started_again_as_the_person():
    code = ISS[ISS.index("procedure CurStepChanged"):ISS.index("function InitializeUninstall")]
    assert "ExecAsOriginalUser(ExpandConstant('{app}\\{#AppExe}'), '--background'" in code
    assert "  Exec(ExpandConstant('{app}\\{#AppExe}')" not in code


def test_old_copy_is_deleted_only_from_its_usual_folder():
    code = ISS[ISS.index("function UserCopyIsDeletable"):ISS.index("procedure RemoveOldVersion")]
    assert "IsLink(Dir)" in code and "{localappdata}\\Programs" in code


def test_the_installer_is_handed_back_to_watch(monkeypatch, tmp_path):
    started = []

    class FakeProcess:
        def poll(self):
            return 2

    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: started.append(args) or FakeProcess())
    monkeypatch.setattr("corefold.settings_backup.keep_copy", lambda version: None)
    proc = updater.run_installer(tmp_path / "Corefold-Setup-9.9.9.exe")
    assert started and started[0][1:] == ["/SILENT", "/SP-", "/SUPPRESSMSGBOXES", "/NORESTART"]
    assert proc.poll() in updater.NOT_ALLOWED  # (ended before asking Corefold to close: nothing installed)
