#!/usr/bin/env bash
# ============================================================
# velavps start script
# ============================================================
set -euo pipefail

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
die()  { echo -e "${RED}[x]${NC} $*" >&2; exit 1; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
info() { echo -e "${GREEN}[+]${NC} $*"; }

# ── Load .env (optional) ─────────────────────────────────────
if [[ -f .env ]]; then
  # Export only KEY=VALUE lines; skip comments and blanks
  set -o allexport
  # shellcheck disable=SC1091
  source .env
  set +o allexport
  info "Loaded .env"
else
  warn ".env not found; continuing with defaults/config.yaml"
fi

# Optional admin key note
if [[ -z "${VPS_API_KEYS:-}" ]]; then
  warn "VPS_API_KEYS is not set. Admin endpoints that require X-API-Key will reject requests."
fi

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
