"""Rename: many files at once, by a few plain rules, with every new name shown before anything changes.

plan() works out the new names (nothing is renamed there); the renaming itself is moretools.rename_files (in two
steps through temporary names, put back as it was if anything fails, and undone from History). What a rule needs
from inside a file (a photo's date, a song's title) is read once and kept in `facts`, in the background. No Qt.

The rules, in the order they're applied (params):
  name     "keep" | "new" (base) | "song" (artist - title, from the song's details)
  find, replace, match_case, pattern (find is a regular expression)
  case     "keep" | "lower" | "upper" | "title" | "sentence"
  before, after          words added before and after the name
  date     "none" | "taken" (a photo's own date, else changed) | "changed";  date_at "before" | "after"
  number   "none" | "before" | "after";  start, step, digits (0: as many as needed)
  ext      "keep" | "lower" | "upper"
  order    "name" | "date" | "size" | "added": the order the numbers go in
"""
from __future__ import annotations

import datetime
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .core import WINDOWS_RESERVED

BAD_CHARS = '<>:"/\\|?*'
MAX_NAME = 240                   # (Windows allows 255 for a name; a little room is kept)
DATE_FORMAT = "%Y-%m-%d"
SEP = " "                        # between the name and what's added to it (a number, a date)

DEFAULTS = {"name": "keep", "base": "", "find": "", "replace": "", "match_case": False, "pattern": False,
            "case": "keep", "before": "", "after": "", "date": "none", "date_at": "before", "number": "none",
            "start": 1, "step": 1, "digits": 0, "ext": "keep", "order": "name"}

# what a row says about its new name
SAME, OK, TAKEN, TWICE, BAD, EMPTY = "same", "ok", "taken", "twice", "bad", "empty"


@dataclass
class Row:
    old: Path
    new: Path
    state: str        # SAME, OK, TAKEN, TWICE, BAD or EMPTY
    why: str = ""     # (BAD: what's wrong with the name, in English for tr())


def natural(path: Path):
    """"2.jpg" before "10.jpg", like File Explorer."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def needs(params: dict) -> set[str]:
    """What has to be read from inside the files for these rules: "taken" and/or "song"."""
    p = {**DEFAULTS, **params}
    out = set()
    if p["date"] == "taken" or p["order"] == "date":
        out.add("taken")
    if p["name"] == "song":
        out.add("song")
    return out


def read_facts(path: Path, what: set[str]) -> dict:
    """A photo's date (as a timestamp) and a song's artist and title, as far as the file has them."""
    facts: dict = {}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    facts["changed"] = mtime
    if "taken" in what:
        from .organizer import taken_on
        facts["taken"] = taken_on(path, path.suffix.lower(), mtime)
    if "song" in what:
        from .audiotags import TAG_EXT, read_tags
        if path.suffix.lower() in TAG_EXT:
            tags = read_tags(path)
            facts["artist"] = tags.get("artist", "")
            facts["title"] = tags.get("title", "")
    return facts


def pattern_problem(params: dict) -> str:
    """When "find" is a regular expression that can't be read: why (else "")."""
    p = {**DEFAULTS, **params}
    if not (p["pattern"] and p["find"]):
        return ""
    try:
        re.compile(p["find"])
    except re.error as e:
        return str(e)
    return ""


def _case(text: str, how: str) -> str:
    if how == "lower":
        return text.lower()
    if how == "upper":
        return text.upper()
    if how == "title":
        return re.sub(r"[^\W_]+(?:'[^\W_]+)?", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), text)
    if how == "sentence":
        low = text.lower()
        for i, ch in enumerate(low):
            if ch.isalpha():
                return low[:i] + ch.upper() + low[i + 1:]
        return low
    return text


def _when(path: Path, facts: dict, kind: str) -> str:
    stamp = facts.get("taken" if kind == "taken" else "changed")
    if stamp is None:
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return ""
    try:
        return datetime.datetime.fromtimestamp(stamp).strftime(DATE_FORMAT)
    except (OverflowError, OSError, ValueError):
        return ""


REASONS = ("The new name is empty", 'A name can\'t have < > : " / \\ | ? *', "A name can't end with a space or a dot",
           "Windows keeps this name for itself", "The new name is too long")


def name_problem(name: str) -> str:
    """Why Windows wouldn't take this name (English, for tr()), or ""."""
    if not name.strip(" ."):
        return "The new name is empty"
    if any(c in BAD_CHARS or ord(c) < 32 for c in name):
        return 'A name can\'t have < > : " / \\ | ? *'
    if name != name.rstrip(" ."):
        return "A name can't end with a space or a dot"
    if name.split(".")[0].upper() in WINDOWS_RESERVED:
        return "Windows keeps this name for itself"
    if len(name) > MAX_NAME:
        return "The new name is too long"
    return ""


