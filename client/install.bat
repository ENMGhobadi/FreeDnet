@echo off
setlocal enabledelayedexpansion
title FreeDnet Windows 11 Installer
cd /d "%~dp0"

echo ===================================================
echo   FreeDnet Pro - Windows 11 x64 One-Click Installer
echo ===================================================
echo.

REM 1. Check Python 3
python --version >nul 2>&1
if errorlevel 1 (
    echo [1/4] Python not found. Attempting automatic install...
    where winget >nul 2>&1
    if not errorlevel 1 (
        winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
    ) else (
        echo Downloading Python 3.11.9 x64 installer...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%TEMP%\py_setup.exe'"
        "%TEMP%\py_setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
        del "%TEMP%\py_setup.exe" >nul 2>&1
    )
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;%PATH%"
) else (
    echo [1/4] Python detected.
)

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python installation failed. Please install Python 3.10+ from python.org and re-run install.bat.
    pause
    exit /b 1
)

REM 2. Create isolated virtual environment & install PySide6
echo [2/4] Setting up virtual environment (.venv) and installing PySide6...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)
".venv\Scripts\python.exe" -c "import PySide6" >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
    ".venv\Scripts\pip.exe" install -r requirements.txt
    if errorlevel 1 (
        echo Pip failed ^(possible dead Windows/env proxy^). Retrying without proxy...
        set "HTTP_PROXY="
        set "HTTPS_PROXY="
        set "ALL_PROXY="
        set "http_proxy="
        set "https_proxy="
        set "all_proxy="
        set "NO_PROXY=*"
        set "no_proxy=*"
        ".venv\Scripts\pip.exe" install --no-cache-dir -r requirements.txt
        if errorlevel 1 (
            echo ERROR: Failed to install Python dependencies.
            pause
            exit /b 1
        )
    )
) else (
    echo       PySide6 already installed.
)

REM 3. Download Xray-core Windows x64 & Wintun driver for TUN Mode
echo [3/4] Preparing Xray-core x64 and Wintun driver in client\xray\ ...
if not exist "xray" mkdir "xray"
if not exist "xray\xray.exe" (
    echo       Downloading Xray-windows-64.zip...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://github.com/XTLS/Xray-core/releases/latest/download/Xray-windows-64.zip' -OutFile 'xray\xray.zip' -TimeoutSec 60; Expand-Archive -Path 'xray\xray.zip' -DestinationPath 'xray\_tmp' -Force; Copy-Item 'xray\_tmp\xray.exe','xray\_tmp\geoip.dat','xray\_tmp\geosite.dat' 'xray\' -Force -ErrorAction SilentlyContinue; Remove-Item 'xray\xray.zip','xray\_tmp' -Recurse -Force } catch { Write-Host 'WARN: Could not download Xray right now (will auto-download via proxy on first TUN click).' }"
) else (
    echo       xray.exe already exists.
)

if not exist "xray\wintun.dll" (
    echo       Downloading wintun.dll (amd64)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.wintun.net/builds/wintun-0.14.1.zip' -OutFile 'xray\wintun.zip' -TimeoutSec 60; Expand-Archive -Path 'xray\wintun.zip' -DestinationPath 'xray\_wt' -Force; Copy-Item 'xray\_wt\wintun\bin\amd64\wintun.dll' 'xray\wintun.dll' -Force; Remove-Item 'xray\wintun.zip','xray\_wt' -Recurse -Force } catch { Write-Host 'WARN: Could not download wintun.dll right now (will auto-download on first TUN click).' }"
) else (
    echo       wintun.dll already exists.
)

REM 4. Create Desktop Shortcut
echo [4/4] Creating Desktop shortcut (FreeDnet.lnk)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\FreeDnet.lnk'); $s.TargetPath = '%~dp0run.bat'; $s.WorkingDirectory = '%~dp0'; $s.Save()" >nul 2>&1

echo.
echo ===================================================
echo   Installation Complete! Run 'run.bat' or Desktop shortcut.
echo ===================================================
pause
