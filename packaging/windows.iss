#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif
[Setup]
AppId={{904AB196-A338-49D3-98D4-E038923F42A0}
AppName=Plex Presence
AppVersion={#AppVersion}
AppPublisher=sixtenths
AppPublisherURL=https://github.com/sixtenths/plex-presence
DefaultDirName={localappdata}\Programs\Plex Presence
DefaultGroupName=Plex Presence
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=PlexPresence-Setup-windows-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\plex_presence\assets\app.ico
UninstallDisplayIcon={app}\PlexPresence.exe
CloseApplications=yes
RestartApplications=no
LicenseFile=..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start Plex Presence in the background when I sign in"; GroupDescription: "Background operation:"

[Files]
Source: "..\dist\PlexPresence\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Plex Presence"; Filename: "{app}\PlexPresence.exe"
Name: "{autodesktop}\Plex Presence"; Filename: "{app}\PlexPresence.exe"; Tasks: desktopicon
Name: "{userstartup}\Plex Presence"; Filename: "{app}\PlexPresence.exe"; Parameters: "--background"; Tasks: startup

[Run]
Filename: "{app}\PlexPresence.exe"; Description: "Launch Plex Presence"; Flags: nowait postinstall skipifsilent
