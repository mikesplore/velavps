#!/usr/bin/env bash
set -euo pipefail

# Create or reuse a virtual environment in .venv
if [ ! -d ".venv" ]; then
  if ! python3 -m venv .venv 2>/dev/null; then
    echo "ERROR: Failed to create virtual environment."
    echo "Install the Python venv package, e.g. on Ubuntu/Debian: sudo apt update && sudo apt install python3-venv"
    exit 1
  fi
fi

# Activate the virtual environment
# shellcheck disable=SC1091
source .venv/bin/activate

# Upgrade pip and install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# Start the FastAPI application
exec uvicorn main:app --host 0.0.0.0 --port 8000
