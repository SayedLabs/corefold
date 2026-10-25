"""The Home page's widgets: what kinds there are, the sizes they come in, the ones a new Home starts with,
and where each one goes on the page. Only lists and arithmetic (no Qt), so the settings can check what's
saved without loading any widget; the widgets themselves are in homewidgets.py.

A kind belongs to a tool: it's offered, and shown, only while its tool is in use (tools.is_on), so a tool
switched off or resting for now loads nothing on the Home page either. Its place is kept in the settings,
and it comes back with its tool. Texts are English and shown through tr()."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

# the sizes, in the page's grid: (columns, rows). A wide one takes the whole row, whatever fits.
SIZES = {"small": (1, 1), "medium": (2, 1), "large": (2, 2), "wide": (4, 1)}
SIZE_NAMES = {"small": "Small", "medium": "Medium", "large": "Large", "wide": "Wide"}
COLUMNS = 4         # the grid's columns when the window is wide enough
NARROW_COLUMNS = 2  # and when it isn't
MAX_WIDGETS = 30
MAX_ITEMS = 12      # shortcuts in one Shortcuts widget


@dataclass(frozen=True)
class Kind:
    id: str
    tool: str           # its tool's id ("" = always there)
    name: str
    line: str           # one line about it (in the gallery)
    icon: str           # icons.py
    sizes: tuple        # the sizes it comes in, the usual one first
    many: bool = False  # can be on the page more than once (a shortcut)


KINDS: dict[str, Kind] = {k.id: k for k in (
    Kind("convert", "converter", "Convert files", "Drop files on it and pick what to make of them.", "arrow_swap",
         ("medium", "small", "large", "wide")),
    Kind("stats", "converter", "What Corefold did", "How many files were converted, and the space saved.",
         "data_pie", ("small", "medium")),
    Kind("pdf", "pdf", "PDF tools", "Edit, merge, split and shrink PDFs in one click.", "document_pdf",
         ("medium", "small", "large", "wide")),
    Kind("archive", "archive", "Archives", "Open an archive, or pack files into a ZIP.", "archive",
         ("small", "medium", "large")),
    Kind("organizer", "organizer", "Tidy a folder", "Sort Downloads or the Desktop into folders, with how many "
         "files wait.", "folder", ("medium", "small")),
    Kind("unlock", "unlock", "What's using this?", "Drop a file you can't delete or move to see which program "
         "holds it.", "lock_open", ("small", "medium")),
    Kind("cloud", "cloud", "Your clouds", "The cloud folders on this PC, one click away.", "cloud",
         ("medium", "small", "large")),
    Kind("shortcut", "", "Shortcut", "A folder, file, app, website or Corefold page, one click away.", "link",
         ("small", "medium"), many=True),
    Kind("shortcuts", "", "Shortcuts", "Several shortcuts together, in one box.", "apps",
         ("wide", "medium", "large"), many=True),
    # (the tools resting for now have theirs too: they show up when their section is back)
    Kind("screenshot", "capture", "Screenshot", "Take a screenshot with one click.", "screenshot",
         ("small", "medium")),
    Kind("clipboard", "clipboard", "Clipboard history", "Everything you copied lately, one click away.",
         "clipboard", ("small",)),
    Kind("disk", "space", "Disk space", "How full each drive is.", "data_pie", ("medium", "small", "large")),
    Kind("color", "colors", "Pick a colour", "Pick any colour on your screen.", "color", ("small",)),
)}
# what a new Home starts with (and what "Start again" puts back)
DEFAULT = (("convert", "medium"), ("pdf", "medium"), ("organizer", "medium"), ("archive", "small"),
           ("unlock", "small"), ("cloud", "medium"), ("stats", "small"), ("shortcut", "small"))
# the default shortcut: the Downloads folder (filled in when it's made, wherever Downloads really is)
DOWNLOADS = "known:downloads"


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def make(kind: str, size: str = "", **extra) -> dict:
    """A new widget's entry in the settings."""
    k = KINDS[kind]
    entry = {"id": new_id(), "kind": kind, "size": size if size in k.sizes else k.sizes[0]}
    entry.update(extra)
    return entry


def default() -> list[dict]:
    out = []
    for kind, size in DEFAULT:
        out.append(make(kind, size, target=DOWNLOADS, name="") if kind == "shortcut" else make(kind, size))
    return out


def clean(saved) -> list[dict]:
    """What's in the settings, made safe: entries that aren't widgets are dropped; a kind this version doesn't
    know (from a newer one) is kept as it is, so going back and forth between versions loses nothing."""
    if not isinstance(saved, list):
        return default()
    out, seen = [], set()
    for e in saved:
        if not isinstance(e, dict) or not isinstance(e.get("kind"), str) or not isinstance(e.get("id"), str):
            continue
        if not e["id"] or e["id"] in seen:
            continue
        seen.add(e["id"])
        e = dict(e)
        k = KINDS.get(e["kind"])
        if k is not None:
            if e.get("size") not in k.sizes:
                e["size"] = k.sizes[0]
            if e["kind"] == "shortcut":
                e["target"] = str(e.get("target") or "")[:1000]
                e["name"] = str(e.get("name") or "")[:60]
                if not e["target"]:
                    continue
            if e["kind"] == "shortcuts":
                items = e.get("items") if isinstance(e.get("items"), list) else []
                e["items"] = [{"name": str(i.get("name") or "")[:60], "target": str(i["target"])[:1000]}
                              for i in items if isinstance(i, dict) and isinstance(i.get("target"), str)
                              and i["target"]][:MAX_ITEMS]
        out.append(e)
        if len(out) >= MAX_WIDGETS:
            break
    return out


def shown(settings, saved: list[dict], is_on) -> list[dict]:
    """The widgets to show now: the ones this version knows, whose tool is in use."""
    out = []
    for e in saved:
        k = KINDS.get(e.get("kind"))
        if k is not None and (not k.tool or is_on(settings, k.tool)):
            out.append(e)
    return out


def offered(settings, saved: list[dict], is_on) -> list[Kind]:
    """The kinds that can be added now (the ones there once already are left out, unless there can be more)."""
    there = {e.get("kind") for e in saved}
    return [k for k in KINDS.values() if (not k.tool or is_on(settings, k.tool)) and (k.many or k.id not in there)]


def span(size: str, columns: int) -> tuple[int, int]:
    cols, rows = SIZES.get(size, (1, 1))
    return min(cols, columns), rows


def pack(sizes: list[str], columns: int) -> list[tuple[int, int, int, int]]:
    """Where each widget goes, in order: (row, column, columns, rows). Each takes the first free place big
    enough for it, reading the page the way it's read (so a small one fills a gap left by a big one)."""
    taken: set[tuple[int, int]] = set()
    places = []
    for size in sizes:
        w, h = span(size, columns)
        row = 0
        while True:
            col = next((c for c in range(columns - w + 1)
                        if all((row + dy, c + dx) not in taken for dy in range(h) for dx in range(w))), None)
            if col is not None:
                break
            row += 1
        for dy in range(h):
            for dx in range(w):
                taken.add((row + dy, col + dx))
        places.append((row, col, w, h))
    return places
