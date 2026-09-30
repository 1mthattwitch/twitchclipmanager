@echo off
REM Clip Manager - one-time installer. You only need this file: double-click it and it
REM downloads the app, installs everything it needs, finds your existing Ollama models,
REM and puts a "Clip Manager" icon on your desktop. Running it again updates and repairs.
setlocal EnableExtensions

set "REPO_URL=https://github.com/1mthattwitch/twitchclipmanager.git"
set "BRANCH=claude/nifty-goodall-p83xy1"
set "GIT_ZIP_URL=https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/MinGit-2.47.1-64-bit.zip"
set "PY_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
set "OLLAMA_URL=https://ollama.com/download/OllamaSetup.exe"
REM Overrides used by the automated tests.
if defined CM_REPO_URL set "REPO_URL=%CM_REPO_URL%"
if defined CM_BRANCH set "BRANCH=%CM_BRANCH%"

set "CM_HOME=%LOCALAPPDATA%\ClipManager"
set "LOG=%CM_HOME%\install.log"
set "DONE_FLAG=%TEMP%\clipmanager-install.done"

REM Run the real work in a child window from a temporary copy: updating the app folder may
REM replace this file, and if anything crashes this window still explains what happened.
if /i not "%~1"=="--from-temp" (
  if not exist "%CM_HOME%" mkdir "%CM_HOME%"
  del "%DONE_FLAG%" >nul 2>nul
  copy /y "%~f0" "%TEMP%\clipmanager-install.bat" >nul
  cmd /c ""%TEMP%\clipmanager-install.bat" --from-temp "%~dp0.""
  if not exist "%DONE_FLAG%" call :crashed
  exit /b
)
title Clip Manager setup
REM Test hook: simulate the setup window dying unexpectedly.
if defined CM_TEST_CRASH exit /b 3
echo ==== Setup started %DATE% %TIME% ====>>"%LOG%"

REM Where to install: next to this file if it already sits in the app folder, else your user folder.
for %%I in ("%~2") do set "HERE=%%~fI"
set "IS_UNC="
if "%HERE:~0,2%"=="\\" set "IS_UNC=1"
if not defined CM_DIR (
  set "CM_DIR=%USERPROFILE%\twitchclipmanager"
  if not defined IS_UNC if exist "%HERE%\backend\app\main.py" set "CM_DIR=%HERE%"
)
echo.
echo  Clip Manager setup
echo  ------------------
echo  Installing to: %CM_DIR%
echo  A log of every step is kept in %LOG%
echo.
call :disk_check

REM ---- 1. Git: downloads the app and its updates ----
set "STEP=Installing Git"
call :add_known_paths
where git >nul 2>nul
if not errorlevel 1 goto :have_git
echo [1/6] Installing Git...
call :winget Git.Git
call :add_known_paths
where git >nul 2>nul
if not errorlevel 1 goto :have_git
echo       Downloading Git directly instead...
call :download "%GIT_ZIP_URL%" "%TEMP%\mingit.zip"
if errorlevel 1 goto :fail
if not exist "%CM_HOME%\tools\git" mkdir "%CM_HOME%\tools\git"
tar -xf "%TEMP%\mingit.zip" -C "%CM_HOME%\tools\git" >>"%LOG%" 2>&1
call :add_known_paths
where git >nul 2>nul
if errorlevel 1 goto :fail
:have_git
echo [1/6] Git is ready.

REM ---- 2. The app itself ----
set "STEP=Downloading the app"
set "GIT_TERMINAL_PROMPT=0"
echo [2/6] Getting the latest version of Clip Manager...
git ls-remote --heads "%REPO_URL%" >>"%LOG%" 2>&1
if errorlevel 1 (
  echo       Couldn't reach GitHub. Check your internet connection, VPN or firewall.
  goto :fail
)
if exist "%CM_DIR%\.git" goto :update_repo
if exist "%CM_DIR%\backend\app\main.py" goto :adopt_folder
git clone --quiet --no-checkout "%REPO_URL%" "%CM_DIR%" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail
goto :checkout
:adopt_folder
REM A copy downloaded as a ZIP: link it to GitHub so it can update itself.
echo       Linking your existing copy to GitHub for updates...
git -C "%CM_DIR%" init --quiet >>"%LOG%" 2>&1
git -C "%CM_DIR%" remote remove origin >nul 2>nul
git -C "%CM_DIR%" remote add origin "%REPO_URL%" >>"%LOG%" 2>&1
:update_repo
git -C "%CM_DIR%" fetch --quiet origin >>"%LOG%" 2>&1
if errorlevel 1 goto :fail
:checkout
REM Use "main" once it has the app, otherwise the development branch.
set "USE_BRANCH=%BRANCH%"
git -C "%CM_DIR%" cat-file -e "origin/main:ClipManager.bat" >nul 2>nul
if not errorlevel 1 set "USE_BRANCH=main"
REM Repair mode: put the app's own files back exactly as on GitHub. Settings, the clip
REM database and downloaded videos are git-ignored and never touched.
if /i "%HERE%"=="%CM_DIR%" goto :checkout_keep_installer
git -C "%CM_DIR%" checkout --quiet -f -B "%USE_BRANCH%" "origin/%USE_BRANCH%" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail
goto :checked_out
:checkout_keep_installer
REM This installer is running from inside the app folder, so every file except install.bat
REM itself is restored now; the launcher refreshes install.bat on its next start.
git -C "%CM_DIR%" symbolic-ref HEAD "refs/heads/%USE_BRANCH%" >>"%LOG%" 2>&1
git -C "%CM_DIR%" reset --quiet --mixed "origin/%USE_BRANCH%" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail
git -C "%CM_DIR%" checkout --quiet -f -- . ":(exclude)install.bat" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail
:checked_out
git -C "%CM_DIR%" branch --quiet --set-upstream-to="origin/%USE_BRANCH%" >>"%LOG%" 2>&1
echo [2/6] Clip Manager is up to date.
cd /d "%CM_DIR%"

