@echo off
REM Build the self-contained HTML dashboard and open it in your browser.
REM Uses the offline odds snapshot (data/odds_2026.json). Refresh it first with
REM "Refresh Data.bat" for the latest results and market prices.
title World Cup 2026 - HTML Dashboard
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
python -m worldcup.cli html -n 2000 --game-iterations 1500
if errorlevel 1 (
    echo.
    echo Python failed to start. Make sure Python 3.10+ is installed and on PATH.
    pause
)
