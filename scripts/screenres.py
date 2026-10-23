"""Sets the screen size of a Windows PC (GitHub's test PCs start at 1024x768, smaller than most laptops).

Run: python scripts/screenres.py 1920 1080"""
import ctypes
import sys
from ctypes import wintypes


class DEVMODEW(ctypes.Structure):
    _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD), ("dmPositionX", wintypes.LONG), ("dmPositionY", wintypes.LONG),
                ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
                ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD), ("dmPelsWidth", wintypes.DWORD),
                ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD), ("dmICMMethod", wintypes.DWORD),
                ("dmICMIntent", wintypes.DWORD), ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
                ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
                ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)]


width, height = int(sys.argv[1]), int(sys.argv[2])
dm = DEVMODEW()
dm.dmSize = ctypes.sizeof(DEVMODEW)
user32 = ctypes.windll.user32
user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm))
print(f"screen was {dm.dmPelsWidth}x{dm.dmPelsHeight}")
dm.dmPelsWidth, dm.dmPelsHeight = width, height
dm.dmFields = 0x80000 | 0x100000  # DM_PELSWIDTH | DM_PELSHEIGHT
result = user32.ChangeDisplaySettingsW(ctypes.byref(dm), 0)
user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm))
print(f"screen is now {dm.dmPelsWidth}x{dm.dmPelsHeight} (result {result})")
