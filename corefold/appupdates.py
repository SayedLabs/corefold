"""App updates for Corefold Uninstaller: which installed apps have a newer version, and updating them,
with Windows' own package manager (winget, which comes with Windows 10 and 11 as "App Installer"), as
UniGetUI and Patch My PC do. Nothing is updated unless it's asked for, one app at a time, each from
where its maker publishes it.

winget prints a table made for people, in the language of Windows: the columns are found from the
line of dashes under its header, counted in screen columns (Chinese or Japanese letters take two).

Nothing here imports Qt; it's slow (winget asks the internet), so the page calls it off the window's
thread."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass

from .core import NO_WINDOW

log = logging.getLogger("corefold")

CHECK_TIMEOUT_S = 240
UPDATE_TIMEOUT_S = 1800
STORE_PAGE = "ms-windows-store://pdp/?productid=9NBLGGH4NNS1"   # App Installer, which has winget
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ID = re.compile(r"^[^\s…]+$")


@dataclass(frozen=True)
class Update:
    name: str
    id: str
    version: str
    available: str
    source: str = ""


def winget() -> str:
    """winget.exe where Windows puts it for this person ("" if it isn't there). Never looked for along
    PATH, where another program could stand in for it."""
    if os.name != "nt":
        return ""
    local = os.environ.get("LOCALAPPDATA", "")
    path = os.path.join(local, "Microsoft", "WindowsApps", "winget.exe") if local else ""
    return path if path and os.path.exists(path) else ""


def _width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _columns(line: str) -> list[tuple[int, str]]:
    """Each character with the screen column it starts at."""
    out, col = [], 0
    for ch in line:
        out.append((col, ch))
        col += _width(ch)
    return out


def _lines(text: str) -> list[str]:
    """What's left on screen: a spinner or a progress bar rewrites its line with \\r."""
    return [_ANSI.sub("", line.rsplit("\r", 1)[-1]).rstrip() for line in text.replace("\r\n", "\n").split("\n")]


def parse(text: str) -> list[Update]:
    """The apps in winget upgrade's first table (the second one lists apps it won't update by itself)."""
    lines = _lines(text)
    dash = next((i for i, line in enumerate(lines) if len(line) >= 10 and set(line) == {"-"}), -1)
    if dash < 1:
        return []
    header = _columns(lines[dash - 1])
    starts = [col for i, (col, ch) in enumerate(header) if not ch.isspace() and (i == 0 or header[i - 1][1].isspace())]
    if len(starts) < 4:
        return []
    found = []
    for line in lines[dash + 1:]:
        if not line.strip():
            break
        cells = [""] * len(starts)
        for col, ch in _columns(line):
            k = max(i for i, s in enumerate(starts) if col >= s) if col >= starts[0] else 0
            cells[k] += ch
        cells = [c.strip() for c in cells]
        name, pid, version, available = cells[:4]
        if not (name and _ID.match(pid) and version and available and " " not in available):
            if found:
                break   # (the line saying how many there are)
            continue
        found.append(Update(name, pid, version, available, cells[4] if len(cells) > 4 else ""))
    return found


def _run(args: list[str], timeout: float) -> tuple[int, str]:
    exe = winget()
    if not exe:
        raise FileNotFoundError("winget")
    r = subprocess.run([exe, *args], capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                       creationflags=NO_WINDOW)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def check() -> list[Update]:
    """The installed apps with a newer version. FileNotFoundError: no winget on this PC."""
    code, out = _run(["upgrade", "--accept-source-agreements", "--disable-interactivity"], CHECK_TIMEOUT_S)
    found = parse(out)
    log.info("app updates: %d (winget said %d)", len(found), code)
    if not found and code:
        log.info("winget upgrade: %s", out.strip()[-600:])
    return found


def update(u: Update) -> tuple[bool, str]:
    """Updates one app silently where its installer allows it (Windows asks first if it needs to be
    administrator). (worked, the last thing winget said)."""
    code, out = _run(["upgrade", "--id", u.id, "--exact", "--silent", "--accept-package-agreements",
                      "--accept-source-agreements", "--disable-interactivity"], UPDATE_TIMEOUT_S)
    said = next((line.strip() for line in reversed(_lines(out)) if line.strip()), "")
    log.info("updating %s to %s: %d %s", u.id, u.available, code, said)
    return code == 0, said
