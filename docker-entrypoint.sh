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

if [ -n "${PIPELINE_ENDED_SOUND}" ]; then
  EXTRA_ARGS+=( "--pipeline-ended-sound" "$PIPELINE_ENDED_SOUND" )

if [ -n "${TIMER_MAX_RING_SECONDS}" ]; then
  EXTRA_ARGS+=( "--timer-max-ring-seconds" "$TIMER_MAX_RING_SECONDS" )
fi

if [ "$ENABLE_OUTPUT_ONLY" = "1" ]; then
  EXTRA_ARGS+=( "--output-only" )
fi


# Add cookie file for pulseaudio to prevent errors
PULSE_COOKIE=${PULSE_COOKIE:-"/run/user/1000/pulse/cookie"}
if [[ "$PULSE_COOKIE" != "DISABLED" ]]; then
  if [ ! -f "$PULSE_COOKIE" ]; then
    echo "Creating PulseAudio cookie file at $PULSE_COOKIE"
    touch "$PULSE_COOKIE"
    chmod 600 "$PULSE_COOKIE"
  fi
fi


### Wait for PulseAudio
# Wait for PulseAudio to be available before starting the application
CP_MAX_RETRIES=30
CP_RETRY_DELAY=1
### while maybe besser?
echo "Checking PulseAudio service status..."
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
