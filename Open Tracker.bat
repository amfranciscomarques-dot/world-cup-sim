@echo off
REM Build the self-contained HTML tracker page (your bets vs the model) and
REM open it in your browser. Reads data/user_bets_2026.json and prices every
REM selection against the engine; writes tracker.html next to this script.
title World Cup 2026 - Bet Tracker
cd /d "%~dp0"
uv run python -m worldcup.cli tracker -n 500
if errorlevel 1 (
    echo.
    echo Python failed to start. Make sure uv is installed (https://docs.astral.sh/uv/).
    pause
)
