"""The Home page's drop area: drop (or choose, or paste) files and see every option as buttons,
grouped by what they do. Options that don't fit the files are greyed out with the reason."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFileDialog, QFileIconProvider, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from .converters import CATEGORY_ICONS, CATEGORY_NAMES, pick
from .core import human_size
from .history import size_of
from .wheel import FORMAT_ACTIONS, GROUP_NAMES, GROUPS, ICONS, action_of

if TYPE_CHECKING:
    from .app import Controller

GROUP_ORDER = ["convert", "edit", "shrink", "read", "archive"]


class FlowLayout(QLayout):
    """Puts widgets in rows and wraps to the next row when there's no more room."""

    def __init__(self, parent=None, spacing: int = 8):
        super().__init__(parent)
        self.items = []
        self.space = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, dry=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _layout(self, rect, dry):
        x, y, line = rect.x(), rect.y(), 0
        for item in self.items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:
                x, y, line = rect.x(), y + line + self.space, 0
            if not dry:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.space
            line = max(line, hint.height())
        return y + line - rect.y()


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


class DropZone(QFrame):
    """Drop files here, or use the buttons."""
    dropped = Signal(list)

    def __init__(self, on_choose, on_paste):
        super().__init__()
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(118)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        self.title = QLabel("Drop files or folders here")
        self.title.setObjectName("h2")
        self.sub = QLabel("Images, video, audio, PDFs, Office files, ZIP / RAR / 7z… You'll see everything "
                          "Satsuma can do with them.")
        self.sub.setObjectName("muted")
        self.sub.setWordWrap(True)
        for w in (self.title, self.sub):
            w.setAlignment(Qt.AlignCenter)
        row = QHBoxLayout()
        row.addStretch()
        for text, fn in (("Choose files…", on_choose), ("Paste (Ctrl + V)", on_paste)):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addWidget(self.title)
        lay.addWidget(self.sub)
        lay.addLayout(row)

    def _hot(self, on: bool):
        self.setProperty("hot", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.title.setText("Let go to see the options" if on else "Drop files or folders here")

    def dragEnterEvent(self, e):
        if any(u.isLocalFile() for u in e.mimeData().urls()):
            e.setDropAction(Qt.CopyAction)
            e.accept()
            self._hot(True)

    def dragMoveEvent(self, e):
        e.setDropAction(Qt.CopyAction)
        e.accept()

    def dragLeaveEvent(self, e):
        self._hot(False)

    def dropEvent(self, e):
        self._hot(False)
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if p.exists()]
        if paths:
            e.setDropAction(Qt.CopyAction)
            e.accept()
            self.dropped.emit(paths)


