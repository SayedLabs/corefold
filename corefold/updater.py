"""
Self-updating via GitHub Releases. No GUI code here.

The build writes the GitHub repository ("owner/name") into _version.py. The app asks GitHub
for the latest release, and if it's newer, downloads the installer, checks its SHA-256 against
the release's SHA256SUMS.txt, and runs it silently. The installer closes the old copy, upgrades
it in place (settings kept) and starts the new one.
"""
from __future__ import annotations

import hashlib
import json
import re
import ssl
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .i18n import tr

INSTALLER_PREFIX = "RakizaCore"
UPDATE_FOLDER = "rakizacore-update"
INSTALLER_RE = re.compile(r"^RakizaCore-Setup-[0-9A-Za-z.\-+]+\.exe$")
SUMS_NAME = "SHA256SUMS.txt"


class UpdateError(Exception):
    pass


CANCELLED = "cancelled"  # not shown to people; the app checks for it


@dataclass
class UpdateInfo:
    version: str
    page_url: str
    installer_name: str | None
    installer_url: str | None
    installer_size: int
    sums_url: str | None
    published: str = ""     # the day it was published ("2026-10-24")
    notes: str = ""         # what's new, as written on its release page (shown as plain text only)


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.10' -> (1, 2, 10). Anything after '-' or '+' (pre-release tags) is ignored."""
    core = re.split(r"[-+]", text.strip().lstrip("vV"), maxsplit=1)[0]
    nums = []
    for part in core.split("."):
        if not part.isdigit():
            break
        nums.append(int(part))
    return tuple(nums) or (0,)


def is_newer(remote: str, local: str) -> bool:
    if local in ("", "dev"):
        return False
    return parse_version(remote) > parse_version(local)


def _request(url: str, user_agent: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": user_agent,
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context())


def _trusted(url: str | None, repo: str) -> bool:
    # GitHub names ignore upper/lower case ("SayedLabs" and "sayedlabs" are the same account)
    return bool(url) and url.lower().startswith(f"https://github.com/{repo}/releases/download/".lower())


def current_repo(data: dict, repo: str) -> str:
    """Where the repository lives now. If the GitHub account or repository was renamed, GitHub
    sends copies that still ask for the old name to the new one; the downloads are then under the
    new name, which this reads from GitHub's own answer (so renaming never breaks updates)."""
    m = re.match(r"https://github\.com/([\w.-]+/[\w.-]+)/releases/", str(data.get("html_url") or ""))
    return m.group(1) if m else repo


def parse_release(data: dict, repo: str) -> UpdateInfo:
    tag = str(data.get("tag_name") or "")
    if not tag:
        raise UpdateError(tr("release has no version tag"))
    if not re.fullmatch(r"[vV]?\d{1,4}(\.\d{1,6}){1,3}([-+][0-9A-Za-z.-]{1,32})?", tag):
        # (it's shown in Rakiza Core's messages and menus: only a plain version number like 1.10.0)
        raise UpdateError(tr("unexpected answer from GitHub"))
    repo = current_repo(data, repo)
    installer = sums = None
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        url = asset.get("browser_download_url")
        if not _trusted(url, repo):
            continue
        if INSTALLER_RE.match(name):
            installer = asset
        elif name == SUMS_NAME:
            sums = asset
    page = str(data.get("html_url") or "")
    if not page.startswith(f"https://github.com/{repo}/releases/"):  # (only ever the official release page)
        page = f"https://github.com/{repo}/releases/latest"
    published = str(data.get("published_at") or "")[:10]
    from .releasenotes import MAX_TEXT
    return UpdateInfo(
        version=tag.lstrip("vV"),
        page_url=page,
        installer_name=installer["name"] if installer else None,
        installer_url=installer["browser_download_url"] if installer else None,
        installer_size=int(installer.get("size") or 0) if installer else 0,
        sums_url=sums["browser_download_url"] if sums else None,
        published=published if re.fullmatch(r"\d{4}-\d{2}-\d{2}", published) else "",
        notes=str(data.get("body") or "")[:MAX_TEXT],
    )


