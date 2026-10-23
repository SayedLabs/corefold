"""The sound the PC is playing, handed to FFmpeg while the screen is recorded.

FFmpeg can't record what Windows plays by itself (only microphones), so Corefold listens to the
speakers through Windows' own "loopback" (the soundcard package) and writes the sound into a named
pipe that FFmpeg reads like a file. Nothing here is loaded unless a recording with the PC's sound
starts, and it stops with the recording."""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import uuid
from ctypes import wintypes

log = logging.getLogger("corefold")

RATE = 48000
CHANNELS = 2
BLOCK = RATE // 50           # 20 ms of sound per write
PIPE_ACCESS_OUTBOUND = 0x2
PIPE_TYPE_BYTE = 0x0
PIPE_WAIT = 0x0
INVALID_HANDLE = ctypes.c_void_p(-1).value
ERROR_PIPE_CONNECTED = 535
GENERIC_READ = 0x80000000
OPEN_EXISTING = 3


def available() -> bool:
    """True if the PC's sound can be recorded (Windows, the soundcard package and a speaker)."""
    if os.name != "nt":
        return False
    try:
        import soundcard
        return soundcard.default_speaker() is not None
    except Exception:  # no sound device at all, or the package is missing
        log.debug("no loopback sound", exc_info=True)
        return False


def ffmpeg_input(pipe: str) -> list[str]:
    """FFmpeg's arguments for reading the sound from the pipe."""
    return ["-f", "f32le", "-ar", str(RATE), "-ac", str(CHANNELS), "-thread_queue_size", "1024", "-i", pipe]


class Loopback:
    """Writes what the speakers play into a named pipe until stop()."""

    def __init__(self):
        self.name = r"\\.\pipe\corefold-sound-" + uuid.uuid4().hex[:12]
        self.error = ""
        self._stop = threading.Event()
        self._pipe = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Makes the pipe (FFmpeg opens it next). Returns False if the sound can't be recorded."""
        if os.name != "nt":
            self.error = "not Windows"
            return False
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateNamedPipeW.restype = wintypes.HANDLE
        k32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        h = k32.CreateNamedPipeW(self.name, PIPE_ACCESS_OUTBOUND, PIPE_TYPE_BYTE | PIPE_WAIT, 1, 1 << 20, 1 << 20,
                                 0, None)
        if not h or h == INVALID_HANDLE:
            self.error = f"pipe: {ctypes.get_last_error()}"
            return False
        self._k32, self._pipe = k32, h
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="corefold-pc-sound")
        self._thread.start()
        self._ready.wait(5)
        if self._mic is None:
            self.error = self.error or "no sound device"
            self.stop()
            self._close()
            return False
        return True

    _mic = None

    def _run(self):
        # everything to do with Windows' sound happens on this thread (COM objects belong to the thread
        # that made them)
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0)
            import soundcard
            speaker = soundcard.default_speaker()
            self._mic = soundcard.get_microphone(speaker.id, include_loopback=True)
        except Exception as e:
            log.info("loopback sound unavailable", exc_info=True)
            self.error = str(e) or type(e).__name__
        finally:
            self._ready.set()
        if self._mic is None:
            self._close()
            return
        k32 = self._k32
        k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                  ctypes.c_void_p]
        if not k32.ConnectNamedPipe(self._pipe, None) and ctypes.get_last_error() != ERROR_PIPE_CONNECTED:
            self._close()
            return
        if self._stop.is_set():  # (woken up by stop() before FFmpeg opened it)
            self._close()
            return
        written = wintypes.DWORD()
        try:
            import numpy
            with self._mic.recorder(samplerate=RATE, channels=CHANNELS, blocksize=BLOCK) as rec:
                while not self._stop.is_set():
                    data = numpy.ascontiguousarray(rec.record(numframes=BLOCK), dtype="<f4").tobytes()
                    if not k32.WriteFile(self._pipe, data, len(data), ctypes.byref(written), None):
                        break  # FFmpeg has finished
        except Exception:
            log.warning("recording the PC's sound stopped", exc_info=True)
        finally:
            self._close()

    def _close(self):
        h, self._pipe = self._pipe, None
        if h:
            self._k32.CloseHandle(wintypes.HANDLE(h))

    def stop(self):
        """Stops writing (call it once FFmpeg has ended)."""
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and self._pipe:
            # still waiting for FFmpeg to open the pipe (FFmpeg failed early): open it ourselves to wake it
            try:
                k32 = self._k32
                k32.CreateFileW.restype = wintypes.HANDLE
                h = k32.CreateFileW(self.name, GENERIC_READ, 0, None, OPEN_EXISTING, 0, None)
                if h and h != INVALID_HANDLE:
                    k32.CloseHandle(wintypes.HANDLE(h))
            except OSError:
                pass
        if t is not None:
            t.join(3)
