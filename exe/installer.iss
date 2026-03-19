; ═══════════════════════════════════════════════
; AGBC — Inno Setup 安装脚本
; 生成专业的 Windows .exe 安装程序
;
; 使用方法:
;   1. 安装 Inno Setup 6: https://jrsoftware.org/isinfo.php
;   2. 先运行 build.py 生成 build/ 目录
;   3. 用 Inno Setup 编译此 .iss 文件
; ═══════════════════════════════════════════════

#define AppName "AGBC"
#define AppVersion "1.0.0"
#define AppPublisher "AGBC"
#define AppURL "https://github.com/fjiangming/ABCard"
#define BuildDir "build"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=dist
OutputBaseFilename=AGBC_Setup_{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
LicenseFile=
SetupIconFile=
UninstallDisplayIcon={app}\AGBC.exe

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Python 嵌入式环境
Source: "{#BuildDir}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs

; 应用代码 (编译后的 .pyd + 入口 .py)
Source: "{#BuildDir}\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs

; 数据目录 (配置模板)
Source: "{#BuildDir}\data\*"; DestDir: "{app}\data"; Flags: ignoreversion recursesubdirs createallsubdirs onlyifdoesntexist

; 启动器
Source: "{#BuildDir}\AGBC.bat"; DestDir: "{app}"; Flags: ignoreversion
; 如果有编译的 exe:
; Source: "{#BuildDir}\AGBC.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\AGBC.bat"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\AGBC.bat"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\AGBC.bat"; Description: "启动 AGBC"; Flags: nowait postinstall skipifsilent shellexec