def check(repo: str, current: str, timeout: float = 15) -> UpdateInfo | None:
    """Returns info about a newer release, or None if this copy is up to date."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
        raise UpdateError(tr("updates aren't set up for this copy"))
    try:
        with _request(f"https://api.github.com/repos/{repo}/releases/latest", f"RakizaCore/{current}",
                      timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # no releases published yet
        raise UpdateError(tr("GitHub answered {code}", code=e.code)) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(tr("couldn't reach GitHub (are you online?)")) from e
    except ValueError as e:
        raise UpdateError(tr("unexpected answer from GitHub")) from e
    info = parse_release(data, repo)
    if not info.installer_url or not info.sums_url:
        # Just published: GitHub is still building the installer (about 10 minutes). Saying
        # "update available" now would only end in "this release has no installer".
        return None
    return info if is_newer(info.version, current) else None


def expected_hash(sums_text: str, name: str) -> str | None:
    for line in sums_text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return None


def download(info: UpdateInfo, current: str, progress: Callable[[float], None] | None = None,
             cancel: threading.Event | None = None, timeout: float = 30) -> Path:
    """Downloads and verifies the installer. Returns its path in the temp folder."""
    if not info.installer_url or not info.installer_name:
        raise UpdateError(tr("this release has no installer"))
    if not info.sums_url:
        raise UpdateError(tr("this release has no checksum file, so it can't be verified"))
    progress = progress or (lambda f: None)
    cancel = cancel or threading.Event()
    ua = f"RakizaCore/{current}"

    try:
        with _request(info.sums_url, ua, timeout) as resp:
            want = expected_hash(resp.read(1_000_000).decode("utf-8", "replace"), info.installer_name)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(tr("the download failed. Check your internet connection")) from e
    if not want:
        raise UpdateError(tr("the installer isn't listed in the checksum file"))

    folder = Path(tempfile.gettempdir()) / UPDATE_FOLDER
    folder.mkdir(exist_ok=True)
    dest = folder / info.installer_name
    part = folder / (info.installer_name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with _request(info.installer_url, ua, timeout) as resp, open(part, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or info.installer_size or 0)
            # never more than GitHub said the file is (or 1 GB if it didn't say): a wrong answer can't fill the disk
            limit = (info.installer_size or 1_000_000_000) + 1_000_000
            while True:
                if cancel.is_set():
                    raise UpdateError(CANCELLED)
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                if done + len(chunk) > limit:
                    raise UpdateError(tr("the download was damaged (checksum mismatch). Try again later"))
                fh.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if total:
                    progress(done / total)
        if digest.hexdigest() != want:
            raise UpdateError(tr("the download was damaged (checksum mismatch). Try again later"))
        dest.unlink(missing_ok=True)
        part.rename(dest)
        return dest
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(tr("the download failed. Check your internet connection")) from e
    finally:
        part.unlink(missing_ok=True)


def clean_old_installers(keep_days: float = 1.0):
    """Deletes installers left from earlier updates (about 100 MB each), also the one a version from
    before 1.7.0 downloaded to update to Rakiza Core (its own folder goes too once it's empty)."""
    import time
    from .core import OLD_APP_NAMES
    temp = Path(tempfile.gettempdir())
    cutoff = time.time() - keep_days * 86400
    for folder, prefix in ((temp / UPDATE_FOLDER, INSTALLER_PREFIX),
                           *((temp / f"{name.lower()}-update", name) for name in OLD_APP_NAMES)):
        for p in list(folder.glob(f"{prefix}-Setup-*")) if folder.is_dir() else []:
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
    for name in OLD_APP_NAMES:
        try:
            (temp / f"{name.lower()}-update").rmdir()  # only if it's empty
        except OSError:
            pass


# How the installer ends when it didn't get to install (Windows' permission wasn't given, or it was cancelled)
NOT_ALLOWED = {1, 2, 1223}


def run_installer(path: Path) -> subprocess.Popen:
    """Starts the installer in silent mode. Rakiza Core is installed for the whole PC (in Program Files), so the
    installer asks Windows for permission itself (that way it can start Rakiza Core again afterwards as the person,
    never with administrator rights). Once it has it, it asks Rakiza Core to close, upgrades it and starts it again.
    The installer is returned: if it ends while Rakiza Core still runs, nothing was installed."""
    from . import __version__, settings_backup
    settings_backup.keep_copy(__version__)  # the settings as they were, in case the new version loses them
    return subprocess.Popen([str(path), "/SILENT", "/SP-", "/SUPPRESSMSGBOXES", "/NORESTART"], close_fds=True)


# ---- updates without Windows' question each time (installer\silent-update.ps1)
# The installer adds a task that runs as SYSTEM and that anyone signed in may start (never change). Started, it
# downloads the newest release from GitHub itself, checks it, and says how it goes in a file next to Rakiza Core
# (in a folder only administrators and Windows can write to). Rakiza Core gives it nothing: it only starts it, reads
# what it says, and closes when it says "ready". Anything else (no task: a copy installed before this came, a portable one,
# a PC where it can't run) and the update goes the way it always did: Windows asks.

SILENT_TASK = "\\Rakiza Core\\Update"
SILENT_STATUS = Path("_update") / "status.txt"


def silent_status_path() -> Path | None:
    import sys
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent / SILENT_STATUS


def start_silent_update() -> bool:
    """Asks Task Scheduler to start the update task. True when it took the request (the task may still not run:
    what it says in silent_status() tells)."""
    if silent_status_path() is None:
        return False
    from .core import system_tool
    try:
        r = subprocess.run([system_tool("schtasks.exe"), "/Run", "/TN", SILENT_TASK], capture_output=True,
                           timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def silent_status(since: float) -> str | None:
    """What the update task said, if it said it after `since` (a time.time()): "started", "downloading 0.42",
    "ready", "installing", "done <code>" or "failed <why>". None while it hasn't said anything (yet)."""
    path = silent_status_path()
    if path is None:
        return None
    try:
        if path.stat().st_mtime < since - 2:
            return None  # (what it said the last time)
        return path.read_text("utf-8-sig", "replace").strip()[:300] or None
    except OSError:
        return None


def silent_progress(status: str | None) -> float | None:
    m = re.fullmatch(r"downloading ([0-9.]+)", status or "")
    try:
        return max(0.0, min(1.0, float(m.group(1)))) if m else None
    except ValueError:
        return None
