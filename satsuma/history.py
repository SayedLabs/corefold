"""Recent conversions and lifetime stats, saved in %APPDATA%\\Satsuma\\history.json."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .settings import CONFIG_DIR

log = logging.getLogger("satsuma")
MAX_ITEMS = 200


@dataclass
class Entry:
    when: float
    tool: str            # description, e.g. "Convert to JPG"
    inputs: list[str]    # file names
    outputs: list[str]   # full paths
    errors: int = 0


@dataclass
class Stats:
    files: int = 0
    jobs: int = 0
    bytes_saved: int = 0
    since: float = field(default_factory=time.time)


def size_of(p: Path) -> int:
    """Size of a file (folders count as 0 here, so stats never walk a huge folder)."""
    try:
        return 0 if p.is_dir() else p.stat().st_size
    except OSError:
        return 0


class History:
    def __init__(self, path: Path | None = None):
        self.path = path or CONFIG_DIR / "history.json"
        self.items: list[Entry] = []
        self.stats = Stats()
        self.load()

    def load(self):
        try:
            data = json.loads(self.path.read_text("utf-8"))
            self.items = [Entry(**e) for e in data.get("items", [])][:MAX_ITEMS]
            self.stats = Stats(**data.get("stats", {}))
        except (OSError, ValueError, TypeError):
            pass

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {"items": [asdict(e) for e in self.items], "stats": asdict(self.stats)}
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), "utf-8")
            os.replace(tmp, self.path)  # never leaves a half-written file
        except OSError:
            log.exception("could not save history")

    def record(self, tool: str, inputs: list[Path], outputs: list[Path], errors: int, bytes_in: int):
        if not outputs and not errors:
            return
        done = max(0, len(inputs) - errors)
        self.items.insert(0, Entry(time.time(), tool, [p.name for p in inputs], [str(p) for p in outputs], errors))
        del self.items[MAX_ITEMS:]
        self.stats.files += done
        self.stats.jobs += 1
        bytes_out = sum(size_of(p) for p in outputs)
        if outputs and bytes_out < bytes_in:
            self.stats.bytes_saved += bytes_in - bytes_out
        self.save()

    def clear(self):
        self.items.clear()
        self.save()

    def reset_stats(self):
        self.stats = Stats()
        self.save()
