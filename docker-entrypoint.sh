#!/bin/bash
set -e

### Handlers
# Handle parameters
EXTRA_ARGS=()

if [ "$ENABLE_DEBUG" = "1" ]; then
  EXTRA_ARGS+=( "--debug" )
fi

if [ -n "${CLIENT_NAME}" ]; then
  EXTRA_ARGS+=( "--name" "$CLIENT_NAME" )
fi

PREFERENCES_FILE=${PREFERENCES_FILE:-"/app/configuration/preferences.json"}
if [ -n "${PREFERENCES_FILE}" ]; then
  EXTRA_ARGS+=( "--preferences-file" "$PREFERENCES_FILE" )
fi

if [ -n "${NETWORK_INTERFACE}" ]; then
  EXTRA_ARGS+=( "--network-interface" "$NETWORK_INTERFACE" )
fi

# IP-ADDRESS
if [ -n "${HOST}" ]; then
  EXTRA_ARGS+=( "--host" "$HOST" )
fi

PORT=${PORT:-6053}
if [ -n "${PORT}" ]; then
  EXTRA_ARGS+=( "--port" "$PORT" )
fi

if [ -n "${AUDIO_INPUT_DEVICE}" ]; then
  EXTRA_ARGS+=( "--audio-input-device" "$AUDIO_INPUT_DEVICE" )
fi

if [ -n "${AUDIO_OUTPUT_DEVICE}" ]; then
  EXTRA_ARGS+=( "--audio-output-device" "$AUDIO_OUTPUT_DEVICE" )
fi

if [ "$ENABLE_THINKING_SOUND" = "1" ]; then
  EXTRA_ARGS+=( "--enable-thinking-sound" )
fi

if [ -n "${WAKE_WORD_DIR}" ]; then
  EXTRA_ARGS+=( "--wake-word-dir" "$WAKE_WORD_DIR" )
fi

if [ -n "${WAKE_MODEL}" ]; then
  EXTRA_ARGS+=( "--wake-model" "$WAKE_MODEL" )
fi

if [ -n "${STOP_MODEL}" ]; then
  EXTRA_ARGS+=( "--stop-model" "$STOP_MODEL" )
fi

if [ -n "${REFACTORY_SECONDS}" ]; then
  EXTRA_ARGS+=( "--refractory-seconds" "$REFACTORY_SECONDS" )
fi

if [ -n "${WAKEUP_SOUND}" ]; then
  EXTRA_ARGS+=( "--wakeup-sound" "$WAKEUP_SOUND" )
fi

if [ -n "${TIMER_FINISHED_SOUND}" ]; then
  EXTRA_ARGS+=( "--timer-finished-sound" "$TIMER_FINISHED_SOUND" )
fi

if [ -n "${PROCESSING_SOUND}" ]; then
  EXTRA_ARGS+=( "--processing-sound" "$PROCESSING_SOUND" )
fi

if [ -n "${MUTE_SOUND}" ]; then
  EXTRA_ARGS+=( "--mute-sound" "$MUTE_SOUND" )
fi

if [ -n "${UNMUTE_SOUND}" ]; then
  EXTRA_ARGS+=( "--unmute-sound" "$UNMUTE_SOUND" )
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
  ./script/run "$@" "${EXTRA_ARGS[@]}" --list-input-devices
  echo "list output devices"
  ./script/run "$@" "${EXTRA_ARGS[@]}" --list-output-devices
  echo "wait 20s and then starting the application"
  sleep 20
fi

echo "starting application"
exec ./script/run "$@" "${EXTRA_ARGS[@]}"
