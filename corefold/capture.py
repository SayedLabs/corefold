"""Corefold Capture: pictures and videos of the screen.

A shortcut freezes the screen, you drag over what you want (or click a window, or press Enter for
the whole screen), and the picture opens in a small editor to mark it up, copy it or save it.
Videos are recorded by FFmpeg in a process of its own, so Corefold stays light while it records.
Nothing is uploaded anywhere."""
from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from .i18n import tr

log = logging.getLogger("corefold")


def screenshots_folder() -> Path:
    """Pictures\\Screenshots, where Windows keeps its own screenshots."""
    pics = Path.home() / "Pictures"
    if os.name == "nt":
        try:
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x27, None, 0, buf) == 0:  # CSIDL_MYPICTURES
                pics = Path(buf.value)
        except Exception:
            pass
    return pics / "Screenshots"


def videos_folder() -> Path:
    vids = Path.home() / "Videos"
    if os.name == "nt":
        try:
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0E, None, 0, buf) == 0:  # CSIDL_MYVIDEO
                vids = Path(buf.value)
        except Exception:
            pass
    return vids / "Captures"


def new_name(folder: Path, ext: str, prefix: str = "Screenshot") -> Path:
    stamp = time.strftime("%Y-%m-%d %H%M%S")
    p = folder / f"{prefix} {stamp}{ext}"
    n = 2
    while p.exists():
        p = folder / f"{prefix} {stamp} ({n}){ext}"
        n += 1
    return p


# ----------------------------------------------------------------------------- windows on screen

def _native_to_logical(x: int, y: int) -> QPoint:
    for screen in QGuiApplication.screens():
        g, r = screen.geometry(), screen.devicePixelRatio()
        if QRect(g.x(), g.y(), round(g.width() * r), round(g.height() * r)).contains(x, y):
            return QPoint(g.x() + round((x - g.x()) / r), g.y() + round((y - g.y()) / r))
    return QPoint(x, y)


def visible_windows(skip: set[int] | None = None) -> list[QRect]:
    """The windows on screen, front to back, in Qt's coordinates (for "click a window")."""
    if os.name != "nt":
        return []
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    dwm = ctypes.windll.dwmapi
    found: list[QRect] = []
    skip = skip or set()
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def each(hwnd, _):
        if hwnd in skip or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
            return True
        cloaked = ctypes.c_int(0)
        dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)  # DWMWA_CLOAKED (hidden apps, other desktops)
        if cloaked.value:
            return True
        if u.GetWindowTextLengthW(hwnd) == 0:
            return True
        r = wt.RECT()
        if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:  # the frame without shadow
            if not u.GetWindowRect(hwnd, ctypes.byref(r)):
                return True
        if r.right - r.left < 40 or r.bottom - r.top < 30:
            return True
        a, b = _native_to_logical(r.left, r.top), _native_to_logical(r.right, r.bottom)
        found.append(QRect(a, b).normalized())
        return True
    try:
        u.EnumWindows(PROC(each), 0)
    except Exception:
        return []
    return found


