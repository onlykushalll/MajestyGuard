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
AppMutex=Global\MajestyGuardDaemon,Global\MajestyGuardMonitor
CloseApplications=no
RestartApplications=no
OutputDir=..\dist\installer
OutputBaseFilename=MajestyGuard-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
#ifdef BrandIcon
SetupIconFile={#BrandIcon}
#endif
UninstallDisplayIcon={app}\MajestyGuard\MajestyGuard.exe

[Files]
Source: "{#BundleRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\MajestyGuard"; Filename: "{app}\MajestyGuard\MajestyGuard.exe"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  RemoveData: Integer;
  DataDirectory: String;
begin
  if (CurUninstallStep <> usPostUninstall) or UninstallSilent then
    Exit;
  DataDirectory := ExpandConstant('{localappdata}\MajestyGuard');

  if not DirExists(DataDirectory) then
    Exit;

  RemoveData := MsgBox(
    'Remove enrollment and diagnostic data?' + #13#10 + #13#10 +
    'This removes embeddings_v2.npy, biometric.mgd, settings, and local logs. ' +
    'Choose No to preserve enrollment for a future reinstall.',
    mbConfirmation,
    MB_YESNO or MB_DEFBUTTON2
  );
  if RemoveData = IDYES then
    DelTree(DataDirectory, True, True, True);
end;
