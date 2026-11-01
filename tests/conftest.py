"""Shared set-up for the pytest tests (pytest-qt gives each test a `qtbot` and a running QApplication).

Corefold's settings and logs go to a throwaway folder, never to the real %APPDATA%. The tests are meant
for Windows; on Linux or macOS the few Windows-only modules are replaced with stand-ins so the widgets
can still be checked there (with QT_QPA_PLATFORM=offscreen when there's no screen)."""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="corefold-pytest-"))
os.environ["APPDATA"] = str(_TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(_TMP / "local")
# File Organizer rests in a built Corefold (tools.PAUSED_TOOLS); the tests keep trying its kept code, and
# tests/test_organizer_resting.py checks it stays hidden while it rests
os.environ["COREFOLD_WAKE_ORGANIZER"] = "1"

if sys.platform != "win32":
    import ctypes
    ctypes.windll = mock.MagicMock()
    _winreg = mock.MagicMock()
    _winreg.EnumKey.side_effect = OSError  # (no keys under any key: a MagicMock "has" keys for ever, and
    _winreg.EnumValue.side_effect = OSError  # the shell menu's clean-up recursed through them without end)
    sys.modules.setdefault("winreg", _winreg)
    sys.modules.setdefault("winsound", mock.MagicMock())


# Qt sometimes crashes in Windows' own tidy-up of its DLLs after pytest has printed "N passed" (0xC0000005 /
# 0xC0000409, after pytest's faulthandler is already off), and pwsh then reports a bare "exit code 1" for a
# run where every test passed. The script tests end through scripts/testexit.py for the same reason; pytest
# does it here, as the very last thing, with its own result as the exit code.
_EXIT = {}


def pytest_sessionfinish(session, exitstatus):
    _EXIT["status"] = int(exitstatus)


def _finish_now(code: int):
    import importlib.util
    spec = importlib.util.spec_from_file_location("testexit", ROOT / "scripts" / "testexit.py")
    testexit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(testexit)
    testexit.finish(code)



@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    if sys.platform == "win32" and "status" in _EXIT and not os.environ.get("COREFOLD_PYTEST_NORMAL_EXIT"):
        _finish_now(_EXIT["status"])
