; Inno Setup script for the Deadlock Analyzer installer. Built by installer/build.py, which passes
; the version (/DAppVersion=...) and first makes dist\Deadlock Analyzer\ with PyInstaller.
;
; Installs for the current user only: no admin prompt, and the app can write its settings, cache
; and screenshots next to itself.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Deadlock Analyzer"
#define AppExe "Deadlock Analyzer.exe"

[Setup]
; Identifies the app to Windows across versions, so a new installer upgrades the old one. Never change it.
AppId={{4F03D513-80DA-4ADF-9BEE-9EB4CEEAF010}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=mjaylove22
AppPublisherURL=https://github.com/mjaylove22/deadlock-analyzer
AppSupportURL=https://github.com/mjaylove22/deadlock-analyzer/issues
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
OutputDir=..\dist
OutputBaseFilename=DeadlockAnalyzer-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Offers to close the app if it's running during an upgrade
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Clear the previous version's program files first, so files it had and this one doesn't can't linger.
; Settings, cache and screenshots sit outside _internal and are kept.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Same AppUserModelID the app sets for itself, so the taskbar groups the shortcut and the window
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "DeadlockAnalyzer"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "DeadlockAnalyzer"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Files the app made while running: removing the app removes them too
Type: filesandordirs; Name: "{app}\cache"
Type: filesandordirs; Name: "{app}\screenshots"
Type: filesandordirs; Name: "{app}\logs"
Type: files; Name: "{app}\settings.json"
