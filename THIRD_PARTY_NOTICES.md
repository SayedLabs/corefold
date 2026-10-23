# Third-party software in Corefold

Corefold is made by Atomic Works. Its own code is MIT-licensed (see LICENSE). It ships with these
components, each under its own licence. The exact version of every Python component in a copy of
Corefold is listed in `COMPONENT-VERSIONS.txt` next to `Corefold.exe`.

| Component | Licence | Source |
|---|---|---|
| FFmpeg (`ffmpeg\ffmpeg.exe`, `ffprobe.exe` and their DLLs) — separate programs Corefold runs | GPL v3 (see `ffmpeg\FFMPEG-LICENSE.txt`) | Attached to every Corefold release as `ffmpeg-source-*.tar.gz` (see below) · https://ffmpeg.org · built by https://github.com/BtbN/FFmpeg-Builds |
| Qt 6 / PySide6 / Shiboken6 | LGPL v3 | https://download.qt.io/official_releases/qt/ · https://download.qt.io/official_releases/QtForPython/ · https://code.qt.io |
| FFmpeg libraries inside Qt Multimedia (Corefold Player: `avcodec`, `avformat`, `avutil`, `swresample`, `swscale` DLLs in `_internal\PySide6`), built by The Qt Company | LGPL v2.1 or later | https://ffmpeg.org · https://code.qt.io/cgit/qt/qtmultimedia.git (how Qt builds it) |
| Pillow | MIT-CMU (HPND) | https://github.com/python-pillow/Pillow |
| pi-heif / libheif | BSD-3 / LGPL v3 | https://github.com/bigcat88/pillow_heif · https://github.com/strukturag/libheif |
| pypdf | BSD-3 | https://github.com/py-pdf/pypdf |
| pypdfium2 / PDFium | Apache 2.0 / BSD-3 | https://github.com/pypdfium2-team/pypdfium2 |
| cryptography (PDF passwords) | Apache 2.0 or BSD-3 (your choice) | https://github.com/pyca/cryptography |
| zxing-cpp | Apache 2.0 | https://github.com/zxing-cpp/zxing-cpp |
| 7-Zip (`7zip\7z.exe`, `7z.dll`), a separate program Corefold runs for archives | GNU LGPL, with the unRAR restriction (see `7zip\7-ZIP-License.txt`): the RAR code may only be used to open RAR files, not to make them | https://www.7-zip.org |
| zstandard / Zstandard (.tar.zst and .zst archives) | BSD-3 | https://github.com/indygreg/python-zstandard · https://github.com/facebook/zstd |
| PyWinRT (Windows text reading) | MIT | https://github.com/pywinrt/pywinrt |
| ONNX Runtime | MIT | https://github.com/microsoft/onnxruntime |
| NumPy | BSD-3 | https://numpy.org |
| SoundCard (recording the PC's sound) / CFFI | BSD-3 / MIT | https://github.com/bastibe/SoundCard · https://github.com/python-cffi/cffi |
| "silueta" background-removal model (U²-Net) | Apache 2.0 (U²-Net) · distributed by rembg (MIT) | https://github.com/xuebinqin/U-2-Net · https://github.com/danielgatis/rembg |
| Fluent UI System Icons (the line icons in the app) | MIT, © Microsoft Corporation | https://github.com/microsoft/fluentui-system-icons |
| Python | PSF License | https://www.python.org |

## Source code for the GPL and LGPL parts

- **FFmpeg:** every Corefold release on GitHub has FFmpeg's complete source code for the exact
  version it ships (`ffmpeg-source-<commit>.tar.gz`) and the build scripts used to make it, which
  name every library built into it (`ffmpeg-source-build-scripts-<commit>.tar.gz`). The version and
  commit are also written in `ffmpeg\SOURCE.txt`.
- **Qt, PySide6, the FFmpeg libraries inside Qt Multimedia and libheif** are used without changes. Their source code for the versions in
  `COMPONENT-VERSIONS.txt` is at the links above.
- **Written offer:** if you can't get any of this source code from those places, ask Atomic Works
  (through the Corefold website or GitHub page) and we will send it to you, for no more than the cost
  of sending it. This offer is valid for three years after we last ship the version you have.

The Qt, Qt Multimedia's FFmpeg and libheif libraries are included as separate DLL files in the `_internal` folder and can
be replaced by the user, as the LGPL requires.
