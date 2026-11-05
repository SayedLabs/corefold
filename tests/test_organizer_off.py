"""The kinds left out of a sort are kept in a small file of their own: ticking one rewrote the whole state (every
move ever made) and a big one held the window 5 s. What older versions kept in the state still counts."""
import json

from corefold import organizer


def test_ticking_a_kind_leaves_the_state_alone(tmp_path, monkeypatch):
    state = tmp_path / "organizer.json"
    monkeypatch.setattr(organizer, "_state_path", lambda: state)
    state.write_text(json.dumps({"off": ["videos"], "moves": ["x"] * 1000}), encoding="utf-8")
    assert organizer.kinds_off() == {"videos"}  # (from an older version)
    before = state.stat().st_mtime_ns, state.read_text(encoding="utf-8")
    organizer.set_off({"documents", "music"})
    assert (state.stat().st_mtime_ns, state.read_text(encoding="utf-8")) == before
    assert organizer.kinds_off() == {"documents", "music"}
    organizer.set_off(set())
    assert organizer.kinds_off() == set()


def test_a_broken_file_falls_back(tmp_path, monkeypatch):
    state = tmp_path / "organizer.json"
    monkeypatch.setattr(organizer, "_state_path", lambda: state)
    (tmp_path / organizer.OFF_FILE).write_text("{not json", encoding="utf-8")
    assert organizer.kinds_off() == set()
