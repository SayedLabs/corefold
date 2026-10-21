# Third-party software in Satsuma

Satsuma's own code is MIT-licensed (see LICENSE). It ships with these components:

| Component | License | Source |
|---|---|---|
| FFmpeg (`ffmpeg\ffmpeg.exe`, `ffprobe.exe`) — separate programs Satsuma runs | GPL v3 (see `ffmpeg\FFMPEG-LICENSE.txt`) | https://ffmpeg.org · build from https://github.com/BtbN/FFmpeg-Builds |
| Qt / PySide6 | LGPL v3 | https://www.qt.io · https://pypi.org/project/PySide6/ |
| Pillow | MIT-CMU (HPND) | https://github.com/python-pillow/Pillow |
| pi-heif / libheif | BSD-3 / LGPL v3 | https://github.com/bigcat88/pillow_heif |
| pypdf | BSD-3 | https://github.com/py-pdf/pypdf |
| pypdfium2 / PDFium | Apache 2.0 / BSD-3 | https://github.com/pypdfium2-team/pypdfium2 |
| zxing-cpp | Apache 2.0 | https://github.com/zxing-cpp/zxing-cpp |
| Python | PSF License | https://www.python.org |

The Qt and libheif libraries are included as separate DLL files in the `_internal`
folder and can be replaced by the user, as the LGPL requires.
