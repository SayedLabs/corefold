"""2.0.1: the default colour is Ink (calm black/white) instead of the logo's teal."""
from corefold import settings as st


def test_new_people_get_ink():
    assert st.Settings().accent == st.DEFAULT_ACCENT == "ink"


def test_teal_from_before_moves_to_ink_once():
    assert st.Settings.from_data({"accent": "core"}).accent == "ink"


def test_teal_picked_after_the_move_stays():
    assert st.Settings.from_data({"accent": "core", "accent_moved": True}).accent == "core"


def test_other_colours_stay():
    assert st.Settings.from_data({"accent": "grape"}).accent == "grape"
