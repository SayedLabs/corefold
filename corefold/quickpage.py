"""Settings → Quick access: the quick menu's and the quick wheel's shortcuts, what's on each of
them, and the person's own shortcuts (apps, files, folders and websites)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QVBoxLayout,
                               QWidget)

from . import hotkey, icons, quick
from .i18n import tr
from .settings import QUICK_KEYS, QUICK_SEARCH_ENGINES


class KeyRecorder(QPushButton):
    """Shows a shortcut; click it and press the keys to change it. Esc keeps the old one."""
    recorded = Signal(str)

    def __init__(self, combo: str, on_start=None, on_end=None):
        super().__init__()
        self.combo = combo
        self.recording = False
        self.on_start, self.on_end = on_start, on_end
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(200)
        self.setFocusPolicy(Qt.StrongFocus)
        self.clicked.connect(self.start)
        self._show()

    def _show(self, text: str = ""):
        if self.recording:
            self.setText(text or tr("Press the keys…"))
        else:
            self.setText(hotkey.display(self.combo) or tr("No shortcut"))

    def set_combo(self, combo: str):
        self.combo = combo
        self._show()

    def start(self):
        if self.recording:
            return
        self.recording = True
        if self.on_start:
            self.on_start()  # (Corefold's own shortcuts would otherwise catch the keys first)
        self._show()
        self.setFocus()
        self.grabKeyboard()

    def stop(self):
        if not self.recording:
            return
        self.recording = False
        self.releaseKeyboard()
        self._show()
        if self.on_end:
            self.on_end()

    def focusOutEvent(self, e):
        self.stop()
        super().focusOutEvent(e)

    def keyPressEvent(self, e):
        if not self.recording:
            super().keyPressEvent(e)
            return
        if e.key() == Qt.Key_Escape and not e.modifiers():
            self.stop()
            return
        combo = hotkey.from_key_event(e.key(), e.modifiers(), e.nativeVirtualKey())
        if not combo:  # only Ctrl, Alt, Shift or Win so far
            mods = [n for n, f in (("Ctrl", Qt.ControlModifier), ("Alt", Qt.AltModifier), ("Shift", Qt.ShiftModifier),
                                   ("Win", Qt.MetaModifier)) if e.modifiers() & f]
            self._show(" + ".join(mods + ["…"]))
            return
        self.stop()
        self.recorded.emit(combo)


class ItemList(QWidget):
    """What's on the wheel or the menu, in order: drag to reorder, add from a list, remove."""
    changed = Signal(list)

    def __init__(self, settings, ids: list[str], limit: int, theme):
        super().__init__()
        self.settings, self.limit, self.theme = settings, limit, theme
        self.list = QListWidget()
        self.list.setObjectName("history")  # (styled like the other lists in the window)
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setIconSize(QSize(18, 18))
        self.list.setMinimumHeight(150)
        self.list.setMaximumHeight(260)
        self.list.model().rowsMoved.connect(lambda *_: self._emit())
        self.list.itemSelectionChanged.connect(self._buttons)
        self.add_btn = QPushButton(tr("Add…"))
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(self._add_menu)
        self.remove_btn = QPushButton(tr("Remove"))
        self.remove_btn.setCursor(Qt.PointingHandCursor)
        self.remove_btn.clicked.connect(self._remove)
        self.up_btn = QPushButton("↑")
        self.down_btn = QPushButton("↓")
        for b, step in ((self.up_btn, -1), (self.down_btn, 1)):
            b.setFixedWidth(40)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, s=step: self._move(s))
        self.up_btn.setToolTip(tr("Move up"))
        self.down_btn.setToolTip(tr("Move down"))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        for b in (self.add_btn, self.remove_btn, self.up_btn, self.down_btn):
            row.addWidget(b)
        row.addStretch()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self.list)
        lay.addLayout(row)
        self.fill(ids)

    def fill(self, ids: list[str]):
        self.list.clear()
        for i in ids:
            e = quick.entry(self.settings, i)
            if e is None:
                continue  # (its tool was removed, or the link deleted: kept in the settings, not shown)
            item = QListWidgetItem(icons.icon(e.icon, self.theme.text), e.label)
            item.setData(Qt.UserRole, i)
            self.list.addItem(item)
        self._buttons()

    def ids(self) -> list[str]:
        return [self.list.item(r).data(Qt.UserRole) for r in range(self.list.count())]

    def _emit(self):
        self._buttons()
        self.changed.emit(self.ids())

    def _buttons(self):
        row = self.list.currentRow() if self.list.selectedItems() else -1
        self.remove_btn.setEnabled(row >= 0)
        self.up_btn.setEnabled(row > 0)
        self.down_btn.setEnabled(0 <= row < self.list.count() - 1)
        self.add_btn.setEnabled(self.list.count() < self.limit)

    def _add_menu(self):
        have = set(self.ids())
        m = QMenu(self)
        for e in quick.choosable(self.settings):
            if e.id in have:
                continue
            m.addAction(icons.icon(e.icon, self.theme.text), e.label, lambda i=e.id: self._add(i))
        if m.isEmpty():
            act = m.addAction(tr("Everything is already on it"))
            act.setEnabled(False)
        m.exec(self.add_btn.mapToGlobal(self.add_btn.rect().bottomLeft()))

    def _add(self, action_id: str):
        if self.list.count() >= self.limit:
            return
        e = quick.entry(self.settings, action_id)
        if e is None:
            return
        item = QListWidgetItem(icons.icon(e.icon, self.theme.text), e.label)
        item.setData(Qt.UserRole, action_id)
        self.list.addItem(item)
        self.list.setCurrentItem(item)
        self._emit()

    def _remove(self):
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)
            self._emit()

    def _move(self, step: int):
        row = self.list.currentRow()
        to = row + step
        if row < 0 or not 0 <= to < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(to, item)
        self.list.setCurrentRow(to)
        self._emit()


