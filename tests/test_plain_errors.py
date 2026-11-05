"""Windows' own error text ("[WinError 5] Access is denied: 'C:\\…'") never reaches the person: a job that
crashes, and the lists saved from Space and the Uninstaller, say what went wrong in Rakiza Core's words."""
import threading
from unittest import mock

from corefold.converters import Options, Tool

DENIED = PermissionError(13, "Access is denied", "C:\\Users\\me\\Desktop\\list.csv")
DENIED.winerror = 5


def test_a_job_that_crashes_on_the_disk_says_it_plainly(qapp, tmp_path):
    from corefold import app

    got = {}
    signals = app.JobSignals()
    signals.finished.connect(lambda _id, result, errors, cancelled: got.update(errors=errors))
    tool = Tool("test:crash", "Crash", "", lambda f, ctx: None)
    with mock.patch.object(app, "run_job", side_effect=DENIED):
        app.Job(1, tool, [tmp_path / "a.txt"], Options(output_folder=tmp_path), signals, threading.Event(), {}).run()
    assert got["errors"] == ["permission denied (is the file open in another app?)"]


def test_the_uninstallers_list_saved_where_windows_says_no(qapp, tmp_path):
    from corefold import appspage

    said = []
    page = mock.MagicMock()
    page.tree.topLevelItemCount.return_value = 0
    from PySide6.QtWidgets import QFileDialog
    with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(str(tmp_path / "list.csv"), "")), \
            mock.patch("builtins.open", side_effect=DENIED), \
            mock.patch.object(appspage.sheet, "warning", side_effect=lambda *a: said.append(a[2])):
        appspage.AppsPage.save_list(page)
    assert said and "WinError" not in said[0] and "Access is denied" not in said[0]
