; Inno Setup 6 script. Build with: python installer/build.py
; Per-user install (no admin): the app writes its keys, memory and log next to itself.

#ifndef AppVer
  #define AppVer "0.0.0"
#endif
; build.py passes /DAppVer from config.VERSION. AppId keeps the old name so upgrades replace earlier installs.

[Setup]
AppId=J.A.R.V.I.S
AppName=JARVIS
AppVersion={#AppVer}
AppPublisher=JARVIS
DefaultDirName={localappdata}\Programs\Jarvis
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=Jarvis-Setup
SetupIconFile=..\jarvis.ico
UninstallDisplayIcon={app}\app\jarvis.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"

[Files]
Source: "..\dist\Jarvis\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{userprograms}\JARVIS"; Filename: "{app}\Jarvis.exe"; WorkingDir: "{app}\app"; IconFilename: "{app}\app\jarvis.ico"
Name: "{userdesktop}\JARVIS"; Filename: "{app}\Jarvis.exe"; WorkingDir: "{app}\app"; IconFilename: "{app}\app\jarvis.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\Jarvis.exe"; WorkingDir: "{app}\app"; Description: "Launch JARVIS"; Flags: nowait postinstall skipifsilent

; No [UninstallDelete] for {app}: files the installer copied go away by themselves; the user's
; data (keys, memory, backups) is deleted only when they tick the box below.

[Code]
var
  WipeUserData: Boolean;

{ Asked before anything is removed. Unticked by default, so a reinstall keeps keys and memory.
  Silent uninstalls never delete user data. }
function InitializeUninstall(): Boolean;
var
  Form: TSetupForm;
  Info: TNewStaticText;
  Check: TNewCheckBox;
  OKButton, CancelButton: TNewButton;
  W: Integer;
begin
  Result := True;
  if UninstallSilent() then
    Exit;
  Form := CreateCustomForm(ScaleX(400), ScaleY(120), False, True);
  try
    Form.Caption := 'Uninstall JARVIS';

    Info := TNewStaticText.Create(Form);
    Info.Parent := Form;
    Info.AutoSize := False;
    Info.WordWrap := True;
    Info.Left := ScaleX(10);
    Info.Top := ScaleY(10);
    Info.Width := Form.ClientWidth - ScaleX(20);
    Info.Height := ScaleY(30);
    Info.Caption := 'JARVIS will be removed. Your API keys, memory and backups are kept unless you tick the box.';

    Check := TNewCheckBox.Create(Form);
    Check.Parent := Form;
    Check.Left := ScaleX(10);
    Check.Top := ScaleY(48);
    Check.Width := Form.ClientWidth - ScaleX(20);
    Check.Height := ScaleY(17);
    Check.Caption := 'Also delete my data (API keys, memory, backups)';
    Check.Checked := False;

    OKButton := TNewButton.Create(Form);
    OKButton.Parent := Form;
    OKButton.Caption := 'Uninstall';
    OKButton.Left := Form.ClientWidth - ScaleX(75 + 6 + 75 + 10);
    OKButton.Top := Form.ClientHeight - ScaleY(23 + 10);
    OKButton.Height := ScaleY(23);
    OKButton.ModalResult := mrOk;
    OKButton.Default := True;

    CancelButton := TNewButton.Create(Form);
    CancelButton.Parent := Form;
    CancelButton.Caption := 'Cancel';
    CancelButton.Left := Form.ClientWidth - ScaleX(75 + 10);
    CancelButton.Top := Form.ClientHeight - ScaleY(23 + 10);
    CancelButton.Height := ScaleY(23);
    CancelButton.ModalResult := mrCancel;
    CancelButton.Cancel := True;

    W := Form.CalculateButtonWidth([OKButton.Caption, CancelButton.Caption]);
    OKButton.Width := W;
    CancelButton.Width := W;
    OKButton.Left := Form.ClientWidth - ScaleX(10 + 6) - 2 * W;
    CancelButton.Left := Form.ClientWidth - ScaleX(10) - W;

    Form.ActiveControl := OKButton;
    Result := Form.ShowModal() = mrOk;   { Cancel or closing the window aborts the uninstall }
    WipeUserData := Check.Checked;
  finally
    Form.Free();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and WipeUserData then
    DelTree(ExpandConstant('{app}'), True, True, True);
end;
