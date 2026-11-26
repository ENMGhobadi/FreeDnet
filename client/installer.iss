; FreeDnet Pro - Windows 11 Next > Next Installer (Inno Setup 6)
; Requests Admin once during install, registers elevated launcher (no UAC prompt on every run),
; and registers cleanly in Windows 11 Settings > Apps > Installed apps for 1-click Uninstall.

[Setup]
AppId={{8F4E2C1A-9B3D-4E7F-A123-456789FREEDN}
AppName=FreeDnet Pro
AppVersion=2.0.0
AppPublisher=FreeDnet
DefaultDirName={autopf}\FreeDnet
DefaultGroupName=FreeDnet Pro
PrivilegesRequired=admin
AllowNoIcons=yes
SetupIconFile=freednet.ico
UninstallDisplayIcon={app}\FreeDnet.exe
UninstallDisplayName=FreeDnet Pro
OutputDir=dist
OutputBaseFilename=FreeDnet-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\FreeDnet.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "launch.vbs"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\FreeDnet Pro"; Filename: "wscript.exe"; Parameters: """{app}\launch.vbs"""; IconFilename: "{app}\FreeDnet.exe"
Name: "{group}\Uninstall FreeDnet Pro"; Filename: "{uninstallexe}"
Name: "{autodesktop}\FreeDnet Pro"; Filename: "wscript.exe"; Parameters: """{app}\launch.vbs"""; IconFilename: "{app}\FreeDnet.exe"; Tasks: desktopicon

[Run]
Filename: "schtasks.exe"; Parameters: "/Create /TN ""FreeDnetPro"" /TR ""\""{app}\FreeDnet.exe\"""" /SC ONCE /ST 00:00 /RL HIGHEST /F"; Flags: runhidden waituntilterminated
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command ""$d = Join-Path $env:APPDATA 'FreeDnet\xray'; New-Item -ItemType Directory -Force -Path $d | Out-Null; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; if (!(Test-Path (Join-Path $d 'xray.exe'))) {{ try {{ Invoke-WebRequest -Uri 'https://github.com/XTLS/Xray-core/releases/latest/download/Xray-windows-64.zip' -OutFile (Join-Path $d 'x.zip') -TimeoutSec 45; Expand-Archive (Join-Path $d 'x.zip') (Join-Path $d '_x') -Force; Copy-Item (Join-Path $d '_x\xray.exe'),(Join-Path $d '_x\geoip.dat'),(Join-Path $d '_x\geosite.dat') $d -Force -ErrorAction SilentlyContinue; Remove-Item (Join-Path $d 'x.zip'),(Join-Path $d '_x') -Recurse -Force }} catch {{}} }}; if (!(Test-Path (Join-Path $d 'wintun.dll'))) {{ try {{ Invoke-WebRequest -Uri 'https://www.wintun.net/builds/wintun-0.14.1.zip' -OutFile (Join-Path $d 'w.zip') -TimeoutSec 45; Expand-Archive (Join-Path $d 'w.zip') (Join-Path $d '_w') -Force; Copy-Item (Join-Path $d '_w\wintun\bin\amd64\wintun.dll') (Join-Path $d 'wintun.dll') -Force; Remove-Item (Join-Path $d 'w.zip'),(Join-Path $d '_w') -Recurse -Force }} catch {{}} }}"""; StatusMsg: "Downloading Xray-core x64 & Wintun driver..."; Flags: runhidden waituntilterminated
Filename: "wscript.exe"; Parameters: """{app}\launch.vbs"""; Description: "Launch FreeDnet Pro"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""FreeDnetPro"" /F"; Flags: runhidden waituntilterminated

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\FreeDnet\xray"
Type: filesandordirs; Name: "{app}"
