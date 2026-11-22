@echo off
rem Starts the JobBot Brave profile and the helper. Closing Brave stops the helper.
cd /d "%~dp0"
uv run python -m jobbot run
if errorlevel 1 pause
