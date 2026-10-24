"""Corefold File Organizer: sorts the loose files of a folder (Downloads, the Desktop, any folder) into a
folder for each kind (Pictures, Documents, Videos…) inside it, with one click.

Nothing is decided without the person seeing it first: plan() says where every file would go, and
sort() only moves what the plan says. Files are only ever renamed into a folder next to them (never
copied, never deleted, never overwritten), and the last sort can be undone, even after a restart.
Left alone: folders, shortcuts, hidden and system files, files still downloading, files a program has
open, and kinds of file Corefold doesn't know. Windows' and programs' own folders aren't sorted at all."""
from __future__ import annotations

import json
import logging
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("corefold")

STATE_FILE = "organizer.json"

# kind: (its folder's English name, shown and named through the language's table; its file types)
KINDS = {
    "pictures": ("Pictures", "jpg jpeg jfif jpe png gif bmp tif tiff webp heic heif avif jxl svg ico raw cr2 cr3 "
                             "nef arw dng orf rw2 raf psd xcf"),
    "videos": ("Videos", "mp4 mkv mov avi wmv webm m4v mpg mpeg ts mts m2ts flv 3gp 3g2 vob ogv"),
    "music": ("Music", "mp3 wav flac m4a aac ogg oga opus wma aiff aif alac mid midi amr"),
    "documents": ("Documents", "pdf doc docx docm dot dotx odt rtf txt md xls xlsx xlsm xlsb csv ods ppt pptx "
                               "pptm pps ppsx odp epub mobi azw3 xps oxps one vsdx pub pages numbers key"),
    "archives": ("Archives", "zip 7z rar tar gz tgz bz2 tbz2 xz txz zst lz lzma cab zipx 001 iso img"),
    "installers": ("Installers", "exe msi msix msixbundle appx appxbundle msp apk"),
    "fonts": ("Fonts", "ttf otf ttc woff woff2 fon"),
}
KIND_OF = {f".{ext}": kind for kind, (_, exts) in KINDS.items() for ext in exts.split()}

# why a file stays where it is (English, shown through tr())
STAYS = {
    "in_use": "A program has it open",
    "downloading": "Still downloading",
    "unknown": "A kind of file Corefold doesn't sort",
    "shortcut": "Shortcuts stay where they are",
    "sorted": "Already in its own folder",
}
PARTIAL = {".crdownload", ".part", ".partial", ".download", ".opdownload", ".tmp", ".!ut", ".!qb", ".aria2"}
SHORTCUTS = {".lnk", ".url", ".website", ".appref-ms"}
SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store", "ehthumbs.db"}

# a refused folder: why (English, shown through tr())
REFUSED = {
    "missing": "That folder isn't there any more.",
    "drive": "Corefold doesn't sort the top of a drive: pick a folder on it.",
    "system": "That's a folder of Windows or of your programs, so Corefold leaves it as it is.",
    "home": "That's your user folder itself: pick a folder inside it, like Downloads.",
    "program": "That folder looks like a program or a project, where moving files would break it.",
}


@dataclass
class Item:
    path: Path                  # where it is now
    size: int = 0
    kind: str = ""              # where it goes ("" = it stays, see why)
    target: Path | None = None  # its new place (a new name if one like it is already there)
    why: str = ""               # why it stays (a STAYS key)
    on: bool = True             # the person can leave a file (or a whole kind) out

    @property
    def renamed(self) -> bool:
        return self.target is not None and self.target.name != self.path.name


@dataclass
class Plan:
    folder: Path
    items: list[Item] = field(default_factory=list)   # what moves, then what stays
    folders: dict[str, Path] = field(default_factory=dict)  # kind: its folder
    new_folders: set[str] = field(default_factory=set)  # kinds whose folder isn't there yet
    refused: str = ""                                 # a REFUSED key: nothing is sorted here

    def moving(self) -> list[Item]:
        return [i for i in self.items if i.kind and i.on]

    def staying(self) -> list[Item]:
        return [i for i in self.items if not i.kind]

    def by_kind(self) -> dict[str, list[Item]]:
        out: dict[str, list[Item]] = {}
        for i in self.items:
            if i.kind:
                out.setdefault(i.kind, []).append(i)
        return out


