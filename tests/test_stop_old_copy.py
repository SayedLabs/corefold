"""Stopping the old-named copy: only the program started from the given file, never another one with the
same name somewhere else (rea, 1.14.1: the installer and the app killed every Satsuma.exe)."""
import os
import shutil
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows' processes")


def _start(path):
    return subprocess.Popen([str(path), "-n", "60", "127.0.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def test_only_the_copy_at_that_path_is_stopped(tmp_path):
    from corefold.uninstall import stop_programs_at
    ping = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "PING.EXE")
    ours, other = tmp_path / "old" / "Satsuma.exe", tmp_path / "elsewhere" / "Satsuma.exe"
    for p in (ours, other):
        p.parent.mkdir()
        shutil.copy(ping, p)
    a, b = _start(ours), _start(other)
    try:
        time.sleep(0.5)
        assert a.poll() is None and b.poll() is None
        assert stop_programs_at([ours]) == 1
        a.wait(timeout=10)
        assert b.poll() is None  # the other one with the same name runs on
    finally:
        for p in (a, b):
            if p.poll() is None:
                p.kill()
                p.wait(timeout=10)


def test_nothing_running_there_stops_nothing(tmp_path):
    from corefold.uninstall import stop_programs_at
    assert stop_programs_at([tmp_path / "Satsuma.exe"]) == 0
