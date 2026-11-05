"""Corefold: answering the boxes Office shows while it works for Corefold out of sight. Word opens a PDF by
handing it to its "PDF Reflow" helper (PDFREFLOW.EXE), which first says "Word will now convert your PDF…" in a
box of its own and waits for OK. Nobody would be looking at it (Word itself is hidden), so the conversion would
wait for ever. While Corefold has Word convert a PDF, that box is answered at once: OK, the same as a person
choosing PDF to Word would. Any other box from the hidden Word (a question Corefold can't answer for the person)
is cancelled, so the conversion stops with a plain message instead of hanging. No Qt here."""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading

log = logging.getLogger("corefold")

OFFICE_EXES = ("winword.exe", "pdfreflow.exe")
WM_COMMAND, IDOK, IDCANCEL = 0x0111, 1, 2


def office_pids(names=OFFICE_EXES) -> set[int]:
    """The processes of these programs running now."""
    if os.name != "nt":
        return set()
    from . import uninstall as un
    try:
        return {pid for pid, _parent, exe in un.processes() if exe.lower() in names}
    except OSError:
        return set()


def boxes(pids: set[int]) -> list[int]:
    """The message boxes (Windows' dialog class "#32770") these processes show on screen now."""
    if os.name != "nt" or not pids:
        return []
    from ctypes import wintypes as wt
    u = ctypes.WinDLL("user32", use_last_error=True)
    proc_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    u.EnumWindows.argtypes = (proc_type, wt.LPARAM)
    u.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
    u.GetClassNameW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
    u.IsWindowVisible.argtypes = (wt.HWND,)
    found = []

    def each(hwnd, _):
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and u.IsWindowVisible(hwnd):
            name = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(hwnd, name, 64)
            if name.value == "#32770":
                found.append(hwnd)
        return True
    u.EnumWindows(proc_type(each), 0)
    return found


def shown(pids: set[int]) -> set[int]:
    """Which of these processes have a window on screen."""
    if os.name != "nt" or not pids:
        return set()
    from ctypes import wintypes as wt
    u = ctypes.WinDLL("user32", use_last_error=True)
    proc_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    u.EnumWindows.argtypes = (proc_type, wt.LPARAM)
    u.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
    u.IsWindowVisible.argtypes = (wt.HWND,)
    out = set()

    def each(hwnd, _):
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and u.IsWindowVisible(hwnd):
            out.add(pid.value)
        return True
    u.EnumWindows(proc_type(each), 0)
    return out


def has_button(hwnd: int, button_id: int) -> bool:
    if os.name != "nt":
        return False
    from ctypes import wintypes as wt
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetDlgItem.argtypes, u.GetDlgItem.restype = (wt.HWND, ctypes.c_int), wt.HWND
    return bool(u.GetDlgItem(hwnd, button_id))


def press(hwnd: int, button_id: int):
    """Presses a button of a box (as its own keyboard shortcut would: works without moving the mouse)."""
    from ctypes import wintypes as wt
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetDlgItem.argtypes, u.GetDlgItem.restype = (wt.HWND, ctypes.c_int), wt.HWND
    u.PostMessageW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    button = u.GetDlgItem(hwnd, button_id) or 0
    u.PostMessageW(hwnd, WM_COMMAND, button_id, button)


class Watch:
    """While Word converts for Corefold: OK on the PDF Reflow box, Cancel on any other box from an Office that
    started for this job (an Office the person already had open is left alone). .asked is True when a box
    had to be cancelled. stop() ends the watching; end_new() closes what this job started (after a time-out)."""

    def __init__(self, reflow=("pdfreflow.exe",), names=OFFICE_EXES, every: float = 0.1):
        self.names, self.reflow, self.every = names, reflow, every
        self.before = office_pids(names)
        self.asked = False
        self.answered = 0
        self._stop = threading.Event()
        self._done: set[int] = set()
        self._thread = threading.Thread(target=self._run, daemon=True, name="office-boxes")
        if os.name == "nt":
            self._thread.start()

    def new_pids(self) -> dict[int, str]:
        if os.name != "nt":
            return {}
        from . import uninstall as un
        try:
            return {pid: exe.lower() for pid, _parent, exe in un.processes()
                    if exe.lower() in self.names and pid not in self.before}
        except OSError:
            return {}

    def _run(self):
        while not self._stop.wait(self.every):
            try:
                self.answer()
            except Exception:
                log.debug("office boxes couldn't be looked at", exc_info=True)

    def answer(self):
        new = self.new_pids()
        reflow = {pid for pid, exe in new.items() if exe in self.reflow}
        for hwnd in boxes(set(new)):
            if hwnd in self._done:
                continue
            self._done.add(hwnd)
            from ctypes import wintypes as wt
            u = ctypes.WinDLL("user32", use_last_error=True)
            u.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in reflow:
                press(hwnd, IDOK)
                self.answered += 1
                log.info("office: Word's PDF conversion box answered OK")
            else:
                self.asked = True
                press(hwnd, IDCANCEL if has_button(hwnd, IDCANCEL) else IDOK)
                log.info("office: a box from the hidden Word was cancelled")

    def stop(self):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(2)

    def end_new(self):
        """Closes the Office this job started and that shows nothing (stuck for good, after the time-out). A Word
        the person opened meanwhile has a window on screen, so it's left alone."""
        self.stop()
        new = set(self.new_pids())
        for pid in new - shown(new):
            subprocess.run([_taskkill(), "/F", "/PID", str(pid)], capture_output=True, stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _taskkill() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(root, "System32", "taskkill.exe") if sys.platform == "win32" else "kill"
