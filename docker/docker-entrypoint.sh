#!/bin/bash
# Wait for PulseAudio to be available before starting the application
# This handles race conditions after host reboot

MAX_RETRIES=30
RETRY_DELAY=2
PORT=6053

echo "Waiting for PulseAudio..."

for i in $(seq 1 $MAX_RETRIES); do
    if pactl info >/dev/null 2>&1; then
        echo "PulseAudio is ready"
        break
    fi
    
    if [ $i -eq $MAX_RETRIES ]; then
        echo "ERROR: PulseAudio not available after $((MAX_RETRIES * RETRY_DELAY)) seconds"
        exit 1
    fi
    
    echo "Attempt $i/$MAX_RETRIES: PulseAudio not ready, waiting ${RETRY_DELAY}s..."
    sleep $RETRY_DELAY
done

# Wait for port to be free (in case of rapid restarts)
echo "Checking port $PORT..."
for i in $(seq 1 $MAX_RETRIES); do
    if ! ss -tln | grep -q ":${PORT} "; then
        echo "Port $PORT is available"
        break
    fi
    
    if [ $i -eq $MAX_RETRIES ]; then
        echo "ERROR: Port $PORT still in use after $((MAX_RETRIES * RETRY_DELAY)) seconds"
        exit 1
    fi
    
    echo "Attempt $i/$MAX_RETRIES: Port $PORT in use, waiting ${RETRY_DELAY}s..."
    sleep $RETRY_DELAY
done

# Start the voice assistant
exec python3 -m linux_voice_assistant "$@"
