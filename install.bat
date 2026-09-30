@echo off
REM Clip Manager - one-time installer. You only need this file: double-click it and it
REM downloads the app, installs everything it needs, and puts a "Clip Manager" icon on
REM your desktop. Running it again later updates and repairs an existing install.
setlocal EnableExtensions

set "REPO_URL=https://github.com/1mthattwitch/twitchclipmanager.git"
set "BRANCH=claude/nifty-goodall-p83xy1"
set "MODEL=qwen2.5vl:7b"
REM Overrides used by the automated tests.
if defined CM_REPO_URL set "REPO_URL=%CM_REPO_URL%"
if defined CM_BRANCH set "BRANCH=%CM_BRANCH%"

REM Hand over to a temporary copy (no "call"): updating the app folder may replace this file.
if /i not "%~1"=="--from-temp" (
  copy /y "%~f0" "%TEMP%\clipmanager-install.bat" >nul
  "%TEMP%\clipmanager-install.bat" --from-temp "%~dp0"
)
title Clip Manager setup

REM Install next to this file if it already sits in the app folder, otherwise in your user folder.
set "HERE=%~2"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"
if not defined CM_DIR (
  if exist "%HERE%\backend\app\main.py" (set "CM_DIR=%HERE%") else (set "CM_DIR=%USERPROFILE%\twitchclipmanager")
)
echo.
echo  Clip Manager setup
echo  ------------------
echo  Installing to: %CM_DIR%
echo.

REM ---- 1. Git (used to download the app and its updates) ----
call :add_known_paths
where git >nul 2>nul
if not errorlevel 1 goto :have_git
echo [1/5] Installing Git...
call :winget Git.Git
if errorlevel 1 goto :stop
call :add_known_paths
where git >nul 2>nul
if errorlevel 1 (
  echo Git was installed but Windows can't find it yet. Restart your PC and run install.bat again.
  goto :stop
)
:have_git
echo [1/5] Git is ready.

REM ---- 2. The app itself: download, or update an existing copy ----
set "GIT_TERMINAL_PROMPT=0"
if exist "%CM_DIR%\.git" goto :update_repo
if exist "%CM_DIR%\backend\app\main.py" goto :adopt_folder
echo [2/5] Downloading Clip Manager...
git clone --branch "%BRANCH%" "%REPO_URL%" "%CM_DIR%"
if errorlevel 1 (
  echo Download failed. Check your internet connection and try again.
  goto :stop
)
goto :have_repo

:adopt_folder
REM A copy that was downloaded as a ZIP: link it to GitHub so it can update itself.
echo [2/5] Linking your existing copy to GitHub for updates...
pushd "%CM_DIR%"
git init --quiet
git remote remove origin >nul 2>nul
git remote add origin "%REPO_URL%"
git fetch --quiet origin "%BRANCH%"
if errorlevel 1 (
  popd
  echo Couldn't reach GitHub. Check your internet connection and try again.
  goto :stop
)
git checkout --quiet -f -B "%BRANCH%" "origin/%BRANCH%"
git branch --quiet --set-upstream-to="origin/%BRANCH%"
popd
goto :have_repo

:update_repo
echo [2/5] Clip Manager is already installed; making sure it's the latest version...
pushd "%CM_DIR%"
REM Repair mode: put the app's own files back exactly as they are on GitHub.
REM Settings, the clip database and downloaded videos are git-ignored and never touched.
git fetch --quiet origin "%BRANCH%"
if errorlevel 1 (
  echo Couldn't reach GitHub, keeping the current version.
) else (
  git checkout --quiet -f -B "%BRANCH%" "origin/%BRANCH%"
  git branch --quiet --set-upstream-to="origin/%BRANCH%"
)
popd

:have_repo
cd /d "%CM_DIR%"

REM ---- 3. Python and the app's components ----
echo [3/5] Checking Python and components...
call scripts\setup-deps.bat
if errorlevel 9 goto :install_python
if errorlevel 1 goto :stop
goto :have_deps

:install_python
echo Installing Python 3.12...
call :winget Python.Python.3.12
if errorlevel 1 goto :stop
call :add_known_paths
call scripts\setup-deps.bat
if errorlevel 9 (
  echo Python was installed but Windows can't find it yet. Restart your PC and run install.bat again.
  goto :stop
)
if errorlevel 1 goto :stop
:have_deps
echo [3/5] Components are ready.

REM ---- 4. Optional: the offline AI ----
set "WANT_OLLAMA=%CM_OLLAMA%"
if not defined WANT_OLLAMA (
  echo.
  echo [4/5] The offline AI runs on your graphics card for free, without internet.
  echo       It needs Ollama plus a one-time download of about 6 GB.
  choice /c YN /m "      Install the offline AI now"
  if errorlevel 2 (set "WANT_OLLAMA=N") else (set "WANT_OLLAMA=Y")
)
if /i "%WANT_OLLAMA%"=="Y" (call :ollama) else (echo [4/5] Skipping the offline AI. You can use Claude online, or run install.bat again later.)

REM ---- 5. Desktop icon ----
echo [5/5] Adding a "Clip Manager" icon to your desktop...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Clip Manager.lnk')); $s.TargetPath=(Join-Path $env:CM_DIR 'ClipManager.bat'); $s.WorkingDirectory=$env:CM_DIR; $s.Description='Twitch Clip Manager'; $s.Save()" >nul 2>nul
if errorlevel 1 echo Couldn't create the desktop icon. Open ClipManager.bat in %CM_DIR% instead.

echo.
echo  All set! From now on, open Clip Manager with the desktop icon.
echo  It checks for updates every time it starts.
echo.
if /i "%CM_NO_LAUNCH%"=="1" exit /b 0
call "%CM_DIR%\ClipManager.bat" --no-update
exit /b 0

:stop
echo.
echo Setup didn't finish. Fix the problem above and run install.bat again.
pause
exit /b 1

REM ================= helpers =================

:winget
REM Installs a package with Windows' built-in installer.
where winget >nul 2>nul
if errorlevel 1 (
  echo This needs "App Installer" from the Microsoft Store: https://apps.microsoft.com/detail/9NBLGGH4NNS1
  echo Install it, then run install.bat again.
  exit /b 1
)
winget install -e --id %1 --silent --accept-package-agreements --accept-source-agreements
REM winget returns non-zero for "already installed"; the caller checks for the program instead.
exit /b 0

:add_known_paths
REM Programs installed a moment ago aren't on this window's PATH yet.
if exist "%ProgramFiles%\Git\cmd\git.exe" set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set "PATH=%LOCALAPPDATA%\Programs\Python\Launcher;%PATH%"
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%PATH%"
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"
exit /b 0

:ollama
where ollama >nul 2>nul
if errorlevel 1 (
  echo [4/5] Installing Ollama...
  call :winget Ollama.Ollama
  call :add_known_paths
)
where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama didn't install. Get it from https://ollama.com/download and run install.bat again.
  exit /b 0
)
ollama list >nul 2>nul
if errorlevel 1 (
  start "" /min ollama serve
  timeout /t 5 /nobreak >nul
)
echo [4/5] Downloading the offline AI model. This is about 6 GB and can take a while...
ollama pull %MODEL%
if errorlevel 1 (echo The model download didn't finish. Run install.bat again to resume it.) else (echo [4/5] Offline AI is ready.)
exit /b 0
