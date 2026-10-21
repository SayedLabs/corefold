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
| 7-Zip (`7zip\7z.exe`, `7z.dll`), a separate program Satsuma runs for archives | GNU LGPL, with the unRAR restriction (see `7zip\7-ZIP-License.txt`): the RAR code may only be used to open RAR files, not to make them | https://www.7-zip.org |
| PyWinRT (Windows text reading) | MIT | https://github.com/pywinrt/pywinrt |
| ONNX Runtime | MIT | https://github.com/microsoft/onnxruntime |
| NumPy | BSD-3 | https://numpy.org |
| "silueta" background-removal model (U²-Net) | Apache 2.0 (U²-Net) · distributed by rembg (MIT) | https://github.com/xuebinqin/U-2-Net · https://github.com/danielgatis/rembg |
| Python | PSF License | https://www.python.org |

The Qt and libheif libraries are included as separate DLL files in the `_internal`
folder and can be replaced by the user, as the LGPL requires.
