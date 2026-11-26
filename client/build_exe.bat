@echo off
setlocal
title Build FreeDnet.exe & FreeDnet-Setup.exe (Windows x64)
cd /d "%~dp0"

echo [1/3] Generating icon & checking dependencies...
set "PY=python"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
)

"%PY%" make_icon.py

"%PY%" -c "import PySide6, PyInstaller, cryptography" >nul 2>&1
if not errorlevel 1 (
    echo PySide6 and PyInstaller already installed. Skipping download.
    goto :build
)

"%PY%" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo.
    echo Pip failed ^(possible dead Windows/env proxy^). Retrying without proxy...
    set "HTTP_PROXY="
    set "HTTPS_PROXY="
    set "ALL_PROXY="
    set "http_proxy="
    set "https_proxy="
    set "all_proxy="
    set "NO_PROXY=*"
    set "no_proxy=*"
    "%PY%" -m pip install --no-cache-dir -r requirements.txt pyinstaller
    if errorlevel 1 (
        echo ERROR: Failed to install PyInstaller / PySide6.
        echo Tip: Turn off Windows System Proxy ^(Settings ^> Network ^> Proxy^) or turn on your VPN/FreeDnet proxy and try again.
        pause
        exit /b 1
    )
)

:build
echo [2/3] Building standalone FreeDnet.exe...
if exist "build" rmdir /s /q "build"
if exist "FreeDnet.spec" del /q "FreeDnet.spec"
if exist "dist\FreeDnet.exe" del /q "dist\FreeDnet.exe"

"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --icon=freednet.ico --add-data "freednet.ico;." --add-data "..\cloudflare\worker.js;cloudflare" --paths . --hidden-import=cryptography --hidden-import=fd_config --hidden-import=fd_dialogs --hidden-import=fd_i18n --hidden-import=fd_net --hidden-import=fd_proxy --hidden-import=fd_themes --name FreeDnet ^
  --exclude-module PySide6.QtWebEngineCore ^
  --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.QtWebEngineQuick ^
  --exclude-module PySide6.QtPdf ^
  --exclude-module PySide6.QtPdfWidgets ^
  --exclude-module PySide6.QtQml ^
  --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.Qt3DCore ^
  --exclude-module PySide6.Qt3DRender ^
  --exclude-module PySide6.QtMultimedia ^
  --exclude-module PySide6.QtSql ^
  --exclude-module PySide6.QtTest ^
  freednet_client.py

if errorlevel 1 (
    echo ERROR: Build failed.
    pause
    exit /b 1
)

if not exist "dist\FreeDnet.exe" (
    echo.
    echo ERROR: PyInstaller finished, but dist\FreeDnet.exe is MISSING!
    echo Windows Defender or your Antivirus almost certainly quarantined it.
    echo Fix: Open Windows Security ^> Virus ^& threat protection ^> Protection history,
    echo      or add "%cd%\dist" to Exclusions and run build_exe.bat again.
    pause
    exit /b 1
)

echo.
echo [3/3] Building Next-Next Windows 11 Installer (FreeDnet-Setup.exe)...
set "ISCC="
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"

if "%ISCC%"=="" (
    where winget >nul 2>&1
    if not errorlevel 1 (
        echo Inno Setup 6 not found. Installing via winget...
        winget install -e --id JRSoftware.InnoSetup --accept-package-agreements --accept-source-agreements
        if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
        if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    )
)

if not "%ISCC%"=="" (
    "%ISCC%" installer.iss
    if exist "dist\FreeDnet-Setup.exe" (
        echo.
        echo Success! Both files are ready:
        echo   Portable EXE : %cd%\dist\FreeDnet.exe
        echo   Setup Wizard : %cd%\dist\FreeDnet-Setup.exe
        pause
        exit /b 0
    )
)

echo.
echo Portable EXE ready: %cd%\dist\FreeDnet.exe
echo NOTE: To also compile FreeDnet-Setup.exe, install Inno Setup 6 ^(jrsoftware.org^) and re-run build_exe.bat.
pause
