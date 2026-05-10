#!/usr/bin/env bash
set -euo pipefail

# Create or reuse a virtual environment in .venv
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

# Activate the virtual environment
# shellcheck disable=SC1091
source .venv/bin/activate

# Upgrade pip and install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# Start the FastAPI application
exec uvicorn main:app --host 0.0.0.0 --port 8000
