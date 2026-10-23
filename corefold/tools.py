"""Every Corefold tool in one list: what it's called, which page and settings it has, and whether
it's ready. The sidebar, the Home page, the Settings tabs and the Store are all made from this list,
so a new tool is added here once (and nothing of a tool someone removed is loaded at all).

Texts are English and shown through tr()."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolInfo:
    id: str                 # kept in the settings ("converter")
    page: str               # the window's page for it ("Converter")
    product: str            # its tile in icons.py
    icon: str               # its sidebar icon in icondata.py
    name: str               # "Corefold Converter"
    nav: str                # its name in the sidebar ("Converter")
    line: str               # one line about it
    features: tuple = ()    # what it does (for the Store and the "Coming soon" page)
    settings: str = ""      # its first Settings page, if it has settings
    settings_pages: tuple = ()  # all its Settings pages (shown as small tabs if more than one)
    ready: bool = True      # False: still being made ("Coming soon")


def _cloud_ready() -> bool:
    from .cloudkeys import any_set
    return any_set()


TOOLS: list[ToolInfo] = [
    ToolInfo("converter", "Converter", "converter", "arrow_swap", "Corefold Converter", "Converter",
             "Convert, shrink and edit pictures, videos, music, PDFs and documents with one drag.",
             ("Hold Shift while dragging a file, or right-click it, and pick a format",
              "Resize, crop, compress, cut out the background, copy the text in a picture",
              "Trim videos, make GIFs and stickers, fix the volume",
              "Merge, split and lock PDFs; turn Office files into PDFs"),
             settings="ConvOptions", settings_pages=("ConvOptions", "WheelLook", "Formats")),
    ToolInfo("uninstaller", "Apps", "cleaner", "broom", "Corefold Uninstaller", "Uninstaller",
             "Remove programs and everything they leave behind, with a backup you can put back.",
             ("Uninstall several programs in one go",
              "Clean up the folders, shortcuts and registry entries they leave behind",
              "See which programs you haven't used in months",
              "Everything removed is kept as a backup you can put back"),
             settings="AppsSettings", settings_pages=("AppsSettings",)),
    ToolInfo("archive", "Archives", "archive", "archive", "Corefold Archive", "Archives",
             "Open, extract and make ZIP, RAR and 7z files. Drag files out, change them inside, rescue damaged ones.",
             ("Open ZIP, RAR, 7z, TAR, ISO and more with a double-click",
              "Drag files out of an archive into any folder",
              "Change a file inside an archive and save it back",
              "Rescue damaged archives, and unpack archives inside archives in one go"),
             settings="ArchiveSettings", settings_pages=("ArchiveSettings",)),
    ToolInfo("viewer", "Viewer", "viewer", "image", "Corefold Viewer", "Viewer",
             "Flip through a folder of pictures fast, fix them on the spot, and open email files.",
             ("Arrow keys through a whole folder, instantly",
              "Zoom, turn and crop, then save or save as another format",
              "HEIC, WebP, AVIF and more, with the photo's details",
              "Open Outlook (.msg) and .eml emails without Outlook, save their attachments, save them as PDF"),
             settings="ViewerSettings", settings_pages=("ViewerSettings",)),
    ToolInfo("player", "Player", "player", "video", "Corefold Player", "Player",
             "Plays every video and song, smooth in 4K, with clean controls that get out of the way.",
             ("Plays every common video and music format",
              "Subtitles you can drag in, resize and shift in time",
              "Picks up where you left off, with a Continue watching list",
              "A mini player that stays on top of other windows",
              "Speed, A–B loop, frame by frame, screenshots and clips"),
             ready=False),
    ToolInfo("capture", "Capture", "capture", "screenshot", "Corefold Capture", "Capture",
             "Take a picture or a video of your screen, mark it up and share it in seconds.",
             ("Pick an area, a window or the whole screen with one shortcut",
              "Draw arrows, boxes and text, and blur anything private",
              "Copy the text you see in any picture or screen",
              "Record your screen as a video, with your microphone if you want",
              "Everything stays on your PC: nothing is uploaded"),
             settings="CaptureSettings", settings_pages=("CaptureSettings",)),
    ToolInfo("space", "Space", "space", "data_pie", "Corefold Space", "Space",
             "See what's filling your disk and free up space safely.",
             ("A clear map of the biggest folders and files",
              "Find duplicate files and photos",
              "Clean up safe junk: temporary files, old installers, leftover caches",
              "Never touches Windows or your personal files without asking",
              "Files you remove go to the Recycle Bin first")),
    ToolInfo("clipboard", "Clipboard", "clipboard", "clipboard", "Corefold Clipboard", "Clipboard",
             "Everything you copy, kept and easy to find again: press Win + Alt + V in any app.",
             ("Text, pictures, links and files you copied, in one list",
              "Pin what you paste often so it's always at the top",
              "Search your clipboard history",
              "Paste as plain text, without the formatting",
              "Stays on your PC, and skips passwords from password managers"),
             settings="ClipboardSettings", settings_pages=("ClipboardSettings",)),
    ToolInfo("cloud", "Cloud", "cloud", "cloud", "Corefold Cloud", "Cloud",
             "All your cloud storage in one window: OneDrive and Dropbox, with Google Drive to come.",
             ("See OneDrive and Dropbox side by side, without switching accounts",
              "Drag files from one cloud to another",
              "Several accounts of the same service at once",
              "Search across all of them",
              "Sign in on Microsoft's and Dropbox's own page: your passwords never reach Corefold"),
             ready=_cloud_ready()),
]
BY_ID = {t.id: t for t in TOOLS}
BY_PAGE = {t.page: t for t in TOOLS}
# the tools people had before the Store (1.7): an update keeps them, newer ones are offered in the Store
ORIGINAL = ("converter", "uninstaller", "archive")


def ready() -> list[ToolInfo]:
    return [t for t in TOOLS if t.ready]


def coming() -> list[ToolInfo]:
    return [t for t in TOOLS if not t.ready]


def enabled(settings) -> list[ToolInfo]:
    """The tools this person uses (ready, and not removed in the Store)."""
    off = set(getattr(settings, "tools_off", []) or [])
    return [t for t in TOOLS if t.ready and t.id not in off]


def is_on(settings, tool_id: str) -> bool:
    t = BY_ID.get(tool_id)
    return bool(t and t.ready and tool_id not in set(getattr(settings, "tools_off", []) or []))


def new_tools(settings) -> list[ToolInfo]:
    """Ready tools this person hasn't been offered yet (shown as New in the Store)."""
    known = set(getattr(settings, "known_tools", []) or [])
    return [t for t in TOOLS if t.ready and t.id not in known]


def offer_new_tools(settings) -> list[ToolInfo]:
    """After an update: tools that weren't there before are put in the Store, not switched on, so
    Corefold only does what this person chose. Returns the ones that are new."""
    fresh = new_tools(settings)
    if fresh:
        settings.tools_off = sorted(set(settings.tools_off) | {t.id for t in fresh})
        settings.known_tools = sorted(set(settings.known_tools) | {t.id for t in fresh})
    return fresh

