"""Report a problem (About page): one zip on the Desktop with Corefold's logs, its settings and a few lines
about the PC, so a bug report can say what happened. Nothing is sent: the person attaches it if they want.
Their Windows user name is hidden in it."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

from . import sheet
from .i18n import tr

log = logging.getLogger("corefold")

YOU = "<you>"
# C:\Users\<name>\… in every way it gets written (\, \\ in JSON, /)
_USERS = re.compile(r"(?i)(\b[a-z]:(?:\\\\|\\|/)+users(?:\\\\|\\|/)+)([^\\/\"'\s:*?<>|]+)")
_NOT_A_PERSON = {"public", "default", "default user", "all users", YOU}
# what's left out of the copied settings: the names of files opened lately
_PRIVATE = ("recent_archives", "recent_views", "last_action")


def user_names() -> list[str]:
    names = {os.environ.get("USERNAME", ""), Path.home().name}
    return sorted((n for n in names if len(n) >= 3 and n.lower() not in _NOT_A_PERSON), key=len, reverse=True)


def mask(text: str, names: list[str] | None = None) -> str:
    """The Windows user name hidden: C:\\Users\\Sam -> C:\\Users\\<you> (and "Sam" anywhere else)."""
    # the whole names first: "C:\Users\John Smith" would otherwise lose only "John" to the folder rule below
    for name in user_names() if names is None else names:
        text = re.sub(r"(?i)(?<![0-9a-z])" + re.escape(name) + r"(?![0-9a-z])", YOU, text)
    return _USERS.sub(lambda m: m.group(0) if m.group(2).lower() in _NOT_A_PERSON else m.group(1) + YOU, text)


def windows_text() -> str:
    """"Windows 11 Pro 23H2 (build 22631.4317), 64-bit"."""
    import platform
    bits = "64-bit" if platform.machine().endswith("64") or os.environ.get("PROCESSOR_ARCHITEW6432") else "32-bit"
    if sys.platform != "win32":
        return f"{platform.system()} {platform.release()}, {bits}"
    build = sys.getwindowsversion().build
    name, release, ubr = "Windows", "", ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            def value(n):
                try:
                    return winreg.QueryValueEx(k, n)[0]
                except OSError:
                    return ""
            name = str(value("ProductName") or name)
            release = str(value("DisplayVersion") or value("ReleaseId") or "")
            ubr = value("UBR")
    except OSError:
        pass
    if build >= 22000:  # Windows 11 still calls itself "Windows 10" there
        name = name.replace("Windows 10", "Windows 11")
    full = f"{build}.{ubr}" if isinstance(ubr, int) else str(build)
    return " ".join(x for x in (name, release) if x) + f" (build {full}), {bits}"


def screens_text() -> list[str]:
    """Each screen's size in pixels and its scale."""
    try:
        from PySide6.QtGui import QGuiApplication
        out = []
        for scr in QGuiApplication.screens():
            ratio = scr.devicePixelRatio()
            size = scr.geometry().size()
            out.append(f"{round(size.width() * ratio)} x {round(size.height() * ratio)} at {round(ratio * 100)}%")
        return out
    except Exception:
        return []


def system_text(settings, screens: list[str] | None = None) -> str:
    import platform
    import struct
    from . import __version__, i18n
    from . import tools as all_tools
    from .settings import installed_uninstaller
    kind = "installed" if installed_uninstaller() else ("portable" if getattr(sys, "frozen", False) else "from source")
    try:
        from PySide6 import __version__ as qt
    except Exception:
        qt = "?"
    tools = ", ".join(t.id for t in all_tools.enabled(settings)) or "none"
    lines = [
        f"Corefold {__version__} ({kind})",
        windows_text(),
        f"Language: {i18n.language()} (chosen: {settings.language}, Windows: {i18n.windows_language()})",
        "Screens: " + ("; ".join(screens_text() if screens is None else screens) or "?"),
        f"Tools in use: {tools}",
        f"Resting for now: {', '.join(sorted(all_tools.PAUSED_SECTIONS)) or 'nothing'}",
        f"Quick menu: {'on' if settings.quick_menu else 'off'}, quick wheel: {'on' if settings.quick_wheel else 'off'}",
        f"Look: {settings.appearance}, text {settings.window_text}, "
        f"animations {'on' if settings.animations else 'off'}",
        f"Python {platform.python_version()} ({struct.calcsize('P') * 8}-bit), PySide6 {qt}",
        f"Made {datetime.now().isoformat(timespec='seconds')}",
    ]
    return "\n".join(lines) + "\n"


