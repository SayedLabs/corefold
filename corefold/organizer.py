"""Corefold File Organizer: sorts the loose files of a folder (Downloads, the Desktop, any folder) into a
folder for each kind (Pictures, Documents, Videos…) inside it, with one click. The person's own rules
("a name with invoice in it goes into Invoices", "PSD and AI files go into Designs") come first, and each
folder can be split by year, or by year and month (a photo by the day it was taken).

Nothing is decided without the person seeing it first: plan() says where every file would go, and
sort() only moves what the plan says. Files are only ever renamed into a folder next to them (never
copied, never deleted, never overwritten), and the last sort can be undone, even after a restart.
Left alone: folders, shortcuts, hidden and system files, files still downloading, files a program has
open, and kinds of file Corefold doesn't know. Windows' and programs' own folders aren't sorted at all.

Every move is written down before it's made (organizer-journal.jsonl), so a sort cut short by a crash or a
power cut can still be undone, and Undo only moves a file back if it's still the file that was moved (same
size and time, and the same file on the disk): a different file that took its place later stays where it is."""
from __future__ import annotations

import json
import logging
import os
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("corefold")

STATE_FILE = "organizer.json"
JOURNAL_FILE = "organizer-journal.jsonl"

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
    "other_drive": "Its rule's folder is on another drive",
}
# inside each folder, by date: (key, English shown through tr())
DATES = {"": "Nothing more", "year": "By year", "month": "By year and month"}
# a rule matches by: (key, English shown through tr())
RULE_MATCHES = {"name": "The name has", "type": "The type is"}
PHOTO_DATES = {".jpg", ".jpeg", ".jfif", ".jpe", ".heic", ".heif", ".tif", ".tiff", ".webp", ".png", ".avif",
               ".dng", ".cr2", ".nef", ".arw", ".orf", ".rw2"}  # (they may say when the photo was taken)
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


# ----------------------------------------------------------------------------- the person's rules

@dataclass
class Rule:
    id: str            # stable, so leaving its files out is remembered ("rule:<id>")
    match: str         # a RULE_MATCHES key
    text: str          # words (any of them, split by commas) or types ("psd, ai, fig")
    folder: str        # a folder's name inside the sorted folder, or a whole path (on the same drive)

    @property
    def kind(self) -> str:
        return "rule:" + self.id

    def words(self) -> list[str]:
        bits = [b.strip().casefold() for b in self.text.replace("،", ",").replace(";", ",").split(",")]
        if self.match == "type":
            return [b.lstrip("*").lstrip(".") for b in bits if b.lstrip("*").lstrip(".")]
        return [b for b in bits if b]

    def matches(self, name: str) -> bool:
        words = self.words()
        if self.match == "type":
            return os.path.splitext(name)[1].lower().lstrip(".") in words
        stem = os.path.splitext(name)[0].casefold()
        return any(w in stem for w in words)

    def name(self) -> str:
        return os.path.basename(self.folder.rstrip("\\/")) or self.folder


def rules() -> list[Rule]:
    out = []
    for r in _load().get("rules", []):
        try:
            rule = Rule(str(r["id"]), r.get("match", "name"), str(r.get("text", "")), str(r.get("folder", "")))
        except (KeyError, TypeError):
            continue
        if rule.words() and safe_folder(rule.folder):
            out.append(rule)
    return out


def set_rules(new: list[Rule]):
    state = _load()
    state["rules"] = [{"id": r.id, "match": r.match, "text": r.text, "folder": r.folder} for r in new]
    _save(state)


def safe_folder(text: str) -> bool:
    """A rule's folder: a plain name (made inside the folder being sorted) or a whole path; never one that
    climbs out with .. or names a drive's top."""
    text = (text or "").strip()
    if not text or any(part == ".." for part in text.replace("\\", "/").split("/")):
        return False
    if os.path.isabs(text) or (len(text) > 1 and text[1] == ":"):
        p = _norm(text)
        return os.path.dirname(p) != p and refused(Path(text), known=True) not in ("system", "drive", "home")
    return not any(c in text for c in '<>:"|?*') and "/" not in text and "\\" not in text


def date_mode() -> str:
    mode = _load().get("dates", "")
    return mode if mode in DATES else ""


def set_date_mode(mode: str):
    state = _load()
    state["dates"] = mode if mode in DATES else ""
    _save(state)


def taken_on(path: Path, ext: str, mtime: float) -> float:
    """When the file was made: a photo's own date when it has one (from the camera or phone), else when
    the file was last changed. A cloud file that isn't on this PC isn't opened (that would download it)."""
    if ext in PHOTO_DATES:
        try:
            attrs = getattr(os.stat(path), "st_file_attributes", 0)
            if not attrs & 0x00441000:  # (offline / recall on open / recall on data access)
                from PIL import Image
                with Image.open(path) as im:
                    exif = im.getexif()
                    when = exif.get_ifd(0x8769).get(36867) or exif.get(306)
                if when:
                    import datetime
                    return datetime.datetime.strptime(str(when).strip()[:19], "%Y:%m:%d %H:%M:%S").timestamp()
        except Exception:
            log.debug("no date taken in %s", path, exc_info=True)
    return mtime


