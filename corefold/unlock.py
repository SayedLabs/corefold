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
import ntpath
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
QUERY_INFORMATION, VM_READ = 0x400, 0x10
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
                     ("WaitForSingleObject", (H, U32), U32),
                     ("ReadProcessMemory", (H, P, P, ctypes.c_size_t, P), I)),
        "ntdll": (("NtQueryInformationProcess", (H, U32, P, U32, P), ctypes.c_long),),
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


def _read(h, address: int, size: int) -> bytes | None:
    buf = ctypes.create_string_buffer(size)
    done = ctypes.c_size_t(0)
    if not address or not _dll("kernel32").ReadProcessMemory(h, ctypes.c_void_p(address), buf, size,
                                                             ctypes.byref(done)) or done.value != size:
        return None
    return buf.raw


def current_folder(pid: int) -> str:
    """The folder a program is "in" (a Command Prompt's folder, or where a program was started from),
    or "". Windows doesn't count that as an open file, but it won't delete or rename the folder while a
    program is in it. Read from the program's own memory, so only for programs run as the same user."""
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        return ""
    k, nt = _dll("kernel32"), _dll("ntdll")
    h = k.OpenProcess(QUERY_INFORMATION | VM_READ, False, pid)
    if not h:
        return ""
    try:
        peb32 = ctypes.c_void_p(0)  # (a 32-bit program keeps its own copy, the one it changes)
        if nt.NtQueryInformationProcess(h, 26, ctypes.byref(peb32), 8, None) == 0 and peb32.value:
            params = _read(h, peb32.value + 0x10, 4)
            text = params and _read(h, int.from_bytes(params, "little") + 0x24, 8)
            if not text:
                return ""
            length, address = int.from_bytes(text[:2], "little"), int.from_bytes(text[4:8], "little")
        else:
            basic = ctypes.create_string_buffer(48)  # PROCESS_BASIC_INFORMATION
            if nt.NtQueryInformationProcess(h, 0, basic, 48, None) != 0:
                return ""
            peb = int.from_bytes(basic.raw[8:16], "little")
            params = _read(h, peb + 0x20, 8)
            text = params and _read(h, int.from_bytes(params, "little") + 0x38, 16)
            if not text:
                return ""
            length, address = int.from_bytes(text[:2], "little"), int.from_bytes(text[8:16], "little")
        if not 0 < length <= 2 * 32767:
            return ""
        raw = _read(h, address, length)
        return raw.decode("utf-16-le", "replace") if raw else ""
    except (OSError, ValueError):
        return ""
    finally:
        k.CloseHandle(h)


def _inside(path: str, tops: tuple[str, ...]) -> bool:
    p = ntpath.normcase(ntpath.normpath(path)).rstrip("\\") + "\\"
    return p.startswith(tops)


def in_folders(folders: list[str], names: dict[int, str], skip: set) -> list[Program]:
    """Programs that are "in" these folders, or were started from a program file inside them: neither
    is an open file, so Restart Manager doesn't see them, yet Windows won't delete the folder."""
    tops = tuple(ntpath.normcase(ntpath.normpath(f)).rstrip("\\") + "\\" for f in folders)
    out = []
    for pid, file_name in names.items():
        if pid in (0, 4) or pid in skip or file_name.lower() in CRITICAL:
            continue
        exe = _exe_of(pid)
        here = current_folder(pid)
        if not (exe and _inside(exe, tops)) and not (here and _inside(here, tops)):
            continue
        p = Program(pid=pid, name=ntpath.splitext(ntpath.basename(exe) or file_name)[0] or f"#{pid}", exe=exe,
                    started=_started(pid), kind=RM_CONSOLE if not windows_of(pid) else RM_MAIN_WINDOW)
        p.explorer = file_name.lower() == "explorer.exe"
        p.own = pid == os.getpid()
        p.blocked = not p.own and not _can_close(pid)
        out.append(p)
    return out


def find(paths: list[Path]) -> Found:
    """Which programs have these files (or anything in these folders) open, or are working in these folders."""
    paths = [Path(p) for p in paths]
    files, capped = files_of(paths)
    result = Found(paths=paths, files=len(files), capped=capped)
    drives = [str(p) for p in paths if is_drive(p)]
    folders = [str(p) for p in paths if not is_drive(p) and p.is_dir()]
    if not files and not drives and not folders:
        return result
    seen = set()
    infos = _rm_list(files) if files else []
    names = process_names() if infos or drives or folders else {}
    for info in infos:
        pid = info.Process.dwProcessId
        if pid in seen:
            continue
        seen.add(pid)
        result.programs.append(program_of(info, names))
    for prog in running_from(drives, names) if drives else []:
        if prog.pid not in seen:
            seen.add(prog.pid)
            result.programs.append(prog)
    for prog in in_folders(folders, names, seen) if folders else []:
        seen.add(prog.pid)
        result.programs.append(prog)
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


