"""Times the archive window with a very big archive, on a real Windows PC.

Used by .github/workflows/screenshots.yml. Run: python scripts/stress.py <folder>
Prints one line per measurement and "stress: N problem(s)" (a problem = slower than it should be).
"""
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "stress").resolve()
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-stress-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from corefold import settings as st  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.check_updates = False
s.save()
qapp = QApplication(sys.argv)
from corefold import app as A  # noqa: E402
from corefold import archives as ar  # noqa: E402
from corefold.browser import ArchiveBrowser  # noqa: E402

c = A.Controller(qapp, QIcon())
lines, problems = [], []


def timed(what, fn, limit):
    start = time.perf_counter()
    result = fn()
    took = time.perf_counter() - start
    ok = took <= limit
    line = f"{'ok  ' if ok else 'SLOW'}  {took:7.2f} s  (limit {limit} s)  {what}"
    print(line, flush=True)
    lines.append(line)
    if not ok:
        problems.append(what)
    return result


FILES = 60_000
big = TMP / "big.zip"


def make():
    with zipfile.ZipFile(big, "w", zipfile.ZIP_STORED) as z:
        for i in range(FILES):
            z.writestr(f"project/module-{i // 1000:02d}/sub-{i // 100 % 10}/file-{i}.txt", "x")


timed(f"make a ZIP with {FILES:,} files (test setup)", make, 120)
entries = timed("list it (7-Zip)", lambda: ar.list_entries(big), 8)
small = TMP / "small.zip"
with zipfile.ZipFile(small, "w") as z:
    z.writestr("a.txt", "x")
ArchiveBrowser(c, small, "", ar.list_entries(small), QIcon()).deleteLater()  # Qt's one-time start-up costs
qapp.processEvents()
import cProfile, io, pstats  # noqa: E401,E402
first = cProfile.Profile()
b = timed("build the archive window", lambda: first.runcall(ArchiveBrowser, c, big, "", entries, QIcon()), 3)
out = io.StringIO()
pstats.Stats(first, stream=out).sort_stats("cumulative").print_stats(30)
(OUT / "stress-profile-first.txt").write_text(out.getvalue(), "utf-8")
b.resize(900, 600)
b.show()
timed("show it", qapp.processEvents, 3)
timed("sort by size (clicking the heading)", lambda: (b._sort_by(1), qapp.processEvents()), 4)
timed("sort by name again", lambda: (b._sort_by(0), qapp.processEvents()), 4)
timed("search for one name", lambda: (b._filter("file-59999"), qapp.processEvents()), 2)
timed("clear the search", lambda: (b._filter(""), qapp.processEvents()), 2)
b.tree.selectAll()
timed("work out the selected files (all)", b._selected_paths, 2)
b.tree.clearSelection()
b._fill("project")
module = next(b.tree.topLevelItem(0).child(i) for i in range(b.tree.topLevelItem(0).childCount())
              if b.tree.topLevelItem(0).child(i).text(0).strip("‪‬") == "module-07")
module.setSelected(True)
members = b._selected_paths()
folder = timed(f"take out a folder of {len([m for m in members if m.endswith('.txt')]):,} files for dragging",
               lambda: ar.extract_for_drag(big, members, ""), 15)
ar.remove_drag_folder(folder)
timed("reload after a change", lambda: b.reload(wait=True), 5)
prof = cProfile.Profile()
prof.runcall(b.reload, wait=True)
out = io.StringIO()
pstats.Stats(prof, stream=out).sort_stats("tottime").print_stats(25)
(OUT / "stress-profile.txt").write_text(out.getvalue(), "utf-8")
b.close()
qapp.processEvents()

# ---- the Converter with thousands of files dropped at once
from PIL import Image  # noqa: E402
many = TMP / "many"
many.mkdir()
for i in range(3000):
    (many / f"photo-{i}.jpg").write_bytes(b"")
Image.new("RGB", (64, 48), (200, 100, 50)).save(many / "photo-0.jpg")
for i in range(1, 3000):
    (many / f"photo-{i}.jpg").write_bytes((many / "photo-0.jpg").read_bytes())
files = sorted(many.iterdir())
w = c.window
w.show()
qapp.processEvents()
timed("drop 3,000 photos on the Converter (options shown)", lambda: (w.show_files(files), qapp.processEvents()), 5)
from corefold.converters import pick  # noqa: E402
timed("work out the wheel for 3,000 photos", lambda: pick(files, quick=True), 3)
w.hide()

(OUT / "stress.txt").write_text("\n".join(lines + [f"stress: {len(problems)} problem(s)"]), "utf-8")
print(f"stress: {len(problems)} problem(s)")
import faulthandler  # noqa: E402
faulthandler.disable()
os._exit(0)
