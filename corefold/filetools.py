"""The tools of Rakiza Core Files, in one list: what each is called, what it does in a line, which category it's
in, which files it takes and how it runs. The Files workspace (fileworkspace.py) is made from this list, so a new
tool is added here (and, if it has settings of its own, where it runs), without changing the workspace.

Every tool here belongs to one of Rakiza Core's tools in tools.py (its owner): a tool switched off or resting
for now isn't offered at all. What's still to come is kept apart (UPCOMING): it's shown, never run.

Texts are English and shown through tr()."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import tools as all_tools

# the categories, in the order the library shows them: id, name, icon
CATEGORIES = [("convert", "Convert and edit", "arrow_swap"),
              ("pdf", "PDF", "document_pdf"),
              ("archive", "Compress and archive", "archive"),
              ("organize", "Organize", "folder"),
              ("protect", "Protect and check", "shield_checkmark")]
CATEGORY_NAMES = {key: name for key, name, _ in CATEGORIES}

# what each kind of item is called when a tool says what it takes (core.category's names)
KIND_NAMES = {"image": "pictures", "audio": "sound files", "video": "videos", "pdf": "PDFs", "document": "documents",
              "archive": "archives", "folder": "folders", "any": "files"}


@dataclass(frozen=True)
class FileTool:
    id: str                  # kept in the settings (pins): never changes
    owner: str               # its tool in tools.py: switched off or resting, it isn't offered
    name: str
    line: str                # what it does, in one line
    icon: str                # icondata.py
    category: str            # CATEGORIES
    kinds: tuple = ("any",)  # what it takes (core.category of each item); "any" is every file
    folders: bool = False    # it takes folders too
    least: int = 1           # the fewest items it needs
    most: int = 0            # the most it takes at once (0: any number)
    empty_ok: bool = False   # it can be opened with nothing added (it picks its own files)
    how: str = ""            # how it runs (fileworkspace.run): "slots:…", "pdf:<key>", "page:<Page>", "archive-open"
    settings: bool = False   # it asks how first (shown inside the side panel, with its own button to start)
    view: bool = False       # it opens its own full view inside Files (with the files handed over)
    page: str = ""           # its owner's own page, for everything else it can do


def _pdf_tools() -> list[FileTool]:
    """Rakiza Core PDF's actions, as they're on its page (pdfpage.ACTIONS and MORE), so an action added there is
    offered here too. Read lazily: pdfpage is only imported when the list is first asked for."""
    from .pdfpage import ACTIONS, MORE
    # how many files each takes, and which open the PDF page's own board or editor (any other runs from the panel)
    one = {"edit", "bookmarks"}
    views = {"edit", "merge", "organize", "extract", "read"}
    asks = {"split", "compress", "searchable", "topictures", "lock", "pagesize", "trim", "clean", "print",
            "bookmarks", "pictures", "pdf:numbers", "pdf:watermark", "pdf:sign", "pdf:fit"}
    plain = {"unlock", "flatten", "repair", "compare", "pdf:text", "pdf:word", "pdf:images"}
    out = []
    for key, name, line, icon in ACTIONS + MORE:
        known = key in views or key in asks or key in plain
        out.append(FileTool(
            "pdf." + key.split(":")[-1], "pdf", name, line, icon, "pdf",
            kinds=("image",) if key == "pictures" else ("pdf", "image") if key == "merge" else ("pdf",),
            least=2 if key in ("merge", "compare") else 1,
            most=1 if key in one else 2 if key == "compare" else 0,
            how=f"pdf:{key}", settings=key in asks,
            view=key in views or not known,  # (an action this list doesn't know yet runs on the PDF page itself)
            page="PDF"))
    return out


def _others() -> list[FileTool]:
    return [
        FileTool("convert", "converter", "Convert and edit",
                 "Change the format, size and more of pictures, videos, sound and documents", "arrow_swap", "convert",
                 kinds=("image", "audio", "video", "document", "folder"), folders=True, how="slots:convert",
                 settings=True, page="Converter"),
        FileTool("archive.extract", "archive", "Extract or test an archive",
                 "Unpack ZIP, RAR, 7z and more, test them, or turn them into another kind", "arrow_download",
                 "archive", kinds=("archive",), how="slots:archive", settings=True, page="Archives"),
        FileTool("archive.pack", "archive", "Make an archive",
                 "Put files and folders into a ZIP or 7z, with a password if you like", "folder_zip", "archive",
                 folders=True, how="slots:pack", settings=True, page="Archives"),
        FileTool("archive.open", "archive", "Open an archive",
                 "See what's inside, and drag files out of it", "folder_open", "archive", kinds=("archive",),
                 most=1, empty_ok=True, how="archive-open", view=True, page="Archives"),
        FileTool("rename", "rename", "Rename files",
                 "Rename many files at once, and see every new name before anything changes", "rename", "organize",
                 folders=True, empty_ok=True, how="page:Rename", view=True),
        FileTool("organizer", "organizer", "File Organizer",  # (resting for now: offered only when it's back)
                 "Sort a messy Downloads or Desktop into clear folders with one click.", "folder", "organize",
                 kinds=("folder",), folders=True, most=1, empty_ok=True, how="page:Organizer", view=True),
        FileTool("backup", "backup", "Back up folders",
                 "Copy important folders to another drive; next time only what changed", "arrow_sync", "organize",
                 kinds=("folder",), folders=True, empty_ok=True, how="page:Backup", view=True),
        FileTool("check", "hash", "Verify a file",
                 "Make sure a download is the real file and wasn't changed", "shield_checkmark", "protect",
                 empty_ok=True, how="page:Check", view=True),
        FileTool("unlock", "unlock", "Unlock files",
                 "See which program holds a file you can't delete or move, and free it", "lock_open", "protect",
                 folders=True, empty_ok=True, how="page:Unlock", view=True),
    ]


_ALL: list[FileTool] | None = None


def all_tools_list() -> list[FileTool]:
    """Every Files tool that exists, used or not, in the library's order (by category)."""
    global _ALL
    if _ALL is None:
        order = {key: i for i, (key, _, _) in enumerate(CATEGORIES)}
        others = _others()
        mixed = others[:1] + _pdf_tools() + others[1:]
        _ALL = sorted(mixed, key=lambda t: order[t.category])  # (sorted is stable: each category keeps its order)
    return _ALL


