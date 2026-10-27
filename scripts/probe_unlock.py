"""Temporary probe (removed before merging): what Unlock sees on a real Windows PC."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from corefold import unlock  # noqa: E402

base = Path(tempfile.mkdtemp(prefix="probe-"))
held = base / "held-folder"
(held / "sub").mkdir(parents=True)
(held / "sub" / "a.txt").write_text("x")
HOLD_DIR = r'''
import ctypes, sys, time
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.CreateFileW.restype = ctypes.c_void_p
h = k.CreateFileW(sys.argv[1], 0x80000000, 0, None, 3, 0x02000000, None)
print("handle", h, ctypes.get_last_error(), flush=True)
time.sleep(60)
'''
child = subprocess.Popen([sys.executable, "-c", HOLD_DIR, str(held / "sub")], cwd=str(base))
time.sleep(2)
found = unlock.find([held])
print("folder handle (no share): found", [(p.name, p.pid) for p in found.programs], "child", child.pid)
try:
    os.rename(held, base / "renamed")
    print("rename worked")
    os.rename(base / "renamed", held)
except OSError as e:
    print("rename refused:", e)
child.kill()
child.wait()

ex = subprocess.Popen([os.path.join(os.environ["SystemRoot"], "explorer.exe"), str(held)])
time.sleep(5)
found = unlock.find([held])
print("explorer window: found", [(p.name, p.pid, p.explorer) for p in found.programs])
try:
    os.rename(held, base / "renamed")
    print("rename with explorer window worked")
except OSError as e:
    print("rename with explorer window refused:", e)

print("too_wide C:", unlock.too_wide("C:\\"), "Windows", unlock.too_wide(os.environ["SystemRoot"]),
      "home", unlock.too_wide(Path.home()), "temp folder", unlock.too_wide(base))
