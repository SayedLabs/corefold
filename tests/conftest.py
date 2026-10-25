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
    winreg = mock.MagicMock()
    # (a registry key with no keys or values under it: a stand-in that "has" endless keys sent
    # shellmenu._delete_tree down for ever, filling the memory)
    winreg.EnumKey.side_effect = OSError
    winreg.EnumValue.side_effect = OSError
    sys.modules.setdefault("winreg", winreg)
    sys.modules.setdefault("winsound", mock.MagicMock())
