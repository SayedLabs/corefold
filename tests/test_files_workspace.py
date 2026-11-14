"""The Files workspace: one list of tools (filetools.py) that the page is made from, files added and taken off
the list (never touched on the PC), the tools that fit what's selected (and why the others don't), a tool picked
before its files, its choices shown in the side panel, the operations from Rakiza Core's own jobs, pins kept,
the library over the page, and what's coming kept apart from what can be used."""

import pytest
from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QPushButton

from corefold import filetools as ft
from corefold import filework as fw
from corefold import settings as settings_mod
from corefold import tools as all_tools


def _pdf(path, pages=1):
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(300, 400)
    with open(path, "wb") as fh:
        w.write(fh)
    return path


@pytest.fixture
def files(tmp_path):
    Image.new("RGB", (64, 48), (200, 40, 40)).save(tmp_path / "photo.jpg")
    Image.new("RGB", (64, 48), (40, 200, 40)).save(tmp_path / "logo.png")
    _pdf(tmp_path / "letter.pdf", 2)
    _pdf(tmp_path / "bill.pdf")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "a.txt").write_text("abc")
    return tmp_path


@pytest.fixture
def window(qtbot):
    from corefold import app as A
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = False
    s.tools_off = []
    s.last_page = "Home"
    s.nav_compact = False
    s.files_pinned = list(ft.DEFAULT_PINS)
    s.files_ops_open = True
    s.save()
    c = A.Controller(A.QApplication.instance(), QIcon())
    c.apply_tools()
    w = c.window
    w.resize(1280, 800)
    w.show()
    qtbot.waitExposed(w)
    w.open_page("Files")
    yield w
    from shiboken6 import delete
    c.tray.hide()
    for timer in c.findChildren(QTimer):
        timer.stop()
    if c._window is not None:
        c._window.close()
        delete(c._window)
    delete(c)


# ---- the list of tools

def test_every_tool_has_a_stable_id_a_name_a_line_and_a_category():
    tools = ft.all_tools_list()
    ids = [t.id for t in tools]
    assert len(ids) == len(set(ids))
    cats = {k for k, _, _ in ft.CATEGORIES}
    for t in tools:
        assert t.name and t.line and t.icon and t.category in cats, t.id
        assert t.owner in all_tools.BY_ID, t.id  # (switched off or resting with its owner)
        assert t.how.startswith(("slots:", "pdf:", "page:", "archive-open")), t.id
    for key in ("convert", "pdf.merge", "pdf.compress", "pdf.split", "archive.extract", "archive.pack",
                "archive.open", "rename", "backup", "check", "unlock"):
        assert key in ids, key
        assert ft.family_of(next(t for t in tools if t.id == key)) is not None, key  # (each has its tile)
    assert set(ft.DEFAULT_PINS) <= {f.id for f in ft.FAMILIES}


def test_clear_names_for_check_and_unlock():
    from corefold import lang_ar
    assert ft.by_id("check").name == "Verify a file"
    assert lang_ar.STRINGS["Verify a file"] == "التحقق من سلامة الملف"
    assert lang_ar.STRINGS["Unlock files"] == "فك قفل الملفات"


def test_a_tool_switched_off_or_resting_isnt_offered(monkeypatch):
    monkeypatch.setattr(all_tools, "PAUSED_TOOLS", frozenset({"organizer"}))
    s = settings_mod.Settings()
    s.tools_off = ["pdf"]
    offered = {t.id for t in ft.offered(s)}
    assert "convert" in offered and not any(i.startswith("pdf.") for i in offered)
    assert "organizer" not in {t.owner for t in ft.offered(settings_mod.Settings())}


def test_what_fits_and_why_not(files):
    kinds = {}
    merge, edit, compare = ft.by_id("pdf.merge"), ft.by_id("pdf.edit"), ft.by_id("pdf.compare")
    letter, bill, photo, notes = files / "letter.pdf", files / "bill.pdf", files / "photo.jpg", files / "notes.txt"
    assert ft.fit(merge, [letter, bill], kinds).ok
    assert ft.fit(merge, [letter, photo], kinds).ok  # (pictures go in a merged PDF too)
    one = ft.fit(merge, [letter], kinds)
    assert not one.ok and "2" in one.why_not
    mixed = ft.fit(ft.by_id("pdf.compress"), [letter, notes], kinds)
    assert mixed.ok and [p for p, _ in mixed.skipped] == [notes] and mixed.usable == [letter]  # (said, not done)
    assert not ft.fit(edit, [letter, bill], kinds).ok  # (one at a time)
    assert not ft.fit(compare, [letter, bill, letter], kinds).ok
    assert not ft.fit(ft.by_id("pdf.compress"), [notes], kinds).ok
    assert not ft.fit(ft.by_id("backup"), [notes], kinds).ok and ft.fit(ft.by_id("backup"), [files / "folder"]).ok
    assert ft.fit(ft.by_id("rename"), [], kinds).ok  # (it can be opened empty: it picks its own files)
    assert not ft.fit(ft.by_id("pdf.compress"), [], kinds).ok


