#!/usr/bin/env bash
set -euo pipefail

# Squeezelite setup script for Debian/Ubuntu with PipeWire
# - Installs squeezelite
# - Disables legacy system service
# - Creates a user systemd service bound to PipeWire
# - Starts and enables the user service
#
# Usage:
#   ./setup_squeezelite.sh
#   (Home Assistant discovery will find the LMS/MA server automatically; no IP required)
#
# Optional: set SQUEEZELITE_NAME to override the player name (default: hostname)
#   SQUEEZELITE_NAME="Laptop Speaker" ./setup_squeezelite.sh
#
# Requirements:
#   - Run as the desktop user (not root); script will sudo only for apt/systemctl --system
#   - PipeWire running (service wants pipewire.service)

SERVICE_NAME="squeezelite"
USER_SERVICE_DIR="${HOME}/.config/systemd/user"
USER_SERVICE_PATH="${USER_SERVICE_DIR}/${SERVICE_NAME}.service"
PLAYER_NAME="${SQUEEZELITE_NAME:-$(hostname)}"

echo "[1/4] Installing squeezelite..."
sudo apt update -y
sudo apt install -y squeezelite

echo "[2/4] Disabling legacy system squeezelite service (if present)..."
sudo systemctl stop ${SERVICE_NAME} || true
sudo systemctl mask ${SERVICE_NAME} || true
sudo update-rc.d -f ${SERVICE_NAME} remove || true
sudo pkill -9 ${SERVICE_NAME} || true

echo "[3/4] Creating user service at ${USER_SERVICE_PATH}..."
mkdir -p "${USER_SERVICE_DIR}"
cat > "${USER_SERVICE_PATH}" <<EOF
[Unit]
Description=Squeezelite Player for Music Assistant
After=network.target pipewire.service
Wants=pipewire.service

[Service]
ExecStart=/usr/bin/squeezelite -n "${PLAYER_NAME}" -o pipewire
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable ${SERVICE_NAME}.service
systemctl --user restart ${SERVICE_NAME}.service

echo "[4/4] Done. Verify with: systemctl --user status ${SERVICE_NAME}.service"
