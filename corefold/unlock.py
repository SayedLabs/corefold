"""Corefold Unlock: which programs have a file or folder open, and closing them.

Windows' own Restart Manager says which programs are using a file (the same thing installers use to
ask "close these programs first"). Closing first asks the program nicely (like clicking its X, so it
can ask to save your work); only if it's still running afterwards can it be forced to close.

Nothing here imports Qt. Everything is slow-ish (it asks Windows about every file in a folder), so the
page calls it off the window's thread."""
from __future__ import annotations

import ctypes
import functools
import logging
import os
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("corefold")

MAX_FILES = 5000           # a folder's files asked about (a whole drive would take minutes)
POLITE_WAIT_S = 4.0        # how long a program gets to close itself
ERROR_MORE_DATA = 234
CCH_RM_SESSION_KEY = 32
CCH_RM_MAX_APP_NAME = 255
CCH_RM_MAX_SVC_NAME = 63
# RM_APP_TYPE
RM_MAIN_WINDOW, RM_OTHER_WINDOW, RM_SERVICE, RM_EXPLORER, RM_CONSOLE, RM_CRITICAL = 1, 2, 3, 4, 5, 1000
SYNCHRONIZE, QUERY_LIMITED, TERMINATE = 0x100000, 0x1000, 0x1
WM_CLOSE = 0x0010
GW_OWNER = 4
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
# parts of Windows that are never offered to be closed (closing them stops the PC working)
CRITICAL = {"system", "registry", "secure system", "memory compression", "idle", "smss.exe", "csrss.exe",
            "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "lsaiso.exe", "svchost.exe", "dwm.exe",
            "fontdrvhost.exe", "sihost.exe", "spoolsv.exe", "msmpeng.exe", "searchindexer.exe", "audiodg.exe"}


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]


class RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", ctypes.c_uint32), ("ProcessStartTime", FILETIME)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32), ("th32ProcessID", ctypes.c_uint32),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_uint32),
                ("cntThreads", ctypes.c_uint32), ("th32ParentProcessID", ctypes.c_uint32),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_wchar * 260)]


class RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [("Process", RM_UNIQUE_PROCESS),
                ("strAppName", ctypes.c_wchar * (CCH_RM_MAX_APP_NAME + 1)),
                ("strServiceShortName", ctypes.c_wchar * (CCH_RM_MAX_SVC_NAME + 1)),
                ("ApplicationType", ctypes.c_int),
                ("AppStatus", ctypes.c_uint32),
                ("TSSessionId", ctypes.c_uint32),
                ("bRestartable", ctypes.c_int)]


_ENUM_PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p) if sys.platform == "win32" else None


@functools.cache
def _dll(name: str):
    """A private copy of a Windows library, with its argument types (so handles aren't cut to 32 bits)."""
    lib = ctypes.WinDLL(name, use_last_error=True)
    H, U32, P, I = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int
    table = {
        "rstrtmgr": (("RmStartSession", (P, U32, ctypes.c_wchar_p), U32),
                     ("RmRegisterResources", (U32, U32, P, U32, P, U32, P), U32),
                     ("RmGetList", (U32, P, P, P, P), U32),
                     ("RmEndSession", (U32,), U32)),
        "kernel32": (("OpenProcess", (U32, I, U32), H), ("CloseHandle", (H,), I),
                     ("CreateToolhelp32Snapshot", (U32, U32), H), ("Process32FirstW", (H, P), I),
                     ("Process32NextW", (H, P), I),
                     ("QueryFullProcessImageNameW", (H, U32, ctypes.c_wchar_p, P), I),
                     ("GetProcessTimes", (H, P, P, P, P), I), ("TerminateProcess", (H, U32), I),
                     ("WaitForSingleObject", (H, U32), U32)),
        "user32": (("EnumWindows", (_ENUM_PROC, P), I), ("GetWindowThreadProcessId", (H, P), U32),
                   ("IsWindowVisible", (H,), I), ("GetWindow", (H, U32), H),
                   ("GetClassNameW", (H, ctypes.c_wchar_p, I), I), ("PostMessageW", (H, U32, P, P), I)),
        "shell32": (("IsUserAnAdmin", (), I),),
    }
    for fn, args, res in table.get(name, ()):
        getattr(lib, fn).argtypes, getattr(lib, fn).restype = args, res
    return lib


@dataclass
class Program:
    """A program that has the file (or something in the folder) open."""
    pid: int
    name: str                  # its own name for itself ("Notepad"), or its program file's name
    exe: str = ""              # its program file, when Windows says (empty for ones run as administrator)
    started: int = 0           # when it started (so a new program that got the same id is never closed)
    kind: int = 0              # what Restart Manager says it is (RM_APP_TYPE)
    service: str = ""          # a Windows service's short name
    session: int = 0
    critical: bool = False     # part of Windows: never offered to close
    explorer: bool = False     # File Explorer: closing it means restarting it
    blocked: bool = False      # runs as administrator (or as another user): Corefold can't close it
    own: bool = False          # Corefold itself

    @property
    def closable(self) -> bool:
        return not (self.critical or self.blocked or self.own or self.service)


