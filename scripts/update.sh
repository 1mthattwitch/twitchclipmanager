#!/usr/bin/env bash
# Checks GitHub for a newer version and applies it (macOS / Linux twin of scripts/update.bat).
# Never blocks startup: on any problem it says so and the current version keeps running.
cd "${1:-.}" || exit 0
if [ ! -d .git ]; then echo "Auto-update is off for this copy (it wasn't installed with git)."; exit 0; fi
command -v git >/dev/null 2>&1 || { echo "Git isn't installed, so updates can't be checked."; exit 0; }
export GIT_TERMINAL_PROMPT=0
echo "Checking for updates..."
if ! git fetch --quiet origin >/dev/null 2>&1; then
  echo "Couldn't reach GitHub. Starting the version you already have."; exit 0
fi
# Follow whichever of "main" and the development branch (where fixes are pushed) is
# newer, so fixes arrive without anyone having to merge them on GitHub first. Only
# branches this copy can fast-forward to are considered.
DEV="${CM_BRANCH:-claude/nifty-goodall-p83xy1}"
ok() { git cat-file -e "origin/$1:ClipManager.bat" 2>/dev/null && git merge-base --is-ancestor HEAD "origin/$1" 2>/dev/null; }
CHANNEL=""
if ok "$DEV"; then
  CHANNEL="$DEV"
  # main already contains everything on the development branch: prefer main.
  if ok main && git merge-base --is-ancestor "origin/$DEV" origin/main 2>/dev/null; then CHANNEL=main; fi
elif ok main; then
  CHANNEL=main
fi
CUR=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ -n "$CHANNEL" ] && [ "$CUR" != "$CHANNEL" ] && git branch --quiet -f "$CHANNEL" HEAD 2>/dev/null; then
  # Same commit, so no files change here; the update below does the rest.
  git symbolic-ref HEAD "refs/heads/$CHANNEL"
  git branch --quiet --set-upstream-to="origin/$CHANNEL" >/dev/null 2>&1
  if [ "$CHANNEL" = main ]; then echo "Switched to the main update channel."
  else echo "Switched to the newest-fixes update channel."; fi
fi
LOCAL=$(git rev-parse HEAD 2>/dev/null)
REMOTE=$(git rev-parse '@{u}' 2>/dev/null) || { echo "This copy isn't linked to an update channel."; exit 0; }
if [ "$LOCAL" = "$REMOTE" ]; then echo "You have the latest version."; exit 0; fi
if ! git merge-base --is-ancestor HEAD '@{u}' 2>/dev/null; then
  echo "This copy has changes that aren't on GitHub, so it wasn't updated automatically."; exit 0
fi
if ! git diff --quiet HEAD 2>/dev/null; then
  echo "An update is available, but files in the app folder were edited so it can't be applied."; exit 0
fi
echo "Downloading the new version..."
if ! git merge --ff-only --quiet '@{u}' >/dev/null 2>&1; then
  git reset --quiet --hard "$LOCAL" >/dev/null 2>&1
  echo "The update couldn't be applied right now; it will try again next time."; exit 0
fi
echo "Updated to the latest version. $(git rev-list --count "$LOCAL..HEAD") change(s)."
