"""Checks for fixes from the review of the window, its Settings pages and the small dialogs (they run
offscreen: no Windows needed)."""
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QIcon, QKeyEvent, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from corefold import settings as settings_mod
from corefold import theme


def test_quick_list_keeps_actions_of_a_tool_switched_off(qtbot):
    from corefold.quickpage import ItemList
    s = settings_mod.Settings()
    s.tools_off = ["capture"]
    ids = ["explorer", "screenshot", "taskmgr", "lock"]
    items = ItemList(s, ids, 40, theme.build_theme("light", "core"))
    qtbot.addWidget(items)
    assert items.ids() == ["explorer", "taskmgr", "lock"]  # (Capture is off: its action isn't shown)
    got = []
    items.changed.connect(got.append)
    items.list.setCurrentRow(2)
    items._move(-1)  # lock moves up one place
    assert got[-1] == ["explorer", "screenshot", "lock", "taskmgr"]  # (the screenshot is still there)


def test_recording_a_shortcut_keeps_the_window_shortcuts_away(qtbot):
    from corefold.quickpage import KeyRecorder
    rec = KeyRecorder("ctrl+alt+space")
    qtbot.addWidget(rec)
    rec.recording = True
    e = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key_K, Qt.ControlModifier)
    e.ignore()
    QApplication.sendEvent(rec, e)
    assert e.isAccepted()
    rec.recording = False


def _empty_dialog(qtbot):
    from corefold import emptyfolders
    from corefold.emptyview import EmptyFoldersDialog
    page = QWidget()
    qtbot.addWidget(page)
    page.c = SimpleNamespace(theme=theme.build_theme("light", "core"), say=lambda *a, **k: None)
    dlg = EmptyFoldersDialog(page)
    folders = [emptyfolders.EmptyFolder(path=f"C:/x/{n}", root="C:/x", created=0.0) for n in "abc"]
    dlg.found = emptyfolders.Found(items=list(folders))
    dlg._fill()
    return dlg, folders


def test_empty_folders_left_unticked_stay_unticked(qtbot):
    dlg, (a, b, c) = _empty_dialog(qtbot)
    dlg.tree.topLevelItem(1).setCheckState(0, Qt.Unchecked)  # b is left out on purpose
    chosen = dlg.ticked()
    assert chosen == [a, c]
    dlg._removed(chosen, [a])  # a went; c couldn't be removed
    assert dlg.ticked() == [c]  # (b isn't ticked again behind the person's back)


def test_crop_shape_picked_early_fits_the_picture(qtbot):
    from corefold.dialogs2 import CropArea
    area = CropArea()
    qtbot.addWidget(area)
    area.set_ratio(1.0)  # "Square", before the picture is read
    area.set_image(QPixmap(40, 30), (4000, 3000))
    x0, y0, x1, y1 = area.box
    assert abs((x1 - x0) * 4000 - (y1 - y0) * 3000) < 1  # square in pixels, not in the 0-1 box


def test_several_videos_each_get_their_own_subtitles(qtbot, tmp_path: Path):
    from corefold.dialogs2 import SubtitlesDialog
    videos = [tmp_path / "one.mp4", tmp_path / "two.mp4"]
    for v in videos:
        v.write_bytes(b"")
        v.with_suffix(".srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", "utf-8")
    dlg = SubtitlesDialog(videos, QIcon(), False)
    qtbot.addWidget(dlg)
    assert dlg.path.text() == "" and dlg.ok.isEnabled()  # (empty: each video's own file)
    assert dlg.params()["subtitles"] == ""
    single = SubtitlesDialog(videos[:1], QIcon(), False)
    qtbot.addWidget(single)
    assert single.path.text() == str(tmp_path / "one.srt")


def test_trim_never_makes_a_tiny_file_when_the_length_is_unknown(qtbot, monkeypatch, tmp_path: Path):
    from corefold import dialogs
    monkeypatch.setattr(dialogs, "media_duration", lambda src: 0.0)
    monkeypatch.setattr(dialogs, "video_frame", lambda *a, **k: None)
    src = tmp_path / "clip.mp3"
    src.write_bytes(b"")
    dlg = dialogs.TrimDialog([src], QIcon(), False)
    qtbot.addWidget(dlg)
    assert dlg.params() == {"start": 0.0, "end": 0.0}  # (not 0 to 0.1 seconds)


def test_archive_password_typed_only_in_the_second_box(qtbot, monkeypatch, tmp_path: Path):
    from corefold import archives, dialogs
    monkeypatch.setattr(archives, "find_7z", lambda: "7z.exe")
    f = tmp_path / "a.txt"
    f.write_text("x", "utf-8")
    dlg = dialogs.PackDialog([f], QIcon(), False)
    qtbot.addWidget(dlg)
    dlg.password2.setText("secret")
    assert not dlg.ok.isEnabled()


def test_keep_now_isnt_held_back_with_unsaved_changes(monkeypatch):
    from corefold.app import Controller
    s = settings_mod.Settings()
    s.viewer_sort = "name"
    window = SimpleNamespace(committed={"viewer_sort": "name"})
    fake = SimpleNamespace(settings=s, _window=window, settings_changed=lambda: None)
    monkeypatch.setattr(settings_mod, "HELD", {"viewer_sort": "name", "volume": 10})
    Controller.keep_now(fake, "viewer_sort", "newest")
    assert s.viewer_sort == "newest"
    assert settings_mod.HELD["viewer_sort"] == "newest" and window.committed["viewer_sort"] == "newest"
