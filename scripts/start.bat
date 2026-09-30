@echo off
setlocal
REM Twitch Clip Manager - Windows launcher. Double-click to run.
cd /d "%~dp0.."

REM Find a real Python 3.10-3.14. Prefer the official "py" launcher; plain "python" can be the
REM Microsoft Store placeholder, which isn't a real install.
set "PY="
for %%V in (3.12 3.11 3.13 3.10 3.14) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,14) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo Python 3.12 is needed. Get the "Windows installer (64-bit)" from https://www.python.org/downloads/windows/
  echo Tick "Add python.exe to PATH" in the installer, then double-click this file again.
  echo.
  pause
  exit /b 1
)

REM Install on first run, or again if a previous install didn't finish.
if not exist ".venv\install-ok" (
  echo First run: installing, this takes a few minutes...
  if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv
    if errorlevel 1 goto :failed
  )
  ".venv\Scripts\python" -m pip install --upgrade pip
  ".venv\Scripts\python" -m pip install -r backend\requirements.txt
  if errorlevel 1 goto :failed
  REM NVIDIA libraries for speech-to-text on the graphics card. Optional: without them it uses the CPU.
  ".venv\Scripts\python" -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
  echo ok> ".venv\install-ok"
)

REM Ollama may be pinned to one GPU with CUDA_VISIBLE_DEVICES; the Clip Manager should see both.
set "CUDA_VISIBLE_DEVICES="
cd backend
"..\.venv\Scripts\python" -m app
pause
exit /b 0

:failed
echo.
echo Installation failed. Check your internet connection and run this file again.
echo If it keeps failing, copy the red text above and ask for help.
pause
exit /b 1
