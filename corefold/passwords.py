"""Archive passwords Corefold may remember, so an archive locked with one used before opens without
asking. Off unless the person ticks "Remember it" when typing one. The list is locked with Windows'
own encryption for this Windows account (like the Cloud keys) and never leaves the PC."""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from .settings import CONFIG_DIR

log = logging.getLogger("corefold")

FILE = CONFIG_DIR / "archive-passwords.bin"
MAX = 30
_lock = threading.Lock()


def load() -> list[str]:
    from .cloud import unprotect
    try:
        data = json.loads(unprotect(FILE.read_bytes()).decode("utf-8") or "[]")
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [p for p in data if isinstance(p, str) and p][:MAX]


def _save(passwords: list[str]):
    from .cloud import protect
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".part")
    tmp.write_bytes(protect(json.dumps(passwords).encode("utf-8")))
    tmp.replace(FILE)


def add(password: str):
    """Remembers a password that worked (the newest first)."""
    if not password:
        return
    with _lock:
        try:
            _save([password] + [p for p in load() if p != password][:MAX - 1])
        except OSError:
            log.warning("couldn't remember the archive password", exc_info=True)


def forget_all():
    with _lock:
        FILE.unlink(missing_ok=True)


def count() -> int:
    return len(load())


def find(src: Path) -> str | None:
    """A remembered password that opens this archive, or None."""
    from .archives import password_works
    for p in load():
        try:
            if password_works(src, p):
                return p
        except Exception:
            log.debug("trying a remembered password failed", exc_info=True)
    return None
