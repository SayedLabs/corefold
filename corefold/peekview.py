"""Quick look's window: a look at the file selected in File Explorer without opening it, like turning
it over in your hand. Pictures, videos and music, PDFs, text and code, folders and archives each get
their own view; anything else shows its icon, type, size and dates.

The window opens at once with the file's icon and details; the preview itself is read in the
background and takes their place when it's ready. Nothing is kept once it's closed."""
from __future__ import annotations

import codecs
import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QFontDatabase, QGuiApplication, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QFileIconProvider, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QScrollArea, QSizePolicy, QSlider, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from . import icons
from .core import ARCHIVE_EXT, human_size, is_cloud_only, short_name
from .i18n import ltr, tr

TEXT_BYTES = 200 * 1024       # the start of a text file that's shown
PDF_PAGES = 8                 # pages of a PDF drawn
ARCHIVE_ROWS = 300            # items of an archive listed
FOLDER_LIMIT, FOLDER_BUDGET_S = 200_000, 3.0   # a folder is counted up to this many items, for this long
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm", ".m4v", ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".flv",
             ".3gp", ".ogv"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma", ".aiff", ".aif", ".mka"}
TEXT_EXT = {".txt", ".md", ".markdown", ".log", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".yaml", ".yml", ".ini",
            ".cfg", ".conf", ".toml", ".properties", ".env", ".py", ".pyw", ".js", ".mjs", ".ts", ".tsx", ".jsx",
            ".html", ".htm", ".css", ".scss", ".less", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".java", ".kt",
            ".go", ".rs", ".rb", ".php", ".sh", ".bat", ".cmd", ".ps1", ".psm1", ".sql", ".lua", ".swift", ".m",
            ".r", ".pl", ".vb", ".vbs", ".reg", ".srt", ".vtt", ".ass", ".ssa", ".tex", ".rst", ".diff", ".patch",
            ".gitignore", ".gitattributes", ".editorconfig", ".svg", ".eml", ".nfo", ".dart", ".vue", ".gradle",
            ".cmake", ".makefile", ".dockerfile", ".csproj", ".sln", ".xaml", ".plist", ".lock"}
TEXT_EXT -= {".ts"}  # (.ts is far more often a video than TypeScript on a PC like this)


def kind_of(path: Path) -> str:
    """picture, video, audio, pdf, text, folder, archive, or "" (looked at more closely in the background)."""
    from .viewer import PICTURE_EXT
    try:
        if path.is_dir():
            return "folder"
    except OSError:
        return ""
    ext = path.suffix.lower()
    if ext in PICTURE_EXT:
        return "picture"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT_EXT:
        return "text"
    if ext in ARCHIVE_EXT or path.name.lower().endswith((".tar.gz", ".tar.xz", ".tar.bz2")):
        return "archive"
    return ""


# ----------------------------------------------------------------------------- reading, in the background