@dataclass
class Found:
    paths: list
    programs: list = field(default_factory=list)
    files: int = 0             # how many files were asked about
    capped: bool = False       # a folder had more files than MAX_FILES (only the first ones were asked about)


def is_admin() -> bool:
    try:
        return bool(_dll("shell32").IsUserAnAdmin())
    except (OSError, AttributeError):
        return False


# ----------------------------------------------------------------------------- what's using it

def files_of(paths: list[Path], limit: int = MAX_FILES) -> tuple[list[str], bool]:
    """The files to ask about: the files themselves, and the files inside folders (not following links
    or junctions, which lead somewhere else). True as well if there were more than the limit."""
    out: list[str] = []
    for p in paths:
        p = Path(p)
        if not p.is_dir():
            out.append(str(p))
            if len(out) >= limit:
                return out, True
            continue
        stack = [str(p)]
        while stack:
            folder = stack.pop()
            try:
                with os.scandir(folder) as it:
                    for e in it:
                        try:
                            st = e.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT or e.is_symlink():
                            continue
                        if stat.S_ISDIR(st.st_mode):
                            stack.append(e.path)
                        else:
                            out.append(e.path)
                            if len(out) >= limit:
                                return out, True
            except OSError:
                continue
    return out, False


def _rm_list(files: list[str]) -> list[RM_PROCESS_INFO]:
    """Asks Restart Manager which programs have these files open."""
    rm = _dll("rstrtmgr")
    session = ctypes.c_uint32(0)
    key = ctypes.create_unicode_buffer(CCH_RM_SESSION_KEY + 1)
    rc = rm.RmStartSession(ctypes.byref(session), 0, key)
    if rc:
        raise OSError(rc, f"RmStartSession failed ({rc})")
    try:
        for i in range(0, len(files), 1000):  # (a few at a time: one huge call is no faster)
            chunk = files[i:i + 1000]
            names = (ctypes.c_wchar_p * len(chunk))(*chunk)
            rc = rm.RmRegisterResources(session.value, len(chunk), names, 0, None, 0, None)
            if rc:
                raise OSError(rc, f"RmRegisterResources failed ({rc})")
        needed, count, reasons = ctypes.c_uint32(0), ctypes.c_uint32(0), ctypes.c_uint32(0)
        found = (RM_PROCESS_INFO * 1)()
        rc = rm.RmGetList(session.value, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons))
        tries = 0
        while rc == ERROR_MORE_DATA and tries < 8:  # (more may have opened it meanwhile: ask again, bigger)
            tries += 1
            count = ctypes.c_uint32(needed.value + 4)
            found = (RM_PROCESS_INFO * count.value)()
            rc = rm.RmGetList(session.value, ctypes.byref(needed), ctypes.byref(count), found, ctypes.byref(reasons))
        if rc:
            raise OSError(rc, f"RmGetList failed ({rc})")
        return list(found[:count.value]) if count.value else []
    finally:
        rm.RmEndSession(session.value)


