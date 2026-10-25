"""Unlock in one click: "Close and delete / move / rename" closes the programs that have the file open,
then does it; if a program won't let go, nothing is done and the page says so. "Copy to…" works without
closing anything. Moving at restart stays on the same drive and away from Windows' own folders."""
import types

from PySide6.QtWidgets import QHBoxLayout

from corefold import theme, unlock


def _page(monkeypatch, programs_after):
    from corefold import unlockpage
    t = theme.build_theme("light", "core")
    page = unlockpage.UnlockPage(types.SimpleNamespace(theme=t, settings=types.SimpleNamespace()), QHBoxLayout())
    note = unlock.Program(pid=4321, name="Notepad", exe="")
    calls = {"closed": [], "found": 0}

    def find(paths):
        calls["found"] += 1
        return unlock.Found(list(paths), [note] if calls["found"] == 1 or programs_after else [])

    def close_politely(programs, wait_s=0):
        calls["closed"].append([p.pid for p in programs])
        return {p.pid: "closed" if not programs_after else "running" for p in programs}
    monkeypatch.setattr(unlock, "find", find)
    monkeypatch.setattr(unlock, "close_politely", close_politely)
    return page, calls


def test_close_and_rename_does_it_once_the_program_let_go(qtbot, tmp_path, monkeypatch):
    f = tmp_path / "report.docx"
    f.write_text("x")
    page, calls = _page(monkeypatch, programs_after=False)
    page.check([f])
    qtbot.waitUntil(lambda: page.found is not None and not page.busy)
    assert page.then_btn.isVisibleTo(page.result) and not page.delete_btn.isVisibleTo(page.result)
    monkeypatch.setattr(page, "_ask_name", lambda: "final.docx")
    page.close_then("rename")
    qtbot.waitUntil(lambda: (tmp_path / "final.docx").exists(), timeout=5000)
    assert calls["closed"] == [[4321]] and not f.exists() and page.pending is None
    page.deleteLater()


def test_nothing_is_done_while_a_program_holds_on(qtbot, tmp_path, monkeypatch):
    f = tmp_path / "report.docx"
    f.write_text("x")
    page, calls = _page(monkeypatch, programs_after=True)
    page.check([f])
    qtbot.waitUntil(lambda: page.found is not None and not page.busy)
    gone = []
    monkeypatch.setattr(page, "delete", lambda: gone.append(True))
    page.close_then("delete")
    qtbot.waitUntil(lambda: not page.busy and calls["found"] >= 2, timeout=5000)
    assert not gone and f.exists() and page.pending == ("delete", None)
    assert "Notepad" in page.problem.text()
    page.deleteLater()


def test_copy_to_works_while_it_is_open(qtbot, tmp_path, monkeypatch):
    f = tmp_path / "data.csv"
    f.write_text("1,2")
    (tmp_path / "out").mkdir()
    page, _ = _page(monkeypatch, programs_after=True)
    page.check([f])
    qtbot.waitUntil(lambda: page.found is not None and not page.busy)
    page.copy_to(str(tmp_path / "out"))
    qtbot.waitUntil(lambda: (tmp_path / "out" / "data.csv").exists() and not page.busy, timeout=5000)
    assert f.exists() and (tmp_path / "out" / "data.csv").read_text() == "1,2"
    page.deleteLater()


def test_moving_at_restart_is_checked_first(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("x")
    assert unlock.move_at_restart_problem(str(src), str(tmp_path / "b.txt")) == ""
    (tmp_path / "b.txt").write_text("y")
    assert unlock.move_at_restart_problem(str(src), str(tmp_path / "b.txt")) == "taken"
    assert unlock.move_at_restart_problem(str(tmp_path / "nope"), str(tmp_path / "c.txt")) == "missing"