@dataclass
class Result:
    moved: int = 0
    folders: int = 0                                       # folders that got files
    failed: list[tuple[Path, str]] = field(default_factory=list)  # (file, why) it stayed after all


# ----------------------------------------------------------------------------- folder names

def _tables() -> list[dict[str, str]]:
    from . import i18n
    return [{}] + [i18n.table_for(code) for code in i18n.LANGUAGES if code not in ("system", "en")]


def folder_name(kind: str) -> str:
    """A kind's folder, named in Corefold's language (straight from the table: tr() can add an invisible
    right-to-left mark, which mustn't end up in a folder's name)."""
    from . import i18n
    english = KINDS[kind][0]
    return i18n.table_for(i18n.language()).get(english, english)


def _known_names(kind: str) -> set[str]:
    """Every name this kind's folder may already have (it was sorted before in another language)."""
    english = KINDS[kind][0]
    return {t.get(english, english).casefold() for t in _tables()}


# ----------------------------------------------------------------------------- what may be sorted

def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def _inside(p: str, top: str) -> bool:
    return p == top or p.startswith(top.rstrip(os.sep) + os.sep)


def refused(folder: Path, known: bool = False) -> str:
    """Why this folder can't be sorted (a REFUSED key), or "" if it can. (known: Downloads or the Desktop,
    where a program's file is just something that was downloaded.)"""
    try:
        if not folder.is_dir():
            return "missing"
    except OSError:
        return "missing"
    p = _norm(folder)
    if os.path.dirname(p) == p:
        return "drive"
    home = Path.home()
    if p in (_norm(home), _norm(home.parent)):
        return "home"
    system = [os.environ.get(n) for n in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
                                          "ProgramData", "APPDATA", "LOCALAPPDATA")]
    system.append(str(home / "AppData"))
    if os.name == "nt":
        system += [os.environ.get("SystemDrive", "C:") + os.sep + n for n in ("Windows", "Program Files",
                                                                             "Program Files (x86)", "ProgramData",
                                                                             "$Recycle.Bin", "Recovery")]
    if any(s and _inside(p, _norm(s)) for s in system):
        return "system"
    if known:
        return ""
    try:
        with os.scandir(folder) as it:
            for e in it:
                name = e.name.lower()
                if name in (".git", ".svn", ".hg", "node_modules", "package.json", "pyproject.toml", "setup.py",
                            "cargo.toml", "pom.xml", "makefile", "cmakelists.txt") or name.endswith((".sln", ".dll")):
                    return "program"
    except OSError:
        return "missing"
    return ""


# ----------------------------------------------------------------------------- in use

def in_use(path: Path) -> bool:
    """A program has this file open in a way that stops it being moved (Word, a half-written download).
    Asks Windows for exactly what a move needs, without reading the file (so a OneDrive file that isn't
    on the PC isn't downloaded)."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                              wintypes.DWORD, wintypes.HANDLE)
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    DELETE, SHARE_ALL, OPEN_EXISTING = 0x10000, 7, 3
    OPEN_REPARSE_POINT, OPEN_NO_RECALL = 0x00200000, 0x00100000
    h = k.CreateFileW(str(path), DELETE, SHARE_ALL, None, OPEN_EXISTING, OPEN_REPARSE_POINT | OPEN_NO_RECALL, None)
    if h is None or h == wintypes.HANDLE(-1).value:
        return ctypes.get_last_error() in (32, 33)  # sharing / lock violation
    k.CloseHandle(h)
    return False


def _hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith("~$") or entry.name.lower() in SKIP_NAMES:
        return True
    try:
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attrs & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM)) if os.name == "nt" else \
        entry.name.startswith(".")


# ----------------------------------------------------------------------------- the plan

def _free_name(folder: Path, name: str, taken: set[str]) -> Path:
    """name in folder, or "name (2)", "name (3)"… if that's already there (or already planned)."""
    stem, ext = os.path.splitext(name)
    if stem.startswith(".") and not ext:
        stem, ext = name, ""
    cand, n = folder / name, 2
    while os.path.lexists(cand) or _norm(cand) in taken:
        cand = folder / f"{stem} ({n}){ext}"
        n += 1
    taken.add(_norm(cand))
    return cand


