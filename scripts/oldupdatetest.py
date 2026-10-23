"""Checks that copies from before 1.7.0 (the app's old name) update themselves to a new Corefold build.

For every old release it installs the real old installer from GitHub, starts it the way that version
does, then runs that version's own updater code (taken from its git tag) against a release made of
the files in dist\\release, exactly as it would see the release on GitHub. Where the old updater can
take the release, it downloads, checks and starts the installer, and this checks that Corefold
replaced the old copy, kept its settings and is running. Also checks that Corefold's own updater
still picks its own installer from the same release.

Windows only. Run after building with -OldUpdaterCopy:
    python scripts/oldupdatetest.py dist/release 1.99.0 out
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import winreg
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from corefold import updater as new_updater  # noqa: E402
from corefold.core import LEGACY_NAME  # noqa: E402

RELEASE = Path(sys.argv[1]).resolve()
VERSION = sys.argv[2]
OUT = Path(sys.argv[3]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
REPO = os.environ.get("GITHUB_REPOSITORY", "SayedLabs/corefold")
TAG = "v" + VERSION
OLD_PKG = LEGACY_NAME.lower()
UNINSTALL_KEY = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{D08578D1-4B3E-404B-B56C-3FDF83BAFAE2}_is1"
PROGRAMS = Path(os.environ["LOCALAPPDATA"]) / "Programs"
ROAMING = Path(os.environ["APPDATA"])
LOCAL = Path(os.environ["LOCALAPPDATA"])
START_MENU = ROAMING / "Microsoft" / "Windows" / "Start Menu" / "Programs"
DESKTOP = Path(os.environ["USERPROFILE"]) / "Desktop"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
FIRST_BRIDGED = (1, 5, 3)  # older updaters only trust downloads under the repository name they were built with

lines: list[str] = []
failed = False


def say(text: str = ""):
    print(text, flush=True)
    lines.append(text)


def check(ok: bool, what: str) -> bool:
    global failed
    say(f"  {'ok  ' if ok else 'FAIL'} {what}")
    failed |= not ok
    return ok


# ---- a release on "GitHub" made of the files in dist\release

def fake_release() -> tuple[dict, dict[str, Path]]:
    files = {}
    assets = []
    for p in sorted(RELEASE.iterdir()):
        if not p.is_file():
            continue
        url = f"https://github.com/{REPO}/releases/download/{TAG}/{p.name}"
        files[url] = p
        assets.append({"name": p.name, "size": p.stat().st_size, "browser_download_url": url})
    data = {"tag_name": TAG, "html_url": f"https://github.com/{REPO}/releases/tag/{TAG}", "assets": assets}
    return data, files


class Answer(io.BytesIO):
    def __init__(self, body: bytes):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}


def serve(module, data: dict, files: dict[str, Path]):
    """Makes the updater module see `data` as the latest release and get its files from disk."""
    def request(url, user_agent, timeout):
        if url.startswith("https://api.github.com/repos/") and url.endswith("/releases/latest"):
            return Answer(json.dumps(data).encode())
        if url in files:
            return Answer(files[url].read_bytes())
        raise OSError(f"not in this release: {url}")
    module._request = request


# ---- processes, registry, files

def running() -> set[str]:
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return {line.split('","')[0].strip('"').lower() for line in out.splitlines() if line.startswith('"')}


def kill(*images: str):
    for image in images:
        subprocess.run(["taskkill", "/f", "/im", image], capture_output=True)


def reg_value(path: str, name: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def reg_delete(path: str, name: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except OSError:
        pass


def wait_for(test, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if test():
            return True
        time.sleep(1)
    return test()


checked_owners: set[str] = set()


def old_owner_free(repo: str):
    """Whether the GitHub account name an old build asks for can be taken by anyone. GitHub only
    sends old names on while nobody owns them, so whoever takes one decides what those builds see."""
    owner = repo.split("/")[0]
    if owner.lower() == REPO.split("/")[0].lower() or owner.lower() in checked_owners:
        return
    checked_owners.add(owner.lower())
    req = urllib.request.Request(f"https://api.github.com/users/{owner}", headers={
        "User-Agent": "oldupdatetest", "Accept": "application/vnd.github+json",
        **({"Authorization": f"Bearer {os.environ['GH_TOKEN']}"} if os.environ.get("GH_TOKEN") else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            who = json.loads(r.read().decode())
        say(f"  info the old account name {owner} now belongs to {who.get('login')} "
            f"({who.get('type')}, id {who.get('id')}, made {who.get('created_at')})")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            say(f"  info the old account name {owner} is free: anyone can take it")
        else:
            say(f"  info couldn't look up the old account name {owner} (GitHub answered {e.code})")
    except OSError as e:
        say(f"  info couldn't look up the old account name {owner} ({e})")


def baked_repo(exe: Path) -> str:
    """The repository an old build checks for updates (its _version.py, packed inside the exe)."""
    blob = exe.read_bytes()
    for m in re.finditer(rb"\x78[\x01\x5e\x9c\xda]", blob):
        try:
            module = zlib.decompressobj().decompress(blob[m.start():m.start() + 4096])
        except zlib.error:
            continue
        if b"UPDATE_REPO" in module and b"_version.py" in module and len(module) < 2000:
            # its strings, as Python stores them: a type byte, the length, the text
            for m2 in re.finditer(rb"[\x5a\x7a\xda\xfa]([\x03-\x7f])", module):
                text = module[m2.end():m2.end() + m2.group(1)[0]]
                if re.fullmatch(rb"[\w.-]+/[\w.-]+", text):
                    return text.decode()
    return ""


def clean_up():
    kill("Corefold.exe", f"{LEGACY_NAME}.exe")
    loc = reg_value(UNINSTALL_KEY, "InstallLocation")
    if loc and (Path(loc) / "unins000.exe").exists():
        subprocess.run([str(Path(loc) / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        wait_for(lambda: not (Path(loc) / "unins000.exe").exists(), 60)
    for base in (ROAMING, LOCAL, PROGRAMS):
        for name in ("Corefold", LEGACY_NAME):
            shutil.rmtree(base / name, ignore_errors=True)
    for name in ("Corefold", LEGACY_NAME):
        reg_delete(RUN_KEY, name)
        for lnk in (START_MENU / f"{name}.lnk", DESKTOP / f"{name}.lnk"):
            lnk.unlink(missing_ok=True)


def load_old_updater(tag: str):
    src = OUT / "old-source" / tag
    if not (src / OLD_PKG).is_dir():
        src.mkdir(parents=True, exist_ok=True)
        tar = subprocess.run(["git", "archive", tag, OLD_PKG], cwd=ROOT, capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(src)], input=tar, check=True)
    for name in [n for n in sys.modules if n == OLD_PKG or n.startswith(OLD_PKG + ".")]:
        del sys.modules[name]
    sys.path.insert(0, str(src))
    try:
        __import__(f"{OLD_PKG}.updater")
    finally:
        sys.path.remove(str(src))
    return sys.modules[f"{OLD_PKG}.updater"]


# ---- the checks

def test_new_updater(data, files):
    say("== Corefold's own updater on the same release")
    serve(new_updater, data, files)
    info = new_updater.check(REPO, "1.7.6")
    if check(bool(info) and info.installer_name == f"Corefold-Setup-{VERSION}.exe",
             f"picks Corefold-Setup-{VERSION}.exe (got {info and info.installer_name})"):
        path = new_updater.download(info, "1.7.6")
        check(path.read_bytes() == (RELEASE / info.installer_name).read_bytes(), "download checks out against SHA256SUMS.txt")
        path.unlink(missing_ok=True)
    sums = (RELEASE / "SHA256SUMS.txt").read_text()
    copy = RELEASE / f"{LEGACY_NAME}-Setup-{VERSION}.exe"
    check(copy.exists() and copy.read_bytes() == (RELEASE / f"Corefold-Setup-{VERSION}.exe").read_bytes(),
          "the copy for old versions is the same installer, byte for byte")
    check(new_updater.expected_hash(sums, copy.name) == hashlib.sha256(copy.read_bytes()).hexdigest(),
          "the copy is listed in SHA256SUMS.txt")


def test_old(tag: str, data, files):
    version = tag.lstrip("v")
    nums = new_updater.parse_version(version)
    say(f"== {tag}")
    clean_up()

    # the real installer of that version, from its release
    setup = OUT / "old-setups" / f"{LEGACY_NAME}-Setup-{version}.exe"
    setup.parent.mkdir(parents=True, exist_ok=True)
    if not setup.exists():
        urllib.request.urlretrieve(f"https://github.com/{REPO}/releases/download/{tag}/{setup.name}", setup)
    old_conf = ROAMING / LEGACY_NAME
    old_conf.mkdir(parents=True, exist_ok=True)
    (old_conf / "settings.json").write_text(json.dumps({"language": "ar", "first_run": False, "tour_done": True}))
    subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TASKS=desktopicon,startup"],
                   check=True)
    old_exe = PROGRAMS / LEGACY_NAME / f"{LEGACY_NAME}.exe"
    if not check(old_exe.exists(), f"old version installed ({old_exe})"):
        return
    # a silent install starts it in the tray, like after one of its own updates
    check(wait_for(lambda: f"{LEGACY_NAME}.exe".lower() in running(), 30), "old version is running")
    time.sleep(8)  # let it start up properly

    repo = baked_repo(old_exe)
    if not check(bool(repo), f"its updater asks github.com/{repo or '?'}"):
        return
    old_owner_free(repo)
    updater = load_old_updater(tag)

    # does GitHub still answer under the name this build asks for? (renamed repositories redirect)
    try:
        with updater._request(f"https://api.github.com/repos/{repo}/releases/latest", f"{LEGACY_NAME}/{version}", 20) as r:
            live = json.loads(r.read().decode())
        check(str(live.get("html_url", "")).lower().startswith(f"https://github.com/{REPO}/".lower()),
              f"GitHub sends its update check on to {REPO} (latest there: {live.get('tag_name')})")
    except Exception as e:  # rate limits on shared runners: not this test's question
        say(f"  info couldn't ask GitHub directly ({e})")

    serve(updater, data, files)
    info = updater.check(repo or REPO, version)
    if nums < FIRST_BRIDGED:
        # these trust downloads only under the name they were built with, which no longer exists
        if info and info.installer_url:
            say(f"  info picks {info.installer_name} after all (not expected; it could update itself)")
        else:
            say("  info can't update itself (it only trusts the repository's old name): download Corefold by hand once")
        clean_up()
        return
    if not check(bool(info) and info.installer_name == f"{LEGACY_NAME}-Setup-{VERSION}.exe",
                 f"sees {VERSION} and picks {info and info.installer_name}"):
        clean_up()
        return
    path = updater.download(info, version)
    check(path.exists(), "downloads it and it matches SHA256SUMS.txt")
    updater.run_installer(path)  # the same command the old app runs, then it quits

    stem = path.stem.lower()
    check(wait_for(lambda: not any(p.startswith(stem) for p in running()), 300), "installer finished")
    new_dir = PROGRAMS / "Corefold"
    check((new_dir / "Corefold.exe").exists(), f"Corefold installed in {new_dir}")
    check(wait_for(lambda: "corefold.exe" in running(), 60), "Corefold is running")
    check(f"{LEGACY_NAME}.exe".lower() not in running(), "old version is closed")
    check(not old_exe.exists(), "old program folder removed")
    key = UNINSTALL_KEY
    check(reg_value(key, "DisplayName") == "Corefold" and reg_value(key, "DisplayVersion") == VERSION,
          f"Apps list shows {reg_value(key, 'DisplayName')} {reg_value(key, 'DisplayVersion')}")
    check(bool(reg_value(RUN_KEY, "Corefold")) and reg_value(RUN_KEY, LEGACY_NAME) is None,
          "starts with Windows as Corefold (old entry gone)")
    check((START_MENU / "Corefold.lnk").exists() and not (START_MENU / f"{LEGACY_NAME}.lnk").exists(),
          "Start menu shortcut renamed")
    check(not (DESKTOP / f"{LEGACY_NAME}.lnk").exists(), "old desktop shortcut removed")
    new_conf = ROAMING / "Corefold" / "settings.json"
    check(wait_for(new_conf.exists, 60), "settings moved to the Corefold folder")
    try:
        lang = json.loads(new_conf.read_text("utf-8")).get("language")
    except (OSError, ValueError):
        lang = None
    check(lang == "ar", f"settings kept (language: {lang})")
    # the installer the old version downloaded (about 100 MB) is cleared like Corefold's own
    check(path.exists(), f"the downloaded installer waits in {path.parent} (Corefold clears it after a day)")
    new_updater.clean_old_installers(keep_days=0)
    check(not path.parent.exists(), "Corefold clears it and its folder")
    clean_up()


def main():
    data, files = fake_release()
    say(f"Release {TAG}: " + ", ".join(a["name"] for a in data["assets"]))
    test_new_updater(data, files)
    tags = subprocess.run(["git", "tag"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    old = sorted((t for t in tags if (1, 2) <= new_updater.parse_version(t) < (1, 7)), key=new_updater.parse_version)
    for tag in old:
        try:
            test_old(tag, data, files)
        except Exception as e:
            check(False, f"{tag}: {type(e).__name__}: {e}")
            clean_up()
    say()
    say("FAILED" if failed else "ALL GOOD")
    (OUT / "oldupdate.txt").write_text("\n".join(lines) + "\n", "utf-8")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
