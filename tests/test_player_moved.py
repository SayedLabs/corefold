"""The Player has left Rakiza Core (it moves to Rakiza Play): nothing of it is listed or loaded, its old
"Open with" entry for videos and music is always taken out, and a page or command an older Rakiza Core
still sends lands somewhere sensible."""
from pathlib import Path

from corefold import assoc, settings, tools as all_tools


def test_the_player_is_not_a_tool_any_more():
    assert "player" not in all_tools.BY_ID
    assert all("player" not in sec.tools for sec in all_tools.SECTIONS)
    assert not (Path(all_tools.__file__).parent / "player.py").exists()


def test_old_settings_with_the_player_still_load():
    s = settings.Settings.from_data({"tools_off": ["player"], "known_tools": ["converter", "player"],
                                     "open_media": True, "player_volume": 40, "last_page": "Player"})
    assert s.tools_off == ["player"] and not hasattr(s, "open_media")
    assert all_tools.state("player") == all_tools.PLANNED  # (an id nobody knows: nothing of it runs)


def test_its_open_with_entry_is_always_taken_out(monkeypatch):
    """sync_archive_opening asks for the media group to be off, whatever the old setting said."""
    from corefold import app
    asked = {}
    monkeypatch.setattr(assoc, "sync_in_background", lambda wanted, *a, **k: asked.update(wanted))
    c = app.Controller.__new__(app.Controller)
    c.settings = settings.Settings()
    app.Controller.sync_archive_opening(c)
    assert asked["media"][0] is False
    assert "media" in assoc.GROUPS  # (kept so unregister() can still find what an older Rakiza Core wrote)


def test_old_pages_open_something_else():
    from corefold import window
    assert "Player" not in window.PAGES and "PlayerSettings" not in window.PAGES
    assert window.OLD_PAGES["Player"] == "Home" and window.OLD_PAGES["PlayerSettings"] == "General"
