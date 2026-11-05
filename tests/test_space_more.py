"""Space: the clean-up by itself (only temporary files, crash reports and closed apps' caches), and Windows settings
that free a lot of room (hibernation file, older restore points, Compact OS), each unticked and done by the
administrator copy."""
from pathlib import Path

from corefold import space
from corefold.settings import Settings
from corefold.space import Junk


def test_auto_clean_only_takes_safe_things():
    ok = [Junk("temp", "", "", size=5), Junk("dumps", "", "", size=5), Junk("browser:chrome.exe", "", "", size=5),
          Junk("app:discord.exe", "", "", size=5)]
    never = [Junk("bin", "", "", size=5), Junk("installers", "", "", size=5, to_bin=True),
             Junk("thumbs", "", "", size=5), Junk("gpu", "", "", size=5), Junk("winold", "", "", size=5, admin=True),
             Junk("browser:chrome.exe", "", "", size=5, busy="Google Chrome"), Junk("temp", "", "", size=0),
             Junk("hibernate", "", "", size=5, admin=True)]
    assert all(space.auto_cleanable(j) for j in ok)
    assert not any(space.auto_cleanable(j) for j in never)


def test_auto_clean_never_looks_for_windows_files(monkeypatch):
    seen = {}

    def fake(downloads=None, cancel=None, admin=True):
        seen["admin"] = admin
        return [Junk("temp", "", "", size=10), Junk("bin", "", "", size=99)]
    cleaned = []
    monkeypatch.setattr(space, "find_junk", fake)
    monkeypatch.setattr(space, "clean", lambda j, cancel=None: cleaned.append(j.key) or j.size)
    assert space.auto_clean() == 10 and cleaned == ["temp"] and seen["admin"] is False


def test_settings_keep_the_schedule():
    s = Settings()
    assert s.space_auto_clean == "off"
    assert set(space.AUTO_EVERY) == {"off", "week", "month"}


def test_setting_rows_are_unticked_and_explained(monkeypatch):
    monkeypatch.setattr(space, "hibernation_file", lambda: 8 << 30)
    monkeypatch.setattr(space, "restore_point_count", lambda: 3)
    monkeypatch.setattr(space, "restore_points_size", lambda: 0)
    monkeypatch.setattr(space, "windows_is_compact", lambda: False)
    rows = {j.key: j for j in space._setting_junk()}
    assert set(rows) == {"hibernate", "restorepoints", "compactos"}
    assert all(j.admin and not j.ticked for j in rows.values())
    assert rows["hibernate"].size == 8 << 30
    assert rows["restorepoints"].count == 2 and rows["restorepoints"].about  # (the newest one stays)
    assert rows["compactos"].about and not rows["compactos"].size
    assert set(space.SETTING_KEYS) <= set(space.ADMIN_KEYS)


def test_no_rows_when_there_is_nothing_to_gain(monkeypatch):
    monkeypatch.setattr(space, "hibernation_file", lambda: 0)
    monkeypatch.setattr(space, "restore_point_count", lambda: 1)
    monkeypatch.setattr(space, "windows_is_compact", lambda: True)
    assert space._setting_junk() == []


def test_admin_copy_runs_windows_own_tools_for_settings(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(space, "_free_on_windows_drive", lambda: 0)
    space._clean_setting("hibernate", tmp_path, lambda *a, **k: ran.append(a))
    space._clean_setting("compactos", tmp_path, lambda *a, **k: ran.append(a))
    assert ran == [("powercfg", "/hibernate", "off"), ("compact", "/CompactOS:always")]
    called = []
    import corefold.uninstall as un
    monkeypatch.setattr(un, "powershell", lambda script, timeout=0: called.append(script) or "")
    space._clean_setting("restorepoints", tmp_path, lambda *a, **k: None)
    assert "ClientAccessible" in called[0] and "Count - 2" in called[0]  # (only Windows' own, never the newest)


def test_clean_as_admin_sends_setting_keys_to_their_own_work(monkeypatch):
    got = []
    monkeypatch.setattr(space, "_clean_setting", lambda key, system32, run: got.append(key) or 7)
    monkeypatch.setattr(space, "_windows_dir", lambda: Path("C:/Windows"))
    assert space.clean_as_admin(["hibernate", "compactos"]) == 14 and got == ["hibernate", "compactos"]
