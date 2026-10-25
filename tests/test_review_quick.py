"""Regression checks for the quick menu's calculator, the clipboard history and Awake's shutdown timer."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QMimeData
from PySide6.QtGui import QColor, QImage

from corefold import awake, quickfind
from corefold import clipboard as cb


@pytest.mark.parametrize("typed, shown", [
    ("round(2.567,1)", "2.6"),       # (was 2567: the comma was taken as a decimal comma)
    ("round(3.14159, 2)", "3.14"),
    ("max(1,2)", "2"),
    ("max(2.5,10)", "10"),
    ("min(1,5; 2)", "1.5"),          # (with ";", commas are still decimal commas)
    ("max(1,000, 5)", "1000"),
    ("100 + 10% + 10%", "121"),      # (was 110.11)
    ("100 - 20% - 10%", "72"),
    ("80 + 15%", "92"),
    ("cos(90)", "0"),                # (was 6.12323399574e-17)
    ("sin(30)", "0.5"),
    ("3,5+1", "4.5"),
    ("1,000+1", "1001"),
    ("2+3*4", "14"),
    ("50% of 200", "100"),
])
def test_calculator(typed, shown):
    ans = quickfind.answer(typed)
    assert ans is not None and ans.shown == shown


@pytest.mark.parametrize("typed", ["tan(90)", "10/0", "9^999", "7"])
def test_calculator_has_no_answer(typed):
    assert quickfind.answer(typed) is None


def test_units_still_convert():
    assert quickfind.answer("10 km to mi").shown == "6.21371 mi"
    assert quickfind.answer("100 f to c").shown == "37.7778 °C"


def _add(h, **kw):
    data = kw["text"] if kw.get("kind") != "image" else kw["image"]
    return h.add(cb.Item(digest=cb._digest(kw.get("kind", "text"), data), **kw))


def test_long_file_list_is_copied_whole(tmp_path):
    h = cb.History(tmp_path)
    paths = "\n".join(rf"C:\Users\someone\Documents\Projects\Report folder\file {i:03}.docx" for i in range(40))
    _add(h, kind="files", text=paths, size=40)
    listed = h.items()[0]
    assert listed.partial and len(listed.files) < 40  # (a list holds only the start)
    used = []
    fake = SimpleNamespace(history=h, cb=SimpleNamespace(setMimeData=used.append), _own_digest="", _own=None,
                           changed=SimpleNamespace(emit=lambda: None))
    fake.mime_for = lambda item, plain=False: cb.Clipboard.mime_for(fake, item, plain)
    assert cb.Clipboard.copy(fake, listed)
    assert len(used[0].urls()) == 40
    h.close()


def test_list_says_which_texts_have_formatting(tmp_path):
    h = cb.History(tmp_path)
    _add(h, kind="text", text="plain", size=5)
    rich = _add(h, kind="text", text="rich", html="<b>rich</b>", size=4)
    by_text = {i.text: i for i in h.items()}
    assert by_text["rich"].html and not by_text["plain"].html  # ("Paste as plain text" is offered)
    assert h.get(rich.id).html == "<b>rich</b>"
    h.close()


class _Board:
    def __init__(self, md):
        self.md, self.set = md, []

    def mimeData(self):
        return self.md

    def setText(self, text):
        self.set.append(text)


def _plain_paste(md):
    board = _Board(md)
    fake = SimpleNamespace(cb=board, _own=None, _press_paste_soon=lambda ms: None)
    cb.Clipboard.paste_plain(fake)
    return board.set


def test_paste_plain_keeps_a_password_managers_marks():
    md = QMimeData()
    md.setText("hunter2")
    md.setData('application/x-qt-windows-mime;value="ExcludeClipboardContentFromMonitorProcessing"', b"\0")
    assert _plain_paste(md) == []  # (not copied again without the marks)
    rich = QMimeData()
    rich.setText("hello")
    rich.setHtml("<b>hello</b>")
    assert _plain_paste(rich) == ["hello"]


def test_thumbnails_follow_the_picture_not_the_id(qtbot, tmp_path):
    from corefold.clipview import Rows
    for name, w in (("a.png", 10), ("b.png", 20)):
        img = QImage(w, w, QImage.Format.Format_ARGB32)
        img.fill(QColor("red"))
        img.save(str(tmp_path / name))
    history = SimpleNamespace(thumb_path=lambda item: tmp_path / "none.png",
                              image_path=lambda item: tmp_path / item.image)
    rows = Rows(SimpleNamespace(history=history), None)  # (the colours aren't needed for a thumbnail)
    first = rows._thumb(cb.Item(kind="image", image="a.png", id=7))
    again = rows._thumb(cb.Item(kind="image", image="b.png", id=7))  # (the same id, given again)
    assert first.width() == 10 and again.width() == 20


def test_a_shutdown_time_gone_by_is_never_straight_away(monkeypatch):
    ran = []
    monkeypatch.setattr(awake, "run_shutdown", lambda *args: (ran.append(args), (0, ""))[1])
    settings = SimpleNamespace(shutdown_at="", shutdown_planned="", save=lambda: None)
    fake = SimpleNamespace(c=SimpleNamespace(settings=settings), planned_shutdown=lambda: None,
                           _forget_shutdown=lambda: None, changed=SimpleNamespace(emit=lambda: None))
    assert awake.AwakeTool.plan_shutdown(fake, datetime.now() - timedelta(minutes=5)) == 0
    assert ran[-1][:2] == ("/s", "/t") and int(ran[-1][2]) >= 60