def _filetime(ft: FILETIME) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def _exe_of(pid: int) -> str:
    k = _dll("kernel32")
    h = k.OpenProcess(QUERY_LIMITED, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_uint32(len(buf))
        return buf.value if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else ""
    finally:
        k.CloseHandle(h)


def process_names() -> dict[int, str]:
    """Every running program's file name by id ("csrss.exe"), even the ones Windows protects."""
    k = _dll("kernel32")
    snap = k.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return {}
    out = {}
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = k.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[e.th32ProcessID] = e.szExeFile
            ok = k.Process32NextW(snap, ctypes.byref(e))
    finally:
        k.CloseHandle(snap)
    return out


def _can_close(pid: int) -> bool:
    k = _dll("kernel32")
    h = k.OpenProcess(TERMINATE | SYNCHRONIZE, False, pid)
    if not h:
        return False
    k.CloseHandle(h)
    return True


def program_of(info: RM_PROCESS_INFO, names: dict[int, str] | None = None) -> Program:
    pid = info.Process.dwProcessId
    exe = _exe_of(pid)
    file_name = os.path.basename(exe) if exe else (names if names is not None else process_names()).get(pid, "")
    name = info.strAppName.strip() or (os.path.splitext(file_name)[0] if file_name else f"#{pid}")
    low = file_name.lower() or name.lower()
    p = Program(pid=pid, name=name, exe=exe, started=_filetime(info.Process.ProcessStartTime),
                kind=info.ApplicationType, service=info.strServiceShortName.strip(), session=info.TSSessionId)
    p.critical = (info.ApplicationType == RM_CRITICAL or pid in (0, 4) or low in CRITICAL
                  or name.lower() in CRITICAL)
    p.explorer = info.ApplicationType == RM_EXPLORER or low == "explorer.exe"
    p.own = pid == os.getpid()
    p.blocked = not p.critical and not p.own and not _can_close(pid)
    return p


def find(paths: list[Path]) -> Found:
    """Which programs have these files (or anything in these folders) open."""
    paths = [Path(p) for p in paths]
    files, capped = files_of(paths)
    result = Found(paths=paths, files=len(files), capped=capped)
    if not files:
        return result
    seen = set()
    infos = _rm_list(files)
    names = process_names() if infos else {}
    for info in infos:
        pid = info.Process.dwProcessId
        if pid in seen:
            continue
        seen.add(pid)
        result.programs.append(program_of(info, names))
    result.programs.sort(key=lambda p: (not p.closable, p.name.lower(), p.pid))
    return result


# ----------------------------------------------------------------------------- closing

def _open_same(prog: Program, access: int):
    """A handle to the program, only if it's still the same one (an id is reused once a program ends)."""
    k = _dll("kernel32")
    h = k.OpenProcess(access | QUERY_LIMITED, False, prog.pid)
    if not h:
        return None
    if prog.started:
        times = [FILETIME() for _ in range(4)]
        if k.GetProcessTimes(h, *[ctypes.byref(t) for t in times]) and _filetime(times[0]) != prog.started:
            k.CloseHandle(h)
            return None
    return h


def running(prog: Program) -> bool:
    k = _dll("kernel32")
    h = _open_same(prog, SYNCHRONIZE)
    if not h:
        return False
    try:
        return k.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT: still running
    finally:
        k.CloseHandle(h)


def windows_of(pid: int, folders_only: bool = False) -> list[int]:
    """The program's own windows on the screen (not its dialogs, which close with them). For File
    Explorer only its folder windows: never the taskbar or the desktop."""
    u = _dll("user32")
    found = []

    def each(hwnd, _):
        owner = ctypes.c_uint32()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and u.IsWindowVisible(hwnd) and not u.GetWindow(hwnd, GW_OWNER):
            if folders_only:
                cls = ctypes.create_unicode_buffer(64)
                u.GetClassNameW(hwnd, cls, 64)
                if cls.value not in ("CabinetWClass", "ExploreWClass"):
                    return 1
            found.append(hwnd)
        return 1
    u.EnumWindows(_ENUM_PROC(each), None)
    return found


def close_politely(programs: list[Program], wait_s: float = POLITE_WAIT_S) -> dict[int, str]:
    """Asks each program to close (like clicking the X on its windows) and waits a few seconds.
    Returns pid: "closed" (it ended, or let go of the file's windows), "running" (still open: it may be
    asking to save), "no_window" (nothing to ask: it can only be forced), or "denied"."""
    u = _dll("user32")
    out: dict[int, str] = {}
    waiting: list[Program] = []
    for prog in programs:
        if not prog.closable:
            out[prog.pid] = "denied"
            continue
        if not running(prog):
            out[prog.pid] = "closed"
            continue
        wins = windows_of(prog.pid, folders_only=prog.explorer)
        if not wins:
            out[prog.pid] = "no_window"
            continue
        for hwnd in wins:
            u.PostMessageW(hwnd, WM_CLOSE, None, None)
        waiting.append(prog)
    deadline = time.monotonic() + wait_s
    while waiting:
        waiting = [p for p in waiting if running(p)]
        if not waiting or time.monotonic() > deadline:
            break
        time.sleep(0.1)
    for prog in programs:
        if prog.pid not in out:
            # (File Explorer stays running: closing its folder windows is all that's asked of it)
            out[prog.pid] = "running" if prog in waiting and not prog.explorer else "closed"
    return out


def force_close(prog: Program, wait_s: float = 3.0) -> bool:
    """Ends the program straight away (anything not saved in it is lost). File Explorer is started
    again afterwards. True if it's gone."""
    if not prog.closable:
        return False
    k = _dll("kernel32")
    h = _open_same(prog, TERMINATE | SYNCHRONIZE)
    if not h:
        return not running(prog)
    try:
        k.TerminateProcess(h, 1)
        gone = k.WaitForSingleObject(h, int(wait_s * 1000)) == 0
    finally:
        k.CloseHandle(h)
    log.info("force-closed %s (%s): %s", prog.name, prog.pid, "gone" if gone else "still there")
    if prog.explorer:
        restart_explorer()
    return gone


def restart_explorer():
    """Windows usually starts File Explorer again by itself; if it hasn't after a few seconds, do it."""
    for _ in range(30):
        if _explorer_running():
            return
        time.sleep(0.2)
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    try:
        subprocess.Popen([os.path.join(windir, "explorer.exe")], close_fds=True)
    except OSError:
        log.exception("couldn't start File Explorer again")


def _explorer_running() -> bool:
    """File Explorer's taskbar is there (it's what Explorer shows first when it starts)."""
    u = _dll("user32")
    found = []

    def each(hwnd, _):
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(hwnd, cls, 64)
        if cls.value == "Shell_TrayWnd":
            found.append(hwnd)
            return 0
        return 1
    u.EnumWindows(_ENUM_PROC(each), None)
    return bool(found)
