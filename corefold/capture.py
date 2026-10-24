"""Corefold Capture: pictures of the screen.

A shortcut freezes the screen, you drag over what you want (or click a window, or press Enter for
the whole screen), and the picture opens in a small editor to mark it up, copy it or save it.
Nothing is uploaded anywhere. (Recording videos of the screen was taken out in 1.8.1, to come back
later as an update of its own.)"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from .i18n import tr

log = logging.getLogger("corefold")


def screenshots_folder() -> Path:
    """Where screenshots are saved: the folder picked in Settings → Capture, or Pictures\\Screenshots."""
    return _chosen("cap_folder") or default_screenshots_folder()


def _chosen(key: str) -> Path | None:
    from . import settings
    s = settings.ACTIVE
    folder = getattr(s, key, "") if s is not None else ""
    return Path(folder) if folder else None


def default_screenshots_folder() -> Path:
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


def new_name(folder: Path, ext: str, prefix: str = "Screenshot") -> Path:
    stamp = time.strftime("%Y-%m-%d %H%M%S")
    p = folder / f"{prefix} {stamp}{ext}"
    n = 2
    while p.exists() or p in _writing:
        p = folder / f"{prefix} {stamp} ({n}){ext}"
        n += 1
    return p


class _Written(QObject):
    done = Signal(object, bool, object)   # (the file, whether it was saved, what to call next or None)


_writing: set[Path] = set()   # pictures being saved right now (their names are taken)
_written: _Written | None = None


def write_later(image, path: Path, then=None):
    """Saves a picture on a thread (a big PNG takes half a second, and the screen shouldn't wait),
    then calls then(ok) back here."""
    global _written
    if _written is None:
        _written = _Written()
        _written.done.connect(_after_writing, Qt.ConnectionType.QueuedConnection)
    _writing.add(path)
    fmt = "JPG" if path.suffix.lower() in (".jpg", ".jpeg") else "PNG"
    signal = _written.done

    def work():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            ok = image.save(str(path), fmt, 92 if fmt == "JPG" else -1)
        except OSError:
            ok = False
        if not ok:
            log.warning("couldn't save %s", path)
        signal.emit(path, ok, then)
    threading.Thread(target=work, daemon=True, name="corefold-save-picture").start()


def _after_writing(path: Path, ok: bool, then):
    _writing.discard(path)
    if then is not None:
        then(ok)


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
    """Keeps this window out of screenshots (the timer's countdown)."""
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

    def __init__(self, screen, shot: QPixmap, windows: list[QRect], hint: str = ""):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.hint = hint
        self.screen_ = screen
        self.shot = shot
        self.geo = screen.geometry()
        self.windows = [w.intersected(self.geo).translated(-self.geo.topLeft()) for w in windows
                        if w.intersects(self.geo)]
        self.start: QPoint | None = None
        self.end: QPoint | None = None
        self.hover: QRect | None = None
        self.mouse: QPoint | None = None   # where the mouse is (the magnifier follows it)
        self._pixels = None                # the frozen screen as an image, for the magnifier's colour
        self._drawn = QRect()              # where the magnifier was last drawn
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
        self._follow(self.mapFromGlobal(QCursor.pos()))

    def _hover_at(self, pos: QPoint):
        under = next((w for w in self.windows if w.contains(pos)), None)
        if under != self.hover:
            self.hover = under
            self.update()

    # ---- the magnifier: the pixels around the mouse, big, with where it is and the colour there
    MAG, ZOOM = 120, 8

    def _mag_rect(self, pos: QPoint) -> QRect:
        size, wide = self.MAG + 2, self.MAG + 52   # (the line under it is wider than the picture)
        x, y = pos.x() + 22, pos.y() + 22
        if x + wide > self.width():
            x = pos.x() - 22 - size
        if y + size + 26 > self.height():
            y = pos.y() - 22 - size - 26
        return QRect(x, y, wide, size + 26)

    def _follow(self, pos: QPoint):
        """The mouse moved: only the magnifier's old and new places are drawn again (a whole screen each
        time would make it lag on a big monitor)."""
        self.mouse = pos
        new = self._mag_rect(pos)
        self.update(self._drawn.united(new).adjusted(-2, -2, 2, 2))
        self._drawn = new

    def _paint_magnifier(self, p: QPainter):
        if self.mouse is None or not self.rect().contains(self.mouse):
            return
        ratio = self.shot.devicePixelRatio() or 1.0
        if self._pixels is None:
            self._pixels = self.shot.toImage()
        cx, cy = round(self.mouse.x() * ratio), round(self.mouse.y() * ratio)
        n = self.MAG // self.ZOOM  # pixels shown across
        src = QRect(cx - n // 2, cy - n // 2, n, n)
        box = self._mag_rect(self.mouse)
        view = QRect(box.x() + 1, box.y() + 1, self.MAG, self.MAG)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(view), 10, 10)
        p.save()
        p.setClipPath(clip)
        p.fillRect(view, QColor(20, 22, 24))
        p.drawImage(QRectF(view), self._pixels, QRectF(src))
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        for k in range(1, n):  # (the pixel grid)
            p.drawLine(view.x() + k * self.ZOOM, view.y(), view.x() + k * self.ZOOM, view.bottom())
            p.drawLine(view.x(), view.y() + k * self.ZOOM, view.right(), view.y() + k * self.ZOOM)
        mid = QRect(view.x() + (n // 2) * self.ZOOM, view.y() + (n // 2) * self.ZOOM, self.ZOOM, self.ZOOM)
        p.setPen(QPen(QColor("#3cc7b2"), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(mid)
        p.restore()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(view).adjusted(-0.5, -0.5, 0.5, 0.5), 10, 10)
        color = self._pixels.pixelColor(min(max(cx, 0), self._pixels.width() - 1),
                                        min(max(cy, 0), self._pixels.height() - 1))
        label = QRect(view.x(), view.bottom() + 5, self.MAG + 48, 21)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 22, 24, 225))
        p.drawRoundedRect(label, 6, 6)
        p.setBrush(color)
        p.setPen(QPen(QColor(255, 255, 255, 120), 1))
        p.drawRoundedRect(QRect(label.x() + 6, label.y() + 5, 11, 11), 3, 3)
        f = QFont(self.font())
        f.setPointSizeF(8)
        p.setFont(f)
        p.setPen(QColor("#fff"))
        p.drawText(label.adjusted(22, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft,
                   f"{cx}, {cy}   {color.name().upper()}")

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
        self._paint_magnifier(p)
        if self.start is None:
            hint = self.hint or tr("Drag over what you want, or click a window. Enter: the whole screen. Esc: cancel.")
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

    def leaveEvent(self, e):  # (on to another monitor: the magnifier goes there)
        super().leaveEvent(e)
        self.mouse = None
        self.update(self._drawn.adjusted(-2, -2, 2, 2))

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
            old = self.selection() or QRect(self.start, self.end).normalized()
            self.end = pos
            now = self.selection() or QRect(self.start, self.end).normalized()
            self.update(old.united(now).adjusted(-40, -40, 40, 40))  # (with its size label)
        else:
            self._hover_at(pos)
        self._follow(pos)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self.start is None:
            return
        self.end = e.position().toPoint()
        sel = self.selection()
        if sel is None:  # a click: the window under it, or the whole screen
            sel = self.hover or self.rect()
        self._finish(sel)

    def keyPressEvent(self, e):
        moves = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
        if e.key() in moves:  # the arrow keys move the mouse one pixel (Shift: ten), to pick exactly
            step = 10 if e.modifiers() & Qt.ShiftModifier else 1
            dx, dy = moves[e.key()]
            QCursor.setPos(QCursor.pos() + QPoint(dx * step, dy * step))
            return
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

    def __init__(self, on_picked, hint: str = ""):
        self.on_picked = on_picked
        self.hint = hint
        self.snips: list[Snip] = []
        self.answered = False

    def start(self):
        screens = QGuiApplication.screens()
        shots = [(s, s.grabWindow(0)) for s in screens]
        windows = visible_windows()
        for screen, shot in shots:
            snip = Snip(screen, shot, windows, self.hint)
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


# ----------------------------------------------------------------------------- the tool

class CaptureTool(QObject):
    """Corefold Capture while it's in use: its shortcut, and what happens after a screenshot."""

    def __init__(self, controller):
        super().__init__()
        from .hotkey import Hotkey
        self.c = controller
        self.picker: Picker | None = None
        self.editors: list = []
        self.shot_key = Hotkey()
        self.shot_key.pressed.connect(self.screenshot, Qt.ConnectionType.QueuedConnection)
        self.text_key = Hotkey()
        self.text_key.pressed.connect(self.copy_text, Qt.ConnectionType.QueuedConnection)
        self._reader = None
        self._hidden = None
        self._later = None     # the countdown before a screenshot with a timer
        self.apply_settings()

    @property
    def settings(self):
        return self.c.settings

    def apply_settings(self) -> bool:
        self.text_key.set(self.settings.cap_text_shortcut)
        return self.shot_key.set(self.settings.cap_shortcut)

    def stop(self):
        if self._later is not None:
            self._later.cancel()
            self._later = None
        self.shot_key.stop()
        self.text_key.stop()

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
        if self.picker is not None:
            return
        self.picker = Picker(self._picked)
        QTimer.singleShot(self._hide_corefold() or 10, self.picker.start)

    def copy_text(self):
        """Copies the text in a part of the screen, straight away (no editor): drag over it, it's read with
        Windows' own OCR, and it's on the clipboard."""
        if self.picker is not None:
            return
        self.picker = Picker(self._text_picked, tr("Drag over the text to copy. Esc: cancel."))
        QTimer.singleShot(self._hide_corefold() or 10, self.picker.start)

    def _text_picked(self, img, rect):
        self.picker = None
        self._show_corefold()
        if img is None:
            return
        from .capview import _Reader
        from .extras import ocr_available
        if not ocr_available():
            self.c.tell(tr("Can't read text here"), tr("Reading text needs Windows 10 or 11"))
            return
        reader = self._reader = _Reader(self)
        reader.done.connect(self._text_read)
        reader.start(img)

    def _text_read(self, lines, error: str, missing: bool):
        self._reader = None
        if error:
            self.c.tell(tr("Can't read text here"), error)
            return
        from .extras import lines_text
        text = lines_text(lines or []).strip()
        if not text:
            self.c.toast.notify(tr("No text found"), tr("Try a bigger part of the screen."), linger_ms=4000)
            return
        QGuiApplication.clipboard().setText(text)
        import re
        preview = re.sub(r"\s+", " ", text)
        preview = preview if len(preview) <= 80 else preview[:77] + "..."
        if missing:
            from .capview import add_arabic
            self.c.toast.notify(tr("Copied the text"), preview, tr("Read Arabic too…"),
                                lambda: add_arabic(self.c, None), linger_ms=8000)
        else:
            self.c.toast.notify(tr("Copied the text"), preview, linger_ms=4000)

    def screenshot_later(self, seconds: int | None = None):
        """A screenshot after a few seconds (time to open a menu, or hover over something)."""
        if self.picker is not None:
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
            write_later(img.copy(), saved, self._shot_saved)
        if self.settings.cap_after == "edit":
            self.edit(img, saved)
        else:
            self.c.toast.notify(tr("Screenshot copied"), tr("Paste it anywhere with Ctrl+V.") if saved is None else
                                tr("Paste it anywhere with Ctrl+V. Saved as {name}.", name=saved.name),
                                tr("Mark it up"), lambda i=img, p=saved: self.edit(i, p), linger_ms=6000)

    def _shot_saved(self, ok: bool):
        if not ok:
            self.c.tell(tr("Couldn't save"), str(screenshots_folder()))
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


def _alive(widget) -> bool:
    try:
        from shiboken6 import isValid
        return isValid(widget)
    except ImportError:
        return True


def recent_captures(limit: int = 10) -> list[Path]:
    """The newest screenshots (in the folder Capture saves to)."""
    found: list[tuple[float, Path]] = []
    try:
        for e in os.scandir(screenshots_folder()):
            if e.is_file() and os.path.splitext(e.name)[1].lower() in (".png", ".jpg"):
                found.append((e.stat().st_mtime, Path(e.path)))
    except OSError:
        pass
    found.sort(reverse=True)
    return [p for _, p in found[:limit]]
