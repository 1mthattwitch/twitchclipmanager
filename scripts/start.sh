#!/usr/bin/env bash
# Twitch Clip Manager - macOS / Linux launcher.
set -e
cd "$(dirname "$0")/.."
PY=python3
if ! $PY -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,14) else 1)' 2>/dev/null; then
  echo "Python 3.10-3.14 is needed (python3 --version)."; exit 1
fi
# Install on first run, or again if a previous install didn't finish.
if [ ! -f .venv/install-ok ]; then
  echo "First run: installing, this takes a few minutes..."
  [ -x .venv/bin/python ] || $PY -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r backend/requirements.txt
  echo ok > .venv/install-ok
fi
cd backend
exec ../.venv/bin/python -m app
