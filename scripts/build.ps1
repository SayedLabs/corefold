# Builds Corefold for Windows:
#   dist\Corefold\Corefold.exe                      (the app folder)
#   dist\release\Corefold-Setup-<version>.exe      (installer with desktop icon)
#   dist\release\Corefold-<version>-portable.zip   (no-install version)
#   dist\release\ffmpeg-source-*.tar.gz           (FFmpeg's source code and build scripts, which its
#                                                  GPL licence asks us to offer with every release)
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Version 1.0.0
param(
    [string]$Version = "1.0.0",
    # GitHub repository ("owner/name") the app checks for updates. Filled in automatically on
    # GitHub; for local builds put it in update-repo.txt next to build.bat.
    [string]$UpdateRepo = "",
    [string]$FfmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip",
    # Background remover model (U2-Net "silueta", ~44 MB) from the rembg project
    [string]$ModelUrl = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/silueta.onnx",
    [string]$ModelSha256 = "75da6c8d2f8096ec743d071951be73b4a8bc7b3e51d9a6625d63644f90ffeedb",
    # Also put a copy of the installer under the name the updater of versions 1.5.3 to 1.6.0 looks
    # for (from before the app was renamed in 1.7.0), so those copies update themselves too. The
    # release workflow turns this on; nothing else needs it.
    [switch]$OldUpdaterCopy
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # makes downloads much faster
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$Version = $Version.TrimStart("v")
if ($Version -notmatch '^[0-9][0-9A-Za-z.+-]*$') { throw "Not a version number: $Version" }  # it's written into code below
$NumericVersion = ($Version -split "[-+]")[0]
$parts = @($NumericVersion.Split(".") | ForEach-Object { [int]$_ })
while ($parts.Count -lt 4) { $parts += 0 }
$VerTuple = ($parts[0..3] -join ", ")
Write-Host "== Building Corefold $Version" -ForegroundColor Cyan

# ---- Python (only needed to BUILD; people who install Corefold don't need it)
function Test-Python($cmd, $argsList) {
    try { & $cmd @argsList -c "import sys; assert sys.version_info >= (3, 10)" 2>$null; return $LASTEXITCODE -eq 0 }
    catch { return $false }
}
$PythonCandidates = @(
    @{ Exe = "python"; Args = @() },
    @{ Exe = "py"; Args = @("-3") },
    @{ Exe = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"; Args = @() },
    @{ Exe = "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"; Args = @() }
)
function Find-Python {
    foreach ($c in $PythonCandidates) { if (Test-Python $c.Exe $c.Args) { return $c } }
    return $null
}
$FoundPy = Find-Python
if (-not $FoundPy -and (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "== Installing Python (needed to build only)"
    winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements | Out-Host
    $FoundPy = Find-Python
}
if (-not $FoundPy) {
    throw "Couldn't find or install Python. Install it from python.org (tick 'Add to PATH'), then run build.bat again."
}
$PyExe = $FoundPy.Exe
$PyArgs = $FoundPy.Args

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "== Creating build environment"
    & $PyExe @PyArgs -m venv .venv
}
$VPy = Join-Path $Root ".venv\Scripts\python.exe"
& $VPy -m pip install --quiet --upgrade pip
& $VPy -m pip install --quiet -r requirements.txt "pyinstaller>=6.10,<7"
if ($LASTEXITCODE) { throw "pip install failed" }

# ---- version stamp + update source
if (-not $UpdateRepo) {
    if ($env:GITHUB_REPOSITORY) { $UpdateRepo = $env:GITHUB_REPOSITORY }
    elseif (Test-Path "update-repo.txt") { $UpdateRepo = (Get-Content "update-repo.txt" -Raw).Trim() }
}
$UpdateRepo = $UpdateRepo -replace "^https://github.com/", "" -replace "/$", ""
if ($UpdateRepo) { Write-Host "== Updates will come from github.com/$UpdateRepo" }
else { Write-Host "== No update source set (see README), so this build won't update itself" -ForegroundColor Yellow }
@"
__version__ = "$Version"
UPDATE_REPO = "$UpdateRepo"
"@ | Set-Content -Encoding utf8 "corefold\_version.py"
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($VerTuple), prodvers=($VerTuple), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Atomic Works'),
      StringStruct('FileDescription', 'Corefold'),
      StringStruct('LegalCopyright', 'Atomic Works'),
      StringStruct('FileVersion', '$Version'),
      StringStruct('InternalName', 'Corefold'),
      StringStruct('OriginalFilename', 'Corefold.exe'),
      StringStruct('ProductName', 'Corefold'),
      StringStruct('ProductVersion', '$Version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Encoding utf8 "version_info.txt"

# ---- FFmpeg (bundled so users don't install anything)
$FfDir = "vendor\ffmpeg"
# Corefold's own smaller FFmpeg (~40 MB instead of ~190 MB: only the parts Corefold uses), made by
# .github\workflows\ffmpeg-lean.yml and kept on this repository's "ffmpeg-lean" branch.
if (-not (Test-Path "$FfDir\FFMPEG-BUILD.sh")) {
    $LeanRef = "refs/remotes/origin/ffmpeg-lean"
    # (through cmd, so a missing branch or git is just a no; PowerShell 5 stops on git's message)
    cmd /c "git fetch -q --depth 1 origin +refs/heads/ffmpeg-lean:$LeanRef 2>nul"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "== Using Corefold's FFmpeg ($(git log -1 --format=%s $LeanRef))"
        $zip = Join-Path $env:TEMP "corefold-ffmpeg-lean.zip"
        git archive --format=zip -o $zip $LeanRef
        if ($LASTEXITCODE) { throw "Couldn't unpack Corefold's FFmpeg" }
        if (Test-Path $FfDir) { Remove-Item $FfDir -Recurse -Force }
        Expand-Archive $zip $FfDir -Force
        Remove-Item $zip -Force
    } else {
        $global:LASTEXITCODE = 0
        Write-Host "== Corefold's FFmpeg isn't here (no ffmpeg-lean branch); using BtbN's full build" -ForegroundColor Yellow
    }
}
# Otherwise BtbN's "shared" build: ffmpeg.exe and ffprobe.exe use the same DLLs instead of each
# carrying a full copy, which makes Corefold about 150 MB smaller on disk than the plain build.
if (-not (Test-Path "$FfDir\ffmpeg.exe") -or -not (Get-ChildItem $FfDir -Filter "avcodec*.dll" -ErrorAction SilentlyContinue)) {
    Write-Host "== Downloading FFmpeg"
    if (Test-Path $FfDir) { Remove-Item $FfDir -Recurse -Force }
    # which commit of the build scripts made this FFmpeg (offered with the source, see below)
    $ScriptsRef = ""
    try { $ScriptsRef = ((git ls-remote https://github.com/BtbN/FFmpeg-Builds refs/tags/latest) -split "\s+")[0] } catch { }
    $zip = Join-Path $env:TEMP "corefold-ffmpeg.zip"
    $tmp = Join-Path $env:TEMP "corefold-ffmpeg"
    Invoke-WebRequest $FfmpegUrl -OutFile $zip
    # check it against the checksums published next to it, so a broken or swapped download stops here
    $sumsUrl = ($FfmpegUrl -replace "/[^/]+$", "/checksums.sha256")
    $zipName = ($FfmpegUrl -split "/")[-1]
    try { $sums = (Invoke-WebRequest $sumsUrl -UseBasicParsing).Content } catch { $sums = "" }
    if ($sums -is [byte[]]) { $sums = [Text.Encoding]::UTF8.GetString($sums) }
    $want = ($sums -split "`n" | Where-Object { $_ -match "\s\*?$([regex]::Escape($zipName))\s*$" } | Select-Object -First 1)
    if (-not $want) { throw "FFmpeg's checksum list doesn't mention $zipName; not using this download" }
    $want = ($want.Trim() -split "\s+")[0].ToLower()
    $got = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $want) { Remove-Item $zip -Force; throw "FFmpeg download doesn't match its checksum" }
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive $zip $tmp -Force
    $bin = Get-ChildItem $tmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    if (-not $bin) { throw "ffmpeg.exe not found in download" }
    New-Item -ItemType Directory -Force $FfDir | Out-Null
    Copy-Item $bin.FullName, (Join-Path $bin.DirectoryName "ffprobe.exe") $FfDir
    Copy-Item (Join-Path $bin.DirectoryName "*.dll") $FfDir
    $lic = Get-ChildItem $tmp -Recurse -Filter "LICENSE*" | Select-Object -First 1
    if ($lic) { Copy-Item $lic.FullName "$FfDir\FFMPEG-LICENSE.txt" }
    if ($ScriptsRef) { Set-Content -Encoding ascii "$FfDir\BUILD-SCRIPTS-COMMIT.txt" $ScriptsRef }
    Remove-Item $zip, $tmp -Recurse -Force
}

# ---- FFmpeg's source code (GPL: whoever passes FFmpeg on must offer its source too)
# The exact FFmpeg commit is read from the bundled ffmpeg.exe; its source and the build scripts
# that made it are attached to every release next to the installer.
$FfVersionLine = (& "$FfDir\ffmpeg.exe" -hide_banner -version | Select-Object -First 1)
$FfCommit = if ($FfVersionLine -match "-g([0-9a-f]{7,40})") { $Matches[1] }
            elseif ($FfVersionLine -match "^ffmpeg version (n[0-9][0-9.]*)") { $Matches[1] }  # a release, like n9.0.2
            else { "" }
$ScriptsRef = if (Test-Path "$FfDir\BUILD-SCRIPTS-COMMIT.txt") { (Get-Content "$FfDir\BUILD-SCRIPTS-COMMIT.txt" -Raw).Trim() } else { "" }
$SourceFiles = @()
New-Item -ItemType Directory -Force "vendor\ffmpeg-source" | Out-Null
if ($FfCommit) {
    $out = "vendor\ffmpeg-source\ffmpeg-source-$FfCommit.tar.gz"
    if (-not (Test-Path $out)) {
        Write-Host "== Downloading FFmpeg source ($FfCommit)"
        try { Invoke-WebRequest "https://github.com/FFmpeg/FFmpeg/archive/$FfCommit.tar.gz" -OutFile $out }
        catch { Remove-Item $out -ErrorAction SilentlyContinue }
    }
    if (Test-Path $out) { $SourceFiles += $out }
}
if ($ScriptsRef) {
    $short = $ScriptsRef.Substring(0, [Math]::Min(10, $ScriptsRef.Length))
    $out = "vendor\ffmpeg-source\ffmpeg-source-build-scripts-$short.tar.gz"
    if (-not (Test-Path $out)) {
        try { Invoke-WebRequest "https://github.com/BtbN/FFmpeg-Builds/archive/$ScriptsRef.tar.gz" -OutFile $out }
        catch { Remove-Item $out -ErrorAction SilentlyContinue }
    }
    if (Test-Path $out) { $SourceFiles += $out }
}
if ($SourceFiles.Count -lt 2) {  # the source and the build scripts
    Write-Warning "Couldn't get FFmpeg's source code or build scripts. Don't publish this build until they are attached to the release (see THIRD_PARTY_NOTICES.md)."
}
$LeanNote = ""
if (Test-Path "$FfDir\FFMPEG-BUILD.sh") {
    # Corefold's own build: the script that chose its parts goes with the source too
    $out = "vendor\ffmpeg-source\ffmpeg-source-corefold-build.sh"
    Copy-Item "$FfDir\FFMPEG-BUILD.sh" $out -Force
    $SourceFiles += $out
    $LeanNote = "`r`nConfigured by FFMPEG-BUILD.sh in this folder (Corefold's own script), which picks the parts`r`nCorefold uses, inside the build image those scripts make."
}
@"
FFmpeg in this folder: $FfVersionLine
FFmpeg commit or release: $FfCommit
Built with the scripts at https://github.com/BtbN/FFmpeg-Builds (commit $ScriptsRef)$LeanNote

FFmpeg is free software under the GNU GPL version 3 (see FFMPEG-LICENSE.txt). Its complete source
code, and the scripts used to build it with the libraries it includes, are attached to the same
Corefold release you downloaded, as ffmpeg-source-*. If you can't get them there, ask
Atomic Works through the Corefold website and we'll send you a copy; this offer is valid for
three years after we last ship this version.
"@ | Set-Content -Encoding utf8 "$FfDir\SOURCE.txt"


# ---- 7-Zip (archives: ZIP, RAR, 7z, ISO ...). Uses an installed 7-Zip, or installs one.
# 25.00 or newer only: older ones can be tricked by a booby-trapped archive into writing outside
# the folder (CVE-2025-11001/11002) or dropping the "downloaded from the internet" mark.
$SzDir = "vendor\7zip"
$Sz7Min = [version]"25.0"
function Get-7ZipVersion($exe) {
    try { return [version](((Get-Item $exe).VersionInfo.ProductVersion -split "[^0-9.]")[0]) } catch { return [version]"0.0" }
}
function Find-7Zip {
    foreach ($d in @("$env:ProgramFiles\7-Zip", "${env:ProgramFiles(x86)}\7-Zip", "$env:LOCALAPPDATA\Programs\7-Zip")) {
        if ((Test-Path "$d\7z.exe") -and (Test-Path "$d\7z.dll") -and ((Get-7ZipVersion "$d\7z.exe") -ge $Sz7Min)) { return $d }
    }
    return $null
}
if ((Test-Path "$SzDir\7z.exe") -and ((Get-7ZipVersion "$SzDir\7z.exe") -lt $Sz7Min)) {
    Write-Host "== The saved 7-Zip is too old; getting a new one"
    Remove-Item $SzDir -Recurse -Force
}
if (-not (Test-Path "$SzDir\7z.exe")) {
    $found = Find-7Zip
    if (-not $found) {
        Write-Host "== Installing or updating 7-Zip (needed to build only)"
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            winget install --id 7zip.7zip -e --silent --accept-package-agreements --accept-source-agreements | Out-Host
            winget upgrade --id 7zip.7zip -e --silent --accept-package-agreements --accept-source-agreements | Out-Host
        }
        if (-not (Find-7Zip) -and (Get-Command choco -ErrorAction SilentlyContinue)) {
            choco upgrade 7zip -y --no-progress | Out-Null
        }
        $found = Find-7Zip
    }
    if ($found) {
        New-Item -ItemType Directory -Force $SzDir | Out-Null
        Copy-Item "$found\7z.exe", "$found\7z.dll" $SzDir
        foreach ($f in @("License.txt", "readme.txt")) { if (Test-Path "$found\$f") { Copy-Item "$found\$f" "$SzDir\7-ZIP-$f" } }
    } else {
        throw "Couldn't find or install 7-Zip $Sz7Min or newer. A release without it (or with an old one) would be unsafe or broken."
    }
}

# ---- background remover model
$ModelFile = "vendor\models\silueta.onnx"
if (-not (Test-Path $ModelFile)) {
    Write-Host "== Downloading background remover model"
    New-Item -ItemType Directory -Force "vendor\models" | Out-Null
    try {
        Invoke-WebRequest $ModelUrl -OutFile $ModelFile
        if ((Get-FileHash $ModelFile -Algorithm SHA256).Hash.ToLower() -ne $ModelSha256) { throw "checksum mismatch" }
    }
    catch { Remove-Item $ModelFile -ErrorAction SilentlyContinue; Write-Warning "Couldn't download the model ($_); 'Cut out' will show as unavailable." }
}
$ModelArgs = @()
if (Test-Path $ModelFile) { $ModelArgs = @("--add-data", "$ModelFile;models") }

# ---- app icon and sounds
if (-not (Test-Path "assets\icon.ico")) { & $VPy scripts\make_icon.py }
if (-not (Test-Path "assets\sounds\corefold.wav")) { & $VPy scripts\make_sounds.py }

# ---- PyInstaller (one folder: starts fast, fewer antivirus false alarms than --onefile)
Write-Host "== Packaging app"
if (Test-Path "dist\Corefold") { Remove-Item "dist\Corefold" -Recurse -Force }
& $VPy -m PyInstaller --noconfirm --clean --windowed --name Corefold `
    --icon assets\icon.ico --version-file version_info.txt `
    --add-data "assets\icon.png;assets" --add-data "assets\sounds;assets\sounds" @ModelArgs `
    --collect-submodules corefold --collect-all pi_heif --collect-all zxingcpp --collect-all pypdfium2 --collect-binaries onnxruntime --collect-all winrt --collect-all soundcard --collect-submodules zstandard `
    --exclude-module tkinter --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick `
    main.py
if ($LASTEXITCODE) { throw "PyInstaller failed" }
# Not needed by Corefold (software OpenGL fallback, ~20 MB)
Get-ChildItem "dist\Corefold" -Recurse -Filter "opengl32sw.dll" | Remove-Item -Force
# Parts of Qt that PyInstaller adds but Corefold never loads (~30 MB): QML and Qt Quick (pulled in
# by the on-screen keyboard plugin), OpenGL, the Direct2D window plugin, the OpenSSL that Qt would
# use for web downloads (Corefold's Qt only talks to itself; updates use Python's own SSL), and
# Qt's words in the languages Corefold doesn't speak. scripts\dllcheck.py checks nothing left
# behind needs them.
$Qt = "dist\Corefold\_internal\PySide6"
foreach ($f in @("Qt6Quick.dll", "Qt6Qml.dll", "Qt6QmlModels.dll", "Qt6QmlMeta.dll", "Qt6QmlWorkerScript.dll",
                 "Qt6VirtualKeyboard.dll", "Qt6OpenGL.dll", "plugins\platforminputcontexts",
                 "plugins\platforms\qdirect2d.dll", "plugins\tls\qopensslbackend.dll",
                 "..\libcrypto-3-x64.dll", "..\libssl-3-x64.dll")) {
    if (Test-Path "$Qt\$f") { Remove-Item "$Qt\$f" -Recurse -Force }
}
Get-ChildItem "$Qt\translations" -Filter "*.qm" -ErrorAction SilentlyContinue |
    Where-Object { $_.BaseName -notmatch "^qt(base)?_(ar|es|fr|tr)$" } | Remove-Item -Force
Copy-Item $FfDir "dist\Corefold\ffmpeg" -Recurse
if (Test-Path "$SzDir\7z.exe") { Copy-Item $SzDir "dist\Corefold\7zip" -Recurse }
Copy-Item LICENSE, THIRD_PARTY_NOTICES.md "dist\Corefold\"
# the exact version of every Python component in this build (THIRD_PARTY_NOTICES.md points here)
& $VPy -m pip freeze --all | Where-Object { $_ -notmatch "^(pip|setuptools|wheel|pyinstaller|pyinstaller-hooks-contrib|altgraph|pefile|pywin32-ctypes|packaging)==" } |
    Set-Content -Encoding utf8 "dist\Corefold\COMPONENT-VERSIONS.txt"

# ---- portable zip
New-Item -ItemType Directory -Force "dist\release" | Out-Null
$zipOut = "dist\release\Corefold-$Version-portable.zip"
if (Test-Path $zipOut) { Remove-Item $zipOut }
Compress-Archive "dist\Corefold" $zipOut
foreach ($f in $SourceFiles) { Copy-Item $f "dist\release\" }

# ---- installer (Inno Setup)
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Host "== Installing Inno Setup (makes the installer)"
    if (Get-Command choco -ErrorAction SilentlyContinue) { choco install innosetup -y --no-progress | Out-Null }
    elseif (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    }
    $iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($iscc) {
    Write-Host "== Building installer"
    & $iscc "/DAppVersion=$Version" "/DNumericVersion=$NumericVersion" "installer\corefold.iss" | Out-Host
    if ($LASTEXITCODE) { throw "Inno Setup failed" }
} else {
    Write-Warning "Inno Setup not found, so no installer was made. Install it from https://jrsoftware.org/isdl.php and run this again."
}

# ---- the same installer for copies from before 1.7.0 (see -OldUpdaterCopy above). Their updater
# only takes "<old name>-Setup-<version>.exe" listed in SHA256SUMS.txt; it runs it like Corefold's
# does, and the installer upgrades that copy to Corefold (installer\corefold.iss, [Code]).
if ($OldUpdaterCopy) {
    $setup = "dist\release\Corefold-Setup-$Version.exe"
    if (-not (Test-Path $setup)) { throw "No installer to copy for older versions" }
    Copy-Item $setup "dist\release\Satsuma-Setup-$Version.exe"
}

# ---- checksums (the app's updater checks the installer against this file)
Get-ChildItem "dist\release" -File | Where-Object { $_.Name -ne "SHA256SUMS.txt" } | ForEach-Object {
    "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name
} | Set-Content -Encoding ascii "dist\release\SHA256SUMS.txt"

Write-Host ""
Write-Host "== Done! Your files are in dist\release" -ForegroundColor Green
Get-ChildItem "dist\release" | ForEach-Object { Write-Host "   $($_.Name)" }
