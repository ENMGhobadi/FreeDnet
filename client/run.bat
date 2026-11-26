@echo off
title FreeDnet Client
cd /d "%~dp0"
python -c "import PySide6" 2>nul
if errorlevel 1 (
    echo Installing PySide6...
    pip install -r requirements.txt
)
python freednet_client.py
if errorlevel 1 pause
