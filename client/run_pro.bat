@echo off
title FreeDnet Pro Launcher
cd /d "%~dp0"
python -c "import PySide6" 2>nul
if errorlevel 1 (
    echo Installing PySide6 for FreeDnet Pro UI...
    pip install -r requirements.txt
)
python freednet_pro.py
if errorlevel 1 pause
