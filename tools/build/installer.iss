; Installer of Subtitle Voicer (TZ 1.24): tools/build.py passes /DAppVersion and /DSourceDir.
; Per user, no admin rights; the user's data in %APPDATA%\SubtitleVoicer is never touched.

#define AppName "Subtitle Voicer"
#define AppExe "SubtitleVoicer.exe"

[Setup]
AppId={{6E0B8A52-3F41-4C9B-9D7A-5B2E1C7F0A11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Subtitle Voicer
DefaultDirName={localappdata}\Programs\SubtitleVoicer
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=SubtitleVoicer-Setup-{#AppVersion}
SetupIconFile={#SourceDir}\..\..\tools\build\app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
; the 32-bit compiler runs out of memory on ~1 GB: compress in a separate 64-bit process
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; a running copy holds this mutex: the installer asks to close it first
AppMutex=SubtitleVoicer.SingleInstance
CloseApplications=yes

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; files of an older version that the new one no longer has
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; autostart is set by the app itself; removed together with the program
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "SubtitleVoicer"; Flags: dontcreatekey uninsdeletevalue

[Run]
; through Explorer, not as a child of the installer: Setup turns on Windows' RedirectionGuard and
; its children inherit it, so uv's python.exe of the character voices could not cross the
; junction to the real Python ("entity not found", os error 448)
Filename: "{win}\explorer.exe"; Parameters: """{app}\{#AppExe}"""; Description: "Запустить {#AppName}"; Flags: nowait postinstall skipifsilent
