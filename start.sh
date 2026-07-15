#!/usr/bin/env bash
# ============================================================
# velavps start script
# ============================================================
set -euo pipefail

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
die()  { echo -e "${RED}[x]${NC} $*" >&2; exit 1; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
info() { echo -e "${GREEN}[+]${NC} $*"; }

# ── Config source ─────────────────────────────────────────────
info "Using config.yaml for service configuration"

# ── Virtual environment ──────────────────────────────────────
if [[ ! -d .venv ]]; then
  info "Creating virtual environment..."
  python3 -m venv .venv || die "Failed to create venv. Try: sudo apt install python3-venv"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
info "Dependencies ready"

# ── Launch ───────────────────────────────────────────────────
HOST="${VPS_HOST:-0.0.0.0}"
PORT="${VPS_PORT:-8000}"
info "Starting velavps on ${HOST}:${PORT}"
exec uvicorn main:app --host "$HOST" --port "$PORT"
