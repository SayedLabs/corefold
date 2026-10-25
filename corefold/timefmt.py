"""Dates and "5 min ago" in the app's language (not always Windows' language)."""
from __future__ import annotations

import time

from PySide6.QtCore import QDateTime, QLocale

from . import i18n
from .i18n import tr


def locale() -> QLocale:
    return QLocale({"ar": QLocale.Language.Arabic, "fr": QLocale.Language.French, "es": QLocale.Language.Spanish,
                    "tr": QLocale.Language.Turkish}.get(i18n.language(), QLocale.Language.English))


_LATIN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٬٫", "0123456789,.")


def latin_digits(text: str) -> str:
    """Qt's Arabic writes "٢٥ أكتوبر ٢٠٢٦"; the rest of Corefold (sizes, counts, times) writes 0-9, so a date
    in a row next to "29.3 KB" is written with the same digits."""
    return text.translate(_LATIN_DIGITS) if i18n.language() == "ar" else text


def number(n: int) -> str:
    """A count, grouped the way the language writes it ("1,234", "1 234"). In Arabic with the same digits as the
    sizes and counts beside it (Qt's Arabic writes "١٬٢٣٤", which looked broken next to "29.9 KB")."""
    if i18n.language() == "ar":
        return f"{int(n):,}"
    return locale().toString(int(n))


def date_text(ts: float, long: bool = False) -> str:
    when = QDateTime.fromSecsSinceEpoch(int(ts)).date()
    return latin_digits(locale().toString(when, "d MMMM yyyy" if long else "d MMM"))


def time_ago(ts: float) -> str:
    d = time.time() - ts
    if d < 60:
        return tr("just now")
    if d < 3600:
        return tr("{count} min ago", count=int(d // 60))
    if d < 86400:
        return tr("{count} h ago", count=int(d // 3600))
    if d < 2 * 86400:
        return tr("yesterday")
    if d < 7 * 86400:
        return tr("{count} days ago", count=int(d // 86400))
    return date_text(ts)
