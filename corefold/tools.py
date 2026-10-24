"""Every Corefold tool in one list: what it's called, which page and settings it has, and whether
it's ready; and the few sections they're grouped in. The sidebar, the Home page, the sections' pages
and the Settings tabs are all made from these lists, so a new tool is added here once (and nothing of a
tool someone switched off is loaded at all).

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
    features: tuple = ()    # what it does (for the "Coming soon" page)
    settings: str = ""      # its first Settings page, if it has settings
    settings_pages: tuple = ()  # all its Settings pages (shown as small tabs if more than one)
    ready: bool = True      # False: still being made ("Coming soon")


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
              "Quick look: select any file in File Explorer and press Ctrl + Space to see what's in it",
              "Zoom, turn and crop, then save or save as another format",
              "HEIC, WebP, AVIF and more, with the photo's details",
              "Open Outlook (.msg) and .eml emails without Outlook, save their attachments, save them as PDF"),
             settings="ViewerSettings", settings_pages=("ViewerSettings",)),
    ToolInfo("player", "Player", "player", "video", "Corefold Player", "Player",
             "Plays every video and song, smooth in 4K, with clean controls that get out of the way.",
             ("Plays every common video and music format",
              "Subtitles: the video's own, or a file you drop in, shifted in time if they're off",
              "Picks up where you left off, with a Continue watching list",
              "A mini player that stays on top of other windows",
              "Speed, A–B loop, frame by frame, screenshots and clips"),
             settings="PlayerSettings", settings_pages=("PlayerSettings",)),
    ToolInfo("capture", "Capture", "capture", "screenshot", "Corefold Capture", "Capture",
             "Take a picture of your screen, mark it up and share it in seconds.",
             ("Pick an area, a window or the whole screen with one shortcut",
              "Draw arrows, boxes and text, and blur anything private",
              "Pick just the words you want in a screenshot and copy them",
              "Pin a screenshot on top while you work, or take one with a timer",
              "Everything stays on your PC: nothing is uploaded"),
             settings="CaptureSettings", settings_pages=("CaptureSettings",)),
    ToolInfo("space", "Space", "space", "data_pie", "Corefold Space", "Space",
             "See what's filling your disk and free up space safely.",
             ("A clear map of the biggest folders and files",
              "Find duplicate files and photos",
              "Find and remove empty folders",
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
             "All your cloud storage in one window: OneDrive, Dropbox, Google Drive and iCloud.",
             ("Finds the OneDrive, Dropbox, Google Drive and iCloud folders on your PC by itself",
              "Drag files and folders from one cloud to another",
              "Several accounts of the same service at once",
              "Search across all of them",
              "Nothing to sign in to: the services' own apps keep everything in sync")),
    ToolInfo("startup", "Startup", "startup", "rocket", "Corefold Startup", "Startup",
             "See what starts with Windows and switch off what slows it down.",
             ("Every program that starts with Windows, in one list",
              "Switch one off with a click, and back on just as easily",
              "Switching off works the same way as Task Manager: nothing is deleted",
              "Spot programs that added themselves since you last looked"),),
    ToolInfo("scan", "Scan", "scan", "shield", "Corefold Scan", "Virus scan",
             "Check your PC for malware with Microsoft Defender, plus Corefold's own checks for tricks viruses use.",
             ("Quick scan, full scan, or right-click any file, folder or drive",
              "Microsoft Defender, part of Windows, does the scanning: nothing is uploaded",
              "Spots tricks viruses use: fake names like photo.jpg.exe, broken signatures, odd programs that "
              "start with Windows",
              "See what Defender put in quarantine, and put a file back if you're sure")),
    ToolInfo("pdf", "PDF", "pdf", "document_pdf", "Corefold PDF", "PDF",
             "Merge, split and arrange PDFs, shrink them, and lock them with a password.",
             ("Merge PDFs and arrange their pages by dragging the page pictures",
              "Split a PDF, or take out just the pages you need",
              "Turn and delete pages, and undo any step",
              "Make a big PDF small enough to send by email",
              "Pictures to PDF, and PDF pages to pictures",
              "Lock a PDF with a password, or unlock one that's yours",
              "Everything happens on your PC: nothing is uploaded")),
    ToolInfo("rename", "Rename", "rename", "rename", "Corefold Rename", "Rename",
             "Rename hundreds of files at once, and see every new name before anything changes.",
             ("Add numbers, dates or words to many names at once",
              "Find and replace in file names",
              "Use the date a photo was taken, or a song's title and artist",
              "See every new name first, and undo it all with one click"),
             ready=False),
    ToolInfo("colors", "Colors", "colors", "color", "Corefold Colors", "Colors",
             "Pick any colour on your screen and copy it as HEX, RGB, HSL, HSV or CMYK.",
             ("Pick a colour anywhere with one shortcut",
              "A magnifier shows every pixel, so you get exactly the one you want",
              "Copy it in the format you need",
              "Your last 24 colours, ready to copy again",
              "Darker and lighter shades of every colour, one click away")),
    ToolInfo("ruler", "Ruler", "ruler", "ruler", "Corefold Ruler", "Ruler",
             "Measure anything on your screen in pixels.",
             ("Measure the distance between two things",
              "See the size of any box or window",
              "Works on top of every app"),
             ready=False),
    ToolInfo("hash", "Check", "hash", "shield_checkmark", "Corefold Check", "Check",
             "Make sure a download is the real file and wasn't changed.",
             ("Paste the code from the website and see if it matches",
              "SHA-256, SHA-1 and MD5",
              "Right-click any file to check it"),
             ready=False),
    ToolInfo("shredder", "Shredder", "shredder", "delete", "Corefold Shredder", "Shredder",
             "Delete private files so they can never be brought back.",
             ("Files are written over before they are deleted",
              "Clean the free space of a drive too",
              "Asks twice, so nothing goes by mistake"),
             ready=False),
    ToolInfo("fonts", "Fonts", "fonts", "text_t", "Corefold Fonts", "Fonts",
             "See all your fonts side by side, and add or remove them easily.",
             ("Type your own words and see them in every font",
              "Install a font with one click",
              "Find and remove fonts you never use"),
             ready=False),
    ToolInfo("network", "Network", "network", "globe", "Corefold Network", "Network",
             "See what's using your internet, and how fast it really is.",
             ("Which apps are using the internet right now",
              "Test your speed without a website",
              "Find the Wi-Fi passwords saved on this PC"),
             ready=False),
    ToolInfo("battery", "Battery", "battery", "battery", "Corefold Battery", "Battery",
             "How healthy your laptop's battery is, and what's draining it.",
             ("How much your battery has worn since it was new",
              "Which apps use the most power",
              "Plain advice to make it last longer"),
             ready=False),
    ToolInfo("focus", "Focus", "focus", "timer", "Corefold Focus", "Focus",
             "A timer for focused work that keeps notifications quiet.",
             ("Work and break timers you can set yourself",
              "Silences notifications while you focus",
              "See how much you focused this week"),
             ready=False),
    ToolInfo("layouts", "Layouts", "layouts", "layout", "Corefold Layouts", "Layouts",
             "Put your windows in neat layouts with one shortcut.",
             ("Snap windows into layouts you design",
              "Keep any window on top of the others",
              "Save a layout and bring it back later"),
             ready=False),
    ToolInfo("drivers", "Drivers", "drivers", "box", "Corefold Drivers", "Drivers",
             "Back up your drivers before you reinstall Windows.",
             ("Save every driver to one folder",
              "Put them all back after a fresh install",
              "See which drivers are old"),
             ready=False),
    ToolInfo("awake", "Awake", "awake", "coffee", "Corefold Awake", "Awake",
             "Keep your PC from going to sleep, for as long as you choose.",
             ("Stay awake for an hour, until a time, or until you say",
              "Keep the screen on or let it turn off",
              "Switch it on and off from the tray",
              "Turn the PC off at a time you pick, even if Corefold is closed by then")),
    ToolInfo("snippets", "Snippets", "snippets", "text_description", "Corefold Snippets", "Snippets",
             "Type a short word and get a whole sentence, address or email.",
             ("Your own short words for text you type often",
              "Works in every app",
              "Add today's date or what you copied"),
             ready=False),
    ToolInfo("backup", "Backup", "backup", "arrow_sync", "Corefold Backup", "Backup",
             "Copy your important folders to another drive, by itself.",
             ("Pick the folders, pick the drive, done",
              "Only copies what changed, so it's quick",
              "Get back an older version of a file"),
             ready=False),
    ToolInfo("notes", "Notes", "notes", "note", "Corefold Notes", "Notes",
             "Quick notes that stay on your desktop, a shortcut away.",
             ("Write something down without opening an app",
              "Notes stay where you put them",
              "Search all your notes"),
             ready=False),
    ToolInfo("system", "System", "system", "desktop", "Corefold System", "System",
             "What's inside your PC, how warm it is, and how it's doing.",
             ("Your processor, memory, graphics and drives in plain words",
              "Temperatures and how busy each part is",
              "A report you can send when asking for help"),
             ready=False),
    ToolInfo("unlock", "Unlock", "unlock", "lock_open", "Corefold Unlock", "Unlock",
             "Find out which program is holding a file you can't delete or move.",
             ("Right-click a file or folder and pick \"What's using this?\"",
              "Asks the program to close first, so it can save your work",
              "Never closes the parts of Windows your PC needs",
              "Then delete or move the file")),
    ToolInfo("organizer", "Organizer", "organizer", "folder", "Corefold File Organizer", "File Organizer",
             "Sort a messy Downloads or Desktop into clear folders with one click.",
             ("Pictures, documents, videos, music and installers each get their own folder",
              "See where every file will go before anything moves",
              "Changed your mind? Undo puts everything back",
              "No rules to write: it just works",
              "Never touches folders, shortcuts or files that are open")),
    ToolInfo("sound", "Sound", "sound", "speaker_2", "Corefold Sound", "Sound",
             "Switch between speakers and headphones with a shortcut, and set each app's volume.",
             ("Change where sound plays with one shortcut",
              "A volume for each app",
              "Pick which microphone apps use"),
             ready=False),
]
@dataclass(frozen=True)
class Section:
    """A part of Corefold in the sidebar, with its tools inside (so the sidebar stays short)."""
    id: str
    page: str               # its page in the window ("Files")
    name: str               # "Files"
    icon: str               # its sidebar icon in icondata.py
    product: str            # the tile its page shows (icons.py)
    line: str               # one line about it
    tools: tuple            # its tools' ids, ready ones and ones on the way, in their first order


SECTIONS: list[Section] = [
    Section("files", "Files", "Files", "document_multiple", "files",
            "Convert, sort, pack and fix your files.",
            ("converter", "pdf", "archive", "organizer", "unlock", "cloud", "rename", "shredder", "backup")),
    Section("media", "Media", "Images & screen", "image_multiple", "media",
            "Screenshots, pictures, videos and colours.",
            ("capture", "viewer", "player", "colors", "ruler", "fonts")),
    Section("computer", "Computer", "Computer", "desktop", "computer",
            "Keep your PC clean, fast and safe.",
            ("space", "uninstaller", "startup", "scan", "hash", "network", "battery", "drivers", "system")),
    Section("quick", "QuickTools", "Quick tools", "flash", "quick",
            "Small helpers you open with one press, from any app.",
            ("clipboard", "awake", "snippets", "notes", "focus", "layouts", "sound")),
]
SECTION_BY_PAGE = {sec.page: sec for sec in SECTIONS}
SECTION_OF = {tool_id: sec for sec in SECTIONS for tool_id in sec.tools}  # tool id -> its section

BY_ID = {t.id: t for t in TOOLS}
BY_PAGE = {t.page: t for t in TOOLS}
# the tools people had before tools could be switched off (1.7): an update keeps them, newer ones wait,
# switched off, in their section
ORIGINAL = ("converter", "uninstaller", "archive")


def ready() -> list[ToolInfo]:
    return [t for t in TOOLS if t.ready]


def coming() -> list[ToolInfo]:
    return [t for t in TOOLS if not t.ready]


def sections(settings) -> list[Section]:
    """The sections in the order this person dragged them into in the sidebar."""
    order = {sec_id: i for i, sec_id in enumerate(getattr(settings, "section_order", []) or [])}
    return sorted(SECTIONS, key=lambda sec: order.get(sec.id, len(order) + SECTIONS.index(sec)))


def in_order(settings, tools: list[ToolInfo]) -> list[ToolInfo]:
    """Tools by their section (in the sidebar's order), and inside it in the order this person put them
    in (tools they never moved keep their first place)."""
    secs = {sec.id: i for i, sec in enumerate(sections(settings))}
    order = {tool_id: i for i, tool_id in enumerate(getattr(settings, "nav_order", []) or [])}

    def key(t):
        sec = SECTION_OF[t.id]
        return secs[sec.id], order.get(t.id, len(order) + sec.tools.index(t.id))
    return sorted(tools, key=key)


def enabled(settings) -> list[ToolInfo]:
    """The tools this person uses (ready, and not switched off), section by section."""
    off = set(getattr(settings, "tools_off", []) or [])
    return in_order(settings, [t for t in TOOLS if t.ready and t.id not in off])


def in_section(settings, sec: Section) -> list[ToolInfo]:
    """A section's ready tools (on or off), in this person's order."""
    return in_order(settings, [BY_ID[tool_id] for tool_id in sec.tools if BY_ID[tool_id].ready])


def is_on(settings, tool_id: str) -> bool:
    t = BY_ID.get(tool_id)
    return bool(t and t.ready and tool_id not in set(getattr(settings, "tools_off", []) or []))


def new_tools(settings) -> list[ToolInfo]:
    """Ready tools this person hasn't been offered yet (shown as New in their section)."""
    known = set(getattr(settings, "known_tools", []) or [])
    return [t for t in TOOLS if t.ready and t.id not in known]


def offer_new_tools(settings) -> list[ToolInfo]:
    """After an update: tools that weren't there before wait in their section, switched off, so
    Corefold only does what this person chose. Returns the ones that are new."""
    fresh = new_tools(settings)
    if fresh:
        settings.tools_off = sorted(set(settings.tools_off) | {t.id for t in fresh})
        settings.known_tools = sorted(set(settings.known_tools) | {t.id for t in fresh})
    return fresh

