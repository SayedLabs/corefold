"""Uses an older Corefold for a moment, the way a person would, so the next version can be started on
top of what it left behind (settings, history, the tools in use, the sidebar order).

Run with the older version's folder: python scripts/upgradeseed.py <old corefold folder> <data folder>
then: python scripts/sweeptest.py <out> --keep <data folder>"""
import os
import sys
import time
from pathlib import Path

OLD = Path(sys.argv[1]).resolve()
DATA = Path(sys.argv[2]).resolve()
os.environ["APPDATA"] = str(DATA / "roaming")
os.environ["LOCALAPPDATA"] = str(DATA / "local")
sys.path.insert(0, str(OLD))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from corefold import __version__  # noqa: E402
from corefold import settings as st  # noqa: E402
from corefold import tools as all_tools  # noqa: E402

s = st.Settings()
s.first_run = False
s.tour_done = True
s.check_updates = False
s.language = "ar"
s.appearance = "light"
ready = [t.id for t in all_tools.ready()]
s.known_tools = sorted(ready)
s.tools_off = sorted(ready[1::2])  # some tools in use, some not
s.save()

from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from corefold import i18n  # noqa: E402
i18n.set_language("ar")
qapp = QApplication(sys.argv)
qapp.setQuitOnLastWindowClosed(False)
from corefold import app as A  # noqa: E402

A.setup_logging()
c = A.Controller(qapp, QIcon(str(OLD / "assets" / "icon.png")))
c.show_window("Store")
end = time.time() + 3
while time.time() < end:
    qapp.processEvents()
    time.sleep(0.02)
c.history.record("png", [OLD / "README.md"], [OLD / "README.md"], 0, 1234)
c.history.save()
c.settings.save()
print(f"upgradeseed: Corefold {__version__} left its settings in {DATA}", flush=True)
from testexit import finish  # (Qt can crash in Windows' tidy-up at exit, after every check passed)
finish(0)
