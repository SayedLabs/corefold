"""Corefold Startup: what starts with Windows, and switching it off or on again.

Programs start with Windows from two places: a "Run" key in the registry (this user's, or the whole
PC's) and a Startup folder (this user's, or everyone's). Switching one off works exactly the way Task
Manager does it: Windows keeps a note for each entry under "StartupApproved", and an entry with a
"switched off" note there is skipped when Windows starts. The entry itself is never changed or
deleted, so switching it back on (here or in Task Manager) is always possible, and both always agree.

Entries for the whole PC (HKLM, everyone's Startup folder) need an administrator to change: Windows
asks first (its usual prompt), and nothing changes if the answer is no."""
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
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
SEEN_FILE = "startup-seen.json"
# (Windows' own note: an odd first byte is "switched off", then the time it was, as a FILETIME)
ON_BYTES = bytes([2]) + bytes(11)
EPOCH_AS_FILETIME = 116444736000000000


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
        return not self.where.startswith(("HKCU", "User"))


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
        e.missing = not os.path.exists(exe)
        name, e.publisher = _program_name(exe)
        if name:
            e.name = name
    if where.endswith("Folder") and e.name == value:
        e.name = Path(value).stem
    return e


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
    out.sort(key=lambda e: (not e.own, e.name.lower()))
    return out


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
    if '"' in value or value.endswith("\\"):
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
