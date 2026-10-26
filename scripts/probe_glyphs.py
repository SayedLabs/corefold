"""TEMPORARY probe: how long Qt takes to draw each piece of the Updates page's rows the first time (Windows)."""
import sys
import time

sys.path.insert(0, ".")
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

app = QApplication([])
from corefold import i18n  # noqa: E402

i18n.set_language(sys.argv[1] if len(sys.argv) > 1 else "ar")
from corefold.notesview import when  # noqa: E402
from corefold.releasenotes import heads  # noqa: E402

warm = QLabel("كورفولد 1.14.1 Corefold")
warm.grab()
t = time.perf_counter()
date = when(heads()[0])
print(f"date text {time.perf_counter() - t:.3f} s: {ascii(date)}")
for text in ("كورفولد 1.14.0  ·  " + date, "◂  كورفولد", "▸  Corefold", "▾  Corefold", "·", "◂", "▸"):
    lab = QLabel(text)
    t = time.perf_counter()
    lab.grab()
    print(f"draw {time.perf_counter() - t:.3f} s: {ascii(text)}")
