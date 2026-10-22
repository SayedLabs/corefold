"""The Apps page: uninstall programs, then clean up what they left behind (with a backup)."""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QFileInfo, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFileIconProvider, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QStackedWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import uninstall as un
from .core import LEGACY_NAME, human_size, short_name
from .i18n import tr

if TYPE_CHECKING:
    from .app import Controller

log = logging.getLogger(__name__)
APP_ROLE = Qt.UserRole
SORT_ROLE = Qt.UserRole + 1


def _button(text: str, name: str | None = None, on_click=None) -> QPushButton:
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    if on_click:
        b.clicked.connect(on_click)
    return b


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


class SortItem(QTreeWidgetItem):
    """Sorts sizes and dates by their value, not their text."""
    def __lt__(self, other):
        tree = self.treeWidget()
        col = tree.sortColumn() if tree else 0
        a, b = self.data(col, SORT_ROLE), other.data(col, SORT_ROLE)
        if a is not None and b is not None:
            return a < b
        return self.text(col).lower() < other.text(col).lower()


class _Signals(QObject):
    listed = Signal(object)         # list[App] or an Exception
    sized = Signal(object)          # App
    state = Signal(str, object)     # what the uninstall dialog is doing


def _icon_for(app: un.App, provider: QFileIconProvider) -> QIcon | None:
    for path in (app.icon, os.path.join(app.location, "") if app.location else ""):
        if path and os.path.exists(path) and (os.path.isdir(path) or path.lower().endswith((".exe", ".ico", ".dll"))):
            icon = provider.icon(QFileInfo(path))
            if not icon.isNull():
                return icon
    return None


# ----------------------------------------------------------------------------- the page

