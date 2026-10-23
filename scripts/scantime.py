"""How fast Space reads a folder from a cold disk with N helpers: python scripts/scantime.py <folder> <workers> <seconds>"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from corefold import space  # noqa: E402

root, workers, seconds = Path(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
stop = threading.Event()
threading.Timer(seconds, stop.set).start()
r = space.scan(root, stop, workers=workers)
print(f"scan {root} with {workers} helpers: {r.files:,} files, {r.folders:,} folders in {r.seconds:.1f} s "
      f"= {r.files / max(r.seconds, 0.1):,.0f} files a second{' (stopped)' if r.cancelled else ''}")
