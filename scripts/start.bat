@echo off
REM Twitch Clip Manager - Windows launcher. Double-click to run.
cd /d "%~dp0\.."
where python >nul 2>nul || (echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
if not exist .venv (
  echo First run: setting things up, this takes a few minutes...
  python -m venv .venv || (pause & exit /b 1)
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\pip install -r backend\requirements.txt || (pause & exit /b 1)
  REM GPU speech-to-text on Windows needs NVIDIA's CUDA libraries:
  .venv\Scripts\pip install nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*
)
REM Ollama may be pinned to one GPU via CUDA_VISIBLE_DEVICES; this app should see both.
set CUDA_VISIBLE_DEVICES=
cd backend
..\.venv\Scripts\python -m app
pause
