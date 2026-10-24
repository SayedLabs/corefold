#!/bin/bash
# Builds the FFmpeg that Corefold ships: the same FFmpeg and libraries as BtbN's "win64-gpl-shared"
# build (https://github.com/BtbN/FFmpeg-Builds), but with only the parts Corefold uses, so it is
# about 150 MB smaller. Runs inside BtbN's build image, which has every library already built:
#
#   docker run --rm -v "$PWD:/work" ghcr.io/btbn/ffmpeg-builds/win64-gpl-shared-9.0:latest \
#       bash /work/scripts/ffmpeg-lean.sh n9.0.2 /work/out
#
# A released version (not FFmpeg's newest code), with the image BtbN builds for that version.
#
# Kept: everything FFmpeg does by itself (it reads nearly every video, song and picture, and writes
# many of them), plus the outside libraries Corefold uses: x264 and the graphics cards' H.264
# (NVIDIA, Intel, AMD), VP9, WebP, MP3, Opus, Vorbis, dav1d for AV1 videos, zscale for HDR
# videos and libass for burnt-in subtitles. Screen and microphone recording are built in.
# Left out: the other outside libraries (x265, AV1/VVC encoders, network protocols, Vulkan and
# OpenCL filters, ...) and ffplay.
set -xe
COMMIT="${1:-n9.0.2}"
OUT="${2:-/work/out}"

cd /tmp
rm -rf ffmpeg prefix
git clone --filter=blob:none https://github.com/FFmpeg/FFmpeg.git ffmpeg
cd ffmpeg
git checkout -q "$COMMIT"
git log -1 --format="FFmpeg %H (%ci)"

./configure --prefix=/tmp/prefix --pkg-config-flags="--static" $FFBUILD_TARGET_FLAGS \
    --enable-gpl --enable-version3 --disable-debug --enable-shared --disable-static \
    --disable-w32threads --enable-pthreads \
    --disable-ffplay --disable-doc --disable-network \
    --enable-iconv --enable-zlib --enable-lzma \
    --enable-libfreetype --enable-libfribidi --enable-libharfbuzz --enable-fontconfig --enable-libass \
    --enable-libzimg --enable-libdav1d \
    --enable-libx264 --enable-libvpx --enable-libwebp \
    --enable-libmp3lame --enable-libopus --enable-libvorbis \
    --enable-amf --enable-ffnvcodec --enable-libvpl \
    --extra-cflags="$FF_CFLAGS" --extra-cxxflags="$FF_CXXFLAGS" --extra-libs="$FF_LIBS" \
    --extra-ldflags="$FF_LDFLAGS" --extra-ldexeflags="$FF_LDEXEFLAGS" \
    --cc="$CC" --cxx="$CXX" --ar="$AR" --ranlib="$RANLIB" --nm="$NM" \
    --extra-version="$(date +%Y%m%d)" || { tail -80 ffbuild/config.log; exit 1; }
make -j"$(nproc)"
make install

mkdir -p "$OUT"
cp /tmp/prefix/bin/ffmpeg.exe /tmp/prefix/bin/ffprobe.exe /tmp/prefix/bin/*.dll "$OUT"/
"${FFBUILD_CROSS_PREFIX}strip" "$OUT"/*.exe "$OUT"/*.dll || true
cp COPYING.GPLv3 "$OUT"/FFMPEG-LICENSE.txt
cp "$0" "$OUT"/FFMPEG-BUILD.sh
./ffbuild/version.sh . > "$OUT"/FFMPEG-VERSION.txt
