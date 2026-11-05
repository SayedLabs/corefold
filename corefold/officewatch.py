"""Corefold: answering the boxes Office shows while it works for Corefold out of sight. Word opens a PDF by
handing it to its "PDF Reflow" helper (PDFREFLOW.EXE), which first says "Word will now convert your PDF…" in a
box of its own and waits for OK. Nobody would be looking at it (Word itself is hidden), so the conversion would
wait for ever. While Corefold has Word convert a PDF, that box is answered at once: OK, the same as a person
choosing PDF to Word would. Any other box from the hidden Word (a question Corefold can't answer for the person,
like "The last time you opened this file, it caused a serious error…") gets No / Cancel, so the conversion stops
with a plain message instead of hanging.

Office draws most of its boxes itself ("NUIDialog": the buttons aren't windows of their own), so they're pressed
through Windows' UI Automation (the way screen readers press buttons); classic Windows boxes ("#32770") are
pressed directly. No Qt here."""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading

log = logging.getLogger("corefold")

OFFICE_EXES = ("winword.exe", "pdfreflow.exe")
BOX_CLASSES = ("#32770", "NUIDialog")
WM_COMMAND, IDOK, IDCANCEL = 0x0111, 1, 2

# presses the button with this AutomationId (for Office's own boxes "1" is OK, "2" is Cancel / No) in one window
UIA_PRESS = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$w = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr][long]$env:CF_HWND)
$c = New-Object System.Windows.Automation.PropertyCondition(
       [System.Windows.Automation.AutomationElement]::AutomationIdProperty, $env:CF_BUTTON)
$b = $w.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $c)
if (-not $b) { exit 3 }
$b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()"""


def _user32():
    from ctypes import wintypes as wt
    u = ctypes.WinDLL("user32", use_last_error=True)
    proc_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    u.EnumWindows.argtypes = (proc_type, wt.LPARAM)
    u.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
    u.GetClassNameW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
    u.IsWindowVisible.argtypes = (wt.HWND,)
    u.GetDlgItem.argtypes, u.GetDlgItem.restype = (wt.HWND, ctypes.c_int), wt.HWND
    u.PostMessageW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    return u, proc_type


def windows(pids: set[int]) -> list[tuple[int, int, str]]:
    """(window, process, class) of every window these processes show on screen now."""
    if os.name != "nt" or not pids:
        return []
    from ctypes import wintypes as wt
    u, proc_type = _user32()
    found = []

    def each(hwnd, _):
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and u.IsWindowVisible(hwnd):
            name = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(hwnd, name, 64)
            found.append((int(hwnd), pid.value, name.value))
        return True
    u.EnumWindows(proc_type(each), 0)
    return found


def office_pids(names=OFFICE_EXES) -> set[int]:
    """The processes of these programs running now."""
    if os.name != "nt":
        return set()
    from . import uninstall as un
    try:
        return {pid for pid, _parent, exe in un.processes() if exe.lower() in names}
    except OSError:
        return set()


def press(hwnd: int, cls: str, button_id: int, uia: bool | None = None) -> bool:
    """Presses a button of a box. Classic boxes: as their own keyboard shortcut would. Office's own boxes (and
    when uia is True): through UI Automation. False when the box has no such button."""
    if uia is None:
        uia = cls != "#32770"
    if not uia:
        u, _ = _user32()
        button = u.GetDlgItem(hwnd, button_id)
        if not button:
            return False
        u.PostMessageW(hwnd, WM_COMMAND, button_id, button)
        return True
    root = os.environ.get("SystemRoot", r"C:\Windows")
    ps = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    try:
        r = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", UIA_PRESS], capture_output=True,
                           timeout=30, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                           env=dict(os.environ, CF_HWND=str(hwnd), CF_BUTTON=str(button_id)))
    except (OSError, subprocess.SubprocessError):
        log.debug("a box couldn't be answered", exc_info=True)
        return False
    if r.returncode:
        log.debug("a box couldn't be answered (%s): %s", r.returncode, r.stderr[-500:])
    return r.returncode == 0


class Watch:
    """While Word converts for Corefold: OK on the PDF Reflow box, No / Cancel on any other box from an Office
    that started for this job (an Office the person already had open is left alone). .asked is True when a box
    had to be turned down. stop() ends the watching; end_new() closes what this job started (after a time-out)."""

    def __init__(self, reflow=("pdfreflow.exe",), names=OFFICE_EXES, every: float = 0.1, uia: bool | None = None):
        self.names, self.reflow, self.every, self.uia = names, reflow, every, uia
        self.before = office_pids(names)
        self.asked = False
        self.answered = 0
        self._stop = threading.Event()
        self._done: dict[int, int] = {}  # box -> times tried
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
        for hwnd, pid, cls in windows(set(new)):
            if cls not in BOX_CLASSES or self._done.get(hwnd, 0) >= 3:
                continue
            self._done[hwnd] = self._done.get(hwnd, 0) + 1
            if new.get(pid) in self.reflow:
                if press(hwnd, cls, IDOK, self.uia):
                    self.answered += 1
                    self._done[hwnd] = 3
                    log.info("office: Word's PDF conversion box answered OK")
            else:
                self.asked = True  # (never Yes / OK to a question nobody here read)
                if press(hwnd, cls, IDCANCEL, self.uia):
                    self._done[hwnd] = 3
                    log.info("office: a box from the hidden Word was turned down")

    def stop(self):
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(2)

    def end_new(self):
        """Closes the Office this job started that's stuck (shows nothing, or only a box), after the time-out.
        A Word the person opened meanwhile has a real window on screen, so it's left alone."""
        self.stop()
        new = set(self.new_pids())
        busy = {pid for _hwnd, pid, cls in windows(new) if cls not in BOX_CLASSES}
        for pid in new - busy:
            subprocess.run([_taskkill(), "/F", "/PID", str(pid)], capture_output=True, stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _taskkill() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(root, "System32", "taskkill.exe") if sys.platform == "win32" else "kill"
