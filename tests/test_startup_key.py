"""Start with Windows on an account that has no Run key yet: switching it on makes the key, switching it off
is quiet (there's nothing to remove)."""
import sys
import types

from corefold import settings


class _Key:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_winreg(keys: dict):
    w = types.SimpleNamespace(HKEY_CURRENT_USER="HKCU", KEY_SET_VALUE=2, REG_SZ=1)

    def open_key(root, path, reserved=0, access=0):
        if path not in keys:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return _Key(keys[path])

    def create_key(root, path, reserved=0, access=0):
        return _Key(keys.setdefault(path, {}))

    def set_value(k, name, reserved, kind, value):
        k.store[name] = value

    def delete_value(k, name):
        if name not in k.store:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        del k.store[name]

    w.OpenKey, w.CreateKeyEx, w.SetValueEx, w.DeleteValue = open_key, create_key, set_value, delete_value
    return w


def test_on_makes_the_run_key(monkeypatch, caplog):
    keys = {}
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(keys))
    monkeypatch.setattr(settings, "launch_command", lambda: '"C:\\Rakiza Core\\RakizaCore.exe" --tray')
    monkeypatch.setattr(settings, "_clear_off_note", lambda: None)
    settings.set_startup(True)
    assert keys[settings.RUN_KEY][settings.KEEP_ID].endswith("--tray")
    settings.set_startup(False)
    assert settings.KEEP_ID not in keys[settings.RUN_KEY]
    assert "could not change startup setting" not in caplog.text


def test_off_without_a_run_key_is_quiet(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg({}))
    settings.set_startup(False)
    assert "could not change startup setting" not in caplog.text
