"""TEMPORARY (audit run only, deleted before the PR): does Corefold itself crash when it quits? Starts it,
asks it to quit over its pipe (as the installer does), and shows its exit code. 3 times."""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from corefold import ipc  # noqa: E402

for n in range(3):
    proc = subprocess.Popen([sys.executable, str(ROOT / "main.py"), "--background"], cwd=ROOT)
    time.sleep(15)
    sent = ipc.send(b"quit")
    try:
        code = proc.wait(60)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = "didn't quit in 60 s"
    hexed = f" (0x{code & 0xFFFFFFFF:08X})" if isinstance(code, int) else ""
    print(f"== Corefold quit {n + 1}: asked {sent}, exit {code}{hexed}", flush=True)
