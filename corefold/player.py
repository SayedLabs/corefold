"""Corefold Player: videos and music, with clean controls that get out of the way.

Playing is done by Qt's own player, which uses FFmpeg (the same decoders most players use) and the
graphics card for HD and 4K. It's only loaded when a Player window opens."""
from __future__ import annotations

import bisect
import html
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")  # (before Qt's player is loaded)


def local_only(on: bool = True):
    """A file on the PC is played without going online: a file can say it's made of parts on the internet
    (a playlist), and fetching them would tell that site you opened it. Qt's FFmpeg already refuses
    that today; this keeps it so whatever FFmpeg does next. Qt's player reads it each time it opens
    something; a link pasted on purpose may go online."""
    if on:
        os.environ["QT_FFMPEG_PROTOCOL_WHITELIST"] = "file"
    else:
        os.environ.pop("QT_FFMPEG_PROTOCOL_WHITELIST", None)


from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl, Signal  # noqa: E402
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QKeySequence, QPixmap, QShortcut  # noqa: E402
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QSlider, \
    QStackedWidget, QVBoxLayout, QWidget  # noqa: E402

from . import icons  # noqa: E402
from .core import APP_NAME  # noqa: E402
from .i18n import ltr, tr  # noqa: E402
from .settings import LOG_DIR  # noqa: E402
from .theme import round_menu  # noqa: E402
from .winutil import set_title_bar_dark  # noqa: E402

if TYPE_CHECKING:
    from .app import Controller

log = logging.getLogger("corefold")

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm", ".m4v", ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".flv",
             ".3gp", ".ogv", ".vob"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma", ".aiff", ".aif", ".alac", ".mka"}
MEDIA_EXT = VIDEO_EXT | AUDIO_EXT
SUB_EXT = {".srt", ".vtt", ".ass", ".ssa"}
SPEEDS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 3.0]
POSITIONS = LOG_DIR / "player.json"
KEEP_POSITIONS = 60
SUB_STEP_MS = 100


