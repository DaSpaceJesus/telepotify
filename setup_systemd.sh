#!/usr/bin/env bash
# ==============================================================================
# Helper Script to Install and Enable systemd Service on Ubuntu Server
# ==============================================================================

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER="$(whoami)"
SERVICE_NAME="spotify-sync"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "=== Installing ${SERVICE_NAME} systemd service ==="
echo "Working Directory: ${DIR}"
echo "User: ${CURRENT_USER}"

# Check virtual environment
if [ ! -d "${DIR}/venv" ]; then
    echo "Creating Python virtual environment in ${DIR}/venv..."
    python3 -m venv "${DIR}/venv"
    "${DIR}/venv/bin/pip" install --upgrade pip
    "${DIR}/venv/bin/pip" install -r "${DIR}/requirements.txt"
fi

# Check .env
if [ ! -f "${DIR}/.env" ]; then
    echo "Warning: .env file not found! Copying from .env.example..."
    cp "${DIR}/.env.example" "${DIR}/.env"
    chmod 600 "${DIR}/.env"
    echo "Please edit ${DIR}/.env with your real API credentials before starting the service."
else
    chmod 600 "${DIR}/.env"
fi

# Generate service unit file
echo "Generating ${SERVICE_FILE}..."
sed -e "s|{{USER}}|${CURRENT_USER}|g" \
    -e "s|{{WORKDIR}}|${DIR}|g" \
    "${DIR}/spotify-sync.service.template" | sudo tee "${SERVICE_FILE}" > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"

echo ""
echo "=== Installation Complete! ==="
echo "To start the service:"
echo "  sudo systemctl start ${SERVICE_NAME}"
echo ""
echo "To check live logs:"
echo "  journalctl -u ${SERVICE_NAME} -f"
echo ""
echo "To stop or restart:"
echo "  sudo systemctl restart ${SERVICE_NAME}"
echo "  sudo systemctl stop ${SERVICE_NAME}"
