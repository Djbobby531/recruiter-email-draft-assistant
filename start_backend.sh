#!/usr/bin/env bash
# Starts the FastAPI backend on http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")/backend"

if [ ! -d ".venv" ]; then
  echo "Creating virtualenv..."
  python3.12 -m venv .venv 2>/dev/null || python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q -r requirements.txt
uvicorn app.main:app --reload --port 8000