def test_pins_are_kept(window):
    w = window
    ws = w.files_workspace
    assert ft.pinned_families(w.c.settings) == ft.DEFAULT_PINS
    ws.pin("rename", True)
    ws.pin("archive", False)
    again = settings_mod.Settings.load()
    assert "rename" in again.files_pinned and "archive" not in again.files_pinned
    assert ft.pinned_families(again) == ["pdf", "rename"]
    order = [t.id for t in ft.suggested_order(again, ft.offered(again))]
    assert order.index("rename") < order.index("convert")  # (pinned tools come first beside a selection)
    again.files_pinned = ["pdf.split", "convert"]  # (pins kept by an older version: their tiles)
    assert ft.pinned_families(again) == ["pdf", "convert"]


def test_coming_is_kept_apart():
    soon = ft.upcoming()
    ids = {t.id for t in ft.all_tools_list()}
    assert all(u.id not in ids for u in soon)  # (never in the tools that can be run)
    assert all(u.status in ft.STATUS_NAMES for u in soon)
    assert "shredder" in {u.id for u in soon}


# ---- the list of files

def test_added_once_selected_and_taken_off_without_touching_the_pc(files):
    st = fw.WorkState()
    st.add([files / "photo.jpg", files / "letter.pdf", files / "photo.jpg"])
    assert [p.name for p in st.items] == ["photo.jpg", "letter.pdf"]
    assert st.selected == [str(files / "photo.jpg"), str(files / "letter.pdf")]
    st.add([files / "missing.png"])  # (not there: not added)
    assert len(st.items) == 2
    st.remove([str(files / "photo.jpg")])
    assert [p.name for p in st.items] == ["letter.pdf"] and (files / "photo.jpg").exists()
    st.clear()
    assert not st.items and (files / "letter.pdf").exists()
    assert st.chosen() == []


def test_the_operations_come_from_the_jobs(files):
    st = fw.WorkState()
    st.add([files / "photo.jpg"])
    tool = type("T", (), {"description": "Make it a PNG", "run": lambda *a: None})()
    st.job_added(7, tool, [files / "photo.jpg"], {"password": "x", "q": 1}, {}, "photo.jpg")
    op = st.op(7)
    assert op.state == fw.WAITING and "password" not in op.params  # (never kept)
    assert st.status[str(files / "photo.jpg")][0] == fw.WORKING
    st.job_running(7)
    st.job_progress(7, 0.5)
    assert op.state == fw.RUNNING and op.progress == 0.5 and not op.can_retry
    st.job_finished(7, [], ["it broke"], False)
    assert op.state == fw.FAILED and op.detail == "it broke" and op.can_retry
    assert st.status[str(files / "photo.jpg")][0] == fw.PROBLEM
    st.job_added(8, tool, [files / "photo.jpg"], {}, {}, "photo.jpg")
    st.job_finished(8, [str(files / "photo.png")], [], False)
    assert st.op(8).state == fw.FINISHED and st.status[str(files / "photo.jpg")][0] == fw.DONE
    st.job_added(9, tool, [files / "photo.jpg"], {}, {}, "")
    st.job_finished(9, [], [], True)
    assert st.op(9).state == fw.STOPPED
    st.forget_finished()
    assert st.ops == []


# ---- the workspace

def _chips(ws):
    return {b.property("tool") for b in ws.panel.findChildren(QPushButton) if b.objectName() == "toolChip"
            and b.property("tool")}