def is_media(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_EXT


def media_in(folder: Path) -> list[Path]:
    from .viewer import natural_key
    try:
        files = [p for p in folder.iterdir() if p.is_file() and is_media(p)]
    except OSError:
        return []
    return sorted(files, key=lambda p: natural_key(p.name))


# how the video fills the window (English, shown through tr())
FIT_MODES = (("fit", "Fit the window"), ("fill", "Fill the window (crops the edges)"), ("stretch", "Stretch"))


def clock(ms: int) -> str:
    s = max(0, int(ms // 1000))
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ----------------------------------------------------------------------------- picking up where you left off

def load_positions() -> dict:
    try:
        data = json.loads(POSITIONS.read_text("utf-8"))
        return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _when(kv) -> float:
    try:
        return float(kv[1].get("when", 0))
    except (TypeError, ValueError):
        return 0.0


def save_position(path: Path, pos: int, dur: int):
    """Remembers how far a file got (forgotten once it's almost finished, or barely started)."""
    data = load_positions()
    key = str(path)
    if dur > 0 and (pos < 15_000 or pos > dur - 20_000 or pos > dur * 0.97):
        data.pop(key, None)
    elif dur > 0:
        data[key] = {"pos": int(pos), "dur": int(dur), "when": time.time()}
    newest = sorted(data.items(), key=_when, reverse=True)[:KEEP_POSITIONS]
    try:
        POSITIONS.parent.mkdir(parents=True, exist_ok=True)
        tmp = POSITIONS.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(newest)), "utf-8")
        os.replace(tmp, POSITIONS)
    except OSError:
        pass


def forget_positions():
    try:
        POSITIONS.unlink()
    except OSError:
        pass


def continue_watching(limit: int = 8) -> list[tuple[Path, int, int, float]]:
    """(file, position, length, when) of files left part way, newest first (moved ones left out)."""
    out = []
    for key, v in sorted(load_positions().items(), key=_when, reverse=True):
        p = Path(key)
        try:
            if p.is_file():
                out.append((p, int(v.get("pos", 0)), int(v.get("dur", 0)), float(v.get("when", 0))))
        except (OSError, TypeError, ValueError):
            continue
        if len(out) >= limit:
            break
    return out


# ----------------------------------------------------------------------------- subtitle files

_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_TAGS = re.compile(r"<[^>]+>|\{[^}]*\}")


def _ms(text: str) -> int | None:
    m = _TIME.search(text)
    if not m:
        return None
    h, mi, s, frac = m.groups()
    frac = (frac + "00")[:3]
    return ((int(h or 0) * 60 + int(mi)) * 60 + int(s)) * 1000 + int(frac)


def parse_subtitles(text: str, ext: str) -> list[tuple[int, int, str]]:
    """SRT, WebVTT and (the text of) ASS/SSA subtitles as (start, end, text) in milliseconds."""
    cues = []
    if ext in (".ass", ".ssa"):
        fmt = None
        for line in text.splitlines():
            if line.lower().startswith("format:") and fmt is None and "start" in line.lower():
                fmt = [f.strip().lower() for f in line.split(":", 1)[1].split(",")]
            elif line.lower().startswith("dialogue:") and fmt:
                parts = line.split(":", 1)[1].split(",", len(fmt) - 1)
                if len(parts) < len(fmt):
                    continue
                d = dict(zip(fmt, parts))
                start, end = _ms("0:" + d.get("start", "").strip() if d.get("start", "").count(":") == 1
                                 else d.get("start", "")), _ms(d.get("end", ""))
                body = _TAGS.sub("", d.get("text", "")).replace("\\N", "\n").replace("\\n", "\n").strip()
                if start is not None and end is not None and body:
                    cues.append((start, end, body))
    else:
        for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")):
            lines = [ln for ln in block.strip().split("\n") if ln.strip()]
            idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
            if idx is None:
                continue
            a, b = lines[idx].split("-->", 1)
            start, end = _ms(a), _ms(b)
            body = "\n".join(_TAGS.sub("", ln) for ln in lines[idx + 1:]).strip()
            if start is not None and end is not None and body:
                cues.append((start, end, html.unescape(body)))
    cues.sort()
    return cues


def read_subtitles(path: Path) -> list[tuple[int, int, str]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1256", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            if enc == "utf-16" and raw[:2] not in (b"\xff\xfe", b"\xfe\xff"):
                continue
            return parse_subtitles(text, path.suffix.lower())
        except (UnicodeDecodeError, ValueError):
            continue
    return []


class Subtitles:
    def __init__(self, cues: list[tuple[int, int, str]], name: str = ""):
        self.cues = cues
        self.starts = [c[0] for c in cues]
        self.name = name
        self.shift = 0  # ms: positive shows them later

    def text_at(self, ms: int) -> str:
        t = ms - self.shift
        i = bisect.bisect_right(self.starts, t) - 1
        shown = []
        while i >= 0 and len(shown) < 3:  # (overlapping lines are shown together)
            start, end, text = self.cues[i]
            if start <= t < end:
                shown.append(text)
            elif t - start > 60_000:
                break
            i -= 1
        return "\n".join(reversed(shown))


def subtitle_for(video: Path) -> Path | None:
    """A subtitle file next to the video with the same name ("Film.srt", "Film.en.srt")."""
    try:
        stem = video.stem.lower()  # (Windows names ignore case: "film.srt" goes with "Film.mp4")
        for p in sorted(video.parent.iterdir()):
            if p.suffix.lower() in SUB_EXT and (p.stem.lower() == stem or p.stem.lower().startswith(stem + ".")):
                return p
    except OSError:
        pass
    return None


def chapters_of(path: Path) -> list[tuple[int, str]]:
    """The chapters inside a film or a lesson: (start in ms, title), read with FFprobe. [] if none."""
    import subprocess
    try:
        from .converters import LOCAL_ONLY, NO_WINDOW, find_tool
        out = subprocess.run([find_tool("ffprobe"), "-v", "error", "-show_chapters", "-of", "json", *LOCAL_ONLY,
                              str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=15, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW).stdout
        found = []
        for i, ch in enumerate(json.loads(out or "{}").get("chapters", [])):
            title = ((ch.get("tags") or {}).get("title") or "").strip()
            found.append((int(float(ch.get("start_time") or 0) * 1000), title or tr("Chapter {number}", number=i + 1)))
        return found if len(found) > 1 else []
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError):
        return []


# ----------------------------------------------------------------------------- the window

class SeekBar(QSlider):
    """Click anywhere on it to jump there (not just a step). Chapters show as small marks."""
    chapters: list = []

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self.chapters or self.maximum() <= 0:
            return
        from PySide6.QtGui import QColor, QPainter
        p = QPainter(self)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 170))
        w, h = self.width(), self.height()
        for ms, _title in self.chapters[1:]:
            frac = ms / self.maximum()
            if self.layoutDirection() == Qt.RightToLeft:
                frac = 1 - frac
            p.drawRect(int(frac * w) - 1, h // 2 - 5, 2, 10)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.maximum() > 0:
            x = e.position().x()
            frac = x / max(1, self.width())
            if self.layoutDirection() == Qt.RightToLeft:
                frac = 1 - frac
            self.setValue(int(self.minimum() + frac * (self.maximum() - self.minimum())))
            self.sliderMoved.emit(self.value())
        super().mousePressEvent(e)


class PlayerWindow(QWidget):
    chapters_done = Signal(str, object)  # (the file, its chapters), from the thread that reads them

    def __init__(self, controller: "Controller", path: Path):
        super().__init__()
        self.link = ""          # a video playing from the internet (no file)
        self.chapters_done.connect(self._chapters_loaded)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        self.c = controller
        self.files: list[Path] = []
        self.index = 0
        self.subs: Subtitles | None = None
        self.loop_a: int | None = None
        self.loop_b: int | None = None
        self.resume_to = 0
        self.mini = False
        self._normal_geometry = None
        self._last_pos_save = 0.0
        self.setObjectName("playerWindow")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowIcon(controller.icon)
        self.setAcceptDrops(True)
        self.resize(1000, 620)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        s = controller.settings
        self.audio.setVolume(max(0, min(100, s.player_volume)) / 100)
        self.video = QVideoWidget()
        self.video.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.player.setVideoOutput(self.video)
        self.video.installEventFilter(self)
        self.cover = QLabel()
        self.cover.setObjectName("playerCover")
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.installEventFilter(self)
        self.screen_stack = QStackedWidget()
        self.screen_stack.addWidget(self.video)
        self.screen_stack.addWidget(self.cover)
        self.note = QLabel()
        self.note.setObjectName("playerNote")
        self.note.setWordWrap(True)
        self.note_btn = QPushButton()
        self.note_btn.setObjectName("flat")
        self.note_btn.setCursor(Qt.PointingHandCursor)
        self.note_btn.clicked.connect(self._note_action)
        self._note_fn = None
        note_row = QHBoxLayout()
        note_row.setContentsMargins(14, 6, 14, 0)
        note_row.addWidget(self.note, 1)
        note_row.addWidget(self.note_btn)
        self.note_box = QWidget()
        self.note_box.setObjectName("playerNoteBox")
        self.note_box.setAttribute(Qt.WA_StyledBackground)
        self.note_box.setLayout(note_row)
        self.note_timer = QTimer(self)
        self.note_timer.setSingleShot(True)
        self.note_timer.timeout.connect(self.note_box.hide)

        # the controls
        self.seek = SeekBar(Qt.Horizontal)
        self.seek.setObjectName("seek")
        self.seek.setRange(0, 0)
        self.seek.setCursor(Qt.PointingHandCursor)
        self.seek.sliderMoved.connect(self._seek_to)
        self.t_now = QLabel("0:00")
        self.t_all = QLabel("0:00")
        for lab in (self.t_now, self.t_all):
            lab.setObjectName("muted")
        row1 = QHBoxLayout()
        row1.setSpacing(10)
        row1.addWidget(self.t_now)
        row1.addWidget(self.seek, 1)
        row1.addWidget(self.t_all)
        self.icon_buttons: list[tuple[QPushButton, str]] = []

        def tool(name, tip, fn, checkable=False):
            b = QPushButton()
            b.setObjectName("small")
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setIconSize(QSize(20, 20))
            b.setCheckable(checkable)
            b.clicked.connect(fn)
            self.icon_buttons.append((b, name))
            return b
        self.b_prev = tool("previous", tr("Previous file (P)"), lambda: self.go(-1))
        self.b_back = tool("rewind", tr("Back (Left arrow)"), lambda: self.skip(-1))
        self.b_play = tool("play", tr("Play / pause (Space)"), self.toggle)
        self.b_fwd = tool("fast_forward", tr("Forward (Right arrow)"), lambda: self.skip(1))
        self.b_next = tool("next", tr("Next file (N)"), lambda: self.go(1))
        self.b_mute = tool("speaker_2", tr("Mute (M)"), self.toggle_mute)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(round(self.audio.volume() * 100))
        self.volume.setFixedWidth(90)
        self.volume.setCursor(Qt.PointingHandCursor)
        self.volume.valueChanged.connect(self._volume)
        self.title = QLabel()
        self.title.setObjectName("h2")
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.b_speed = QPushButton("1×")
        self.b_speed.setObjectName("small")
        self.b_speed.setToolTip(tr("Speed ([ and ])"))
        self.b_speed.setCursor(Qt.PointingHandCursor)
        self.speed_menu = QMenu(self.b_speed)
        round_menu(self.speed_menu)
        for sp in SPEEDS:
            self.speed_menu.addAction(f"{sp:g}×", lambda sp=sp: self.set_speed(sp))
        self.b_speed.setMenu(self.speed_menu)
        self.b_subs = tool("closed_caption", tr("Subtitles"), self._subs_menu)
        self.b_audio = tool("music_note_2", tr("Sound track"), self._audio_menu)
        self.b_loop = tool("arrow_repeat_all", tr("A–B loop: press once for the start, again for the end (L)"),
                           self.mark_loop, checkable=True)
        self.b_shot = tool("camera", tr("Save this frame as a picture (S)"), self.screenshot)
        self.b_mini = tool("picture_in_picture", tr("Mini player, always on top (I)"), self.toggle_mini)
        self.b_full = tool("full_screen_maximize", tr("Full screen (F)"), self.toggle_full)
        self.b_more = QPushButton(tr("More"))
        self.b_more.setObjectName("small")
        self.b_more.setCursor(Qt.PointingHandCursor)
        more = QMenu(self.b_more)
        round_menu(more)
        more.addAction(tr("Open a file…"), self.open_dialog)
        more.addAction(tr("Playlist (Q)"), self.toggle_playlist)
        self.a_repeat = more.addAction(tr("Repeat this file"), self._repeat)
        self.a_repeat.setCheckable(True)
        self.a_shuffle = more.addAction(tr("Shuffle"), self._shuffle)
        self.a_shuffle.setCheckable(True)
        picture = more.addMenu(tr("Picture (Z)"))
        round_menu(picture)
        self.fit_actions = {}
        for key, text in FIT_MODES:
            a = picture.addAction(tr(text), lambda k=key: self.set_fit(k))
            a.setCheckable(True)
            a.setChecked(key == "fit")
            self.fit_actions[key] = a
        more.addSeparator()
        more.addAction(tr("Save the A–B part as a clip…"), self.save_clip)
        more.addAction(tr("Show in folder"), self._reveal)
        more.addAction(tr("Open with…"), self._open_with)
        self.b_more.setMenu(more)
        row2 = QHBoxLayout()
        row2.setSpacing(4)
        for b in (self.b_prev, self.b_back, self.b_play, self.b_fwd, self.b_next):
            row2.addWidget(b)
        row2.addSpacing(6)
        row2.addWidget(self.b_mute)
        row2.addWidget(self.volume)
        row2.addSpacing(10)
        row2.addWidget(self.title, 1)
        for b in (self.b_speed, self.b_subs, self.b_audio, self.b_loop, self.b_shot, self.b_mini, self.b_full,
                  self.b_more):
            row2.addWidget(b)
        self.bar = QFrame()
        self.bar.setObjectName("playerBar")
        bl = QVBoxLayout(self.bar)
        bl.setContentsMargins(14, 8, 14, 8)
        bl.setSpacing(4)
        bl.addLayout(row1)
        bl.addLayout(row2)

        from PySide6.QtWidgets import QListWidget
        self.playlist = QListWidget()
        self.playlist.setObjectName("playlist")
        self.playlist.setFixedWidth(260)
        self.playlist.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.playlist.itemActivated.connect(lambda it: self._play_row(self.playlist.row(it)))
        self.playlist.itemClicked.connect(lambda it: self._play_row(self.playlist.row(it)))
        self.playlist.hide()
        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(0)
        middle.addWidget(self.screen_stack, 1)
        middle.addWidget(self.playlist)
        self.fit = "fit"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(middle, 1)
        lay.addWidget(self.note_box)
        lay.addWidget(self.bar)
        self.note_box.hide()

        self.player.positionChanged.connect(self._position)
        self.player.durationChanged.connect(self._duration)
        self.player.playbackStateChanged.connect(self._state)
        self.player.mediaStatusChanged.connect(self._status)
        self.player.errorOccurred.connect(self._error)
        self.player.tracksChanged.connect(self._tracks)
        self.player.metaDataChanged.connect(self._meta)

        # full screen: the controls and the pointer hide while you watch
        self.idle = QTimer(self)
        self.idle.setInterval(400)
        self.idle.timeout.connect(self._idle_check)
        self._last_mouse = QCursor.pos()
        self._still_since = time.monotonic()
        self.click_timer = QTimer(self)
        self.click_timer.setSingleShot(True)
        self.click_timer.setInterval(QGuiApplication.styleHints().mouseDoubleClickInterval())
        self.click_timer.timeout.connect(self.toggle)

        keys = [("Space", self.toggle), ("K", self.toggle), ("Left", lambda: self.skip(-1)),
                ("Right", lambda: self.skip(1)), ("J", lambda: self.skip(-3)), ("Shift+Left", lambda: self.skip(-3)),
                ("Shift+Right", lambda: self.skip(3)), ("Up", lambda: self.nudge_volume(5)),
                ("Down", lambda: self.nudge_volume(-5)), ("M", self.toggle_mute), ("F", self.toggle_full),
                ("F11", self.toggle_full), ("Return", self.toggle_full), ("Esc", self._escape), ("N", lambda: self.go(1)),
                ("P", lambda: self.go(-1)), ("[", lambda: self.step_speed(-1)), ("]", lambda: self.step_speed(1)),
                (",", lambda: self.frame_step(-1)), (".", lambda: self.frame_step(1)), ("S", self.screenshot),
                ("L", self.mark_loop), ("I", self.toggle_mini), ("G", lambda: self.shift_subs(-SUB_STEP_MS)),
                ("H", lambda: self.shift_subs(SUB_STEP_MS)), ("V", self.cycle_subs), ("Ctrl+O", self.open_dialog),
                ("Home", lambda: self._seek_to(0)), ("Q", self.toggle_playlist), ("Z", self.cycle_fit),
                ("Ctrl+Right", lambda: self.chapter_step(1)), ("Ctrl+Left", lambda: self.chapter_step(-1)),
                ("Ctrl+U", self._ask_link)]
        for i in range(10):
            keys.append((str(i), lambda i=i: self._jump_percent(i * 10)))
        for k, fn in keys:
            QShortcut(QKeySequence(k), self).activated.connect(fn)
        self.apply_theme(controller.theme)
        self.open(path)

    # ---- opening

    def open(self, path: Path, keep_list: bool = False):
        self._remember()
        path = Path(path)
        if not keep_list or path not in self.files:
            self.files = media_in(path.parent) or [path]
            if path not in self.files:
                self.files.insert(0, path)
            if self.a_shuffle.isChecked():
                self.a_shuffle.setChecked(False)
        self.index = self.files.index(path) if path in self.files else 0
        self.subs = None
        self.loop_a = self.loop_b = None
        self.b_loop.setChecked(False)
        self.note_timer.stop()
        self.note_box.hide()
        self.setWindowTitle(f"{path.name} - {APP_NAME}")
        self.title.setText(ltr(path.name))
        self.title.setToolTip(str(path))
        audio_only = path.suffix.lower() in AUDIO_EXT
        self.screen_stack.setCurrentWidget(self.cover if audio_only else self.video)
        self._show_cover(None)
        self.resume_to = 0
        self._resume_said = False
        self._resume_tries = 0
        if self.c.settings.player_resume:
            saved = load_positions().get(str(path))
            if saved:
                self.resume_to = int(saved.get("pos", 0))
        self._said_playing = False
        log.info("player opened a %s file", path.suffix.lower())
        self.link = ""
        self._load_chapters(path)
        local_only()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()
        if not audio_only:
            side = subtitle_for(path)
            if side is not None:
                self.load_subtitles(side, quiet=True)
        self._buttons()

    def current(self) -> Path:
        return self.files[self.index] if self.files else Path()

    def open_link(self, url: str):
        """Plays a video or a stream from the internet (only when the person pastes a link)."""
        url = url.strip()
        if not re.match(r"^(https?|rtsp|rtmp)://\S+$", url, re.I):
            self.flash(tr("That isn't a link to a video."), 3500)
            return
        self._remember()
        self.files, self.index, self.link = [], 0, url
        self.subs = None
        self.loop_a = self.loop_b = None
        self.b_loop.setChecked(False)
        self.seek.chapters = []
        self.resume_to = 0
        self.setWindowTitle(f"{url} - {APP_NAME}")
        self.title.setText(ltr(url if len(url) < 80 else url[:77] + "…"))
        self.title.setToolTip(url)
        self.screen_stack.setCurrentWidget(self.video)
        log.info("player opened a link")
        local_only(False)
        self.player.setSource(QUrl(url))
        self.player.play()
        self._buttons()

    def _ask_link(self):
        from PySide6.QtWidgets import QInputDialog
        start = QGuiApplication.clipboard().text().strip()
        url, ok = QInputDialog.getText(self, tr("Play a link"), tr("Paste the link of a video or a stream:"),
                                       text=start if "://" in start else "")
        if ok and url.strip():
            self.open_link(url)

    # ---- chapters

    def _load_chapters(self, path: Path):
        self.seek.chapters = []
        self.seek.update()
        if path.suffix.lower() not in (".mkv", ".mp4", ".m4v", ".m4b", ".mov", ".webm", ".m4a", ".mka"):
            return
        import threading
        done = self.chapters_done

        def work():
            found = chapters_of(path)
            try:
                done.emit(str(path), found)
            except RuntimeError:  # the window was closed
                pass
        threading.Thread(target=work, daemon=True, name="corefold-chapters").start()

    def _chapters_loaded(self, path: str, found: list):
        if path == str(self.current()) and not self.link:
            self.seek.chapters = found
            self.seek.update()

    def chapter_step(self, step: int):
        """The next or the previous chapter (Ctrl+Right, Ctrl+Left)."""
        marks = self.seek.chapters
        if not marks:
            return
        pos = self.player.position()
        starts = [ms for ms, _t in marks]
        i = bisect.bisect_right(starts, pos + 500) - 1  # the chapter playing (half a second of slack)
        if step < 0 and pos - starts[max(i, 0)] > 3000:
            target = max(i, 0)  # back to the start of this one first, like a CD player
        else:
            target = min(max(i + step, 0), len(starts) - 1)
        self._seek_to(starts[target])
        self.flash(marks[target][1])

    # ---- the playlist

    def toggle_playlist(self):
        if self.mini:
            return
        self.playlist.setVisible(not self.playlist.isVisible())
        self._fill_playlist()

    def _fill_playlist(self):
        if not self.playlist.isVisible():
            return
        self.playlist.blockSignals(True)
        self.playlist.clear()
        for i, f in enumerate(self.files):
            self.playlist.addItem(ltr(f"{i + 1}. {f.stem}"))
            self.playlist.item(i).setToolTip(f.name)
        if 0 <= self.index < self.playlist.count():
            self.playlist.setCurrentRow(self.index)
            self.playlist.scrollToItem(self.playlist.currentItem())
        self.playlist.blockSignals(False)

    def _play_row(self, row: int):
        if 0 <= row < len(self.files) and row != self.index:
            self.open(self.files[row], keep_list=True)

    def _repeat(self, on: bool):
        from PySide6.QtMultimedia import QMediaPlayer
        self.player.setLoops(QMediaPlayer.Loops.Infinite if on else 1)
        self.flash(tr("Repeating this file") if on else tr("Repeat off"))

    def _shuffle(self, on: bool):
        """Shuffle: the rest of the list in a random order (the file playing stays where it is)."""
        import random
        cur = self.current()
        if on:
            rest = [f for f in self.files if f != cur]
            random.shuffle(rest)
            self.files = [cur] + rest
        else:
            self.files = media_in(cur.parent) or [cur]
            if cur not in self.files:
                self.files.insert(0, cur)
        self.index = self.files.index(cur) if cur in self.files else 0
        self._buttons()
        self._fill_playlist()
        self.flash(tr("Shuffle on") if on else tr("Shuffle off"))

    # ---- how the picture fills the window

    def set_fit(self, key: str):
        modes = {"fit": Qt.KeepAspectRatio, "fill": Qt.KeepAspectRatioByExpanding, "stretch": Qt.IgnoreAspectRatio}
        self.fit = key if key in modes else "fit"
        self.video.setAspectRatioMode(modes[self.fit])
        for k, a in self.fit_actions.items():
            a.setChecked(k == self.fit)
        self.flash(tr(dict(FIT_MODES)[self.fit]), 1500)

    def cycle_fit(self):
        keys = [k for k, _ in FIT_MODES]
        self.set_fit(keys[(keys.index(self.fit) + 1) % len(keys)])

    def go(self, step: int):
        if len(self.files) < 2:
            return
        i = self.index + step
        if 0 <= i < len(self.files):
            self.open(self.files[i], keep_list=True)

    def open_dialog(self):
        pattern = " ".join(f"*{e}" for e in sorted(MEDIA_EXT))
        name, _ = QFileDialog.getOpenFileName(self, tr("Open a file"), str(self.current().parent or Path.home()),
                                              f"{tr('Videos and music')} ({pattern});;{tr('All files')} (*)")
        if name:
            self.open(Path(name))

    def _buttons(self):
        many = len(self.files) > 1
        self.b_prev.setEnabled(many and self.index > 0)
        self.b_next.setEnabled(many and self.index < len(self.files) - 1)
        self._fill_playlist()

    # ---- playing

    def toggle(self):
        from PySide6.QtMultimedia import QMediaPlayer
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            if self.player.mediaStatus() == QMediaPlayer.EndOfMedia:
                self.player.setPosition(0)
            self.player.play()

    def skip(self, times: int):
        step = int(self.c.settings.player_skip) * 1000
        self._seek_to(max(0, self.player.position() + times * step))

    def _seek_to(self, ms: int):
        self.player.setPosition(int(max(0, min(ms, self.player.duration() or ms))))

    def _jump_percent(self, pct: int):
        if self.player.duration() > 0:
            self._seek_to(self.player.duration() * pct // 100)

    def frame_step(self, direction: int):
        """One picture forward or back (while paused)."""
        from PySide6.QtMultimedia import QMediaMetaData
        self.player.pause()
        fps = self.player.metaData().value(QMediaMetaData.Key.VideoFrameRate)
        try:
            fps = float(fps) if fps else 25.0
        except (TypeError, ValueError):
            fps = 25.0
        self._seek_to(self.player.position() + direction * max(1, round(1000 / max(1.0, fps))))

    def set_speed(self, speed: float):
        self.player.setPlaybackRate(speed)
        self.b_speed.setText(f"{speed:g}×")
        self.flash(tr("Speed: {speed}", speed=f"{speed:g}×"))

    def step_speed(self, direction: int):
        now = self.player.playbackRate()
        i = min(range(len(SPEEDS)), key=lambda k: abs(SPEEDS[k] - now))
        self.set_speed(SPEEDS[max(0, min(len(SPEEDS) - 1, i + direction))])

    def _volume(self, v: int):
        self.audio.setVolume(v / 100)
        self.audio.setMuted(False)
        self.c.settings.player_volume = v
        self._mute_icon()

    def nudge_volume(self, d: int):
        self.volume.setValue(max(0, min(100, self.volume.value() + d)))
        self.flash(tr("Volume: {value}%", value=self.volume.value()))

    def toggle_mute(self):
        self.audio.setMuted(not self.audio.isMuted())
        self._mute_icon()

    def _mute_icon(self):
        name = "speaker_mute" if self.audio.isMuted() or self.volume.value() == 0 else "speaker_2"
        self._set_icon(self.b_mute, name)

    def mark_loop(self):
        """First press: the loop's start. Second: its end (it starts looping). Third: no loop."""
        pos = self.player.position()
        if self.loop_a is None:
            self.loop_a = pos
            self.b_loop.setChecked(True)
            self.flash(tr("Loop from {time}: press again where it should end", time=clock(pos)))
        elif self.loop_b is None and pos > self.loop_a + 300:
            self.loop_b = pos
            self.b_loop.setChecked(True)
            self.flash(tr("Looping {a} to {b}", a=clock(self.loop_a), b=clock(pos)))
            self._seek_to(self.loop_a)
        else:
            self.loop_a = self.loop_b = None
            self.b_loop.setChecked(False)
            self.flash(tr("Loop off"))

    def save_clip(self):
        """The A–B part as a video of its own (made by the Converter's Trim, in the background)."""
        if self.link:
            self.flash(tr("This works with files on your PC."), 3500)
            return
        if self.loop_a is None or self.loop_b is None:
            self.flash(tr("Mark the start and the end with the loop button (L) first."), 4000)
            return
        from .converters import catalog
        src = self.current()
        kind = "audio" if src.suffix.lower() in AUDIO_EXT else "video"
        tool = next((t for t in catalog().get(kind, []) if t.key.endswith(":trim")), None)
        if tool is None:
            return
        self.c.start_job(tool, [src], params={"start": self.loop_a / 1000, "end": self.loop_b / 1000})

    def screenshot(self):
        """The frame on screen, saved in Pictures\\Screenshots and copied."""
        from .capture import screenshots_folder
        frame = self.video.videoSink().videoFrame() if self.screen_stack.currentWidget() is self.video else None
        img = frame.toImage() if frame is not None and frame.isValid() else QImage()
        if img.isNull():
            self.flash(tr("There's no picture to save here."))
            return
        folder = screenshots_folder()
        stem = re.sub(r'[<>:"/\\|?*]', "", self.current().stem)[:80] or "Video"
        out = folder / f"{stem} {clock(self.player.position()).replace(':', '.')}.png"
        n = 2
        while out.exists():
            out = folder / f"{stem} {clock(self.player.position()).replace(':', '.')} ({n}).png"
            n += 1
        try:
            folder.mkdir(parents=True, exist_ok=True)
            saved = img.save(str(out), "PNG")
        except OSError:
            saved = False
        QGuiApplication.clipboard().setImage(img)
        if not saved:  # (the folder is on a drive that's gone, or can't be written to: it's still copied)
            self.flash(tr("Copied") + " · " + tr("Couldn't save"), 3500)
            return
        self.flash(tr("Saved and copied: {name}", name=out.name), 3500)
        self.last_shot = out

    # ---- subtitles and sound tracks

    def _track_name(self, meta, n: int) -> str:
        from PySide6.QtCore import QLocale
        from PySide6.QtMultimedia import QMediaMetaData
        title = meta.value(QMediaMetaData.Key.Title)
        lang = meta.value(QMediaMetaData.Key.Language)
        bits = []
        if title:
            bits.append(str(title))
        try:
            if lang is not None and int(lang) not in (0, 1):  # (AnyLanguage, C)
                bits.append(QLocale.languageToString(lang))
        except (TypeError, ValueError):
            pass
        return " · ".join(bits) or tr("Track {number}", number=n + 1)

    def _subs_menu(self):
        menu = QMenu(self)
        round_menu(menu)
        off = menu.addAction(tr("Off"), lambda: self.set_subtitle_track(-1))
        off.setCheckable(True)
        on_track = self.player.activeSubtitleTrack()
        off.setChecked(on_track < 0 and self.subs is None)
        for i, meta in enumerate(self.player.subtitleTracks()):
            act = menu.addAction(self._track_name(meta, i), lambda i=i: self.set_subtitle_track(i))
            act.setCheckable(True)
            act.setChecked(i == on_track and self.subs is None)
        if self.subs is not None:
            act = menu.addAction(ltr(self.subs.name), lambda: None)
            act.setCheckable(True)
            act.setChecked(True)
        menu.addSeparator()
        menu.addAction(tr("Add a subtitle file…"), self._pick_subtitles)
        if self.subs is not None:
            menu.addAction(tr("Show them earlier (G)"), lambda: self.shift_subs(-SUB_STEP_MS))
            menu.addAction(tr("Show them later (H)"), lambda: self.shift_subs(SUB_STEP_MS))
        menu.exec(QCursor.pos())

    def set_subtitle_track(self, i: int):
        self.subs = None
        self.video.videoSink().setSubtitleText("")
        self.player.setActiveSubtitleTrack(i)

    def cycle_subs(self):
        tracks = len(self.player.subtitleTracks())
        if not tracks:
            return
        nxt = self.player.activeSubtitleTrack() + 1
        self.set_subtitle_track(nxt if nxt < tracks else -1)
        self.flash(tr("Subtitles: {name}", name=self._track_name(self.player.subtitleTracks()[nxt], nxt))
                   if nxt < tracks else tr("Subtitles off"))

    def _pick_subtitles(self):
        pattern = " ".join(f"*{e}" for e in sorted(SUB_EXT))
        name, _ = QFileDialog.getOpenFileName(self, tr("Add a subtitle file"), str(self.current().parent),
                                              f"{tr('Subtitles')} ({pattern})")
        if name:
            self.load_subtitles(Path(name))

    def load_subtitles(self, path: Path, quiet: bool = False) -> bool:
        try:
            cues = read_subtitles(path)
        except OSError:
            cues = []
        if not cues:
            if not quiet:
                self.flash(tr("No subtitles found in {name}", name=path.name))
            return False
        self.player.setActiveSubtitleTrack(-1)
        self.subs = Subtitles(cues, path.name)
        if not quiet:
            self.flash(tr("Subtitles: {name}", name=path.name))
        return True

    def shift_subs(self, ms: int):
        if self.subs is None:
            return
        self.subs.shift += ms
        self.flash(tr("Subtitles shifted by {seconds} s", seconds=f"{self.subs.shift / 1000:+.1f}"))
        self._show_subs(self.player.position())

    def _show_subs(self, pos: int):
        if self.subs is not None:
            self.video.videoSink().setSubtitleText(self.subs.text_at(pos))

    def _audio_menu(self):
        tracks = self.player.audioTracks()
        menu = QMenu(self)
        round_menu(menu)
        if len(tracks) < 2:
            act = menu.addAction(tr("This file has one sound track"))
            act.setEnabled(False)
        for i, meta in enumerate(tracks):
            act = menu.addAction(self._track_name(meta, i), lambda i=i: self.player.setActiveAudioTrack(i))
            act.setCheckable(True)
            act.setChecked(i == self.player.activeAudioTrack())
        menu.exec(QCursor.pos())

    # ---- what the player says

    def _start_over(self):
        self.resume_to = 0
        self._seek_to(0)

    def _position(self, pos: int):
        if self.resume_to:  # (a jump asked for while the file was still opening can be lost: ask again)
            if pos >= self.resume_to - 1500 or self._resume_tries >= 5:
                self.resume_to = 0
            elif pos > 0:
                self._resume_tries += 1
                self.player.setPosition(self.resume_to)
                return
        if not self.seek.isSliderDown():
            self.seek.setValue(int(pos))
        self.t_now.setText(clock(pos))
        self._show_subs(pos)
        if self.loop_b is not None and pos >= self.loop_b:
            self._seek_to(self.loop_a or 0)
        now = time.monotonic()
        if now - self._last_pos_save > 10:  # (if the PC turns off, it's still remembered)
            self._last_pos_save = now
            self._remember()

    def _duration(self, dur: int):
        self.seek.setRange(0, int(dur))
        self.t_all.setText(clock(dur))

    def _state(self, state):
        from PySide6.QtMultimedia import QMediaPlayer
        playing = state == QMediaPlayer.PlayingState
        self._set_icon(self.b_play, "pause" if playing else "play")
        if not playing:
            self._remember()

    def _status(self, status):
        from PySide6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.BufferedMedia and not self._said_playing:
            self._said_playing = True
            log.info("player is playing (video %s, sound %s)", self.player.hasVideo(), self.player.hasAudio())
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) and self.resume_to:
            if not self._resume_said:
                self._resume_said = True
                self.flash(tr("Picking up where you left off ({time}).", time=clock(self.resume_to)), 6000,
                           tr("Start over"), self._start_over)
            self.player.setPosition(self.resume_to)  # (checked again as it plays: see _position)
        elif status == QMediaPlayer.EndOfMedia:
            save_position(self.current(), 0, self.player.duration())  # (finished: not in Continue watching)
            if self.c.settings.player_next and self.index < len(self.files) - 1:
                self.go(1)

    def _error(self, error, text: str = ""):
        from PySide6.QtMultimedia import QMediaPlayer
        if error == QMediaPlayer.NoError:
            return
        log.warning("player can't play: %s %s", error, text)
        self.flash(tr("Can't play {name}: {reason}", name=self.link or self.current().name,
                      reason=text or str(error)), 8000)

    def _tracks(self):
        if self.subs is None and self.player.activeSubtitleTrack() < 0 and self.player.subtitleTracks() \
                and self.c.settings.player_subtitles:
            self.player.setActiveSubtitleTrack(0)

    def _meta(self):
        from PySide6.QtMultimedia import QMediaMetaData
        meta = self.player.metaData()
        title = meta.value(QMediaMetaData.Key.Title)
        artist = meta.value(QMediaMetaData.Key.ContributingArtist) or meta.value(QMediaMetaData.Key.AlbumArtist)
        if self.screen_stack.currentWidget() is self.cover:
            art = meta.value(QMediaMetaData.Key.ThumbnailImage) or meta.value(QMediaMetaData.Key.CoverArtImage)
            self._show_cover(art if isinstance(art, QImage) and not art.isNull() else None)
            if title:
                self.title.setText(ltr(f"{title}" + (f" · {artist}" if artist else "")))

    def _show_cover(self, art):
        if art is None:
            pm = icons.product_tile("player", 160, True, self.devicePixelRatioF())
        else:
            pm = QPixmap.fromImage(art).scaled(QSize(360, 360) * self.devicePixelRatioF(), Qt.KeepAspectRatio,
                                               Qt.SmoothTransformation)
            pm.setDevicePixelRatio(self.devicePixelRatioF())
        self.cover.setPixmap(pm)

    def flash(self, text: str, ms: int = 2200, action_text: str = "", action=None):
        """A short line above the controls."""
        self.note.setText(text)
        self._note_fn = action
        self.note_btn.setText(action_text)
        self.note_btn.setVisible(bool(action_text and action))
        if not (self.isFullScreen() and self.bar.isHidden()):
            self.note_box.show()
        self.note_timer.start(ms)

    def _note_action(self):
        fn, self._note_fn = self._note_fn, None
        self.note_box.hide()
        if fn:
            fn()

    def _remember(self):
        p = self.current()
        if p.name and self.player.duration() > 0 and p.suffix.lower() in VIDEO_EXT | {".m4b"}:
            save_position(p, self.player.position(), self.player.duration())

    # ---- full screen and the mini player

    def toggle_full(self):
        if self.mini:
            self.toggle_mini()
        if self.isFullScreen():
            self.showNormal()
            self.idle.stop()
            self.bar.show()
            self.unsetCursor()
        else:
            self.playlist.hide()
            self.showFullScreen()
            self._still_since = time.monotonic()
            self.idle.start()
        self._set_icon(self.b_full, "full_screen_minimize" if self.isFullScreen() else "full_screen_maximize")

    def _idle_check(self):
        pos = QCursor.pos()
        if pos != self._last_mouse:
            self._last_mouse = pos
            self._still_since = time.monotonic()
            if self.bar.isHidden():
                self.bar.show()
                self.unsetCursor()
        elif time.monotonic() - self._still_since > 2.5 and not self.bar.underMouse() and not self.bar.isHidden():
            from PySide6.QtMultimedia import QMediaPlayer
            if self.player.playbackState() == QMediaPlayer.PlayingState:
                self.bar.hide()
                self.note_box.hide()
                self.setCursor(Qt.BlankCursor)

    def toggle_mini(self):
        """A small window that stays on top of everything (for watching while you work)."""
        if self.isFullScreen():
            self.toggle_full()
        self.mini = not self.mini
        if self.mini:
            self._normal_geometry = self.saveGeometry()
            self.playlist.hide()
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            for b in (self.b_prev, self.b_next, self.b_mute, self.volume, self.title, self.b_speed, self.b_subs,
                      self.b_audio, self.b_loop, self.b_shot, self.b_more):
                b.hide()
            self.show()
            area = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
            self.resize(480, 340)
            self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 24)
        else:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
            for b in (self.b_prev, self.b_next, self.b_mute, self.volume, self.title, self.b_speed, self.b_subs,
                      self.b_audio, self.b_loop, self.b_shot, self.b_more):
                b.show()
            self.show()
            if self._normal_geometry is not None:
                self.restoreGeometry(self._normal_geometry)
        self.b_mini.setChecked(self.mini)

    def _escape(self):
        if self.isFullScreen():
            self.toggle_full()
        elif self.mini:
            self.toggle_mini()
        else:
            self.close()

    # ---- the mouse and dropped files

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.MouseButtonPress and e.button() == Qt.LeftButton:
            self.click_timer.start()  # (a double click cancels it: full screen instead of pausing)
            return True
        if t == QEvent.MouseButtonPress and e.button() in (Qt.BackButton, Qt.ForwardButton):
            self.go(-1 if e.button() == Qt.BackButton else 1)  # (the mouse's side buttons: as in mpv)
            return True
        if t == QEvent.MouseButtonDblClick and e.button() == Qt.LeftButton:
            self.click_timer.stop()
            self.toggle_full()
            return True
        if t == QEvent.Wheel:
            up = e.angleDelta().y() > 0
            if self.c.settings.player_wheel == "seek":
                self.skip(1 if up else -1)
            else:
                self.nudge_volume(5 if up else -5)
            return True
        if t == QEvent.ContextMenu:
            self._context_menu()
            return True
        return super().eventFilter(obj, e)

    def _context_menu(self):
        menu = QMenu(self)
        round_menu(menu)
        menu.addAction(tr("Play / pause"), self.toggle)
        menu.addAction(tr("Full screen"), self.toggle_full)
        menu.addAction(tr("Mini player"), self.toggle_mini)
        menu.addSeparator()
        menu.addAction(tr("Save this frame as a picture"), self.screenshot)
        menu.addAction(tr("Add a subtitle file…"), self._pick_subtitles)
        if self.seek.chapters:
            sub = menu.addMenu(tr("Chapters"))
            round_menu(sub)
            for ms, title in self.seek.chapters:
                sub.addAction(f"{clock(ms)}  {title}", lambda m=ms: self._seek_to(m))
        menu.addAction(tr("Open a file…"), self.open_dialog)
        menu.addAction(tr("Play a link…"), self._ask_link)
        menu.exec(QCursor.pos())

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        subs = [p for p in paths if p.suffix.lower() in SUB_EXT]
        media = [p for p in paths if is_media(p)]
        if len(media) > 1:  # several files: they're the playlist, in the order they were picked
            self.files, self.index = media, 0
            self.open(media[0], keep_list=True)
        elif media:
            self.open(media[0])
        if subs:
            self.load_subtitles(subs[0])

    def _reveal(self):
        from .winutil import reveal_in_explorer
        reveal_in_explorer(self.current())

    def _open_with(self):
        """Windows' "Open with" box, for when another player does something this one doesn't."""
        from .winutil import open_with
        path = self.current()
        if path.is_file():
            self.player.pause()
            open_with(path)

    # ---- looks and closing

    INK, DIM = "#ececec", "#55585a"  # (the player is always dark, whatever the app's look)

    def _set_icon(self, b: QPushButton, name: str):
        for i, (btn, _n) in enumerate(self.icon_buttons):
            if btn is b:
                self.icon_buttons[i] = (btn, name)
        b.setIcon(icons.icon(name, self.INK, disabled=self.DIM))

    def apply_theme(self, theme):
        for b, name in self.icon_buttons:
            b.setIcon(icons.icon(name, self.INK, disabled=self.DIM))
        set_title_bar_dark(self, True)

    def showEvent(self, e):
        super().showEvent(e)
        set_title_bar_dark(self, True)

    def changeEvent(self, e):
        """Settings › Player: a video pauses while the window is minimised, and goes on when it's back."""
        super().changeEvent(e)
        if e.type() != QEvent.WindowStateChange or not getattr(self.c.settings, "player_pause_minimized", False):
            return
        from PySide6.QtMultimedia import QMediaPlayer
        if self.isMinimized():
            video = self.screen_stack.currentWidget() is self.video  # (music goes on playing)
            if video and self.player.playbackState() == QMediaPlayer.PlayingState:
                self.player.pause()
                self._paused_minimized = True
        elif getattr(self, "_paused_minimized", False):
            self._paused_minimized = False
            if self.player.playbackState() == QMediaPlayer.PausedState:
                self.player.play()

    def closeEvent(self, e):
        self._remember()
        self.idle.stop()
        self.player.stop()
        self.player.setSource(QUrl())
        self.c.settings_changed()
        super().closeEvent(e)