def _kind_folder(folder: Path, kind: str, names: list[str], remembered: dict) -> tuple[Path, bool]:
    """Where this kind goes in folder: the folder sorted into last time, one already named like it (in any
    of Corefold's languages), or a new one. Returns (it, already there)."""
    wanted = remembered.get(kind)
    known = _known_names(kind)
    if wanted:
        known = {wanted.casefold()} | known
    for name in ([wanted] if wanted else []) + names:
        if name.casefold() in known:
            p = folder / name
            if p.is_dir() and not p.is_symlink() and not _is_junction(p):
                return p, True
    name = folder_name(kind)
    p = folder / name
    n = 2
    while os.path.lexists(p):  # (a file, or a link elsewhere, has that name)
        p = folder / f"{name} ({n})"
        n += 1
    return p, False


def _is_junction(p: Path) -> bool:
    try:
        return p.is_junction()
    except AttributeError:  # (before Python 3.12)
        return False


def plan(folder: Path, check_in_use: bool = True, known: bool = False) -> Plan:
    """Where every loose file in this folder would go. Changes nothing."""
    folder = Path(folder)
    out = Plan(folder)
    out.refused = refused(folder, known)
    if out.refused:
        return out
    state = _load()
    remembered = state.get("folders", {}).get(_norm(folder), {})
    off = set(state.get("off", []))
    files, dirs = [], []
    with os.scandir(folder) as it:
        for e in it:
            try:
                if e.is_dir(follow_symlinks=False) or _is_junction(Path(e.path)):
                    dirs.append(e.name)
                elif e.is_file(follow_symlinks=False) and not _hidden(e):
                    files.append(e)
            except OSError:
                continue
    names = {e.name.casefold() for e in files}
    here = folder.name.casefold()
    taken: set[str] = set()
    moving, staying = [], []
    for e in sorted(files, key=lambda e: e.name.casefold()):
        p = Path(e.path)
        try:
            size = e.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        ext = os.path.splitext(e.name)[1].lower()
        item = Item(p, size)
        kind = KIND_OF.get(ext, "")
        if ext in PARTIAL or any((e.name + s).casefold() in names for s in (".crdownload", ".part", ".partial",
                                                                            ".download", ".opdownload")):
            item.why = "downloading"
        elif ext in SHORTCUTS:
            item.why = "shortcut"
        elif not kind:
            item.why = "unknown"
        elif here in _known_names(kind):  # (sorting Pictures itself: its pictures stay)
            item.why = "sorted"
        elif check_in_use and in_use(p):
            item.why = "in_use"
        if item.why:
            staying.append(item)
            continue
        if kind not in out.folders:
            out.folders[kind], there = _kind_folder(folder, kind, dirs, remembered)
            if not there:
                out.new_folders.add(kind)
        item.kind = kind
        item.on = kind not in off
        item.target = _free_name(out.folders[kind], e.name, taken)
        moving.append(item)
    order = list(KINDS)
    moving.sort(key=lambda i: order.index(i.kind))
    out.items = moving + staying
    return out


def loose_count(folder: Path, known: bool = False) -> int:
    """How many files a sort of this folder would move (quick: doesn't ask Windows what's in use)."""
    try:
        return len(plan(folder, check_in_use=False, known=known).moving())
    except OSError:
        return 0


# ----------------------------------------------------------------------------- sorting and undoing

