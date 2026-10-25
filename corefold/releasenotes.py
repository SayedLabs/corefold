"""What changed in each version: the notes kept with the app (assets/release-notes, the same words as the
release's page on GitHub), and the notes of a new version as GitHub gives them before it's installed.

Notes are only ever shown as plain text: Markdown marks, links and any HTML in them are reduced to their
words, so nothing in them is run, loaded or opened. No GUI code here.

A release's notes file (assets/release-notes/<version>.md, and <version>.<lang>.md in another language):
    date: 2026-10-24
    tag: v1.10.0          (only when the release's tag isn't "v" and the version)

    A heading
    - a change
    - another change
"""
from __future__ import annotations

import html
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

MAX_TEXT = 20_000          # (a release page's words: more than any real notes, never a whole book)
MAX_LINE = 600
_VERSION = re.compile(r"^\d{1,4}(\.\d{1,6}){1,3}$")


@dataclass
class Part:
    heading: str
    lines: list[str] = field(default_factory=list)   # each change, plain text


@dataclass
class Notes:
    version: str
    date: str = ""            # "2026-10-24", or "" if it isn't known
    tag: str = ""             # the release's tag on GitHub ("v1.10.0")
    parts: list[Part] = field(default_factory=list)

    def empty(self) -> bool:
        return not any(p.heading or p.lines for p in self.parts)


def folder() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / "release-notes"


def version_key(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in version.split("."))
    except ValueError:
        return ()


def plain(text: str) -> str:
    """One line of notes as plain words: HTML tags go, entities become letters, Markdown marks and links
    are reduced to their words (a link's address is dropped)."""
    text = re.sub(r"<[^>]{0,500}>", "", text)
    text = html.unescape(text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)      # pictures: only their words
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)       # links: only their words
    text = re.sub(r"(\*\*|__|`)", "", text)
    text = re.sub(r"(?<!\w)[*_](\S[^*_]*?)[*_](?!\w)", r"\1", text)
    text = "".join(ch for ch in text if ch == "\t" or ch >= " ")  # (no control characters)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_LINE]


def parse(text: str, version: str, date: str = "") -> Notes:
    """Notes from a release's words (the file kept with the app, or GitHub's release text)."""
    notes = Notes(version, date, "v" + version)
    part = None
    for raw in (text or "")[:MAX_TEXT].replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^date:\s*(\d{4}-\d{2}-\d{2})\s*$", line, re.I)
        if m and not notes.parts:
            notes.date = m.group(1)
            continue
        m = re.match(r"^tag:\s*(v?[\d.]{3,20})\s*$", line, re.I)
        if m and not notes.parts:
            notes.tag = m.group(1)
            continue
        bullet = re.match(r"^([-*+•]|\d{1,3}[.)])\s*(.*)$", line)
        if bullet:
            words = plain(bullet.group(2))
            if not words:
                continue  # (an empty "-")
            if part is None:
                part = Part("")
                notes.parts.append(part)
            part.lines.append(words)
            continue
        marked = line.startswith("#")
        words = plain(line.lstrip("#").strip())
        if not words or re.fullmatch(r"(Corefold\s+)?v?" + re.escape(version), words, re.I):
            continue  # (the version's own name as a heading: shown anyway)
        if marked or (len(words) <= 60 and not words.endswith((".", "!", "?", ":"))):
            part = Part(words)  # a heading ("## Fixes", or a short line of its own like "Capture")
            notes.parts.append(part)
        else:  # a sentence between the changes: kept as one of them
            if part is None:
                part = Part("")
                notes.parts.append(part)
            part.lines.append(words)
    return notes


def kept(version: str, language: str = "en") -> Notes | None:
    """The notes kept with the app for a version, in this language if they were written in it."""
    if not _VERSION.match(version or ""):
        return None
    base = folder()
    for name in ([f"{version}.{language}.md"] if language and language != "en" else []) + [f"{version}.md"]:
        try:
            text = (base / name).read_text("utf-8")
        except OSError:
            continue
        notes = parse(text, version)
        if name != f"{version}.md":  # (the date and tag are only written in the English notes)
            try:
                first = parse((base / f"{version}.md").read_text("utf-8"), version)
                notes.date, notes.tag = notes.date or first.date, first.tag
            except OSError:
                pass
        return notes
    return None


def history(language: str = "en") -> list[Notes]:
    """Every version's notes kept with the app, newest first."""
    try:
        names = {p.name.split(".md")[0] for p in folder().glob("*.md")}
    except OSError:
        return []
    versions = sorted({n for n in names if _VERSION.match(n)}, key=version_key, reverse=True)
    return [n for n in (kept(v, language) for v in versions) if n is not None]


def release_page(repo: str, notes: Notes) -> str:
    """The version's official page on GitHub (only for the app's own repository and a plain tag)."""
    tag = notes.tag or "v" + notes.version
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or "") or not re.fullmatch(r"v?[\d.]{3,20}", tag):
        return ""
    return f"https://github.com/{repo}/releases/tag/{tag}"