def month_name(month: int) -> str:
    """"10 October", in Corefold's language (the number first, so the folders line up in order)."""
    try:
        from PySide6.QtCore import QLocale
        from . import i18n
        name = QLocale(i18n.language()).standaloneMonthName(month, QLocale.FormatType.LongFormat)
    except Exception:
        name = ""
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"/\\|?*\u200e\u200f')
    return f"{month:02d} {name}".strip()


def _date_folder(base: Path, when: float, mode: str, made: dict) -> Path:
    """base/2026, or base/2026/10 October (an existing "10 …" folder there is used, whatever its language)."""
    t = time.localtime(when)
    year = base / str(t.tm_year)
    if mode != "month":
        return year
    key = (_norm(year), t.tm_mon)
    if key not in made:
        prefix, found = f"{t.tm_mon:02d}", None
        try:
            with os.scandir(year) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False) and (e.name == prefix or e.name.startswith(prefix + " ")):
                        found = e.name
                        break
        except OSError:
            pass
        made[key] = year / (found or month_name(t.tm_mon))
    return made[key]


def _same_drive(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.splitdrive(os.path.abspath(a))[0]) == \
        os.path.normcase(os.path.splitdrive(os.path.abspath(b))[0])


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
    my_rules = rules()
    dates = date_mode()
    months: dict = {}
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
        rule = next((r for r in my_rules if r.matches(e.name)), None)
        kind = rule.kind if rule else KIND_OF.get(ext, "")
        if ext in PARTIAL or any((e.name + s).casefold() in names for s in (".crdownload", ".part", ".partial",
                                                                            ".download", ".opdownload")):
            item.why = "downloading"
        elif ext in SHORTCUTS:
            item.why = "shortcut"
        elif not kind:
            item.why = "unknown"
        elif rule is None and here in _known_names(kind):  # (sorting Pictures itself: its pictures stay)
            item.why = "sorted"
        elif rule is not None and _norm(_rule_folder(folder, rule)) == _norm(folder):
            item.why = "sorted"
        elif rule is not None and not _same_drive(_rule_folder(folder, rule), folder):
            item.why = "other_drive"  # (a move across drives is a copy and a delete: never done here)
        elif check_in_use and in_use(p):
            item.why = "in_use"
        if item.why:
            staying.append(item)
            continue
        if kind not in out.folders:
            if rule is not None:
                out.folders[kind] = _rule_folder(folder, rule)
                there = out.folders[kind].is_dir()
            else:
                out.folders[kind], there = _kind_folder(folder, kind, dirs, remembered)
            if not there:
                out.new_folders.add(kind)
        item.kind = kind
        item.on = kind not in off
        into = out.folders[kind]
        if dates:
            into = _date_folder(into, taken_on(p, ext, e.stat(follow_symlinks=False).st_mtime), dates, months)
        item.target = _free_name(into, e.name, taken)
        moving.append(item)
    order = [r.kind for r in my_rules] + list(KINDS)
    moving.sort(key=lambda i: order.index(i.kind))
    out.items = moving + staying
    return out


def _rule_folder(folder: Path, rule: Rule) -> Path:
    target = rule.folder.strip()
    return Path(target) if os.path.isabs(target) or (len(target) > 1 and target[1] == ":") else folder / target


def kind_name(kind: str) -> str:
    """What a kind (or a rule) is called on the page."""
    if kind.startswith("rule:"):
        rule = next((r for r in rules() if r.kind == kind), None)
        return rule.name() if rule else kind
    return folder_name(kind)


def loose_count(folder: Path, known: bool = False) -> int:
    """How many files a sort of this folder would move (quick: doesn't ask Windows what's in use)."""
    try:
        return len(plan(folder, check_in_use=False, known=known).moving())
    except OSError:
        return 0


# ----------------------------------------------------------------------------- sorting and undoing

