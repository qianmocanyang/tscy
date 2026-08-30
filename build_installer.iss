; Inno Setup 脚本：生成 tscy 安装包
; 用法：安装 Inno Setup 后，右键本文件 -> Compile，或在命令行执行 iscc build_installer.iss

#define MyAppName "同声传译 tscy"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "tscy"
#define MyAppExeName "tscy.exe"

[Setup]
AppId={{3A9F2B1C-7E4D-4C5A-9B8E-1D2F3A4B5C6D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\tscy
DefaultGroupName=tscy
OutputDir=dist
OutputBaseFilename=tscy_setup_v1.0
SetupIconFile=assets\logo.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\tscy.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\logo.ico"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "assets\logo.png"; DestDir: "{app}\assets"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\logo.ico"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"; IconFilename: "{app}\assets\logo.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\logo.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
