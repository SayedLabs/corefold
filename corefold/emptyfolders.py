"""Corefold Space's empty folders: folders with no files anywhere inside them, in your own folders.

Careful by design: nothing inside Windows, programs' folders or AppData is looked at, hidden and system
folders and links (junctions) are left alone, and a folder that holds anything left alone isn't offered
either (removing it would take that with it). Your own folders (Documents, …) and the folder you picked
are never offered themselves, even when they're empty, and neither are Windows' other own folders (Camera
Roll, Saved Pictures, the Public ones, …) or any folder Windows has customised (its desktop.ini). Outside
your own folders, nothing in a folder that holds a program (.exe, .dll) is offered: programs expect their
folders to be there. Folders made in the last day are left out: an app may be about to put something in
them. What goes, goes to the Recycle Bin."""
from __future__ import annotations

import ctypes
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

MAX_FOLDERS = 200_000        # folders looked at in one go (a whole big drive stops here and says so)
NEW_SECONDS = 24 * 3600      # folders made more recently than this are left out
PROGRESS_EVERY = 500         # folders between progress reports
# files Windows (or a Mac) leaves in folders by itself: a folder holding only these is still empty
LEFTOVERS = {"desktop.ini", "thumbs.db", ".ds_store"}
USER_FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")

_READONLY, _HIDDEN, _SYSTEM, _REPARSE = 0x1, 0x2, 0x4, 0x400
# a folder whose files aren't all here (only in the cloud): what's in it can't be known from a look
_NOT_HERE = 0x1000 | 0x40000            # offline, recall on open
PROGRAM_FILES = {".exe", ".dll"}         # a folder holding these belongs to a program
# Windows' other own folders: never offered, even when they hold nothing (apps and libraries point at them)
_SPECIAL_IDS = (
    "{AB5FB87B-7CE2-4F83-915D-550846C9537B}",   # Camera Roll
    "{3B193882-D3AD-4EAB-965A-69829D1FB59F}",   # Saved Pictures
    "{B7BEDE81-DF94-4682-A7D8-57A52620B86F}",   # Screenshots
    "{DE92C1C7-837F-4F69-A3BB-86E631204A23}",   # Playlists
    "{56784854-C6CB-462B-8169-88E350ACB882}",   # Contacts
    "{1777F761-68AD-4D8A-87BD-30B759FA33DD}",   # Favorites
    "{BFB9D5E0-C6A9-404C-B2B2-AE6DB6AF4968}",   # Links
    "{4C5C32FF-BB9D-43B0-B5B4-2D72E54EAAA4}",   # Saved Games
    "{7D1D3A04-DEBB-4115-95CF-2F29DA2920DA}",   # Searches
    "{31C0DD25-9439-4F12-BF41-7FF4EDA38722}",   # 3D Objects
    "{DFDF76A2-C82A-4D63-906A-5644AC457385}",   # Public
    "{C4AA340D-F20F-4863-AFEF-F87EF2E6BA25}",   # Public Desktop
    "{ED4824AF-DCE4-45A8-81E2-FC7965083634}",   # Public Documents
    "{3D644C9B-1FB8-4F30-9B45-F670235F79C0}",   # Public Downloads
    "{3214FAB5-9757-4298-BB61-92A9DEAA44FF}",   # Public Music
    "{B6EBFB86-6907-413C-9AF7-4FC2ABF07CC5}",   # Public Pictures
    "{2400183A-6185-49FB-A2D8-4A392A602BA3}",   # Public Videos
)
_SKIP_NAMES = {"$recycle.bin", "system volume information", "appdata", "$windows.~bt", "$windows.~ws",
               "$sysreset", "config.msi"}