def looks_like_text(sample: bytes) -> bool:
    if not sample:
        return True
    if sample.startswith((codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) or _utf16(sample):
        return True
    if b"\0" in sample:
        return False
    try:
        _utf8(sample)
        return True
    except UnicodeDecodeError:
        pass
    control = sum(1 for b in sample if b < 9 or 13 < b < 32)
    return control < len(sample) * 0.02


def _utf16(sample: bytes) -> str:
    """"utf-16-le" or "utf-16-be" for UTF-16 without a byte-order mark (Windows writes some files like
    that), else ""."""
    s = sample[:4096]
    half = len(s) // 2
    if half < 2:
        return ""
    odd, even = s[1::2].count(0), s[0::2].count(0)
    if odd > half * 0.4 and even < half * 0.05:
        return "utf-16-le"
    if even > half * 0.4 and odd < half * 0.05:
        return "utf-16-be"
    return ""


def _utf8(data: bytes, cut: bool = True) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        if cut and e.start >= len(data) - 3:  # (cut in the middle of a character at the end)
            return data[:e.start].decode("utf-8")
        raise


def decode_text(data: bytes, cut: bool = False) -> str:
    """Text in whatever way it was saved. cut: data is only the start of the file."""
    if data.startswith(codecs.BOM_UTF8):
        return data[3:].decode("utf-8", "replace")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", "replace")
    wide = _utf16(data)
    if wide:
        return data[: len(data) // 2 * 2].decode(wide, "replace")
    try:
        return _utf8(data, cut)
    except UnicodeDecodeError:
        return data.decode("mbcs" if os.name == "nt" else "latin-1", "replace")


def read_text(path: Path) -> tuple[str, bool]:
    """The start of a text file, and whether there's more."""
    with open(path, "rb") as f:
        data = f.read(TEXT_BYTES + 1)
    more = len(data) > TEXT_BYTES
    return decode_text(data[:TEXT_BYTES], more), more


def count_folder(path: Path, stop: threading.Event) -> tuple[int, int, int, bool]:
    """(files, folders, bytes, complete) inside a folder, giving up on a huge one."""
    files = folders = total = 0
    deadline = time.monotonic() + FOLDER_BUDGET_S
    todo = [str(path)]
    while todo:
        if stop.is_set():
            return files, folders, total, False
        try:
            with os.scandir(todo.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            folders += 1
                            todo.append(e.path)
                        else:
                            files += 1
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            continue
        if files + folders > FOLDER_LIMIT or time.monotonic() > deadline:
            return files, folders, total, False
    return files, folders, total, True


def load_picture(path: Path, box: QSize) -> QImage:
    """The picture, made only as big as the window needs (a 50-megapixel photo opens as fast as a small one)."""
    from .viewer import QT_READS
    from .viewer import load_picture as full_picture
    if path.suffix.lower() in QT_READS:
        from PySide6.QtGui import QImageReader
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and (size.width() > box.width() or size.height() > box.height()):
            reader.setScaledSize(size.scaled(box, Qt.KeepAspectRatio))
        img = reader.read()
        if not img.isNull():
            return img
    img = full_picture(path)
    if img.width() > box.width() or img.height() > box.height():
        img = img.scaled(box, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return img


def render_pdf(path: Path, width: int, emit, stop: threading.Event):
    import pypdfium2 as pdfium
    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as e:
        emit("pdf_error", "password" if "password" in str(e).lower() else "broken")
        return
    try:
        n = len(pdf)
        emit("pdf_count", n)
        for i in range(min(n, PDF_PAGES)):
            if stop.is_set():
                return
            page = pdf[i]
            w, _h = page.get_size()
            im = page.render(scale=width / max(w, 1)).to_pil().convert("RGB")
            page.close()
            emit("pdf_page", QImage(im.tobytes("raw", "RGB"), im.width, im.height, 3 * im.width,
                                    QImage.Format_RGB888).copy())
    finally:
        pdf.close()


class _Bridge(QObject):
    done = Signal(int, str, object)   # (which file it was for, what, the result)


# ----------------------------------------------------------------------------- the views

class _Fit(QWidget):
    """A picture, as big as fits (never bigger than it is)."""

    def __init__(self, image: QImage):
        super().__init__()
        self.pixmap = QPixmap.fromImage(image)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def paintEvent(self, _):
        if self.pixmap.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        dpr = self.devicePixelRatioF()
        size = QSize(round(self.pixmap.width() / dpr), round(self.pixmap.height() / dpr))
        if size.width() > self.width() or size.height() > self.height():
            size = size.scaled(self.size(), Qt.KeepAspectRatio)
        x, y = (self.width() - size.width()) / 2, (self.height() - size.height()) / 2
        p.drawPixmap(QRectF(x, y, size.width(), size.height()), self.pixmap, QRectF(self.pixmap.rect()))


def _muted(text: str = "", wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("muted")
    lab.setWordWrap(wrap)
    return lab


def type_name(path: Path) -> str:
    """What Windows calls this kind of file ("PNG File", "Text Document"), in Windows' own language."""
    if os.name == "nt":
        try:
            import ctypes
            import ctypes.wintypes as wt

            class SHFILEINFOW(ctypes.Structure):
                _fields_ = [("hIcon", ctypes.c_void_p), ("iIcon", ctypes.c_int), ("dwAttributes", wt.DWORD),
                            ("szDisplayName", ctypes.c_wchar * 260), ("szTypeName", ctypes.c_wchar * 80)]
            info = SHFILEINFOW()
            if ctypes.windll.shell32.SHGetFileInfoW(str(path), 0, ctypes.byref(info), ctypes.sizeof(info), 0x400):
                return info.szTypeName
        except Exception:
            pass
    return tr("Folder") if path.is_dir() else (path.suffix[1:].upper() or "")


class _Media(QWidget):
    """A video (playing, without sound until Mute is pressed) or a song (waiting for Play)."""

    def __init__(self, path: Path, video: bool, theme, icon: QPixmap):
        super().__init__()
        os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        if video:
            from PySide6.QtMultimediaWidgets import QVideoWidget
            self.video = QVideoWidget()
            self.video.setFocusPolicy(Qt.NoFocus)
            self.player.setVideoOutput(self.video)
            lay.addWidget(self.video, 1)
        else:
            art = QLabel()
            art.setAlignment(Qt.AlignCenter)
            art.setPixmap(icon)
            lay.addStretch()
            lay.addWidget(art)
            title = QLabel(short_name(path.stem, 60))
            title.setObjectName("h2")
            title.setAlignment(Qt.AlignCenter)
            lay.addWidget(title)
            lay.addStretch()
        row = QHBoxLayout()
        self.theme = theme
        self.play_btn = QPushButton()
        self.play_btn.setToolTip(tr("Play / pause"))
        self.mute_btn = QPushButton()
        self.mute_btn.setToolTip(tr("Mute"))
        self.mute_btn.setCheckable(True)
        for b in (self.play_btn, self.mute_btn):
            b.setObjectName("flat")
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.setIconSize(QSize(20, 20))
        self.play_btn.clicked.connect(self.toggle)
        self.mute_btn.toggled.connect(self._mute)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.clock = _muted(wrap=False)
        self.player.durationChanged.connect(lambda d: self.slider.setRange(0, int(d)))
        self.player.positionChanged.connect(self._moved)
        self.player.playbackStateChanged.connect(lambda _s: self._icons())
        row.addWidget(self.play_btn)
        row.addWidget(self.slider, 1)
        row.addWidget(self.clock)
        row.addWidget(self.mute_btn)
        lay.addLayout(row)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.mute_btn.setChecked(video)
        self.audio.setMuted(video)
        if video:
            self.player.setLoops(QMediaPlayer.Loops.Infinite)
            self.player.play()
        self._icons()

    def _moved(self, ms: int):
        if not self.slider.isSliderDown():
            self.slider.setValue(int(ms))
        self.clock.setText(ltr(_clock(ms) + " / " + _clock(self.player.duration())))

    def _mute(self, on: bool):
        self.audio.setMuted(on)
        self._icons()

    def _icons(self):
        from PySide6.QtMultimedia import QMediaPlayer
        playing = self.player.playbackState() == QMediaPlayer.PlayingState
        self.play_btn.setIcon(icons.icon("pause" if playing else "play", self.theme.text))
        self.mute_btn.setIcon(icons.icon("speaker_mute" if self.mute_btn.isChecked() else "speaker_2",
                                         self.theme.text))

    def toggle(self):
        from PySide6.QtMultimedia import QMediaPlayer
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop(self):
        self.player.stop()
        self.player.setSource(QUrl())


def _clock(ms: int) -> str:
    s = max(0, int(ms) // 1000)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# ----------------------------------------------------------------------------- the window

class PeekWindow(QWidget):
    """Opens over File Explorer with the selected file; ← and → for the others; Esc, Space or the shortcut
    closes it, Enter opens the file."""

    def __init__(self, tool):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.tool = tool
        self.c = tool.c
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(tr("Quick look"))
        self.setFocusPolicy(Qt.StrongFocus)
        self.files: list[Path] = []
        self.index = 0
        self.hwnd = 0          # the File Explorer window it was opened from
        self.token = 0         # which file the background work is for (older results are dropped)
        self.stop = threading.Event()
        self.content: QWidget | None = None
        self.kind = ""
        self.pdf_total = 0
        self.bridge = _Bridge()
        self.bridge.done.connect(self._done, Qt.QueuedConnection)
        self.shown_at = 0.0

        self.frame = QFrame(self)
        self.frame.setObjectName("peekFrame")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.frame)
        lay = QVBoxLayout(self.frame)
        lay.setContentsMargins(16, 12, 16, 16)
        lay.setSpacing(12)
        self.bar = QWidget()
        self.bar.installEventFilter(self)
        top = QHBoxLayout(self.bar)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)
        self.file_icon = QLabel()
        top.addWidget(self.file_icon)
        words = QVBoxLayout()
        words.setSpacing(0)
        self.name = QLabel()
        self.name.setObjectName("cardTitle")
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.facts = _muted(wrap=False)
        self.facts.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        words.addWidget(self.name)
        words.addWidget(self.facts)
        top.addLayout(words, 1)
        self.counter = _muted(wrap=False)
        top.addWidget(self.counter)
        self.open_btn = self._button(tr("Open"), self.open_file, "primary")
        self.corefold_btn = self._button(tr("Open with Corefold"), self.open_in_corefold)
        self.folder_btn = self._button(tr("Show in folder"), self.show_in_folder)
        self.close_btn = self._button("", self.close_peek, "flat")
        self.close_btn.setToolTip(tr("Close"))
        for b in (self.open_btn, self.corefold_btn, self.folder_btn, self.close_btn):
            top.addWidget(b)
        lay.addWidget(self.bar)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self.body, 1)
        self.note = _muted()
        self.note.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.note)
        self.apply_theme(self.c.theme)

    def _button(self, text: str, fn, name: str = "") -> QPushButton:
        b = QPushButton(text)
        if name:
            b.setObjectName(name)
        b.setFocusPolicy(Qt.NoFocus)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(fn)
        return b

    def apply_theme(self, t):
        p = t.tokens
        self.frame.setStyleSheet(f"QFrame#peekFrame {{ background: {p.raised.name()}; "
                                 f"border: 1px solid {p.line_strong.name()}; border-radius: 14px; }}")
        self.close_btn.setIcon(icons.icon("dismiss", t.text))

    # ---- opening

    def show_files(self, files: list[Path], hwnd: int):
        self.files = files
        self.index = 0
        self.hwnd = hwnd
        self._place(hwnd)
        self.show_current()
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        from .quickmenu import _bring_to_front
        _bring_to_front(int(self.winId()))
        self.shown_at = time.perf_counter()

    def set_folder(self, hwnd: int, paths: list[Path]):
        """Everything in the folder (one file was selected): ← and → go through it."""
        if hwnd != self.hwnd or len(self.files) != 1 or not self.isVisible():
            return
        current = self.files[0]
        key = os.path.normcase(str(current))
        at = next((i for i, p in enumerate(paths) if os.path.normcase(str(p)) == key), -1)
        if at >= 0:
            self.files, self.index = paths, at
            self._counter()

    def _place(self, hwnd: int):
        screen = _screen_of(hwnd) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        w = min(1200, int(area.width() * 0.72))
        h = min(840, int(area.height() * 0.8))
        self.setGeometry(area.x() + (area.width() - w) // 2, area.y() + (area.height() - h) // 2, w, h)

    def current(self) -> Path | None:
        return self.files[self.index] if 0 <= self.index < len(self.files) else None

    def go(self, step: int):
        if len(self.files) > 1:
            self.index = (self.index + step) % len(self.files)
            self.show_current()

    def _counter(self):
        n = len(self.files)
        self.counter.setText(tr("{number} of {total}", number=self.index + 1, total=n) if n > 1 else "")

    def show_current(self):
        path = self.current()
        if path is None:
            return
        self.stop.set()
        self.stop = threading.Event()
        self.token += 1
        self.pdf_total = 0
        self._counter()
        self.name.setText(path.name or str(path))
        self.name.setToolTip(str(path))
        prov = QFileIconProvider()
        from PySide6.QtCore import QFileInfo
        ic = prov.icon(QFileInfo(str(path)))
        self.file_icon.setPixmap(ic.pixmap(32, 32))
        self.big_icon = ic.pixmap(128, 128)
        try:
            st = path.stat()
        except OSError:
            st = None
        self.stat = st
        is_dir = path.is_dir()
        self.facts.setText(self._facts(st, is_dir))
        self.kind = kind_of(path) if st is not None else "missing"
        self.corefold_btn.setVisible(self._corefold_opens(path))
        self.note.setText("")
        self._set(self._info(path, st))
        if st is None:
            self.note.setText(tr("This file isn't there any more."))
            return
        if not is_dir and is_cloud_only(path):
            self.note.setText(tr("This file is in the cloud and isn't on this PC yet, so it isn't previewed."))
            return
        if self.kind in ("video", "audio"):
            QTimer.singleShot(0, lambda t=self.token, p=path: self._media(t, p))
            return
        self._work(path)

    def _facts(self, st, is_dir: bool) -> str:
        if st is None:
            return ""
        from .timefmt import locale
        from PySide6.QtCore import QDateTime, QLocale
        when = locale().toString(QDateTime.fromSecsSinceEpoch(int(st.st_mtime)), QLocale.FormatType.ShortFormat)
        return when if is_dir else f"{human_size(st.st_size)}  ·  {when}"

    def _corefold_opens(self, path: Path) -> bool:
        from . import tools as all_tools
        s = self.c.settings
        if self.kind == "picture" or path.suffix.lower() in (".eml", ".msg"):
            return True
        if self.kind in ("video", "audio"):
            return all_tools.is_on(s, "player")
        if self.kind == "archive":
            return all_tools.is_on(s, "archive")
        return False

    def _set(self, w: QWidget):
        old = self.content
        if old is not None:
            if isinstance(old, _Media):
                old.stop()
            old.hide()
            old.deleteLater()
        self.content = w
        self.body.addWidget(w, 1)

    def _info(self, path: Path, st) -> QWidget:
        """The icon and details: shown at once, and all there is for files without a preview."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch()
        art = QLabel()
        art.setAlignment(Qt.AlignCenter)
        art.setPixmap(self.big_icon)
        lay.addWidget(art)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        rows = [(tr("Type"), type_name(path))]
        if st is not None:
            from .timefmt import locale
            from PySide6.QtCore import QDateTime, QLocale

            def when(ts):
                dt, loc = QDateTime.fromSecsSinceEpoch(int(ts)), locale()
                return f"{loc.toString(dt.date(), QLocale.FormatType.LongFormat)}  " \
                       f"{loc.toString(dt.time(), QLocale.FormatType.ShortFormat)}"
            if not path.is_dir():
                rows.append((tr("Size"), human_size(st.st_size)))
            rows.append((tr("Modified"), when(st.st_mtime)))
            created = getattr(st, "st_birthtime", None) or (st.st_ctime if os.name == "nt" else None)
            if created:
                rows.append((tr("Created"), when(created)))
        self.folder_label = None
        if self.kind == "folder":
            self.folder_label = QLabel(tr("Counting…"))
            rows.append((tr("Contains"), self.folder_label))
        for r, (name, value) in enumerate(rows):
            key = _muted(name, wrap=False)
            key.setAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
            grid.addWidget(key, r, 0)
            grid.addWidget(value if isinstance(value, QLabel) else QLabel(value), r, 1)
        holder = QHBoxLayout()
        holder.addStretch()
        holder.addLayout(grid)
        holder.addStretch()
        lay.addLayout(holder)
        lay.addStretch()
        return w

    # ---- the background part

    def _work(self, path: Path):
        token, stop, emit_to = self.token, self.stop, self.bridge
        kind = self.kind
        dpr = self.devicePixelRatioF()
        box = QSize(int(max(self.width() - 32, 200) * dpr), int(max(self.height() - 110, 200) * dpr))

        def emit(what, result):
            if not stop.is_set():
                try:
                    emit_to.done.emit(token, what, result)
                except RuntimeError:  # (the window is gone)
                    stop.set()

        def run():
            k = kind
            try:
                if not k:
                    with open(path, "rb") as f:
                        k = "text" if looks_like_text(f.read(4096)) else "other"
                if k == "picture":
                    emit("picture", load_picture(path, box))
                elif k == "text":
                    emit("text", read_text(path))
                elif k == "pdf":
                    width = min(box.width() - int(40 * dpr), int(900 * dpr))  # (room for the scroll bar)
                    render_pdf(path, width, emit, stop)
                elif k == "folder":
                    emit("folder", count_folder(path, stop))
                elif k == "archive":
                    from .archives import NeedPassword, list_entries
                    try:
                        entries = list_entries(path)
                        emit("archive", ([(e.path, e.size, e.is_dir) for e in entries[:ARCHIVE_ROWS]], len(entries),
                                         any(e.encrypted for e in entries)))
                    except NeedPassword:
                        emit("error", "archive_password")
            except Exception:
                import logging
                logging.getLogger("corefold").debug("quick look couldn't read %s", path, exc_info=True)
                emit("error", "broken")
        threading.Thread(target=run, name="corefold-peek-read", daemon=True).start()

    def _done(self, token: int, what: str, result):
        if token != self.token or not self.isVisible():
            return
        if what == "picture":
            if isinstance(result, QImage) and not result.isNull():
                self._set(_Fit(result))
                self._movie()
            else:
                self.note.setText(tr("Couldn't show this file."))
        elif what == "text":
            text, more = result
            self._set(self._text(text))
            if more:
                self.note.setText(tr("Showing the first {size}.", size=human_size(TEXT_BYTES)))
        elif what == "pdf_count":
            self.pdf_total = int(result)
            self._set(self._pages())
            if self.pdf_total > PDF_PAGES:
                self.note.setText(tr("The first {count} of {total} pages", count=PDF_PAGES, total=self.pdf_total))
        elif what == "pdf_page" and getattr(self, "pages_box", None) is not None:
            page = QLabel()
            page.setAlignment(Qt.AlignHCenter)
            pm = QPixmap.fromImage(result)
            pm.setDevicePixelRatio(self.devicePixelRatioF())
            page.setPixmap(pm)
            self.pages_box.addWidget(page)
        elif what == "pdf_error":
            self.note.setText(tr("This PDF is locked with a password.") if result == "password"
                              else tr("Couldn't show this file."))
        elif what == "folder" and self.folder_label is not None:
            files, folders, size, complete = result
            from .timefmt import locale
            text = tr("{files} files and {folders} folders, {size}", files=locale().toString(files),
                      folders=locale().toString(folders), size=human_size(size))
            self.folder_label.setText(text if complete else tr("At least {text}", text=text))
        elif what == "archive":
            rows, total, locked = result
            self._set(self._archive(rows))
            if total > len(rows):
                self.note.setText(tr("The first {count} of {total} items", count=len(rows), total=total))
            elif locked:
                self.note.setText(tr("The files inside are locked with a password."))
        elif what == "error":
            self.note.setText(tr("This archive is locked with a password.") if result == "archive_password"
                              else tr("Couldn't show this file."))

    def _movie(self):
        """Animated GIFs and WebPs move."""
        path = self.current()
        if path is None or path.suffix.lower() not in (".gif", ".webp"):
            return
        from PySide6.QtGui import QMovie
        pic = self.content
        movie = QMovie(str(path), parent=pic)
        if not movie.isValid() or movie.frameCount() == 1:
            movie.deleteLater()
            return

        def frame(_n):
            pic.pixmap = QPixmap.fromImage(movie.currentImage())
            pic.update()
        movie.frameChanged.connect(frame)
        movie.start()

    def _text(self, text: str) -> QWidget:
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setFocusPolicy(Qt.NoFocus)
        view.setLineWrapMode(QPlainTextEdit.NoWrap)
        view.setLayoutDirection(Qt.LeftToRight)
        have = set(QFontDatabase.families())
        mono = next((f for f in ("Cascadia Mono", "Consolas") if f in have),
                    QFontDatabase.systemFont(QFontDatabase.FixedFont).family())
        view.setObjectName("peekText")  # (the window's stylesheet sets one font for everything: not for code)
        view.setStyleSheet(f'#peekText, #peekText QWidget {{ font-family: "{mono}"; font-size: 10pt; }}')
        view.setPlainText(text)
        return view

    def _pages(self) -> QWidget:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFocusPolicy(Qt.NoFocus)
        area.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        self.pages_box = QVBoxLayout(inner)
        self.pages_box.setSpacing(12)
        self.pages_box.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        area.setWidget(inner)
        inner.destroyed.connect(lambda *_: setattr(self, "pages_box", None))
        return area

    def _archive(self, rows) -> QWidget:
        tree = QTreeWidget()
        tree.setFocusPolicy(Qt.NoFocus)
        tree.setRootIsDecorated(False)
        tree.setHeaderLabels([tr("Name"), tr("Size")])
        tree.headerItem().setTextAlignment(1, Qt.AlignTrailing | Qt.AlignVCenter)
        tree.setUniformRowHeights(True)
        folder_icon = icons.icon("folder", self.c.theme.muted)
        file_icon = icons.icon("document", self.c.theme.muted)
        for name, size, is_dir in rows:
            item = QTreeWidgetItem([name, "" if is_dir else human_size(size)])
            item.setIcon(0, folder_icon if is_dir else file_icon)
            item.setTextAlignment(1, Qt.AlignTrailing | Qt.AlignVCenter)
            tree.addTopLevelItem(item)
        tree.header().setStretchLastSection(False)
        tree.header().setSectionResizeMode(0, tree.header().ResizeMode.Stretch)
        tree.header().setSectionResizeMode(1, tree.header().ResizeMode.ResizeToContents)
        return tree

    def _media(self, token: int, path: Path):
        if token != self.token or not self.isVisible():
            return
        try:
            self._set(_Media(path, self.kind == "video", self.c.theme, self.big_icon))
        except Exception:  # (Qt's player missing from a broken install)
            import logging
            logging.getLogger("corefold").debug("quick look can't play %s", path, exc_info=True)

    # ---- buttons and keys

    def open_file(self):
        path = self.current()
        if path is None:
            return
        self.close_peek(back=False)
        from .core import os_open
        try:
            os_open(path)
        except OSError:
            self.c.tell(tr("Couldn't open it"), short_name(path.name))

    def open_in_corefold(self):
        path = self.current()
        if path is None:
            return
        kind = self.kind
        self.close_peek(back=False)
        if kind in ("video", "audio"):
            self.c.open_player(path)
        elif kind == "archive":
            self.c.open_browser(path)
        else:
            self.c.open_viewer(path)

    def show_in_folder(self):
        path = self.current()
        if path is None:
            return
        self.close_peek(back=False)
        from .winutil import reveal_in_explorer
        reveal_in_explorer(path)

    def close_peek(self, back: bool = True):
        hwnd = self.hwnd
        self.hide()
        if back and hwnd:
            from .quickmenu import _bring_to_front
            _bring_to_front(hwnd)  # (back to File Explorer, where you were)

    def keyPressEvent(self, e):
        from . import hotkey
        key = e.key()
        if e.isAutoRepeat() and key == Qt.Key_Space:
            return  # (the shortcut's own key, still held down from opening it)
        combo = hotkey.from_key_event(key, e.modifiers(), e.nativeVirtualKey())
        if key in (Qt.Key_Escape, Qt.Key_Space) and not e.modifiers() or (
                combo and hotkey.normalize(combo) == hotkey.normalize(self.c.settings.peek_key)):
            self.close_peek()
        elif key in (Qt.Key_Left, Qt.Key_Right):
            self.go(1 if key == Qt.Key_Right else -1)  # (the same way as in the Viewer)
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self.open_file()
        elif key in (Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End):
            self._scroll(key)
        else:
            super().keyPressEvent(e)

    def _scroll(self, key):
        bar = None
        w = self.content
        if w is not None and hasattr(w, "verticalScrollBar"):
            bar = w.verticalScrollBar()
        if bar is None:
            return
        steps = {Qt.Key_Up: -bar.singleStep() * 3, Qt.Key_Down: bar.singleStep() * 3, Qt.Key_PageUp: -bar.pageStep(),
                 Qt.Key_PageDown: bar.pageStep(), Qt.Key_Home: -bar.maximum(), Qt.Key_End: bar.maximum()}
        bar.setValue(bar.value() + steps[key])

    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        if obj is self.bar and e.type() == QEvent.MouseButtonPress and e.button() == Qt.LeftButton:
            handle = self.windowHandle()
            if handle is not None:
                handle.startSystemMove()  # (dragged by its top bar, like a window's title)
            return True
        return super().eventFilter(obj, e)

    def changeEvent(self, e):
        from PySide6.QtCore import QEvent
        if e.type() == QEvent.ActivationChange and not self.isActiveWindow() and self.isVisible():
            QTimer.singleShot(0, self._hide_if_away)
        super().changeEvent(e)

    def _hide_if_away(self):
        """It stays on top while you pick other files in File Explorer, and goes away when you switch to
        another app (so it never sits over it)."""
        from PySide6.QtWidgets import QApplication
        from .peekshell import foreground, is_files_window
        if self.isVisible() and not self.isActiveWindow() and QApplication.activeWindow() is None \
                and foreground() and not is_files_window(foreground()):
            self.hide()

    def hideEvent(self, e):
        self.stop.set()
        self.token += 1
        if self.content is not None:
            if isinstance(self.content, _Media):
                self.content.stop()
            self.content.deleteLater()
            self.content = None
        self.files = []
        self.c.popup_closed()
        super().hideEvent(e)


def _screen_of(hwnd: int):
    """The screen File Explorer is on (Windows names screens the way Qt does: \\\\.\\DISPLAY1)."""
    if not hwnd or os.name != "nt":
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD),
                        ("szDevice", ctypes.c_wchar * 32)]
        u = ctypes.windll.user32
        u.MonitorFromWindow.restype = ctypes.c_void_p
        u.MonitorFromWindow.argtypes = [ctypes.c_void_p, wt.DWORD]
        mon = u.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        if mon and u.GetMonitorInfoW(mon, ctypes.byref(info)):
            for s in QGuiApplication.screens():
                if s.name() == info.szDevice:
                    return s
    except Exception:
        pass
    return None
