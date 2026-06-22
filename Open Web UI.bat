@echo off
REM Start the interactive web UI and open it in your browser.
REM Same features as the TUI: lineups, match/odds/tournament sims, Polymarket
REM comparisons and the model-vs-market dashboard. Leave this window open; close
REM it (or press Ctrl-C) to stop the server.
title World Cup 2026 - Web UI
cd /d "%~dp0"
uv run python -m worldcup.cli web
if errorlevel 1 (
    echo.
    echo Python failed to start. Make sure uv is installed (https://docs.astral.sh/uv/).
    pause
)