def test_empty_then_files_with_the_tools_that_fit(window, files):
    w = window
    ws = w.files_workspace
    assert ws.drop.big and not ws.table.isVisible()  # (the big place to drop, with buttons to pick)
    assert ws.library.isVisible() and ws.library.tiles  # (the tool library under it)
    w.c.files_work.add([files / "letter.pdf", files / "bill.pdf"])
    assert not ws.drop.big and ws.table.topLevelItemCount() == 2
    if not ws.panel.isVisible():  # (a narrow window: it's a click away)
        assert ws.tools_btn.isVisible()
        ws.tools_btn.click()
    assert ws.panel.isVisible()
    shown = _chips(ws)
    assert "pdf.merge" in shown and "pdf.edit" not in shown  # (two PDFs: editing is one at a time)
    assert "convert" not in shown  # (PDFs aren't the Converter's here)
    assert ws.panel.head_words.text() == "2 files selected"


def test_the_tick_boxes_select_rows(window, files):
    from corefold.fileworkspace import COL_CHECK
    w = window
    ws = w.files_workspace
    st = w.c.files_work
    st.add([files / "photo.jpg", files / "letter.pdf", files / "bill.pdf"])
    st.select([])
    ws._fill_table()
    assert st.selected == []
    ws._toggle_all()  # (the heading's box: all of them)
    assert len(st.selected) == 3
    from PySide6.QtCore import QItemSelectionModel
    table = ws.table
    idx = table.model().index(0, COL_CHECK)
    table.selectionModel().select(idx, QItemSelectionModel.Toggle | QItemSelectionModel.Rows)
    assert len(st.selected) == 2  # (one box off, the others stay)
    ws._toggle_all()
    assert len(st.selected) == 3
    ws._toggle_all()
    assert st.selected == []


def test_a_tile_shows_its_tools_beside_the_files(window, files):
    w = window
    ws = w.files_workspace
    st = w.c.files_work
    st.add([files / "letter.pdf", files / "bill.pdf"])
    ws.choose_family("pdf")
    assert st.family == "pdf" and st.tool == "pdf.merge"  # (the one that fits them best, picked already)
    assert all(t.startswith("pdf.") for t in _chips(ws))
    read = ft.by_id("pdf.read")
    assert read is not None and not read.settings  # (nothing asked when it's picked)
    ws.choose_tool("pdf.read", keep_family=True)  # (a chip: the tile stays picked)
    assert st.family == "pdf" and st.tool == "pdf.read"
    ws.clear_family()
    assert st.family is None and st.tool == "pdf.read"


def test_merge_right_in_the_side_panel(window, files, qtbot, tmp_path):
    from pypdf import PdfReader
    w = window
    ws = w.files_workspace
    st = w.c.files_work
    st.add([files / "letter.pdf", files / "bill.pdf"])
    ws.choose_family("pdf")
    form = ws.panel.merge
    assert form is not None and [p.name for p in form.order()] == ["letter.pdf", "bill.pdf"]
    assert ws.panel.run_btn.text() == "Merge 2 files"
    item = form.list.takeItem(1)  # (bill first: the order is what's shown)
    form.list.insertItem(0, item)
    form.name.setText("Both.pdf")
    out = tmp_path / "out"
    out.mkdir()
    ws.panel.save_to.insertItem(0, "out", str(out))
    ws.panel.save_to.setCurrentIndex(0)
    ws.panel.run_btn.click()
    qtbot.waitUntil(lambda: bool(st.ops) and st.ops[0].over, timeout=15000)
    assert st.ops[0].state == fw.FINISHED, st.ops[0].detail
    made = out / "Both.pdf"
    assert made.exists() and len(PdfReader(made).pages) == 3
    assert st.save_to is None  # (only for what was started from the panel)
    row = ws.ops.rows[st.ops[0].job_id]
    assert row.folder.isVisibleTo(ws.ops) and not row.bar.isVisibleTo(ws.ops)
    st.forget(st.ops[0].job_id)
    assert not st.ops and not ws.ops.rows


def test_a_tool_first_then_its_files(window, files):
    w = window
    ws = w.files_workspace
    ws.choose_tool("convert")
    assert ws.panel.isVisible() and w.c.files_work.tool == "convert"
    assert ws.panel.run_btn.text() == "Add the files it needs…"
    w.c.files_work.add([files / "photo.jpg", files / "logo.png"])
    assert w.c.files_work.tool == "convert"  # (kept as the files come)
    slots = [b for b in ws.panel.findChildren(QPushButton) if b.objectName() == "slot"]
    assert any(b.text() == "PNG" for b in slots)
    assert not ws.panel.run_btn.isEnabled()  # (until something is picked)


