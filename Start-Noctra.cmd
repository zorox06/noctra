@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Setup-Noctra.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\launcher.py
if errorlevel 1 pause
