"""Start with Windows works on an account that has no Run key yet (a new Windows account): the key is made
when it's switched on, and switching it off then is simply nothing to do (no error in the log)."""
import sys
import types

from corefold import settings


class FakeKey:
    def __init__(self, values):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_winreg(store: dict):
    w = types.ModuleType("winreg")
    w.HKEY_CURRENT_USER, w.KEY_SET_VALUE, w.REG_SZ = object(), 2, 1

    def open_key(root, path, reserved=0, access=0):
        if path not in store:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return FakeKey(store[path])

    def create_key(root, path, reserved=0, access=0):
        return FakeKey(store.setdefault(path, {}))

    def set_value(k, name, reserved, kind, value):
        k.values[name] = value

    def delete_value(k, name):
        if name not in k.values:
            raise FileNotFoundError(2, "gone")
        del k.values[name]
    w.OpenKey, w.CreateKeyEx, w.SetValueEx, w.DeleteValue = open_key, create_key, set_value, delete_value
    return w


def test_startup_without_a_run_key(monkeypatch, caplog):
    store: dict = {}
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(store))
    monkeypatch.setattr(settings, "launch_command", lambda: '"Corefold.exe" --tray')
    monkeypatch.setattr(settings, "_clear_off_note", lambda: None)
    settings.set_startup(False)  # (no key at all: nothing to take off)
    assert store == {}
    settings.set_startup(True)
    assert store[settings.RUN_KEY] == {settings.APP_NAME: '"Corefold.exe" --tray'}
    settings.set_startup(False)
    assert store[settings.RUN_KEY] == {}
    assert "could not change startup setting" not in caplog.text
