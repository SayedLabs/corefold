"""Corefold Startup: what starts with Windows, and switching it off or on again.

Programs start with Windows from two places: a "Run" key in the registry (this user's, or the whole
PC's) and a Startup folder (this user's, or everyone's). Switching one off works exactly the way Task
Manager does it: Windows keeps a note for each entry under "StartupApproved", and an entry with a
"switched off" note there is skipped when Windows starts. The entry itself is never changed or
deleted, so switching it back on (here or in Task Manager) is always possible, and both always agree.

Entries for the whole PC (HKLM, everyone's Startup folder) need an administrator to change: Windows
asks first (its usual prompt), and nothing changes if the answer is no.

Apps from the Microsoft Store start with Windows another way: a "startup task" in the app's own
description (its AppxManifest.xml), which Windows switches on or off with a number kept for this user
under AppModel\\SystemAppData (2 is on, 1 is "switched off by the person"). That is what Task Manager
changes too, so Store apps are switched the same way, and need no administrator.

The one thing ever removed is a leftover: a line whose program was deleted from the PC, and only when
the person asks (a copy of each one is kept in startup-removed.json first)."""
from __future__ import annotations

import json
import logging
import os
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

from .core import APP_NAME

log = logging.getLogger("corefold")

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APPMODEL = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel"
STORE_PACKAGES = _APPMODEL + r"\Repository\Packages"   # this user's installed packages, with their folders
STORE_STATES = _APPMODEL + r"\SystemAppData"           # <family>\<task id>: "State", a DWORD
# a startup task's State: 0 off (as the app was made), 1 switched off by the person, 2 on, 3 off by the
# organisation's rules, 4 on by those rules (the last two can't be changed here or in Task Manager)
TASK_OFF, TASK_ON, TASK_BY_POLICY = 1, 2, (3, 4)
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
SEEN_FILE = "startup-seen.json"
REMOVED_FILE = "startup-removed.json"
# (Windows' own note: an odd first byte is "switched off", then the time it was, as a FILETIME)
ON_BYTES = bytes([2]) + bytes(11)
EPOCH_AS_FILETIME = 116444736000000000
# programs that only run something else: their own name ("Windows host process") says nothing, so
# the entry's name is shown instead
HOSTS = {"rundll32.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "wscript.exe", "cscript.exe", "mshta.exe",
         "conhost.exe", "explorer.exe", "regsvr32.exe", "javaw.exe", "java.exe", "pythonw.exe", "python.exe",
         "py.exe", "pyw.exe", "msiexec.exe", "schtasks.exe", "start.exe"}


@dataclass
class Entry:
    id: str               # "HKCU-Run|Name", unique and the same every time
    name: str             # the program's own name, or the entry's
    value: str            # the entry's name (a registry value, or a file in a Startup folder)
    command: str          # what Windows runs
    exe: str              # the program (may be "" or gone)
    where: str            # "HKCU-Run", "HKLM-Run", "HKLM-Run32", "User-Folder", "Common-Folder"
    on: bool = True
    off_since: float = 0  # when it was switched off (0 if unknown or on)
    publisher: str = ""
    missing: bool = False  # its program isn't there any more
    own: bool = False      # Corefold itself
    new: bool = False      # added since this page was last looked at
    extra: dict = field(default_factory=dict)

    @property
    def admin(self) -> bool:
        """Belongs to the whole PC: an administrator has to change it."""
        return not self.where.startswith(("HKCU", "User", "Store"))

    @property
    def locked(self) -> bool:
        """The organisation's rules decide (a Store app's task): it can't be switched here."""
        return self.extra.get("state") in TASK_BY_POLICY


# where each kind of entry lives, and where its on/off note goes:
# where -> (hive, entry's key or folder, registry view, the note's key)
PLACES = {
    "HKCU-Run": ("HKCU", RUN, 0, APPROVED + r"\Run"),
    "HKLM-Run": ("HKLM", RUN, 64, APPROVED + r"\Run"),
    "HKLM-Run32": ("HKLM", RUN, 32, APPROVED + r"\Run32"),
    "User-Folder": ("HKCU", "", 0, APPROVED + r"\StartupFolder"),
    "Common-Folder": ("HKLM", "", 0, APPROVED + r"\StartupFolder"),
}


