#ifndef AppDir
  #error AppDir must name the reviewed PyInstaller application directory.
#endif
#ifndef OutputDir
  #error OutputDir must name the release output directory.
#endif
#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{D31123D7-525C-440D-BF51-48F92CFC0DC8}
AppName=PiShock Bridge
AppVersion={#AppVersion}
AppPublisher=PiShock Bridge contributors
AppPublisherURL=https://github.com/ipechman/pishock-flipper-bridge
AppSupportURL=https://github.com/ipechman/pishock-flipper-bridge/issues
AppUpdatesURL=https://github.com/ipechman/pishock-flipper-bridge/releases
DefaultDirName={localappdata}\Programs\PiShock Bridge
DefaultGroupName=PiShock Bridge
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=PiShockBridge-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\PiShockBridge.exe
SetupIconFile={#AppDir}\_internal\assets\bridge.ico
LicenseFile={#AppDir}\_internal\LICENSE
CloseApplications=yes
RestartApplications=no
SetupLogging=no

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#AppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PiShock Bridge"; Filename: "{app}\PiShockBridge.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\PiShock Bridge"; Filename: "{app}\PiShockBridge.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\PiShockBridge.exe"; Description: "Open PiShock Bridge"; Flags: nowait postinstall skipifsilent

; User profiles live separately in Local AppData. Uninstall removes only files
; this installer installed; it does not remove the user's saved device identity.
