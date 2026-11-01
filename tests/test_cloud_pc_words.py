"""Saving a cloud folder into a place on this PC that Windows won't let Corefold write to (or a file another
program holds) is said in Corefold's own words, never Windows' "[WinError 5] Access is denied: 'C:\\…'"
(seen by writefailtest on the runner)."""
from pathlib import Path
from unittest import mock

import pytest

from corefold import cloud
from corefold.cloudlocal import Local


def _local(root: Path) -> Local:
    return Local(cloud.Account("onedrive", "", extra={"local": str(root)}))


def test_saving_a_folder_where_windows_says_no_is_said_plainly(tmp_path):
    root = tmp_path / "OneDrive"
    (root / "trip").mkdir(parents=True)
    (root / "trip" / "a.txt").write_text("hello", "utf-8")
    c = _local(root)
    denied = PermissionError(13, "Access is denied")
    with mock.patch.object(Path, "mkdir", side_effect=denied), pytest.raises(cloud.CloudError) as e:
        cloud.download_tree(c, c._entry(root / "trip"), tmp_path / "here")
    assert str(e.value) == "it's open in another app, or Windows didn't allow it"


def test_a_held_file_sent_up_is_said_plainly(tmp_path):
    root = tmp_path / "OneDrive"
    root.mkdir()
    src = tmp_path / "held.txt"
    src.write_text("x", "utf-8")
    c = _local(root)
    real_open = open

    def held(path, *a, **kw):
        if Path(path) == src:
            raise PermissionError(13, "The process cannot access the file")
        return real_open(path, *a, **kw)
    with mock.patch("builtins.open", side_effect=held), pytest.raises(cloud.CloudError):
        cloud.upload_tree(c, src, None)
    assert not any(p.name.endswith(".corefold-part") for p in root.iterdir())
