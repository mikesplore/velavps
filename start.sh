#!/usr/bin/env bash
# ============================================================
# velavps start script
# ============================================================
set -euo pipefail

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
die()  { echo -e "${RED}[x]${NC} $*" >&2; exit 1; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
info() { echo -e "${GREEN}[+]${NC} $*"; }

# ── Load .env ────────────────────────────────────────────────
if [[ -f .env ]]; then
  # Export only KEY=VALUE lines; skip comments and blanks
  set -o allexport
  # shellcheck disable=SC1091
  source .env
  set +o allexport
  info "Loaded .env"
else
  die ".env not found. Copy .env.example → .env and fill in your secrets."
fi

# ── Validate required secrets ────────────────────────────────
missing=()
[[ -z "${VPS_API_KEYS:-}"     ]] && missing+=("VPS_API_KEYS")
[[ -z "${VPS_AGENT_SECRET:-}" ]] && missing+=("VPS_AGENT_SECRET")
if [[ ${#missing[@]} -gt 0 ]]; then
  die "Missing required variables in .env: ${missing[*]}"
fi

# Warn if placeholder values are still set
[[ "${VPS_API_KEYS}"     == "REPLACE_ME" ]] && die "VPS_API_KEYS is still set to REPLACE_ME — update .env"
[[ "${VPS_AGENT_SECRET}" == "REPLACE_ME" ]] && die "VPS_AGENT_SECRET is still set to REPLACE_ME — update .env"

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
