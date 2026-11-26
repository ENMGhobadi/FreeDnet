@echo off
title Build FreeDnet.exe (Windows x64)
cd /d "%~dp0"

echo Building standalone FreeDnet.exe with PyInstaller...
python make_icon.py
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo ERROR: Failed to install PyInstaller / PySide6.
    pause
    exit /b 1
)

python -m PyInstaller --noconfirm --onefile --windowed --icon=freednet.ico --name FreeDnet freednet_client.py
if errorlevel 1 (
    echo ERROR: Build failed.
    pause
    exit /b 1
)

echo.
echo Success! Your standalone executable is ready at:
echo   %cd%\dist\FreeDnet.exe
pause