class LinkDialog(QDialog):
    """A name and an address for one of the person's own shortcuts."""

    def __init__(self, parent, name: str = "", target: str = "", website: bool = False):
        super().__init__(parent)
        self.setWindowTitle(tr("Add a website") if website else tr("Add a shortcut"))
        self.name = QLineEdit(name)
        self.target = QLineEdit(target)
        self.target.setPlaceholderText("https://…" if website else "")
        form = QFormLayout()
        form.addRow(tr("Name"), self.name)
        form.addRow(tr("Address") if website else tr("Opens"), self.target)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(tr("Add"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.target.textChanged.connect(lambda t: buttons.button(QDialogButtonBox.Ok).setEnabled(bool(t.strip())))
        buttons.button(QDialogButtonBox.Ok).setEnabled(bool(target.strip()))
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)
        self.resize(460, self.sizeHint().height())


def build(win) -> QWidget:
    """The page (win is the main window: its building blocks and the controller)."""
    from .window import _reset_row, button, card, checkbox, label, page
    c = win.c
    s = c.settings
    w, lay = page(tr("Quick access"), tr("Your tools and shortcuts from any app: a menu over the screen, and a "
                                         "wheel round the mouse."))

    def pause_keys():
        if c.quick is not None:
            c.quick.menu_key.stop()
            c.quick.wheel_key.stop()
            c.quick.ontop_key.stop()

    def resume_keys():
        if c.quick is not None:
            c.quick.apply_settings()

    def others(but: str) -> dict[str, str]:
        """Corefold's other shortcuts, so two never get the same keys."""
        out = {}
        if s.quick_menu and but != "menu":
            out[hotkey.normalize(s.quick_menu_key)] = tr("the quick menu")
        if s.quick_wheel and but != "wheel":
            out[hotkey.normalize(s.quick_wheel_key)] = tr("the quick wheel")
        if but != "ontop":
            out[hotkey.normalize(s.ontop_key)] = tr("keeping a window on top")
        if c.clip is not None:
            out[s.clip_shortcut] = tr("the clipboard")
        if c.capture is not None:
            out[s.cap_shortcut] = tr("screenshots")
            out[s.cap_record_shortcut] = tr("recording")
        out.pop("off", None)
        out.pop("", None)
        return out

    def recorded(which: str, combo: str, rec: KeyRecorder, note):
        combo = hotkey.normalize(combo)
        why = hotkey.problem(combo) if combo else hotkey.PROBLEMS["bad"]
        if not why and combo in others(which):
            note.setText(tr("Corefold already uses {key} for {what}.", key=hotkey.display(combo),
                            what=others(which)[combo]))
            note.setVisible(True)
            return
        if why:
            note.setText(tr(why))
            note.setVisible(True)
            return
        rec.set_combo(combo)
        if which == "menu":
            s.quick_menu_key = combo
        elif which == "ontop":
            s.ontop_key = combo
        else:
            s.quick_wheel_key = combo
        c.settings_changed()
        refresh_notes()
        if hotkey.types_on_some_keyboards(combo):
            note.setText(tr("On some keyboards Ctrl + Alt + this key types a letter or symbol. If it does on yours, "
                            "pick another one."))
            note.setVisible(True)

    def refresh_notes():
        ok_menu, ok_wheel = c.quick.keys_ok() if c.quick is not None else (True, True)
        taken = tr("Another app already uses this shortcut, so it can't open this. Pick another one.")
        ok_top = c.quick.ontop_key.ok if c.quick is not None else True
        for note, ok, on in ((menu_note, ok_menu, s.quick_menu), (wheel_note, ok_wheel, s.quick_wheel),
                             (ontop_note, ok_top, s.ontop_key != "off")):
            note.setText(taken)
            note.setVisible(bool(on and not ok))

    # the menu
    box, bl = card()
    bl.addWidget(label(tr("Quick menu"), "h2"))
    menu_on = checkbox(tr("Open a quick menu over the screen from any app"), s.quick_menu)
    bl.addWidget(menu_on)
    row = QHBoxLayout()
    row.addWidget(label(tr("Shortcut")))
    menu_rec = KeyRecorder(s.quick_menu_key, pause_keys, resume_keys)
    row.addWidget(menu_rec)
    row.addStretch()
    bl.addLayout(row)
    menu_note = label("", "warning", wrap=True)
    bl.addWidget(menu_note)
    bl.addWidget(label(tr("Click the shortcut and press the keys you want. Start typing in the menu to search it."),
                       "muted", wrap=True))
    bl.addWidget(label(tr("Quick actions on the menu (your tools are always on it)"), wrap=True))
    menu_items = ItemList(s, s.quick_menu_items, 40, c.theme)
    bl.addWidget(menu_items)
    bl.addWidget(label(tr("Typing in the menu also works out sums (12*7), converts units (10 km to mi) and finds "
                          "pages of Windows Settings. Nothing is sent online unless you pick “Search the web”."),
                       "muted", wrap=True))
    row = QHBoxLayout()
    row.addWidget(label(tr("Search the web with")))
    engine = QComboBox()
    for key, name in QUICK_SEARCH_ENGINES.items():
        engine.addItem(name, key)
    engine.setCurrentIndex(max(0, engine.findData(s.quick_search_engine)))
    row.addWidget(engine)
    row.addStretch()
    bl.addLayout(row)
    lay.addWidget(box)

    # the wheel
    box, bl = card()
    bl.addWidget(label(tr("Quick wheel"), "h2"))
    wheel_on = checkbox(tr("Open a quick wheel round the mouse from any app"), s.quick_wheel)
    bl.addWidget(wheel_on)
    row = QHBoxLayout()
    row.addWidget(label(tr("Shortcut")))
    wheel_rec = KeyRecorder(s.quick_wheel_key, pause_keys, resume_keys)
    win.quick_menu_rec, win.quick_wheel_rec = menu_rec, wheel_rec  # (Settings → Shortcuts points at them)
    row.addWidget(wheel_rec)
    row.addStretch()
    bl.addLayout(row)
    wheel_note = label("", "warning", wrap=True)
    bl.addWidget(wheel_note)
    bl.addWidget(label(tr("Hold the shortcut, point at an option and let go. Or tap it, then click an option or "
                          "press its number."), "muted", wrap=True))
    button_on = checkbox(tr("Show a small round button on the screen that opens the wheel"), s.quick_button)
    bl.addWidget(button_on)
    bl.addWidget(label(tr("On the wheel (up to {count})", count=quick.MAX_WHEEL), wrap=True))
    wheel_items = ItemList(s, s.quick_wheel_items, quick.MAX_WHEEL, c.theme)
    bl.addWidget(wheel_items)
    if any(t.id == "converter" for t in win.tools_on):
        look = QHBoxLayout()
        look.addWidget(label(tr("It looks like the Converter's wheel."), "muted"))
        look.addWidget(button(tr("Change the look…"), "flat", lambda: win.open_page("WheelLook")))
        look.addStretch()
        bl.addLayout(look)
    lay.addWidget(box)

    # keeping a window on top
    box, bl = card()
    bl.addWidget(label(tr("Keep a window on top"), "h2"))
    bl.addWidget(label(tr("The window you're using stays above all the others until you switch it back. You can "
                          "put it on the menu or the wheel too."), "muted", wrap=True))
    ontop_on = checkbox(tr("Use a shortcut for it"), s.ontop_key != "off")
    bl.addWidget(ontop_on)
    row = QHBoxLayout()
    row.addWidget(label(tr("Shortcut")))
    ontop_rec = KeyRecorder(s.ontop_key if s.ontop_key != "off" else "", pause_keys, resume_keys)
    ontop_rec.setEnabled(s.ontop_key != "off")
    row.addWidget(ontop_rec)
    row.addStretch()
    bl.addLayout(row)
    ontop_note = label("", "warning", wrap=True)
    bl.addWidget(ontop_note)
    lay.addWidget(box)

    # their own shortcuts
    box, bl = card()
    bl.addWidget(label(tr("Your shortcuts"), "h2"))
    bl.addWidget(label(tr("Apps, files, folders and websites you open often. Put them on the menu or the wheel."),
                       "muted", wrap=True))
    links = QListWidget()
    links.setObjectName("history")
    links.setIconSize(QSize(18, 18))
    links.setMinimumHeight(110)
    links.setMaximumHeight(220)
    bl.addWidget(links)
    row = QHBoxLayout()
    add_app = button(tr("Add an app or file…"))
    add_folder = button(tr("Add a folder…"))
    add_site = button(tr("Add a website…"))
    remove_link = button(tr("Remove"))
    for b in (add_app, add_folder, add_site, remove_link):
        row.addWidget(b)
    row.addStretch()
    bl.addLayout(row)
    lay.addWidget(box)
    lay.addLayout(_reset_row(lambda: win.reset_to_defaults(QUICK_KEYS)))
    lay.addStretch()

    def fill_links():
        links.clear()
        for link in s.quick_links:
            target = link.get("target", "")
            item = QListWidgetItem(icons.icon(quick.link_icon(target), c.theme.text),
                                   f"{link.get('name') or target}  —  {target}")
            item.setData(Qt.UserRole, link.get("id"))
            item.setToolTip(target)
            links.addItem(item)
        remove_link.setEnabled(False)
        full = len(s.quick_links) >= quick.MAX_LINKS
        for b in (add_app, add_folder, add_site):
            b.setEnabled(not full)

    def add_link(name: str, target: str, ask: bool, website: bool = False):
        if ask:
            dlg = LinkDialog(win, name, target, website)
            if not dlg.exec():
                return
            name, target = dlg.name.text(), dlg.target.text()
        if not target.strip():
            return
        link = quick.new_link(name or Path(target).stem or target, target)
        s.quick_links = s.quick_links + [link]
        new_id = f"link:{link['id']}"
        # straight onto the menu (and the wheel if there's room): that's why it was added
        if new_id not in s.quick_menu_items:
            s.quick_menu_items = s.quick_menu_items + [new_id]
        c.settings_changed()
        fill_links()
        menu_items.fill(s.quick_menu_items)
        wheel_items.fill(s.quick_wheel_items)

    def pick_file():
        name, _ = QFileDialog.getOpenFileName(win, tr("Pick an app or file"), str(Path.home()))
        if name:
            add_link(Path(name).stem, str(Path(name)), ask=False)

    def pick_folder():
        name = QFileDialog.getExistingDirectory(win, tr("Pick a folder"), str(Path.home()))
        if name:
            add_link(Path(name).name or name, str(Path(name)), ask=False)

    def remove_selected():
        item = links.currentItem()
        if item is None:
            return
        lid = item.data(Qt.UserRole)
        s.quick_links = [x for x in s.quick_links if x.get("id") != lid]
        s.quick_menu_items = [i for i in s.quick_menu_items if i != f"link:{lid}"]
        s.quick_wheel_items = [i for i in s.quick_wheel_items if i != f"link:{lid}"]
        c.settings_changed()
        fill_links()
        menu_items.fill(s.quick_menu_items)
        wheel_items.fill(s.quick_wheel_items)

    add_app.clicked.connect(pick_file)
    add_folder.clicked.connect(pick_folder)
    add_site.clicked.connect(lambda: add_link("", "", ask=True, website=True))
    remove_link.clicked.connect(remove_selected)
    links.itemSelectionChanged.connect(lambda: remove_link.setEnabled(bool(links.selectedItems())))
    fill_links()

    def toggled(*_):
        s.quick_menu = menu_on.isChecked()
        s.quick_wheel = wheel_on.isChecked()
        s.quick_button = button_on.isChecked()
        for wid in (menu_rec, menu_items, engine):
            wid.setEnabled(s.quick_menu)
        for wid in (wheel_rec, wheel_items, button_on):
            wid.setEnabled(s.quick_wheel)
        c.settings_changed()
        refresh_notes()

    def items_changed(which: str, ids: list):
        if which == "menu":
            s.quick_menu_items = list(ids)
        else:
            s.quick_wheel_items = list(ids)
        c.settings_changed()

    for cb in (menu_on, wheel_on, button_on):
        cb.toggled.connect(toggled)
    menu_rec.recorded.connect(lambda combo: recorded("menu", combo, menu_rec, menu_note))
    ontop_rec.recorded.connect(lambda combo: recorded("ontop", combo, ontop_rec, ontop_note))

    def ontop_toggled(on: bool):
        from .settings import Settings
        key = Settings().ontop_key
        s.ontop_key = key if on and key not in others("ontop") else "off"
        ontop_rec.set_combo(s.ontop_key if s.ontop_key != "off" else "")
        ontop_rec.setEnabled(on)
        c.settings_changed()
        refresh_notes()
        if on and s.ontop_key == "off":  # (its usual keys are taken: pick others)
            ontop_rec.start()

    def engine_changed(_=0):
        s.quick_search_engine = engine.currentData()
        c.settings_changed()
    ontop_on.toggled.connect(ontop_toggled)
    engine.currentIndexChanged.connect(engine_changed)
    wheel_rec.recorded.connect(lambda combo: recorded("wheel", combo, wheel_rec, wheel_note))
    menu_items.changed.connect(lambda ids: items_changed("menu", ids))
    wheel_items.changed.connect(lambda ids: items_changed("wheel", ids))
    for wid in (menu_rec, menu_items, engine):
        wid.setEnabled(s.quick_menu)
    for wid in (wheel_rec, wheel_items, button_on):
        wid.setEnabled(s.quick_wheel)
    win.quick_notes = refresh_notes
    refresh_notes()
    return w
