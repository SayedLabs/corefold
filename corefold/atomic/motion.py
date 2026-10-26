"""Small animations: a page that rises into place, a value that glides. Each one is skipped when
animations are off, and none of them holds anything up (they run on the side)."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QVariantAnimation
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from .tokens import MOTION


def _alive(w) -> bool:
    try:
        from shiboken6 import isValid
        return isValid(w)
    except ImportError:
        return True


def fade_in(w: QWidget, duration: int = MOTION["fast"]):
    """w fades in from nothing."""
    effect = QGraphicsOpacityEffect(w)
    effect.setOpacity(0.0)
    w.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", w)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.finished.connect(lambda: _alive(w) and w.setGraphicsEffect(None))
    anim.start(QPropertyAnimation.DeleteWhenStopped)
    return anim


def rise_in(w: QWidget, distance: int = 10, duration: int = MOTION["normal"]):
    """w fades in while it rises the last few pixels into place (a page being opened).
    Only for widgets that aren't placed by a layout of their own, or that a layout puts back."""
    fade_in(w, duration)
    end = w.pos()
    anim = QPropertyAnimation(w, b"pos", w)
    anim.setDuration(duration)
    anim.setStartValue(end + QPoint(0, distance))
    anim.setEndValue(end)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.start(QPropertyAnimation.DeleteWhenStopped)
    return anim


def glide(owner, start: float, end: float, on_value, duration: int = MOTION["fast"],
          curve: QEasingCurve.Type = QEasingCurve.OutCubic) -> QVariantAnimation:
    """A number that moves from start to end, calling on_value(v) each frame."""
    anim = QVariantAnimation(owner)
    anim.setDuration(duration)
    anim.setStartValue(float(start))
    anim.setEndValue(float(end))
    anim.setEasingCurve(curve)
    anim.valueChanged.connect(on_value)
    anim.start(QVariantAnimation.DeleteWhenStopped)
    return anim


class _Cover(QWidget):
    """A picture of how the window looked, laid over it while it changes underneath, then faded away."""

    def __init__(self, host: QWidget, picture):
        super().__init__(host)
        self.picture = picture
        self.opacity = 1.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setGeometry(host.rect())
        self.show()
        self.raise_()

    def set_opacity(self, v: float):
        self.opacity = v
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setOpacity(self.opacity)
        p.drawPixmap(self.rect(), self.picture)
        p.end()


def fade_from(host: QWidget, picture, duration: int = MOTION["normal"]):
    """The window shows `picture` (how it looked a moment ago) and lets it fade into how it looks now."""
    cover = _Cover(host, picture)

    def gone():
        if _alive(cover):
            cover.hide()
            cover.deleteLater()
    glide(cover, 1.0, 0.0, lambda v: _alive(cover) and cover.set_opacity(v), duration,
          QEasingCurve.InOutQuad).finished.connect(gone)
    return cover


def cross_fade(host: QWidget | None, change, duration: int = MOTION["normal"], animate: bool = True):
    """Does change() (a new look, a tool switched on: much is drawn again) with the window going softly from
    how it looked to how it looks now, instead of all of it jumping at once."""
    if host is None or not animate or not host.isVisible() or host.isMinimized():
        change()
        return
    picture = host.grab()
    try:
        change()
    finally:
        if _alive(host) and host.isVisible():
            fade_from(host, picture, duration)
