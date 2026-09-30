#!/usr/bin/env bash
# Clip Manager - macOS / Linux launcher. Checks for updates, installs what's needed, starts the app.
set -e
cd "$(dirname "$0")/.."
# Run the updater from a temp copy so an update can safely replace it.
if [ "$1" != "--no-update" ]; then
  tmp=$(mktemp); cp scripts/update.sh "$tmp"; bash "$tmp" "$PWD" || true; rm -f "$tmp"
fi
PY=python3
if ! $PY -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,14) else 1)' 2>/dev/null; then
  echo "Python 3.10-3.14 is needed (python3 --version)."; exit 1
fi
# Reinstall on first run, after a broken install, or when an update changed the requirements.
REQHASH=$($PY -c "import hashlib; print(hashlib.sha256(open('backend/requirements.txt','rb').read()).hexdigest())")
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys' 2>/dev/null \
   || [ "$(cat .venv/install-ok 2>/dev/null)" != "$REQHASH" ]; then
  echo "Installing components. The first time takes a few minutes..."
  [ -x .venv/bin/python ] && .venv/bin/python -c 'import sys' 2>/dev/null || { rm -rf .venv; $PY -m venv .venv; }
  .venv/bin/python -m pip install --upgrade pip --quiet --disable-pip-version-check
  .venv/bin/python -m pip install -r backend/requirements.txt --disable-pip-version-check
  echo "$REQHASH" > .venv/install-ok
fi
# Twitch site changes break old yt-dlp versions: fetch the newest at most once a day.
TODAY=$(date +%F)
if [ "$(cat .venv/ytdlp-updated 2>/dev/null)" != "$TODAY" ]; then
  .venv/bin/python -m pip install -U yt-dlp --quiet --disable-pip-version-check --timeout 10 --retries 1 >/dev/null 2>&1 \
    && echo "$TODAY" > .venv/ytdlp-updated || true
fi
[ "$CM_NO_LAUNCH" = "1" ] && exit 0
cd backend
exec ../.venv/bin/python -m app
