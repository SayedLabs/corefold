"""Settings → General: "Save my settings…" in one file, and "Bring back settings…" from one (the file
itself is read and checked in settings_backup.py)."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QMessageBox, QWidget

from . import settings_backup
from .core import short_name
from .i18n import tr

log = logging.getLogger("corefold")


def _documents() -> Path:
    where = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    return Path(where) if where and Path(where).is_dir() else Path.home()


def _filter() -> str:
    return tr("Corefold settings") + f" (*{settings_backup.EXTENSION} *.json)"


def card(win) -> QWidget:
    from .window import button, card as make_card, label
    box, lay = make_card()
    lay.addWidget(label(tr("Your settings in a file"), "h2"))
    lay.addWidget(label(tr("Save your settings in one file, to bring them back later or on another PC. Corefold "
                           "also keeps a copy by itself before each update."), "muted", wrap=True))
    row = QHBoxLayout()
    row.setSpacing(8)
    row.addWidget(button(tr("Save my settings…"), None, lambda: save_file(win)))
    row.addWidget(button(tr("Bring back settings…"), None, lambda: bring_back(win)))
    row.addStretch()
    lay.addLayout(row)
    return box


def _startup_choice(win) -> bool | None:
    from .settings import startup_enabled
    try:
        return startup_enabled() if win.pending_startup is None else win.pending_startup
    except Exception:
        return None


def save_file(win, path: Path | None = None) -> Path | None:
    if path is None:
        chosen, _ = QFileDialog.getSaveFileName(win, tr("Save my settings"),
                                                str(_documents() / settings_backup.file_name()), _filter())
        if not chosen:
            return None
        path = Path(chosen)
        if path.suffix.lower() not in (settings_backup.EXTENSION, ".json"):
            path = path.with_name(path.name + settings_backup.EXTENSION)
    try:
        settings_backup.export(win.c.settings, path, _startup_choice(win))
    except OSError:
        log.exception("couldn't save the settings file")
        QMessageBox.warning(win, tr("Save my settings"), tr("Couldn't save the file. Try another folder."))
        return None
    win.c.toast.notify(tr("Your settings are saved"), path.name, linger_ms=5000)
    return path


def describe(saved: settings_backup.Saved) -> str:
    from .timefmt import date_text
    when = date_text(saved.saved.timestamp(), long=True) if saved.saved else ""
    if when and saved.app_version:
        return tr("Saved on {date} with Corefold {version}.", date=when, version=saved.app_version)
    if when:
        return tr("Saved on {date}.", date=when)
    return tr("Saved with Corefold {version}.", version=saved.app_version or "?")


def links_note(saved: settings_backup.Saved, shown: int = 6) -> str:
    """The quick menu links the file brings: they open programs, so the person sees them before agreeing."""
    found = settings_backup.links(saved)
    if not found:
        return ""
    lines = [f"•  {short_name(name, 40)} ({short_name(target, 70)})" if name else f"•  {short_name(target, 70)}"
             for name, target in found[:shown]]
    if len(found) > shown:
        lines.append(tr("…and {count} more", count=len(found) - shown))
    return "\n\n" + tr("It also adds links to your quick menu ({count}). They open:", count=len(found)) + "\n" + \
        "\n".join(lines)


def bring_back(win, path: Path | None = None, ask: bool = True) -> bool:
    """Reads a settings file, says what it is, and (once the person agrees) uses it and restarts Corefold."""
    title = tr("Bring back settings")
    if path is None:
        chosen, _ = QFileDialog.getOpenFileName(win, title, str(_documents()), _filter())
        if not chosen:
            return False
        path = Path(chosen)
    try:
        saved = settings_backup.read(path)
    except settings_backup.BadFile as e:
        if ask:
            QMessageBox.warning(win, title, tr(str(e)))
        else:
            log.info("not a settings file: %s", e)
        return False
    if ask:
        box = QMessageBox(QMessageBox.Question, title, describe(saved), parent=win)
        box.setInformativeText(tr("Your current settings are replaced with these, and Corefold restarts to use them. "
                                  "Your history, clipboard and window size stay as they are.") + links_note(saved))
        yes = box.addButton(tr("Bring them back"), QMessageBox.AcceptRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.setDefaultButton(yes)
        box.exec()
        go = box.clickedButton() is yes
        box.deleteLater()
        if not go:
            return False
    restore(win, saved)
    return True


def restore(win, saved: settings_backup.Saved):
    from . import __version__
    from . import settings as settings_mod
    c = win.c
    settings_backup.keep_copy(__version__)  # (the settings until now, in case this was a mistake)
    new = settings_backup.apply(c.settings, saved)
    for key in settings_backup.kept_keys():
        setattr(c.settings, key, getattr(new, key))
    # nothing is "waiting for Save" any more: what's on disk is what was brought back
    settings_mod.HELD = None
    win.committed = win._current_settings()
    win.pending_startup = None
    if saved.startup is not None:
        settings_mod.set_startup(saved.startup)
    c.settings.save()
    log.info("settings brought back from a file (Corefold %s)", saved.app_version or "?")
    c.restart("General")


def recovered_note(c):
    """At start: settings.json was lost or damaged and a copy was used. Says so, once."""
    used = settings_backup.RECOVERED
    if used is None:
        return
    settings_backup.RECOVERED = None
    from .timefmt import date_text
    try:
        when = date_text(used.stat().st_mtime, long=True)
    except OSError:
        when = "?"
    c.toast.notify(tr("Settings brought back"), tr("Corefold's settings file was missing or damaged, so it used "
                                                   "the copy from {date}.", date=when), linger_ms=9000)

