"""
Translations. Every piece of text people see goes through tr("English text", name=value).

The language is picked once when Satsuma starts (changing it in Settings restarts the app), so
tr() is just a dictionary lookup. English is the source; satsuma/lang_<code>.py hold the others
(Arabic, French, Spanish, Turkish).
scripts/check_translations.py makes sure nothing is left untranslated.
"""
from __future__ import annotations

import os

LANGUAGES = {"system": "Match Windows", "en": "English", "ar": "العربية", "fr": "Français", "es": "Español",
             "tr": "Türkçe"}
# Windows' primary language IDs -> ours
WINDOWS_LANGUAGES = {0x01: "ar", 0x0C: "fr", 0x0A: "es", 0x1F: "tr"}
RTL_LANGUAGES = {"ar"}

_lang = "en"
_table: dict[str, str] = {}


def windows_language() -> str:
    """The language Windows itself is in, if Satsuma has it; otherwise 'en'."""
    if os.name != "nt":
        return "en"
    try:
        import ctypes
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return WINDOWS_LANGUAGES.get(langid & 0x3FF, "en")
    except Exception:
        return "en"


def resolve(choice: str) -> str:
    if choice == "system":
        return windows_language()
    return choice if choice in LANGUAGES else "en"


def set_language(choice: str) -> str:
    """Switches the language for this run of the app. Returns the language actually used."""
    global _lang, _table
    _lang = resolve(choice)
    _table = table_for(_lang)
    return _lang


def table_for(lang: str) -> dict[str, str]:
    if lang == "ar":
        from .lang_ar import STRINGS
    elif lang == "fr":
        from .lang_fr import STRINGS
    elif lang == "es":
        from .lang_es import STRINGS
    elif lang == "tr":
        from .lang_tr import STRINGS
    else:
        return {}
    return STRINGS


def language() -> str:
    return _lang


def is_rtl() -> bool:
    return _lang in RTL_LANGUAGES


def tr(text: str, /, **values) -> str:
    """The text in the current language, with {names} filled in."""
    out = _table.get(text, text) if _table else text
    if values:
        try:
            return out.format(**values)
        except (KeyError, IndexError, ValueError):  # a broken translation must never crash the app
            return text.format(**values)
    return out
