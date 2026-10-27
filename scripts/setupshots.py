"""TEMPORARY (pre-launch look, removed before merging): pictures of Setup in a language.
Run: python scripts/setupshots.py <setup.exe> <out> <lang>"""
import subprocess
import sys
import time
from pathlib import Path

from PIL import ImageGrab
from pywinauto import keyboard

setup, out, lang = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
out.mkdir(parents=True, exist_ok=True)
p = subprocess.Popen([str(setup), f"/LANG={lang}"])
time.sleep(12)
ImageGrab.grab().save(out / f"setup-{lang}-1-options.png")
keyboard.send_keys("{ENTER}")
for _ in range(120):
    time.sleep(2)
    shot = ImageGrab.grab()
    if p.poll() is not None:
        break
    if _ >= 20:
        break
ImageGrab.grab().save(out / f"setup-{lang}-2-done.png")
keyboard.send_keys("{SPACE}")  # (untick "Open Corefold now")
time.sleep(1)
ImageGrab.grab().save(out / f"setup-{lang}-3-unticked.png")
keyboard.send_keys("{ENTER}")
time.sleep(3)
p.kill() if p.poll() is None else None
print("setupshots", lang, "done")

# taking it away again: the question about the settings is in the same language
import os  # noqa: E402

subprocess.run(["taskkill", "/f", "/im", "Corefold.exe"], capture_output=True)
unins = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Corefold" / "unins000.exe"
if unins.exists():
    u = subprocess.Popen([str(unins)])
    time.sleep(6)
    ImageGrab.grab().save(out / f"setup-{lang}-4-remove.png")
    keyboard.send_keys("{ENTER}")  # Yes, remove it
    time.sleep(10)
    ImageGrab.grab().save(out / f"setup-{lang}-5-settings-question.png")
    keyboard.send_keys("{ENTER}")  # No (the default): keep them
    time.sleep(6)
    ImageGrab.grab().save(out / f"setup-{lang}-6-removed.png")
    keyboard.send_keys("{ENTER}")
    time.sleep(2)
    if u.poll() is None:
        u.kill()
else:
    print("no uninstaller at", unins)
