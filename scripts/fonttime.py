"""How long the first use of a font takes in a fresh Corefold-like process (python scripts/fonttime.py <family>)."""
import os
import sys
import time

t0 = time.perf_counter()
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402
app = QApplication(sys.argv)
t1 = time.perf_counter()
fam = sys.argv[1] if len(sys.argv) > 1 else ""
if fam == "css":
    app.setStyleSheet('* { font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 10pt; }')
    w = QLabel("Hello")
    w.ensurePolished()
    h = w.fontMetrics().height()
elif fam == "families":
    h = len(QFontDatabase.families())
else:
    h = QFontMetrics(QFont(fam)).height() if fam else QFontMetrics(app.font()).height()
t2 = time.perf_counter()
w2 = QLabel("مرحبا")
w2.ensurePolished()
w2.fontMetrics().horizontalAdvance("مرحبا")
t3 = time.perf_counter()
fonts = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")
print(f"{fam or 'default'!r}: qt {t1 - t0:.2f} s, first font use {t2 - t1:.2f} s (h={h}), arabic {t3 - t2:.2f} s, "
      f"SegUIVar.ttf there: {os.path.exists(os.path.join(fonts, 'SegUIVar.ttf'))}, "
      f"fonts: {len(os.listdir(fonts)) if os.path.isdir(fonts) else '?'}")
