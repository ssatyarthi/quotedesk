@echo off
REM One-click start for Windows. First run creates a virtual environment and installs packages.
cd /d %~dp0
if not exist .venv (
  echo Setting up for the first time, this takes a few minutes...
  python -m venv .venv
  call .venv\Scripts\activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate
)
if not exist .env (
  copy .env.example .env
  echo.
  echo Opened .env - paste your Anthropic API key, save, then run this file again.
  notepad .env
  exit /b
)
streamlit run app.py
