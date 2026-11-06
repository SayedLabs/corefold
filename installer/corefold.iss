; Rakiza Core installer (Inno Setup 6). Built by scripts\build.ps1.
; Installs for the whole PC, in Program Files: Windows asks for permission once, then one page, two clicks.
; (A folder only administrators can change: the part of Rakiza Core that runs with administrator rights can't be
; swapped by any other program the person runs.) A copy installed the old way, for one user only, is moved over.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef NumericVersion
  #define NumericVersion AppVersion
#endif
; GitHub repository ("owner/name") updates come from (build.ps1 passes it): the update task gets it
#ifndef UpdateRepo
  #define UpdateRepo ""
#endif
#define AppName "Rakiza Core"
#define AppExe "RakizaCore.exe"
#define AppProcess "RakizaCore"
#define Publisher "Rakiza Labs"
; The id Windows knows the app by (core.KEEP_ID): its taskbar pins, the start-up entry and the pipe keep the
; name earlier versions gave them, so an update keeps a pin, and start-up switched off in Task Manager stays off.
#define KeepId "Corefold"
; The app's name from 1.7.0 to 1.19. Used only to upgrade and tidy up after those versions (their own folder in
; Program Files moves to the new one).
#define PrevName "Corefold"
#define PrevExe "Corefold.exe"
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
; Setup's own file details (right-click > Properties > Details), the same as RakizaCore.exe's
VersionInfoVersion={#NumericVersion}
VersionInfoCompany={#Publisher}
VersionInfoCopyright={#Publisher}
VersionInfoDescription={#AppName} Setup
VersionInfoOriginalFileName=RakizaCore-Setup-{#AppVersion}.exe
VersionInfoProductName={#AppName}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoTextVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
; An update goes into the folder Rakiza Core is already in (also one picked with /DIR), so its shortcuts, taskbar
; pin and start-up entry keep pointing at the same folder. Only the earlier names' own folders aren't kept: that
; upgrade moves to the new folder, and the old one is removed after installing (see InitializeWizard, [Code]).
UsePreviousAppDir=yes
DefaultGroupName={#AppName}
PrivilegesRequired=admin
; (HKCU, the desktop and the Start menu of the person installing are used on purpose: their start-up entry,
; their desktop icon and taskbar pin from a copy installed for them alone, see [Code])
UsedUserAreasWarning=no
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
OutputBaseFilename=RakizaCore-Setup-{#AppVersion}
; the strongest LZMA2 setting (a bigger dictionary finds more repeats across the many DLLs)
Compression=lzma2/ultra64
SolidCompression=yes
; We close a running Rakiza Core ourselves (see [Code]), so skip Windows' "files in use" page.
CloseApplications=no
; Setup speaks the language Windows is in (English when it's none of Rakiza Core's), without asking first
ShowLanguageDialog=no
LanguageDetectionMethod=uilanguage

[Languages]
; (the same five languages as Rakiza Core; an older Inno Setup without Arabic leaves it out: Arabic Windows then
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
en.FinishedLabel=Rakiza Core is installed. Hold Shift and drag any file in File Explorer, or right-click a file and choose Rakiza Core.
#if FileExists(AddBackslash(CompilerPath) + "Languages\Arabic.isl")
ar.SelectTasksLabel2=اختر ما تريد، ثم انقر «تثبيت».
ar.FinishedLabel=تم تثبيت ركيزة كور. اضغط مع الاستمرار على Shift واسحب أي ملف في مستكشف الملفات، أو انقر بزر الماوس الأيمن على ملف واختر Rakiza Core.
#endif
fr.SelectTasksLabel2=Choisissez vos options, puis cliquez sur Installer.
fr.FinishedLabel=Rakiza Core est installé. Maintenez Maj et faites glisser un fichier dans l'Explorateur de fichiers, ou faites un clic droit sur un fichier et choisissez Rakiza Core.
es.SelectTasksLabel2=Elige tus opciones y haz clic en Instalar.
es.FinishedLabel=Rakiza Core está instalado. Mantén pulsada Mayús y arrastra cualquier archivo en el Explorador de archivos, o haz clic derecho en un archivo y elige Rakiza Core.
tr.SelectTasksLabel2=Seçeneklerinizi belirleyin, ardından Kur'a tıklayın.
tr.FinishedLabel=Rakiza Core yüklendi. Dosya Gezgini'nde Shift'e basılı tutup herhangi bir dosyayı sürükleyin veya bir dosyaya sağ tıklayıp Rakiza Core'u seçin.

[CustomMessages]
en.DesktopIcon=Put a Rakiza Core icon on my desktop
en.StartWithWindows=Start Rakiza Core when Windows starts (recommended)
en.OpenNow=Open Rakiza Core now
en.StillWorking=Rakiza Core is still finishing something (a conversion, sorting files, or an app's uninstall).%n%nRetry: wait for it to finish.%nIgnore: close Rakiza Core now (what it is doing is stopped half-way).%nAbort: stop.
en.FolderNotEmpty=The folder %1 already has other files in it. Install Rakiza Core into an empty folder (or leave out /DIR to use its usual folder).
en.StillBusy=Rakiza Core is still busy. Install again when it's done.
en.DeleteData=Also delete your Rakiza Core settings and what it keeps for you?%n%nThat is: settings, the clipboard history (pinned copies too), saved archive passwords, your saved signature, cloud sign-ins and the File Organizer's Undo. Backups of removed apps and files opened from Cloud are kept either way.%n%nChoose No if you might install Rakiza Core again and want to keep them.
#if FileExists(AddBackslash(CompilerPath) + "Languages\Arabic.isl")
ar.DesktopIcon=إضافة أيقونة ركيزة كور إلى سطح المكتب
ar.StartWithWindows=تشغيل ركيزة كور مع بدء ويندوز (مستحسن)
ar.OpenNow=فتح ركيزة كور الآن
ar.StillWorking=ما زال ركيزة كور يُنهي عملًا ما (تحويلًا، أو ترتيب ملفات، أو إزالة برنامج).%n%nإعادة المحاولة: الانتظار حتى ينتهي.%nتجاهل: إغلاق ركيزة كور الآن (يتوقف ما يفعله في منتصفه).%nإحباط: إيقاف التثبيت.
ar.FolderNotEmpty=المجلد %1 يحتوي على ملفات أخرى. ثبّت ركيزة كور في مجلد فارغ (أو احذف /DIR ليستخدم مجلده المعتاد).
ar.StillBusy=ما زال ركيزة كور مشغولًا. أعد التثبيت عندما ينتهي.
ar.DeleteData=هل تريد أيضًا حذف إعدادات ركيزة كور وما يحفظه لك؟%n%nأي: الإعدادات، وسجل الحافظة (بما فيه العناصر المثبّتة)، وكلمات مرور الأرشيفات المحفوظة، وتوقيعك المحفوظ، وتسجيلات الدخول إلى السحابة، والتراجع في منظّم الملفات. أما النسخ الاحتياطية للبرامج المُزالة والملفات المفتوحة من السحابة فتبقى في كل الأحوال.%n%nاختر «لا» إذا كنت قد تثبّت ركيزة كور مرة أخرى وتريد الاحتفاظ بها.
#endif
fr.DesktopIcon=Mettre une icône Rakiza Core sur mon bureau
fr.StartWithWindows=Démarrer Rakiza Core au démarrage de Windows (recommandé)
fr.OpenNow=Ouvrir Rakiza Core maintenant
fr.StillWorking=Rakiza Core termine encore quelque chose (une conversion, un rangement de fichiers ou la désinstallation d'une appli).%n%nRéessayer : attendre qu'il ait fini.%nIgnorer : fermer Rakiza Core maintenant (ce qu'il fait s'arrête en cours de route).%nAbandonner : arrêter.
fr.FolderNotEmpty=Le dossier %1 contient déjà d'autres fichiers. Installez Rakiza Core dans un dossier vide (ou retirez /DIR pour utiliser son dossier habituel).
fr.StillBusy=Rakiza Core est encore occupé. Relancez l'installation quand il aura fini.
fr.DeleteData=Supprimer aussi vos réglages Rakiza Core et ce qu'il garde pour vous ?%n%nC'est-à-dire : les réglages, l'historique du presse-papiers (y compris les éléments épinglés), les mots de passe d'archives enregistrés, votre signature enregistrée, les connexions au cloud et l'annulation du rangement de fichiers. Les sauvegardes des applis supprimées et les fichiers ouverts depuis le cloud sont gardés dans tous les cas.%n%nChoisissez Non si vous pensez réinstaller Rakiza Core et voulez les garder.
es.DesktopIcon=Poner un icono de Rakiza Core en mi escritorio
es.StartWithWindows=Iniciar Rakiza Core al iniciar Windows (recomendado)
es.OpenNow=Abrir Rakiza Core ahora
es.StillWorking=Rakiza Core todavía está terminando algo (una conversión, ordenando archivos o desinstalando una app).%n%nReintentar: esperar a que termine.%nOmitir: cerrar Rakiza Core ahora (lo que está haciendo se detiene a medias).%nAnular: parar.
es.FolderNotEmpty=La carpeta %1 ya tiene otros archivos. Instala Rakiza Core en una carpeta vacía (o quita /DIR para usar su carpeta habitual).
es.StillBusy=Rakiza Core todavía está ocupado. Vuelve a instalar cuando termine.
es.DeleteData=¿Borrar también tus ajustes de Rakiza Core y lo que guarda para ti?%n%nEs decir: los ajustes, el historial del portapapeles (también lo fijado), las contraseñas de archivos comprimidos guardadas, tu firma guardada, las sesiones de la nube y el Deshacer del organizador de archivos. Las copias de seguridad de las apps quitadas y los archivos abiertos desde la nube se guardan en cualquier caso.%n%nElige No si quizá vuelvas a instalar Rakiza Core y quieres conservarlos.
tr.DesktopIcon=Masaüstüme bir Rakiza Core simgesi koy
tr.StartWithWindows=Windows başlarken Rakiza Core'u başlat (önerilir)
tr.OpenNow=Rakiza Core'u şimdi aç
tr.StillWorking=Rakiza Core hâlâ bir işi bitiriyor (bir dönüştürme, dosya düzenleme ya da bir uygulamanın kaldırılması).%n%nYeniden Dene: bitmesini bekle.%nYoksay: Rakiza Core'u şimdi kapat (yaptığı iş yarıda kalır).%nDurdur: kurulumu durdur.
tr.FolderNotEmpty=%1 klasöründe zaten başka dosyalar var. Rakiza Core'u boş bir klasöre yükleyin (ya da her zamanki klasörünü kullanmak için /DIR'i kaldırın).
tr.StillBusy=Rakiza Core hâlâ meşgul. İşi bitince yeniden yükleyin.
tr.DeleteData=Rakiza Core ayarlarınızı ve sizin için sakladıklarını da silelim mi?%n%nYani: ayarlar, pano geçmişi (sabitlenenler de), kayıtlı arşiv parolaları, kayıtlı imzanız, bulut oturumları ve Dosya Düzenleyici'nin Geri Al'ı. Kaldırılan uygulamaların yedekleri ve buluttan açılan dosyalar her durumda saklanır.%n%nRakiza Core'u yeniden yükleyebilir ve bunları tutmak isterseniz Hayır'ı seçin.

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"
Name: "startup"; Description: "{cm:StartWithWindows}"

[Files]
; Copied next to the Rakiza Core that's there, which keeps working until every file is in; then the two are swapped
; in a moment (SwapInNewFiles in [Code]). An update cut off half-way (power cut, Setup ended) leaves the older
; Rakiza Core whole, never a mix of the two that can't start.
Source: "..\dist\RakizaCore\*"; DestDir: "{app}\_next"; Flags: ignoreversion recursesubdirs createallsubdirs
; Updates without Windows' question each time: run by a task as SYSTEM (only the installed copy has it, see [Code])
Source: "silent-update.ps1"; DestDir: "{app}\_next"; Flags: ignoreversion

[InstallDelete]
; what an earlier update cut off half-way left
Type: filesandordirs; Name: "{app}\_next"
Type: filesandordirs; Name: "{app}\_previous"
; shortcuts made by versions before 1.7.0
Type: files; Name: "{autoprograms}\{#OldName}.lnk"
Type: files; Name: "{autodesktop}\{#OldName}.lnk"
Type: files; Name: "{userprograms}\{#OldName}.lnk"
Type: files; Name: "{userdesktop}\{#OldName}.lnk"
; shortcuts made by versions from 1.7.0 to 1.19 (a taskbar pin is written again instead, see [Icons])
Type: files; Name: "{autoprograms}\{#PrevName}.lnk"
Type: files; Name: "{autodesktop}\{#PrevName}.lnk"
Type: files; Name: "{userprograms}\{#PrevName}.lnk"
Type: files; Name: "{userdesktop}\{#PrevName}.lnk"

[UninstallDelete]
; only Rakiza Core's own folders: the uninstaller removes every file it installed by itself, and the folder goes
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
Type: files; Name: "{app}\silent-update.ps1"
; (the update task's own folder: the installer it downloaded, what it said)
Type: filesandordirs; Name: "{app}\_update"
; (Setup's own working file, left in the folder when an update was cut off: never anyone else's)
Type: files; Name: "{app}\is-*.tmp"
Type: dirifempty; Name: "{app}"

[Icons]
; The same app id Rakiza Core gives itself (app.py APP_ID): Windows then sees the shortcut, a taskbar pin made
; from it and the running Rakiza Core as one app, so the taskbar button stays where it's pinned.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "AtomicWorks.{#KeepId}"
; (an update leaves an icon that's there alone, so it keeps its place, and one deleted stays deleted)
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "AtomicWorks.{#KeepId}"; \
  Tasks: desktopicon; Check: MakeDesktopIcon
; Moving over from a copy installed for this person alone: their own desktop icon and taskbar pin are written again
; in the same place and under the same name (so they keep their spot), pointing at the Rakiza Core installed here.
; Their own desktop icon under an earlier name is written again in its place under the new name too.
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "AtomicWorks.{#KeepId}"; \
  Check: KeepUserDesktopIcon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\{#AppName}"; \
  Filename: "{app}\{#AppExe}"; AppUserModelID: "AtomicWorks.{#KeepId}"; Check: KeepTaskbarPin
; A taskbar pin from 1.7.0 to 1.19 (under that name): written again in place, now pointing at the new folder, so
; the button stays where it's pinned and still works.
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\{#PrevName}"; \
  Filename: "{app}\{#AppExe}"; AppUserModelID: "AtomicWorks.{#KeepId}"; Check: KeepPrevPin

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "{#KeepId}"; ValueData: """{app}\{#AppExe}"" --background"; Tasks: startup; Check: WriteStartup; \
  Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:OpenNow}"; Flags: nowait postinstall skipifsilent
; (after an automatic (silent) update Rakiza Core is started again in the tray from [Code], once the new files are in
; place: a [Run] entry without postinstall runs before that)

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  { How long Rakiza Core is given to finish what it's doing and close by itself (seconds). }
  CloseWait = 60;

var
  OldStartedWithWindows: Boolean;
  { Rakiza Core was already installed: this is an update (the person's choices are kept as they are) }
  Upgrading: Boolean;
  { a desktop icon (for everyone) under an earlier name was there (it's replaced by Rakiza Core's) }
  HadOldDesktopIcon: Boolean;
  { the same on the person's own desktop: where 1.16 to 1.19 wrote it again when they moved a copy installed for
    this person alone over to the whole PC. It's written again there under the new name (2.0.0 deleted it and
    made none) }
  HadOldUserDesktopIcon: Boolean;
  { the folder of a Rakiza Core installed the old way, for this person alone (empty when there's none): it's moved
    over to the whole-PC install and then removed }
  UserCopyDir: String;
  { what that copy had: an icon on the person's own desktop, a taskbar pin }
  UserHadDesktopIcon, UserHadPin: Boolean;
  { a taskbar pin from 1.7.0 to 1.19 (under that name) was there }
  HadPrevPin: Boolean;

const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{D08578D1-4B3E-404B-B56C-3FDF83BAFAE2}_is1';

const
  PinDir = '{userappdata}\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar';

{ A Rakiza Core installed the old way, for the person installing alone (in their own AppData, registered in
  HKCU): its folder, or '' when there's none. }
function FindUserCopy(): String;
var
  Dir: String;
begin
  Result := '';
  if not RegQueryStringValue(HKEY_CURRENT_USER, UninstallKey, 'Inno Setup: App Path', Dir) then
    Dir := ExpandConstant('{localappdata}\Programs\{#AppName}');
  Dir := RemoveBackslashUnlessRoot(Dir);
  if (Dir <> '') and (FileExists(Dir + '\{#AppExe}') or FileExists(Dir + '\{#PrevExe}') or
                     FileExists(Dir + '\{#OldExe}')) then
    Result := Dir
  else if FileExists(ExpandConstant('{localappdata}\Programs\{#PrevName}\{#PrevExe}')) then
    Result := ExpandConstant('{localappdata}\Programs\{#PrevName}')
  else if FileExists(ExpandConstant('{localappdata}\Programs\{#OldName}\{#OldExe}')) then
    Result := ExpandConstant('{localappdata}\Programs\{#OldName}');
end;

function InitializeSetup(): Boolean;
begin
  Upgrading := RegValueExists(HKA, UninstallKey, 'UninstallString');
  HadOldDesktopIcon := FileExists(ExpandConstant('{autodesktop}\{#OldName}.lnk')) or
                       FileExists(ExpandConstant('{autodesktop}\{#PrevName}.lnk'));
  HadOldUserDesktopIcon := FileExists(ExpandConstant('{userdesktop}\{#OldName}.lnk')) or
                           FileExists(ExpandConstant('{userdesktop}\{#PrevName}.lnk'));
  HadPrevPin := FileExists(ExpandConstant(PinDir + '\{#PrevName}.lnk'));
  UserCopyDir := FindUserCopy();
  if UserCopyDir <> '' then
  begin
    Log('Moving over a copy installed for this user alone: ' + UserCopyDir);
    UserHadDesktopIcon := FileExists(ExpandConstant('{userdesktop}\{#AppName}.lnk')) or
                          FileExists(ExpandConstant('{userdesktop}\{#PrevName}.lnk')) or
                          FileExists(ExpandConstant('{userdesktop}\{#OldName}.lnk'));
    UserHadPin := FileExists(ExpandConstant(PinDir + '\{#AppName}.lnk')) or
                  FileExists(ExpandConstant(PinDir + '\{#OldName}.lnk'));
  end;
  Result := True;
end;

{ An update from an earlier name moves to the app's own folder; any other update stays where the app is. A copy
  installed for one user alone moves to Program Files (never kept in a folder any program could change). }
procedure InitializeWizard();
var
  Dir: String;
begin
  Dir := RemoveBackslashUnlessRoot(WizardForm.DirEdit.Text);
  if (CompareText(Dir, ExpandConstant('{autopf}\{#OldName}')) = 0) or
     (CompareText(Dir, ExpandConstant('{autopf}\{#PrevName}')) = 0) or
     ((UserCopyDir <> '') and (CompareText(Dir, UserCopyDir) = 0)) then
    WizardForm.DirEdit.Text := ExpandConstant('{autopf}\{#AppName}');
end;

{ Started by Rakiza Core's update task (as SYSTEM, see silent-update.ps1): the task starts Rakiza Core again for
  the people it was running for, so Setup never does (it would run as SYSTEM, where nobody sees it). }
function ByTask(): Boolean;
begin
  Result := ExpandConstant('{param:BYTASK|0}') = '1';
end;

{ Rakiza Core's update task (silent-update.ps1 -Mode Register / Unregister): with it, an update needs no
  "allow this app to make changes?" from Windows. A problem here only means updates ask, as before. }
procedure UpdateTask(Mode: String);
var
  ResultCode: Integer;
begin
  if not FileExists(ExpandConstant('{app}\silent-update.ps1')) then
    Exit;
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
              '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\silent-update.ps1') +
              '" -Mode ' + Mode + ' -Repo "{#UpdateRepo}"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) or
     (ResultCode <> 0) then
    Log('Update task (' + Mode + ') not done: ' + IntToStr(ResultCode));
end;

{ An update: Rakiza Core was installed already, for the whole PC or the old way for this person alone }
function IsUpdate(): Boolean;
begin
  Result := Upgrading or (UserCopyDir <> '');
end;

function KeepUserDesktopIcon(): Boolean;
begin
  Result := UserHadDesktopIcon or HadOldUserDesktopIcon;
end;

function KeepTaskbarPin(): Boolean;
begin
  Result := UserHadPin;
end;

function KeepPrevPin(): Boolean;
begin
  Result := HadPrevPin;
end;

{ On the options page of an update started by hand: ticked as things are now (not as they were first installed). }
procedure CurPageChanged(CurPageID: Integer);
var
  Value: String;
begin
  if (CurPageID = wpSelectTasks) and IsUpdate() then
  begin
    WizardForm.TasksList.Checked[0] := FileExists(ExpandConstant('{autodesktop}\{#AppName}.lnk')) or
                                       FileExists(ExpandConstant('{userdesktop}\{#AppName}.lnk')) or
                                       UserHadDesktopIcon or HadOldDesktopIcon or HadOldUserDesktopIcon;  { (an icon under an earlier name too) }
    WizardForm.TasksList.Checked[1] := RegQueryStringValue(HKEY_CURRENT_USER, RunKey, '{#KeepId}', Value);
  end;
end;

{ The desktop icon: made on a fresh install; on an update only when it's asked for on the options page (an
  update started by hand) and isn't there yet, or to replace the old name's. An icon that's there is left
  alone, so it keeps its place on the desktop. }
function MakeDesktopIcon(): Boolean;
begin
  if UserHadDesktopIcon or (HadOldUserDesktopIcon and not HadOldDesktopIcon) then
    Result := False  { (their own icon is written again instead, see [Icons]) }
  else
    Result := (not IsUpdate()) or HadOldDesktopIcon or
              ((not WizardSilent()) and not FileExists(ExpandConstant('{autodesktop}\{#AppName}.lnk')) and
               not FileExists(ExpandConstant('{userdesktop}\{#AppName}.lnk')));
end;

{ Start with Windows: written on a fresh install, or when ticked on the options page of an update started by
  hand. An automatic update never changes it, so "off" (in Rakiza Core, Task Manager or Rakiza Core Startup) stays off. }
function WriteStartup(): Boolean;
begin
  Result := (not IsUpdate()) or not WizardSilent();
end;

function SetEnvironmentVariable(lpName, lpValue: String): BOOL;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

{ PowerShell with the paths it needs passed as data (environment variables), never pasted into its script. }
function RunPowerShell(Script: String; var ResultCode: Integer): Boolean;
var
  Apps: String;
begin
  { the copies this installer replaces, '|' between them: the one installed here, one from 1.7.0 to 1.19 (in
    this folder or its own), and the copy installed the old way, for this user alone }
  Apps := ExpandConstant('{app}\{#AppExe}') + '|' + ExpandConstant('{app}\{#PrevExe}') + '|' +
          ExpandConstant('{autopf}\{#PrevName}\{#PrevExe}');
  if UserCopyDir <> '' then
    Apps := Apps + '|' + UserCopyDir + '\{#AppExe}' + '|' + UserCopyDir + '\{#PrevExe}';
  SetEnvironmentVariable('CF_APPS', Apps);
  SetEnvironmentVariable('CF_PIPE', '{#KeepId}-' + GetEnv('USERNAME'));
  SetEnvironmentVariable('CF_WAIT', IntToStr(CloseWait));
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
                 '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' + Script + '"', '',
                 SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

{ Asks the running Rakiza Core (only the copy installed here: another program that happens to be called
  RakizaCore.exe is never touched) to close, the way it closes by itself: what it's doing (a conversion, sorting
  files, an uninstall) is finished first and nothing new starts. It says it heard by leaving a mark file.
  0: no Rakiza Core from this folder runs (any more). 1: still running after CloseWait seconds.
  2: it didn't answer (a version from before 1.10 doesn't know how), so it can only be stopped. }
function AskAppToClose(): Integer;
var
  ResultCode: Integer;
begin
  Result := 2;
  SetEnvironmentVariable('CF_MARK', ExpandConstant('{localappdata}\{#AppName}\closing-for-installer'));
  { (a version from 1.7.0 to 1.19 leaves it in its own folder) }
  SetEnvironmentVariable('CF_MARK_PREV', ExpandConstant('{localappdata}\{#PrevName}\closing-for-installer'));
  if RunPowerShell(
    '$ErrorActionPreference = ''SilentlyContinue''; ' +
    'function Mine { Get-Process -Name {#AppProcess},{#PrevName} | Where-Object { $env:CF_APPS.Split([char]124) -contains $_.Path } }; ' +
    'function Heard { (Test-Path -LiteralPath $env:CF_MARK) -or (Test-Path -LiteralPath $env:CF_MARK_PREV) }; ' +
    'if (-not (Mine)) { exit 0 }; ' +
    'Remove-Item -LiteralPath $env:CF_MARK -Force; ' +
    'Remove-Item -LiteralPath $env:CF_MARK_PREV -Force; ' +
    'try { $p = New-Object System.IO.Pipes.NamedPipeClientStream(''.'', $env:CF_PIPE, [System.IO.Pipes.PipeDirection]::Out); ' +
    '$p.Connect(3000); $b = [Text.Encoding]::UTF8.GetBytes(''quit''); $p.Write($b, 0, $b.Length); $p.Flush(); $p.Dispose() } catch {}; ' +
    '$heard = (Get-Date).AddSeconds(6); ' +
    'while ((Mine) -and -not (Heard) -and ((Get-Date) -lt $heard)) { Start-Sleep -Milliseconds 200 }; ' +
    'if (-not (Mine)) { exit 0 }; ' +
    'if (-not (Heard)) { exit 2 }; ' +
    '$end = (Get-Date).AddSeconds([int]$env:CF_WAIT); ' +
    'while ((Mine) -and ((Get-Date) -lt $end)) { Start-Sleep -Milliseconds 250 }; ' +
    'if (Mine) { exit 1 } else { exit 0 }', ResultCode) and (ResultCode >= 0) and (ResultCode <= 2) then
    Result := ResultCode;
end;

{ Stops the Rakiza Core installed here at once (only when it didn't close by itself). Never by name alone: a
  portable Rakiza Core elsewhere keeps running. If PowerShell can't run, the files it holds are asked about later. }
procedure StopApp();
var
  ResultCode: Integer;
begin
  RunPowerShell(
    '$ErrorActionPreference = ''SilentlyContinue''; ' +
    'Get-Process -Name {#AppProcess},{#PrevName} | Where-Object { $env:CF_APPS.Split([char]124) -contains $_.Path } | Stop-Process -Force; ' +
    'Start-Sleep -Milliseconds 300', ResultCode);
end;

{ The same app under its name before 1.7.0 (it can't be asked: those versions don't know how), only from its
  old folder or this one: another program that happens to be called Satsuma.exe is never touched. }
procedure StopOldApp();
var
  ResultCode: Integer;
  OldInstalled, OldHere: String;
begin
  OldInstalled := ExpandConstant('{localappdata}\Programs\{#OldName}\{#OldExe}');
  if UserCopyDir <> '' then
    OldInstalled := UserCopyDir + '\{#OldExe}';
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

{ True when Rakiza Core isn't running any more (it closed, or the person chose to stop it). }
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
      { an older Rakiza Core that can't be asked; or an automatic update (Rakiza Core already waited for its work
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

{ An installation folder chosen on the command line (/DIR=) that already holds other things: Rakiza Core would
  be mixed in with them, so it's refused. Its own folder from before (RakizaCore.exe in it) is fine. }
function FolderIsSafe(): Boolean;
var
  FindRec: TFindRec;
  Dir: String;
begin
  Result := True;
  Dir := ExpandConstant('{app}');
  if FileExists(Dir + '\{#AppExe}') or FileExists(Dir + '\{#PrevExe}') or FileExists(Dir + '\{#OldExe}') or
     DirExists(Dir + '\_internal') or
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
  { the old version started with Windows: Rakiza Core keeps doing that }
  OldStartedWithWindows := RegQueryStringValue(HKEY_CURRENT_USER, RunKey, '{#OldName}', Value);
end;

{ A folder or file that is a link (junction, symbolic link) to somewhere else. }
function IsLink(Path: String): Boolean;
var
  FindRec: TFindRec;
begin
  Result := False;
  if FindFirst(Path, FindRec) then
  begin
    Result := (FindRec.Attributes and $400) <> 0;  { FILE_ATTRIBUTE_REPARSE_POINT }
    FindClose(FindRec);
  end;
end;

{ The old copy's folder is deleted (with administrator rights) only when it's the usual one in the person's own
  AppData and nothing on the way is a link: a folder named only in HKCU, which any program the person runs can
  change, never leads Setup into deleting things elsewhere. }
function UserCopyIsDeletable(Dir: String): Boolean;
var
  Programs: String;
begin
  Programs := ExpandConstant('{localappdata}\Programs');
  Result := ((CompareText(Dir, Programs + '\{#AppName}') = 0) or (CompareText(Dir, Programs + '\{#PrevName}') = 0) or
             (CompareText(Dir, Programs + '\{#OldName}') = 0)) and
            not IsLink(ExpandConstant('{localappdata}')) and not IsLink(Programs) and not IsLink(Dir);
end;

{ After upgrading from the name it had from 1.7.0 to 1.19: its start-up entry (same name) now starts the app
  installed here, its program file is taken out of a folder it stays in (one picked with /DIR), and its own
  folder in Program Files goes. Settings are moved over by the app itself the first time it starts. }
procedure RemovePrevVersion();
var
  PrevDir, Value: String;
begin
  if RegQueryStringValue(HKEY_CURRENT_USER, RunKey, '{#KeepId}', Value) and (Pos(Lowercase('{#PrevExe}'), Lowercase(Value)) > 0) then
    RegWriteStringValue(HKEY_CURRENT_USER, RunKey, '{#KeepId}', '"' + ExpandConstant('{app}\{#AppExe}') + '" --background');
  if FileExists(ExpandConstant('{app}\{#PrevExe}')) then
    DeleteFile(ExpandConstant('{app}\{#PrevExe}'));
  if FileExists(ExpandConstant('{app}\{#OldExe}')) then
    DeleteFile(ExpandConstant('{app}\{#OldExe}'));
  PrevDir := ExpandConstant('{autopf}\{#PrevName}');
  if (CompareText(PrevDir, ExpandConstant('{app}')) <> 0) and FileExists(PrevDir + '\{#PrevExe}') then
    DelTree(PrevDir, True, True, True);
end;

{ After upgrading from the old name: take away its start-up entry (Rakiza Core's replaces it) and its
  old program folder. Settings are moved over by the app itself the first time it starts. }
procedure RemoveOldVersion();
var
  OldDir: String;
begin
  if RegValueExists(HKEY_CURRENT_USER, RunKey, '{#OldName}') then
    RegDeleteValue(HKEY_CURRENT_USER, RunKey, '{#OldName}');
  if OldStartedWithWindows then
    RegWriteStringValue(HKEY_CURRENT_USER, RunKey, '{#KeepId}', '"' + ExpandConstant('{app}\{#AppExe}') + '" --background');
  OldDir := ExpandConstant('{autopf}\{#OldName}');
  if (CompareText(OldDir, ExpandConstant('{app}')) <> 0) and FileExists(OldDir + '\{#OldExe}') then
    DelTree(OldDir, True, True, True);
  { (installed for one user, the old name's folder was in their own AppData) }
  OldDir := ExpandConstant('{localappdata}\Programs\{#OldName}');
  if (CompareText(OldDir, ExpandConstant('{app}')) <> 0) and FileExists(OldDir + '\{#OldExe}') and
     UserCopyIsDeletable(OldDir) then
    DelTree(OldDir, True, True, True);
  RemovePrevVersion();
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
  same disk, over in a moment, so an update cut off before this point leaves the older Rakiza Core whole.
  RakizaCore.exe goes last. }
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

{ Deletes one of Rakiza Core's own things in the old folder (never anything else that's in there). }
procedure DelOwn(Path: String);
begin
  if DirExists(Path) then
    DelTree(Path, True, True, True)
  else if FileExists(Path) then
    DeleteFile(Path);
end;

{ After moving over from a copy installed for this person alone: its start-up entry now starts the Rakiza Core
  installed here, its Start-menu shortcut gives way to the one for everyone, and its folder (only Rakiza Core's own
  files in it) and its entry in Windows' list of installed apps are removed. Settings stay where they are. }
procedure RemoveUserCopy();
var
  Dir, Value: String;
  FindRec: TFindRec;
begin
  if UserCopyDir = '' then
    Exit;
  Dir := UserCopyDir;
  if RegQueryStringValue(HKEY_CURRENT_USER, RunKey, '{#KeepId}', Value) then
    RegWriteStringValue(HKEY_CURRENT_USER, RunKey, '{#KeepId}', '"' + ExpandConstant('{app}\{#AppExe}') + '" --background');
  DeleteFile(ExpandConstant('{userprograms}\{#AppName}.lnk'));
  DeleteFile(ExpandConstant(PinDir + '\{#OldName}.lnk'));
  RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER, UninstallKey);
  { Inno Setup marks the name in the Apps list "(All users)" while this user's own copy is still listed; that
    one is gone now, so the name is the app's own again }
  if IsAdminInstallMode() and RegQueryStringValue(HKA, UninstallKey, 'DisplayName', Value) and
     (Pos('{#AppName} (', Value) = 1) then
    RegWriteStringValue(HKA, UninstallKey, 'DisplayName', '{#AppName}');
  if CompareText(Dir, ExpandConstant('{app}')) = 0 then
    Exit;  { (never: InitializeWizard moved it to Program Files) }
  if not UserCopyIsDeletable(Dir) then
  begin
    Log('The old copy is in a folder of its own choosing, left as it is: ' + Dir);
    Exit;
  end;
  DelOwn(Dir + '\_internal');
  DelOwn(Dir + '\ffmpeg');
  DelOwn(Dir + '\7zip');
  DelOwn(Dir + '\_next');
  DelOwn(Dir + '\_previous');
  DelOwn(Dir + '\{#AppExe}');
  DelOwn(Dir + '\{#PrevExe}');
  DelOwn(Dir + '\{#OldExe}');
  DelOwn(Dir + '\LICENSE');
  DelOwn(Dir + '\THIRD_PARTY_NOTICES.md');
  DelOwn(Dir + '\COMPONENT-VERSIONS.txt');
  if FindFirst(Dir + '\unins???.*', FindRec) then
  begin
    try
      repeat
        DelOwn(Dir + '\' + FindRec.Name);
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
  if FindFirst(Dir + '\is-*.tmp', FindRec) then
  begin
    try
      repeat
        DelOwn(Dir + '\' + FindRec.Name);
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
  RemoveDir(Dir);  { (only goes if nothing else is in it) }
  Log('Moved over from the copy for this user alone; its folder is gone: ' + IntToStr(Ord(not DirExists(Dir))));
end;

{ An update cut off half-way (power cut, Setup ended) can leave an uninstaller of its own (unins000.exe/.dat):
  the next update then writes unins001. Only the one in Windows' list of apps is kept, so removing Rakiza Core
  later leaves nothing behind. }
procedure RemoveStaleUninstallers();
var
  App, Current: String;
  FindRec: TFindRec;
begin
  App := ExpandConstant('{app}');
  Current := ChangeFileExt(ExtractFileName(ExpandConstant('{uninstallexe}')), '');
  if FindFirst(App + '\unins???.*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY = 0) and
           (CompareText(ChangeFileExt(FindRec.Name, ''), Current) <> 0) then
        begin
          Log('Removing an uninstaller left by an update cut off half-way: ' + FindRec.Name);
          DeleteFile(App + '\' + FindRec.Name);
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssDone then
    RemoveStaleUninstallers();
  if CurStep = ssPostInstall then
  begin
    SwapInNewFiles();
    RemoveUserCopy();
    UpdateTask('Register');
    { an automatic update: Rakiza Core back in the tray, as the person (never with administrator rights) }
    if WizardSilent() and not ByTask() then
      ExecAsOriginalUser(ExpandConstant('{app}\{#AppExe}'), '--background', '', SW_SHOWNORMAL, ewNoWait, ResultCode);
    RemoveOldVersion();
    { an update started by hand with "Start Rakiza Core when Windows starts" unticked: it doesn't }
    if IsUpdate() and (not WizardSilent()) and (not WizardIsTaskSelected('startup')) then
      RegDeleteValue(HKEY_CURRENT_USER, RunKey, '{#KeepId}');
  end;
end;

function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  Result := CloseRunningApp(UninstallSilent());
  if not Result then
    Exit;
  UpdateTask('Unregister');
  { remove Rakiza Core (and any entries left by older versions) from the right-click menu }
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
    RegDeleteValue(HKEY_CURRENT_USER, RunKey, '{#KeepId}');
    RegDeleteValue(HKEY_CURRENT_USER, RunKey, '{#OldName}');
    if (not UninstallSilent) and
       (MsgBox(CustomMessage('DeleteData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
    begin
      DelTree(ExpandConstant('{userappdata}\{#AppName}'), True, True, True);
      DelTree(ExpandConstant('{userappdata}\{#PrevName}'), True, True, True);
      DelTree(ExpandConstant('{userappdata}\{#OldName}'), True, True, True);
      { backups of apps removed with Rakiza Core, and files opened from Cloud (maybe changed and not uploaded yet),
        are the person's files: they stay }
      DelTreeExcept(ExpandConstant('{localappdata}\{#AppName}'), 'Uninstall backups', 'Opened cloud files');
      DelTreeExcept(ExpandConstant('{localappdata}\{#PrevName}'), 'Uninstall backups', 'Opened cloud files');
      DelTreeExcept(ExpandConstant('{localappdata}\{#OldName}'), 'Uninstall backups', 'Opened cloud files');
    end;
  end;
end;
