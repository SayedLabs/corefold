"""Checks for the problems left after the 1.10 review: Office cancel, Startup leftovers in everyone's folder,
the administrator helper, the Fast Startup shutdown note and the clipboard pictures' total size."""
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from corefold import awake, converters, startup
from corefold import clipboard as cb
from corefold import uninstall as un
from corefold.core import Cancelled


def test_cancelled_office_job_stops_waiting_and_tidies_up_after(tmp_path):
    folder = tmp_path / "office"
    folder.mkdir()
    made = folder / "document.pdf"
    slow = "import os, pathlib, time; time.sleep(1.5); pathlib.Path(os.environ['CF_OUT']).write_text('pdf')"
    ctx = SimpleNamespace(cancelled=True)
    start = time.monotonic()
    with pytest.raises(Cancelled):
        converters._run_office([sys.executable, "-c", slow], {**os.environ, "CF_OUT": str(made)},
                               folder, ctx)
    assert time.monotonic() - start < 1  # (the person isn't kept waiting for Office)
    for _ in range(100):
        if not folder.exists():
            break
        time.sleep(0.1)
    assert not folder.exists()  # (what Office made after the Cancel went with its folder)


def test_finished_office_job_hands_back_what_it_said(tmp_path):
    ctx = SimpleNamespace(cancelled=False)
    code, err = converters._run_office([sys.executable, "-c", "import sys; sys.stderr.write('oops'); sys.exit(3)"],
                                       dict(os.environ), tmp_path, ctx)
    assert code == 3 and "oops" in err


def test_helper_takes_only_files_right_in_everyones_startup_folder(tmp_path, monkeypatch):
    common = tmp_path / "StartUp"
    (common / "sub").mkdir(parents=True)
    inside, deeper, elsewhere = common / "old.lnk", common / "sub" / "x.lnk", tmp_path / "other.lnk"
    for f in (inside, deeper, elsewhere):
        f.write_text("x")
    monkeypatch.setattr(startup, "folders", lambda: {"User-Folder": tmp_path / "u", "Common-Folder": common})
    gone = []
    monkeypatch.setattr(un, "recycle", lambda p: (gone.append(p), p.unlink(), True)[2])
    monkeypatch.setattr(startup, "_delete_value", lambda *a: None)
    out = startup.clean_common_as_admin({"items": [["old.lnk", str(inside)], ["x.lnk", str(deeper)],
                                                   ["o.lnk", str(elsewhere)], ["c", str(common)]]})
    assert out == {"removed": 1} and gone == [inside] and deeper.exists() and elsewhere.exists()


def test_main_starts_the_helper_for_every_kind_it_knows():
    text = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    assert all(f'"{k}"' in text for k in un.HELPER_KINDS) and "--startup-clean" in un.HELPER_KINDS


def test_a_shutdown_since_planning_forgets_the_plan(monkeypatch):
    planned = datetime.now() - timedelta(hours=2)
    s = SimpleNamespace(shutdown_at=(datetime.now() + timedelta(hours=1)).isoformat(),
                        shutdown_planned=planned.isoformat(), save=lambda: None)
    forgot = []
    fake = SimpleNamespace(c=SimpleNamespace(settings=s), _forget_shutdown=lambda: forgot.append(1))
    monkeypatch.setattr(awake, "booted_at", lambda: planned - timedelta(days=3))  # (Fast Startup: "on" for days)
    monkeypatch.setattr(awake, "shut_down_at", lambda: None)
    assert awake.AwakeTool.planned_shutdown(fake) is not None and not forgot
    monkeypatch.setattr(awake, "shut_down_at", lambda: planned + timedelta(minutes=30))
    assert awake.AwakeTool.planned_shutdown(fake) is None and forgot


def test_pictures_kept_stay_under_their_total(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "MAX_PICTURES", 1000)
    h = cb.History(tmp_path)
    for n in range(3):
        h.add(cb.Item(kind="image", image=f"{n}.png", digest=f"d{n}", size=400))
        h.db.execute("UPDATE items SET used = ? WHERE digest = ?", (1000 + n, f"d{n}"))
    pinned = h.add(cb.Item(kind="image", image="p.png", digest="p", size=5000, pinned=True))
    h.db.execute("UPDATE items SET pinned = 1, used = 1 WHERE id = ?", (pinned.id,))
    h.db.commit()
    assert h.trim(0, 0) == 1
    left = {i.image for i in h.items()}
    assert left == {"1.png", "2.png", "p.png"}  # (the oldest went; a pinned one never does)
    h.close()
