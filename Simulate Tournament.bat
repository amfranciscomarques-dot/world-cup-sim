@echo off
REM Simulate one full tournament (random) and keep the window open.
title World Cup 2026 - Full Tournament
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
python -m worldcup.cli tournament
echo.
pause
