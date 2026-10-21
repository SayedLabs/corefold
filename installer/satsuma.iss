; Satsuma installer (Inno Setup 6). Built by scripts\build.ps1.
; Installs for the current user only: no admin prompt, one page, two clicks.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef NumericVersion
  #define NumericVersion AppVersion
#endif
#define AppName "Satsuma"
#define AppExe "Satsuma.exe"

[Setup]
; Keep this AppId the same forever, so new versions upgrade the old one in place.
AppId={{D08578D1-4B3E-404B-B56C-3FDF83BAFAE2}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
VersionInfoVersion={#NumericVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Fewer pages: no welcome, folder, Start-menu or "ready" pages.
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
WizardStyle=modern
WizardImageFile=..\assets\wizard.bmp,..\assets\wizard@2x.bmp
WizardSmallImageFile=..\assets\wizard-small.bmp,..\assets\wizard-small@2x.bmp
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=..\dist\release
OutputBaseFilename=Satsuma-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
; We close a running Satsuma ourselves (see [Code]), so skip Windows' "files in use" page.
CloseApplications=no

[Messages]
SelectTasksLabel2=Choose your options, then click Install.
FinishedLabel=Satsuma is installed. Hold Shift and drag any file in File Explorer, or right-click a file and choose Convert with Satsuma.

[Tasks]
Name: "desktopicon"; Description: "Put a Satsuma icon on my desktop"
Name: "startup"; Description: "Start Satsuma when Windows starts (recommended)"

[Files]
Source: "..\dist\Satsuma\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; clear out files from an older version before copying the new ones
Type: filesandordirs; Name: "{app}\_internal"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"" --background"; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open Satsuma now"; Flags: nowait postinstall skipifsilent
; after an automatic (silent) update, start Satsuma again in the tray
Filename: "{app}\{#AppExe}"; Parameters: "--background"; Flags: nowait; Check: WizardSilent

[Code]
procedure CloseRunningSatsuma();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(300);
end;

{ The very first Satsuma was a Python script (satsuma.py + run.bat). Stop it and remove its
  start-with-Windows entry so it doesn't run next to the real app. }
procedure RemoveScriptVersion();
var
  Value: String;
  ResultCode: Integer;
begin
  if RegQueryStringValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Satsuma', Value) then
    if Pos('.py', Lowercase(Value)) > 0 then
      RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Satsuma');
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | ' +
    'Where-Object { $_.Name -like ''python*.exe'' -and $_.CommandLine -match ''satsuma'' } | ' +
    'ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseRunningSatsuma();
  RemoveScriptVersion();
  Result := '';
end;

function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  CloseRunningSatsuma();
  { remove "Convert with Satsuma" from the right-click menu }
  Exec(ExpandConstant('{app}\{#AppExe}'), '--remove-shell-menu', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    { also remove the startup entry if it was turned on from inside the app }
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Satsuma');
    if (not UninstallSilent) and
       (MsgBox('Also delete your Satsuma settings?' + #13#10#13#10 +
               'Choose No if you might install Satsuma again and want to keep them.',
               mbConfirmation, MB_YESNO) = IDYES) then
    begin
      DelTree(ExpandConstant('{userappdata}\Satsuma'), True, True, True);
      DelTree(ExpandConstant('{localappdata}\Satsuma'), True, True, True);
    end;
  end;
end;