def exclude_from_capture(widget):
    """Keeps this window out of screenshots and recordings (the recording bar, the frame)."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), 0x11)  # WDA_EXCLUDEFROMCAPTURE
    except Exception:
        pass


# ----------------------------------------------------------------------------- picking an area

class Snip(QWidget):
    """The frozen screen, darker, on one monitor. Drag over an area, click a window, or press Enter
    for the whole screen. Esc gives up."""
    picked = Signal(object, object)   # (QImage, its rectangle on screen in Qt's coordinates), or (None, None)

    def __init__(self, screen, shot: QPixmap, windows: list[QRect], mode: str = "picture"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.screen_ = screen
        self.shot = shot
        self.mode = mode
        self.geo = screen.geometry()
        self.windows = [w.intersected(self.geo).translated(-self.geo.topLeft()) for w in windows
                        if w.intersects(self.geo)]
        self.start: QPoint | None = None
        self.end: QPoint | None = None
        self.hover: QRect | None = None
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setGeometry(self.geo)
        self.done = False

    def showEvent(self, e):
        super().showEvent(e)
        exclude_from_capture(self)
        self.activateWindow()
        self.raise_()
        self.setFocus()
        self._hover_at(self.mapFromGlobal(QCursor.pos()))

    def _hover_at(self, pos: QPoint):
        under = next((w for w in self.windows if w.contains(pos)), None)
        if under != self.hover:
            self.hover = under
            self.update()

    def selection(self) -> QRect | None:
        if self.start is None or self.end is None:
            return None
        r = QRect(self.start, self.end).normalized()
        return r if r.width() > 3 and r.height() > 3 else None

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.shot)
        dim = QColor(0, 0, 0, 110)
        sel = self.selection() or (self.hover if self.start is None else None)
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))
        if sel is not None:
            hole = QPainterPath()
            hole.addRect(QRectF(sel))
            path = path.subtracted(hole)
        p.fillPath(path, dim)
        if sel is not None:
            p.setPen(QPen(QColor("#3cc7b2"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(sel.adjusted(0, 0, -1, -1))
            ratio = self.shot.devicePixelRatio() or 1.0
            text = f"{round(sel.width() * ratio)} × {round(sel.height() * ratio)}"
            f = QFont(self.font())
            f.setPointSizeF(9)
            p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(text) + 14
            box = QRect(sel.left(), sel.top() - 26 if sel.top() > 30 else sel.bottom() + 6, tw, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(20, 22, 24, 220))
            p.drawRoundedRect(box, 6, 6)
            p.setPen(QColor("#fff"))
            p.drawText(box, Qt.AlignCenter, text)
        if self.start is None:
            hint = tr("Drag over what you want, or click a window. Enter: the whole screen. Esc: cancel.") \
                if self.mode == "picture" else \
                tr("Drag over what to record, or click a window. Enter: the whole screen. Esc: cancel.")
            f = QFont(self.font())
            f.setPointSizeF(10)
            p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(hint) + 28
            box = QRect((self.width() - tw) // 2, 28, tw, 34)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(20, 22, 24, 225))
            p.drawRoundedRect(box, 10, 10)
            p.setPen(QColor("#fff"))
            p.drawText(box, Qt.AlignCenter, hint)

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self._finish(None)
            return
        if e.button() == Qt.LeftButton:
            self.start = self.end = e.position().toPoint()
            self.update()

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self.start is not None:
            self.end = pos
            self.update()
        else:
            self._hover_at(pos)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self.start is None:
            return
        self.end = e.position().toPoint()
        sel = self.selection()
        if sel is None:  # a click: the window under it, or the whole screen
            sel = self.hover or self.rect()
        self._finish(sel)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._finish(None)
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._finish(self.rect())

    def _finish(self, sel: QRect | None):
        if self.done:
            return
        self.done = True
        if sel is None:
            self.picked.emit(None, None)
        else:
            ratio = self.shot.devicePixelRatio() or 1.0
            px = QRect(round(sel.x() * ratio), round(sel.y() * ratio), round(sel.width() * ratio),
                       round(sel.height() * ratio))
            img = self.shot.toImage().copy(px)
            img.setDevicePixelRatio(1.0)
            self.picked.emit(img, sel.translated(self.geo.topLeft()))
        self.close()


class Picker:
    """Freezes every screen and lets you pick an area on one of them."""

    def __init__(self, on_picked, mode: str = "picture"):
        self.on_picked = on_picked
        self.snips: list[Snip] = []
        self.mode = mode
        self.answered = False

    def start(self):
        screens = QGuiApplication.screens()
        shots = [(s, s.grabWindow(0)) for s in screens]
        windows = visible_windows()
        for screen, shot in shots:
            snip = Snip(screen, shot, windows, self.mode)
            snip.picked.connect(self._picked)
            self.snips.append(snip)
            snip.show()
        under = QGuiApplication.screenAt(QCursor.pos())
        for snip in self.snips:
            if snip.screen_ is under:
                snip.activateWindow()
                snip.setFocus()

    def _picked(self, img, rect):
        if self.answered:
            return
        self.answered = True
        for snip in self.snips:
            if not snip.done:
                snip.done = True
                snip.close()
        self.snips = []
        QTimer.singleShot(0, lambda: self.on_picked(img, rect))


# ----------------------------------------------------------------------------- recording

def ffmpeg_path() -> Path | None:
    from .converters import find_tool
    try:
        return Path(find_tool("ffmpeg"))
    except RuntimeError:
        return None


def microphones() -> list[str]:
    """The microphones FFmpeg can record from (their Windows names)."""
    exe = ffmpeg_path()
    if exe is None or os.name != "nt":
        return []
    try:
        from .core import job_flags
        out = subprocess.run([str(exe), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                             creationflags=job_flags()).stderr
    except (OSError, subprocess.SubprocessError):
        return []
    return [m.group(1) for m in re.finditer(r'"([^"]+)"\s*\(audio\)', out)]


def native_rect(rect: QRect) -> QRect:
    """A rectangle in Qt's coordinates as Windows' pixels (what FFmpeg records)."""
    screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
    g, r = screen.geometry(), screen.devicePixelRatio()
    x = g.x() + round((rect.x() - g.x()) * r)
    y = g.y() + round((rect.y() - g.y()) * r)
    w, h = round(rect.width() * r), round(rect.height() * r)
    return QRect(x, y, w - w % 2, h - h % 2)  # (the video needs even sizes)


def record_command(exe: Path, rect: QRect, out: Path, fps: int = 30, mic: str = "", cursor: bool = True,
                   pc_sound: str = "") -> list[str]:
    """FFmpeg's command for recording rect. pc_sound is the pipe the PC's own sound comes through ("" = none)."""
    n = native_rect(rect)
    cmd = [str(exe), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1" if cursor else "0",
           "-offset_x", str(n.x()), "-offset_y", str(n.y()), "-video_size", f"{n.width()}x{n.height()}",
           "-thread_queue_size", "512", "-i", "desktop"]
    sounds = 0
    if mic:
        cmd += ["-f", "dshow", "-thread_queue_size", "512", "-i", f"audio={mic}"]
        sounds += 1
    if pc_sound:
        from .loopback import ffmpeg_input
        cmd += ffmpeg_input(pc_sound)
        sounds += 1
    if sounds == 2:  # the microphone and the PC's sound together in one track
        cmd += ["-filter_complex", "[1:a][2:a]amix=inputs=2:duration=first:normalize=0[a]", "-map", "0:v",
                "-map", "[a]"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    if sounds:
        cmd += ["-c:a", "aac", "-b:a", "160k"]
    cmd += ["-movflags", "+faststart", str(out)]
    return cmd


class Recorder:
    """FFmpeg recording an area of the screen, until stop(). A pause ends the part recorded so far and
    a resume starts the next part; stop() joins the parts into one file (without re-encoding)."""

    def __init__(self, rect: QRect, out: Path, fps: int = 30, mic: str = "", cursor: bool = True,
                 pc_sound: bool = False):
        self.rect, self.out = rect, out
        self.fps, self.mic, self.cursor, self.pc_sound = fps, mic, cursor, pc_sound
        self.proc: subprocess.Popen | None = None
        self.loopback = None
        self.started = 0.0
        self.error = ""
        self.no_pc_sound = False  # the PC's sound was asked for but couldn't be recorded
        self.parts: list[Path] = []
        self.paused = False
        self._before = 0.0        # seconds recorded in the parts before this one

    def start(self) -> bool:
        exe = ffmpeg_path()
        if exe is None:
            self.error = tr("FFmpeg is missing, so recording isn't possible.")
            return False
        self.out.parent.mkdir(parents=True, exist_ok=True)
        return self._start_part(exe)

    def _start_part(self, exe=None) -> bool:
        exe = exe or ffmpeg_path()
        part = self.out.with_name(f"{self.out.stem}.part{len(self.parts) + 1}{self.out.suffix}")
        pipe = ""
        if self.pc_sound:
            from .loopback import Loopback
            lb = Loopback()
            if lb.start():
                self.loopback, pipe = lb, lb.name
            else:
                log.info("recording without the PC's sound: %s", lb.error)
                self.no_pc_sound = True
        from .core import job_flags
        try:
            self.proc = subprocess.Popen(record_command(exe, self.rect, part, self.fps, self.mic, self.cursor,
                                                        pipe),
                                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                         creationflags=job_flags())
        except OSError as e:
            self.error = str(e)
            self._stop_sound()
            return False
        self.parts.append(part)
        self.started = time.monotonic()
        self.paused = False
        return True

    def _stop_sound(self):
        lb, self.loopback = self.loopback, None
        if lb is not None:
            lb.stop()

    def running(self) -> bool:
        """Still recording (or paused)."""
        return self.paused or (self.proc is not None and self.proc.poll() is None)

    def seconds(self) -> float:
        if self.paused or not self.started:
            return self._before
        return self._before + time.monotonic() - self.started

    def _finish_part(self, timeout: float = 15) -> str:
        """Asks FFmpeg to finish the part properly (like pressing q). Returns what FFmpeg said."""
        p, self.proc = self.proc, None
        if p is None:
            return ""
        self._before += time.monotonic() - self.started
        if p.poll() is None:
            try:
                p.stdin.write(b"q")
                p.stdin.flush()
                p.stdin.close()
            except OSError:
                pass
            try:
                p.wait(timeout)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(5)
        self._stop_sound()
        try:
            return p.stderr.read().decode("utf-8", "replace") if p.stderr else ""
        except (OSError, ValueError):
            return ""

    def pause(self) -> bool:
        if self.paused or self.proc is None:
            return False
        self._finish_part()
        self.paused = True
        return True

    def resume(self) -> bool:
        if not self.paused:
            return False
        if not self._start_part():
            return False
        return True

    def stop(self, timeout: float = 15) -> bool:
        """Finishes the recording: the last part, then the parts joined into self.out."""
        if not self.parts:
            return False
        err = self._finish_part(timeout) if not self.paused else ""
        self.paused = False
        parts = [p for p in self.parts if p.exists() and p.stat().st_size > 1000]
        try:
            if len(parts) == 1:
                os.replace(parts[0], self.out)
            elif parts:
                self._join(parts)
        except OSError as e:
            err = str(e)
        for p in self.parts:
            p.unlink(missing_ok=True)
        ok = self.out.exists() and self.out.stat().st_size > 1000
        if not ok:
            self.error = err.strip().splitlines()[-1] if err.strip() else tr("The recording couldn't be saved.")
        return ok

    def _join(self, parts: list[Path]):
        from .core import job_flags
        listing = self.out.with_name(self.out.stem + ".parts.txt")
        listing.write_text("".join("file '" + str(p).replace("'", "'\\''") + "'\n" for p in parts), "utf-8")
        try:
            r = subprocess.run([str(ffmpeg_path()), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
                                "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart",
                                str(self.out)], capture_output=True, timeout=600, creationflags=job_flags())
            if r.returncode != 0:
                raise OSError(r.stderr.decode("utf-8", "replace").strip()[-300:] or "joining failed")
        finally:
            listing.unlink(missing_ok=True)


# ----------------------------------------------------------------------------- the tool

class CaptureTool(QObject):
    """Corefold Capture while it's in use: its two shortcuts, and what happens after a screenshot
    or a recording."""

    def __init__(self, controller):
        super().__init__()
        from .hotkey import Hotkey
        self.c = controller
        self.picker: Picker | None = None
        self.editors: list = []
        self.recorder: Recorder | None = None
        self.bar = None
        self.frame = None
        self.tick = QTimer(self)
        self.tick.timeout.connect(self._tick)
        self.shot_key = Hotkey()
        self.shot_key.pressed.connect(self.screenshot, Qt.ConnectionType.QueuedConnection)
        self.rec_key = Hotkey()
        self.rec_key.pressed.connect(self.toggle_recording, Qt.ConnectionType.QueuedConnection)
        self._hidden = None
        self._later = None     # the countdown before a screenshot with a timer
        self._gif = False      # the recording being picked becomes a GIF
        self.apply_settings()

    @property
    def settings(self):
        return self.c.settings

    def apply_settings(self) -> tuple[bool, bool]:
        return self.shot_key.set(self.settings.cap_shortcut), self.rec_key.set(self.settings.cap_record_shortcut)

    def stop(self):
        if self._later is not None:
            self._later.cancel()
            self._later = None
        self.shot_key.stop()
        self.rec_key.stop()
        if self.recording():
            self.stop_recording(wait=True)

    def recording(self) -> bool:
        return self.recorder is not None

    # ---- pictures

    def _hide_corefold(self):
        """Corefold's own window steps aside while you pick (it comes back afterwards)."""
        w = self.c.window_if_open()
        if w is not None and w.isVisible() and not w.isMinimized():
            self._hidden = w
            w.hide()
            return 250
        return 0

    def _show_corefold(self):
        w, self._hidden = self._hidden, None
        if w is not None:
            w.show()

    def screenshot(self):
        if self.picker is not None or self.recording():
            return
        self.picker = Picker(self._picked, "picture")
        QTimer.singleShot(self._hide_corefold() or 10, self.picker.start)

    def screenshot_later(self, seconds: int | None = None):
        """A screenshot after a few seconds (time to open a menu, or hover over something)."""
        if self.picker is not None or self.recording():
            return
        if self._later is not None:
            self._later.cancel()  # (pressed again: start over)
        from .capview import Countdown
        self._later = Countdown(int(seconds or self.settings.cap_delay), self._later_done)
        self._later.show()

    def _later_done(self):
        self._later = None
        self.screenshot()

    def pin_last(self):
        """Pins what's copied (a screenshot just taken) on top of the screen."""
        img = QGuiApplication.clipboard().image()
        if img.isNull():
            self.c.tell(tr("Nothing to pin"), tr("Take a screenshot first, or copy a picture."))
            return
        from .capview import pin_picture
        pin_picture(self.c, img)

    def _picked(self, img, rect):
        self.picker = None
        self._show_corefold()
        if img is None:
            return
        QGuiApplication.clipboard().setImage(img)
        saved = None
        if self.settings.cap_save:
            saved = new_name(screenshots_folder(), ".png")
            try:
                saved.parent.mkdir(parents=True, exist_ok=True)
                if not img.save(str(saved), "PNG"):
                    saved = None
            except OSError:
                saved = None
        if self.settings.cap_after == "edit":
            self.edit(img, saved)
        else:
            self.c.toast.notify(tr("Screenshot copied"), tr("Paste it anywhere with Ctrl+V.") if saved is None else
                                tr("Paste it anywhere with Ctrl+V. Saved as {name}.", name=saved.name),
                                tr("Mark it up"), lambda i=img, p=saved: self.edit(i, p), linger_ms=6000)
        w = self.c.window_if_open()
        if w is not None and hasattr(w, "refresh_captures"):
            w.refresh_captures()

    def edit(self, img, saved=None):
        from .capview import CaptureEditor
        ed = CaptureEditor(self.c, img, saved)
        self.editors = [e for e in self.editors if _alive(e)] + [ed]
        ed.show()
        ed.raise_()
        ed.activateWindow()

    # ---- videos

    def toggle_recording(self):
        if self.recording():
            self.stop_recording()
        else:
            self.record()

    def record_gif(self):
        """Records an area as an animated GIF (no sound), for chats and bug reports."""
        if self.picker is not None or self.recording():
            return
        self._gif = True
        self.record()

    def record(self):
        if self.picker is not None or self.recording():
            return
        if ffmpeg_path() is None:
            self.c.tell(tr("Can't record"), tr("FFmpeg is missing, so recording isn't possible."))
            return
        self.picker = Picker(self._record_picked, "record")
        QTimer.singleShot(self._hide_corefold() or 10, self.picker.start)

    def _record_picked(self, img, rect):
        self.picker = None
        if rect is None:
            self._gif = False
            self._show_corefold()
            return
        from .capview import RecordFrame
        self.frame = RecordFrame(rect)
        self.frame.show()
        self._rect = rect
        self._count = 3 if self.settings.cap_countdown else 0
        self._countdown()

    def _countdown(self):
        if self.frame is None:
            return
        if self._count > 0:
            self.frame.count = self._count
            self.frame.update()
            self._count -= 1
            QTimer.singleShot(1000, self._countdown)
            return
        self.frame.count = 0
        self.frame.update()
        gif, self._gif = self._gif, False
        out = new_name(videos_folder(), ".mp4", "Recording")
        if gif:
            import tempfile
            out = Path(tempfile.mkdtemp(prefix="corefold-gif-")) / "recording.mp4"
            rec = Recorder(self._rect, out, int(self.settings.cap_fps), "", self.settings.cap_cursor, False)
        else:
            rec = Recorder(self._rect, out, int(self.settings.cap_fps), self.settings.cap_mic,
                           self.settings.cap_cursor, self.settings.cap_pc_sound)
        rec.gif = gif
        if not rec.start():
            self._end_frame()
            self._show_corefold()
            self.c.tell(tr("Can't record"), rec.error)
            return
        self.recorder = rec
        if rec.no_pc_sound:
            self.c.toast.notify(tr("Recording without the PC's sound"),
                                tr("Windows didn't let Corefold hear the speakers. The microphone still works."),
                                linger_ms=6000)
        from .capview import RecordBar
        self.bar = RecordBar(self._rect, self.c.theme)
        self.bar.stop.connect(self.stop_recording)
        self.bar.pause.connect(self.toggle_pause)
        self.bar.show()
        self.tick.start(500)
        self.c.update_tray_tooltip() if hasattr(self.c, "update_tray_tooltip") else None

    def _tick(self):
        rec = self.recorder
        if rec is None:
            self.tick.stop()
            return
        if not rec.running():  # FFmpeg stopped by itself (a microphone unplugged, the disk full)
            self.stop_recording()
            return
        if self.bar is not None:
            self.bar.set_seconds(rec.seconds())

    def toggle_pause(self):
        """Pauses the recording (what happens meanwhile isn't in the video), or carries on."""
        rec = self.recorder
        if rec is None:
            return
        if rec.paused:
            if not rec.resume():
                self.stop_recording()
                return
        else:
            rec.pause()
        if self.bar is not None:
            self.bar.set_paused(rec.paused)
        if self.frame is not None:
            self.frame.paused = rec.paused
            self.frame.update()

    def _end_frame(self):
        for w in (self.frame, self.bar):
            if w is not None:
                w.close()
                w.deleteLater()
        self.frame = self.bar = None

    def stop_recording(self, wait: bool = False):
        rec, self.recorder = self.recorder, None
        self.tick.stop()
        self._end_frame()
        self._show_corefold()
        if rec is None:
            return
        if wait:
            self._recorded(rec, rec.stop())
            return
        import threading

        from PySide6.QtCore import QObject as _Q, Signal as _S

        class _Done(_Q):
            done = _S(object, bool)
        sig = _Done()
        sig.done.connect(self._recorded)
        self._done_sig = sig
        threading.Thread(target=lambda: sig.done.emit(rec, rec.stop()), daemon=True,
                         name="corefold-recording").start()

    def _recorded(self, rec, ok: bool):
        if not ok:
            self.c.tell(tr("The recording couldn't be saved"), rec.error)
            return
        if getattr(rec, "gif", False):
            self._make_gif(rec.out)
            return
        self.c.toast.notify(tr("Recording saved"), rec.out.name, tr("Show in folder"),
                            lambda p=rec.out: _reveal(p), linger_ms=8000)
        w = self.c.window_if_open()
        if w is not None and hasattr(w, "refresh_captures"):
            w.refresh_captures()


    def _make_gif(self, mp4: Path):
        """Turns a GIF recording (an MP4 in a temporary folder) into the GIF, next to the other recordings."""
        import shutil
        import threading

        from PySide6.QtCore import QObject as _Q, Signal as _S

        from .converters import VIDEO_ARGS
        from .core import job_flags
        out = new_name(videos_folder(), ".gif", "Recording")

        class _Done(_Q):
            done = _S(bool)
        sig = _Done()
        self._gif_sig = sig

        def work():
            ok = False
            try:
                exe = ffmpeg_path()
                if exe is not None:
                    r = subprocess.run([str(exe), "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp4),
                                        *VIDEO_ARGS[".gif"], str(out)], capture_output=True, timeout=600,
                                       creationflags=job_flags())
                    ok = r.returncode == 0 and out.exists() and out.stat().st_size > 0
            except (OSError, subprocess.SubprocessError):
                log.warning("making the GIF failed", exc_info=True)
            shutil.rmtree(mp4.parent, ignore_errors=True)
            sig.done.emit(ok)

        def done(ok: bool):
            if not ok:
                out.unlink(missing_ok=True)
                self.c.tell(tr("The recording couldn't be saved"), tr("The GIF couldn't be made."))
                return
            self.c.toast.notify(tr("GIF saved"), out.name, tr("Show in folder"), lambda p=out: _reveal(p),
                                linger_ms=8000)
            w = self.c.window_if_open()
            if w is not None and hasattr(w, "refresh_captures"):
                w.refresh_captures()
        sig.done.connect(done)
        threading.Thread(target=work, daemon=True, name="corefold-gif").start()


def _reveal(path: Path):
    from .winutil import reveal_in_explorer
    reveal_in_explorer(path)


def _alive(widget) -> bool:
    try:
        from shiboken6 import isValid
        return isValid(widget)
    except ImportError:
        return True


def recent_captures(limit: int = 10) -> list[Path]:
    """The newest screenshots and recordings (in the folders Capture saves to)."""
    found: list[tuple[float, Path]] = []
    for folder, exts in ((screenshots_folder(), (".png", ".jpg")), (videos_folder(), (".mp4", ".gif"))):
        try:
            for e in os.scandir(folder):
                if e.is_file() and os.path.splitext(e.name)[1].lower() in exts:
                    found.append((e.stat().st_mtime, Path(e.path)))
        except OSError:
            continue
    found.sort(reverse=True)
    return [p for _, p in found[:limit]]
