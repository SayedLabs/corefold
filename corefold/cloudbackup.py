"""Corefold Cloud: folder backups. A folder on this PC copied into a cloud account (online, a server of your own, or
a cloud folder on this PC), by itself every day or every week, or when asked. One way only: what's new or changed
on this PC goes up; nothing in the account is ever deleted, and nothing on this PC is ever touched. A file that
changed goes up first, and only once all of it arrived does it take the old copy's place. No Qt here."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import cloud

log = logging.getLogger("corefold")

BACKUPS_FILE = cloud.FOLDER / "backups.json"
EVERY = {"day": 86400, "week": 7 * 86400, "never": 0}
SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
_lock = threading.RLock()


@dataclass
class Backup:
    source: str                         # the folder on this PC
    account: str                        # cloud.Account.key
    target: list = field(default_factory=list)  # folder names from the account's top to where it goes
    every: str = "day"                  # EVERY
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    last: float = 0.0                   # when it last finished (all of it)
    tried: float = 0.0                  # when it last ran
    result: str = ""                    # what the last run did, in a few words (shown on the list)
    sent: dict = field(default_factory=dict)  # relative path -> [size, modified] of what's up there already

    @property
    def name(self) -> str:
        return Path(self.source).name or self.source


def load() -> list[Backup]:
    with _lock:
        try:
            data = json.loads(BACKUPS_FILE.read_text("utf-8"))
        except (OSError, ValueError):
            return []
    out = []
    for d in data if isinstance(data, list) else []:
        try:
            b = Backup(**{k: v for k, v in d.items() if k in Backup.__dataclass_fields__})
        except TypeError:
            continue
        if isinstance(b.source, str) and isinstance(b.account, str) and isinstance(b.target, list) and \
                isinstance(b.sent, dict) and b.every in EVERY:
            b.target = [str(t) for t in b.target]
            out.append(b)
    return out


def save(backups: list[Backup]):
    with _lock:
        BACKUPS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = BACKUPS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(b) for b in backups], ensure_ascii=False), "utf-8")
        os.replace(tmp, BACKUPS_FILE)


def add(source: Path, account: cloud.Account, target: list[str], every: str = "day") -> Backup:
    src = Path(os.path.abspath(str(source)))
    if not src.is_dir():
        raise cloud.CloudError("it isn't there any more")
    if account.local and _inside(account.local, str(src)) or account.local and _inside(str(src), account.local):
        raise cloud.CloudError(cloud.INTO_ITSELF)  # (a cloud folder of this PC inside what's backed up, or the reverse)
    b = Backup(str(src), account.key, list(target), every if every in EVERY else "day")
    with _lock:
        save([x for x in load() if not (x.source == b.source and x.account == b.account and x.target == b.target)]
             + [b])
    return b


def remove(backup_id: str):
    with _lock:
        save([b for b in load() if b.id != backup_id])


def _update(b: Backup):
    """Keeps what a run did (a backup removed while it ran stays removed)."""
    with _lock:
        backups = load()
        if any(x.id == b.id for x in backups):
            save([b if x.id == b.id else x for x in backups])


def _inside(path: str, folder: str) -> bool:
    p, f = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


def due(b: Backup, now: float | None = None) -> bool:
    every = EVERY.get(b.every, 0)
    now = time.time() if now is None else now
    # (a run that failed is tried again after an hour, not after a whole day)
    return bool(every) and now - b.last >= every and now - b.tried >= 3600


def find_account(key: str) -> cloud.Account | None:
    for acc in cloud.load_accounts():
        if acc.key == key:
            return acc
    try:
        from . import cloudlocal
        for acc in cloudlocal.find():
            if acc.key == key:
                return acc
    except Exception:
        log.debug("cloud folders on this PC couldn't be looked for", exc_info=True)
    return None


def _files(src: Path, cancel=None) -> list[tuple[str, Path, int, float]]:
    """(relative path with "/", path, size, modified) of every file to back up. Links and junctions aren't
    followed (they lead out of the folder, or round in a circle); files kept online only aren't downloaded."""
    from .core import is_link
    out = []
    stack = [src]
    while stack:
        if cancel is not None and cancel.is_set():
            raise cloud.CloudError("cancelled")
        folder = stack.pop()
        try:
            items = list(os.scandir(folder))
        except OSError:
            continue
        for d in items:
            try:
                p = Path(d.path)
                if is_link(p) or d.name.lower() in SKIP_NAMES or d.name.endswith(".corefold-part"):
                    continue
                st = d.stat(follow_symlinks=False)
                attrs = getattr(st, "st_file_attributes", 0)
                if attrs & 0x00440000 or attrs & 0x1000:  # (online only: recall on open/access, or offline)
                    continue
                if d.is_dir(follow_symlinks=False):
                    stack.append(p)
                else:
                    out.append((p.relative_to(src).as_posix(), p, st.st_size, st.st_mtime))
            except (OSError, ValueError):
                continue
    return sorted(out)


