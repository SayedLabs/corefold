; Corefold installer (Inno Setup 6). Built by scripts\build.ps1.
; Installs for the current user only: no admin prompt, one page, two clicks.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef NumericVersion
  #define NumericVersion AppVersion
#endif
#define AppName "Corefold"
#define AppExe "Corefold.exe"
#define Publisher "Atomic Works"
; The app's name before 1.7.0. Used only to upgrade and tidy up after those versions.
#define OldName "Satsuma"
#define OldExe "Satsuma.exe"

[Setup]
; Keep this AppId the same forever, so new versions upgrade the old one in place
; (versions from before 1.7.0, under the old name, have it too, so they are upgraded in place).
AppId={{D08578D1-4B3E-404B-B56C-3FDF83BAFAE2}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#Publisher}
VersionInfoVersion={#NumericVersion}
VersionInfoCompany={#Publisher}
VersionInfoProductName={#AppName}
DefaultDirName={autopf}\{#AppName}
; Always use the folder named after the app (an upgrade from the old name moves to the new folder;
; the old one is removed after installing, see [Code]).
UsePreviousAppDir=no
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
OutputBaseFilename=Corefold-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
; We close a running Corefold ourselves (see [Code]), so skip Windows' "files in use" page.
CloseApplications=no

[Messages]
SelectTasksLabel2=Choose your options, then click Install.
FinishedLabel=Corefold is installed. Hold Shift and drag any file in File Explorer, or right-click a file and choose Corefold.

[Tasks]
Name: "desktopicon"; Description: "Put a Corefold icon on my desktop"
Name: "startup"; Description: "Start Corefold when Windows starts (recommended)"

[Files]
Source: "..\dist\Corefold\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; clear out files from an older version before copying the new ones
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\ffmpeg"
; shortcuts made by versions before 1.7.0
Type: files; Name: "{autoprograms}\{#OldName}.lnk"
Type: files; Name: "{autodesktop}\{#OldName}.lnk"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"" --background"; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open Corefold now"; Flags: nowait postinstall skipifsilent
; after an automatic (silent) update, start Corefold again in the tray
Filename: "{app}\{#AppExe}"; Parameters: "--background"; Flags: nowait; Check: WizardSilent

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

var
  OldStartedWithWindows: Boolean;

procedure CloseRunningApp();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  { the same app under its name before 1.7.0 }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im {#OldExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(300);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Value: String;
begin
  CloseRunningApp();
  { the old version started with Windows: Corefold keeps doing that }
  OldStartedWithWindows := RegQueryStringValue(HKEY_CURRENT_USER, RunKey, '{#OldName}', Value);
  Result := '';
end;

{ After upgrading from the old name: take away its start-up entry (Corefold's replaces it) and its
  old program folder. Settings are moved over by the app itself the first time it starts. }
procedure RemoveOldVersion();
var
  OldDir: String;
begin
  if RegValueExists(HKEY_CURRENT_USER, RunKey, '{#OldName}') then
    RegDeleteValue(HKEY_CURRENT_USER, RunKey, '{#OldName}');
  if OldStartedWithWindows then
    RegWriteStringValue(HKEY_CURRENT_USER, RunKey, '{#AppName}', '"' + ExpandConstant('{app}\{#AppExe}') + '" --background');
  OldDir := ExpandConstant('{autopf}\{#OldName}');
  if (CompareText(OldDir, ExpandConstant('{app}')) <> 0) and FileExists(OldDir + '\{#OldExe}') then
    DelTree(OldDir, True, True, True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RemoveOldVersion();
end;

function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  CloseRunningApp();
  { remove Corefold (and any entries left by older versions) from the right-click menu }
  Exec(ExpandConstant('{app}\{#AppExe}'), '--remove-shell-menu', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := True;
end;

{ Deletes a folder's contents except one subfolder (kept when it has something in it). }
procedure DelTreeExcept(Dir, Keep: String);
var
  FindRec: TFindRec;
begin
  if FindFirst(Dir + '\*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') and (CompareText(FindRec.Name, Keep) <> 0) then
        begin
          if FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then
            DelTree(Dir + '\' + FindRec.Name, True, True, True)
          else
            DeleteFile(Dir + '\' + FindRec.Name);
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
  RemoveDir(Dir + '\' + Keep);  { only goes if it's empty }
  RemoveDir(Dir);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    { also remove the startup entry if it was turned on from inside the app }
    RegDeleteValue(HKEY_CURRENT_USER, RunKey, '{#AppName}');
    RegDeleteValue(HKEY_CURRENT_USER, RunKey, '{#OldName}');
    if (not UninstallSilent) and
       (MsgBox('Also delete your Corefold settings?' + #13#10#13#10 +
               'Choose No if you might install Corefold again and want to keep them.',
               mbConfirmation, MB_YESNO) = IDYES) then
    begin
      DelTree(ExpandConstant('{userappdata}\{#AppName}'), True, True, True);
      DelTree(ExpandConstant('{userappdata}\{#OldName}'), True, True, True);
      { backups of apps removed with Corefold are the person's files: they stay }
      DelTreeExcept(ExpandConstant('{localappdata}\{#AppName}'), 'Uninstall backups');
      DelTreeExcept(ExpandConstant('{localappdata}\{#OldName}'), 'Uninstall backups');
    end;
  end;
end;
