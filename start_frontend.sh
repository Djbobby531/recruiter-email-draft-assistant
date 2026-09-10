#!/usr/bin/env bash
# Starts the React dev server on http://localhost:5173
set -euo pipefail
cd "$(dirname "$0")/frontend"

if [ ! -d "node_modules" ]; then
  echo "Installing frontend dependencies..."
  npm install
fi

npm run dev
