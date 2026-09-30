#!/usr/bin/env bash
# Twitch Clip Manager - macOS / Linux launcher.
set -e
cd "$(dirname "$0")/.."
if [ ! -d .venv ]; then
  echo "First run: setting things up, this takes a few minutes..."
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r backend/requirements.txt
fi
cd backend
exec ../.venv/bin/python -m app
