"""The Home page's drop area: drop (or choose, or paste) files and see every option as buttons,
grouped by what they do. Options that don't fit the files are greyed out with the reason.

Anything slow (thumbnails, reading a video to see if it has sound) happens in the background,
so dropping a big file never makes the window freeze."""
from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from .converters import CATEGORY_ICONS, CATEGORY_NAMES, pick, warm_up
from . import icons
from .core import human_size, is_cloud_only, quick_size, short_name
from .i18n import tr
from .wheel import FORMAT_ACTIONS, GROUP_NAMES, GROUPS, ICONS, action_of

if TYPE_CHECKING:
    from .app import Controller

GROUP_ORDER = ["convert", "edit", "shrink", "read", "archive"]


class FlowLayout(QLayout):
    """Puts widgets in rows and wraps to the next row when there's no more room
    (right to left in Arabic)."""

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
        rtl = QGuiApplication.layoutDirection() == Qt.RightToLeft
        x, y, line = rect.x(), rect.y(), 0
        for item in self.items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:
                x, y, line = rect.x(), y + line + self.space, 0
            if not dry:
                left = rect.right() + 1 - (x - rect.x()) - hint.width() if rtl else x
                item.setGeometry(QRect(QPoint(left, y), hint))
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

    def __init__(self, on_choose, on_paste, title: str = "", sub: str = "", hot_title: str = "",
                 buttons: list | None = None):
        super().__init__()
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(118)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        self.title_text = title or tr("Drop files or folders here")
        self.hot_text = hot_title or tr("Let go to see the options")
        self.title = QLabel(self.title_text)
        self.title.setObjectName("h2")
        self.sub = QLabel(sub or tr("Images, video, audio, PDFs, Office files, ZIP / RAR / 7z… You'll see everything "
                                    "Corefold can do with them."))
        self.sub.setObjectName("muted")
        self.sub.setWordWrap(True)
        for w in (self.title, self.sub):
            w.setAlignment(Qt.AlignCenter)
        row = QHBoxLayout()
        row.addStretch()
        for text, fn, *style in buttons or ((tr("Choose files…"), on_choose), (tr("Paste (Ctrl + V)"), on_paste)):
            b = QPushButton(text)
            if style:
                b.setObjectName(style[0])
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
        self.title.setText(self.hot_text if on else self.title_text)

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


def make_thumbnail(path: Path, kind: str) -> bytes | None:
    """A small PNG preview of a picture or video (runs in a background thread)."""
    if is_cloud_only(path):
        return None  # only in OneDrive: a preview would download the whole file
    try:
        if kind == "image" and path.is_file():
            from PIL import Image, ImageOps
            from .converters import setup_pillow
            setup_pillow()
            from .converters import load_image
            if path.suffix.lower() in (".jpg", ".jpeg", ".jfif"):
                # JPEG can be decoded at a fraction of its size: a 50-megapixel photo loads in a blink
                with Image.open(path) as raw:
                    raw.draft("RGB", (288, 288))
                    im = ImageOps.exif_transpose(raw)
                    im.load()
            else:
                im = load_image(path)
            im.thumbnail((144, 144))
            buf = io.BytesIO()
            im.convert("RGBA").save(buf, "PNG")
            return buf.getvalue()
        if kind == "video" and path.is_file():
            from .converters import media_duration, video_frame
            return video_frame(path, min(1.0, media_duration(path) / 3), 144)
    except Exception:
        return None
    return None