# known folders (shell32's SHGetKnownFolderPath), so a moved Documents (into OneDrive, to D:) is found
_KNOWN_IDS = {
    "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "Music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "Videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}


@dataclass
class EmptyFolder:
    path: str
    root: str               # the folder it was found in
    created: float
    inside: int = 0         # empty folders inside it (they go with it)
    leftovers: tuple = ()   # names of the leftover files in it ("desktop.ini"), if any


@dataclass
class Found:
    items: list = field(default_factory=list)   # EmptyFolder, by path
    looked: int = 0                             # folders looked at
    new_left: int = 0                           # empty, but made in the last day: left out
    capped: bool = False                        # stopped at MAX_FOLDERS
    cancelled: bool = False
    refused: list = field(default_factory=list) # roots inside Windows or programs' folders: not looked in
    seconds: float = 0.0


def _key(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]


def _known_folder(name: str) -> Path | None:
    """Where a known folder is, by its name in _KNOWN_IDS or its id ("{…}"); None if it has no place."""
    if os.name != "nt":
        return None
    try:
        # (own copies of the functions, with their own prototypes: ctypes.windll's are shared with other modules,
        # which may set other argtypes on them)
        ole32, shell32 = ctypes.WinDLL("ole32"), ctypes.WinDLL("shell32")
        ole32.CLSIDFromString.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(_GUID)]
        ole32.CLSIDFromString.restype = ctypes.c_long
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole32.CoTaskMemFree.restype = None
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(_GUID), ctypes.c_uint32, ctypes.c_void_p,
                                                 ctypes.POINTER(ctypes.c_void_p)]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        guid = _GUID()
        if ole32.CLSIDFromString(_KNOWN_IDS.get(name, name), ctypes.byref(guid)) != 0:
            return None
        out = ctypes.c_void_p()
        hr = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
        try:
            text = ctypes.wstring_at(out.value) if hr == 0 and out.value else ""
            return Path(text) if text else None
        finally:
            ole32.CoTaskMemFree(out)   # (freed even when it failed, as Windows asks)
    except Exception:
        return None


def user_folders() -> list[Path]:
    """Desktop, Documents, Downloads, Pictures, Music and Videos where they really are, then OneDrive's
    folder if there is one (only those that exist)."""
    home = Path.home()
    out: list[Path] = []
    for name in USER_FOLDERS:
        p = _known_folder(name) or home / name
        if p.is_dir():
            out.append(p)
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        v = os.environ.get(var)
        if v and Path(v).is_dir():
            out.append(Path(v))
    seen, unique = set(), []
    for p in out:
        if _key(p) not in seen:
            seen.add(_key(p))
            unique.append(p)
    return unique


def special_folders() -> list[Path]:
    """Windows' other own folders (Camera Roll, Saved Pictures, the Public ones, …) where they are."""
    return [p for p in (_known_folder(i) for i in _SPECIAL_IDS) if p is not None]


def _off_limits() -> list[str]:
    """Windows, programs' folders, ProgramData and AppData: never looked inside."""
    names = ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData",
             "APPDATA", "LOCALAPPDATA")
    out = [_key(os.environ[n]) for n in names if os.environ.get(n)]
    out.append(_key(Path.home() / "AppData"))
    if os.name == "nt":
        out.append(_key(os.environ.get("SystemDrive", "C:") + "\\Windows"))
    return out


def _under(key: str, places: list[str]) -> bool:
    return any(key == p or key.startswith(p.rstrip(os.sep) + os.sep) for p in places)


def off_limits(path) -> bool:
    """True for a folder in a place Corefold never looks for empty folders (Windows, programs, AppData, …)."""
    key = _key(path)
    if _under(key, _off_limits()):
        return True
    return any(part.lower() in _SKIP_NAMES for part in Path(key).parts)


def _created(st) -> float:
    return getattr(st, "st_birthtime", None) or st.st_ctime


