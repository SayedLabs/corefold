"""Corefold Uninstaller: the extensions added to the browsers (Chrome, Edge, Brave, Vivaldi, Opera
and Firefox), read from their profile folders.

Nothing is deleted here: a browser puts back (or marks as broken) an extension removed behind its
back, so each one is removed from the browser's own extensions page, which Corefold opens on it."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Chromium browsers: (name, program file, profiles folder under LOCALAPPDATA, its address for the extensions page)
CHROMIUM = [
    ("Google Chrome", "chrome.exe", r"Google\Chrome\User Data", "chrome"),
    ("Microsoft Edge", "msedge.exe", r"Microsoft\Edge\User Data", "edge"),
    ("Brave", "brave.exe", r"BraveSoftware\Brave-Browser\User Data", "brave"),
    ("Vivaldi", "vivaldi.exe", r"Vivaldi\User Data", "vivaldi"),
]
OPERA = ("Opera", "opera.exe", r"Opera Software\Opera Stable", "opera")  # under APPDATA, one profile
FIREFOX = ("Firefox", "firefox.exe", r"Mozilla\Firefox\Profiles")      # under APPDATA
# where the program usually is, when Windows' list of programs doesn't say
KNOWN_EXE = {
    "chrome.exe": [r"%ProgramFiles%\Google\Chrome\Application", r"%ProgramFiles(x86)%\Google\Chrome\Application",
                   r"%LOCALAPPDATA%\Google\Chrome\Application"],
    "msedge.exe": [r"%ProgramFiles(x86)%\Microsoft\Edge\Application", r"%ProgramFiles%\Microsoft\Edge\Application"],
    "brave.exe": [r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application",
                  r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application"],
    "vivaldi.exe": [r"%LOCALAPPDATA%\Vivaldi\Application", r"%ProgramFiles%\Vivaldi\Application"],
    "opera.exe": [r"%LOCALAPPDATA%\Programs\Opera"],
    "firefox.exe": [r"%ProgramFiles%\Mozilla Firefox", r"%ProgramFiles(x86)%\Mozilla Firefox"],
}
ALL_SITES = {"<all_urls>", "*://*/*", "http://*/*", "https://*/*", "*://*/"}
_ID = re.compile(r"^[a-p]{32}$")


@dataclass
class Extension:
    browser: str          # "Google Chrome"
    exe: str              # "chrome.exe"
    profile: str          # the profile's name as the browser shows it ("Person 1", "Default")
    profile_dir: str      # its folder's name ("Default", "Profile 2")
    id: str
    name: str
    version: str = ""
    size: int = 0
    path: str = ""
    all_sites: bool = False   # it can see and change every website
    theme: bool = False
    on: bool = True
    scheme: str = ""          # "chrome" -> chrome://extensions ("" for Firefox)


def _read_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _tree_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _version_key(name: str):
    return [int(p) if p.isdigit() else 0 for p in re.split(r"[._]", name)]


def _message(folder: Path, manifest: dict, text: str) -> str:
    """ "__MSG_appName__" read from the extension's own translations (its default language)."""
    m = re.fullmatch(r"__MSG_(\w+)__", text or "")
    if not m:
        return text or ""
    key = m.group(1).lower()
    for lang in (manifest.get("default_locale") or "en", "en", "en_US"):
        messages = _read_json(folder / "_locales" / lang / "messages.json")
        for k, v in messages.items():
            if k.lower() == key and isinstance(v, dict) and v.get("message"):
                return str(v["message"])
    return ""


def _sees_all_sites(manifest: dict) -> bool:
    asked = list(manifest.get("permissions") or []) + list(manifest.get("host_permissions") or [])
    for script in manifest.get("content_scripts") or []:
        if isinstance(script, dict):
            asked += list(script.get("matches") or [])
    return any(isinstance(p, str) and p in ALL_SITES for p in asked)


def _profiles(user_data: Path) -> list[tuple[str, str]]:
    """(folder, name) of each profile of a Chromium browser."""
    info = (_read_json(user_data / "Local State").get("profile") or {}).get("info_cache") or {}
    found = []
    try:
        entries = sorted(os.scandir(user_data), key=lambda e: e.name)
    except OSError:
        return found
    for e in entries:
        if e.is_dir() and (e.name == "Default" or e.name.startswith("Profile ")) and \
                os.path.isdir(os.path.join(e.path, "Extensions")):
            name = (info.get(e.name) or {}).get("name") or e.name
            found.append((e.name, str(name)))
    return found


