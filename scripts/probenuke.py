"""Temporary: which delete flags make Windows ask before deleting for good (delete before merge)."""
import ctypes, os, sys, threading, time, winreg
from ctypes import wintypes as wt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from corefold import uninstall
d = Path(os.environ.get("SystemDrive", "C:") + "\\") / "nukeprobe"
d.mkdir(exist_ok=True)
buf = ctypes.create_unicode_buffer(64)
ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(str(d)[:3], buf, 64)
guid = buf.value[buf.value.find("{"):buf.value.find("}") + 1]
key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume\{guid}")
user32 = ctypes.windll.user32
def windows():
    found = []
    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def each(h, _):
        pid = wt.DWORD(); user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value == os.getpid() and user32.IsWindowVisible(h):
            t = ctypes.create_unicode_buffer(200); user32.GetWindowTextW(h, t, 200)
            c = ctypes.create_unicode_buffer(200); user32.GetClassNameW(h, c, 200)
            found.append((h, t.value, c.value))
        return True
    user32.EnumWindows(proc(each), 0)
    return found
def attempt(flags, size, label):
    f = d / f"f{flags:x}-{size}.bin"
    f.write_bytes(b"x" * size)
    op = uninstall.SHFILEOPSTRUCTW(); op.wFunc = 3; op.pFrom = str(f) + "\0"; op.fFlags = flags
    res = {}
    t = threading.Thread(target=lambda: res.setdefault("r", ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))), daemon=True)
    t.start()
    seen = []
    end = time.monotonic() + 6
    while t.is_alive() and time.monotonic() < end:
        w = windows()
        if w:
            seen = w
            for h, *_ in w: user32.PostMessageW(h, 0x0010, 0, 0)
        time.sleep(0.2)
    t.join(5)
    print(f"{label:28} flags={flags:#06x} -> ret={res.get('r')} aborted={op.fAnyOperationsAborted} exists={f.exists()} dialog={seen}", flush=True)
BASE = 0x04 | 0x10 | 0x40 | 0x400
for scen in ("nuke", "small"):
    if scen == "nuke":
        winreg.SetValueEx(key, "NukeOnDelete", 0, winreg.REG_DWORD, 1)
    else:
        winreg.SetValueEx(key, "NukeOnDelete", 0, winreg.REG_DWORD, 0)
        winreg.SetValueEx(key, "MaxCapacity", 0, winreg.REG_DWORD, 1)  # 1 MB
    for flags in (BASE, BASE | 0x4000, 0x10 | 0x40 | 0x4000, 0x04 | 0x10 | 0x40 | 0x4000, 0x40 | 0x4000, 0x40 | 0x4000 | 0x400):
        attempt(flags, 5 * 2**20, scen)
winreg.SetValueEx(key, "NukeOnDelete", 0, winreg.REG_DWORD, 0)
winreg.DeleteValue(key, "MaxCapacity")
