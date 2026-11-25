@echo off
title FreeDnet Client
cd /d "%~dp0"

where python >nul 2>&1
if %errorlevel%==0 (
    python freednet_client.py
    goto :end
)

where py >nul 2>&1
if %errorlevel%==0 (
    py -3 freednet_client.py
    goto :end
)

echo Python 3 was not found in PATH.
echo Please install Python 3.8+ from https://www.python.org/downloads/
pause

:end
