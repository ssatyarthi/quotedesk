#!/usr/bin/env bash
# One-click start for macOS / Linux. First run creates a virtual environment and installs packages.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Setting up for the first time, this takes a few minutes..."
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt
else
  source .venv/bin/activate
fi
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env - open it, paste your Anthropic API key, save, then run ./run.sh again."
  exit 0
fi
streamlit run app.py