class AppsPage(QWidget):
    def __init__(self, controller: "Controller"):
        super().__init__()
        self.c = controller
        self.apps: list[un.App] = []
        self.loaded = False
        self.loading = False
        self.signals = _Signals(self)
        self.signals.listed.connect(self._listed)
        self.signals.sized.connect(self._sized)
        self.cancel = threading.Event()
        self.provider = QFileIconProvider()
        self._icon_queue: list[QTreeWidgetItem] = []
        self.icon_timer = QTimer(self)
        self.icon_timer.timeout.connect(self._load_icons)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(12)
        lay.addWidget(_label(tr("Apps"), "h1"))
        lay.addWidget(_label(tr("Uninstall programs, then clean up the folders, shortcuts and registry entries they "
                                "leave behind. Everything removed is kept as a backup for {days} days, so you can "
                                "put it back.", days=un.KEEP_DAYS), "muted", wrap=True))

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search apps"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.store = QCheckBox(tr("Store apps"))
        self.store.setChecked(True)
        self.store.toggled.connect(self._filter)
        top.addWidget(self.search, 1)
        top.addWidget(self.store)
        top.addWidget(_button(tr("Refresh"), on_click=self.reload))
        lay.addLayout(top)

        self.tree = QTreeWidget()
        self.tree.setObjectName("apps")
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels([tr("Name"), tr("Publisher"), tr("Size"), tr("Installed")])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        h = self.tree.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3):
            h.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._selection)
        self.tree.itemDoubleClicked.connect(lambda *_: self.uninstall_selected())
        lay.addWidget(self.tree, 1)

        self.status = _label(tr("Loading the list of apps…"), "muted")
        lay.addWidget(self.status)

        row = QHBoxLayout()
        self.b_uninstall = _button(tr("Uninstall"), "primary", self.uninstall_selected)
        self.b_leftovers = _button(tr("Remove leftovers only"), on_click=lambda: self.uninstall_selected(forced=True))
        self.b_leftovers.setToolTip(tr("For apps whose uninstaller is missing or broken: skips it and only offers "
                                       "what the app left behind."))
        self.b_folder = _button(tr("Open its folder"), on_click=self.open_folder)
        row.addWidget(self.b_uninstall)
        row.addWidget(self.b_leftovers)
        row.addWidget(self.b_folder)
        row.addStretch()
        row.addWidget(_button(tr("Backups…"), on_click=self.show_backups))
        lay.addLayout(row)
        self._selection()

    # ---- loading

    def ensure_loaded(self):
        if not self.loaded and not self.loading:
            self.reload()

    def reload(self):
        if self.loading:
            return
        self.loading = True
        self.cancel.set()
        self.cancel = threading.Event()
        self.status.setText(tr("Loading the list of apps…"))
        signals, cancel = self.signals, self.cancel

        def work():
            # programs first (quick), then Store apps (Windows takes a few seconds to list those)
            try:
                apps = un.list_apps(include_store=False)
            except Exception as e:
                log.exception("couldn't list apps")
                apps = e
            try:
                signals.listed.emit(apps)
            except RuntimeError:
                return
            if isinstance(apps, list) and not cancel.is_set():
                try:
                    store = un.store_apps()
                except Exception:
                    log.debug("couldn't list Store apps", exc_info=True)
                    store = []
                if store and not cancel.is_set():
                    apps = sorted(apps + store, key=lambda a: a.name.lower())
                    try:
                        signals.listed.emit(apps)
                    except RuntimeError:
                        return
            if isinstance(apps, list):
                missing = sorted([a for a in apps if not a.size], key=lambda a: a.name.lower())

                def sized(a):
                    try:
                        signals.sized.emit(a)
                    except RuntimeError:
                        cancel.set()
                un.fill_sizes(missing, cancel, sized)
        threading.Thread(target=work, daemon=True).start()

    def _listed(self, apps):
        self.loading = False
        if isinstance(apps, Exception):
            self.status.setText(tr("Couldn't read the list of apps: {error}", error=str(apps)))
            return
        self.loaded = True
        self.apps = apps
        selected = {i.data(0, APP_ROLE).id for i in self.tree.selectedItems()}
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        self._icon_queue = []
        for app in apps:
            item = SortItem([app.name, app.publisher, human_size(app.size) if app.size else "",
                             app.date])
            item.setData(0, APP_ROLE, app)
            item.setData(2, SORT_ROLE, app.size)
            item.setData(3, SORT_ROLE, app.date or "0")
            tip = [app.name]
            if app.version:
                tip.append(tr("Version {version}", version=app.version))
            if app.location:
                tip.append(app.location)
            if app.store:
                tip.append(tr("From the Microsoft Store"))
            elif app.broken:
                tip.append(tr("Its uninstaller is missing: use \"Remove leftovers only\"."))
            item.setToolTip(0, "\n".join(tip))
            if app.broken:
                item.setForeground(0, self.palette().placeholderText())
            self.tree.addTopLevelItem(item)
            if app.id in selected:
                item.setSelected(True)
            self._icon_queue.append(item)
        self.tree.setSortingEnabled(True)
        self._filter()
        self.icon_timer.start(15)

    def _load_icons(self):
        """A few icons at a time, so the list shows straight away."""
        for _ in range(12):
            if not self._icon_queue:
                self.icon_timer.stop()
                return
            item = self._icon_queue.pop(0)
            try:
                icon = _icon_for(item.data(0, APP_ROLE), self.provider)
            except RuntimeError:
                continue
            if icon is not None:
                item.setIcon(0, icon)

    def _sized(self, app):
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, APP_ROLE) is app:
                item.setText(2, human_size(app.size))
                item.setData(2, SORT_ROLE, app.size)
                break
        self._update_status()

    def _filter(self, *_):
        words = self.search.text().lower().split()
        stores = self.store.isChecked()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            app = item.data(0, APP_ROLE)
            text = f"{app.name} {app.publisher}".lower()
            item.setHidden(not all(w in text for w in words) or (bool(app.store) and not stores))
        self._update_status()

    def _update_status(self):
        shown = [self.tree.topLevelItem(i).data(0, APP_ROLE) for i in range(self.tree.topLevelItemCount())
                 if not self.tree.topLevelItem(i).isHidden()]
        total = sum(a.size for a in shown)
        self.status.setText(tr("{count} apps · {size}", count=len(shown), size=human_size(total)) if self.loaded
                            else tr("Loading the list of apps…"))

    def _selected(self) -> list[un.App]:
        return [i.data(0, APP_ROLE) for i in self.tree.selectedItems()]

    def _selection(self):
        apps = self._selected()
        self.b_uninstall.setEnabled(bool(apps))
        self.b_leftovers.setEnabled(bool(apps) and not any(a.store for a in apps))
        self.b_folder.setEnabled(len(apps) == 1 and bool(apps[0].location) and os.path.isdir(apps[0].location))

    # ---- actions

    def open_folder(self):
        apps = self._selected()
        if apps and apps[0].location and os.path.isdir(apps[0].location):
            os.startfile(apps[0].location)  # noqa: S606

    def uninstall_selected(self, forced: bool = False):
        apps = self._selected()
        if not apps:
            return
        if any(_is_self(a) for a in apps):
            QMessageBox.information(self, tr("Apps"), tr("To remove Corefold itself, use \"Uninstall Corefold\" on "
                                                         "the About page."))
            return
        dlg = UninstallDialog(self.c, apps, self.apps, forced, self.window().windowIcon())
        dlg.exec()
        dlg.deleteLater()
        if dlg.changed:
            self.reload()

    def show_backups(self):
        dlg = BackupsDialog(self.c, self.window().windowIcon())
        dlg.exec()
        dlg.deleteLater()
        if dlg.changed:
            self.reload()


