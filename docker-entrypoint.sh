#!/bin/bash
set -e

### Generate name for this client
# Get active interface
IFACE=$(ip route | grep default | awk '{print $5}' | head -n 1)

# Check if IFACE is empty
if [ -z "$IFACE" ]; then
    echo "No active network interface found."
fi

# Check if interface directory exists
if [ ! -e "/sys/class/net/$IFACE/address" ]; then
    echo "Interface $IFACE does not exist or has no MAC address."
fi

# Read MAC address and remove ':'
MAC=$(cat /sys/class/net/$IFACE/address | tr -d ':')

if [ -z "$MAC" ]; then
    echo "Could not read MAC address."
fi

# Generate CLIENT_NAME from it
TEMP_CLIENT_NAME="lva-${MAC}"


### Handlers
# Handle parameters
EXTRA_ARGS=""

if [ "$ENABLE_DEBUG" = "1" ]; then
  EXTRA_ARGS="$EXTRA_ARGS --debug"
fi

CLIENT_NAME=${CLIENT_NAME:-$TEMP_CLIENT_NAME}
if [ -n "${CLIENT_NAME}" ]; then
  EXTRA_ARGS="$EXTRA_ARGS --name $CLIENT_NAME"
fi

PREFERENCES_FILE=${PREFERENCES_FILE:-"/app/configuration/preferences.json"}
if [ -n "${PREFERENCES_FILE}" ]; then
  EXTRA_ARGS="$EXTRA_ARGS --preferences-file $PREFERENCES_FILE"
fi

PORT=${PORT:-6053}
if [ -n "${PORT}" ]; then
  EXTRA_ARGS="$EXTRA_ARGS --port $PORT"
fi

if [ -n "${AUDIO_INPUT_DEVICE}" ]; then
  EXTRA_ARGS="$EXTRA_ARGS --audio-input-device $AUDIO_INPUT_DEVICE"
fi

if [ -n "${AUDIO_OUTPUT_DEVICE}" ]; then
  EXTRA_ARGS="$EXTRA_ARGS --audio-output-device $AUDIO_OUTPUT_DEVICE"
fi

if [ "$ENABLE_THINKING_SOUND" = "1" ]; then
  EXTRA_ARGS="$EXTRA_ARGS --enable-thinking-sound"
fi


### Wait for PulseAudio
# Wait for PulseAudio to be available before starting the application
CP_MAX_RETRIES=30
CP_RETRY_DELAY=1
### while maybe besser?
echo "Checking port $PORT..."
for i in $(seq 1 $CP_MAX_RETRIES); do
  # Check if PulseAudio is running
  if pactl info >/dev/null 2>&1; then
    echo "✅ PulseAudio is running"
    break
  fi

  if [ $i -eq $CP_MAX_RETRIES ]; then
      echo "❌ PulseAudio did not start after $CP_MAX_RETRIES seconds"
      exit 2
  fi

  echo "⏳ PulseAudio not running yet, retrying in $CP_RETRY_DELAY s..."
  sleep $CP_RETRY_DELAY
done


### Check port availability
# PORT variable is used from env
PA_MAX_RETRIES=30
PA_RETRY_DELAY=2
echo "Checking port $PORT..."
for i in $(seq 1 $PA_MAX_RETRIES); do
  # Wait for port to be free (in case of rapid restarts)
  if ! ss -tln | grep -q ":${PORT} "; then
      echo "Port $PORT is available"
      break
  fi

  if [ $i -eq $PA_MAX_RETRIES ]; then
      echo "ERROR: Port $PORT still in use after $((PA_MAX_RETRIES * PA_RETRY_DELAY)) seconds"
      exit 2
  fi

  echo "Attempt $i/$PA_MAX_RETRIES: Port $PORT in use, waiting ${PA_RETRY_DELAY}s..."
  sleep $PA_RETRY_DELAY
done


### Start application
if [ "$LIST_DEVICES" = "1" ]; then
  echo "list input devices"
  ./script/run "$@" $EXTRA_ARGS --list-input-devices
  echo "list output devices"
  ./script/run "$@" $EXTRA_ARGS --list-output-devices
else
  echo "starting application"
  exec ./script/run "$@" $EXTRA_ARGS
fi
