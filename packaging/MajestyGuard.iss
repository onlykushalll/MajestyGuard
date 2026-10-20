#ifndef BundleRoot
  #define BundleRoot "..\dist\MajestyGuard"
#endif

[Setup]
AppId={{4F4B2A97-2C2B-4F4A-930E-D1CDE2DC90D3}
AppName=MajestyGuard
AppVersion=0.1.0
AppPublisher=MajestyGuard
DefaultDirName={localappdata}\Programs\MajestyGuard
DefaultGroupName=MajestyGuard
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist\installer
OutputBaseFilename=MajestyGuard-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\MajestyGuard.exe

[Files]
Source: "{#BundleRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "MajestyGuard"; ValueData: """{app}\MajestyGuard.exe"" --background"; Flags: uninsdeletevalue

[Icons]
Name: "{autoprograms}\MajestyGuard"; Filename: "{app}\MajestyGuard.exe"

[Run]
Filename: "{app}\MajestyGuard.exe"; Parameters: "--background"; Description: "Start MajestyGuard"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeUninstall(): Boolean;
var
  RemoveData: Integer;
  DataDirectory: String;
begin
  Result := True;
  DataDirectory := ExpandConstant('{localappdata}\MajestyGuard');

  if not DirExists(DataDirectory) then
    Exit;

  RemoveData := MsgBox(
    'Remove enrollment and diagnostic data?' + #13#10 + #13#10 +
    'This removes embeddings_v2.npy, biometric.mgd, settings, and local logs. ' +
    'Choose No to preserve enrollment for a future reinstall.',
    mbConfirmation,
    MB_YESNO
  );
  if RemoveData = IDYES then
    DelTree(DataDirectory, True, True, True);
end;
