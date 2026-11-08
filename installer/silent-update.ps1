# Rakiza Core's updates without Windows' "Do you want to allow this app to make changes?" question.
#
# The installer (which Windows already allowed) runs this once with -Mode Register: it adds one task to Task
# Scheduler, "\Rakiza Core\Update", that runs this script as Windows itself (SYSTEM). Everyone signed in to the PC
# may START that task, nobody but administrators may change it. Rakiza Core starts it when a new version is out.
#
# Started that way (-Mode Update), the script trusts nothing the person's programs can touch:
# - it asks GitHub itself for the newest release (over HTTPS, the certificate checked) and downloads the installer
#   and SHA256SUMS.txt itself, into {app}\_update (only administrators and Windows can write there);
# - it checks the installer's SHA-256 on that copy, and runs exactly that copy;
# - only a version newer than the one installed, only from this repository's own release downloads;
# - it runs as SYSTEM, so nothing from the person's own environment (variables, profile, modules) reaches it;
# - Rakiza Core only reads what it writes (status.txt): it gives this script nothing.
# The installer is run silently; Rakiza Core (which closed by itself) is started again for each person it was
# running for, as that person, never with administrator rights.
#
# -Mode Unregister (the uninstaller): removes the task.

param(
    [ValidateSet("Update", "Register", "Unregister")][string]$Mode = "Update",
    [string]$Repo = ""
)

# only Windows' own PowerShell modules
$env:PSModulePath = "$PSHOME\Modules"
$ErrorActionPreference = "Stop"

$AppDir = [IO.Path]::GetDirectoryName($PSCommandPath)
$AppExe = [IO.Path]::Combine($AppDir, "RakizaCore.exe")
$Work = [IO.Path]::Combine($AppDir, "_update")
$StatusFile = [IO.Path]::Combine($Work, "status.txt")
$LogFile = [IO.Path]::Combine($Work, "update.log")
$TaskFolder = "Rakiza Core"
$TaskName = "Update"
$UninstallKey = "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{D08578D1-4B3E-404B-B56C-3FDF83BAFAE2}_is1"
# SYSTEM and administrators: everything; anyone signed in: read and run it (never change what it runs)
$TaskSddl = "D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;AU)"
# (the task that starts Rakiza Core again for one person: only SYSTEM and administrators)
$StartSddl = "D:P(A;;FA;;;SY)(A;;FA;;;BA)"
# who had Rakiza Core running when it closed for the update (their SIDs): it's started again for them
$script:People = @{}
$SetupName = '^RakizaCore-Setup-(\d{1,4}(\.\d{1,6}){1,3})\.exe$'

function Esc([string]$s) { [Security.SecurityElement]::Escape($s) }

function TaskService {
    $svc = New-Object -ComObject Schedule.Service
    $svc.Connect()
    $svc
}