REM ---- 3. Python and the app's components ----
set "STEP=Installing Python and components"
echo [3/6] Checking Python and components...
call scripts\setup-deps.bat
if errorlevel 9 goto :install_python
if errorlevel 1 goto :fail
goto :have_deps
:install_python
echo       Installing Python 3.12...
call :winget Python.Python.3.12
call :add_known_paths
call scripts\setup-deps.bat
if errorlevel 9 goto :python_direct
if errorlevel 1 goto :fail
goto :have_deps
:python_direct
echo       Downloading Python directly instead...
call :download "%PY_URL%" "%TEMP%\python-3.12-installer.exe"
if errorlevel 1 goto :fail
"%TEMP%\python-3.12-installer.exe" /quiet InstallAllUsers=0 Include_launcher=1 PrependPath=0 Include_test=0 >>"%LOG%" 2>&1
call :add_known_paths
call scripts\setup-deps.bat
if errorlevel 9 goto :python_not_found
if errorlevel 1 goto :fail
goto :have_deps
:python_not_found
echo       Python was installed but Windows can't find it yet. Restart your PC and run install.bat again.
goto :fail
:have_deps
echo [3/6] Components are ready.

REM ---- 4. The offline AI, using the models you already have ----
set "STEP=Setting up the offline AI"
call :add_known_paths
where ollama >nul 2>nul
if not errorlevel 1 goto :ollama_setup
set "WANT_OLLAMA=%CM_OLLAMA%"
if defined WANT_OLLAMA goto :ollama_decided
echo.
echo [4/6] The offline AI runs free on your graphics card, without internet.
echo       It needs Ollama, plus a one-time download of about 6 GB unless you already have the model.
choice /c YN /m "      Install the offline AI now"
if errorlevel 2 (set "WANT_OLLAMA=N") else (set "WANT_OLLAMA=Y")
:ollama_decided
if /i not "%WANT_OLLAMA%"=="Y" goto :skip_ollama
echo [4/6] Installing Ollama...
call :winget Ollama.Ollama
call :add_known_paths
where ollama >nul 2>nul
if not errorlevel 1 goto :ollama_setup
call :download "%OLLAMA_URL%" "%TEMP%\OllamaSetup.exe"
if errorlevel 1 goto :ollama_failed
"%TEMP%\OllamaSetup.exe" /VERYSILENT /NORESTART /SUPPRESSMSGBOXES >>"%LOG%" 2>&1
call :add_known_paths
where ollama >nul 2>nul
if errorlevel 1 goto :ollama_failed
:ollama_setup
echo [4/6] Setting up the offline AI. Looking for the models you already have first...
set "YES="
if defined CM_ASSUME_YES set "YES=--yes"
pushd backend
"..\.venv\Scripts\python.exe" -m app.setup_cli ollama %YES%
set "OLLAMA_RC=%ERRORLEVEL%"
popd
if "%OLLAMA_RC%"=="0" goto :after_ollama
echo       The offline AI isn't ready yet. You can finish it in the app: Settings, Run the setup wizard.
goto :after_ollama
:ollama_failed
echo       Ollama didn't install. Get it from https://ollama.com/download and run install.bat again.
goto :after_ollama
:skip_ollama
echo [4/6] Skipping the offline AI. You can use Claude online, or run install.bat again later.
:after_ollama

REM ---- 5. DaVinci Resolve script ----
set "STEP=Installing the Resolve script"
echo [5/6] Checking for DaVinci Resolve...
pushd backend
"..\.venv\Scripts\python.exe" -m app.setup_cli resolve
popd

