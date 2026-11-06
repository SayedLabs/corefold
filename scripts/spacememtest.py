"""How much memory a Space look at a whole drive takes and keeps (Windows): python scripts/spacememtest.py <code
folder> <drive or folder>. Prints the private memory (what Windows has to keep for the app) before the look,
after it, at its peak, and after the map is let go."""
import ctypes
import gc
import sys
import time
from ctypes import wintypes as wt
from pathlib import Path

CODE = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(CODE))


class PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


def mem():
    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc),
                                             pmc.cb)
    return pmc.PrivateUsage / 2**20, pmc.PeakPagefileUsage / 2**20, pmc.WorkingSetSize / 2**20


import numpy  # noqa: E402,F401  (the app has it loaded already)
from corefold import space  # noqa: E402

gc.disable()  # (as in the app: collected on the window's thread)
before = mem()
t = time.monotonic()
res = space.scan(Path(sys.argv[2]))
took = time.monotonic() - t
gc.collect()
after = mem()
files, folders = res.files, res.folders
del res
gc.collect()
dropped = mem()
ctypes.windll.kernel32.SetProcessWorkingSetSize(ctypes.windll.kernel32.GetCurrentProcess(),
                                                ctypes.c_size_t(-1), ctypes.c_size_t(-1))
print(f"{CODE.name}: {files:,} files, {folders:,} folders in {took:.1f} s | private before {before[0]:.0f} MB, "
      f"with the map {after[0]:.0f} MB, peak {after[1]:.0f} MB, after letting go {dropped[0]:.0f} MB")
