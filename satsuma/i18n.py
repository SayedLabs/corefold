"""
Translations. Every piece of text people see goes through tr("English text", name=value).

The language is picked once when Satsuma starts (changing it in Settings restarts the app), so
tr() is just a dictionary lookup. English is the source; satsuma/lang_ar.py holds Arabic.
scripts/check_translations.py makes sure nothing is left untranslated.
"""
from __future__ import annotations

import os

LANGUAGES = {"system": "Match Windows", "en": "English", "ar": "العربية"}
RTL_LANGUAGES = {"ar"}

_lang = "en"
_table: dict[str, str] = {}


def windows_language() -> str:
    """'ar' if Windows itself is in Arabic, otherwise 'en'."""
    if os.name != "nt":
        return "en"
    try:
        import ctypes
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "ar" if (langid & 0x3FF) == 0x01 else "en"  # LANG_ARABIC
    except Exception:
        return "en"


def resolve(choice: str) -> str:
    if choice == "system":
        return windows_language()
    return choice if choice in ("en", "ar") else "en"


def set_language(choice: str) -> str:
    """Switches the language for this run of the app. Returns the language actually used."""
    global _lang, _table
    _lang = resolve(choice)
    if _lang == "ar":
        from .lang_ar import STRINGS
        _table = STRINGS
    else:
        _table = {}
    return _lang


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