class FilePanel(QFrame):
    """Shows the dropped files and every option for them."""

    def __init__(self, controller: "Controller"):
        super().__init__()
        self.c = controller
        self.files: list[Path] = []
        self.setObjectName("card")
        self.icons = QFileIconProvider()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        head = QHBoxLayout()
        self.thumb = QLabel()
        self.thumb.setFixedSize(72, 72)
        self.thumb.setAlignment(Qt.AlignCenter)
        info = QVBoxLayout()
        info.setSpacing(2)
        self.name = QLabel()
        self.name.setObjectName("h2")
        self.name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.details = QLabel()
        self.details.setObjectName("muted")
        self.details.setWordWrap(True)
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        info.addWidget(self.name)
        info.addWidget(self.details)
        info.addWidget(self.summary)
        clear = QPushButton("✕ Clear")
        clear.setObjectName("flat")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(self.clear)
        head.addWidget(self.thumb)
        head.addSpacing(6)
        head.addLayout(info, 1)
        head.addWidget(clear, 0, Qt.AlignTop)
        lay.addLayout(head)

        self.groups = QVBoxLayout()
        self.groups.setSpacing(10)
        lay.addLayout(self.groups)
        self.setVisible(False)

    def clear(self):
        self.files = []
        clear_layout(self.groups)
        self.setVisible(False)

    def refresh(self):
        if self.files:
            self.set_files(self.files)

    def set_files(self, paths: list[Path]):
        s = self.c.settings
        paths = [p for p in paths if p.exists()]
        if not paths:
            self.clear()
            return
        pk = pick(paths, set(s.hidden_tools), s.favorites, True, s.tool_order, s.pack_on_every_wheel)
        self.files = pk.files
        clear_layout(self.groups)

        # header
        first = pk.files[0]
        total = sum(size_of(p) for p in pk.files)
        kind_name = CATEGORY_NAMES.get(pk.kind, "Files").split(" (")[0]
        if len(pk.files) == 1:
            self.name.setText(first.name)
            what = "Folder" if first.is_dir() else (first.suffix[1:].upper() + " file" if first.suffix else "File")
            self.details.setText(f"{what} · {human_size(total)} · in {first.parent}")
        else:
            self.name.setText(f"{len(pk.files)} items")
            self.details.setText(f"{kind_name} · {human_size(total)} in total")
        dropped_others = len(paths) - len(pk.files)
        usable = sum(1 for sl in pk.slots if sl.ok)
        bits = [f"{usable} thing{'s' if usable != 1 else ''} you can do"]
        if pk.dimmed:
            bits.append(f"{pk.dimmed} don't fit (greyed out, point at one to see why)")
        if pk.hidden:
            bits.append(f"{pk.hidden} hidden in Settings → Formats")
        if dropped_others:
            bits.append(f"{dropped_others} split-archive part{'s' if dropped_others != 1 else ''} opened with the first one")
        self.summary.setText(" · ".join(bits))
        self._thumbnail(first, pk.kind)

        # buttons, grouped
        favs = set(s.favorites)
        by_group: dict[str, list] = {}
        for slot in pk.slots:
            by_group.setdefault(GROUPS.get(action_of(slot.tool.key), "convert"), []).append(slot)
        for group in GROUP_ORDER:
            slots = by_group.get(group)
            if not slots:
                continue
            title = QLabel(GROUP_NAMES[group].upper())
            title.setObjectName("section")
            self.groups.addWidget(title)
            holder = QWidget()
            flow = FlowLayout(holder)
            for slot in slots:
                flow.addWidget(self._button(slot, slot.tool.key in favs))
            self.groups.addWidget(holder)
        if not pk.slots:
            msg = QLabel("Nothing to do with these files. Check Settings → Formats if you hid some options.")
            msg.setObjectName("muted")
            msg.setWordWrap(True)
            self.groups.addWidget(msg)
        self.setVisible(True)

    def _button(self, slot, favorite: bool) -> QPushButton:
        action = action_of(slot.tool.key)
        icon = "" if action in FORMAT_ACTIONS else ICONS.get(action, "")
        text = ("★ " if favorite else "") + (f"{icon}  " if icon else "") + slot.tool.label
        b = QPushButton(text)
        b.setObjectName("action")
        b.setCursor(Qt.PointingHandCursor if slot.ok else Qt.ForbiddenCursor)
        b.setToolTip(slot.reason or slot.tool.description)
        b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        b.setEnabled(slot.ok)
        if slot.ok:
            b.clicked.connect(lambda _=False, t=slot.tool: self.c.tool_chosen(t, list(self.files)))
        b.setMinimumWidth(92)
        return b

    def _thumbnail(self, path: Path, kind: str):
        pm = QPixmap()
        if kind == "image" and path.is_file():
            try:
                import io

                from .converters import load_image
                im = load_image(path)
                im.thumbnail((144, 144))
                buf = io.BytesIO()
                im.convert("RGBA").save(buf, "PNG")
                pm.loadFromData(buf.getvalue())
            except Exception:
                pm = QPixmap()
        elif kind == "video" and path.is_file():
            from .converters import media_duration, video_frame
            data = video_frame(path, min(1.0, media_duration(path) / 3), 144)
            if data:
                pm.loadFromData(data)
        if not pm.isNull():
            self.thumb.setPixmap(pm.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.thumb.setText("")
            return
        emoji = CATEGORY_ICONS.get(kind if kind != "files" else ("folder" if path.is_dir() else "files"), "📦")
        if path.is_dir():
            emoji = "📁"
        self.thumb.setPixmap(QPixmap())
        self.thumb.setText(emoji)
        self.thumb.setStyleSheet("font-size: 34pt;")


def choose_files(parent) -> list[Path]:
    paths, _ = QFileDialog.getOpenFileNames(parent, "Choose files for Satsuma", str(Path.home()))
    return [Path(p) for p in paths]

