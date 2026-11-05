"""The Apps list finds each app's icon file on the worker thread: filling the list doesn't touch the disk."""
import os

from corefold import appspage
from corefold import uninstall as un


def test_icon_files_are_found_ahead(tmp_path, monkeypatch):
    exe = tmp_path / "app.exe"
    exe.write_bytes(b"MZ")
    with_icon = un.App(id="a", name="A", icon=str(exe))
    without = un.App(id="b", name="B", icon=str(tmp_path / "gone.exe"))
    appspage._find_icons([with_icon, without])

    def no_disk(_path):
        raise AssertionError("the disk was asked again")
    monkeypatch.setattr(os.path, "exists", no_disk)
    assert appspage._icon_path(with_icon) == str(exe)
    assert appspage._icon_path(without) == ""