REM ---- 6. Desktop icon ----
echo [6/6] Adding a "Clip Manager" icon to your desktop...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Clip Manager.lnk')); $s.TargetPath=(Join-Path $env:CM_DIR 'ClipManager.bat'); $s.WorkingDirectory=$env:CM_DIR; $s.Description='Twitch Clip Manager'; $s.Save()" >>"%LOG%" 2>&1
if errorlevel 1 echo       Couldn't create the desktop icon. Open ClipManager.bat in %CM_DIR% instead.

echo ==== Setup finished %DATE% %TIME% ====>>"%LOG%"
echo.
echo  All set! From now on, open Clip Manager with the desktop icon.
echo  It checks for updates every time it starts. The app now opens a setup wizard in your browser.
echo.
echo done>"%DONE_FLAG%"
if /i "%CM_NO_LAUNCH%"=="1" exit /b 0
call "%CM_DIR%\ClipManager.bat" --no-update
exit /b 0

:fail
echo.
echo  Setup stopped at: %STEP%
call :report
echo done>"%DONE_FLAG%"
pause
exit /b 1

REM ================= helpers =================

:crashed
REM The setup window closed without finishing, e.g. an unexpected error.
set "STEP=Setup closed unexpectedly"
call :report
pause
exit /b 1

:report
REM Writes a problem report to paste when asking for help, and opens it in Notepad.
set "REPORT=%USERPROFILE%\Desktop\ClipManager-problem.txt"
if not exist "%USERPROFILE%\Desktop" set "REPORT=%CM_HOME%\ClipManager-problem.txt"
>"%REPORT%" echo Clip Manager setup problem report
>>"%REPORT%" echo Step that failed: %STEP%
>>"%REPORT%" echo Time: %DATE% %TIME%
>>"%REPORT%" ver
>>"%REPORT%" echo Install folder: %CM_DIR%
>>"%REPORT%" echo --- programs ---
>>"%REPORT%" 2>&1 where py
>>"%REPORT%" 2>&1 where python
>>"%REPORT%" 2>&1 where git
>>"%REPORT%" 2>&1 where winget
>>"%REPORT%" 2>&1 where ollama
>>"%REPORT%" 2>&1 py --list
>>"%REPORT%" 2>&1 git --version
>>"%REPORT%" echo --- free space ---
>>"%REPORT%" 2>&1 dir "%USERPROFILE%" /-c
>>"%REPORT%" echo --- last lines of the install log ---
powershell -NoProfile -Command "Get-Content -Tail 80 -LiteralPath $env:LOG" >>"%REPORT%" 2>nul
if errorlevel 1 type "%LOG%" >>"%REPORT%" 2>nul
echo.
echo  A problem report was saved to:
echo    %REPORT%
echo  It opens in Notepad now. Copy everything in it and send it, and the cause can be fixed.
start "" notepad "%REPORT%" >nul 2>nul
exit /b 0

:disk_check
REM Components need about 3 GB and the offline AI model about 6 GB.
set "FREE_GB="
for /f %%F in ('powershell -NoProfile -Command "try { [math]::Floor((Get-PSDrive -Name $env:CM_DIR.Substring(0,1)).Free / 1GB) } catch { }" 2^>nul') do set "FREE_GB=%%F"
if not defined FREE_GB exit /b 0
if %FREE_GB% GEQ 12 exit /b 0
echo  Warning: only %FREE_GB% GB free on this drive. Clip Manager needs about 3 GB, plus about 6 GB
echo  for the offline AI model unless you already have it.
echo.
exit /b 0

:download
REM Downloads %1 to %2 with the curl.exe built into Windows 10 and 11.
echo       Downloading %~nx2...
curl.exe -L --fail --retry 3 -o "%~2" "%~1" >>"%LOG%" 2>&1
if errorlevel 1 goto :download_failed
exit /b 0
:download_failed
echo       Download failed: %~1
exit /b 1

:winget
REM Installs a package with Windows' built-in installer, if this PC has it.
where winget >nul 2>nul
if errorlevel 1 exit /b 0
winget install -e --id %1 --silent --accept-package-agreements --accept-source-agreements >>"%LOG%" 2>&1
REM winget returns non-zero for "already installed"; the caller checks for the program instead.
exit /b 0

:add_known_paths
REM Programs installed a moment ago aren't on this window's PATH yet.
if exist "%CM_HOME%\tools\git\cmd\git.exe" set "PATH=%CM_HOME%\tools\git\cmd;%PATH%"
if exist "%ProgramFiles%\Git\cmd\git.exe" set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set "PATH=%LOCALAPPDATA%\Programs\Python\Launcher;%PATH%"
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%PATH%"
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"
exit /b 0
