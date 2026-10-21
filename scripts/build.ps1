# Builds Satsuma for Windows:
#   dist\Satsuma\Satsuma.exe                      (the app folder)
#   dist\release\Satsuma-Setup-<version>.exe      (installer with desktop icon)
#   dist\release\Satsuma-<version>-portable.zip   (no-install version)
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Version 1.0.0
param(
    [string]$Version = "1.0.0",
    # GitHub repository ("owner/name") the app checks for updates. Filled in automatically on
    # GitHub; for local builds put it in update-repo.txt next to build.bat.
    [string]$UpdateRepo = "",
    [string]$FfmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # makes downloads much faster
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$Version = $Version.TrimStart("v")
$NumericVersion = ($Version -split "[-+]")[0]
$parts = @($NumericVersion.Split(".") | ForEach-Object { [int]$_ })
while ($parts.Count -lt 4) { $parts += 0 }
$VerTuple = ($parts[0..3] -join ", ")
Write-Host "== Building Satsuma $Version" -ForegroundColor Cyan

# ---- Python (only needed to BUILD; people who install Satsuma don't need it)
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
"@ | Set-Content -Encoding utf8 "satsuma\_version.py"
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($VerTuple), prodvers=($VerTuple), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Satsuma'),
      StringStruct('FileDescription', 'Satsuma file converter'),
      StringStruct('FileVersion', '$Version'),
      StringStruct('InternalName', 'Satsuma'),
      StringStruct('OriginalFilename', 'Satsuma.exe'),
      StringStruct('ProductName', 'Satsuma'),
      StringStruct('ProductVersion', '$Version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Encoding utf8 "version_info.txt"

# ---- FFmpeg (bundled so users don't install anything)
$FfDir = "vendor\ffmpeg"
if (-not (Test-Path "$FfDir\ffmpeg.exe")) {
    Write-Host "== Downloading FFmpeg"
    $zip = Join-Path $env:TEMP "satsuma-ffmpeg.zip"
    $tmp = Join-Path $env:TEMP "satsuma-ffmpeg"
    Invoke-WebRequest $FfmpegUrl -OutFile $zip
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive $zip $tmp -Force
    $bin = Get-ChildItem $tmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    if (-not $bin) { throw "ffmpeg.exe not found in download" }
    New-Item -ItemType Directory -Force $FfDir | Out-Null
    Copy-Item $bin.FullName, (Join-Path $bin.DirectoryName "ffprobe.exe") $FfDir
    $lic = Get-ChildItem $tmp -Recurse -Filter "LICENSE*" | Select-Object -First 1
    if ($lic) { Copy-Item $lic.FullName "$FfDir\FFMPEG-LICENSE.txt" }
    Remove-Item $zip, $tmp -Recurse -Force
}

# ---- app icon
if (-not (Test-Path "assets\icon.ico")) { & $VPy scripts\make_icon.py }

# ---- PyInstaller (one folder: starts fast, fewer antivirus false alarms than --onefile)
Write-Host "== Packaging app"
if (Test-Path "dist\Satsuma") { Remove-Item "dist\Satsuma" -Recurse -Force }
& $VPy -m PyInstaller --noconfirm --clean --windowed --name Satsuma `
    --icon assets\icon.ico --version-file version_info.txt `
    --add-data "assets\icon.png;assets" `
    --collect-all pi_heif --collect-all zxingcpp --collect-all pypdfium2 `
    main.py
if ($LASTEXITCODE) { throw "PyInstaller failed" }
Copy-Item $FfDir "dist\Satsuma\ffmpeg" -Recurse
Copy-Item LICENSE, THIRD_PARTY_NOTICES.md "dist\Satsuma\"

# ---- portable zip
New-Item -ItemType Directory -Force "dist\release" | Out-Null
$zipOut = "dist\release\Satsuma-$Version-portable.zip"
if (Test-Path $zipOut) { Remove-Item $zipOut }
Compress-Archive "dist\Satsuma" $zipOut

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
    & $iscc "/DAppVersion=$Version" "/DNumericVersion=$NumericVersion" "installer\satsuma.iss" | Out-Host
    if ($LASTEXITCODE) { throw "Inno Setup failed" }
} else {
    Write-Warning "Inno Setup not found, so no installer was made. Install it from https://jrsoftware.org/isdl.php and run this again."
}

# ---- checksums (the app's updater checks the installer against this file)
Get-ChildItem "dist\release" -File | Where-Object { $_.Name -ne "SHA256SUMS.txt" } | ForEach-Object {
    "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name
} | Set-Content -Encoding ascii "dist\release\SHA256SUMS.txt"

Write-Host ""
Write-Host "== Done! Your files are in dist\release" -ForegroundColor Green
Get-ChildItem "dist\release" | ForEach-Object { Write-Host "   $($_.Name)" }
