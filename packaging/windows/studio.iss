#ifndef ProjectRoot
  #error ProjectRoot must be supplied
#endif
#define AppVersion "0.3.1"
[Setup]
AppId={{27A6264D-1716-4417-ACDC-AFB52EAEE69D}
AppName=Книжная мастерская
AppVersion={#AppVersion}
AppPublisher=Local Books Studio
DefaultDirName={localappdata}\Programs\LocalBooksStudio
DefaultGroupName=Книжная мастерская
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#ProjectRoot}\dist\windows
OutputBaseFilename=LocalBooksStudio-{#AppVersion}-windows-x64-setup
SetupIconFile={#ProjectRoot}\client\native\assets\icon.ico
UninstallDisplayIcon={app}\local-books-order.exe
Compression=lzma2/fast
SolidCompression=no
DiskSpanning=yes
DiskSliceSize=2000000000
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
InfoBeforeFile={#ProjectRoot}\packaging\windows\readme.txt
AppMutex=LocalBooksStudioInstaller
[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; Flags: checkedonce
[Files]
Source: "{#ProjectRoot}\client\native\target\release\local-books-order.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\build\sidecar-full\books-engine\*"; DestDir: "{app}\engine"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ProjectRoot}\packaging\windows\readme.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\client\native\assets\*-OFL.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion
[Icons]
Name: "{autoprograms}\Книжная мастерская"; Filename: "{app}\local-books-order.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Книжная мастерская"; Filename: "{app}\local-books-order.exe"; WorkingDir: "{app}"; Tasks: desktopicon
[Run]
Filename: "{app}\local-books-order.exe"; Description: "Открыть Книжную мастерскую"; Flags: nowait postinstall skipifsilent
; No UninstallDelete: the user's library, voices, model and license are preserved.
