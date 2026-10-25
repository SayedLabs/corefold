"""Recent conversions and lifetime stats, saved in %APPDATA%\\Corefold\\history.json."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .core import category
from .settings import CONFIG_DIR

log = logging.getLogger("corefold")
MAX_ITEMS = 200
KEEP = 20  # names remembered per entry


@dataclass
class Entry:
    when: float
    tool: str            # description, e.g. "Convert to JPG"
    inputs: list[str]    # file names (the first few)
    outputs: list[str]   # full paths (the first few)
    errors: int = 0
    input_count: int = 0   # how many there were in total
    output_count: int = 0


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
        self.unreadable = False
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except FileNotFoundError:
            return
        except OSError:  # (there but held by another program: never written over with an empty history)
            log.warning("couldn't read the history", exc_info=True)
            self.unreadable = True
            return
        except ValueError:
            return
        try:
            fields = set(Entry.__dataclass_fields__)
            self.items = [Entry(**{k: v for k, v in e.items() if k in fields}) for e in data.get("items", [])
                          if isinstance(e, dict)][:MAX_ITEMS]
            self.stats = Stats(**data.get("stats", {}))
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save(self):
        if self.unreadable:  # (read again first: this run's entries go on top of what the file has)
            mine, stats = self.items, self.stats
            self.items, self.stats = [], Stats()
            self.load()
            if self.unreadable:
                self.items, self.stats = mine, stats
                return
            self.items = (mine + self.items)[:MAX_ITEMS]
            self.stats.files += stats.files
            self.stats.jobs += stats.jobs
            self.stats.bytes_saved += stats.bytes_saved
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
        # only the first few names are kept: a job with 2,000 photos would make the file huge
        self.items.insert(0, Entry(time.time(), tool, [p.name for p in inputs[:KEEP]], [str(p) for p in outputs[:KEEP]],
                                   errors, len(inputs), len(outputs)))
        del self.items[MAX_ITEMS:]
        self.stats.files += done
        self.stats.jobs += 1
        bytes_out = sum(size_of(p) for p in outputs)
        # only files count: extracting an archive makes a folder, which isn't "space saved". And only when every
        # file came out as the same kind of file (a smaller photo, a smaller PDF): a song taken out of a 2 GB
        # video, or a job where some files failed, isn't space saved either
        if outputs and not errors and all(p.is_file() for p in outputs) and bytes_out < bytes_in \
                and {category(p) for p in outputs} <= {category(p) for p in inputs}:
            self.stats.bytes_saved += bytes_in - bytes_out
        self.save()

    def clear(self):
        if self.unreadable:
            self.save()  # (what the file has comes back first, so it's cleared too)
        self.items.clear()
        self.save()

    def reset_stats(self):
        if self.unreadable:
            self.save()
        self.stats = Stats()
        self.save()