# ----------------------------------------------------------------------------- deleting at the next start

MOVEFILE_DELAY_UNTIL_REBOOT = 0x4
MAX_AT_RESTART = 20000     # files and folders Windows is asked to delete at the next start


def at_restart_order(paths: list[str]) -> list[str]:
    """Everything to delete, innermost first: Windows deletes them in this order as the PC starts,
    and a folder only goes once it's empty. A link or junction is only the link: what it points at
    (another drive, Windows' own folders) is never gone through."""
    from .core import is_link
    order: list[str] = []

    def walk(folder: str):
        try:
            with os.scandir(folder) as it:
                entries = list(it)
        except OSError:
            entries = []
        for e in entries:
            if len(order) > MAX_AT_RESTART:
                return
            if e.is_dir(follow_symlinks=False) and not is_link(e.path):
                walk(e.path)  # (its own folders and files come first)
            order.append(e.path)

    for p in paths:
        if not is_link(p) and os.path.isdir(p):
            walk(p)
            if len(order) > MAX_AT_RESTART:
                return order
        order.append(p)
    return order


def at_restart_allowed(path: str) -> bool:
    """What the administrator copy agrees to delete as the PC starts: never through a link or junction,
    never Windows, programs' folders or a drive, and a folder only where Corefold would remove one."""
    from .core import is_link
    from .space import protected
    from .uninstall import _safe_folder, through_link
    if through_link(path) or protected(Path(path)):
        return False
    return is_link(path) or not os.path.isdir(path) or _safe_folder(path)


def delete_at_restart(paths: list[str]) -> dict:
    """Asks Windows to delete these when the PC next starts, before any program can open them (as
    administrator: Windows only takes this from one). Returns how many it took."""
    order = at_restart_order(paths)
    if len(order) > MAX_AT_RESTART:
        return {"error": "too many"}
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.MoveFileExW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    k32.MoveFileExW.restype = ctypes.c_int
    done = failed = 0
    for p in order:
        if k32.MoveFileExW(p, None, MOVEFILE_DELAY_UNTIL_REBOOT):
            done += 1
        else:
            failed += 1
            log.warning("Windows won't delete %s at the next start (error %s)", p, ctypes.get_last_error())
    return {"scheduled": done, "failed": failed}


def move_at_restart_problem(src: str, dst: str) -> str:
    """Why Windows can't move src to dst as the PC starts ("" when it can): it only moves on the same
    drive, never over something already there, and never Windows' or programs' own folders."""
    from .space import protected
    if not (os.path.isabs(src) and os.path.isabs(dst)) or not os.path.lexists(src):
        return "missing"
    if os.path.normcase(os.path.splitdrive(src)[0]) != os.path.normcase(os.path.splitdrive(dst)[0]):
        return "drive"
    if os.path.lexists(dst):
        return "taken"
    parent = os.path.dirname(dst)
    if not os.path.isdir(parent) or protected(Path(dst)) or not at_restart_allowed(src):
        return "refused"
    return ""


