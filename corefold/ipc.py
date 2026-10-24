"""
Talking to the Corefold that's already running, without loading Qt.

Each right-click on a file starts Corefold.exe once per selected file. Those helper processes only
pass the request on to the running app through a named pipe and quit straight away, so selecting
fifty photos doesn't start fifty copies of the whole app. (The running app listens with Qt's
QLocalServer, which on Windows is a named pipe with this name.)
"""
from __future__ import annotations

import os
import time

SERVER_NAME = f"Corefold-{os.environ.get('USERNAME', 'user')}"
PIPE_PATH = "\\\\.\\pipe\\" + SERVER_NAME


def leave_start_folder(argv: list[str]):
    """Started from a right-click, Corefold's current folder is the one the clicked file is in (often
    Downloads). Windows looks in the current folder for programs started by name, and Pillow for
    Ghostscript, so a "shutdown.exe" or "gswin64c.exe" lying there could run. Files named on the command
    line get their full path first, then Corefold moves to the home folder."""
    for i in range(1, len(argv)):
        arg = argv[i]
        if argv[i - 1] in ("--page", "--run"):
            continue  # (a page's or a tool's name, not a file)
        if not arg.startswith("--") and not os.path.isabs(arg) and os.path.exists(arg):
            argv[i] = os.path.abspath(arg)
    try:
        os.chdir(os.path.expanduser("~"))
    except OSError:
        pass


def message_for(argv: list[str]) -> bytes | None:
    """The message for the running app, or None if this start should open the app itself."""
    if "--restarted" in argv or "--remove-shell-menu" in argv:
        return None

    def after(flag: str) -> list[str]:
        if flag not in argv:
            return []
        return [_unquoted(a) for a in argv[argv.index(flag) + 1:] if not a.startswith("--")]

    if "--run" in argv:
        rest = after("--run")
        if len(rest) >= 2:
            return ("run\n" + "\n".join(rest)).encode("utf-8")
        return None
    for flag, word in (("--convert", "convert"), ("--open", "open"), ("--browse", "browse"), ("--view", "view"),
                       ("--play", "play"), ("--uninstall", "uninstall"), ("--unlock", "unlock"),
                       ("--scan", "scan")):
        if flag in argv:
            paths = after(flag)
            return (word + "\n" + "\n".join(paths)).encode("utf-8") if paths else None
    if "--background" in argv:
        return b""  # already running: nothing to do
    page = after("--page")
    return ("show\n" + page[0]).encode("utf-8") if page else b"show"


def _unquoted(arg: str) -> str:
    """A right-clicked drive comes as "C:\\" (in quotes) and Windows reads the backslash before the closing
    quote as "keep this quote", so the path arrives as C:" (a place that doesn't exist). Put it back."""
    return arg[:-1] + "\\" if arg.endswith('"') else arg


def send(message: bytes, wait_s: float = 4.0) -> bool:
    """Hands the message to the running Corefold. False if it isn't running."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes as wt
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wt.HANDLE
    k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
    k32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    k32.CloseHandle.argtypes = [wt.HANDLE]
    k32.WaitNamedPipeW.argtypes = [wt.LPCWSTR, wt.DWORD]
    invalid = wt.HANDLE(-1).value
    generic_write, open_existing = 0x40000000, 3
    # the running Corefold may only identify this process, never act as it (if another account on the PC
    # made a pipe with this name first, it can't borrow this person's rights through it)
    identify_only = 0x00100000 | 0x00010000   # SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION
    error_file_not_found, error_pipe_busy = 2, 231
    deadline = time.monotonic() + wait_s
    while True:
        handle = k32.CreateFileW(PIPE_PATH, generic_write, 0, None, open_existing, identify_only, None)
        if handle and handle != invalid:
            break
        err = ctypes.get_last_error()
        if err == error_file_not_found:
            return False  # Corefold isn't running
        if time.monotonic() > deadline:
            return False
        if err == error_pipe_busy:
            k32.WaitNamedPipeW(PIPE_PATH, 250)
        else:
            time.sleep(0.05)
    try:
        server = _server_pid(handle)
        if not server or not same_user(server):
            # (a pipe of this name made by someone else: the files clicked aren't told to it)
            log_refused(server)
            return False
        # This helper was started by a click in Explorer, so Windows lets it bring windows to the
        # front. Pass that on (to the running Corefold only): otherwise Corefold's window (e.g.
        # "Resize…") could open behind Explorer, only blinking in the taskbar.
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(server)
        except Exception:
            pass
        written = wt.DWORD(0)
        view = 0
        while view < len(message):
            chunk = message[view:]
            if not k32.WriteFile(handle, chunk, len(chunk), ctypes.byref(written), None) or not written.value:
                return False
            view += written.value
        return True
    finally:
        k32.CloseHandle(handle)


def _server_pid(handle) -> int:
    import ctypes
    from ctypes import wintypes as wt
    pid = wt.ULONG(0)
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetNamedPipeServerProcessId.argtypes = [wt.HANDLE, ctypes.POINTER(wt.ULONG)]
        if k32.GetNamedPipeServerProcessId(handle, ctypes.byref(pid)):
            return int(pid.value)
    except (OSError, AttributeError):
        pass
    return 0


def _token_user(process) -> bytes | None:
    """The account (its SID, as bytes) a process runs as, or None if it can't be read."""
    import ctypes
    from ctypes import wintypes as wt
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    adv.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD,
                                        ctypes.POINTER(wt.DWORD)]
    adv.GetLengthSid.argtypes = [ctypes.c_void_p]
    adv.GetLengthSid.restype = wt.DWORD
    k32.CloseHandle.argtypes = [wt.HANDLE]
    token = wt.HANDLE()
    if not adv.OpenProcessToken(process, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
        return None
    try:
        buf = ctypes.create_string_buffer(256)
        size = wt.DWORD(0)
        if not adv.GetTokenInformation(token, 1, buf, 256, ctypes.byref(size)):  # TokenUser
            return None
        sid = ctypes.c_void_p.from_buffer(buf).value   # TOKEN_USER starts with the SID's address
        return ctypes.string_at(sid, adv.GetLengthSid(sid)) if sid else None
    finally:
        k32.CloseHandle(token)


def same_user(pid: int) -> bool:
    """The process runs as this Windows account. (When its account can't be read, the same Windows
    session counts: another person's programs run in another session.)"""
    import ctypes
    from ctypes import wintypes as wt
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wt.HANDLE
    k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k32.GetCurrentProcess.restype = wt.HANDLE
    k32.CloseHandle.argtypes = [wt.HANDLE]
    k32.ProcessIdToSessionId.argtypes = [wt.DWORD, ctypes.POINTER(wt.DWORD)]
    mine = _token_user(k32.GetCurrentProcess())
    h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        theirs = _token_user(h)
    finally:
        k32.CloseHandle(h)
    if mine is not None and theirs is not None:
        return mine == theirs
    a, b = wt.DWORD(), wt.DWORD()
    return bool(k32.ProcessIdToSessionId(os.getpid(), ctypes.byref(a)) and
                k32.ProcessIdToSessionId(pid, ctypes.byref(b)) and a.value == b.value)


def log_refused(pid: int):
    try:
        import logging
        logging.getLogger("corefold").warning("the pipe %s belongs to another account (process %s): not used",
                                              PIPE_PATH, pid)
    except Exception:
        pass


def forward(argv: list[str]) -> bool:
    """True if a running Corefold took over this request (so this process can just quit)."""
    message = message_for(argv)
    if message is None:
        return False
    return send(message)
