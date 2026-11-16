"""Startup is back in Computer (2.3), after being checked again: it's a tool in use, it's switched on once for
everyone, and the fixes from that check (Squirrel apps' names, entries with the same name, a task's state)."""
from unittest import mock

from corefold import settings as settings_mod
from corefold import startup, startuppage
from corefold import tools as all_tools


def test_startup_is_in_use_in_computer_and_scan_still_rests():
    assert all_tools.state("startup") == all_tools.AVAILABLE
    assert all_tools.state("scan") == all_tools.PAUSED
    computer = all_tools.SECTION_BY_PAGE["Computer"]
    assert "startup" in {t.id for t in all_tools.in_section(settings_mod.Settings(), computer)}
    assert all_tools.is_on(settings_mod.Settings(), "startup")


def test_it_comes_back_on_once_after_the_update_even_if_it_was_off():
    """Someone who already had Space and the Uninstaller switched back on (2.0 to 2.2) and had Startup off
    from when it was there before (1.8): it's on again once, and stays as they leave it afterwards."""
    s = settings_mod.Settings()
    s.known_tools = sorted(t.id for t in all_tools.available())  # (startup too: it was there in 1.8)
    s.back_on = ["space", "uninstaller"]
    s.tools_off = ["startup", "network"]
    assert [t.id for t in all_tools.switch_back_on(s)] == ["startup"]
    assert all_tools.is_on(s, "startup") and not all_tools.is_on(s, "network")
    assert all_tools.offer_new_tools(s) == []  # (not offered as New as well)
    s.tools_off = ["startup"]  # (switched off again)
    assert all_tools.switch_back_on(s) == [] and not all_tools.is_on(s, "startup")


def test_squirrel_apps_are_named_after_the_program_update_exe_starts(tmp_path):
    """Discord, Slack, GitHub Desktop: their line runs "Update.exe --processStart Discord.exe"."""
    home = tmp_path / "Discord"
    (home / "app-1.0.9").mkdir(parents=True)
    (home / "app-1.0.10").mkdir()
    for p in (home / "Update.exe", home / "Discord.exe", home / "app-1.0.9" / "Discord.exe",
              home / "app-1.0.10" / "Discord.exe"):
        p.write_bytes(b"MZ")
    update = str(home / "Update.exe")
    newest = str(home / "app-1.0.10" / "Discord.exe")  # (1.0.10 is newer than 1.0.9, not older)
    assert startup.started_program(update, f'"{update}" --processStart Discord.exe') == newest
    assert startup.started_program(update, f'"{update}" --processStart "Discord.exe" --process-start-args '
                                           f'"--start-minimized"') == newest
    assert startup.started_program(update, f"{update} --processStart=Discord.exe") == newest
    # only a program's own name next to Update.exe, never a path somewhere else
    assert startup.started_program(update, f'"{update}" --processStart ..\\other\\evil.exe') == update
    assert startup.started_program(update, f'"{update}" --processStart Missing.exe') == update
    assert startup.started_program(update, f'"{update}" --update https://example.com') == update
    other = str(tmp_path / "Tool.exe")
    assert startup.started_program(other, f'"{other}" --processStart Discord.exe') == other
    (home / "app-1.0.10" / "Discord.exe").unlink()
    (home / "app-1.0.9" / "Discord.exe").unlink()
    assert startup.started_program(update, f'"{update}" --processStart Discord.exe') == str(home / "Discord.exe")


def _entry(name, value, where="HKCU-Run", **extra):
    return startup.Entry(id=f"{where}|{value}", name=name, value=value, command="", exe="", where=where,
                         extra=dict(extra))


def test_entries_with_the_same_name_say_which_is_which():
    entries = [_entry("Opera GX auto-updater", "\\Opera GX scheduled Autoupdate 1754772823", "Task"),
               _entry("Opera GX auto-updater", "\\Opera GX scheduled assistant Autoupdate 1755846059", "Task"),
               _entry("Discord", "Discord"), _entry("Discord", "DiscordCanary"),
               _entry("Notes", "Notes.lnk", "User-Folder"), _entry("Notes", "Notes.lnk", "Common-Folder"),
               _entry("Steam", "Steam")]
    startup.mark_same_names(entries)
    also = [e.extra.get("also") for e in entries]
    assert also == ["Opera GX scheduled Autoupdate 1754772823", "Opera GX scheduled assistant Autoupdate 1755846059",
                    None, "DiscordCanary", None, None, None]
    with mock.patch.object(startuppage, "tr", side_effect=lambda text, **kw: text.format(**kw)):
        assert startuppage.describe(entries[3]).endswith("  ·  DiscordCanary")
        assert "Steam" not in startuppage.describe(entries[6])


def test_a_tasks_state_is_read_with_task_schedulers_own_object(monkeypatch):
    from corefold import uninstall
    asked = []

    def fake(script, timeout=90, env=None):
        asked.append((script, env))
        return {"\\Folder": "False", "\\": "True"}.get(env["CF_TASK_PATH"], "")
    monkeypatch.setattr(uninstall, "powershell", fake)
    assert startup._task_enabled("\\Folder\\My task") is False
    assert asked[-1][1] == {"CF_TASK_PATH": "\\Folder", "CF_TASK_NAME": "My task"}
    assert "Schedule.Service" in asked[-1][0] and "Get-ScheduledTask" not in asked[-1][0]
    assert startup._task_enabled("\\GCC") is True
    assert asked[-1][1] == {"CF_TASK_PATH": "\\", "CF_TASK_NAME": "GCC"}
    assert startup._task_enabled("\\Gone\\Task") is None  # (can't be read: not taken as on or off)