def move_at_restart(moves: list[tuple[str, str]]) -> dict:
    """Asks Windows to move or rename these as the PC next starts (as administrator, like deleting)."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.MoveFileExW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    k32.MoveFileExW.restype = ctypes.c_int
    done = failed = 0
    for src, dst in moves:
        if k32.MoveFileExW(src, dst, MOVEFILE_DELAY_UNTIL_REBOOT):
            done += 1
        else:
            failed += 1
            log.warning("Windows won't move %s at the next start (error %s)", src, ctypes.get_last_error())
    return {"scheduled": done, "failed": failed}


def ask_move_at_restart(moves: list[tuple[Path, Path]]) -> dict | None:
    """Runs move_at_restart in Corefold as administrator (Windows asks first). None if the person said no."""
    from .uninstall import run_elevated
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        command = [sys.executable, str(Path(__file__).resolve().parent.parent / "main.py")]
    return run_elevated(command, "delete-at-restart", {"paths": [], "moves": [[str(a), str(b)] for a, b in moves]})


def ask_delete_at_restart(paths: list[Path]) -> dict | None:
    """Runs delete_at_restart in Corefold as administrator (Windows asks first). None if the person said no."""
    from .uninstall import run_elevated
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        command = [sys.executable, str(Path(__file__).resolve().parent.parent / "main.py")]
    return run_elevated(command, "delete-at-restart", {"paths": [str(p) for p in paths]})


# ----------------------------------------------------------------------------- USB drives

DRIVE_REMOVABLE, DRIVE_FIXED = 2, 3
BUS_USB = 7
IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x2D1080
FILE_SHARE_RW, OPEN_EXISTING = 0x3, 3
INVALID_HANDLE = ctypes.c_void_p(-1).value
GUID_DEVINTERFACE_DISK = "53f56307-b6bf-11d0-94f2-00a0c91efb8b"
DEVPKEY_INSTANCE_ID = ("78c34fc8-104a-4aca-9ea4-524d52996e57", 256)
EJECT_TRIES = 3


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_uint8 * 8)]


class DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_uint32)]


class STORAGE_DEVICE_NUMBER(ctypes.Structure):
    _fields_ = [("DeviceType", ctypes.c_uint32), ("DeviceNumber", ctypes.c_uint32),
                ("PartitionNumber", ctypes.c_uint32)]


def _guid(text: str) -> GUID:
    import uuid
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


@functools.cache
def _usb_dll(name: str):
    """kernel32 and cfgmgr32 with what the USB drives need (kept apart from _dll's own copies)."""
    lib = ctypes.WinDLL(name, use_last_error=True)
    H, U32, P, I, W = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p
    table = {
        "kernel32": (("CreateFileW", (W, U32, U32, P, U32, U32, H), H), ("CloseHandle", (H,), I),
                     ("DeviceIoControl", (H, U32, P, U32, P, U32, P, P), I),
                     ("GetLogicalDrives", (), U32), ("GetDriveTypeW", (W,), U32),
                     ("GetVolumeInformationW", (W, W, U32, P, P, P, W, U32), I)),
        "cfgmgr32": (("CM_Get_Device_Interface_List_SizeW", (P, P, W, U32), U32),
                     ("CM_Get_Device_Interface_ListW", (P, W, P, U32, U32), U32),
                     ("CM_Get_Device_Interface_PropertyW", (W, P, P, P, P, U32), U32),
                     ("CM_Locate_DevNodeW", (P, W, U32), U32), ("CM_Get_Parent", (P, U32, U32), U32),
                     ("CM_Get_Device_IDW", (U32, W, U32, U32), U32),
                     ("CM_Request_Device_EjectW", (U32, P, W, U32, U32), U32)),
    }
    for fn, args, res in table.get(name, ()):
        getattr(lib, fn).argtypes, getattr(lib, fn).restype = args, res
    return lib


@dataclass
class Drive:
    """A USB drive (or a memory card) with a letter."""
    root: str                  # "E:\\"
    label: str = ""            # its own name ("KINGSTON"), or what it is ("SanDisk Ultra")
    total: int = 0
    free: int = 0


def _open_device(path: str):
    h = _usb_dll("kernel32").CreateFileW(path, 0, FILE_SHARE_RW, None, OPEN_EXISTING, 0, None)
    return None if not h or h == INVALID_HANDLE else h


def _ioctl(h, code: int, inp=None, out_size: int = 1024) -> bytes | None:
    out = ctypes.create_string_buffer(out_size)
    got = ctypes.c_uint32(0)
    ok = _usb_dll("kernel32").DeviceIoControl(h, code, ctypes.byref(inp) if inp is not None else None,
                                               ctypes.sizeof(inp) if inp is not None else 0, out, out_size,
                                               ctypes.byref(got), None)
    return out.raw[:got.value] if ok else None


def _describe(root: str) -> tuple[int, str]:
    """The drive's bus (USB is 7) and its product name, from the drive itself. (-1, "") if it won't say."""
    import struct
    h = _open_device("\\\\.\\" + root[:2])
    if h is None:
        return -1, ""
    try:
        query = (ctypes.c_uint32 * 3)(0, 0, 0)  # (StorageDeviceProperty, PropertyStandardQuery)
        raw = _ioctl(h, IOCTL_STORAGE_QUERY_PROPERTY, query)
    finally:
        _usb_dll("kernel32").CloseHandle(h)
    if not raw or len(raw) < 32:
        return -1, ""
    product_at, bus = struct.unpack_from("<I", raw, 16)[0], struct.unpack_from("<i", raw, 28)[0]
    product = ""
    if 0 < product_at < len(raw):
        product = raw[product_at:].split(b"\0", 1)[0].decode("ascii", "replace").strip()
    return bus, product


def _system_drive() -> str:
    return os.path.normcase(os.environ.get("SystemDrive", "C:")[:2])


def drive_letters() -> int:
    """Which drive letters there are now (quick: the page asks every few seconds)."""
    return _usb_dll("kernel32").GetLogicalDrives() if sys.platform == "win32" else 0


def usb_drives() -> list[Drive]:
    """The USB drives and memory cards plugged in now (never the drive Windows is on)."""
    if sys.platform != "win32":
        return []
    import shutil
    k = _usb_dll("kernel32")
    mask = k.GetLogicalDrives()
    out = []
    for i in range(26):
        if not mask & (1 << i):
            continue
        root = f"{chr(65 + i)}:\\"
        kind = k.GetDriveTypeW(root)
        if kind not in (DRIVE_REMOVABLE, DRIVE_FIXED) or os.path.normcase(root[:2]) == _system_drive():
            continue
        bus, product = _describe(root)
        if kind == DRIVE_FIXED and bus != BUS_USB:  # (a disk inside the PC)
            continue
        try:
            u = shutil.disk_usage(root)
        except OSError:
            continue  # (a card reader with no card in it)
        name = ctypes.create_unicode_buffer(261)
        label = name.value if k.GetVolumeInformationW(root, name, 261, None, None, None, None, 0) else ""
        out.append(Drive(root, label.strip() or product, u.total, u.free))
    return out


def too_wide(path) -> bool:
    """A whole drive that isn't a USB drive (C:\\), Windows' own folder or anything in it, or a folder that
    holds every program or everyone's files (Program Files, Users, your own folder, AppData). Nearly every
    program uses something in it, so "Close all" would close them all, and none of it can be deleted."""
    p = os.path.normcase(os.path.abspath(str(path))).rstrip("\\/")
    if is_drive(str(path)):
        return os.path.normcase(str(path)[:2]) not in {os.path.normcase(d.root[:2]) for d in usb_drives()}
    if not os.path.isdir(p):
        return False
    windows = os.environ.get("SystemRoot")
    if windows and (p == _norm(windows) or p.startswith(_norm(windows) + os.sep)):
        return True
    home = Path.home()
    tops = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData", "APPDATA",
                                         "LOCALAPPDATA", "PUBLIC")]
    tops += [str(home), str(home.parent), str(home / "AppData"), str(home / "AppData" / "LocalLow"),
             os.environ.get("SystemDrive", "C:") + os.sep + "Users"]
    return any(t and p == _norm(t) for t in tops)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def is_drive(path) -> bool:
    """A drive's own top ("E:\\"), not a folder on it."""
    p = str(path)
    return len(p) in (2, 3) and p[1] == ":" and p[0].isalpha() and (len(p) == 2 or p[2] in "\\/")


