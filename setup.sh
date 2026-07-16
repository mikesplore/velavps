#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
die()  { echo -e "${RED}[x]${NC} $*" >&2; exit 1; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
info() { echo -e "${GREEN}[+]${NC} $*"; }

SERVICE_NAME="velavps"
SERVICE_FILE="${SERVICE_NAME}.service"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_FILE="${SERVICE_NAME}"
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"
UVICORN_BIN="${PROJECT_DIR}/.venv/bin/uvicorn"
SERVICE_DEST="/etc/systemd/system/${SERVICE_FILE}"
CLI_DEST="/usr/local/bin/${CLI_FILE}"
SERVICE_USER="${SUDO_USER:-${USER}}"
SERVICE_GROUP="${SERVICE_USER}"

command -v systemctl >/dev/null 2>&1 || die "systemctl not found. This script requires systemd."
command -v python3 >/dev/null 2>&1 || die "python3 not found."
[[ -f "${PROJECT_DIR}/${CLI_FILE}" ]] || die "Missing ${CLI_FILE} in project root."

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

TMP_SERVICE="$(mktemp)"
cat > "${TMP_SERVICE}" <<EOF
[Unit]
Description=Vela VPS Relay Service (velavps)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${PROJECT_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${UVICORN_BIN} main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=3
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF

info "Installing systemd service (${SERVICE_USER}@${PROJECT_DIR})..."
if [[ "${EUID}" -eq 0 ]]; then
  cp "${TMP_SERVICE}" "${SERVICE_DEST}"
  rm -f "${TMP_SERVICE}"
  install -m 0755 "${PROJECT_DIR}/${CLI_FILE}" "${CLI_DEST}"
  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}"
  systemctl restart "${SERVICE_NAME}"
else
  sudo cp "${TMP_SERVICE}" "${SERVICE_DEST}"
  rm -f "${TMP_SERVICE}"
  sudo install -m 0755 "${PROJECT_DIR}/${CLI_FILE}" "${CLI_DEST}"
  sudo systemctl daemon-reload
  sudo systemctl enable "${SERVICE_NAME}"
  sudo systemctl restart "${SERVICE_NAME}"
fi

info "Setup complete."
info "Global command installed: ${CLI_DEST}"
info "Usage:"
echo "  velavps --start | --stop | --restart | --status | --logs"
info "Service status command:"
echo "  sudo systemctl status ${SERVICE_NAME}"
info "Live logs command:"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
