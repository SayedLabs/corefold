"""Shared set-up for the pytest tests (pytest-qt gives each test a `qtbot` and a running QApplication).

Corefold's settings and logs go to a throwaway folder, never to the real %APPDATA%. The tests are meant
for Windows; on Linux or macOS the few Windows-only modules are replaced with stand-ins so the widgets
can still be checked there (with QT_QPA_PLATFORM=offscreen when there's no screen)."""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="corefold-pytest-"))
os.environ["APPDATA"] = str(_TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(_TMP / "local")

if sys.platform != "win32":
    import ctypes
    ctypes.windll = mock.MagicMock()
    _winreg = mock.MagicMock()
    _winreg.EnumKey.side_effect = OSError  # (no keys under any key: a MagicMock "has" keys for ever, and
    _winreg.EnumValue.side_effect = OSError  # the shell menu's clean-up recursed through them without end)
    sys.modules.setdefault("winreg", _winreg)
    sys.modules.setdefault("winsound", mock.MagicMock())
