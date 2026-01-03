#!/usr/bin/env bash
set -euo pipefail

# Squeezelite setup script for Debian/Ubuntu with PipeWire/PulseAudio
# - Installs squeezelite
# - Disables legacy system service
# - Auto-detects best audio output device
# - Creates a user systemd service with proper dependencies
# - Starts and enables the user service
#
# Usage:
#   ./setup_squeezelite.sh
#   (Home Assistant discovery will find the LMS/MA server automatically; no IP required)
#
# Optional environment variables:
#   SQUEEZELITE_NAME="Custom Name"    - Override player name (default: hostname)
#   SQUEEZELITE_DEVICE="default"      - Override audio device (default: auto-detect)
#
# Examples:
#   ./setup_squeezelite.sh
#   SQUEEZELITE_NAME="Kitchen" ./setup_squeezelite.sh
#   SQUEEZELITE_DEVICE="plughw:CARD=Headphones,DEV=0" ./setup_squeezelite.sh
#
# Requirements:
#   - Run as the desktop user (not root); script will sudo only for apt/systemctl --system

SERVICE_NAME="squeezelite"
USER_SERVICE_DIR="${HOME}/.config/systemd/user"
USER_SERVICE_PATH="${USER_SERVICE_DIR}/${SERVICE_NAME}.service"
PLAYER_NAME="${SQUEEZELITE_NAME:-$(hostname)}"
LOG_FILE="/tmp/squeezelite.log"

detect_audio_device() {
    # If user specified a device, use it
    if [[ -n "${SQUEEZELITE_DEVICE:-}" ]]; then
        echo "$SQUEEZELITE_DEVICE"
        return
    fi
    
    # Try to find best available device
    local devices
    devices=$(/usr/bin/squeezelite -l 2>/dev/null | grep -E "^\s+(plughw|default|sysdefault)" | awk '{print $1}' || true)
    
    if [[ -z "$devices" ]]; then
        echo "default"
        return
    fi
    
    # Prefer USB audio devices, then plughw, then default
    local usb_device
    usb_device=$(echo "$devices" | grep -i "usb" | head -1 || true)
    if [[ -n "$usb_device" ]]; then
        echo "$usb_device"
        return
    fi
    
    # Try specific card types in order: USB, Headphones, then default
    for card_pattern in "USB" "PowerConf" "Headphones"; do
        local card_device
        card_device=$(echo "$devices" | grep -i "$card_pattern" | grep "plughw" | head -1 || true)
        if [[ -n "$card_device" ]]; then
            echo "$card_device"
            return
        fi
    done
    
    # Fallback to first plughw device
    local plughw_device
    plughw_device=$(echo "$devices" | grep "plughw" | head -1 || true)
    if [[ -n "$plughw_device" ]]; then
        echo "$plughw_device"
        return
    fi
    
    # Last resort: try "pipewire" for non-Pi systems, otherwise "default"
    if [[ -f /proc/device-tree/model ]] && grep -qi "Raspberry Pi" /proc/device-tree/model; then
        echo "default"
    elif grep -qi "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
        echo "default"
    else
        # Check if pipewire device exists
        if echo "$devices" | grep -q "pipewire"; then
            echo "pipewire"
        else
            echo "default"
        fi
    fi
}

test_audio_device() {
    local device="$1"
    echo "Testing audio device: $device"
    
    # Try to start squeezelite briefly to validate device
    timeout 3 /usr/bin/squeezelite -n "test-$$" -o "$device" -d output=info 2>&1 | grep -q "opened device" && {
        pkill -f "squeezelite.*test-$$" 2>/dev/null || true
        echo "✓ Device $device validated successfully"
        return 0
    }
    
    pkill -f "squeezelite.*test-$$" 2>/dev/null || true
    echo "✗ Device $device failed validation"
    return 1
}

echo "[1/5] Installing squeezelite..."
sudo apt update -y
sudo apt install -y squeezelite

echo "[2/5] Detecting audio device..."
OUTPUT_DEVICE=$(detect_audio_device)
echo "Selected audio device: $OUTPUT_DEVICE"

echo "[3/5] Validating audio device..."
if ! test_audio_device "$OUTPUT_DEVICE"; then
    echo "Warning: Initial device validation failed, but will attempt to use it anyway"
    echo "Check logs after service starts: tail -f $LOG_FILE"
fi

echo "[4/5] Disabling legacy system squeezelite service (if present)..."
sudo systemct-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/squeezelite -n "${PLAYER_NAME}" -o ${OUTPUT_DEVICE} -d all=info -f ${LOG_FILE}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable ${SERVICE_NAME}.service
systemctl --user restart ${SERVICE_NAME}.service

echo ""
echo "✓ Setup complete!"
echo ""
echo "Player name: ${PLAYER_NAME}"
echo "Audio device: ${OUTPUT_DEVICE}"
echo "Log file: ${LOG_FILE}"
echo ""
echo "Commands:"
echo "  Status:  systemctl --user status ${SERVICE_NAME}.service"
echo "  Logs:    tail -f ${LOG_FILE}"
echo "  Stop:    systemctl --user stop ${SERVICE_NAME}.service"
echo "  Restart: systemctl --user restart ${SERVICE_NAME}.service"
echo "

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable ${SERVICE_NAME}.service
systemctl --user restart ${SERVICE_NAME}.service

echo "[4/4] Done. Verify with: systemctl --user status ${SERVICE_NAME}.service"