def _left_alone(entry: os.DirEntry, st, places: list[str]) -> bool:
    """A sub-folder that is never looked in or removed: hidden or system, a link, Windows' own places."""
    if entry.name.lower() in _SKIP_NAMES:
        return True
    if os.name != "nt":
        return entry.name.startswith(".") or entry.is_symlink()
    attrs = _attrs(st)
    if attrs & (_HIDDEN | _SYSTEM | _NOT_HERE):
        return True
    if attrs & _REPARSE:
        # junctions, links and anything else that leads elsewhere; only cloud folders (OneDrive's files
        # that live online) are real folders
        tag = getattr(st, "st_reparse_tag", 0)
        if tag & 0xFFFF0FFF != 0x9000001A:
            return True
    return _under(_key(entry.path), places)


def _attrs(st) -> int:
    """A file's or folder's Windows attributes (0 elsewhere)."""
    return getattr(st, "st_file_attributes", 0)


def _customised(attrs: int, leftovers) -> bool:
    """A folder Windows has customised: a desktop.ini, which it only reads in a read-only or system folder
    (its own folders' names and icons, Camera Roll, …). Removing it would take that away."""
    return bool(attrs & (_READONLY | _SYSTEM)) and any(n.lower() == "desktop.ini" for n in leftovers or ())


def _look(path: str, places: list[str]):
    """One folder: (its sub-folders as (path, created, attributes), whether it holds something that stays
    (a file, or a folder left alone), the leftover files in it, whether it holds a program's files). None
    if it can't be read."""
    subs, holds, leftovers, program = [], False, [], False
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    holds = True
                    continue
                if not is_dir:
                    if e.name.lower() in LEFTOVERS:
                        leftovers.append(e.name)
                    else:
                        holds = True  # (any file, even a link or one only in the cloud)
                        program = program or os.path.splitext(e.name)[1].lower() in PROGRAM_FILES
                    continue
                if _left_alone(e, st, places):
                    holds = True
                    continue
                subs.append((e.path, _created(st), _attrs(st)))
    except OSError:
        return None
    return subs, holds, leftovers, program