def running_from(roots: list[str], names: dict[int, str] | None = None) -> list[Program]:
    """Programs whose own program file is on these drives (a program run from a USB stick keeps it
    busy even when it has no file open on it)."""
    tops = tuple(os.path.normcase(r[:2]) + "\\" for r in roots)
    out = []
    for pid, file_name in (names if names is not None else process_names()).items():
        if pid in (0, 4):
            continue
        exe = _exe_of(pid)
        if not exe or not os.path.normcase(exe).startswith(tops):
            continue
        p = Program(pid=pid, name=os.path.splitext(os.path.basename(exe))[0] or file_name, exe=exe,
                    started=_started(pid), kind=RM_MAIN_WINDOW)
        p.critical = file_name.lower() in CRITICAL
        p.own = pid == os.getpid()
        p.blocked = not p.critical and not p.own and not _can_close(pid)
        out.append(p)
    return out


def _started(pid: int) -> int:
    k = _dll("kernel32")
    h = k.OpenProcess(QUERY_LIMITED, False, pid)
    if not h:
        return 0
    try:
        times = [FILETIME() for _ in range(4)]
        return _filetime(times[0]) if k.GetProcessTimes(h, *[ctypes.byref(t) for t in times]) else 0
    finally:
        k.CloseHandle(h)