def report_folder() -> Path:
    """The Desktop (or Downloads, or the home folder if there's no Desktop)."""
    places = []
    try:
        from PySide6.QtCore import QStandardPaths
        for kind in (QStandardPaths.DesktopLocation, QStandardPaths.DownloadLocation):
            where = QStandardPaths.writableLocation(kind)
            if where:
                places.append(Path(where))
    except Exception:
        pass
    places += [Path.home() / "Desktop", Path.home() / "Downloads"]
    for p in places:
        if p.is_dir() and os.access(p, os.W_OK):
            return p
    return Path.home()


def _free_name(folder: Path, stem: str, ext: str) -> Path:
    path = folder / f"{stem}{ext}"
    n = 2
    while path.exists():
        path = folder / f"{stem} ({n}){ext}"
        n += 1
    return path


def make(settings, folder: Path | None = None, screens: list[str] | None = None) -> Path:
    """Makes "Corefold report <date>.zip". Raises OSError if it can't be written."""
    import zipfile
    from .settings import LOG_DIR
    folder = folder or report_folder()
    target = _free_name(folder, f"Corefold report {datetime.now().strftime('%Y-%m-%d')}", ".zip")
    names = user_names()
    values = asdict(settings)
    for key in _PRIVATE:
        if key in values:
            values[key] = type(values[key])()
    try:
        logs = sorted(p for p in LOG_DIR.glob("*.log*") if p.is_file())
    except OSError:
        logs = []
    tmp = target.with_name(target.name + ".part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for p in logs:
                try:
                    text = p.read_text("utf-8", errors="replace")
                except OSError:
                    continue
                z.writestr(f"logs/{p.name}", mask(text, names))
            z.writestr("settings.json", mask(json.dumps(values, indent=2, ensure_ascii=False), names))
            z.writestr("system.txt", mask(system_text(settings, screens), names))
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    log.info("made a problem report")
    return target


def issue_url(repo: str, version: str, windows: str | None = None) -> str:
    """GitHub's "new issue" page with the version and Windows filled in (nothing else: no paths, no names)."""
    windows = windows_text() if windows is None else windows
    body = "\n".join([f"Corefold {version}", windows, "", "### " + tr("What happened?"), "", "",
                      "### " + tr("What did you expect?"), "", "",
                      tr("If you want, attach the \"Corefold report\" zip from your Desktop here."), ""])
    query = urlencode({"title": f"Corefold {version}: ", "body": body}, quote_via=quote)
    return f"https://github.com/{repo}/issues/new?{query}"


# ----------------------------------------------------------------------------- on the About page

def card(win):
    from .window import button, card as make_card, label
    box, lay = make_card()
    lay.addWidget(label(tr("Something not working?"), "h2"))
    lay.addWidget(label(tr("Make a file with Corefold's logs and a few details about your PC, to attach to a "
                           "bug report. Nothing is sent by itself."), "muted", wrap=True))
    from PySide6.QtCore import Qt
    lay.addWidget(button(tr("Report a problem…"), None, lambda: report_problem(win)), 0, Qt.AlignLeading)
    return box


def report_problem(win, ask: bool = True) -> Path | None:
    """Says what goes in the file, makes it, shows it in File Explorer and opens GitHub's report page."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QMessageBox
    from . import UPDATE_REPO, __version__
    from .winutil import reveal_in_explorer
    if ask:
        box = QMessageBox(QMessageBox.Information, tr("Report a problem"),
                          tr("Corefold makes a zip file on your Desktop with:"), parent=win)
        box.setInformativeText("\n".join([
            "•  " + tr("Its log files (what it did lately)"),
            "•  " + tr("Your settings"),
            "•  " + tr("Your Windows version, screens and the tools you use"),
            "", tr("Your Windows user name is hidden in it. Nothing is sent: you choose whether to attach it to "
                   "your report.")]))
        yes = box.addButton(tr("Make the file"), QMessageBox.AcceptRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        sheet.run(box)
        go = box.clickedButton() is yes
        box.deleteLater()
        if not go:
            return None
    try:
        path = make(win.c.settings)
    except OSError as e:
        log.exception("couldn't make the problem report")
        sheet.warning(win, tr("Report a problem"), tr("Couldn't make the report: {error}",
                                                      error=e.strerror or str(e)))
        return None
    reveal_in_explorer(path)
    if UPDATE_REPO:
        QDesktopServices.openUrl(QUrl(issue_url(UPDATE_REPO, __version__), QUrl.TolerantMode))
        text = tr("It's selected in File Explorer. On the GitHub page, attach it to your report if you want.")
    else:
        text = tr("It's selected in File Explorer.")
    win.c.toast.notify(tr("The report is on your Desktop") if path.parent.name == "Desktop"
                       else tr("The report is ready"), text, linger_ms=9000)
    return path
