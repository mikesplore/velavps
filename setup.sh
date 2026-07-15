#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
die()  { echo -e "${RED}[x]${NC} $*" >&2; exit 1; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
info() { echo -e "${GREEN}[+]${NC} $*"; }

SERVICE_NAME="velavps"
SERVICE_FILE="${SERVICE_NAME}.service"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"
UVICORN_BIN="${PROJECT_DIR}/.venv/bin/uvicorn"

[[ -f "${PROJECT_DIR}/${SERVICE_FILE}" ]] || die "Missing ${SERVICE_FILE} in project root."
command -v systemctl >/dev/null 2>&1 || die "systemctl not found. This script requires systemd."
command -v python3 >/dev/null 2>&1 || die "python3 not found."

if [[ ! -d "${PROJECT_DIR}/.venv" ]]; then
  info "Creating virtual environment..."
  python3 -m venv "${PROJECT_DIR}/.venv" || die "Failed to create virtual environment."
fi

info "Installing Python dependencies..."
"${PYTHON_BIN}" -m pip install --upgrade pip --quiet
"${PYTHON_BIN}" -m pip install -r "${PROJECT_DIR}/requirements.txt" --quiet

if [[ ! -x "${UVICORN_BIN}" ]]; then
  die "uvicorn binary not found at ${UVICORN_BIN}. Dependency installation may have failed."
fi

info "Installing systemd service..."
if [[ "${EUID}" -eq 0 ]]; then
  cp "${PROJECT_DIR}/${SERVICE_FILE}" "/etc/systemd/system/${SERVICE_FILE}"
  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}"
  systemctl restart "${SERVICE_NAME}"
else
  sudo cp "${PROJECT_DIR}/${SERVICE_FILE}" "/etc/systemd/system/${SERVICE_FILE}"
  sudo systemctl daemon-reload
  sudo systemctl enable "${SERVICE_NAME}"
  sudo systemctl restart "${SERVICE_NAME}"
fi

info "Setup complete."
info "Service status command:"
echo "  sudo systemctl status ${SERVICE_NAME}"
info "Live logs command:"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
