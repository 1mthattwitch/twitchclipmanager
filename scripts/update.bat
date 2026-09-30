@echo off
REM Checks GitHub for a newer version of Clip Manager and applies it.
REM Called by ClipManager.bat and install.bat from the app folder. Never blocks startup:
REM if anything goes wrong it says so and the current version keeps running.
setlocal EnableExtensions

REM git pull may rewrite this very file, and cmd reads .bat files from disk as it
REM goes, so run from a temporary copy.
REM Handing over without "call" means that when the copy finishes, control returns
REM straight to whoever called this file.
if /i not "%~1"=="--from-temp" (
  copy /y "%~f0" "%TEMP%\clipmanager-update.bat" >nul
  "%TEMP%\clipmanager-update.bat" --from-temp
)

if not exist ".git" (
  echo Auto-update is off for this copy. Run install.bat once to turn it on.
  exit /b 0
)
where git >nul 2>nul
if errorlevel 1 (
  echo Git isn't installed, so updates can't be checked. Run install.bat to fix this.
  exit /b 0
)

REM Never pop up a password prompt; the repo is public.
set "GIT_TERMINAL_PROMPT=0"
echo Checking for updates...
git fetch --quiet origin >nul 2>nul
if errorlevel 1 (
  echo Couldn't reach GitHub. Starting the version you already have.
  exit /b 0
)

REM Follow whichever of "main" and the development branch (where fixes are pushed) is
REM newer, so fixes arrive without anyone having to merge them on GitHub first. Only
REM branches this copy can fast-forward to are considered.
set "DEV=claude/nifty-goodall-p83xy1"
if defined CM_BRANCH set "DEV=%CM_BRANCH%"
call :pick_channel
if not defined CHANNEL goto :channel_ok
set "CUR="
for /f "delims=" %%A in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "CUR=%%A"
if /i "%CUR%"=="%CHANNEL%" goto :channel_ok
REM Same commit, so no files change here; the update below does the rest.
git branch --quiet -f "%CHANNEL%" HEAD >nul 2>nul
if errorlevel 1 goto :channel_ok
git symbolic-ref HEAD "refs/heads/%CHANNEL%" >nul 2>nul
git branch --quiet --set-upstream-to="origin/%CHANNEL%" >nul 2>nul
if /i "%CHANNEL%"=="main" echo Switched to the main update channel.
if /i not "%CHANNEL%"=="main" echo Switched to the newest-fixes update channel.
:channel_ok

set "LOCAL="
set "REMOTE="
for /f "delims=" %%A in ('git rev-parse HEAD 2^>nul') do set "LOCAL=%%A"
for /f "delims=" %%A in ('git rev-parse "@{u}" 2^>nul') do set "REMOTE=%%A"
if not defined REMOTE (
  echo This copy isn't linked to an update channel. Run install.bat to fix this.
  exit /b 0
)
if "%LOCAL%"=="%REMOTE%" (
  echo You have the latest version.
  exit /b 0
)

REM Only fast-forward. Local edits to the app's own files block the update rather
REM than being thrown away. Your clips, settings and videos are never touched.
git merge-base --is-ancestor HEAD "@{u}" >nul 2>nul
if errorlevel 1 goto :diverged
git diff --quiet HEAD >nul 2>nul
if errorlevel 1 goto :blocked

echo Downloading the new version...
git merge --ff-only --quiet "@{u}" >nul 2>nul
if errorlevel 1 goto :half_applied
set "COUNT=?"
for /f %%N in ('git rev-list --count %LOCAL%..HEAD 2^>nul') do set "COUNT=%%N"
echo Updated to the latest version. %COUNT% change^(s^).
exit /b 0

:diverged
echo This copy has changes that aren't on GitHub, so it wasn't updated automatically.
echo Run install.bat to reset it to the latest version.
exit /b 0

:half_applied
REM The copy was clean before, so putting every app file back loses nothing.
git reset --quiet --hard %LOCAL% >nul 2>nul
echo The update couldn't be applied right now, maybe a file was in use.
echo Starting the version you already have; it will try again next time.
exit /b 0

:blocked
echo An update is available, but files in the app folder were edited so it can't be applied.
echo Run install.bat to repair it. Your clips and settings are kept.
exit /b 0

:pick_channel
REM Sets CHANNEL to the development branch or "main", or leaves it empty.
set "CHANNEL="
set "OK_DEV="
set "OK_MAIN="
call :can_reach "%DEV%" OK_DEV
call :can_reach main OK_MAIN
if not defined OK_DEV goto :pick_main
set "CHANNEL=%DEV%"
REM main already contains everything on the development branch: prefer main.
if not defined OK_MAIN exit /b 0
git merge-base --is-ancestor "origin/%DEV%" origin/main >nul 2>nul
if not errorlevel 1 set "CHANNEL=main"
exit /b 0
:pick_main
if defined OK_MAIN set "CHANNEL=main"
exit /b 0

:can_reach
REM Sets %2=1 if origin/%1 has the app and this copy can fast-forward to it.
git cat-file -e "origin/%~1:ClipManager.bat" >nul 2>nul
if errorlevel 1 exit /b 0
git merge-base --is-ancestor HEAD "origin/%~1" >nul 2>nul
if not errorlevel 1 set "%~2=1"
exit /b 0
