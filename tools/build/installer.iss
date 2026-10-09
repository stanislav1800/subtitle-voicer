; Installer of Subtitle Voicer (TZ 1.24): tools/build.py passes /DAppVersion and /DSourceDir.
; Per user, no admin rights; the user's data in %APPDATA%\SubtitleVoicer is removed on uninstall
; only if the user says so (default: kept).

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

[UninstallDelete]
; whatever appeared in the program folder after the install (e.g. __pycache__)
Type: filesandordirs; Name: "{app}"

[Run]
; through Explorer, not as a child of the installer: Setup turns on Windows' RedirectionGuard and
; its children inherit it, so uv's python.exe of the character voices could not cross the
; junction to the real Python ("entity not found", os error 448)
Filename: "{win}\explorer.exe"; Parameters: """{app}\{#AppExe}"""; Description: "Запустить {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
// Uninstall: one window of our own with the checkbox «also remove the data» (off by default) —
// settings, profiles, downloaded models and character voices; a reinstall finds what is kept.
// Windows runs the uninstaller with /SILENT /ASK (written after the install), so Inno Setup's
// own «Are you sure?» is skipped and this window takes its place. A plain silent uninstall
// (/SILENT or /VERYSILENT alone, e.g. by a script) asks nothing and keeps the data.
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6E0B8A52-3F41-4C9B-9D7A-5B2E1C7F0A11}_is1';

var
  DeleteData: Boolean;

function AskOnUninstall(): Boolean;
var
  Form: TSetupForm;
  Text: TNewStaticText;
  Check: TNewCheckBox;
  Path: TNewStaticText;
  Ok, Cancel: TNewButton;
  Data: String;
begin
  Data := ExpandConstant('{userappdata}\SubtitleVoicer');
  Form := CreateCustomForm(ScaleX(440), ScaleY(170), False, True);
  try
    Form.Caption := 'Удаление {#AppName}';

    Text := TNewStaticText.Create(Form);
    Text.Parent := Form;
    Text.Left := ScaleX(16);
    Text.Top := ScaleY(16);
    Text.Width := Form.ClientWidth - ScaleX(32);
    Text.WordWrap := True;
    Text.Caption := 'Программа {#AppName} будет удалена с компьютера.';

    Check := TNewCheckBox.Create(Form);
    Check.Parent := Form;
    Check.Left := ScaleX(16);
    Check.Top := ScaleY(48);
    Check.Width := Form.ClientWidth - ScaleX(32);
    Check.Height := ScaleY(20);
    Check.Caption := 'Также удалить настройки, профили и скачанные модели';
    Check.Checked := False;
    Check.Enabled := DirExists(Data);

    Path := TNewStaticText.Create(Form);
    Path.Parent := Form;
    Path.Left := ScaleX(34);
    Path.Top := ScaleY(70);
    Path.Width := Form.ClientWidth - ScaleX(50);
    Path.WordWrap := True;
    Path.Caption := Data + #13#10 + 'Если оставить, после повторной установки всё будет на месте.';

    Ok := TNewButton.Create(Form);
    Ok.Parent := Form;
    Ok.Caption := 'Удалить';
    Ok.Width := ScaleX(90);
    Ok.Height := ScaleY(26);
    Ok.Left := Form.ClientWidth - ScaleX(16 + 90 + 8 + 90);
    Ok.Top := Form.ClientHeight - ScaleY(16 + 26);
    Ok.ModalResult := mrOk;

    Cancel := TNewButton.Create(Form);
    Cancel.Parent := Form;
    Cancel.Caption := 'Отмена';
    Cancel.Width := ScaleX(90);
    Cancel.Height := ScaleY(26);
    Cancel.Left := Form.ClientWidth - ScaleX(16 + 90);
    Cancel.Top := Ok.Top;
    Cancel.ModalResult := mrCancel;
    Cancel.Cancel := True;
    Form.ActiveControl := Cancel;

    Result := Form.ShowModal() = mrOk;
    DeleteData := Result and Check.Checked;
  finally
    Form.Free();
  end;
end;

function HasParam(Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
      Result := True;
end;

function InitializeUninstall(): Boolean;
begin
  DeleteData := False;
  Result := True;
  if HasParam('/ASK') or not UninstallSilent then
    Result := AskOnUninstall();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and DeleteData then
    DelTree(ExpandConstant('{userappdata}\SubtitleVoicer'), True, True, True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // the uninstall entry is written by now: Windows runs our window instead of «Are you sure?»
  if CurStep = ssPostInstall then
    RegWriteStringValue(HKA, UninstallKey, 'UninstallString',
      '"' + ExpandConstant('{uninstallexe}') + '" /SILENT /ASK');
end;