def by_id(tool_id: str) -> FileTool | None:
    return next((t for t in all_tools_list() if t.id == tool_id), None)


def offered(settings) -> list[FileTool]:
    """The tools this person can use now: their owner is switched on and not resting."""
    return [t for t in all_tools_list() if all_tools.is_on(settings, t.owner)]


# pinned to begin with (shown before any file is added)
DEFAULT_PINS = ["convert", "pdf.merge", "pdf.compress", "archive.pack", "rename", "check"]


def pinned(settings) -> list[FileTool]:
    """The pinned tools this person can use, in the order they were pinned."""
    pins = getattr(settings, "files_pinned", DEFAULT_PINS)
    here = {t.id: t for t in offered(settings)}
    return [here[p] for p in pins if p in here]


def set_pinned(settings, tool_id: str, on: bool):
    pins = list(getattr(settings, "files_pinned", DEFAULT_PINS))
    if on and tool_id not in pins:
        pins.append(tool_id)
    elif not on:
        pins = [p for p in pins if p != tool_id]
    settings.files_pinned = pins


# ---- does a tool fit these items?

def kind_of(path: Path) -> str:
    from .core import category
    try:
        return category(path)
    except OSError:
        return "other"


@dataclass
class Fit:
    usable: list            # the items it would work on
    skipped: list           # (item, why) it would leave out
    why_not: str = ""       # why it can't be used on these at all ("" when it can)

    @property
    def ok(self) -> bool:
        return not self.why_not


def takes(tool: FileTool, path: Path, kind: str | None = None) -> bool:
    kind = kind or kind_of(path)
    if kind == "folder":
        return tool.folders
    return "any" in tool.kinds or kind in tool.kinds


def what_it_takes(tool: FileTool) -> str:
    """"PDFs", "pictures", "files and folders"…: what a tool works on, in words."""
    from .i18n import tr
    kinds = [k for k in tool.kinds if k != "folder"]
    names = [tr(KIND_NAMES[k]) for k in kinds]
    if tool.folders and "folder" not in tool.kinds:
        names.append(tr("folders"))
    elif "folder" in tool.kinds:
        names.append(tr("folders"))
    return tr("{first} and {last}", first=", ".join(names[:-1]), last=names[-1]) if len(names) > 1 else names[0]


def how_many(tool: FileTool) -> str:
    from .i18n import tr
    if tool.most == 1:
        return tr("One at a time")
    if tool.least == tool.most == 2:
        return tr("Exactly two")
    if tool.least >= 2:
        return tr("{count} or more", count=tool.least)
    return tr("One or more")


def fit(tool: FileTool, items: list[Path], kinds: dict | None = None) -> Fit:
    """Which of these items the tool would use and which it would leave out (and why), or why it can't be used.
    Nothing is ever run on an item a tool doesn't take: what's left out is shown before it starts."""
    from .i18n import tr
    kinds = kinds or {}
    usable, skipped = [], []
    for p in items:
        if takes(tool, p, kinds.get(str(p))):
            usable.append(p)
        else:
            skipped.append((p, tr("{tool} doesn't take this kind of item", tool=tr(tool.name))))
    if not items:
        return Fit([], [], "" if tool.empty_ok else tr("Add the files it needs first"))
    if not usable:
        return Fit([], skipped, tr("Works on {what}", what=what_it_takes(tool)))
    if len(usable) < tool.least:
        return Fit(usable, skipped, tr("Needs {count} or more of: {what}", count=tool.least, what=what_it_takes(tool)))
    if tool.most and len(usable) > tool.most:
        return Fit(usable, skipped, tr("Select just one") if tool.most == 1 else
                   tr("Select exactly two") if tool.most == 2 else tr("Select fewer"))
    return Fit(usable, skipped)


# ---- what's coming (kept apart from the tools: shown, never run)

STUDYING, PLANNED, BUILDING = "studying", "planned", "building"
STATUS_NAMES = {STUDYING: "Under consideration", PLANNED: "Planned", BUILDING: "In development"}


@dataclass(frozen=True)
class Upcoming:
    id: str
    name: str
    line: str
    icon: str
    status: str


# What's on Rakiza Core's roadmap for Files, with how far along it really is. Only what's confirmed goes here (no
# dates): the Coming tab shows an empty state when there's nothing. A tool still being made comes from tools.py
# (ready=False, in the Files section), so it's offered here as soon as it has a status below.
ROADMAP_STATUS = {"shredder": PLANNED}


def upcoming() -> list[Upcoming]:
    files = all_tools.SECTION_BY_PAGE["Files"]
    out = []
    for tool_id in files.tools:
        t = all_tools.BY_ID[tool_id]
        if not t.ready and tool_id in ROADMAP_STATUS:
            out.append(Upcoming(t.id, t.nav, t.line, t.icon, ROADMAP_STATUS[tool_id]))
    return out