def _winreg():
    import winreg
    return winreg


def _root(hive: str):
    w = _winreg()
    return w.HKEY_CURRENT_USER if hive == "HKCU" else w.HKEY_LOCAL_MACHINE


def _access(view: int, write: bool = False) -> int:
    w = _winreg()
    flags = (w.KEY_QUERY_VALUE | w.KEY_SET_VALUE) if write else w.KEY_READ
    if view == 64:
        flags |= 0x0100   # KEY_WOW64_64KEY
    elif view == 32:
        flags |= 0x0200   # KEY_WOW64_32KEY
    return flags


def _values(hive: str, path: str, view: int) -> dict[str, object]:
    w = _winreg()
    out: dict[str, object] = {}
    try:
        with w.OpenKey(_root(hive), path, 0, _access(view)) as k:
            i = 0
            while True:
                try:
                    name, value, _kind = w.EnumValue(k, i)
                except OSError:
                    break
                out[name] = value
                i += 1
    except OSError:
        pass
    return out


def folders() -> dict[str, Path]:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    common = os.environ.get("ProgramData") or r"C:\ProgramData"
    tail = Path("Microsoft") / "Windows" / "Start Menu" / "Programs"
    return {"User-Folder": Path(appdata) / tail / "Startup", "Common-Folder": Path(common) / tail / "StartUp"}


# ----------------------------------------------------------------------------- on/off notes

def note_is_off(note) -> bool:
    """Windows' note for an entry: no note, or an even first byte, is on."""
    return isinstance(note, (bytes, bytearray)) and len(note) >= 1 and bool(note[0] & 1)


def note_time(note) -> float:
    """When it was switched off (seconds since 1970), 0 if the note doesn't say."""
    if not isinstance(note, (bytes, bytearray)) or len(note) < 12:
        return 0
    ft = struct.unpack_from("<Q", bytes(note), 4)[0]
    if not ft:
        return 0
    return max(0.0, (ft - EPOCH_AS_FILETIME) / 10_000_000)


def off_note(when: float | None = None) -> bytes:
    """The note Task Manager writes to switch an entry off: 3, three zero bytes, then the time."""
    when = time.time() if when is None else when
    return bytes([3, 0, 0, 0]) + struct.pack("<Q", int(when * 10_000_000) + EPOCH_AS_FILETIME)


# ----------------------------------------------------------------------------- the list

def split_command(cmd: str) -> tuple[str, str]:
    from .uninstall import split_command as split
    return split(cmd)


def _program_name(exe: str) -> tuple[str, str]:
    """(the program's own name, its publisher) from the file, both "" if it doesn't say."""
    if os.name != "nt" or not exe or not os.path.isfile(exe):
        return "", ""
    import ctypes
    out = {"FileDescription": "", "CompanyName": ""}
    try:
        v = ctypes.windll.version
        size = v.GetFileVersionInfoSizeW(exe, None)
        if not size:
            return "", ""
        data = ctypes.create_string_buffer(size)
        if not v.GetFileVersionInfoW(exe, 0, size, data):
            return "", ""
        ptr, length = ctypes.c_void_p(), ctypes.c_uint()
        langs = ["040904b0", "040904e4"]
        if v.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)) \
                and length.value >= 4:
            lang, cp = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort * 2)).contents
            langs.insert(0, f"{lang:04x}{cp:04x}")
        for key in out:
            for lang in langs:
                if v.VerQueryValueW(data, f"\\StringFileInfo\\{lang}\\{key}", ctypes.byref(ptr),
                                    ctypes.byref(length)) and length.value:
                    text = ctypes.wstring_at(ptr, length.value).strip("\0 ").strip()
                    if text:
                        out[key] = text
                        break
    except Exception:
        log.debug("version info of %s", exe, exc_info=True)
    name = out["FileDescription"]
    if len(name) > 60:
        name = ""
    return name, out["CompanyName"][:80]


def shortcut_target(path: Path) -> tuple[str, str]:
    """(program, its arguments) a .lnk points at; ("", "") if it can't be read."""
    if os.name != "nt":
        return "", ""
    try:
        from PySide6.QtCore import QFileInfo
        info = QFileInfo(str(path))
        target = info.symLinkTarget() if info.isShortcut() else ""
        return (os.path.normpath(target) if target else ""), ""
    except Exception:
        return "", ""


