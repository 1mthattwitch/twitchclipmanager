@echo off
REM Clip Manager - double-click this to start. It checks for updates first.
REM This file is deliberately tiny and never changes, so an update never has to replace
REM the file that's running. The real launcher is scripts\launch.bat, run from a temp copy.
REM (Not called start.bat on purpose: "start" is a built-in cmd command.)
if not exist "%~dp0scripts\launch.bat" (
  echo The app folder is incomplete. Run install.bat to repair it.
  pause
  exit /b 1
)
copy /y "%~dp0scripts\launch.bat" "%TEMP%\clipmanager-launch.bat" >nul
"%TEMP%\clipmanager-launch.bat" "%~dp0" %1
