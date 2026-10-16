; Dowse's Windows installer (Inno Setup 6), built by scripts/build_installer.py, which passes the defines below.
;
; Per user, no administrator rights: the app goes to %LOCALAPPDATA%\Programs\Dowse, its data stays in
; %LOCALAPPDATA%\Dowse (the index, the model, settings: what setup downloads and indexing builds, kept by updates).
; An update is this installer run again: it asks the running app to quit, replaces the app's Python and libraries
; whole (old ones removed first, so nothing stale lingers), and starts the app again if it was running. An
; interrupted install is repaired the same way: run the installer again. Uninstalling removes the app, its Start menu
; entry, Explorer's Dowse menu and the start-with-Windows entry, and asks whether to delete the data too.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\installer\app"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef WebView2Setup
  #define WebView2Setup "..\build\cache\MicrosoftEdgeWebview2Setup.exe"
#endif
#ifndef SetupIcon
  #define SetupIcon "..\assets\brand\dowse.ico"
#endif
; brand.APP_ID: the shortcut and the running app share one taskbar identity
#define AppUserModelID "Dowse.App"

[Setup]
AppId={{8F6D2E54-6B1C-4C1D-9A3E-0D5E3C7B2A91}
AppName=Dowse
AppVersion={#AppVersion}
AppVerName=Dowse {#AppVersion}
AppPublisher=Dowse
AppComments=Search the files on your PC by what's in them.
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Dowse
VersionInfoDescription=Dowse setup
DefaultDirName={autopf}\Dowse
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
WizardStyle=modern
SetupIconFile={#SetupIcon}
UninstallDisplayIcon={app}\Dowse.exe
UninstallDisplayName=Dowse
OutputDir={#OutputDir}
OutputBaseFilename=Dowse-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
SetupMutex=DowseSetup
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[CustomMessages]
english.KeepData=Also delete Dowse's index, downloaded model and settings?%n%n%1%n%nChoose No to keep them: installing Dowse again then picks up where you left off.
french.KeepData=Supprimer aussi l'index de Dowse, le modèle téléchargé et les réglages ?%n%n%1%n%nChoisissez Non pour les garder : en réinstallant Dowse, vous retrouverez tout.
english.WebView2Failed=Dowse's window needs Microsoft Edge WebView2, which couldn't be installed (error %1). Connect to the internet and run this installer again.
french.WebView2Failed=La fenêtre de Dowse a besoin de Microsoft Edge WebView2, qui n'a pas pu être installé (erreur %1). Connectez-vous à Internet et relancez cette installation.
english.StillRunning=Dowse is still running and couldn't be closed. Quit it from its tray icon, then try again.
french.StillRunning=Dowse est encore ouvert et n'a pas pu être fermé. Quittez-le depuis son icône près de l'horloge, puis réessayez.
english.Comment=Search your files by what's in them
french.Comment=Cherchez vos fichiers par leur contenu

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An update replaces the app's Python and libraries whole: nothing from the old version stays behind.
Type: filesandordirs; Name: "{app}\Lib"
Type: filesandordirs; Name: "{app}\redist"
Type: filesandordirs; Name: "{app}\bin"
Type: files; Name: "{app}\*.pyd"
Type: files; Name: "{app}\python3*.dll"
Type: files; Name: "{app}\python3*.zip"
Type: files; Name: "{app}\python3*._pth"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#WebView2Setup}"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\Dowse"; Filename: "{app}\Dowse.exe"; Parameters: "-m dowse.desktop.launch"; \
    IconFilename: "{app}\dowse.ico"; AppUserModelID: "{#AppUserModelID}"; Comment: "{cm:Comment}"
Name: "{autodesktop}\Dowse"; Filename: "{app}\Dowse.exe"; Parameters: "-m dowse.desktop.launch"; \
    IconFilename: "{app}\dowse.ico"; AppUserModelID: "{#AppUserModelID}"; Comment: "{cm:Comment}"; Tasks: desktopicon

[Run]
Filename: "{app}\Dowse.exe"; Parameters: "-m dowse.desktop.launch"; Description: "{cm:LaunchProgram,Dowse}"; \
    Flags: nowait postinstall skipifsilent
; A silent update of a running Dowse starts it again, in the tray, as it was.
Filename: "{app}\Dowse.exe"; Parameters: "-m dowse.desktop.launch --background"; Flags: nowait; Check: WasRunning

[UninstallRun]
Filename: "{app}\Dowse.exe"; Parameters: "-m dowse.desktop.launch --quit"; Flags: runhidden waituntilterminated; \
    RunOnceId: "QuitDowse"
Filename: "{app}\python.exe"; Parameters: "-m dowse.desktop.shell --remove"; Flags: runhidden waituntilterminated; \
    RunOnceId: "RemoveExplorerMenu"

[UninstallDelete]
; Python's caches and anything else the app wrote beside itself
Type: filesandordirs; Name: "{app}"

[Code]
const
  WebView2Key = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  QuitWasRunning = 2;

var
  Running: Boolean;

function WasRunning(): Boolean;
begin
  Result := Running and WizardSilent();
end;

function HasVersion(Root: Integer; Key: String): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(Root, Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0');
end;

function WebView2Installed(): Boolean;
begin
  Result := HasVersion(HKLM, 'SOFTWARE\WOW6432Node\' + WebView2Key) or HasVersion(HKLM, 'SOFTWARE\' + WebView2Key)
    or HasVersion(HKCU, 'Software\' + WebView2Key);
end;

function QuitDowse(): Integer;
var
  Code: Integer;
begin
  Result := 0;
  if FileExists(ExpandConstant('{app}\Dowse.exe')) then
    if Exec(ExpandConstant('{app}\Dowse.exe'), '-m dowse.desktop.launch --quit', '', SW_HIDE, ewWaitUntilTerminated, Code) then
      Result := Code;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Result := '';
  Code := QuitDowse();
  if Code = 1 then
  begin
    Result := CustomMessage('StillRunning');
    exit;
  end;
  Running := Code = QuitWasRunning;
  if not WebView2Installed() then
  begin
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
    if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '', SW_HIDE,
                ewWaitUntilTerminated, Code) or ((Code <> 0) and not WebView2Installed()) then
      Result := FmtMessage(CustomMessage('WebView2Failed'), [IntToStr(Code)]);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Dowse');
    Data := ExpandConstant('{localappdata}\Dowse');
    if DirExists(Data) and not UninstallSilent() then
      if MsgBox(FmtMessage(CustomMessage('KeepData'), [Data]), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Data, True, True, True);
  end;
end;
