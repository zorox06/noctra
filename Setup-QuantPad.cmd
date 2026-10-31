@echo off
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Install Node.js 20.9 or newer, then run this setup again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" py -3.12 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r backend\requirements.lock.txt
if errorlevel 1 goto failed
call npm.cmd ci
if errorlevel 1 goto failed
call npm.cmd run build
if errorlevel 1 goto failed
echo Setup complete. Launch Start-QuantPad.cmd.
pause
exit /b 0
:failed
echo Setup failed. Review the error above.
pause
exit /b 1