function TaskFolderOf($svc, [bool]$create) {
    try { return $svc.GetFolder("\$TaskFolder") } catch { }
    if ($create) { return $svc.GetFolder("\").CreateFolder($TaskFolder) }
    $null
}

function Register {
    if ($Repo -notmatch '^[\w.-]+/[\w.-]+$') { "no update source: no task"; return }
    $ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $taskArgs = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`" -Mode Update -Repo $Repo"
    $xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>Rakiza Labs</Author>
    <Description>Installs Rakiza Core's updates when Rakiza Core asks for it, without asking for permission each time.</Description>
  </RegistrationInfo>
  <Principals>
    <Principal id="System">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Priority>5</Priority>
  </Settings>
  <Actions Context="System">
    <Exec>
      <Command>$(Esc $ps)</Command>
      <Arguments>$(Esc $taskArgs)</Arguments>
    </Exec>
  </Actions>
</Task>
"@
    $svc = TaskService
    $folder = TaskFolderOf $svc $true
    # 6: make or replace; 5: the service account in the task (SYSTEM); the last one: who may do what with it
    [void]$folder.RegisterTask($TaskName, $xml, 6, "S-1-5-18", $null, 5, $TaskSddl)
    "task registered"
}

function Unregister {
    $svc = TaskService
    $folder = TaskFolderOf $svc $false
    if ($null -eq $folder) { return }
    foreach ($t in @($folder.GetTasks(1))) { try { $folder.DeleteTask($t.Name, 0) } catch { } }
    try { $svc.GetFolder("\").DeleteFolder($TaskFolder, 0) } catch { }
    "task removed"
}

# ---- the update itself

function Log([string]$text) {
    try { [IO.File]::AppendAllText($LogFile, ("{0:u} {1}`r`n" -f (Get-Date), $text)) } catch { }
}

# What Rakiza Core reads: started / downloading 0.42 / ready / installing / done <code> / failed <reason>
function Say([string]$text) {
    Log $text
    for ($i = 0; $i -lt 10; $i++) {  # (Rakiza Core may be reading it that very moment)
        try { [IO.File]::WriteAllText($StatusFile, $text); return } catch { Start-Sleep -Milliseconds 100 }
    }
}

function VersionOf([string]$text) {
    $core = ($text.Trim().TrimStart("v", "V") -split "[-+]")[0]
    $parts = @($core.Split(".") | ForEach-Object { [int]$_ })
    while ($parts.Count -lt 4) { $parts += 0 }
    New-Object Version $parts[0], $parts[1], $parts[2], $parts[3]
}

function HttpGet([string]$url, [int]$timeoutMs = 30000) {
    # (GitHub answers 502/503/504 now and then for a few seconds, or a connection drops: asked again after a
    # short wait, a few times, before the update gives up. A 4xx answer is final.)
    $waits = @(3, 10, 30)
    for ($try = 0; ; $try++) {
        $req = [Net.HttpWebRequest]::Create($url)
        $req.UserAgent = "RakizaCore-Updater"
        $req.Accept = "application/vnd.github+json"
        $req.Timeout = $timeoutMs
        $req.ReadWriteTimeout = $timeoutMs
        $req.AllowAutoRedirect = $true
        try {
            return $req.GetResponse()
        } catch {
            $e = $_.Exception
            while ($e -and -not ($e -is [Net.WebException])) { $e = $e.InnerException }
            if (-not $e) { throw }
            $code = 0
            if ($e.Response) { $code = [int]$e.Response.StatusCode; $e.Response.Close() }
            if (($code -ne 0 -and $code -lt 500 -and $code -ne 429) -or $try -ge $waits.Count) { throw }
            Log ("GitHub didn't answer ({0}); asking again in {1} s" -f $(if ($code) { $code } else { $e.Status }), $waits[$try])
            Start-Sleep -Seconds $waits[$try]
        }
    }
}

function GetText([string]$url) {
    $resp = HttpGet $url
    try {
        $reader = New-Object IO.StreamReader($resp.GetResponseStream(), [Text.Encoding]::UTF8)
        $reader.ReadToEnd()
    } finally { $resp.Close() }
}

function Download([string]$url, [string]$dest, [long]$size) {
    $resp = HttpGet $url 60000
    try {
        $total = if ($resp.ContentLength -gt 0) { $resp.ContentLength } else { $size }
        $limit = $(if ($size -gt 0) { $size } else { 1000000000 }) + 1000000
        $in = $resp.GetResponseStream()
        $out = [IO.File]::Open($dest, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try {
            $buf = New-Object byte[] 262144
            $done = 0L
            $said = [DateTime]::MinValue
            while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
                $done += $n
                if ($done -gt $limit) { throw "the download is bigger than GitHub said" }
                $out.Write($buf, 0, $n)
                if ($total -gt 0 -and ((Get-Date) - $said).TotalMilliseconds -gt 700) {
                    Say ("downloading {0:0.000}" -f ($done / $total))
                    $said = Get-Date
                }
            }
        } finally { $out.Close() }
    } finally { $resp.Close() }
}

function Sha256([string]$path) {
    $sha = [Security.Cryptography.SHA256]::Create()
    $fs = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try { ([BitConverter]::ToString($sha.ComputeHash($fs)) -replace "-", "").ToLowerInvariant() }
    finally { $fs.Close() }
}

# The copies of Rakiza Core installed here that are running, with who they run for (SID) and in which session
function RunningCopies {
    @(Get-Process -Name RakizaCore -IncludeUserName -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and ([string]::Compare($_.Path, $AppExe, $true) -eq 0) })
}

function SidOf([string]$user) {
    try { (New-Object Security.Principal.NTAccount($user)).Translate([Security.Principal.SecurityIdentifier]).Value }
    catch { $null }
}

# Rakiza Core started again as the person (in their own session, never with administrator rights): a task that
# that runs as them (kept: removing a task while what it started runs could stop it too)
function StartAs([string]$sid) {
    $xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Principals>
    <Principal id="Person">
      <UserId>$(Esc $sid)</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
  </Settings>
  <Actions Context="Person">
    <Exec>
      <Command>$(Esc $AppExe)</Command>
      <Arguments>--background</Arguments>
    </Exec>
  </Actions>
</Task>
"@
    $name = "Start " + $sid
    try {
        $svc = TaskService
        $folder = TaskFolderOf $svc $true
        $task = $folder.RegisterTask($name, $xml, 6, $sid, $null, 3, $StartSddl)
        [void]$task.Run($null)
        Log "started again for $sid"
    } catch { Log "couldn't start it again for ${sid}: $_" }
}

function Update {
    # (what an earlier run left: never anything else, only administrators and Windows can write here)
    if ([IO.Directory]::Exists($Work)) {
        foreach ($f in [IO.Directory]::GetFiles($Work)) { try { [IO.File]::Delete($f) } catch { } }
    }
    [void][IO.Directory]::CreateDirectory($Work)
    Say "started"
    Log ("running as " + [Security.Principal.WindowsIdentity]::GetCurrent().Name)
    if ($Repo -notmatch '^[\w.-]+/[\w.-]+$') { throw "no update source" }
    $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($UninstallKey)
    if ($null -eq $key) { throw "Rakiza Core isn't installed for the whole PC" }
    $installed = [string]$key.GetValue("DisplayVersion")
    $key.Close()

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $release = (GetText "https://api.github.com/repos/$Repo/releases/latest") | ConvertFrom-Json
    $tag = [string]$release.tag_name
    if ($tag -notmatch '^[vV]?\d{1,4}(\.\d{1,6}){1,3}$') { throw "unexpected version tag" }
    if ((VersionOf $tag) -le (VersionOf $installed)) { throw "nothing newer than $installed" }
    # (a renamed repository: GitHub sends the old name to the new one, the downloads are under the new name)
    $here = $Repo
    if ([string]$release.html_url -match '^https://github\.com/([\w.-]+/[\w.-]+)/releases/') { $here = $Matches[1] }
    $prefix = "https://github.com/$here/releases/download/"
    $setup = $null; $sums = $null
    foreach ($a in @($release.assets)) {
        $url = [string]$a.browser_download_url
        if (-not $url.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { continue }
        $m = [regex]::Match([string]$a.name, $SetupName)
        if ($m.Success -and (VersionOf $m.Groups[1].Value) -eq (VersionOf $tag)) { $setup = $a }
        elseif ([string]$a.name -eq "SHA256SUMS.txt") { $sums = $a }
    }
    if ($null -eq $setup -or $null -eq $sums) { throw "the release has no installer or checksums yet" }
    $name = [string]$setup.name
    $want = $null
    foreach ($line in (GetText ([string]$sums.browser_download_url)) -split "`n") {
        $parts = $line.Trim() -split "\s+"
        if ($parts.Count -ge 2 -and $parts[-1].TrimStart("*") -eq $name -and $parts[0] -match '^[0-9a-fA-F]{64}$') {
            $want = $parts[0].ToLowerInvariant()
        }
    }
    if (-not $want) { throw "the installer isn't in the checksums" }

    $file = [IO.Path]::Combine($Work, $name)
    Say "downloading 0"
    Download ([string]$setup.browser_download_url) $file ([long]$setup.size)
    if ((Sha256 $file) -ne $want) { throw "checksum mismatch" }
    Log "verified $name ($want)"

    # Rakiza Core closes by itself when it reads this (once what it's doing is done). Nothing is ever stopped
    # half-way: if no copy closes within 10 minutes, nothing is installed. Once the one that asked has closed,
    # copies other people have open get a minute more (then Setup closes them, and they're started again).
    Say "ready"
    $first = @(RunningCopies).Count
    $gone = $null
    $end = (Get-Date).AddMinutes(10)
    while ($true) {
        $running = @(RunningCopies)
        foreach ($p in $running) {
            if ($p.SessionId -gt 0 -and $p.UserName) {
                $sid = SidOf $p.UserName
                if ($sid) { $script:People[$sid] = $true }
            }
        }
        if ($running.Count -eq 0) { break }
        if ($null -eq $gone -and $running.Count -lt $first) { $gone = Get-Date }
        if ($null -ne $gone -and ((Get-Date) - $gone).TotalSeconds -gt 60) { break }
        if ((Get-Date) -gt $end) { $script:People = @{}; throw "Rakiza Core didn't close" }
        Start-Sleep -Milliseconds 500
    }

    Say "installing"
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $file
    $psi.Arguments = "/VERYSILENT /SP- /SUPPRESSMSGBOXES /NORESTART /BYTASK=1 /LOG=`"$Work\setup.log`""
    $psi.UseShellExecute = $false
    $proc = [Diagnostics.Process]::Start($psi)
    $proc.WaitForExit()
    $code = $proc.ExitCode
    Log "installer ended: $code"
    # (never left running as SYSTEM: an installer that didn't know /BYTASK would start it that way)
    foreach ($p in RunningCopies) { if ($p.SessionId -eq 0) { try { $p.Kill() } catch { } } }
    try { [IO.File]::Delete($file) } catch { }
    foreach ($sid in @($script:People.Keys)) { StartAs $sid }
    Say "done $code"
}

switch ($Mode) {
    "Register" { Register }
    "Unregister" { Unregister }
    "Update" {
        try { Update }
        catch {
            $why = ($_.Exception.Message -replace "[\r\n]+", " ")
            Say "failed $why"
            # Rakiza Core may have closed already (it read "ready"): it's started again either way
            if ((RunningCopies).Count -eq 0) { foreach ($sid in @($script:People.Keys)) { StartAs $sid } }
            exit 1
        }
    }
}
