@echo off
title FreeDnet Client
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" freednet_client.py
    exit /b 0
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" freednet_client.py
    if errorlevel 1 pause
    exit /b 0
)

python -c "import PySide6" 2>nul
if errorlevel 1 (
    echo Dependencies not installed yet. Running install.bat...
    call install.bat
)

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" freednet_client.py
    exit /b 0
)

python freednet_client.py
if errorlevel 1 pause
