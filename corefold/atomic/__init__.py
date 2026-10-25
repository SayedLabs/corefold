"""Atomic: the Atomic Works design system for Qt (PySide6) desktop apps.

Everything an app needs to look like the family: colours, type, spacing, corners, depth and motion
(tokens.py), the base stylesheet for Qt's own widgets (qss.py), shared building blocks like
switches, setting rows, groups, glow panels and the command palette (widgets.py, palette.py), and
small animation helpers (motion.py).

This package imports nothing from the app around it, so a new app can copy the folder as it is:

    from corefold.atomic import tokens, qss
    pal = tokens.palette(dark=True, accent=QColor("#3fb6a2"))
    app.setStyleSheet(qss.base(pal) + my_own_rules)

Names that pages use (objectName on a widget) are listed at the top of qss.py.
"""
from . import motion, qss, tokens  # noqa: F401
