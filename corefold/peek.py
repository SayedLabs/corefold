"""Quick look (part of Corefold Viewer): select a file in File Explorer or on the desktop, press
Ctrl + Space, and see what's in it without opening it.

Made only while the Viewer is in use and the shortcut isn't switched off. The shortcut is only taken
while File Explorer or the desktop is in front (peekshell.py); the window (peekview.py) is built the
first time it's needed and freed a while after it's closed."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt

log = logging.getLogger("corefold")


class PeekTool(QObject):
    def __init__(self, controller):
        super().__init__()
        from .peekshell import Watcher
        self.c = controller
        self.window = None   # peekview.PeekWindow, while it's open (and a little after)
        self.watcher = Watcher()
        self.watcher.picked.connect(self.picked, Qt.ConnectionType.QueuedConnection)
        self.watcher.siblings.connect(self._siblings, Qt.ConnectionType.QueuedConnection)
        self.last_open_ms = 0.0   # how long the last one took, from the key press to the window (for the tests)
        self.apply_settings()

    @property
    def ok(self) -> bool:
        """False when another app took the shortcut first (then it can't work)."""
        return self.watcher.ok

    def apply_settings(self):
        self.watcher.set(self.c.settings.peek_key)

    def stop(self):
        self.watcher.stop()
        if self.window is not None:
            self.window.hide()
            self.window.deleteLater()
            self.window = None

    def apply_theme(self, theme):
        if self.window is not None:
            self.window.apply_theme(theme)

    def picked(self, hwnd: int, paths: list):
        """The shortcut was pressed in File Explorer: show what's selected (or close it if it's that again)."""
        start = time.perf_counter()
        files = [Path(p) for p in paths]
        w = self.window
        if w is not None and w.isVisible():
            if not files or files == w.files or (len(files) == 1 and w.current() == files[0]):
                w.close_peek()
                return
        if not files:
            return
        if w is None:
            from .peekview import PeekWindow
            w = self.window = PeekWindow(self)
        w.show_files(files, hwnd)
        self.last_open_ms = (time.perf_counter() - start) * 1000
        log.info("quick look opened in %.0f ms", self.last_open_ms)

    def show(self, paths: list[Path], hwnd: int = 0):
        """Opens it on these files (as if they were selected)."""
        self.picked(hwnd, [str(p) for p in paths])

    def _siblings(self, hwnd: int, paths: list):
        if self.window is not None and paths:
            self.window.set_folder(hwnd, [Path(p) for p in paths])
