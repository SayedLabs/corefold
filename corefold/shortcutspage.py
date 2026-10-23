"""Settings → Shortcuts: every Corefold shortcut in one list. The ones that work in any app show whether
they're working (or another app took them) and open their setting; the keys of the window, the Viewer
and the Player are listed under them.

Everything shown comes from the two tables below: a new tool's shortcut is one more line there.
Texts are English and shown through tr()."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from . import hotkey
from . import tools as all_tools
from .i18n import tr


@dataclass(frozen=True)
class Global:
    label: str           # what it does
    setting: str         # the settings field with its keys ("off" = none)
    page: str            # the Settings page where it's changed
    widget: str          # the window's attribute for that setting (lit up by "Change")
    tool: str = ""       # the tool it belongs to ("" = Corefold itself)
    live: str = ""       # the running shortcut, from the controller ("clip.hotkey"); its .ok says if it works
    switch: str = ""     # a settings field that switches it on and off
    names: str = ""      # a table in settings.py naming its values, if it isn't a hotkey.py shortcut


# shortcuts that work in any app
GLOBAL = [
    Global("Convert a file: hold this key while you start dragging it", "shortcut", "ConvOptions", "shortcut",
           tool="converter", names="SHORTCUT_LABELS"),
    Global("Show the clipboard history", "clip_shortcut", "ClipboardSettings", "clip_shortcut", tool="clipboard",
           live="clip.hotkey"),
    Global("Take a screenshot", "cap_shortcut", "CaptureSettings", "cap_shortcut", tool="capture",
           live="capture.shot_key"),
    Global("Start or stop recording the screen", "cap_record_shortcut", "CaptureSettings", "cap_record_shortcut",
           tool="capture", live="capture.rec_key"),
    Global("Open the quick menu", "quick_menu_key", "Quick", "quick_menu_rec", live="quick.menu_key",
           switch="quick_menu"),
    Global("Open the quick wheel", "quick_wheel_key", "Quick", "quick_wheel_rec", live="quick.wheel_key",
           switch="quick_wheel"),
    Global("Keep a window on top", "ontop_key", "Quick", "quick_ontop_rec", live="quick.ontop_key"),
    Global("Pick a colour", "colors_shortcut", "Colors", "", tool="colors", live="colors.key"),
]

# keys inside Corefold's windows: (heading, the tool it's for or "", [(keys, what it does[, its tool])])
WINDOW_KEYS = [
    ("In the Corefold window", "", [
        ("Ctrl + K", "Search pages, settings and actions"),
        ("Alt + ←", "Back"),
        ("Ctrl + V", "Convert what's copied", "converter"),
        ("Esc", "Close the window"),
    ]),
    ("In the Viewer", "viewer", [
        ("← / →", "Previous or next picture"),
        ("+ / −", "Zoom in or out"),
        ("0 / 1", "Fit to the window / actual size"),
        ("R / Shift + R", "Turn right or left"),
        ("C", "Crop"),
        ("I", "Details"),
        ("Del", "Delete"),
        ("F5", "Slideshow"),
        ("F11 / F", "Full screen"),
        ("Ctrl + C", "Copy the picture"),
    ]),
    ("In the Player", "player", [
        ("Space / K", "Play / pause"),
        ("← / →", "Back or forward"),
        ("↑ / ↓", "Volume up or down"),
        ("M", "Mute"),
        ("F / F11", "Full screen"),
        ("N / P", "Next or previous file"),
        ("[ / ]", "Slower or faster"),
        (", / .", "One frame back or forward"),
        ("S", "Save this frame as a picture"),
        ("I", "Mini player"),
    ]),
]

STATES = {"working": "Working", "off": "Switched off", "taken": "Another app is using it", "unused": "Not in use"}


def running(c, path: str):
    """The object at "clip.hotkey" on the controller, or None while that part isn't running."""
    obj = c
    for part in path.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def state(c, entry: Global) -> str:
    """"working", "off", "taken" (another app registered the keys first) or "unused" (its tool isn't in use)."""
    s = c.settings
    if entry.tool and not all_tools.is_on(s, entry.tool):
        return "unused"
    if entry.switch and not getattr(s, entry.switch, False):
        return "off"
    value = getattr(s, entry.setting, "off")
    if not value or value == "off":
        return "off"
    if not entry.live:
        return "working" if getattr(s, "enabled", True) else "off"
    key = running(c, entry.live)
    if key is None:
        return "off"
    return "working" if getattr(key, "ok", False) else "taken"