def _full_path(exe: str) -> str:
    """ "rundll32.exe" -> its place in System32 (programs Windows finds by name)."""
    if not exe or os.path.isabs(exe):
        return exe
    system = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    for folder in (system / "System32", system):
        candidate = folder / exe
        if candidate.suffix == "":
            candidate = candidate.with_suffix(".exe")
        if candidate.is_file():
            return str(candidate)
    return exe


def _make(where: str, value: str, command: str, exe: str, notes: dict) -> Entry:
    note = notes.get(value)
    e = Entry(id=f"{where}|{value}", name=value, value=value, command=command, exe=exe, where=where,
              on=not note_is_off(note), off_since=note_time(note) if note_is_off(note) else 0)
    if exe and os.path.isabs(exe):
        e.missing = is_gone(exe)
        name, e.publisher = _program_name(exe)
        if os.path.basename(exe).lower() in HOSTS:
            name = ""   # (and the publisher is the host's, so it's left out too)
            e.publisher = ""
        if name:
            e.name = name
    if where.endswith("Folder") and e.name == value:
        e.name = Path(value).stem
    return e


def is_gone(exe: str) -> bool:
    """The program really isn't on this PC: its drive is there but the file isn't. (A program on a
    drive that isn't plugged in, or on another computer in the network, may well come back.)"""
    if not exe or os.path.exists(exe):
        return False
    drive = os.path.splitdrive(exe)[0]
    if not drive or drive.startswith(("\\\\", "//")):
        return False
    return os.path.isdir(drive + os.sep)


def list_entries() -> list[Entry]:
    """Every program that starts with Windows from the Run keys and the Startup folders, Corefold
    first and then by name (on and off alike)."""
    out: list[Entry] = []
    for where in ("HKCU-Run", "HKLM-Run", "HKLM-Run32"):
        hive, key, view, approved = PLACES[where]
        if where == "HKLM-Run32" and not _is_64bit():
            continue
        notes = _values(hive, approved, 64 if hive == "HKLM" else 0)
        for value, command in _values(hive, key, view).items():
            if not value or not isinstance(command, str) or not command.strip():
                continue  # (the default value, or not a command)
            e = _make(where, value, command, _full_path(split_command(command)[0]), notes)
            e.own = where == "HKCU-Run" and value == APP_NAME
            out.append(e)
    for where, folder in folders().items():
        hive, _, _, approved = PLACES[where]
        notes = _values(hive, approved, 64 if hive == "HKLM" else 0)
        try:
            files = sorted(p for p in folder.iterdir() if p.is_file())
        except OSError:
            continue
        for p in files:
            if p.name.lower() == "desktop.ini":
                continue
            exe = str(p)
            command = str(p)
            if p.suffix.lower() == ".lnk":
                target, _ = shortcut_target(p)
                exe = target or ""
                command = target or str(p)
            e = _make(where, p.name, command, exe, notes)
            e.extra["file"] = str(p)
            out.append(e)
    try:
        out += store_entries()
    except Exception:
        log.warning("couldn't read the Store apps' startup tasks", exc_info=True)
    out.sort(key=lambda e: (not e.own, e.name.lower()))
    return out


# ----------------------------------------------------------------------------- Store apps

