@echo off
REM Run a quick Monte Carlo title-odds report (2000 simulations) and keep the window open.
title World Cup 2026 - Title Odds
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
python -m worldcup.cli odds -n 2000 --top 16
echo.
pause
