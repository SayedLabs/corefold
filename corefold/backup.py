"""Backup: important folders copied to another drive, and kept up to date by themselves. No Qt.

On the backup drive, everything goes in one plain folder anyone can open in File Explorer, with no special
format and nothing needed to get files back:

    E:\\<PC name> backup\\Documents\\...                   the folders as they are now
    E:\\<PC name> backup\\Older versions\\2026-11-05 14-30\\Documents\\...
                                                       a file as it was before it changed, or was deleted

A run copies only what's new or changed (by size and time). A file that changed or was deleted on the PC isn't
lost from the backup: its last copy moves to Older versions (kept KEEP_DAYS days). Nothing on the PC is ever
changed. Files that are only in the cloud (OneDrive and others) are left alone: reading them would download them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("corefold")

OLDER = "Older versions"
STAMP = "%Y-%m-%d %H-%M"
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}-\d{2}( \(\d+\))?$")
KEEP_DAYS = 90
EVERY_HOURS = 24            # (a backup by itself: at most this often, whenever the drive is there)
ROOM = 200 << 20            # (left free on the backup drive)
PARTIAL = ".partial-copy"
_CLOUD = 0x1000 | 0x40000 | 0x400000   # offline, recall on open, recall on data access
_HIDDEN_SYSTEM = 0x2 | 0x4
SKIP_NAMES = {"desktop.ini", "thumbs.db", "$recycle.bin", "system volume information"}


class Cancelled(Exception):
    pass


@dataclass
class Plan:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    sources: list[str] = field(default_factory=list)
    dest: str = ""
    auto: bool = True
    keep_days: int = KEEP_DAYS
    last: float = 0.0                       # when it last finished
    last_files: int = 0                     # what that run copied
    last_bytes: int = 0
    last_failed: int = 0
    last_problem: str = ""                  # (English, for tr(): why the last run stopped, if it did)


@dataclass
class Summary:
    copied: int = 0
    bytes: int = 0
    kept_old: int = 0                       # files whose last copy moved to Older versions
    failed: list = field(default_factory=list)   # (path, why)
    missing: list = field(default_factory=list)  # sources that weren't there
    problem: str = ""                       # why it stopped early (English, for tr())
    seconds: float = 0.0


def load(text: str) -> list[Plan]:
    try:
        rows = json.loads(text or "[]")
    except ValueError:
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict):
            known = {k: v for k, v in r.items() if k in Plan.__dataclass_fields__}
            try:
                out.append(Plan(**known))
            except TypeError:
                continue
    return out


def dump(plans: list[Plan]) -> str:
    return json.dumps([asdict(p) for p in plans])


def pc_name() -> str:
    name = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "PC"
    return re.sub(r'[<>:"/\\|?*]', "", name).strip(" .") or "PC"


def home(plan: Plan) -> Path:
    """The backup's own folder on the backup drive."""
    return Path(plan.dest) / f"{pc_name()} backup"


def folder_names(sources: list[str]) -> dict[str, str]:
    """Each source's folder name in the backup: its own name, and for two with the same name, a short mark
    of where each is."""
    names = {}
    seen: dict[str, int] = {}
    for s in sources:
        base = Path(s).name or Path(s).drive.rstrip(":\\/") or "Drive"
        seen[base.lower()] = seen.get(base.lower(), 0) + 1
    for s in sources:
        base = Path(s).name or Path(s).drive.rstrip(":\\/") or "Drive"
        if seen[base.lower()] > 1 or base.lower() == OLDER.lower():  # (that name is the backup's own)
            base = f"{base} ({hashlib.sha1(os.path.normcase(s).encode()).hexdigest()[:6]})"
        names[s] = base
    return names


def _inside(a: Path, b: Path) -> bool:
    """a is b or inside it."""
    a, b = os.path.normcase(os.path.abspath(a)), os.path.normcase(os.path.abspath(b))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def problem(sources: list[str], dest: str) -> str:
    """Why these folders can't be backed up there (English, for tr()), or ""."""
    if not sources:
        return "Add the folders to keep safe"
    if not dest:
        return "Choose where the backup goes"
    d = Path(dest)
    if not d.is_dir():
        return "The backup drive isn't connected"
    for s in sources:
        if _inside(d, Path(s)):
            return "The backup can't go inside a folder it keeps safe"
        if _inside(Path(s), d):
            return "A folder to keep safe can't be inside the backup"
    return ""


def same_drive(sources: list[str], dest: str) -> bool:
    """Whether the backup is on the same drive as a folder it keeps (a broken drive would take both)."""
    drive = os.path.splitdrive(os.path.abspath(dest))[0].lower()
    return bool(drive) and any(os.path.splitdrive(os.path.abspath(s))[0].lower() == drive for s in sources)


def due(plan: Plan, now: float | None = None) -> bool:
    """Whether a backup by itself should run now (and its drive is there)."""
    now = time.time() if now is None else now
    return plan.auto and now - plan.last >= EVERY_HOURS * 3600 and not problem(plan.sources, plan.dest)


