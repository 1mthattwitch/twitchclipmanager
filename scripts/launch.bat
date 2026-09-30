@echo off
REM The real launcher, run by ClipManager.bat from a temporary copy.
REM Usage: launch.bat <app folder> [--no-update]
setlocal EnableExtensions
cd /d "%~1"
title Clip Manager

REM Already running? Just open it instead of starting a second copy.
if not exist "%SystemRoot%\System32\curl.exe" goto :not_running
"%SystemRoot%\System32\curl.exe" -s -o nul -m 2 http://127.0.0.1:8765/api/status >nul 2>nul
if errorlevel 1 goto :not_running
echo Clip Manager is already running. Opening it in your browser...
start "" http://localhost:8765
exit /b 0
:not_running

REM The updater also runs from a temp copy, so no file in the app folder is in use
REM while the update replaces files.
if /i "%~2"=="--no-update" goto :updated
copy /y "scripts\update.bat" "%TEMP%\clipmanager-update.bat" >nul
call "%TEMP%\clipmanager-update.bat" --from-temp
:updated

call scripts\setup-deps.bat
if errorlevel 9 goto :nopython
if errorlevel 1 goto :stop

if /i "%CM_NO_LAUNCH%"=="1" exit /b 0
REM Ollama may be pinned to one GPU with CUDA_VISIBLE_DEVICES; the Clip Manager should see both.
set "CUDA_VISIBLE_DEVICES="
echo.
echo Starting Clip Manager. Your browser will open in a moment.
echo Keep this window open while you use it. Close it to stop the app.
echo.
cd backend
"..\.venv\Scripts\python.exe" -m app
echo.
echo Clip Manager has stopped.
pause
exit /b 0

:nopython
echo.
echo Python isn't installed. Run install.bat to set everything up.
:stop
echo.
pause
exit /b 1
