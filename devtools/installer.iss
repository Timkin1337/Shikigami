; Установщик Shikigami (Inno Setup 6). Собирает devtools/build_release.py:
;   ISCC /DAppVersion=0.9.0 /DSrc=<папка программы> /DOut=<куда положить> devtools\installer.iss
; Ставится для одного пользователя, без прав администратора: модули (голос, распознавание) докачивают
; библиотеки в папку программы, а в Program Files без администратора писать нельзя.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8F3A2C51-6B1E-4C7D-9A2F-5E7B1D3C9A10}
AppName=Shikigami
AppVerName=Shikigami {#AppVersion}
AppVersion={#AppVersion}
AppPublisher=Shikigami contributors
DefaultDirName={localappdata}\Programs\Shikigami
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#Out}
OutputBaseFilename=Shikigami-Setup-{#AppVersion}
SetupIconFile={#Src}\resources\app\assets\shikigami.ico
UninstallDisplayIcon={app}\Shikigami.exe
UninstallDisplayName=Shikigami
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#Src}\LICENSE
CloseApplications=yes

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#Src}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Shikigami"; Filename: "{app}\Shikigami.exe"
Name: "{autodesktop}\Shikigami"; Filename: "{app}\Shikigami.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Shikigami.exe"; Description: "{cm:LaunchProgram,Shikigami}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; библиотеки, которые докачали модули, и кэш Python — программа удаляется целиком;
; настройки, переписка и скачанные модели (config.json, .env, data) остаются, если человек вернётся
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\resources"
Type: filesandordirs; Name: "{app}\__pycache__"