def keys_text(c, entry: Global) -> str:
    value = getattr(c.settings, entry.setting, "off")
    if entry.names:
        from . import settings as settings_mod
        return tr(getattr(settings_mod, entry.names).get(value, value))
    if not value or value == "off":
        return tr("No shortcut")
    return hotkey.display(value) or value


def _wrapping(lab):
    """Words that wrap in a narrow window (the keys and buttons next to them keep their room)."""
    lab.setWordWrap(True)
    lab.setMinimumWidth(1)
    lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    lab.setProperty("keepsHeight", True)  # (the layout gives it the height its lines need at any width)
    return lab


def _kbd(text: str):
    """Keys as printed on the keyboard, always left to right (arrows too, in Arabic)."""
    from .window import label
    lab = label("", "kbd")
    lab.setLayoutDirection(Qt.LeftToRight)
    set_keys(lab, text)
    return lab


def set_keys(lab, text: str):
    from .i18n import is_rtl
    lab.setText(f"\u202a{text}\u202c" if text and is_rtl() else text)


class ShortcutsPage:
    """The page (a scroll area made by window.page()) and what refreshes it."""

    def __init__(self, win):
        from .window import button, card, label, page
        self.win = win
        self.c = win.c
        self.widget, lay = page(tr("Shortcuts"), tr("Every keyboard shortcut in Corefold. The ones that work in any "
                                                    "app can be changed."))
        box, bl = card()
        bl.addWidget(_wrapping(label(tr("Keys that work in any app"), "h2")))
        self.rows = []
        for r, entry in enumerate(GLOBAL):
            if r:
                line = QFrame()
                line.setObjectName("line")
                bl.addWidget(line)
            line_box = QWidget()
            row = QHBoxLayout(line_box)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(12)
            words = QVBoxLayout()
            words.setSpacing(2)
            words.addWidget(_wrapping(label(tr(entry.label), "shortcutWhat")))
            status = label("", "muted")
            words.addWidget(status)
            row.addLayout(words, 1)
            keys = _kbd("")
            row.addWidget(keys, 0, Qt.AlignVCenter)
            change = button(tr("Change"), "flat", lambda _=False, e=entry: self.change(e))
            row.addWidget(change, 0, Qt.AlignVCenter)
            bl.addWidget(line_box)
            self.rows.append((entry, keys, status, change))
        lay.addWidget(box)

        self.sections = []
        for heading, tool, keys_list in WINDOW_KEYS:
            box, bl = card()
            top = QHBoxLayout()
            top.addWidget(_wrapping(label(tr(heading), "h2")), 1)
            body = QWidget()
            g = QGridLayout(body)
            g.setContentsMargins(0, 0, 0, 0)
            g.setHorizontalSpacing(14)
            g.setVerticalSpacing(8)
            for r, (keys, what, *only) in enumerate(keys_list):
                if only and not all_tools.is_on(self.c.settings, only[0]):
                    continue
                g.addWidget(_kbd(keys), r, 0, Qt.AlignLeading | Qt.AlignVCenter)
                g.addWidget(_wrapping(label(tr(what), "shortcutWhat")), r, 1)
            g.setColumnStretch(1, 1)
            toggle = button("", "flat")
            toggle.clicked.connect(lambda _=False, b=body, t=toggle: self._show(b, t, b.isHidden()))
            top.addWidget(toggle)
            bl.addLayout(top)
            bl.addWidget(body)
            self._show(body, toggle, not tool)  # the window's own keys open, the others folded
            lay.addWidget(box)
            self.sections.append((tool, box, body, toggle))
        lay.addStretch()
        self.refresh()

    @staticmethod
    def _show(body: QWidget, toggle, shown: bool):
        body.setVisible(shown)
        toggle.setText(tr("Hide the keys") if shown else tr("Show the keys"))

    def expand_all(self):
        for _, _, body, toggle in self.sections:
            self._show(body, toggle, True)

    def refresh(self):
        """Reads each shortcut again (and whether it works now)."""
        for entry, keys, status, change in self.rows:
            st = state(self.c, entry)
            set_keys(keys, keys_text(self.c, entry))
            keys.setVisible(st != "unused")
            status.setText(tr(STATES[st]))
            status.setObjectName("unsaved" if st == "taken" else "muted")
            status.setToolTip(tr("Pick other keys, or close the app that uses these.") if st == "taken" else "")
            status.style().unpolish(status)
            status.style().polish(status)
            change.setVisible(st != "unused")
        for tool, box, _, _ in self.sections:
            box.setVisible(not tool or all_tools.is_on(self.c.settings, tool))

    def change(self, entry: Global):
        widget = getattr(self.win, entry.widget, None)
        if widget is not None:
            self.win._show_setting(entry.page, widget)
        else:
            self.win.open_page(entry.page)
