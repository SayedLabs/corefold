"""Where a running Rakiza Core's memory is (Windows, read only): walks the process's address space and adds up
what's committed by kind (private, the program's own files, mapped), groups the private blocks by size (Python's
1 MB arenas, big blocks set aside in one go...), lists the biggest private blocks, and says which module each
thread started in.

Run: python scripts/memmap.py [pid]   (default: the first RakizaCore.exe running)"""
import ctypes
import subprocess
import sys
from collections import Counter, defaultdict
from ctypes import wintypes as wt

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

PROCESS_QUERY_INFORMATION, PROCESS_VM_READ = 0x400, 0x10
MEM_COMMIT, MEM_PRIVATE, MEM_IMAGE, MEM_MAPPED = 0x1000, 0x20000, 0x1000000, 0x40000
PAGE_GUARD = 0x100


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wt.DWORD), ("PartitionId", wt.WORD), ("RegionSize", ctypes.c_size_t),
                ("State", wt.DWORD), ("Protect", wt.DWORD), ("Type", wt.DWORD)]


k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
psapi.EnumProcessModulesEx.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p), wt.DWORD,
                                       ctypes.POINTER(wt.DWORD), wt.DWORD]
psapi.GetModuleBaseNameW.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.LPWSTR, wt.DWORD]


class MODINFO(ctypes.Structure):
    _fields_ = [("lpBaseOfDll", ctypes.c_void_p), ("SizeOfImage", wt.DWORD), ("EntryPoint", ctypes.c_void_p)]


psapi.GetModuleInformation.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MODINFO), wt.DWORD]


def find_pid() -> int:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq RakizaCore.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[1].isdigit():
            return int(parts[1])
    sys.exit("RakizaCore.exe isn't running")


def modules(h) -> list[tuple[int, int, str]]:
    arr = (ctypes.c_void_p * 2048)()
    need = wt.DWORD()
    psapi.EnumProcessModulesEx(h, arr, ctypes.sizeof(arr), ctypes.byref(need), 3)
    out = []
    for base in arr[:need.value // ctypes.sizeof(ctypes.c_void_p)]:
        name = ctypes.create_unicode_buffer(260)
        psapi.GetModuleBaseNameW(h, base, name, 260)
        mi = MODINFO()
        psapi.GetModuleInformation(h, base, ctypes.byref(mi), ctypes.sizeof(mi))
        out.append((mi.lpBaseOfDll or 0, mi.SizeOfImage, name.value))
    return sorted(out)


def module_at(mods, addr) -> str:
    for base, size, name in mods:
        if base <= addr < base + size:
            return name
    return "?"


def thread_starts(pid, mods) -> Counter:
    TH32CS_SNAPTHREAD = 4

    class TE(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ThreadID", wt.DWORD),
                    ("th32OwnerProcessID", wt.DWORD), ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long),
                    ("dwFlags", wt.DWORD)]
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    k32.OpenThread.restype = wt.HANDLE
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    te = TE()
    te.dwSize = ctypes.sizeof(TE)
    starts = Counter()
    ok = k32.Thread32First(snap, ctypes.byref(te))
    while ok:
        if te.th32OwnerProcessID == pid:
            th = k32.OpenThread(0x40, False, te.th32ThreadID)  # THREAD_QUERY_INFORMATION
            addr = ctypes.c_void_p()
            if th:
                ntdll.NtQueryInformationThread(th, 9, ctypes.byref(addr), ctypes.sizeof(addr), None)
                k32.CloseHandle(th)
            starts[module_at(mods, addr.value or 0)] += 1
        ok = k32.Thread32Next(snap, ctypes.byref(te))
    k32.CloseHandle(snap)
    return starts


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else find_pid()
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        sys.exit(f"can't open process {pid}: {ctypes.get_last_error()}")
    mods = modules(h)
    kinds = Counter()
    blocks = defaultdict(int)  # allocation base -> committed private bytes
    addr = 0
    mbi = MBI()
    while k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        if mbi.State == MEM_COMMIT:
            kind = {MEM_PRIVATE: "private", MEM_IMAGE: "image (program files)", MEM_MAPPED: "mapped"}.get(mbi.Type, "?")
            kinds[kind] += mbi.RegionSize
            if mbi.Type == MEM_PRIVATE:
                blocks[mbi.AllocationBase or 0] += mbi.RegionSize
        addr = (mbi.BaseAddress or 0) + mbi.RegionSize
        if addr >= 1 << 47:
            break
    mb = 2 ** 20
    print(f"process {pid}: committed by kind")
    for k, v in kinds.most_common():
        print(f"  {k:<24} {v / mb:8.1f} MB")
    buckets = Counter()
    counts = Counter()
    for size in blocks.values():
        b = ("< 256 KB" if size < 256 << 10 else "256 KB - 1 MB" if size <= 1 << 20 else "1 - 4 MB" if size <= 4 << 20
             else "4 - 16 MB" if size <= 16 << 20 else "16 - 64 MB" if size <= 64 << 20 else "> 64 MB")
        buckets[b] += size
        counts[b] += 1
    print("private, by block size (committed)")
    for b in ("< 256 KB", "256 KB - 1 MB", "1 - 4 MB", "4 - 16 MB", "16 - 64 MB", "> 64 MB"):
        print(f"  {b:<14} {buckets[b] / mb:8.1f} MB in {counts[b]} blocks")
    print("biggest private blocks")
    for base, size in sorted(blocks.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  {base:#014x} {size / mb:8.1f} MB")
    print("threads, by the module they started in")
    for name, n in thread_starts(pid, mods).most_common():
        print(f"  {n:3d} {name}")


if __name__ == "__main__":
    main()