class FilePanel(QFrame):
    """Shows the dropped files and every option for them."""
    _ready = Signal(int, object)   # load number, thumbnail bytes (or None)

    def __init__(self, controller: "Controller"):
        super().__init__()
        self.c = controller
        self.files: list[Path] = []
        self.paths: list[Path] = []
        self.load_id = 0
        self.thumb_for: tuple | None = None
        self.setObjectName("card")
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
        clear = QPushButton(tr("✕ Clear"))
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
        self._ready.connect(self._background_done)
        self.setVisible(False)

    def clear(self):
        self.load_id += 1
        self.files = []
        self.paths = []
        self.thumb_for = None
        clear_layout(self.groups)
        self.setVisible(False)

    def refresh(self):
        if self.paths:
            self.set_files(self.paths)  # slow checks again in the background, never on the window

    def set_files(self, paths: list[Path], quick: bool = True):
        s = self.c.settings
        paths = [p for p in paths if p.exists()]
        if not paths:
            self.clear()
            return
        self.paths = paths
        pk = pick(paths, set(s.hidden_tools), s.favorites, True, s.tool_order, s.pack_on_every_wheel, quick=quick)
        self.files = pk.files
        clear_layout(self.groups)

        # header
        first = pk.files[0]
        sizes = [quick_size(p, 0.25 / max(1, len(pk.files))) for p in pk.files]
        total = sum(b for b, _ in sizes)
        size_text = human_size(total) + ("+" if not all(done for _, done in sizes) else "")
        self.name.setToolTip(first.name if len(pk.files) == 1 else "")
        if len(pk.files) == 1:
            self.name.setText(short_name(first.name))
            if first.is_dir():
                what = tr("Folder")
            elif first.suffix:
                what = tr("{format} file", format=first.suffix[1:].upper())
            else:
                what = tr("File")
            self.details.setText(tr("{what} · {size} · in {folder}", what=what, size=size_text,
                                    folder=short_name(str(first.parent), 70)))
        else:
            kind_name = tr("Files and folders") if pk.kind == "files" else \
                tr(CATEGORY_NAMES.get(pk.kind, "Images")).split(" (")[0]
            self.name.setText(tr("{count} items", count=len(pk.files)))
            self.details.setText(tr("{kind} · {size} in total", kind=kind_name, size=size_text))
        bits = [tr("Things you can do: {count}", count=sum(1 for sl in pk.slots if sl.ok))]
        if pk.dimmed:
            bits.append(tr("{count} don't fit (greyed out, point at one to see why)", count=pk.dimmed))
        if pk.hidden:
            bits.append(tr("{count} hidden in Settings → Formats", count=pk.hidden))
        if len(paths) > len(pk.files):
            bits.append(tr("Other parts of a split archive open with the first part"))
        self.summary.setText(" · ".join(bits))

        # buttons, grouped
        favs = set(s.favorites)
        by_group: dict[str, list] = {}
        for slot in pk.slots:
            by_group.setdefault(GROUPS.get(action_of(slot.tool.key), "convert"), []).append(slot)
        for group in GROUP_ORDER:
            slots = by_group.get(group)
            if not slots:
                continue
            title = QLabel(tr(GROUP_NAMES[group]).upper())
            title.setObjectName("section")
            self.groups.addWidget(title)
            holder = QWidget()
            flow = FlowLayout(holder)
            for slot in slots:
                flow.addWidget(self._button(slot, slot.tool.key in favs))
            self.groups.addWidget(holder)
        if not pk.slots:
            msg = QLabel(tr("Nothing to do with these files. Check Settings → Formats if you hid some options."))
            msg.setObjectName("muted")
            msg.setWordWrap(True)
            self.groups.addWidget(msg)
        self.setVisible(True)

        # thumbnail + slow checks in the background
        key = (str(first), pk.kind)
        need_thumb = key != self.thumb_for
        if need_thumb:
            self._placeholder(first, pk.kind)
        if need_thumb or pk.pending:
            self.load_id += 1
            load_id, files, kind, pending = self.load_id, list(pk.files), pk.kind, pk.pending
            self.thumb_for = key

            def work():
                if pending:
                    warm_up(files)
                data = make_thumbnail(files[0], kind) if need_thumb else b""
                try:
                    self._ready.emit(load_id, (data, pending))
                except RuntimeError:
                    pass  # the window was closed and freed meanwhile
            threading.Thread(target=work, daemon=True).start()

    def _background_done(self, load_id: int, payload):
        if load_id != self.load_id:
            return  # something else was dropped meanwhile
        data, pending = payload
        if data:
            pm = QPixmap()
            if pm.loadFromData(data):
                self.thumb.setStyleSheet("")
                self.thumb.setPixmap(pm.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        if pending and self.paths:
            self.set_files(self.paths, quick=False)

    def _placeholder(self, path: Path, kind: str):
        name = "folder" if path.is_dir() else CATEGORY_ICONS.get(kind, "box")
        self.thumb.setText("")
        self.thumb.setStyleSheet("")
        self.thumb.setPixmap(icons.pixmap(name, 44, self.c.theme.muted, self.devicePixelRatioF()))

    def _button(self, slot, favorite: bool) -> QPushButton:
        action = action_of(slot.tool.key)
        icon = "" if action in FORMAT_ACTIONS else ICONS.get(action, "")
        text = ("★ " if favorite else "") + slot.tool.label
        b = QPushButton(text)
        if icon:
            b.setIcon(icons.icon(icon, self.c.theme.text, self.c.theme.muted))
            b.setIconSize(icons.SMALL)
        b.setObjectName("action")
        b.setCursor(Qt.PointingHandCursor if slot.ok else Qt.ForbiddenCursor)
        b.setToolTip(slot.reason or slot.tool.description)
        b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        b.setEnabled(slot.ok)
        if slot.ok:
            b.clicked.connect(lambda _=False, t=slot.tool: self.c.tool_chosen(t, list(self.files)))
        b.setMinimumWidth(92)
        return b


def choose_files(parent) -> list[Path]:
    paths, _ = QFileDialog.getOpenFileNames(parent, tr("Choose files for Corefold"), str(Path.home()))
    return [Path(p) for p in paths]
