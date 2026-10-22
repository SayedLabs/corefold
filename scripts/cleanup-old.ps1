# Removes old copies left over from earlier downloads of Corefold (called Satsuma before 1.7.0):
#   - the first version (satsuma.py + run.bat): stops it, removes its startup entry and files
#   - older project folders (the ones with build.bat), except the one this script is in
#   - old "Satsuma-source" zip downloads
#   - optionally, the Python add-ons the first version installed
# It never touches the installed Corefold app or your settings, and asks before deleting anything.
# Only the app's own files are deleted; any other files in those folders are left alone.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$Here = (Resolve-Path (Split-Path $PSScriptRoot -Parent)).Path

function Ask($question) {
    $answer = Read-Host "$question [Y/N]"
    return $answer -match "^[Yy]"
}

function Is-Inside($path, $folder) {
    $p = $path.TrimEnd("\") + "\"
    $f = $folder.TrimEnd("\") + "\"
    return $p.StartsWith($f, [StringComparison]::OrdinalIgnoreCase)
}

function Mentions-Satsuma($file) {
    return (Test-Path $file) -and (Select-String -Path $file -Pattern "Satsuma" -Quiet)
}

Write-Host ""
Write-Host "Corefold clean-up" -ForegroundColor Cyan
Write-Host "Looking for old copies (from when the app was called Satsuma). Your installed Corefold and settings are not touched."
Write-Host ""

# ---- 1. stop the old script version and remove its startup entry
$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "python*.exe" -and $_.CommandLine -match "satsuma" }
foreach ($p in $procs) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped the old version that was running in the background."
}
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$run = (Get-ItemProperty -Path $runKey -Name "Satsuma" -ErrorAction SilentlyContinue).Satsuma
if ($run -and $run -match "\.py") {
    Remove-ItemProperty -Path $runKey -Name "Satsuma" -ErrorAction SilentlyContinue
    Write-Host "Removed the old version's start-with-Windows entry."
}

# ---- 2. find old copies
$roots = @("$env:USERPROFILE\Desktop", "$env:USERPROFILE\Downloads", "$env:USERPROFILE\Documents",
           (Split-Path $Here -Parent))
if ($env:OneDrive) { $roots += @("$env:OneDrive\Desktop", "$env:OneDrive\Documents") }
$roots = $roots | Where-Object { $_ -and (Test-Path $_) } | Sort-Object -Unique
$installed = "$env:LOCALAPPDATA\Programs\Corefold"
$installedOld = "$env:LOCALAPPDATA\Programs\Satsuma"  # removed by the Corefold installer itself

$found = @()   # each: @{ Kind; Folder; Items }
$seen = @{}
$hits = Get-ChildItem -Path $roots -Recurse -Depth 4 -File -ErrorAction SilentlyContinue `
        -Include "satsuma.py", "satsuma.iss", "Satsuma-source*.zip"
foreach ($hit in $hits) {
    $path = $hit.FullName
    if ((Is-Inside $path $Here) -or (Is-Inside $path $installed) -or (Is-Inside $path $installedOld) -or $path -match "\\\.venv\\") { continue }

    if ($hit.Name -like "Satsuma-source*.zip") {
        if (-not $seen[$path]) { $seen[$path] = 1; $found += @{ Kind = "Old download"; Folder = $hit.DirectoryName; Items = @($path) } }
        continue
    }

    if ($hit.Name -eq "satsuma.py") {
        # first version: satsuma.py with run.bat next to it, no main.py
        $dir = $hit.DirectoryName
        if (-not (Test-Path "$dir\run.bat") -or (Test-Path "$dir\main.py") -or $seen[$dir]) { continue }
        $items = @("$dir\satsuma.py", "$dir\run.bat", "$dir\__pycache__",
                   "$dir\Satsuma.spec", "$dir\dist\Satsuma.exe", "$dir\build\Satsuma")
        foreach ($f in @("$dir\README.md", "$dir\build.bat")) { if (Mentions-Satsuma $f) { $items += $f } }
        $req = "$dir\requirements.txt"
        if ((Test-Path $req) -and (Select-String -Path $req -Pattern "PySide6" -Quiet)) { $items += $req }
        $seen[$dir] = 1
        $found += @{ Kind = "First version (run.bat)"; Folder = $dir; Items = $items }
        continue
    }

    if ($hit.Name -eq "satsuma.iss") {
        # project folder: <folder>\installer\satsuma.iss with build.bat and main.py
        $dir = Split-Path $hit.DirectoryName -Parent
        if (-not ((Test-Path "$dir\build.bat") -and (Test-Path "$dir\main.py")) -or $seen[$dir]) { continue }
        if (Is-Inside $Here $dir) { continue }  # never a folder that contains this one
        $names = @("main.py", "satsuma", "scripts", "installer", "assets", ".github", ".venv", "build", "dist",
                   "vendor", "build.bat", "run-from-source.bat", "remove-old-versions.bat", "requirements.txt",
                   "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", ".gitignore", "update-repo.txt",
                   "version_info.txt", "Satsuma.spec")
        $seen[$dir] = 1
        $found += @{ Kind = "Old project folder"; Folder = $dir; Items = @($names | ForEach-Object { "$dir\$_" }) }
    }
}

# ---- 3. delete (after asking)
if ($found.Count -eq 0) {
    Write-Host "No old copies found." -ForegroundColor Green
} else {
    Write-Host "Found these old copies:" -ForegroundColor Yellow
    $i = 1
    foreach ($f in $found) {
        $where = if ($f.Kind -eq "Old download") { $f.Items[0] } else { $f.Folder }
        Write-Host ("  {0}. {1}:  {2}" -f $i, $f.Kind, $where)
        $i++
    }
    Write-Host ""
    if (Ask "Delete all of these?") {
        foreach ($f in $found) {
            foreach ($item in $f.Items) {
                if (Test-Path -LiteralPath $item) { Remove-Item -LiteralPath $item -Recurse -Force -ErrorAction SilentlyContinue }
            }
            foreach ($sub in @("dist", "build")) {  # tidy now-empty build folders
                $d = Join-Path $f.Folder $sub
                if ((Test-Path $d) -and -not (Get-ChildItem $d -Force)) { Remove-Item $d -Force }
            }
            if ($f.Kind -ne "Old download" -and (Test-Path $f.Folder) -and -not (Get-ChildItem $f.Folder -Force)) {
                Remove-Item $f.Folder -Force  # folder is empty now
            }
        }
        Write-Host "Deleted." -ForegroundColor Green
    } else {
        Write-Host "Nothing deleted."
    }
}

# ---- 4. Python add-ons from the first version (optional)
$py = $null
foreach ($cand in @("python", "py")) {
    try { & $cand -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $py = $cand; break } } catch {}
}
if ($py) {
    $has = & $py -m pip list 2>$null | Select-String -Pattern "^(PySide6|PyMuPDF|pillow.heif|zxing.cpp)\s" -Quiet
    if ($has) {
        Write-Host ""
        Write-Host "The first version also installed some Python add-ons (a few hundred MB)."
        if (Ask "Remove them? (Say N if you use Python for other things)") {
            & $py -m pip uninstall -y PySide6 PySide6-Essentials PySide6-Addons shiboken6 pymupdf pillow-heif zxing-cpp | Out-Host
        }
    }
}

Write-Host ""
Write-Host "All done." -ForegroundColor Green