def test_files_of_several_kinds_are_never_converted_together(window, files):
    w = window
    ws = w.files_workspace
    w.c.files_work.add([files / "photo.jpg", files / "notes.txt"])
    ws.choose_tool("convert")
    from PySide6.QtWidgets import QLabel
    notes = [lab.text() for lab in ws.panel.findChildren(QLabel) if lab.objectName() == "fitStop"]
    assert any("one kind" in n for n in notes)
    assert not [b for b in ws.panel.findChildren(QPushButton) if b.objectName() == "slot"]


def test_a_conversion_runs_and_shows_in_the_operations(window, files, qtbot):
    w = window
    ws = w.files_workspace
    st = w.c.files_work
    st.add([files / "photo.jpg"])
    ws.choose_tool("convert")
    png = next(b for b in ws.panel.findChildren(QPushButton) if b.objectName() == "slot" and b.text() == "PNG")
    png.click()
    assert ws.panel.run_btn.isEnabled()
    ws.panel.run_btn.click()
    qtbot.waitUntil(lambda: bool(st.ops) and st.ops[0].over, timeout=15000)
    op = st.ops[0]
    assert op.state == fw.FINISHED and (files / "photo.png").exists()
    assert st.status[str(files / "photo.jpg")][0] == fw.DONE
    row = ws.ops.rows[op.job_id]
    assert row.folder.isVisibleTo(ws.ops) and not row.cancel.isVisibleTo(ws.ops)


def test_a_tools_choices_show_in_the_side_panel(window, files, qtbot):
    w = window
    ws = w.files_workspace
    w.c.files_work.add([files / "letter.pdf"])
    seen = {}

    def look():
        dlg = ws.panel.inline
        seen["dlg"] = type(dlg).__name__ if dlg is not None else None
        seen["inside"] = dlg is not None and ws.panel.isAncestorOf(dlg) and not dlg.isWindow()
        if dlg is not None:
            dlg.reject()
    QTimer.singleShot(400, look)
    ws.choose_tool("pdf.compress")  # (its choices show by themselves, in the panel, until answered)
    qtbot.waitUntil(lambda: "dlg" in seen, timeout=5000)
    assert seen["dlg"] == "CompressDialog" and seen["inside"]
    qtbot.waitUntil(lambda: ws.panel.inline is None, timeout=2000)
    assert not w.c.files_work.ops  # (cancelled: nothing started)


def test_a_full_tool_opens_with_the_files(window, files):
    w = window
    ws = w.files_workspace
    w.c.files_work.add([files / "photo.jpg", files / "logo.png"])
    ws.choose_tool("rename")
    ws.panel.run_btn.click()
    assert w.current_page() == "Rename" and w.one_head.isVisible()
    w.one_head.crumb.click()
    assert w.current_page() == "Files" and len(w.c.files_work.items) == 2  # (the list is still there)


def test_remove_from_list_and_the_library(window, files):
    w = window
    ws = w.files_workspace
    st = w.c.files_work
    st.add([files / "photo.jpg", files / "letter.pdf"])
    st.select([str(files / "photo.jpg")])
    ws._fill_table()
    ws.remove_selected()
    assert [p.name for p in st.items] == ["letter.pdf"] and (files / "photo.jpg").exists()
    ws.open_library("now")
    assert ws.library.isVisible()
    ws.library.search.setText("merge")
    from corefold.fileworkspace import ComingTile, LibTile
    tiles = [t.tool.id for t in ws.library.tiles]
    assert tiles == ["pdf.merge"]
    ws.library.search.clear()
    assert {t.family.id for t in ws.library.tiles} >= {"convert", "pdf", "archive"}  # (a tile for each)
    ws.library.show_tab("soon")
    QApplication.processEvents()
    cards = [c for c in ws.library.findChildren(ComingTile) if c.isVisibleTo(ws.library)]
    shown = [t for t in ws.library.findChildren(LibTile) if t.isVisibleTo(ws.library)]
    assert cards and not shown  # (what's coming can't be picked)
    assert all(not isinstance(c, QPushButton) for c in cards)
    ws.close_library()
    assert ws.library.tab == "now" and len(st.items) == 1  # (the files stay)


