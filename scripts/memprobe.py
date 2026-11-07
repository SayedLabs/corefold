"""What each heavy part of Rakiza Core costs in memory on this PC, and what it leaves behind once it's let go,
each tried in a fresh Python of its own (Windows; nothing is written anywhere, no settings, no registry).

Run: python scripts/memprobe.py [drive or folder for Space, default C:\\]
Prints one line per part: private memory (what Windows keeps for the app) and threads, before, while held and
after it's let go (garbage collected, heaps compacted)."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROBE = r'''
import ctypes, gc, os, sys, time, threading
from ctypes import wintypes as wt
sys.path.insert(0, ROOT)

class PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]
k32 = ctypes.windll.kernel32
k32.GetCurrentProcess.restype = ctypes.c_void_p

def threads():
    TH32CS_SNAPTHREAD = 4
    class TE(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ThreadID", wt.DWORD),
                    ("th32OwnerProcessID", wt.DWORD), ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long),
                    ("dwFlags", wt.DWORD)]
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    te = TE(); te.dwSize = ctypes.sizeof(TE); n = 0; me = os.getpid()
    ok = k32.Thread32First(ctypes.c_void_p(snap), ctypes.byref(te))
    while ok:
        n += te.th32OwnerProcessID == me
        ok = k32.Thread32Next(ctypes.c_void_p(snap), ctypes.byref(te))
    k32.CloseHandle(ctypes.c_void_p(snap))
    return n

def mem():
    p = PMC(); p.cb = ctypes.sizeof(PMC)
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), wt.DWORD]
    ctypes.windll.psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(p), p.cb)
    return p.PrivateUsage / 2**20, p.PeakPagefileUsage / 2**20, p.WorkingSetSize / 2**20

def compact():
    gc.collect()
    k32.GetProcessHeaps.restype = wt.DWORD
    k32.GetProcessHeaps.argtypes = [wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    k32.HeapCompact.argtypes = [wt.HANDLE, wt.DWORD]
    heaps = (wt.HANDLE * 256)()
    for h in heaps[:min(k32.GetProcessHeaps(256, heaps), 256)]:
        k32.HeapCompact(h, 0)
    time.sleep(0.5)

def line(name, a, b, c, extra=""):
    print(f"{name:<34} before {a[0]:7.1f} MB/{a[3]:3d} thr | held {b[0]:7.1f} MB/{b[3]:3d} thr | "
          f"peak {b[1]:7.1f} MB | after {c[0]:7.1f} MB/{c[3]:3d} thr {extra}", flush=True)

def m():
    return (*mem(), threads())
'''

PARTS = {
    "python + import corefold.app": r'''
a = m()
import corefold.app
time.sleep(1)
b = m(); compact(); c = m()
line(NAME, a, b, c)
''',
    "import numpy": r'''
import corefold.app
a = m()
import numpy as np
time.sleep(2)
b = m(); compact(); c = m()
line(NAME, a, b, c, f"(OPENBLAS_NUM_THREADS={os.environ.get('OPENBLAS_NUM_THREADS', 'unset')})")
''',
    "import numpy, set inside (as main.py)": r'''
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import corefold.app
a = m()
import numpy as np
time.sleep(2)
b = m(); compact(); c = m()
line(NAME, a, b, c)
''',
    "numpy sort+sum 2M (Space-like)": r'''
import corefold.app
import numpy as np
time.sleep(1)
a = m()
x = np.random.default_rng(1).integers(0, 1 << 40, 2_000_000)
y = np.sort(x); z = x.sum(); w = np.argsort(x); d = np.dot(x[:1000].astype(float), x[:1000].astype(float))
time.sleep(1)
b = m(); del x, y, w; compact(); c = m()
line(NAME, a, b, c, f"(OPENBLAS_NUM_THREADS={os.environ.get('OPENBLAS_NUM_THREADS', 'unset')})")
''',
    "Space look at the drive": r'''
import corefold.app
import numpy
from pathlib import Path
from corefold import space
time.sleep(1)
compact(); a = m()
t = time.monotonic()
res = space.scan(Path(TARGET))
took = time.monotonic() - t
gc.collect(); b = m()
files = res.files
del res
compact(); c = m()
line(NAME, a, b, c, f"({files:,} files, {took:.0f} s)")
''',
    "12 MP photo open + convert": r'''
import corefold.app
from PIL import Image
im = Image.effect_noise((400, 300), 60); del im
compact(); a = m()
r = Image.effect_noise((4000, 3000), 60)
im = Image.merge("RGB", (r, r.rotate(180), r.transpose(Image.FLIP_LEFT_RIGHT)))
import io
buf = io.BytesIO(); im.save(buf, "WEBP", quality=80)
b = m(); del r, im, buf; compact(); c = m()
line(NAME, a, b, c)
''',
    "PDF engine (pypdfium2) loaded": r'''
import corefold.app
compact(); a = m()
import pypdfium2
b = m(); compact(); c = m()
line(NAME, a, b, c)
''',
}


def main():
    only = os.environ.get("MEMPROBE_ONLY")
    target = sys.argv[1] if len(sys.argv) > 1 else "C:\\"
    runs = [(name, {}) for name in PARTS]
    runs.insert(2, ("import numpy", {"OPENBLAS_NUM_THREADS": "1"}))
    runs.insert(4, ("numpy sort+sum 2M (Space-like)", {"OPENBLAS_NUM_THREADS": "1"}))
    print(f"{os.cpu_count()} logical CPUs, Python {sys.version.split()[0]}", flush=True)
    for name, env in runs:
        if only and only not in name:
            continue
        code = PROBE + f"\nNAME = {name!r}\nTARGET = {target!r}\n" + PARTS[name]
        e = dict(os.environ, **env)
        try:
            subprocess.run([sys.executable, "-c", code.replace("sys.path.insert(0, ROOT)",
                                                               f"sys.path.insert(0, {str(ROOT)!r})")],
                           env=e, timeout=900, check=False)
        except subprocess.TimeoutExpired:
            print(f"{name}: took too long", flush=True)


if __name__ == "__main__":
    main()
