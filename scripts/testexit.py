"""Ends a test script with its result as the exit code, and nothing else.

os._exit() still runs Windows' own tidy-up of every loaded DLL, and Qt's sometimes crashes there on the
runner (0xC0000005 / 0xC0000409) after every check has passed, which turned a passing test red at random
(main showed it too). TerminateProcess skips that tidy-up, so the code is the test's own."""
import os
import sys


def finish(code: int):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code)
    os._exit(code)