def find(roots: list[Path], cancel: threading.Event | None = None, progress=None,
         new_seconds: float | None = None, limit: int | None = None, keep: list[Path] | None = None) -> Found:
    """Empty folders in these folders. progress(folders looked at, folder) is called now and then.
    A tree of empty folders is one item: the top one, with how many are inside it. keep: folders that
    are never offered themselves (your own folders, by default)."""
    started = time.monotonic()
    new_seconds = NEW_SECONDS if new_seconds is None else new_seconds
    limit = MAX_FOLDERS if limit is None else limit
    now = time.time()
    result = Found()
    places = _off_limits()
    yours = [_key(p) for p in (user_folders() if keep is None else keep)]
    kept = set(yours) | {_key(p) for p in (special_folders() if keep is None else ())}
    kept.add(_key(Path.home()))
    # roots: the ones that can be looked in, and none inside another (it would be looked at twice)
    chosen: list[Path] = []
    for r in roots:
        if not Path(r).is_dir():
            continue
        if off_limits(r):
            result.refused.append(Path(r))
            continue
        chosen.append(Path(r))
    keys = [_key(r) for r in chosen]
    chosen = [r for r, k in zip(chosen, keys) if not any(o != k and _under(k, [o]) for o in keys)]

    for root in chosen:
        # every folder is numbered as it's found; folders are always found after the folder they're in,
        # so going through them backwards sees what's inside a folder before the folder itself
        paths: list[str] = [str(root)]
        parent: list[int] = [-1]
        created: list[float] = [0.0]
        holds: list[bool] = [False]
        looked: list[bool] = [False]
        leftovers: list = [None]
        attrs: list[int] = [0]
        program: list[bool] = [False]   # it, or a folder it's in, holds a program's files
        stack = [0]
        while stack:
            if cancel is not None and cancel.is_set():
                result.cancelled = True
                break
            i = stack.pop()
            seen = _look(paths[i], places)
            result.looked += 1
            if progress is not None and result.looked % PROGRESS_EVERY == 0:
                progress(result.looked, paths[i])
            if seen is None:
                holds[i] = True  # (can't be read: not known to be empty)
                looked[i] = True
                continue
            subs, holding, left, programs = seen
            holds[i] = holding or _customised(attrs[i], left)
            leftovers[i] = left or None
            looked[i] = True
            program[i] = program[i] or programs
            for sub, made, sub_attrs in subs:
                if len(paths) >= limit:
                    result.capped = True
                    holds[i] = True  # (not all of it was looked at)
                    break
                paths.append(sub)
                parent.append(i)
                created.append(made)
                holds.append(False)
                looked.append(False)
                leftovers.append(None)
                attrs.append(sub_attrs)
                program.append(program[i])
                stack.append(len(paths) - 1)
        n = len(paths)
        inside = [0] * n
        # each folder: "empty" (nothing inside that stays, and old enough to go), "young" (would be empty,
        # but it or something in it was made in the last day) or "holds" (something stays)
        state = ["empty"] * n
        young = [False] * n
        names: list = [None] * n
        for i in range(n - 1, -1, -1):
            fixed = i == 0 or _key(paths[i]) in kept or _is_drive_top(paths[i])
            if not looked[i] or holds[i] or fixed:
                state[i] = "holds"
            elif young[i] or (new_seconds > 0 and now - created[i] < new_seconds):  # (0: any age, whatever the clock)
                state[i] = "young"
            p = parent[i]
            if p < 0:
                continue
            if state[i] == "empty":
                inside[p] += inside[i] + 1
                if leftovers[i] or names[i]:
                    names[p] = (names[p] or set()) | set(leftovers[i] or ()) | (names[i] or set())
            elif state[i] == "young":
                young[p] = True
            else:
                holds[p] = True
        for i in range(1, n):
            p = parent[i]
            if state[i] == "empty" and state[p] != "empty":
                if program[p] and not _under(_key(paths[i]), yours):
                    continue  # (a program's folder, outside your own folders: it may expect this one there)
                left = set(leftovers[i] or ()) | (names[i] or set())
                result.items.append(EmptyFolder(paths[i], str(root), created[i], inside[i],
                                                tuple(sorted(left, key=str.lower))))
            elif state[i] == "young" and state[p] != "young":
                result.new_left += 1
        if result.cancelled:
            break
    result.items.sort(key=lambda f: f.path.lower())
    result.seconds = time.monotonic() - started
    return result


def _is_drive_top(path: str) -> bool:
    """A folder right at the top of a drive (C:\\Intel, D:\\Games): Windows and programs make those."""
    up = os.path.dirname(os.path.abspath(path))
    return os.path.dirname(up) == up


def shown_path(item: EmptyFolder) -> str:
    """Where it is, from the folder it was found in ("Documents\\Old project")."""
    root = Path(item.root)
    base = root.parent if root.parent != root else root
    try:
        return os.path.relpath(item.path, base)
    except ValueError:
        return item.path


def still_empty(path, places: list[str] | None = None) -> bool:
    """Looks again just before removing: still nothing inside that would be lost."""
    places = _off_limits() if places is None else places
    try:
        top = _attrs(os.stat(path))
    except OSError:
        return False
    stack, count = [(str(path), top)], 0
    while stack:
        here, attrs = stack.pop()
        seen = _look(here, places)
        count += 1
        if seen is None or seen[1] or count > 10_000 or _customised(attrs, seen[2]):
            return False
        stack += [(sub, sub_attrs) for sub, _made, sub_attrs in seen[0]]
    return True


def remove(items: list[EmptyFolder]) -> list[EmptyFolder]:
    """Sends the ones that are still empty to the Recycle Bin. Returns the ones that went."""
    from .space import recycle
    places = _off_limits()
    ok = [it for it in items if not off_limits(it.path) and still_empty(it.path, places)]
    if not ok:
        return []
    recycle([Path(it.path) for it in ok])
    return [it for it in ok if not os.path.lexists(it.path)]
