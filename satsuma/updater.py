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

INSTALLER_RE = re.compile(r"^Satsuma-Setup-[0-9A-Za-z.\-+]+\.exe$")
SUMS_NAME = "SHA256SUMS.txt"


class UpdateError(Exception):
    pass


@dataclass
class UpdateInfo:
    version: str
    page_url: str
    installer_name: str | None
    installer_url: str | None
    installer_size: int
    sums_url: str | None


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
    return bool(url) and url.startswith(f"https://github.com/{repo}/releases/download/")


def parse_release(data: dict, repo: str) -> UpdateInfo:
    tag = str(data.get("tag_name") or "")
    if not tag:
        raise UpdateError("release has no version tag")
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
    page = str(data.get("html_url") or f"https://github.com/{repo}/releases/latest")
    return UpdateInfo(
        version=tag.lstrip("vV"),
        page_url=page,
        installer_name=installer["name"] if installer else None,
        installer_url=installer["browser_download_url"] if installer else None,
        installer_size=int(installer.get("size") or 0) if installer else 0,
        sums_url=sums["browser_download_url"] if sums else None,
    )


def check(repo: str, current: str, timeout: float = 15) -> UpdateInfo | None:
    """Returns info about a newer release, or None if this copy is up to date."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
        raise UpdateError("updates aren't set up for this copy")
    try:
        with _request(f"https://api.github.com/repos/{repo}/releases/latest", f"Satsuma/{current}",
                      timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # no releases published yet
        raise UpdateError(f"GitHub answered {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError("couldn't reach GitHub (are you online?)") from e
    except ValueError as e:
        raise UpdateError("unexpected answer from GitHub") from e
    info = parse_release(data, repo)
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
        raise UpdateError("this release has no installer")
    if not info.sums_url:
        raise UpdateError("this release has no checksum file, so it can't be verified")
    progress = progress or (lambda f: None)
    cancel = cancel or threading.Event()
    ua = f"Satsuma/{current}"

    with _request(info.sums_url, ua, timeout) as resp:
        want = expected_hash(resp.read(1_000_000).decode("utf-8", "replace"), info.installer_name)
    if not want:
        raise UpdateError("the installer isn't listed in the checksum file")

    dest = Path(tempfile.gettempdir()) / info.installer_name
    part = dest.with_suffix(".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with _request(info.installer_url, ua, timeout) as resp, open(part, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or info.installer_size or 0)
            while True:
                if cancel.is_set():
                    raise UpdateError("cancelled")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if total:
                    progress(done / total)
        if digest.hexdigest() != want:
            raise UpdateError("the download was damaged (checksum mismatch). Try again later")
        dest.unlink(missing_ok=True)
        part.rename(dest)
        return dest
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError("the download failed. Check your internet connection") from e
    finally:
        part.unlink(missing_ok=True)


def run_installer(path: Path):
    """Starts the installer in silent mode. It closes Satsuma, upgrades it and starts it again."""
    subprocess.Popen([str(path), "/SILENT", "/SP-", "/SUPPRESSMSGBOXES", "/NORESTART"],
                     close_fds=True)