def sort(p: Plan, progress=None) -> Result:
    """Moves what the plan says (each file is checked again just before). Kept so it can be undone."""
    res = Result()
    todo = p.moving()
    if p.refused or not todo:
        return res
    made: list[str] = []
    moves: list[list[str]] = []
    used: set[str] = set()
    taken: set[str] = set()
    state = _load()
    before = state.get("last")  # (kept if this sort ends up moving nothing)
    record = {"folder": str(p.folder), "time": time.time(), "moves": moves, "made": made}
    state["last"] = record
    for kind in {i.kind for i in todo}:
        f = p.folders[kind]
        if not f.is_dir():
            try:
                f.mkdir()
                made.append(str(f))
            except OSError as e:
                log.warning("couldn't make %s: %s", f, e)
    for n, item in enumerate(todo, 1):
        src = item.path
        folder = p.folders[item.kind]
        if not os.path.lexists(src):
            res.failed.append((src, "gone"))
            continue
        dst = item.target if item.target is not None else folder / src.name
        if os.path.lexists(dst) or dst.parent != folder:  # (something took its name since the plan)
            dst = _free_name(folder, src.name, taken)
        try:
            if os.name != "nt" and os.path.lexists(dst):  # (Windows itself never replaces a file on rename)
                raise FileExistsError(dst)
            os.rename(src, dst)
        except OSError as e:
            log.info("organizer: couldn't move %s: %s", src, e)
            res.failed.append((src, "in_use" if getattr(e, "winerror", 0) in (32, 33) else "error"))
            continue
        moves.append([str(src), str(dst)])
        used.add(item.kind)
        res.moved += 1
        if n % 200 == 0:
            _save(state)
            if progress:
                progress((n, len(todo)))
    for f in list(made):  # (a folder made for files that all stayed)
        if not any(m[1].startswith(f + os.sep) for m in moves):
            try:
                os.rmdir(f)
                made.remove(f)
            except OSError:
                pass
    res.folders = len(used)
    remembered = state.setdefault("folders", {}).setdefault(_norm(p.folder), {})
    for kind in used:
        remembered[kind] = p.folders[kind].name
    if not moves:  # nothing moved: the sort before this one can still be undone
        if before:
            state["last"] = before
        else:
            state.pop("last", None)
    _save(state)
    return res


def auto_sort(folder: Path, days: float = 1.0) -> Result:
    """"Keep this folder tidy by itself": a sort of the files that arrived at least `days` ago (one just
    downloaded or saved is likely still being used, so it waits for the next day). Undone like any sort."""
    p = plan(folder)
    if p.refused:
        return Result()
    cutoff = time.time() - days * 86400
    for item in p.moving():
        try:
            st = item.path.stat()
            arrived = max(st.st_mtime, getattr(st, "st_birthtime", st.st_ctime))  # (Windows: when it came)
        except OSError:
            item.on = False
            continue
        if arrived > cutoff:
            item.on = False
    return sort(p)


def last_sort() -> dict | None:
    """The last sort that can still be undone: {"folder", "time", "moves": [[from, to]…], "made": […]}."""
    last = _load().get("last")
    return last if last and last.get("moves") else None


@dataclass
class UndoResult:
    back: int = 0
    gone: int = 0                                     # moved or deleted since: nothing to put back
    failed: list[Path] = field(default_factory=list)  # in use: kept for another Undo
    renamed: int = 0                                  # put back as "name (2)" (its old name was taken)


def undo(progress=None) -> UndoResult:
    """Puts every file of the last sort back where it was, and removes the folders it made (if empty)."""
    res = UndoResult()
    state = _load()
    last = state.get("last")
    if not last:
        return res
    left = []
    moves = last.get("moves", [])
    taken: set[str] = set()
    for n, (src, dst) in enumerate(reversed(moves), 1):
        if not os.path.lexists(dst):
            res.gone += 1
            continue
        back = Path(src)
        if os.path.lexists(back):
            back = _free_name(back.parent, back.name, taken)
            res.renamed += 1
        try:
            if os.name != "nt" and os.path.lexists(back):
                raise FileExistsError(back)
            os.rename(dst, back)
            res.back += 1
        except OSError as e:
            log.info("organizer: couldn't put back %s: %s", dst, e)
            res.failed.append(Path(dst))
            left.append([src, dst])
            if back != Path(src):
                res.renamed -= 1
        if progress and n % 200 == 0:
            progress((n, len(moves)))
    made = []
    for f in last.get("made", []):
        try:
            os.rmdir(f)  # (only if empty: anything put in it since stays, and so does the folder)
        except OSError:
            if os.path.isdir(f):
                made.append(f)
    if left:
        last["moves"] = list(reversed(left))
        last["made"] = made
    else:
        state.pop("last", None)
    _save(state)
    return res


# ----------------------------------------------------------------------------- what's kept

def set_off(kinds: set[str]):
    """The kinds the person left out (they stay out next time)."""
    state = _load()
    state["off"] = sorted(kinds)
    _save(state)


def kinds_off() -> set[str]:
    return set(_load().get("off", []))


def _state_path() -> Path:
    from .settings import CONFIG_DIR
    return CONFIG_DIR / STATE_FILE


def _load() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state: dict):
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        log.warning("couldn't keep the organizer's state: %s", e)
