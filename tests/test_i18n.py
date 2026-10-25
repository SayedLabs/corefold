"""Languages: Arabic turns the layout right-to-left and every language falls back to English safely."""
import pytest

from corefold import i18n


@pytest.fixture(autouse=True)
def back_to_english():
    yield
    i18n.set_language("en")


def test_arabic_is_right_to_left():
    assert i18n.set_language("ar") == "ar"
    assert i18n.is_rtl()
    assert i18n.tr("Copy the path") != "Copy the path"


@pytest.mark.parametrize("lang", ["fr", "es", "tr"])
def test_other_languages_are_left_to_right(lang):
    assert i18n.set_language(lang) == lang
    assert not i18n.is_rtl()


def test_unknown_language_falls_back_to_english():
    assert i18n.set_language("xx") == "en"
    assert i18n.tr("Copy the path") == "Copy the path"
