"""
Translations. Every piece of text people see goes through tr("English text", name=value).

The language is picked once when Corefold starts (changing it in Settings restarts the app), so
tr() is just a dictionary lookup. English is the source; corefold/lang_<code>.py hold the others
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
    """The language Windows itself is in, if Corefold has it; otherwise 'en'."""
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
            out = out.format(**values)
        except (KeyError, IndexError, ValueError):  # a broken translation must never crash the app
            return text.format(**values)
        if _lang == "ar" and any(isinstance(v, int) or (isinstance(v, str) and v[:1].isdigit())
                                 for v in values.values()):
            out = arabic_counts(out)
    return _rtl_start(_isolate_latin(out)) if _lang in RTL_LANGUAGES else out


_LATIN = "A-Za-z0-9\u00c0-\u024f"
_latin_run_re = None


def _isolate_latin(text: str) -> str:
    """Latin words with spaces between them in an Arabic sentence ("Ctrl + K", "File Explorer") are kept
    in a box of their own (Unicode isolates): Qt otherwise measures the first wrapped line a little too wide
    and cuts its last word off at the left edge. HTML tags are left as they are."""
    global _latin_run_re
    if " " not in text or not any("a" <= ch.lower() <= "z" for ch in text):
        return text
    if _latin_run_re is None:
        import re
        token = f"[{_LATIN}](?:[{_LATIN}._'\u2019&+\\/:-]*[{_LATIN}])?"
        _latin_run_re = re.compile(f"{token}(?:(?:[ \u00a0]+[+&/\u2013-])*[ \u00a0]+{token})+")

    def box(m):
        run = m.group(0)
        return f"\u2066{run}\u2069" if any(ch.isalpha() and ch.isascii() or "\u00c0" <= ch <= "\u024f"
                                            for ch in run) else run
    if "<" in text:
        import re
        parts = re.split(r"(<[^>]*>)", text)
        return "".join(part if part.startswith("<") else _latin_run_re.sub(box, part) for part in parts)
    return _latin_run_re.sub(box, text)


# Arabic says a count's noun differently by the number: 1 ملف, ملفين, 3–10 ملفات, 11–99 ملفًا, 100 ملف.
# The translations are written with one form ("{count} ملف" or "{count} ملفات"); the right one is put in here.
# (any form the translation uses) -> (one, two, 3 to 10, 11 to 99)
_AR_NOUNS = {
    ("دقيقة", "دقائق"): ("دقيقة", "دقيقتين", "دقائق", "دقيقة"),
    ("ساعة", "ساعات"): ("ساعة", "ساعتين", "ساعات", "ساعة"),
    ("يوم", "أيام", "يومًا"): ("يوم", "يومين", "أيام", "يومًا"),
    ("ثانية", "ثوان", "ثوانٍ"): ("ثانية", "ثانيتين", "ثوانٍ", "ثانية"),
    ("ملف", "ملفات", "ملفًا"): ("ملف", "ملفين", "ملفات", "ملفًا"),
    ("مجلد", "مجلدات", "مجلدًا"): ("مجلد", "مجلدين", "مجلدات", "مجلدًا"),
    ("أداة", "أدوات"): ("أداة", "أداتين", "أدوات", "أداة"),
    ("أرشيف", "أرشيفات", "أرشيفًا"): ("أرشيف", "أرشيفين", "أرشيفات", "أرشيفًا"),
    ("تطبيق", "تطبيقات", "تطبيقًا"): ("تطبيق", "تطبيقين", "تطبيقات", "تطبيقًا"),
    ("صفحة", "صفحات"): ("صفحة", "صفحتين", "صفحات", "صفحة"),
    ("صورة", "صور"): ("صورة", "صورتين", "صور", "صورة"),
    ("عنصر", "عناصر", "عنصرًا"): ("عنصر", "عنصرين", "عناصر", "عنصرًا"),
    ("مرفق", "مرفقات", "مرفقًا"): ("مرفق", "مرفقين", "مرفقات", "مرفقًا"),
    ("نسخة", "نسخ"): ("نسخة", "نسختين", "نسخ", "نسخة"),
}
_AR_FORMS = {word: forms for words, forms in _AR_NOUNS.items() for word in words}
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_ar_count_re = None


def arabic_counts(text: str) -> str:
    global _ar_count_re
    if _ar_count_re is None:
        import re
        words = "|".join(sorted(map(re.escape, _AR_FORMS), key=len, reverse=True))
        _ar_count_re = re.compile(r"(?<![\w.,٬])([0-9٠-٩][0-9٠-٩,٬]*) (" + words + r")(?![\w])")

    def fix(m):
        digits = m.group(1).translate(_AR_DIGITS).replace(",", "").replace("٬", "")
        try:
            n = int(digits)
        except ValueError:
            return m.group(0)
        one, two, few, many = _AR_FORMS[m.group(2)]
        if n == 2:
            return two  # "منذ دقيقتين", not "منذ 2 دقيقة"
        rest = n % 100
        if 3 <= rest <= 10:
            return f"{m.group(1)} {few}"
        if 11 <= rest <= 99:
            return f"{m.group(1)} {many}"
        return f"{m.group(1)} {one}"
    return _ar_count_re.sub(fix, text)


def _rtl_start(text: str) -> str:
    """An Arabic sentence that starts with a Latin word ("Space pauses…", "{name} is protected…") would
    be laid out left to right, with its words and full stop out of order: mark it as right to left."""
    import unicodedata
    for ch in text:
        kind = unicodedata.bidirectional(ch)
        if kind in ("R", "AL"):
            return text
        if kind == "L":
            break
    else:
        return text
    if any(unicodedata.bidirectional(ch) in ("R", "AL") for ch in text):
        return "\u200f" + text  # (text with no Arabic at all, like "PNG", stays as it is)
    return text


def ltr(text: str) -> str:
    """A Latin name (an app, a file, a company) shown in Arabic: kept in its own reading order, so
    "7-Zip 26.04 (x64)" doesn't come out as "Zip 26.04 (x64)-7"."""
    if not text or not is_rtl():
        return text
    import unicodedata
    for ch in text:
        kind = unicodedata.bidirectional(ch)
        if kind == "L":
            return f"‪{text}‬"
        if kind in ("R", "AL"):
            return text
    return f"‪{text}‬"  # only numbers and signs: keep them as written too
