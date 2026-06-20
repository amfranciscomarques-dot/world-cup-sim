@echo off
REM Pull the latest played results, Polymarket odds, and SofaScore player ratings
REM (needs an internet connection, no API key). Writes data/results_2026.json,
REM data/odds_2026.json and data/sofascore_2026.json, which the simulator and HTML
REM dashboard read offline.
title World Cup 2026 - Refresh Data
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
echo Updating played results...
python scripts\update_results.py
echo.
echo Updating Polymarket odds (this fetches per-game price history)...
python scripts\update_odds.py
echo.
echo Updating SofaScore player ratings (in-tournament form)...
python scripts\update_sofascore.py
echo.
echo Done. Run "Open Dashboard.bat" to view the refreshed dashboard.
pause