def _sort_files(p: Plan, todo: list[Item], journal: "_Journal", moves: list, used: set, taken: set, res: Result,
                progress, made: list):
    for n, item in enumerate(todo, 1):
        src = item.path
        folder = item.target.parent if item.target is not None else p.folders[item.kind]
        if not _make(folder, journal, made):
            res.failed.append((src, "error"))
            continue
        try:
            st = os.lstat(src)
        except OSError:
            res.failed.append((src, "gone"))
            continue
        dst = item.target if item.target is not None else folder / src.name
        if os.path.lexists(dst) or dst.parent != folder:  # (something took its name since the plan)
            dst = _free_name(folder, src.name, taken)
        move = [str(src), str(dst), st.st_size, st.st_mtime_ns]
        journal.moving(move)
        try:
            if os.name != "nt" and os.path.lexists(dst):  # (Windows itself never replaces a file on rename)
                raise FileExistsError(dst)
            os.rename(src, dst)
        except OSError as e:
            journal.failed()
            log.info("organizer: couldn't move %s: %s", src, e)
            res.failed.append((src, "in_use" if getattr(e, "winerror", 0) in (32, 33) else "error"))
            continue
        move.append(_file_id(dst))
        journal.moved(move[-1])
        moves.append(move)
        used.add(item.kind)
        res.moved += 1
        if progress and n % 200 == 0:
            progress((n, len(todo)))


_one_at_a_time = threading.Lock()  # (the daily tidy and a sort or Undo from the page share one journal and one record)


def _make(folder: Path, journal: "_Journal", made: list) -> bool:
    """The folder (and the ones it's in) made if they aren't there yet, each written down first: one made
    just before a crash is still taken away. Only ever inside the sorted folder, or a rule's folder."""
    if folder.is_dir():
        return True
    missing = []
    f = folder
    while not f.is_dir():
        if os.path.lexists(f) or f.parent == f:
            return False  # (a file has that name)
        missing.append(f)
        f = f.parent
    for f in reversed(missing):
        try:
            journal.made(f)
            f.mkdir()
            made.append(str(f))
        except OSError as e:
            log.warning("couldn't make %s: %s", f, e)
            return False
    return True


def sort(p: Plan, progress=None) -> Result:
    """Moves what the plan says (each file is checked again just before). Kept so it can be undone."""
    with _one_at_a_time:
        return _sort(p, progress)


def _sort(p: Plan, progress=None) -> Result:
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
    journal = _Journal(record)
    try:
        _sort_files(p, todo, journal, moves, used, taken, res, progress, made)
    finally:
        journal.close()
    for f in reversed(list(made)):  # (a folder made for files that all stayed; the inner ones first)
        if not any(m[1].startswith(f + os.sep) for m in moves):
            try:
                os.rmdir(f)
                made.remove(f)
            except OSError:
                pass
    res.folders = len(used)
    remembered = state.setdefault("folders", {}).setdefault(_norm(p.folder), {})
    for kind in used:
        if not kind.startswith("rule:"):  # (a rule's folder is the rule's own)
            remembered[kind] = p.folders[kind].name
    if not moves:  # nothing moved: the sort before this one can still be undone
        if before:
            state["last"] = before
        else:
            state.pop("last", None)
    _save(state)
    journal.finish()
    return res


def auto_sort(folder: Path, days: float = 1.0, known: bool = False) -> Result:
    """"Keep this folder tidy by itself": a sort of the files that arrived at least `days` ago (one just
    downloaded or saved is likely still being used, so it waits for the next day). Undone like any sort.
    known: it's Downloads or the Desktop (allowed even with a program's files lying in it, as on the page)."""
    p = plan(folder, known=known)
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
    """The last sort that can still be undone: {"folder", "time", "moves": [[from, to, size, time, id]…],
    "made": […]}."""
    last = _load().get("last")
    return last if last and last.get("moves") else None


@dataclass
class UndoResult:
    back: int = 0
    gone: int = 0                                     # moved or deleted since: nothing to put back
    failed: list[Path] = field(default_factory=list)  # in use: kept for another Undo
    renamed: int = 0                                  # put back as "name (2)" (its old name was taken)
    changed: int = 0                                  # a different file is there now: left alone


def undo(progress=None) -> UndoResult:
    """Puts every file of the last sort back where it was, and removes the folders it made (if empty)."""
    with _one_at_a_time:
        return _undo(progress)


def _undo(progress=None) -> UndoResult:
    res = UndoResult()
    state = _load()
    last = state.get("last")
    if not last:
        return res
    left = []
    moves = last.get("moves", [])
    taken: set[str] = set()
    saved = time.monotonic()
    for n, move in enumerate(reversed(moves), 1):
        src, dst = move[0], move[1]
        if not os.path.lexists(dst):
            res.gone += 1
            continue
        if not _same_file(dst, move):
            res.changed += 1
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
            left.append(move)
            if back != Path(src):
                res.renamed -= 1
        if n % 200 == 0:
            # (a crash part-way: what's still to do, and what stayed, can be undone again. Written down once
            # a second, not every 200 files: each time is the whole list, and 50,000 files made that minutes)
            if time.monotonic() - saved > 1:
                last["moves"] = moves[:len(moves) - n] + list(reversed(left))
                _save(state)
                saved = time.monotonic()
            if progress:
                progress((n, len(moves)))
    made = []
    for f in reversed(last.get("made", [])):  # (the inner ones first: 2026/10 before 2026, before Pictures)
        try:
            os.rmdir(f)  # (only if empty: anything put in it since stays, and so does the folder)
        except OSError:
            if os.path.isdir(f):
                made.insert(0, f)
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
    state = _read_state()
    if _Journal.open_now == 0 and _journal_path().exists():
        state = _recover(state)
    return state