def test_files_dropped_on_the_window_go_to_the_workspace(window, files):
    w = window
    w.open_page("Home")
    w.show_files([files / "photo.jpg"])
    assert w.current_page() == "Files" and [p.name for p in w.c.files_work.items] == ["photo.jpg"]


def test_narrow_window_folds_the_side_panel(window, files, qtbot):
    w = window
    ws = w.files_workspace
    w.c.files_work.add([files / "photo.jpg"])
    w.resize(880, 640)
    qtbot.waitUntil(lambda: ws.width() < 900, timeout=2000)
    QApplication.processEvents()
    if ws.width() < 900:
        assert not ws.panel.isVisible() and ws.tools_btn.isVisible()
        ws.tools_btn.click()
        assert ws.panel.isVisible()



# ---- "Save to" belongs to what's started from the side panel, and only to that

def _locked_pdf(path, password="pw"):
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter(clone_from=PdfReader(str(_pdf(path.with_name("open-" + path.name)))))
    w.encrypt(password, password, algorithm="AES-256")
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def test_save_to_lets_go_when_a_tool_gives_up_and_the_page_is_left(window, files, qtbot):
    w = window
    ws = w.files_workspace
    st = w.c.files_work
    st.add([_locked_pdf(files / "locked.pdf")])
    ws.choose_tool("pdf.lock")  # ("This PDF already has a password": nothing is asked, nothing starts)
    qtbot.wait(300)
    assert not st.ops
    w.open_page("Home")
    QApplication.processEvents()
    assert st.save_to is None and not ws.panel.armed  # (a conversion from another page saves where it always does)


def test_save_to_is_only_for_files_on_the_list(window, files, tmp_path):
    from corefold import tools as T  # noqa: F401
    from corefold.core import Result, Tool
    st = window.c.files_work
    st.add([files / "photo.jpg"])
    picked = tmp_path / "picked"
    picked.mkdir()
    seen = {}

    def note(fs, ctx):
        seen[fs[0].name] = ctx.options.output_folder
        return Result([])
    tool = Tool("test:note", "Note", "Note", note, batch=True)
    st.save_to = str(picked)
    try:
        window.c.start_job(tool, [files / "photo.jpg"])
        window.c.start_job(tool, [files / "logo.png"])  # (not on the list: Explorer's menu, say)
    finally:
        st.save_to = None
    from PySide6.QtTest import QTest
    for _ in range(100):
        if len(seen) == 2:
            break
        QTest.qWait(50)
    assert seen["photo.jpg"] == picked and seen["logo.png"] != picked


def test_save_to_stays_when_a_password_is_asked_for(window, files, tmp_path, qtbot, monkeypatch):
    from corefold import pdftools
    c = window.c
    st = c.files_work
    locked = _locked_pdf(files / "locked.pdf")
    st.add([locked])
    picked = tmp_path / "picked"
    picked.mkdir()
    monkeypatch.setattr(c, "ask_password", lambda *a, **k: "pw")
    st.save_to = str(picked)
    try:
        c.start_job(pdftools.REPAIR, [locked], {})
    finally:
        st.save_to = None  # (the side panel lets go as soon as the job has started)
    qtbot.waitUntil(lambda: bool(st.ops) and st.ops[0].over, timeout=15000)
    assert st.ops[0].state == fw.FINISHED, st.ops[0].detail
    assert [p.parent for p in map(__import__("pathlib").Path, st.ops[0].outputs)] == [picked]


def test_every_pdf_tool_that_asks_shows_its_choices_in_the_panel(window, files, qtbot):
    # (Page numbers' dialog had a "style" box of its own in place of Qt's style(): in the panel it never showed)
    w = window
    ws = w.files_workspace
    w.c.files_work.add([files / "letter.pdf"])
    for tool_id, want in (("pdf.numbers", "PageNumbersDialog"), ("pdf.watermark", "WatermarkDialog"),
                          ("pdf.headerfooter", "HeaderFooterDialog")):
        seen = {}

        def look(seen=seen):
            dlg = ws.panel.inline
            if dlg is None:
                QTimer.singleShot(100, lambda: look(seen))
                return
            seen["dlg"] = type(dlg).__name__
            dlg.reject()
        QTimer.singleShot(200, look)
        ws.choose_tool(tool_id)
        qtbot.waitUntil(lambda seen=seen: "dlg" in seen, timeout=5000)
        assert seen["dlg"] == want
        qtbot.waitUntil(lambda: ws.panel.inline is None, timeout=2000)