def _walk(root: Path, cancel, failed: list, unread: set, cloud: set | None = None):
    """(relative path, full path, size, mtime) of every file in root, folders that lead elsewhere and files only
    in the cloud left out (those go in cloud, if given). A folder that can't be read goes in unread (its backup
    is left as it is)."""
    todo = [Path()]
    while todo:
        rel = todo.pop()
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        here = root / rel
        try:
            with os.scandir(here) as it:
                entries = list(it)
        except OSError as e:
            unread.add(rel)
            if rel != Path():
                failed.append((str(here), e.strerror or str(e)))
            continue
        for e in entries:
            if e.name.lower() in SKIP_NAMES:
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            attrs = getattr(st, "st_file_attributes", 0)
            if stat.S_ISLNK(st.st_mode) or attrs & 0x400 and getattr(st, "st_reparse_tag", 0) in (0xA000000C,
                                                                                                    0xA0000003):
                continue  # (a link or junction: what it leads to isn't this folder's)
            if e.is_dir(follow_symlinks=False):
                todo.append(rel / e.name)
            elif stat.S_ISREG(st.st_mode) or os.name == "nt":
                if attrs & _CLOUD:
                    if cloud is not None:
                        cloud.add(os.path.normcase(str(rel / e.name)))
                    continue
                if e.name.endswith(PARTIAL):
                    continue
                yield rel / e.name, Path(e.path), st.st_size, st.st_mtime


def _same(src_size: int, src_mtime: float, dst: Path) -> bool:
    try:
        st = dst.stat()
    except OSError:
        return False
    return st.st_size == src_size and abs(st.st_mtime - src_mtime) <= 2  # (FAT and exFAT keep times to 2 s)


def _keep_old(dst: Path, older: Path, rel: Path) -> bool:
    """The backup's copy of a file that changed or went: moved into this run's Older versions folder."""
    to = older / rel
    try:
        to.parent.mkdir(parents=True, exist_ok=True)
        os.replace(dst, to)
        return True
    except OSError:
        log.info("couldn't keep the older copy of %s", dst, exc_info=True)
        return False


def run(plan: Plan, progress=None, cancel=None, now: float | None = None) -> Summary:
    """One backup of every source of the plan. progress(files done, bytes copied, file name)."""
    started = time.monotonic()
    out = Summary()
    why = problem(plan.sources, plan.dest)
    if why:
        out.problem = why
        return out
    root = home(plan)
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime(STAMP, time.localtime(now))
    older_root = root / OLDER / stamp
    n = 2
    while older_root.exists():
        older_root = root / OLDER / f"{stamp} ({n})"
        n += 1
    names = folder_names(plan.sources)
    seen_files = 0
    try:
        for source in plan.sources:
            src = Path(source)
            if not src.is_dir():
                out.missing.append(source)
                continue
            base = root / names[source]
            older = older_root / names[source]
            unread: set[Path] = set()
            present: set[str] = set()
            for rel, full, size, mtime in _walk(src, cancel, out.failed, unread, present):
                seen_files += 1
                present.add(os.path.normcase(str(rel)))
                dst = base / rel
                if _same(size, mtime, dst):
                    if progress is not None and seen_files % 200 == 0:
                        progress(seen_files, out.bytes, full.name)
                    continue
                try:
                    free = shutil.disk_usage(plan.dest).free
                except OSError:
                    free = 0
                if free < size + ROOM:
                    out.problem = "The backup drive is full"
                    return out
                partial = dst.with_name(dst.name + PARTIAL)
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(full, partial)
                    if dst.exists() and _keep_old(dst, older, rel):
                        out.kept_old += 1
                    os.replace(partial, dst)
                    out.copied += 1
                    out.bytes += size
                except OSError as e:
                    out.failed.append((str(full), e.strerror or str(e)))
                    try:
                        partial.unlink(missing_ok=True)
                    except OSError:
                        pass
                if progress is not None:
                    progress(seen_files, out.bytes, full.name)
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
            # files deleted on the PC: their last copy goes to Older versions (never where a folder couldn't be read,
            # and never a file only in the cloud now: its copy here stays as it is)
            if base.is_dir():
                for rel, full, _size, _mtime in list(_walk(base, cancel, [], set())):
                    if os.path.normcase(str(rel)) in present:
                        continue
                    if any(rel.is_relative_to(u) for u in unread):
                        continue
                    if _keep_old(full, older, rel):
                        out.kept_old += 1
                _remove_empty(base)
    except Cancelled:
        out.problem = "Stopped"
    finally:
        out.seconds = time.monotonic() - started
    prune(plan, now)
    return out


def _remove_empty(base: Path):
    for folder, dirs, files in os.walk(base, topdown=False):
        if folder != str(base) and not dirs and not files:
            try:
                os.rmdir(folder)
            except OSError:
                pass


def versions(plan: Plan) -> list[Path]:
    """The Older versions folders, newest first."""
    folder = home(plan) / OLDER
    try:
        return sorted((p for p in folder.iterdir() if p.is_dir() and STAMP_RE.match(p.name)), reverse=True)
    except OSError:
        return []


def prune(plan: Plan, now: float | None = None) -> int:
    """Older versions kept longer than the plan says are deleted (only the dated folders the backup made)."""
    now = time.time() if now is None else now
    gone = 0
    for v in versions(plan):
        try:
            when = time.mktime(time.strptime(v.name[:16], STAMP))
        except ValueError:
            continue
        if now - when > max(1, plan.keep_days) * 86400:
            shutil.rmtree(v, ignore_errors=True)
            gone += 1
    return gone


def finished(plan: Plan, summary: Summary, now: float | None = None) -> None:
    """What the plan keeps of a run (shown on the page). A run that stopped before the end doesn't count as the
    last backup: the next chance to back up by itself tries again."""
    plan.last_problem = summary.problem
    if summary.problem:
        return
    plan.last = time.time() if now is None else now
    plan.last_files, plan.last_bytes, plan.last_failed = summary.copied, summary.bytes, len(summary.failed)