def _read_state() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# ----------------------------------------------------------------------------- the journal

def _journal_path() -> Path:
    from .settings import CONFIG_DIR
    return CONFIG_DIR / JOURNAL_FILE


def _file_id(path) -> int:
    """The file's number on its disk (0 where the disk has none that lasts, like FAT32 drives)."""
    try:
        return os.lstat(path).st_ino or 0
    except OSError:
        return 0


def _same_file(path, move: list) -> bool:
    """Still the file that was moved there: the same size and time (a rename keeps both), and the same file
    on the disk. Moves written down before 1.10 have neither, so they're taken as they are."""
    if len(move) < 4:
        return True
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISDIR(st.st_mode) or st.st_size != move[2] or st.st_mtime_ns != move[3]:
        return False
    return len(move) < 5 or not move[4] or not st.st_ino or st.st_ino == move[4]


class _Journal:
    """Each move written down just before it's made, one line each. Lines reach Windows straight away (so a
    crash of Corefold loses none), and the disk itself every second (a power cut loses at most that)."""
    open_now = 0  # (a sort is running: its journal isn't one left by a crash)

    def __init__(self, record: dict):
        _Journal.open_now += 1
        self.counted = True
        self.fh = None
        self.synced = time.monotonic()
        try:
            path = _journal_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            self.fh = open(path, "w", encoding="utf-8")
            self._write({"sort": {"folder": record["folder"], "time": record["time"]}}, sync=True)
        except OSError as e:
            log.warning("couldn't start the organizer's journal: %s", e)
            self.fh = None

    def _write(self, line: dict, sync: bool = False):
        if self.fh is None:
            return
        try:
            self.fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            self.fh.flush()
            if sync or time.monotonic() - self.synced > 1:
                os.fsync(self.fh.fileno())
                self.synced = time.monotonic()
        except OSError as e:
            log.warning("couldn't write the organizer's journal: %s", e)

    def made(self, folder: Path):
        self._write({"made": str(folder)})

    def moving(self, move: list):
        self._write({"move": move})

    def moved(self, file_id: int):
        self._write({"ok": file_id})

    def failed(self):
        self._write({"no": 1})

    def close(self):
        if self.fh is not None:
            try:
                self.fh.close()
            except OSError:
                pass
            self.fh = None
        if self.counted:
            _Journal.open_now -= 1
            self.counted = False

    def finish(self):
        """The sort is kept in organizer.json now: the journal isn't needed any more."""
        self.close()
        try:
            _journal_path().unlink()
        except OSError:
            pass


def _recover(state: dict) -> dict:
    """A sort that was cut short (Corefold or Windows stopped part-way): what it moved becomes the last sort,
    so Undo puts it back. A move written down but maybe not made counts only if the file is there."""
    record, move = None, None
    moves: list[list] = []
    made: list[str] = []

    def settle(m, file_id=None):
        if m is None:
            return
        if file_id is None:  # (cut short between writing it down and the answer)
            if not (os.path.lexists(m[1]) and not os.path.lexists(m[0]) and _same_file(m[1], m)):
                return
            file_id = _file_id(m[1])
        moves.append(m[:4] + [file_id])

    try:
        with open(_journal_path(), encoding="utf-8") as fh:
            for raw in fh:
                try:
                    line = json.loads(raw)
                except ValueError:  # (the last line, half-written)
                    break
                if not isinstance(line, dict):
                    continue
                if "sort" in line:
                    record = line["sort"]
                elif "made" in line:
                    made.append(str(line["made"]))
                elif "move" in line:
                    settle(move)
                    m = line["move"]
                    move = m if isinstance(m, list) and len(m) >= 4 else None
                elif "ok" in line:
                    settle(move, int(line["ok"] or 0))
                    move = None
                elif "no" in line:
                    move = None
            settle(move)
    except OSError as e:
        log.warning("couldn't read the organizer's journal: %s", e)
        return state
    if record is not None and moves:
        log.info("organizer: a sort was cut short; %d moves can be undone", len(moves))
        state["last"] = {"folder": record.get("folder", ""), "time": record.get("time", 0), "moves": moves,
                         "made": [f for f in made if os.path.isdir(f)], "cut_short": True}
    else:
        for f in reversed(made):  # (made just before the crash, nothing in it)
            try:
                os.rmdir(f)
            except OSError:
                pass
    _save(state)
    try:
        _journal_path().unlink()
    except OSError:
        pass
    return state


def _save(state: dict):
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        log.warning("couldn't keep the organizer's state: %s", e)