def _subkeys(hive: str, path: str) -> list[str]:
    w = _winreg()
    out = []
    try:
        with w.OpenKey(_root(hive), path, 0, w.KEY_READ) as k:
            i = 0
            while True:
                try:
                    out.append(w.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except OSError:
        pass
    return out


def family_of(full_name: str) -> str:
    """ "Name_1.2.3.0_x64__8wekyb3d8bbwe" -> "Name_8wekyb3d8bbwe" (how Windows names an app's own data)."""
    parts = full_name.split("_")
    return f"{parts[0]}_{parts[-1]}" if len(parts) >= 5 else full_name


def resolve_text(text: str, full_name: str = "", name: str = "") -> str:
    """An app's name as written in its package: plain, "@{…}", or "ms-resource:…" (looked up in the app's
    own resources, in the language Windows uses). "" when it can't be read."""
    text = (text or "").strip()
    if not text:
        return ""
    if text.lower().startswith("ms-resource:") and full_name:
        rest = text[len("ms-resource:"):]
        if rest.startswith("//"):
            uri = "ms-resource:" + rest
        elif rest.startswith("/"):
            uri = f"ms-resource://{name}{rest}"
        else:
            uri = f"ms-resource://{name}/Resources/{rest}"
        text = f"@{{{full_name}?{uri}}}"
    if not text.startswith("@"):
        return text
    if os.name != "nt":
        return ""
    import ctypes
    buf = ctypes.create_unicode_buffer(512)
    try:
        if ctypes.windll.shlwapi.SHLoadIndirectString(text, buf, len(buf), None) == 0:
            return buf.value.strip()
    except Exception:
        pass
    return ""


def _startup_tasks(manifest: Path) -> tuple[list[dict], dict]:
    """The startup tasks an app's AppxManifest.xml declares, and what it says about the app itself."""
    try:
        raw = manifest.read_bytes()
    except OSError:
        return [], {}
    if b"startupTask" not in raw and b"StartupTask" not in raw:
        return [], {}  # (most apps have none: they aren't even parsed)
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return [], {}

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]
    about = {}
    for el in root:
        if local(el.tag) == "Identity":
            about["name"] = el.get("Name", "")
        elif local(el.tag) == "Properties":
            for prop in el:
                if local(prop.tag) in ("DisplayName", "PublisherDisplayName", "Logo") and prop.text:
                    about[local(prop.tag)] = prop.text.strip()
    tasks = []
    for ext in root.iter():
        if local(ext.tag) != "Extension" or ext.get("Category", "").lower() != "windows.startuptask":
            continue
        for task in ext:
            if local(task.tag) == "StartupTask" and task.get("TaskId"):
                tasks.append({"id": task.get("TaskId"), "enabled": task.get("Enabled", "").lower() == "true",
                              "name": task.get("DisplayName", ""), "exe": ext.get("Executable", "")})
    return tasks, about


def _logo(root: Path, logo: str) -> str:
    """The app's logo file: Windows keeps it at several sizes ("StoreLogo.scale-100.png")."""
    if not logo:
        return ""
    p = root / logo
    if p.is_file():
        return str(p)
    try:
        found = sorted(p.parent.glob(p.stem + ".*" + p.suffix))
    except (OSError, ValueError):
        return ""
    return str(found[0]) if found else ""


def store_entries() -> list[Entry]:
    """The Store apps that can start with Windows, each on or off as Windows keeps it for this user."""
    out, seen = [], set()
    for full in sorted(_subkeys("HKCU", STORE_PACKAGES), reverse=True):  # (two versions of one app: the newer)
        info = _values("HKCU", STORE_PACKAGES + "\\" + full, 0)
        root = Path(str(info.get("PackageRootFolder") or ""))
        if not str(root) or str(root) == ".":
            continue
        tasks, about = _startup_tasks(root / "AppxManifest.xml")
        if not tasks:
            continue
        family = family_of(full)
        name = about.get("name") or full.split("_")[0]
        app_name = resolve_text(str(info.get("DisplayName") or ""), full, name) or \
            resolve_text(about.get("DisplayName", ""), full, name) or name
        publisher = resolve_text(about.get("PublisherDisplayName", ""), full, name)
        logo = _logo(root, about.get("Logo", ""))
        for t in tasks:
            state = _values("HKCU", f"{STORE_STATES}\\{family}\\{t['id']}", 0).get("State")
            state = state if isinstance(state, int) else None
            on = state in (TASK_ON, 4) if state is not None else t["enabled"]
            exe = str(root / t["exe"]) if t["exe"] else ""
            label = resolve_text(t["name"], full, name)
            if f"{family}|{t['id']}" in seen:
                continue
            seen.add(f"{family}|{t['id']}")
            e = Entry(id=f"Store-App|{family}|{t['id']}", name=label or app_name, value=t["id"],
                      command=exe or full, exe=exe, where="Store-App", on=on, publisher=publisher,
                      extra={"family": family, "package": full, "state": state, "icon": logo})
            out.append(e)
    return out