def _device_number(path: str) -> tuple[int, int] | None:
    h = _open_device(path)
    if h is None:
        return None
    try:
        raw = _ioctl(h, IOCTL_STORAGE_GET_DEVICE_NUMBER, out_size=ctypes.sizeof(STORAGE_DEVICE_NUMBER))
    finally:
        _usb_dll("kernel32").CloseHandle(h)
    if not raw or len(raw) < ctypes.sizeof(STORAGE_DEVICE_NUMBER):
        return None
    n = STORAGE_DEVICE_NUMBER.from_buffer_copy(raw)
    return n.DeviceType, n.DeviceNumber


def instance_of(interface: str) -> str:
    """A disk's device id from its interface's path: Windows says it, or it's read from the path
    ("\\\\?\\usbstor#disk&ven_x#123&0#{guid}" is USBSTOR\\DISK&VEN_X\\123&0)."""
    try:
        cm = _usb_dll("cfgmgr32")
        key = DEVPROPKEY(_guid(DEVPKEY_INSTANCE_ID[0]), DEVPKEY_INSTANCE_ID[1])
        kind, size = ctypes.c_uint32(0), ctypes.c_uint32(1024)
        buf = ctypes.create_unicode_buffer(512)
        if cm.CM_Get_Device_Interface_PropertyW(interface, ctypes.byref(key), ctypes.byref(kind), buf,
                                                ctypes.byref(size), 0) == 0 and buf.value:
            return buf.value
    except (OSError, AttributeError):
        pass
    body = interface[4:] if interface.startswith(("\\\\?\\", "\\\\.\\")) else interface
    if "#{" in body:
        body = body[:body.rindex("#{")]
    return body.replace("#", "\\").upper()


def _disk_of(root: str) -> str:
    """The device id of the disk the drive is on ("" if Windows won't say)."""
    number = _device_number("\\\\.\\" + root[:2])
    if number is None:
        return ""
    cm = _usb_dll("cfgmgr32")
    guid = _guid(GUID_DEVINTERFACE_DISK)
    size = ctypes.c_uint32(0)
    if cm.CM_Get_Device_Interface_List_SizeW(ctypes.byref(size), ctypes.byref(guid), None, 0) or size.value < 2:
        return ""
    buf = ctypes.create_unicode_buffer(size.value)
    if cm.CM_Get_Device_Interface_ListW(ctypes.byref(guid), None, buf, size.value, 0):
        return ""
    for interface in ctypes.wstring_at(ctypes.addressof(buf), size.value).split("\0"):
        if interface and _device_number(interface) == number:
            return instance_of(interface)
    return ""


def eject(root: str) -> dict:
    """Asks Windows to safely remove the USB drive (what "Eject" in File Explorer does): it says no
    while a program still uses it. {"ok": True}, or {"ok": False, "why": "busy"|"unknown"|"gone"}."""
    if not os.path.exists(root):
        return {"ok": False, "why": "gone"}
    disk = _disk_of(root)
    if not disk:
        return {"ok": False, "why": "unknown"}
    cm = _usb_dll("cfgmgr32")
    node, parent = ctypes.c_uint32(0), ctypes.c_uint32(0)
    if cm.CM_Locate_DevNodeW(ctypes.byref(node), disk, 0):
        return {"ok": False, "why": "unknown"}
    # (the USB device the disk is on is what comes out; a memory card in the PC's own reader only
    # takes its card out, never the reader)
    target = node.value
    if cm.CM_Get_Parent(ctypes.byref(parent), node.value, 0) == 0:
        pid = ctypes.create_unicode_buffer(512)
        if cm.CM_Get_Device_IDW(parent.value, pid, 512, 0) == 0 and pid.value.upper().startswith("USB\\"):
            target = parent.value
    veto, name = ctypes.c_int(0), ctypes.create_unicode_buffer(261)
    for _ in range(EJECT_TRIES):
        veto.value = 0
        # (given somewhere to say why, Windows shows none of its own messages)
        rc = cm.CM_Request_Device_EjectW(target, ctypes.byref(veto), name, 261, 0)
        if rc == 0 and veto.value == 0:
            log.info("ejected %s (%s)", root, disk)
            return {"ok": True}
        log.info("eject %s: Windows said no (cr %s, veto %s %s)", root, rc, veto.value, name.value)
        time.sleep(0.5)
    return {"ok": False, "why": "busy" if veto.value else "unknown"}
