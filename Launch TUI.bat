@echo off
REM Launch the interactive World Cup simulator terminal UI.
title World Cup 2026 Simulator
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
python -m worldcup.cli tui
if errorlevel 1 (
    echo.
    echo Python failed to start. Make sure Python 3.10+ is installed and on PATH.
    pause
)