def _set_task(entry: Entry, on: bool) -> bool:
    if entry.locked:
        return False
    w = _winreg()
    key = f"{STORE_STATES}\\{entry.extra['family']}\\{entry.value}"
    with w.CreateKeyEx(w.HKEY_CURRENT_USER, key, 0, w.KEY_QUERY_VALUE | w.KEY_SET_VALUE) as k:
        w.SetValueEx(k, "State", 0, w.REG_DWORD, TASK_ON if on else TASK_OFF)
    ok = _values("HKCU", key, 0).get("State") == (TASK_ON if on else TASK_OFF)
    if ok:
        entry.on = on
        entry.extra["state"] = TASK_ON if on else TASK_OFF
        entry.off_since = 0 if on else time.time()
    return ok


def _is_64bit() -> bool:
    return os.environ.get("PROCESSOR_ARCHITECTURE", "").endswith("64") or "PROCESSOR_ARCHITEW6432" in os.environ


# ----------------------------------------------------------------------------- "new since you last looked"

def _seen_path() -> Path:
    from .settings import CONFIG_DIR
    return CONFIG_DIR / SEEN_FILE


def mark_new(entries: list[Entry]) -> int:
    """Marks the entries that weren't there the last time this list was looked at, and remembers this
    list for next time. The very first look marks nothing (there's nothing to compare with)."""
    path = _seen_path()
    try:
        before = set(json.loads(path.read_text(encoding="utf-8")).get("ids", []))
        first = False
    except (OSError, ValueError, AttributeError):
        before, first = set(), True
    count = 0
    for e in entries:
        e.new = not first and e.id not in before
        count += e.new
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ids": sorted(e.id for e in entries)}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        log.debug("couldn't remember the startup list", exc_info=True)
    return count


# ----------------------------------------------------------------------------- switching on and off

class NeedsAdmin(Exception):
    """Windows only lets an administrator change this entry."""


def read_note(entry: Entry):
    hive, _, _, approved = PLACES[entry.where]
    return _values(hive, approved, 64 if hive == "HKLM" else 0).get(entry.value)


def set_on(entry: Entry, on: bool, ask_admin: bool = True) -> bool:
    """Switches an entry on or off the way Task Manager does. Only its note under StartupApproved
    changes; the entry itself is never touched. Returns True when the change is made. An entry for
    the whole PC asks Windows for an administrator first (False if that's refused)."""
    if entry.where == "Store-App":
        return _set_task(entry, on)
    note = ON_BYTES if on else off_note()
    hive, _, _, approved = PLACES[entry.where]
    try:
        _write_note(hive, approved, entry.value, note)
    except PermissionError:
        if not ask_admin:
            raise NeedsAdmin(entry.value)
        if not _write_note_as_admin(hive, approved, entry.value, note):
            return False
    ok = note_is_off(read_note(entry)) != on
    if ok:
        entry.on = on
        entry.off_since = 0 if on else time.time()
    return ok


def _write_note(hive: str, key: str, value: str, note: bytes):
    w = _winreg()
    view = 64 if hive == "HKLM" else 0
    with w.CreateKeyEx(_root(hive), key, 0, _access(view, write=True)) as k:
        w.SetValueEx(k, value, 0, w.REG_BINARY, note)


def _write_note_as_admin(hive: str, key: str, value: str, note: bytes) -> bool:
    """reg.exe, started as administrator (Windows asks). False if it was refused or failed."""
    if not plain_name(value) or '"' in value or value.endswith("\\"):
        return False  # (a name reg.exe can't be given safely)
    from .uninstall import shell_run, wait_process
    reg = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "reg.exe"
    args = f'add "{hive}\\{key}" /v "{value}" /t REG_BINARY /d {note.hex()} /f /reg:64'
    handle = shell_run(str(reg), args, admin=True, show=0)
    if handle is None:
        return False  # (the person said no)
    code = wait_process(handle, timeout_s=30)
    return code == 0 or (code is None and handle == 0)


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ----------------------------------------------------------------------------- leftovers of removed programs

def leftovers(entries: list[Entry]) -> list[Entry]:
    """The lines whose program was removed from this PC (they can't run, and do nothing)."""
    return [e for e in entries if e.missing and not e.own]