class _Tree:
    """The backup's folders in the account, listed when first needed and made when missing."""

    def __init__(self, c: cloud.Client, top: cloud.Entry | None):
        self.c = c
        self.folders: dict[str, cloud.Entry | None] = {"": top}
        self.listed: dict[str, dict[str, cloud.Entry]] = {}

    def items(self, rel: str) -> dict[str, cloud.Entry]:
        if rel not in self.listed:
            folder = self.folders[rel]
            self.listed[rel] = {e.name.lower(): e for e in self.c.list(folder)} if (folder is not None or rel == "") \
                else {}
        return self.listed[rel]

    def folder(self, rel: str) -> cloud.Entry | None:
        if rel in self.folders:
            return self.folders[rel]
        parent_rel, _, name = rel.rpartition("/")
        parent = self.folder(parent_rel)
        have = self.items(parent_rel).get(name.lower())
        if have is None or not have.folder:
            have = self.c.new_folder(name, parent)
            self.items(parent_rel)[have.name.lower()] = have
            self.listed[rel] = {}
        self.folders[rel] = have
        return have


def _top(c: cloud.Client, target: list[str]) -> cloud.Entry | None:
    """The folder the backup goes into (made when it isn't there)."""
    here = None
    for name in target:
        found = next((e for e in c.list(here) if e.folder and e.name.lower() == name.lower()), None)
        here = found or c.new_folder(name, here)
    return here


def run(b: Backup, cancel: threading.Event | None = None, progress=None) -> dict:
    """One backup run. Returns {"sent": files that went up, "same": files already there, "failed": [names]}.
    Each file that went up is noted at once, so a run cut off half way goes on where it stopped next time."""
    b.tried = time.time()
    src = Path(b.source)
    out = {"sent": 0, "same": 0, "failed": []}
    try:
        if not src.is_dir():
            raise cloud.CloudError("it isn't there any more")
        acc = find_account(b.account)
        if acc is None:
            raise cloud.CloudError("this account isn't in Corefold any more")
        c = cloud.client(acc)
        files = _files(src, cancel)
        tree = _Tree(c, _top(c, b.target))
        total = sum(f[2] for f in files) or 1
        done = 0
        last_save = time.monotonic()
        for rel, path, size, modified in files:
            if cancel is not None and cancel.is_set():
                raise cloud.CloudError("cancelled")
            folder_rel, _, name = rel.rpartition("/")
            had = b.sent.get(rel)
            try:
                folder = tree.folder(folder_rel)
                there = tree.items(folder_rel).get(name.lower())
                if had == [size, modified] and there is not None and not there.folder and \
                        (there.size == size or there.size == 0):
                    out["same"] += 1
                else:
                    new = c.upload(path, folder, None if progress is None else
                                   lambda d, t, base=done, r=rel: progress((r, base + d, total)), cancel)
                    if there is not None and not there.folder and new.name.lower() != name.lower():
                        # (the changed file arrived whole under another name: only now does it take the old one's
                        # place; the old copy goes to the account's deleted files where it has them)
                        c.delete(there)
                        new = c.rename(new, there.name)
                    tree.items(folder_rel)[new.name.lower()] = new
                    b.sent[rel] = [size, modified]
                    out["sent"] += 1
            except cloud.SignInNeeded:
                raise
            except cloud.CloudError as e:
                if str(e) == "cancelled":
                    raise
                log.info("backup: %s couldn't go up: %s", rel, e)
                out["failed"].append(rel)
            except OSError as e:  # (a file another program holds, or Windows didn't let it be read)
                log.info("backup: %s couldn't be read: %s", rel, e)
                out["failed"].append(rel)
            done += size
            if progress is not None:
                progress((rel, done, total))
            if time.monotonic() - last_save > 30:
                _update(b)
                last_save = time.monotonic()
        live = {f[0] for f in files}
        b.sent = {k: v for k, v in b.sent.items() if k in live}
        if not out["failed"]:
            b.last = time.time()
        b.result = "ok" if not out["failed"] else f"failed:{len(out['failed'])}"
    except cloud.CloudError as e:
        b.result = "error:" + str(e)
        out["error"] = e
    finally:
        _update(b)
    return out


def run_due(cancel: threading.Event | None = None) -> list[tuple[Backup, dict]]:
    """Every backup whose day or week has gone by (what the app's hourly timer calls)."""
    ran = []
    for b in load():
        if cancel is not None and cancel.is_set():
            break
        if due(b):
            ran.append((b, run(b, cancel)))
    return ran