def _chromium_profile(browser, exe, scheme, folder: Path, profile_dir: str, profile: str) -> list[Extension]:
    prefs = {}
    for f in ("Secure Preferences", "Preferences"):  # what's turned off, and what came with the browser
        prefs.update((_read_json(folder / f).get("extensions") or {}).get("settings") or {})
    found = []
    try:
        ids = [e for e in os.scandir(folder / "Extensions") if e.is_dir() and _ID.match(e.name)]
    except OSError:
        return found
    for e in ids:
        state = prefs.get(e.name) or {}
        if state.get("location") in (5, 10):  # parts of the browser itself
            continue
        try:
            versions = sorted((v.name for v in os.scandir(e.path) if v.is_dir()), key=_version_key)
        except OSError:
            continue
        if not versions:
            continue
        top = Path(e.path) / versions[-1]
        manifest = _read_json(top / "manifest.json")
        name = _message(top, manifest, manifest.get("name", ""))
        if not name:
            continue
        on = not state.get("disable_reasons") if "disable_reasons" in state else state.get("state", 1) != 0
        found.append(Extension(browser, exe, profile, profile_dir, e.name, name, str(manifest.get("version", "")),
                               _tree_size(Path(e.path)), e.path, _sees_all_sites(manifest), "theme" in manifest,
                               bool(on), scheme))
    return found


def _firefox(root: Path) -> list[Extension]:
    found = []
    try:
        profiles = sorted((e for e in os.scandir(root) if e.is_dir()), key=lambda e: e.name)
    except OSError:
        return found
    for p in profiles:
        data = _read_json(Path(p.path) / "extensions.json")
        profile = p.name.split(".", 1)[-1]
        for a in data.get("addons") or []:
            if not isinstance(a, dict) or a.get("location") != "app-profile" or \
                    a.get("type") not in ("extension", "theme"):
                continue
            name = (a.get("defaultLocale") or {}).get("name") or a.get("id") or ""
            path = a.get("path") or ""
            try:
                size = os.path.getsize(path) if path and os.path.isfile(path) else 0
            except OSError:
                size = 0
            origins = (a.get("userPermissions") or {}).get("origins") or []
            found.append(Extension(FIREFOX[0], FIREFOX[1], profile, p.name, str(a.get("id", "")), str(name),
                                   str(a.get("version", "")), size, path, any(o in ALL_SITES for o in origins),
                                   a.get("type") == "theme", bool(a.get("active", True))))
    return found


def find(local: Path | None = None, roaming: Path | None = None) -> list[Extension]:
    """Every extension of every browser and profile on this PC (for this Windows user)."""
    local = local or (Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else None)
    roaming = roaming or (Path(os.environ["APPDATA"]) if os.environ.get("APPDATA") else None)
    found: list[Extension] = []
    if local is not None:
        for browser, exe, base, scheme in CHROMIUM:
            user_data = local.joinpath(*base.split("\\"))
            for folder, name in _profiles(user_data):
                found += _chromium_profile(browser, exe, scheme, user_data / folder, folder, name)
    if roaming is not None:
        browser, exe, base, scheme = OPERA
        folder = roaming.joinpath(*base.split("\\"))
        if (folder / "Extensions").is_dir():
            found += _chromium_profile(browser, exe, scheme, folder, "", "")
        found += _firefox(roaming.joinpath(*FIREFOX[2].split("\\")))
    found.sort(key=lambda x: (x.browser, x.profile, x.name.lower()))
    return found


def browser_exe(exe: str) -> str:
    """Where a browser's program file is, from Windows' list of programs ("" when it isn't found)."""
    if os.name == "nt":
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                    path = str(winreg.QueryValueEx(k, "")[0]).strip().strip('"')
                if os.path.isfile(path):
                    return path
            except OSError:
                pass
    for folder in KNOWN_EXE.get(exe, []):
        path = os.path.join(os.path.expandvars(folder), exe)
        if "%" not in path and os.path.isfile(path):
            return path
    return ""


def page_command(ext: Extension, exe_path: str) -> list[str]:
    """The browser opened on this extension in its extensions page (where it's removed)."""
    if not ext.scheme:  # Firefox
        return [exe_path, "about:addons"]
    cmd = [exe_path]
    if ext.profile_dir:
        cmd.append(f"--profile-directory={ext.profile_dir}")
    return cmd + [f"{ext.scheme}://extensions/?id={ext.id}"]


def open_page(ext: Extension) -> bool:
    exe = browser_exe(ext.exe)
    if not exe:
        return False
    try:
        subprocess.Popen(page_command(ext, exe), close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        return True
    except OSError:
        return False
