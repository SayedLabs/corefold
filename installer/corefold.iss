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
AppCopyright={#Publisher}
; Setup's own file details (right-click > Properties > Details), the same as Corefold.exe's
VersionInfoVersion={#NumericVersion}
VersionInfoCompany={#Publisher}
VersionInfoCopyright={#Publisher}
VersionInfoDescription={#AppName} Setup
VersionInfoOriginalFileName=Corefold-Setup-{#AppVersion}.exe
VersionInfoProductName={#AppName}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoTextVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
; An update goes into the folder Corefold is already in (also one picked with /DIR), so its shortcuts, taskbar
; pin and start-up entry keep pointing at the same Corefold.exe. Only the old name's folder isn't kept: that
; upgrade moves to the new folder, and the old one is removed after installing (see InitializeWizard, [Code]).
UsePreviousAppDir=yes
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
; the strongest LZMA2 setting (a bigger dictionary finds more repeats across the many DLLs)
Compression=lzma2/ultra64
SolidCompression=yes
; We close a running Corefold ourselves (see [Code]), so skip Windows' "files in use" page.
CloseApplications=no
; Setup speaks the language Windows is in (English when it's none of Corefold's), without asking first
ShowLanguageDialog=no
LanguageDetectionMethod=uilanguage

[Languages]
; (the same five languages as Corefold; an older Inno Setup without Arabic leaves it out: Arabic Windows then
; gets English, as before)
Name: "en"; MessagesFile: "compiler:Default.isl"
#if FileExists(AddBackslash(CompilerPath) + "Languages\Arabic.isl")
Name: "ar"; MessagesFile: "compiler:Languages\Arabic.isl"
#endif
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"

[Messages]
en.SelectTasksLabel2=Choose your options, then click Install.
en.FinishedLabel=Corefold is installed. Hold Shift and drag any file in File Explorer, or right-click a file and choose Corefold.
#if FileExists(AddBackslash(CompilerPath) + "Languages\Arabic.isl")
ar.SelectTasksLabel2=اختر اللي تبيه، وبعدين اضغط «تثبيت».
ar.FinishedLabel=تم تثبيت كورفولد. اضغط Shift واسحب أي ملف في مستكشف الملفات، أو اضغط على الملف بالزر الأيمن واختر Corefold.
#endif
fr.SelectTasksLabel2=Choisissez vos options, puis cliquez sur Installer.
fr.FinishedLabel=Corefold est installé. Maintenez Maj et faites glisser un fichier dans l'Explorateur de fichiers, ou faites un clic droit sur un fichier et choisissez Corefold.
es.SelectTasksLabel2=Elige tus opciones y haz clic en Instalar.
es.FinishedLabel=Corefold está instalado. Mantén pulsada Mayús y arrastra cualquier archivo en el Explorador de archivos, o haz clic derecho en un archivo y elige Corefold.
tr.SelectTasksLabel2=Seçeneklerinizi belirleyin, ardından Kur'a tıklayın.
tr.FinishedLabel=Corefold yüklendi. Dosya Gezgini'nde Shift'e basılı tutup herhangi bir dosyayı sürükleyin veya bir dosyaya sağ tıklayıp Corefold'u seçin.

[CustomMessages]
en.DesktopIcon=Put a Corefold icon on my desktop
en.StartWithWindows=Start Corefold when Windows starts (recommended)
en.OpenNow=Open Corefold now
en.StillWorking=Corefold is still finishing something (a conversion, sorting files, or an app's uninstall).%n%nRetry: wait for it to finish.%nIgnore: close Corefold now (what it is doing is stopped half-way).%nAbort: stop.
en.FolderNotEmpty=The folder %1 already has other files in it. Install Corefold into an empty folder (or leave out /DIR to use its usual folder).
en.StillBusy=Corefold is still busy. Install again when it's done.
en.DeleteData=Also delete your Corefold settings and what it keeps for you?%n%nThat is: settings, the clipboard history (pinned copies too), saved archive passwords, your saved signature, cloud sign-ins and the File Organizer's Undo. Backups of removed apps and files opened from Cloud are kept either way.%n%nChoose No if you might install Corefold again and want to keep them.
#if FileExists(AddBackslash(CompilerPath) + "Languages\Arabic.isl")
ar.DesktopIcon=حط أيقونة كورفولد على سطح المكتب
ar.StartWithWindows=تشغيل كورفولد مع بدء ويندوز (ننصح فيه)
ar.OpenNow=افتح كورفولد الحين
ar.StillWorking=كورفولد لسا يخلّص شغلة (تحويل، أو ترتيب ملفات، أو إزالة برنامج).%n%nإعادة المحاولة: انتظر لين يخلّص.%nتجاهل: سكّر كورفولد الحين (اللي يسويه يوقف في نصه).%nإحباط: وقّف التثبيت.
ar.FolderNotEmpty=المجلد %1 فيه ملفات ثانية. ثبّت كورفولد في مجلد فاضي (أو شيل /DIR عشان يستخدم مجلده المعتاد).
ar.StillBusy=كورفولد لسا مشغول. ثبّته مرة ثانية لما يخلّص.
ar.DeleteData=تبي تحذف بعد إعدادات كورفولد واللي يحفظه لك؟%n%nيعني: الإعدادات، وسجل الحافظة (حتى المثبّت فيه)، وكلمات سر الأرشيفات المحفوظة، وتوقيعك المحفوظ، وتسجيلات الدخول للسحابة، والتراجع في منظّم الملفات. النسخ الاحتياطية للبرامج المحذوفة والملفات المفتوحة من السحابة تنحفظ في كل الأحوال.%n%nاختر «لا» إذا يمكن تثبّت كورفولد مرة ثانية وتبي تحتفظ فيها.
#endif
fr.DesktopIcon=Mettre une icône Corefold sur mon bureau
fr.StartWithWindows=Démarrer Corefold au démarrage de Windows (recommandé)
fr.OpenNow=Ouvrir Corefold maintenant
fr.StillWorking=Corefold termine encore quelque chose (une conversion, un rangement de fichiers ou la désinstallation d'une appli).%n%nRéessayer : attendre qu'il ait fini.%nIgnorer : fermer Corefold maintenant (ce qu'il fait s'arrête en cours de route).%nAbandonner : arrêter.
fr.FolderNotEmpty=Le dossier %1 contient déjà d'autres fichiers. Installez Corefold dans un dossier vide (ou retirez /DIR pour utiliser son dossier habituel).
fr.StillBusy=Corefold est encore occupé. Relancez l'installation quand il aura fini.
fr.DeleteData=Supprimer aussi vos réglages Corefold et ce qu'il garde pour vous ?%n%nC'est-à-dire : les réglages, l'historique du presse-papiers (y compris les éléments épinglés), les mots de passe d'archives enregistrés, votre signature enregistrée, les connexions au cloud et l'annulation du rangement de fichiers. Les sauvegardes des applis supprimées et les fichiers ouverts depuis le cloud sont gardés dans tous les cas.%n%nChoisissez Non si vous pensez réinstaller Corefold et voulez les garder.
es.DesktopIcon=Poner un icono de Corefold en mi escritorio
es.StartWithWindows=Iniciar Corefold al iniciar Windows (recomendado)
es.OpenNow=Abrir Corefold ahora
es.StillWorking=Corefold todavía está terminando algo (una conversión, ordenando archivos o desinstalando una app).%n%nReintentar: esperar a que termine.%nOmitir: cerrar Corefold ahora (lo que está haciendo se detiene a medias).%nAnular: parar.
es.FolderNotEmpty=La carpeta %1 ya tiene otros archivos. Instala Corefold en una carpeta vacía (o quita /DIR para usar su carpeta habitual).
es.StillBusy=Corefold todavía está ocupado. Vuelve a instalar cuando termine.
es.DeleteData=¿Borrar también tus ajustes de Corefold y lo que guarda para ti?%n%nEs decir: los ajustes, el historial del portapapeles (también lo fijado), las contraseñas de archivos comprimidos guardadas, tu firma guardada, las sesiones de la nube y el Deshacer del organizador de archivos. Las copias de seguridad de las apps quitadas y los archivos abiertos desde la nube se guardan en cualquier caso.%n%nElige No si quizá vuelvas a instalar Corefold y quieres conservarlos.
tr.DesktopIcon=Masaüstüme bir Corefold simgesi koy
tr.StartWithWindows=Windows başlarken Corefold'u başlat (önerilir)
tr.OpenNow=Corefold'u şimdi aç
tr.StillWorking=Corefold hâlâ bir işi bitiriyor (bir dönüştürme, dosya düzenleme ya da bir uygulamanın kaldırılması).%n%nYeniden Dene: bitmesini bekle.%nYoksay: Corefold'u şimdi kapat (yaptığı iş yarıda kalır).%nDurdur: kurulumu durdur.
tr.FolderNotEmpty=%1 klasöründe zaten başka dosyalar var. Corefold'u boş bir klasöre yükleyin (ya da her zamanki klasörünü kullanmak için /DIR'i kaldırın).
tr.StillBusy=Corefold hâlâ meşgul. İşi bitince yeniden yükleyin.
tr.DeleteData=Corefold ayarlarınızı ve sizin için sakladıklarını da silelim mi?%n%nYani: ayarlar, pano geçmişi (sabitlenenler de), kayıtlı arşiv parolaları, kayıtlı imzanız, bulut oturumları ve Dosya Düzenleyici'nin Geri Al'ı. Kaldırılan uygulamaların yedekleri ve buluttan açılan dosyalar her durumda saklanır.%n%nCorefold'u yeniden yükleyebilir ve bunları tutmak isterseniz Hayır'ı seçin.

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"
Name: "startup"; Description: "{cm:StartWithWindows}"

[Files]
; Copied next to the Corefold that's there, which keeps working until every file is in; then the two are swapped
; in a moment (SwapInNewFiles in [Code]). An update cut off half-way (power cut, Setup ended) leaves the older
; Corefold whole, never a mix of the two that can't start.
Source: "..\dist\Corefold\*"; DestDir: "{app}\_next"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; what an earlier update cut off half-way left
Type: filesandordirs; Name: "{app}\_next"
Type: filesandordirs; Name: "{app}\_previous"
; shortcuts made by versions before 1.7.0
Type: files; Name: "{autoprograms}\{#OldName}.lnk"
Type: files; Name: "{autodesktop}\{#OldName}.lnk"

[UninstallDelete]
; only Corefold's own folders: the uninstaller removes every file it installed by itself, and the folder goes
; once it's empty (never "everything in {app}": installed with /DIR into a folder holding other things, that
; would delete them too)
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\ffmpeg"
Type: filesandordirs; Name: "{app}\7zip"
Type: filesandordirs; Name: "{app}\_next"
Type: filesandordirs; Name: "{app}\_previous"
; (the files are copied in under _next, so the uninstaller's own list names them there)
Type: files; Name: "{app}\{#AppExe}"
Type: files; Name: "{app}\LICENSE"
Type: files; Name: "{app}\THIRD_PARTY_NOTICES.md"
Type: files; Name: "{app}\COMPONENT-VERSIONS.txt"
; (Setup's own working file, left in the folder when an update was cut off: never anyone else's)
Type: files; Name: "{app}\is-*.tmp"
Type: dirifempty; Name: "{app}"

[Icons]
; The same app id Corefold gives itself (app.py APP_ID): Windows then sees the shortcut, a taskbar pin made
; from it and the running Corefold as one app, so the taskbar button stays where it's pinned.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "AtomicWorks.Corefold"
; (an update leaves an icon that's there alone, so it keeps its place, and one deleted stays deleted)
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "AtomicWorks.Corefold"; \
  Tasks: desktopicon; Check: MakeDesktopIcon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"" --background"; Tasks: startup; Check: WriteStartup; \
  Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:OpenNow}"; Flags: nowait postinstall skipifsilent
; (after an automatic (silent) update Corefold is started again in the tray from [Code], once the new files are in
; place: a [Run] entry without postinstall runs before that)

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  { How long Corefold is given to finish what it's doing and close by itself (seconds). }
  CloseWait = 60;

var
  OldStartedWithWindows: Boolean;
  { Corefold was already installed: this is an update (the person's choices are kept as they are) }
  Upgrading: Boolean;
  { a desktop icon from before 1.7.0 was there (it's replaced by Corefold's) }
  HadOldDesktopIcon: Boolean;

const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{D08578D1-4B3E-404B-B56C-3FDF83BAFAE2}_is1';

function InitializeSetup(): Boolean;
begin
  Upgrading := RegValueExists(HKA, UninstallKey, 'UninstallString');
  HadOldDesktopIcon := FileExists(ExpandConstant('{autodesktop}\{#OldName}.lnk'));
  Result := True;
end;

{ An update from the old name moves to Corefold's own folder; any other update stays where Corefold is. }
procedure InitializeWizard();
begin
  if CompareText(RemoveBackslashUnlessRoot(WizardForm.DirEdit.Text), ExpandConstant('{autopf}\{#OldName}')) = 0 then
    WizardForm.DirEdit.Text := ExpandConstant('{autopf}\{#AppName}');
end;

{ On the options page of an update started by hand: ticked as things are now (not as they were first installed). }
procedure CurPageChanged(CurPageID: Integer);
var
  Value: String;
begin
  if (CurPageID = wpSelectTasks) and Upgrading then
  begin
    WizardForm.TasksList.Checked[0] := FileExists(ExpandConstant('{autodesktop}\{#AppName}.lnk'));
    WizardForm.TasksList.Checked[1] := RegQueryStringValue(HKEY_CURRENT_USER, RunKey, '{#AppName}', Value);
  end;
end;

{ The desktop icon: made on a fresh install; on an update only when it's asked for on the options page (an
  update started by hand) and isn't there yet, or to replace the old name's. An icon that's there is left
  alone, so it keeps its place on the desktop. }
function MakeDesktopIcon(): Boolean;
begin
  Result := (not Upgrading) or HadOldDesktopIcon or
            ((not WizardSilent()) and not FileExists(ExpandConstant('{autodesktop}\{#AppName}.lnk')));
end;

{ Start with Windows: written on a fresh install, or when ticked on the options page of an update started by
  hand. An automatic update never changes it, so "off" (in Corefold, Task Manager or Corefold Startup) stays off. }
function WriteStartup(): Boolean;
begin
  Result := (not Upgrading) or not WizardSilent();
end;

function SetEnvironmentVariable(lpName, lpValue: String): BOOL;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

{ PowerShell with the paths it needs passed as data (environment variables), never pasted into its script. }
function RunPowerShell(Script: String; var ResultCode: Integer): Boolean;
begin
  SetEnvironmentVariable('CF_APP', ExpandConstant('{app}\{#AppExe}'));
  SetEnvironmentVariable('CF_PIPE', 'Corefold-' + GetEnv('USERNAME'));
  SetEnvironmentVariable('CF_WAIT', IntToStr(CloseWait));
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
                 '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' + Script + '"', '',
                 SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

{ Asks the running Corefold (only the copy installed here: another program that happens to be called
  Corefold.exe is never touched) to close, the way it closes by itself: what it's doing (a conversion, sorting
  files, an uninstall) is finished first and nothing new starts. It says it heard by leaving a mark file.
  0: no Corefold from this folder runs (any more). 1: still running after CloseWait seconds.
  2: it didn't answer (a version from before 1.10 doesn't know how), so it can only be stopped. }
function AskAppToClose(): Integer;
var
  ResultCode: Integer;
begin
  Result := 2;
  SetEnvironmentVariable('CF_MARK', ExpandConstant('{localappdata}\{#AppName}\closing-for-installer'));
  if RunPowerShell(
    '$ErrorActionPreference = ''SilentlyContinue''; ' +
    'function Mine { Get-Process -Name Corefold | Where-Object { $_.Path -eq $env:CF_APP } }; ' +
    'if (-not (Mine)) { exit 0 }; ' +
    'Remove-Item -LiteralPath $env:CF_MARK -Force; ' +
    'try { $p = New-Object System.IO.Pipes.NamedPipeClientStream(''.'', $env:CF_PIPE, [System.IO.Pipes.PipeDirection]::Out); ' +
    '$p.Connect(3000); $b = [Text.Encoding]::UTF8.GetBytes(''quit''); $p.Write($b, 0, $b.Length); $p.Flush(); $p.Dispose() } catch {}; ' +
    '$heard = (Get-Date).AddSeconds(6); ' +
    'while ((Mine) -and -not (Test-Path -LiteralPath $env:CF_MARK) -and ((Get-Date) -lt $heard)) { Start-Sleep -Milliseconds 200 }; ' +
    'if (-not (Mine)) { exit 0 }; ' +
    'if (-not (Test-Path -LiteralPath $env:CF_MARK)) { exit 2 }; ' +
    '$end = (Get-Date).AddSeconds([int]$env:CF_WAIT); ' +
    'while ((Mine) -and ((Get-Date) -lt $end)) { Start-Sleep -Milliseconds 250 }; ' +
    'if (Mine) { exit 1 } else { exit 0 }', ResultCode) and (ResultCode >= 0) and (ResultCode <= 2) then
    Result := ResultCode;
end;

{ Stops the Corefold installed here at once (only when it didn't close by itself). Never by name alone: a
  portable Corefold elsewhere keeps running. If PowerShell can't run, the files it holds are asked about later. }
procedure StopApp();
var
  ResultCode: Integer;
begin
  RunPowerShell(
    '$ErrorActionPreference = ''SilentlyContinue''; ' +
    'Get-Process -Name Corefold | Where-Object { $_.Path -eq $env:CF_APP } | Stop-Process -Force; ' +
    'Start-Sleep -Milliseconds 300', ResultCode);
end;

{ The same app under its name before 1.7.0 (it can't be asked: those versions don't know how), only from its
  old folder or this one: another program that happens to be called Satsuma.exe is never touched. }
procedure StopOldApp();
var
  ResultCode: Integer;
  OldInstalled, OldHere: String;
begin
  OldInstalled := ExpandConstant('{autopf}\{#OldName}\{#OldExe}');
  OldHere := ExpandConstant('{app}\{#OldExe}');
  if not FileExists(OldInstalled) and not FileExists(OldHere) then
    Exit;
  SetEnvironmentVariable('CF_OLD_INSTALLED', OldInstalled);
  SetEnvironmentVariable('CF_OLD_HERE', OldHere);
  RunPowerShell(
    '$ErrorActionPreference = ''SilentlyContinue''; ' +
    'Get-Process -Name {#OldName} | Where-Object { ($_.Path -eq $env:CF_OLD_INSTALLED) -or ($_.Path -eq $env:CF_OLD_HERE) } | Stop-Process -Force; ' +
    'Start-Sleep -Milliseconds 300', ResultCode);
end;

{ True when Corefold isn't running any more (it closed, or the person chose to stop it). }
function CloseRunningApp(Silent: Boolean): Boolean;
var
  Answer: Integer;
begin
  Result := True;
  while True do
  begin
    Answer := AskAppToClose();
    if Answer = 0 then
      Break;
    if (Answer = 2) or Silent then
    begin
      { an older Corefold that can't be asked; or an automatic update (Corefold already waited for its work
        before starting this installer) }
      StopApp();
      Break;
    end;
    case MsgBox(CustomMessage('StillWorking'), mbError, MB_ABORTRETRYIGNORE) of
      IDIGNORE:
        begin
          StopApp();
          Break;
        end;
      IDABORT:
        begin
          Result := False;
          Exit;
        end;
    end;
  end;
  StopOldApp();
end;

{ An installation folder chosen on the command line (/DIR=) that already holds other things: Corefold would
  be mixed in with them, so it's refused. Its own folder from before (Corefold.exe in it) is fine. }
function FolderIsSafe(): Boolean;
var
  FindRec: TFindRec;
  Dir: String;
begin
  Result := True;
  Dir := ExpandConstant('{app}');
  if FileExists(Dir + '\{#AppExe}') or FileExists(Dir + '\{#OldExe}') or DirExists(Dir + '\_internal') or
     DirExists(Dir + '\_next') then  { (the last two: an update of it was cut off) }
    Exit;
  if FindFirst(Dir + '\*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        begin
          Result := False;
          Exit;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Value: String;
begin
  Result := '';
  if not FolderIsSafe() then
  begin
    Result := FmtMessage(CustomMessage('FolderNotEmpty'), [ExpandConstant('{app}')]);
    Exit;
  end;
  if not CloseRunningApp(WizardSilent()) then
  begin
    Result := CustomMessage('StillBusy');
    Exit;
  end;
  { the old version started with Windows: Corefold keeps doing that }
  OldStartedWithWindows := RegQueryStringValue(HKEY_CURRENT_USER, RunKey, '{#OldName}', Value);
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

{ A rename, tried again for a few seconds (antivirus can hold a file it's just looked at). }
function MoveWithRetry(FromPath, ToPath: String; Tries: Integer): Boolean;
var
  I: Integer;
begin
  Result := True;
  for I := 1 to Tries do
  begin
    if RenameFile(FromPath, ToPath) then
      Exit;
    Sleep(250);
  end;
  Result := False;
end;

{ One new file into its place: moved, or copied when something (antivirus) still holds it. }
procedure PutFile(FromPath, ToPath: String);
begin
  if FileExists(ToPath) then
    DeleteFile(ToPath);
  if not MoveWithRetry(FromPath, ToPath, 20) then
    if not FileCopy(FromPath, ToPath, False) then
      Log('Could not put in place: ' + ToPath);
end;

{ A new folder into its place file by file (when it can't be moved whole: a file in it is still held). }
procedure PutTree(FromDir, ToDir: String);
var
  FindRec: TFindRec;
begin
  ForceDirectories(ToDir);
  if FindFirst(FromDir + '\*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        begin
          if FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then
            PutTree(FromDir + '\' + FindRec.Name, ToDir + '\' + FindRec.Name)
          else
            PutFile(FromDir + '\' + FindRec.Name, ToDir + '\' + FindRec.Name);
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

{ The new files, all copied in under _next, take the place of the old ones: each old one is moved aside into
  _previous (or deleted, if it can't be), then the new one is moved into its place. These are renames on the
  same disk, over in a moment, so an update cut off before this point leaves the older Corefold whole.
  Corefold.exe goes last. }
procedure SwapInNewFiles();
var
  App, NextDir, PrevDir, Name: String;
  FindRec: TFindRec;
  Names: TArrayOfString;
  Count, I: Integer;
begin
  App := ExpandConstant('{app}');
  NextDir := App + '\_next';
  PrevDir := App + '\_previous';
  if not DirExists(NextDir) then
    Exit;
  Count := 0;
  if FindFirst(NextDir + '\*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') and (CompareText(FindRec.Name, '{#AppExe}') <> 0) then
        begin
          SetArrayLength(Names, Count + 1);
          Names[Count] := FindRec.Name;
          Count := Count + 1;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
  SetArrayLength(Names, Count + 1);
  Names[Count] := '{#AppExe}';
  Count := Count + 1;
  if DirExists(PrevDir) then
    DelTree(PrevDir, True, True, True);
  ForceDirectories(PrevDir);
  for I := 0 to Count - 1 do
  begin
    Name := Names[I];
    if DirExists(NextDir + '\' + Name) then
    begin
      if DirExists(App + '\' + Name) and not MoveWithRetry(App + '\' + Name, PrevDir + '\' + Name, 8) then
        DelTree(App + '\' + Name, True, True, True);
      if not RenameFile(NextDir + '\' + Name, App + '\' + Name) then
        PutTree(NextDir + '\' + Name, App + '\' + Name);
    end
    else if FileExists(NextDir + '\' + Name) then
    begin
      if FileExists(App + '\' + Name) and not MoveWithRetry(App + '\' + Name, PrevDir + '\' + Name, 8) then
        DeleteFile(App + '\' + Name);
      PutFile(NextDir + '\' + Name, App + '\' + Name);
    end;
  end;
  DelTree(PrevDir, True, True, True);
  DelTree(NextDir, True, True, True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    SwapInNewFiles();
    if WizardSilent() then  { an automatic update: Corefold back in the tray }
      Exec(ExpandConstant('{app}\{#AppExe}'), '--background', '', SW_SHOWNORMAL, ewNoWait, ResultCode);
    RemoveOldVersion();
    { an update started by hand with "Start Corefold when Windows starts" unticked: it doesn't }
    if Upgrading and (not WizardSilent()) and (not WizardIsTaskSelected('startup')) then
      RegDeleteValue(HKEY_CURRENT_USER, RunKey, '{#AppName}');
  end;
end;

function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  Result := CloseRunningApp(UninstallSilent());
  if not Result then
    Exit;
  { remove Corefold (and any entries left by older versions) from the right-click menu }
  Exec(ExpandConstant('{app}\{#AppExe}'), '--remove-shell-menu', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

{ Deletes a folder's contents except two subfolders (kept when they have something in them). }
procedure DelTreeExcept(Dir, Keep, Keep2: String);
var
  FindRec: TFindRec;
begin
  if FindFirst(Dir + '\*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') and (CompareText(FindRec.Name, Keep) <> 0) and
           (CompareText(FindRec.Name, Keep2) <> 0) then
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
  RemoveDir(Dir + '\' + Keep2);
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
       (MsgBox(CustomMessage('DeleteData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
    begin
      DelTree(ExpandConstant('{userappdata}\{#AppName}'), True, True, True);
      DelTree(ExpandConstant('{userappdata}\{#OldName}'), True, True, True);
      { backups of apps removed with Corefold, and files opened from Cloud (maybe changed and not uploaded yet),
        are the person's files: they stay }
      DelTreeExcept(ExpandConstant('{localappdata}\{#AppName}'), 'Uninstall backups', 'Opened cloud files');
      DelTreeExcept(ExpandConstant('{localappdata}\{#OldName}'), 'Uninstall backups', 'Opened cloud files');
    end;
  end;
end;