def _is_self(app: un.App) -> bool:
    if app.name.lower().startswith(("corefold", LEGACY_NAME.lower())):
        return True
    if getattr(sys, "frozen", False) and app.location:
        return os.path.normcase(os.path.dirname(sys.executable)).startswith(
            os.path.normcase(app.location.rstrip("\\/")))
    return False


# ----------------------------------------------------------------------------- uninstalling

class UninstallDialog(QDialog):
    """Confirm → the app's own uninstaller → looking for leftovers → choose → removed (with undo)."""

    def __init__(self, controller, apps: list[un.App], everything: list[un.App], forced: bool, icon: QIcon):
        super().__init__(None, Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.c = controller
        self.apps, self.everything, self.forced = apps, everything, forced
        self.changed = False
        self.cancel = threading.Event()
        self.leftovers: list[un.Leftover] = []
        self.backup: Path | None = None
        self.busy = False
        self.setWindowIcon(icon)
        self.setWindowTitle(tr("Uninstall") if not forced else tr("Remove leftovers"))
        self.setMinimumSize(620, 460)
        self.signals = _Signals(self)
        self.signals.state.connect(self._state)

        self.stack = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.addWidget(self.stack, 1)

        # 1. confirm
        p1 = QWidget()
        l1 = QVBoxLayout(p1)
        l1.setContentsMargins(0, 0, 0, 0)
        names = ", ".join(short_name(a.name, 40) for a in apps[:3]) + ("…" if len(apps) > 3 else "")
        title = tr("Uninstall {name}?", name=names) if not forced else tr("Remove what {name} left behind?", name=names)
        l1.addWidget(_label(title, "h2", wrap=True))
        if forced:
            l1.addWidget(_label(tr("The app's uninstaller won't run. Corefold only looks for its folders, shortcuts "
                                   "and registry entries, and you choose what goes."), "muted", wrap=True))
        else:
            l1.addWidget(_label(tr("First the app's own uninstaller runs (follow its steps). Then Corefold looks for "
                                   "anything it left behind, and you choose what goes."), "muted", wrap=True))
        self.quiet = QCheckBox(tr("Uninstall quietly where the app allows it (no questions)"))
        self.quiet.setVisible(not forced and any(a.quiet or a.msi or a.store for a in apps))
        self.quiet.setChecked(len(apps) > 1)
        self.restore_point = QCheckBox(tr("Make a System Restore point first (asks for administrator)"))
        self.restore_point.setVisible(not forced)
        l1.addSpacing(6)
        l1.addWidget(self.quiet)
        l1.addWidget(self.restore_point)
        l1.addStretch()
        r1 = QHBoxLayout()
        r1.addStretch()
        r1.addWidget(_button(tr("Cancel"), on_click=self.reject))
        self.go = _button(tr("Uninstall") if not forced else tr("Look for leftovers"), "primary", self.start)
        r1.addWidget(self.go)
        l1.addLayout(r1)
        self.stack.addWidget(p1)

        # 2. working (uninstaller running, scanning, removing)
        p2 = QWidget()
        l2 = QVBoxLayout(p2)
        l2.setContentsMargins(0, 0, 0, 0)
        self.work_title = _label("", "h2", wrap=True)
        self.work_detail = _label("", "muted", wrap=True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        l2.addStretch()
        l2.addWidget(self.work_title)
        l2.addWidget(self.work_detail)
        l2.addWidget(self.bar)
        l2.addStretch()
        r2 = QHBoxLayout()
        r2.addStretch()
        self.skip = _button(tr("The uninstaller is done"), on_click=self._skip_wait)
        self.skip.setToolTip(tr("If the uninstaller has finished but Corefold is still waiting, click here."))
        self.stop = _button(tr("Cancel"), on_click=self._stop)
        r2.addWidget(self.skip)
        r2.addWidget(self.stop)
        l2.addLayout(r2)
        self.stack.addWidget(p2)

        # 3. leftovers
        p3 = QWidget()
        l3 = QVBoxLayout(p3)
        l3.setContentsMargins(0, 0, 0, 0)
        self.left_title = _label("", "h2", wrap=True)
        l3.addWidget(self.left_title)
        l3.addWidget(_label(tr("Ticked items clearly belong to the app. Unticked ones only might, so check them first. "
                               "Everything removed is kept as a backup."), "muted", wrap=True))
        self.left_tree = QTreeWidget()
        self.left_tree.setObjectName("apps")
        self.left_tree.setHeaderLabels([tr("What"), tr("Size")])
        self.left_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.left_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.left_tree.header().setStretchLastSection(False)
        self.left_tree.itemChanged.connect(self._ticks_changed)
        l3.addWidget(self.left_tree, 1)
        r3 = QHBoxLayout()
        self.left_info = _label("", "muted")
        r3.addWidget(self.left_info, 1)
        r3.addWidget(_button(tr("Keep everything"), on_click=self._keep))
        self.remove_btn = _button(tr("Remove selected"), "primary", self.remove_selected)
        r3.addWidget(self.remove_btn)
        l3.addLayout(r3)
        self.stack.addWidget(p3)

        # 4. done
        p4 = QWidget()
        l4 = QVBoxLayout(p4)
        l4.setContentsMargins(0, 0, 0, 0)
        self.done_title = _label("", "h2", wrap=True)
        self.done_detail = _label("", "muted", wrap=True)
        l4.addStretch()
        l4.addWidget(self.done_title)
        l4.addWidget(self.done_detail)
        l4.addStretch()
        r4 = QHBoxLayout()
        self.undo_btn = _button(tr("Undo"), on_click=self.undo)
        r4.addWidget(self.undo_btn)
        r4.addStretch()
        r4.addWidget(_button(tr("Close"), "primary", self.accept))
        l4.addLayout(r4)
        self.stack.addWidget(p4)
        self.skip_event = threading.Event()

    # ---- running

    def _emit(self, state: str, data=None):
        try:
            self.signals.state.emit(state, data)
        except RuntimeError:
            self.cancel.set()

    def start(self):
        self.busy = True
        self.stack.setCurrentIndex(1)
        quiet, point = self.quiet.isChecked(), self.restore_point.isChecked() and self.restore_point.isVisible()
        apps, everything, forced = list(self.apps), list(self.everything), self.forced
        cancel, skip = self.cancel, self.skip_event
        command = _app_command()

        def work():
            if point:
                self._emit("restore_point")
                un.run_elevated(command, "restore-point",
                                {"description": tr("Before uninstalling {name}", name=apps[0].name)}, cancel)
            removed, still = [], []
            for n, app in enumerate(apps):
                if cancel.is_set():
                    break
                if forced:
                    removed.append(app)
                    continue
                self._emit("running", (app, n, len(apps)))
                handle = un.start_uninstall(app, quiet)
                if handle is None and not app.store:
                    self._emit("not_started", app)
                    continue
                skip.clear()
                gone = _wait(app, handle, cancel, skip, lambda s: self._emit(s, app))
                if gone:
                    removed.append(app)
                    self.changed = True
                else:  # cancelled in its uninstaller, or it's still listed: its files are left alone
                    still.append(app)
            if cancel.is_set() or not removed:
                self._emit("stopped", still)
                return
            self._emit("scanning", None)
            installed = [a for a in everything if a not in removed]
            found = []
            for app in removed:
                try:
                    found += un.find_leftovers(app, installed, forced=forced or app.broken)
                except Exception:
                    log.exception("looking for leftovers of %s failed", app.name)
            self._emit("found", (removed, found))
        threading.Thread(target=work, daemon=True).start()

    def _skip_wait(self):
        self.skip_event.set()

    def _stop(self):
        self.cancel.set()
        self.reject()

    def _state(self, state: str, data):
        if state == "restore_point":
            self.work_title.setText(tr("Making a restore point…"))
            self.work_detail.setText(tr("This can take a minute."))
            self.skip.hide()
        elif state == "running":
            app, n, total = data
            self.work_title.setText(tr("Uninstalling {name}…", name=app.name) +
                                    (f"  ({n + 1}/{total})" if total > 1 else ""))
            self.work_detail.setText(tr("Follow the steps in the app's uninstaller window. Corefold waits until "
                                        "it's finished."))
            self.skip.show()
        elif state == "finishing":
            self.work_detail.setText(tr("Waiting for the uninstaller to finish…"))
        elif state == "not_started":
            self.work_detail.setText(tr("{name}'s uninstaller couldn't be started.", name=data.name))
        elif state == "scanning":
            self.skip.hide()
            self.work_title.setText(tr("Looking for leftovers…"))
            self.work_detail.setText(tr("Checking program folders, app data, shortcuts and the registry."))
        elif state == "stopped":
            self.busy = False
            detail = tr("The uninstaller didn't run or was cancelled, so Corefold didn't look for leftovers.")
            if data:  # it's still listed as installed
                detail += "\n" + tr('If the app is gone but still listed, use "Remove leftovers only".')
            self._finish(tr("Nothing was removed"), detail, undo=False)
        elif state == "found":
            removed, found = data
            self.busy = False
            self.leftovers = found
            if not found:
                self._finish(tr("All clean"), tr("{name} left nothing behind.", name=removed[0].name)
                             if len(removed) == 1 else tr("These apps left nothing behind."), undo=False)
                return
            self._show_leftovers(removed, found)
        elif state == "removed":
            self._removed(data)

    # ---- choosing leftovers

    def _show_leftovers(self, removed, found):
        self.left_tree.blockSignals(True)
        self.left_tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for item in found:
            group = groups.get(item.reason)
            if group is None:
                group = QTreeWidgetItem([tr(un.REASONS.get(item.reason, item.reason)), ""])
                group.setFlags(group.flags() | Qt.ItemIsAutoTristate | Qt.ItemIsUserCheckable)
                f = group.font(0)
                f.setBold(True)
                group.setFont(0, f)
                self.left_tree.addTopLevelItem(group)
                groups[item.reason] = group
            row = QTreeWidgetItem([item.label(), human_size(item.size) if item.size else ""])
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(0, Qt.Checked if item.safe else Qt.Unchecked)
            row.setData(0, APP_ROLE, item)
            row.setToolTip(0, item.label())
            group.addChild(row)
        self.left_tree.expandAll()
        self.left_tree.blockSignals(False)
        name = removed[0].name if len(removed) == 1 else tr("{count} apps", count=len(removed))
        self.left_title.setText(tr("{name} left {count} things behind", name=name, count=len(found)))
        self.stack.setCurrentIndex(2)
        self._ticks_changed()

    def _chosen(self) -> list[un.Leftover]:
        out = []
        for g in range(self.left_tree.topLevelItemCount()):
            group = self.left_tree.topLevelItem(g)
            for c in range(group.childCount()):
                row = group.child(c)
                if row.checkState(0) == Qt.Checked:
                    out.append(row.data(0, APP_ROLE))
        return out

    def _ticks_changed(self, *_):
        chosen = self._chosen()
        size = sum(i.size for i in chosen)
        self.left_info.setText(tr("{count} selected · {size}", count=len(chosen), size=human_size(size)))
        self.remove_btn.setEnabled(bool(chosen))

    def _keep(self):
        self.accept()

    def remove_selected(self):
        chosen = self._chosen()
        if not chosen:
            return
        self.busy = True
        self.stack.setCurrentIndex(1)
        self.skip.hide()
        self.stop.setEnabled(False)
        self.work_title.setText(tr("Removing leftovers…"))
        self.work_detail.setText(tr("Making a backup of everything first."))
        apps = sorted({a.name for a in self.apps})
        command = _app_command()

        def work():
            folder = un.new_backup(apps)
            result = un.remove(chosen, folder, apps)
            failed = un.items_from(result["failed"])
            needs_admin = [i for i in failed if i.needs_admin]
            if needs_admin:
                self._emit("admin", None)
                more = un.run_elevated(command, "cleanup", {"items": result["failed"], "folder": str(folder),
                                                            "apps": apps})
                if more and "done" in more:
                    result["done"] += more["done"]
                    result["freed"] += more.get("freed", 0)
                    result["failed"] = more.get("failed", [])
            self._emit("removed", (folder, result))
        threading.Thread(target=work, daemon=True).start()

    def _removed(self, data):
        self.busy = False
        self.stop.setEnabled(True)
        folder, result = data
        self.backup = folder if any(d.get("stored") for d in result["done"]) else None
        self.changed = True
        done, failed = len(result["done"]), len(result["failed"])
        detail = tr("{size} freed. Everything is kept as a backup for {days} days: undo it here or from "
                    "\"Backups…\" on the Apps page.", size=human_size(result["freed"]), days=un.KEEP_DAYS)
        if failed:
            detail += "\n" + tr("{count} items couldn't be removed (in use, or administrator permission was "
                                "refused). Restarting the PC and trying again usually helps.", count=failed)
        self._finish(tr("Removed {count} leftovers", count=done), detail, undo=self.backup is not None)
        if self.c is not None:
            self.c.play_done_sound()

    def _finish(self, title: str, detail: str, undo: bool):
        self.done_title.setText(title)
        self.done_detail.setText(detail)
        self.undo_btn.setVisible(undo)
        self.stack.setCurrentIndex(3)

    def undo(self):
        if self.backup is None:
            return
        result = restore_backup(self.backup)
        if result["failed"]:
            self.done_detail.setText(tr("{count} items couldn't be put back.", count=len(result["failed"])))
        else:
            self.done_title.setText(tr("Put back"))
            self.done_detail.setText(tr("Everything that was removed is back where it was. (The app itself stays "
                                        "uninstalled: install it again to use it.)"))
        if result.get("recycled"):
            self.done_detail.setText(self.done_detail.text() + "\n" + _recycled_text(result["recycled"]))
        self.undo_btn.hide()

    def reject(self):
        if self.busy and self.stack.currentIndex() == 1 and not self.stop.isEnabled():
            return  # removing: don't close half-way
        self.cancel.set()
        super().reject()

    def closeEvent(self, e):
        if self.busy and not self.stop.isEnabled():
            e.ignore()
            return
        self.cancel.set()
        super().closeEvent(e)


def _wait(app, handle, cancel, skip, on_state) -> bool:
    both = threading.Event()

    def watch():
        while not (cancel.is_set() or skip.is_set() or both.is_set()):
            time.sleep(0.2)
        both.set()
    threading.Thread(target=watch, daemon=True).start()
    try:
        return un.wait_until_removed(app, handle, both, on_state)
    finally:
        both.set()


def _app_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return [sys.executable, str(main_py)]


def _recycled_text(count: int) -> str:
    return tr("{count} items from another drive went to the Recycle Bin: put them back from there.", count=count)


def restore_backup(folder: Path) -> dict:
    result = un.restore(folder)
    if result["failed"]:
        more = un.run_elevated(_app_command(), "restore", {"folder": str(folder)})
        if more and "failed" in more:
            result = {"done": result["done"] + more.get("done", 0), "failed": more["failed"],
                      "recycled": result.get("recycled", 0)}
    return result


# ----------------------------------------------------------------------------- backups

class BackupsDialog(QDialog):
    def __init__(self, controller, icon: QIcon):
        super().__init__(None, Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.c = controller
        self.changed = False
        self.setWindowIcon(icon)
        self.setWindowTitle(tr("Backups"))
        self.setMinimumSize(560, 380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.addWidget(_label(tr("Backups"), "h2"))
        lay.addWidget(_label(tr("What Corefold removed after uninstalling apps. Put it back, or delete the backup to "
                                "free the space. Backups are deleted by themselves after {days} days.",
                                days=un.KEEP_DAYS), "muted", wrap=True))
        self.tree = QTreeWidget()
        self.tree.setObjectName("apps")
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels([tr("Apps"), tr("When"), tr("Items")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.itemSelectionChanged.connect(self._selection)
        lay.addWidget(self.tree, 1)
        self.empty = _label(tr("No backups."), "muted")
        lay.addWidget(self.empty)
        row = QHBoxLayout()
        self.b_restore = _button(tr("Put back"), "primary", self.restore)
        self.b_delete = _button(tr("Delete backup"), "danger", self.delete)
        row.addWidget(self.b_restore)
        row.addWidget(self.b_delete)
        row.addStretch()
        row.addWidget(_button(tr("Close"), on_click=self.accept))
        lay.addLayout(row)
        self.fill()

    def fill(self):
        self.tree.clear()
        backups = un.list_backups()
        for folder, data in backups:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(data.get("time", 0)))
            item = QTreeWidgetItem([", ".join(data.get("apps", [])) or folder.name, when,
                                    str(len(data.get("items", [])))])
            item.setData(0, APP_ROLE, folder)
            item.setToolTip(0, "\n".join(i.get("path", "") for i in data.get("items", [])[:25]))
            self.tree.addTopLevelItem(item)
        self.empty.setVisible(not backups)
        self._selection()

    def _folder(self) -> Path | None:
        items = self.tree.selectedItems()
        return items[0].data(0, APP_ROLE) if items else None

    def _selection(self):
        has = self._folder() is not None
        self.b_restore.setEnabled(has)
        self.b_delete.setEnabled(has)

    def restore(self):
        folder = self._folder()
        if folder is None:
            return
        result = restore_backup(folder)
        self.changed = True
        if result["failed"]:
            QMessageBox.warning(self, tr("Backups"), tr("{count} items couldn't be put back.",
                                                        count=len(result["failed"])))
        elif result.get("recycled"):
            QMessageBox.information(self, tr("Backups"), _recycled_text(result["recycled"]))
        self.fill()

    def delete(self):
        folder = self._folder()
        if folder is None:
            return
        if QMessageBox.question(self, tr("Backups"), tr("Delete this backup for good? It can't be put back after "
                                                       "that.")) != QMessageBox.Yes:
            return
        if not un.delete_backup(folder):
            QMessageBox.warning(self, tr("Backups"), tr("Some files in the backup couldn't be deleted."))
        self.fill()
