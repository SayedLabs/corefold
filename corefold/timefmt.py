"""Dates and "5 min ago" in the app's language (not always Windows' language)."""
from __future__ import annotations

import time

from PySide6.QtCore import QDateTime, QLocale

from . import i18n
from .i18n import tr


def locale() -> QLocale:
    return QLocale({"ar": QLocale.Language.Arabic, "fr": QLocale.Language.French, "es": QLocale.Language.Spanish,
                    "tr": QLocale.Language.Turkish}.get(i18n.language(), QLocale.Language.English))


def date_text(ts: float, long: bool = False) -> str:
    when = QDateTime.fromSecsSinceEpoch(int(ts)).date()
    return locale().toString(when, "d MMMM yyyy" if long else "d MMM")


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