def _keep_copy(gone: list[Entry]):
    """A copy of each line before it's removed (where, name, what it ran, Windows' on/off note)."""
    from .settings import CONFIG_DIR
    path = CONFIG_DIR / REMOVED_FILE
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
        kept = kept if isinstance(kept, list) else []
    except (OSError, ValueError):
        kept = []
    for e in gone:
        note = read_note(e)
        kept.append({"where": e.where, "value": e.value, "command": e.command, "file": e.extra.get("file", ""),
                     "note": note.hex() if isinstance(note, (bytes, bytearray)) else "", "removed": time.time()})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(kept[-500:], indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _delete_value(hive: str, key: str, view: int, value: str):
    w = _winreg()
    try:
        with w.OpenKey(_root(hive), key, 0, _access(view, write=True)) as k:
            w.DeleteValue(k, value)
    except FileNotFoundError:
        pass


def _reg_key(hive: str, key: str, view: int) -> str:
    """The key's full name as a .reg file (read in the 64-bit view) spells it."""
    if view == 32 and key.lower().startswith("software\\"):
        key = "SOFTWARE\\WOW6432Node\\" + key[len("software\\"):]
    return ("HKEY_CURRENT_USER" if hive == "HKCU" else "HKEY_LOCAL_MACHINE") + "\\" + key


def plain_name(value: str) -> bool:
    """A value's name that can go into a .reg file or reg.exe's command as it is. A name can hold a line
    break, and in the .reg file run as administrator that would start a line of its own (a value of
    the attacker's choosing, written for the whole PC)."""
    return bool(value) and not any(ord(ch) < 32 or ch == "\x7f" for ch in value)


def _reg_text(lines: list[tuple[str, str]]) -> str:
    """A .reg file that deletes these (key, value) pairs (names that aren't plain are left out)."""
    lines = [(k, v) for k, v in lines if plain_name(v)]
    out = ["Windows Registry Editor Version 5.00", ""]
    for key in dict.fromkeys(k for k, _ in lines):
        out.append(f"[{key}]")
        for k, value in lines:
            if k == key:
                name = value.replace("\\", "\\\\").replace('"', '\\"')
                out.append(f'"{name}"=-')
        out.append("")
    return "\r\n".join(out) + "\r\n"


def _delete_as_admin(lines: list[tuple[str, str]]) -> bool:
    """reg.exe imports a .reg file that deletes them, started as administrator (Windows asks once)."""
    import tempfile
    from .uninstall import shell_run, wait_process
    fd, name = tempfile.mkstemp(prefix="corefold-startup-", suffix=".reg")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(_reg_text(lines).encode("utf-16"))   # (with its byte order mark, as reg.exe wants)
        reg = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "reg.exe"
        handle = shell_run(str(reg), f'import "{name}" /reg:64', admin=True, show=0)
        if handle is None:
            return False  # (the person said no)
        code = wait_process(handle, timeout_s=30)
        return code == 0 or (code is None and handle == 0)
    finally:
        try:
            os.remove(name)
        except OSError:
            pass


def remove_leftovers(entries: list[Entry]) -> list[Entry]:
    """Removes the lines of programs that are gone from this PC, with Windows' on/off note for each. A
    line in a Startup folder (a shortcut) goes to the Recycle Bin. Lines for the whole PC ask Windows
    for an administrator once for all of them. Returns the ones that are really gone now."""
    gone = [e for e in leftovers(entries) if is_gone(e.exe)]   # (looked at again, just before)
    if not gone:
        return []
    _keep_copy(gone)
    as_admin: list[tuple[str, str]] = []
    for e in gone:
        hive, key, view, approved = PLACES[e.where]
        note_view = 64 if hive == "HKLM" else 0
        try:
            if e.where.endswith("Folder"):
                from .uninstall import recycle
                if not recycle(Path(e.extra.get("file", ""))):
                    continue
            else:
                _delete_value(hive, key, view, e.value)
            _delete_value(hive, approved, note_view, e.value)
        except PermissionError:
            if not e.where.endswith("Folder"):
                as_admin.append((_reg_key(hive, key, view), e.value))
            as_admin.append((_reg_key(hive, approved, note_view), e.value))
    if as_admin:
        _delete_as_admin(as_admin)
    after = {x.id for x in list_entries()}
    return [e for e in gone if e.id not in after]
