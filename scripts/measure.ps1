# Measures how much memory and CPU the built Corefold uses on a real Windows PC, in the moments
# people actually meet: waiting in the tray, with the window open, after converting, after the window
# is closed, and after doing the same things again and again (to catch memory that's never handed back).
# Run after scripts\build.ps1. Writes a Markdown table to the file given (and prints it).
param([string]$Out = "measure\results.md")

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null

# a clean start: no first-run window, no update check, no tour
$cfg = Join-Path $env:APPDATA "Corefold"
New-Item -ItemType Directory -Force $cfg | Out-Null
$work = Join-Path $env:TEMP "corefold-measure"
Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$work\out" | Out-Null
@{ first_run = $false; tour_done = $true; check_updates = $false; output_folder = "$work\out";
   sound = "off" } | ConvertTo-Json | Set-Content -Encoding utf8 "$cfg\settings.json"

# test files: a 12-megapixel photo and a 20-second 1080p video
$py = Join-Path $Root ".venv\Scripts\python.exe"
& $py -c @"
from PIL import Image
r = Image.effect_noise((4000, 3000), 60)
Image.merge('RGB', (r, r.rotate(180), r.transpose(Image.FLIP_LEFT_RIGHT))).save(r'$work\photo.png')
Image.radial_gradient('L').resize((1200, 900)).convert('RGB').save(r'$work\small.png')
"@
$ff = Join-Path $Root "vendor\ffmpeg\ffmpeg.exe"
& $ff -hide_banner -loglevel error -f lavfi -i "testsrc2=size=1920x1080:rate=30:duration=20" `
    -f lavfi -i "sine=frequency=440:duration=20" -c:v libx264 -preset veryfast -c:a aac -shortest "$work\clip.mp4"

foreach ($f in "photo.png", "small.png", "clip.mp4") {
    if (-not (Test-Path "$work\$f")) { throw "couldn't make the test file $f" }
}

$exe = Resolve-Path "dist\Corefold\Corefold.exe"
$env:COREFOLD_MEMLOG = "1"   # the app writes its memory to its log at each step
$log = Join-Path $env:LOCALAPPDATA "Corefold\corefold.log"
Remove-Item $log -ErrorAction SilentlyContinue

$clock = [Diagnostics.Stopwatch]::StartNew()
$p = Start-Process $exe -ArgumentList "--background" -PassThru
while (-not (Test-Path $log) -and $clock.Elapsed.TotalSeconds -lt 30) { Start-Sleep -Milliseconds 50 }
$startSeconds = [math]::Round($clock.Elapsed.TotalSeconds, 2)

$rows = New-Object System.Collections.Generic.List[string]
$cores = [Environment]::ProcessorCount
function MB($b) { [math]::Round($b / 1MB, 1) }

# memory now, and the CPU used over the next $seconds (0 = don't measure CPU)
function Snap([string]$what, [int]$seconds = 0) {
    $p.Refresh()
    if ($p.HasExited) { throw "Corefold stopped by itself during '$what' (exit code $($p.ExitCode))" }
    $cpu = ""
    if ($seconds -gt 0) {
        $before = $p.TotalProcessorTime
        Start-Sleep $seconds
        $p.Refresh()
        $used = ($p.TotalProcessorTime - $before).TotalSeconds
        $cpu = "$([math]::Round(100 * $used / $seconds, 2)) % of one core"
    }
    $row = "| $what | **$(MB $p.WorkingSet64) MB** | $(MB $p.PrivateMemorySize64) MB | $($p.Threads.Count) | $($p.HandleCount) | $cpu |"
    $rows.Add($row)
    Write-Host $row
}
function Send([string[]]$arguments) {
    Start-Process $exe -ArgumentList $arguments -Wait
}
function CloseWindow {
    $p.Refresh()
    if ($p.MainWindowHandle -ne [IntPtr]::Zero) { [void]$p.CloseMainWindow() }
}
function Converted([int]$atLeast, [int]$timeout = 120) {
    $c = [Diagnostics.Stopwatch]::StartNew()
    # new files anywhere in the test folder (next to the originals, or in the output folder)
    while ((Get-ChildItem $work -File -Recurse -ErrorAction SilentlyContinue).Count - 3 -lt $atLeast -and
           $c.Elapsed.TotalSeconds -lt $timeout) { Start-Sleep -Milliseconds 250 }
    Start-Sleep 2
}

Start-Sleep 70   # start-up work finished and memory handed back (it tidies 45 s after starting)
Snap "Waiting in the tray" 60

Send @("--page", "Home");          Start-Sleep 4; Snap "Window open: Home" 20
Send @("--page", "Converter");     Start-Sleep 3; Snap "Window open: Converter"
Send @("--page", "Apps");          Start-Sleep 15; Snap "Window open: Uninstaller (list loaded)" 20
Send @("--page", "History");       Start-Sleep 3; Snap "Window open: History"
Send @("--page", "Look");          Start-Sleep 3; Snap "Window open: Look (wheel previews)"
CloseWindow;                        Start-Sleep 5; Snap "Window just closed" 20
Start-Sleep 100;                                   Snap "Window closed for 2 minutes (freed)" 30

$n = 0
Send @("--run", "image:webp", "$work\photo.png");  $n++; Converted $n; Snap "Just converted a 12 MP photo to WebP"
Start-Sleep 6;                                                         Snap "Six seconds after converting"
Start-Sleep 60;                                                        Snap "A minute after converting"
Send @("--run", "image:cutout", "$work\small.png"); $n++; Converted $n 300; Snap "Just removed a background (AI model loaded)"
Start-Sleep 60;                                                         Snap "A minute after removing a background"
Send @("--run", "video:mp3", "$work\clip.mp4");    $n++; Converted $n; Snap "Just took the sound out of a 1080p video"
Send @("--run", "video:gif", "$work\clip.mp4");    $n++; Converted $n 300; Snap "Just made a GIF from it"
Start-Sleep 60;                                                        Snap "A minute after the video jobs"

# the same things ten times: memory should come back to where it was
for ($i = 0; $i -lt 10; $i++) {
    Send @("--page", "Home"); Start-Sleep 2
    Send @("--page", "Apps"); Start-Sleep 4
    CloseWindow; Start-Sleep 1
    Send @("--run", "image:jpg", "$work\photo.png"); $n++; Converted $n
}
Start-Sleep 5;   Snap "After 10 rounds of open, Uninstaller, close, convert"
Start-Sleep 110; Snap "Two minutes later (window freed, memory tidied)" 60

$p.Refresh()
$peak = MB $p.PeakWorkingSet64
$kids = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($p.Id)").Count
$size = [math]::Round(((Get-ChildItem "dist\Corefold" -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 0)

$text = @"
## How light Corefold is

| Moment | Memory (Task Manager) | Private memory | Threads | Handles | CPU |
|---|---|---|---|---|---|
$($rows -join "`n")

- Started (log file opened) after **$startSeconds s**
- Most memory used at any moment: $peak MB
- Processes still running under Corefold at the end: $kids
- App folder: $size MB
- Machine: GitHub windows-latest, $cores cores
"@
$text | Out-File -Encoding utf8 $Out
Write-Host $text
if (Test-Path $log) { Copy-Item $log (Join-Path (Split-Path $Out) "corefold.log") }
Stop-Process -Id $p.Id -Force
