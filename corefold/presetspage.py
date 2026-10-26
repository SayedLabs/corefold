"""The Converter's presets on its Options page: the ones made so far (each can be changed or removed), a new
one, and a few ready ones to add with one click. Changing a preset is done in a sheet in the window."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from . import icons, presets, sheet
from .atomic.widgets import Dropdown, FlowLayout, IconButton
from .dialogs import Base
from .i18n import is_rtl, ltr, tr


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


class PresetsCard(QFrame):
    """Presets: a list of the person's own conversions, with New preset… and ready ones."""

    def __init__(self, controller, changed=None):
        super().__init__()
        self.c = controller
        self.changed = changed
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        lay.addWidget(_label(tr("Presets"), "h2"))
        lay.addWidget(_label(tr("Your own conversions, each with its own quality and size. They show on the wheel and "
                                "in the right-click menu, next to the formats."), "muted", wrap=True))
        self.rows = QVBoxLayout()
        self.rows.setSpacing(6)
        lay.addLayout(self.rows)
        self.adds = FlowLayout()
        lay.addLayout(self.adds)
        self._show()

    def _mine(self) -> list[dict]:
        return list(self.c.settings.converter_presets)

    def _keep(self, items: list[dict]):
        s = self.c.settings
        s.converter_presets = presets.clean(items)
        presets.use(s.converter_presets)
        self.c.settings_changed(menu=True)
        if self.changed:
            self.changed()
        self._show()

    def _clear(self, lay):
        while lay.count():
            item = lay.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                self._clear(item.layout())
                item.layout().deleteLater()

    def _show(self):
        self._clear(self.rows)
        self._clear(self.adds)
        t = self.c.theme
        mine = self._mine()
        for p in mine:
            row = QHBoxLayout()
            row.setSpacing(8)
            glyph = QLabel()
            glyph.setPixmap(icons.pixmap({"video": "video", "audio": "music_note_2", "image": "image"}[p["kind"]], 18,
                                         t.text, self.devicePixelRatioF()))
            row.addWidget(glyph)
            words = QVBoxLayout()
            words.setSpacing(0)
            words.addWidget(_label(("\u200f" if is_rtl() else "") + ltr(p["name"]), "cardTitle"))
            words.addWidget(_label(presets.describe(p), "muted"))
            row.addLayout(words, 1)
            edit = IconButton(tr("Change"))
            edit.setIcon(icons.icon("rename", t.muted))
            edit.clicked.connect(lambda _=False, p=p: self.edit(p))
            remove = IconButton(tr("Remove"))
            remove.setIcon(icons.icon("delete", t.muted))
            remove.clicked.connect(lambda _=False, p=p: self._keep([x for x in self._mine() if x["id"] != p["id"]]))
            row.addWidget(edit)
            row.addWidget(remove)
            self.rows.addLayout(row)
        if not mine:
            self.rows.addWidget(_label(tr("No presets yet."), "subtle"))
        full = len(mine) >= presets.MAX
        new = QPushButton(tr("New preset…"))
        new.setIcon(icons.icon("add", t.text))
        new.setCursor(Qt.PointingHandCursor)
        new.setEnabled(not full)
        new.clicked.connect(lambda: self.edit(None))
        self.adds.addWidget(new)
        names = {p["name"].casefold() for p in mine}
        for name, kind, fmt, quality, size in presets.READY:
            if tr(name).casefold() in names:
                continue
            b = QPushButton(tr("Add {name}", name=tr(name)))
            b.setObjectName("ghost")
            b.setCursor(Qt.PointingHandCursor)
            b.setEnabled(not full)
            b.setToolTip(presets.describe({"kind": kind, "format": fmt, "quality": quality, "size": size}))
            b.clicked.connect(lambda _=False, r=(name, kind, fmt, quality, size): self._keep(self._mine() + [
                {"id": presets.new_id(), "name": tr(r[0]), "kind": r[1], "format": r[2], "quality": r[3],
                 "size": r[4]}]))
            self.adds.addWidget(b)

    def edit(self, p: dict | None):
        dlg = PresetDialog(self.c.icon, self.c.theme.dark, p)
        try:
            if not sheet.run(dlg):
                return
            new = dlg.preset()
        finally:
            dlg.deleteLater()
        mine = self._mine()
        if p is None:
            mine.append(new)
        else:
            mine = [new if x["id"] == p["id"] else x for x in mine]
        self._keep(mine)


class PresetDialog(Base):
    """A preset's name, what it's for, its format, quality and size."""

    def __init__(self, icon, dark, p: dict | None):
        super().__init__(tr("Change the preset") if p else tr("New preset"), "", icon, dark)
        self.setMinimumWidth(440)
        self.pid = p["id"] if p else presets.new_id()
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.name = QLineEdit(p["name"] if p else "")
        self.name.setPlaceholderText(tr("Like: For my phone"))
        self.name.setMaxLength(40)
        self.kind = Dropdown()
        for key, text in presets.KINDS.items():
            self.kind.addItem(tr(text), key)
        self.format, self.quality, self.size = Dropdown(), Dropdown(), Dropdown()
        for key, text in presets.QUALITIES.items():
            self.quality.addItem(tr(text), key)
        self.size_label = QLabel(tr("Size"))
        for r, (text, w) in enumerate(((tr("Name"), self.name), (tr("For"), self.kind), (tr("Format"), self.format),
                                       (tr("Quality"), self.quality), (self.size_label, self.size))):
            grid.addWidget(text if isinstance(text, QLabel) else QLabel(text), r, 0)
            grid.addWidget(w, r, 1)
        grid.setColumnStretch(1, 1)
        self.lay.addLayout(grid)
        self.lay.addWidget(_label(tr("The new file gets the preset's name after its own, like "
                                     "\"holiday (WhatsApp).mp4\". The original stays as it is."), "muted", wrap=True))
        self.buttons(tr("Save"))
        if p:
            self.kind.setCurrentIndex(max(0, self.kind.findData(p["kind"])))
        self._kind_changed()
        if p:
            self.format.setCurrentIndex(max(0, self.format.findData(p["format"])))
            self.quality.setCurrentIndex(max(0, self.quality.findData(p["quality"])))
            self.size.setCurrentIndex(max(0, self.size.findData(p["size"])))
        else:
            self.quality.setCurrentIndex(max(0, self.quality.findData("balanced")))
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.name.textChanged.connect(self._check)
        self._check()

    def _kind_changed(self, *_):
        kind = self.kind.currentData()
        self.format.clear()
        for ext in presets.FORMATS[kind]:
            self.format.addItem(ext[1:].upper(), ext)
        self.size.clear()
        for key, text in presets.sizes(kind).items():
            self.size.addItem(tr(text), key)
        self.size.setVisible(kind != "audio")
        self.size_label.setVisible(kind != "audio")

    def _check(self, *_):
        self.ok.setEnabled(bool(self.name.text().strip()))

    def preset(self) -> dict:
        return {"id": self.pid, "name": self.name.text().strip(), "kind": self.kind.currentData(),
                "format": self.format.currentData(), "quality": self.quality.currentData(),
                "size": self.size.currentData() or "original"}
