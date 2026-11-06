"""Check: is a file the real one? Its fingerprints (SHA-256, SHA-1, MD5, SHA-512), worked out in one read, compared
with a code pasted from a download page, with another file, or with a list of codes (SHA256SUMS, file.sha256...).
No Qt.

A fingerprint ("checksum", "hash") is a short code worked out from every byte of a file: a file changed by one byte
has a completely different one. Download pages show the code their file should have."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

ALGOS = ("sha256", "sha1", "md5", "sha512")
NAMES = {"sha256": "SHA-256", "sha1": "SHA-1", "md5": "MD5", "sha512": "SHA-512"}
BY_LENGTH = {64: "sha256", 40: "sha1", 32: "md5", 128: "sha512"}   # (hex digits of each)
CHUNK = 4 << 20
LIST_EXTS = {".sha256": "sha256", ".sha1": "sha1", ".md5": "md5", ".sha512": "sha512", ".sha256sum": "sha256",
             ".sha1sum": "sha1", ".md5sum": "md5", ".sha512sum": "sha512"}
LIST_NAMES = re.compile(r"^(sha(1|256|512)|md5)sums?(\.txt)?$|^checksums?(\.txt|\.sha256)?$", re.IGNORECASE)
MAX_LIST = 1 << 20      # (a list of codes bigger than 1 MB isn't one)
MAX_ENTRIES = 5000

# what a row of a list says
SAME, DIFFERENT, MISSING, UNREADABLE = "same", "different", "missing", "unreadable"


class Cancelled(Exception):
    pass


def fingerprints(path: Path, progress=None, cancel=None, algos=ALGOS) -> dict[str, str]:
    """{"sha256": hex, ...} of the file, every one in one read. progress(fraction) now and then."""
    hashes = {a: hashlib.new(a) for a in algos}
    size = path.stat().st_size or 1
    done = 0
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            for h in hashes.values():
                h.update(chunk)
            done += len(chunk)
            if progress is not None:
                progress(min(1.0, done / size))
    return {a: h.hexdigest() for a, h in hashes.items()}


def clean(text: str) -> str:
    """A pasted code as plain lower-case hex: spaces, colons and dashes taken out ("AB:CD..." and "ab cd ..."
    are both seen on download pages); "" when it isn't one."""
    text = (text or "").strip()
    m = re.search(r"(?:sha-?(?:1|256|512)|md5)\s*[:=]?\s*(\S.*)$", text, re.IGNORECASE)
    if m:  # ("SHA256: abc...": the part after the name)
        text = m.group(1)
    code = re.sub(r"[\s:\-]", "", text).lower()
    if code and re.fullmatch(r"[0-9a-f]+", code):
        return code
    # a whole line copied from a list of codes ("code  setup.exe", "SHA256 (setup.exe) = code"): the code in it
    for word in re.findall(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32,128}(?![0-9a-fA-F])", text):
        if len(word) in BY_LENGTH:
            return word.lower()
    return ""


def kind_of(code: str) -> str | None:
    """Which fingerprint a code is, by its length (None: not one of them)."""
    return BY_LENGTH.get(len(code))


def matches(found: dict[str, str], pasted: str) -> tuple[str | None, bool | None]:
    """(the fingerprint the pasted code is, whether it's this file's): (None, None) when it isn't a code."""
    code = clean(pasted)
    algo = kind_of(code)
    if algo is None:
        return None, None
    have = found.get(algo)
    return algo, (have is not None and have == code)


def same_file(a: Path, b: Path, progress=None, cancel=None) -> bool:
    """Whether two files have exactly the same bytes (sizes first, then SHA-256)."""
    if a.stat().st_size != b.stat().st_size:
        return False
    one = fingerprints(a, (lambda p: progress(p / 2)) if progress else None, cancel, ("sha256",))
    two = fingerprints(b, (lambda p: progress(0.5 + p / 2)) if progress else None, cancel, ("sha256",))
    return one == two


def is_list(path: Path) -> bool:
    """Whether a file is a list of codes (file.sha256, SHA256SUMS, checksums.txt...)."""
    name = path.name
    return path.suffix.lower() in LIST_EXTS or bool(LIST_NAMES.match(name))


_LINE_GNU = re.compile(r"^\\?([0-9a-fA-F]{32,128})\s+[ *]?(.+?)\s*$")             # "code  name" / "code *name"
_LINE_BSD = re.compile(r"^(SHA-?(?:1|256|512)|MD5)\s*\((.+)\)\s*=\s*([0-9a-fA-F]{32,128})\s*$", re.IGNORECASE)


@dataclass
class Entry:
    name: str           # as the list writes it (a name, or a path inside the list's folder)
    code: str
    algo: str
    state: str = ""     # SAME, DIFFERENT or MISSING once checked
    path: Path | None = None


def read_list(path: Path) -> list[Entry]:
    """The files a list of codes names, with their codes. A list for one file can hold the code alone (then it's
    for the file of the same name without the list's extension: setup.exe.sha256 → setup.exe)."""
    if path.stat().st_size > MAX_LIST:
        return []
    text = path.read_bytes().decode("utf-8-sig", "replace")
    out: list[Entry] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        m = _LINE_BSD.match(line)
        if m:
            code = m.group(3).lower()
            algo = kind_of(code)
            if algo:
                out.append(Entry(m.group(2).strip(), code, algo))
        else:
            m = _LINE_GNU.match(line)
            if m:
                code = m.group(1).lower()
                algo = kind_of(code)
                if algo:
                    out.append(Entry(m.group(2).strip(), code, algo))
            else:
                code = clean(line)
                algo = kind_of(code)
                if algo and path.suffix.lower() in LIST_EXTS and not out:
                    out.append(Entry(path.stem, code, algo))
        if len(out) >= MAX_ENTRIES:
            break
    return out


def where_is(folder: Path, name: str) -> Path | None:
    """The file a list names, inside the list's own folder only (never "..\\", never a full path elsewhere)."""
    name = name.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    if not name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    target = (folder / name).resolve()
    try:
        target.relative_to(folder.resolve())
    except ValueError:
        return None
    return target


def check_list(path: Path, progress=None, cancel=None) -> list[Entry]:
    """Every file the list names, checked: SAME, DIFFERENT or MISSING. progress(fraction, name)."""
    entries = read_list(path)
    folder = path.parent
    todo = []
    for e in entries:
        e.path = where_is(folder, e.name)
        try:
            size = e.path.stat().st_size if e.path is not None and e.path.is_file() else None
        except OSError:
            size = None
        if size is None:
            e.state = MISSING
        else:
            todo.append((e, size))
    total = sum(size for _e, size in todo) or 1
    done = 0
    for e, size in todo:

        def step(p, e=e, base=done, size=size):
            if progress is not None:
                progress(min(1.0, (base + p * size) / total), e.name)
        try:
            found = fingerprints(e.path, step, cancel, (e.algo,))
        except OSError:  # (open in another app, or not allowed: the others are still checked)
            e.state = UNREADABLE
        else:
            e.state = SAME if found[e.algo] == e.code else DIFFERENT
        done += size
    return entries