def ordered(files: list[Path], params: dict, facts: dict[Path, dict] | None = None) -> list[Path]:
    """The files in the order their numbers go in."""
    order = {**DEFAULTS, **params}["order"]
    facts = facts or {}
    if order == "added":
        return list(files)
    if order == "size":
        def size(f):
            try:
                return f.stat().st_size
            except OSError:
                return 0
        return sorted(files, key=lambda f: (size(f), natural(f)))
    if order == "date":
        return sorted(files, key=lambda f: (facts.get(f, {}).get("taken", facts.get(f, {}).get("changed", 0)) or 0,
                                            natural(f)))
    return sorted(files, key=natural)


def new_name(path: Path, index: int, params: dict, facts: dict | None = None) -> str:
    """The new name of one file (the index-th in the numbers' order)."""
    p = {**DEFAULTS, **params}
    facts = facts or {}
    folder = path.is_dir()
    stem, ext = (path.name, "") if folder else (path.stem, path.suffix)
    if p["name"] == "new" and p["base"].strip():
        stem = p["base"].strip()
    elif p["name"] == "song":
        artist, title = facts.get("artist", ""), facts.get("title", "")
        if title:
            stem = f"{artist} - {title}" if artist else title
    if p["find"]:
        if p["pattern"]:
            try:
                stem = re.sub(p["find"], p["replace"], stem, flags=0 if p["match_case"] else re.IGNORECASE)
            except (re.error, IndexError):
                pass  # (said on the page: the list shows the names as they are until it's fixed)
        elif p["match_case"]:
            stem = stem.replace(p["find"], p["replace"])
        else:
            stem = re.sub(re.escape(p["find"]), lambda _m: p["replace"], stem, flags=re.IGNORECASE)
    stem = _case(stem, p["case"])
    stem = f"{p['before']}{stem}{p['after']}"
    if p["date"] in ("taken", "changed"):
        when = _when(path, facts, p["date"])
        if when:
            stem = f"{when}{SEP}{stem}" if p["date_at"] == "before" else f"{stem}{SEP}{when}"
    if p["number"] in ("before", "after"):
        value = int(p["start"]) + index * max(1, int(p["step"]))
        text = f"{value:0{max(0, int(p['digits']))}d}"
        stem = f"{text}{SEP}{stem}" if p["number"] == "before" else f"{stem}{SEP}{text}"
    if p["ext"] == "lower":
        ext = ext.lower()
    elif p["ext"] == "upper":
        ext = ext.upper()
    return stem.strip() + ext


def plan(files: list[Path], params: dict, facts: dict[Path, dict] | None = None) -> list[Row]:
    """Every file with its new name and whether it can have it, in the numbers' order. Nothing is renamed."""
    p = {**DEFAULTS, **params}
    facts = facts or {}
    files = ordered(files, p, facts)
    if p["number"] != "none" and not int(p["digits"]):
        last = int(p["start"]) + max(0, len(files) - 1) * max(1, int(p["step"]))
        p["digits"] = len(str(abs(last)))
    rows = []
    for i, f in enumerate(files):
        name = new_name(f, i, p, facts.get(f))
        why = name_problem(name)
        if not why and f.suffix and name.lower() == f.suffix.lower():
            why = "The new name is empty"   # (only ".jpg" left: Explorer would show a file with no name)
            name = ""
        try:
            new = f.with_name(name) if name.strip(" .") else f
        except ValueError:   # (a / in the name: the list shows the name as it is, with why it can't be)
            new = f
        rows.append(Row(f, new, OK))
        if why:
            rows[-1].state, rows[-1].why = (EMPTY if not name.strip(" .") else BAD), why
    olds = {os.path.normcase(str(r.old)) for r in rows}
    seen: dict[str, Row] = {}
    for r in rows:
        if r.state in (BAD, EMPTY):
            continue
        if str(r.new) == str(r.old):  # (compared as text: "photo" → "Photo" is a change on Windows too)
            r.state = SAME
        key = os.path.normcase(str(r.new))
        if key in seen:
            r.state = TWICE
            if seen[key].state in (OK, SAME):
                seen[key].state = TWICE
        else:
            seen[key] = r
            if r.state == OK and key not in olds and os.path.lexists(r.new):
                r.state = TAKEN
    return rows


def pairs(rows: list[Row]) -> list[tuple[Path, Path]]:
    """What can be renamed: [(old, new)], only when no row has a problem."""
    if any(r.state in (TAKEN, TWICE, BAD, EMPTY) for r in rows):
        return []
    return [(r.old, r.new) for r in rows if r.state == OK]


def files_in(folder: Path, limit: int = 20000) -> list[Path]:
    """The files right inside a folder (not its subfolders, not hidden ones), in Explorer's order."""
    out = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                try:
                    if e.is_file(follow_symlinks=False) and not e.name.startswith(".") and \
                            not getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x2:
                        out.append(Path(e.path))
                except OSError:
                    continue
                if len(out) >= limit:
                    break
    except OSError:
        return []
    return sorted(out, key=natural)
