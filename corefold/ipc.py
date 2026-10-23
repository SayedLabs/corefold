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


def message_for(argv: list[str]) -> bytes | None:
    """The message for the running app, or None if this start should open the app itself."""
    if "--restarted" in argv or "--remove-shell-menu" in argv:
        return None

    def after(flag: str) -> list[str]:
        if flag not in argv:
            return []
        return [a for a in argv[argv.index(flag) + 1:] if not a.startswith("--")]

    if "--run" in argv:
        rest = after("--run")
        if len(rest) >= 2:
            return ("run\n" + "\n".join(rest)).encode("utf-8")
        return None
    for flag, word in (("--convert", "convert"), ("--open", "open"), ("--browse", "browse"), ("--view", "view"),
                       ("--play", "play"), ("--scan", "scan")):
        if flag in argv:
            paths = after(flag)
            return (word + "\n" + "\n".join(paths)).encode("utf-8") if paths else None
    if "--background" in argv:
        return b""  # already running: nothing to do
    page = after("--page")
    return ("show\n" + page[0]).encode("utf-8") if page else b"show"


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
    error_file_not_found, error_pipe_busy = 2, 231
    deadline = time.monotonic() + wait_s
    while True:
        handle = k32.CreateFileW(PIPE_PATH, generic_write, 0, None, open_existing, 0, None)
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
        # This helper was started by a click in Explorer, so Windows lets it bring windows to the
        # front. Pass that on: otherwise Corefold's window (e.g. "Resize…") could open behind
        # Explorer, only blinking in the taskbar.
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
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


def forward(argv: list[str]) -> bool:
    """True if a running Corefold took over this request (so this process can just quit)."""
    message = message_for(argv)
    if message is None:
        return False
    return send(message)
