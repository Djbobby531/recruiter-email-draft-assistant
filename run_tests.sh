#!/usr/bin/env bash
# Runs the backend automated test suite
set -euo pipefail
cd "$(dirname "$0")/backend"
source .venv/bin/activate
python -m pytest -v
