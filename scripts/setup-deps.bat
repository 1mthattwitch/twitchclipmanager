@echo off
REM Makes sure Python and the app's components are installed. Run from the app folder.
REM Reinstalls only when needed: first run, a broken install, or an update that
REM changed backend\requirements.txt.
REM Exit codes: 0 = ready, 1 = install failed, 9 = no Python found.
setlocal EnableExtensions

REM Find a real Python 3.10-3.14. Prefer the "py" launcher; plain "python" can be the
REM Microsoft Store placeholder, which isn't a real install.
set "PY="
where py >nul 2>nul
if errorlevel 1 goto :try_python
for %%V in (3.12 3.11 3.13 3.10 3.14) do call :try_py %%V
if defined PY goto :have_python
:try_python
where python >nul 2>nul
if errorlevel 1 exit /b 9
python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,14) else 1)" >nul 2>nul
if errorlevel 1 exit /b 9
set "PY=python"
:have_python

REM A fingerprint of the requirements list decides whether to reinstall.
set "REQHASH="
for /f "delims=" %%H in ('%PY% -c "import hashlib; print(hashlib.sha256(open('backend/requirements.txt','rb').read()).hexdigest())"') do set "REQHASH=%%H"
set "OLDHASH="
if exist ".venv\install-ok" set /p OLDHASH=<".venv\install-ok"

REM A venv whose Python was uninstalled or moved has to be rebuilt.
if not exist ".venv\Scripts\python.exe" goto :venv_checked
".venv\Scripts\python.exe" -c "import sys" >nul 2>nul
if errorlevel 1 rmdir /s /q ".venv"
:venv_checked
if exist ".venv\Scripts\python.exe" if defined REQHASH if "%OLDHASH%"=="%REQHASH%" goto :ytdlp_daily

REM Under install.bat, pip's long output goes to the install log instead of the screen.
set "OUT=con"
if defined LOG set "OUT=%LOG%"
echo.
echo Installing components. The first time takes a few minutes...
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv >>"%OUT%" 2>&1
  if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --disable-pip-version-check >>"%OUT%" 2>&1
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt --disable-pip-version-check >>"%OUT%" 2>&1
if errorlevel 1 goto :failed
REM NVIDIA libraries for speech-to-text on the graphics card. Optional: without them it uses the CPU.
".venv\Scripts\python.exe" -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*" --quiet --disable-pip-version-check >>"%OUT%" 2>&1
if errorlevel 1 echo GPU speech libraries didn't install; speech-to-text will use the CPU instead.
>".venv\install-ok" echo %REQHASH%
echo Components ready.

:ytdlp_daily
REM Twitch site changes break old versions of the clip downloader (yt-dlp), so fetch the
REM newest one at most once a day. Offline or failing is fine: the app has a fallback.
set "TODAY="
for /f "delims=" %%D in ('.venv\Scripts\python.exe -c "import datetime; print(datetime.date.today())"') do set "TODAY=%%D"
if not defined TODAY exit /b 0
set "LASTYT="
if exist ".venv\ytdlp-updated" set /p LASTYT=<".venv\ytdlp-updated"
if "%LASTYT%"=="%TODAY%" exit /b 0
if not defined OUT set "OUT=nul"
".venv\Scripts\python.exe" -m pip install -U yt-dlp --quiet --disable-pip-version-check --timeout 10 --retries 1 >>"%OUT%" 2>&1
if errorlevel 1 exit /b 0
>".venv\ytdlp-updated" echo %TODAY%
exit /b 0

:failed
echo.
echo Installing components failed. Check your internet connection and try again.
if not defined LOG goto :failed_end
echo Last lines of the log:
powershell -NoProfile -Command "Get-Content -Tail 25 -LiteralPath $env:LOG" 2>nul
:failed_end
echo If it keeps failing, copy the red text above and ask for help.
exit /b 1

:try_py
REM Sets PY to "py -<version>" if that version is installed and actually runs.
if defined PY exit /b 0
py -%1 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -%1"
exit /b 0
