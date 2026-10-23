"""Small animations: a page that rises into place, a value that glides. Each one is skipped when
animations are off, and none of them holds anything up (they run on the side)."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QVariantAnimation
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
