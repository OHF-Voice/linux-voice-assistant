# Additional Audio Settings

This document is still in an early stage and will be updated soon.

## Acoustic Echo Cancellation

Acoustic Echo Cancellation (AEC) filters speaker output from the microphone signal so that
TTS playback does not trigger false stop-word detections. It works with both PulseAudio and
PipeWire (via the PulseAudio compatibility layer).

### Docker (recommended)

Set `ENABLE_ECHO_CANCEL=1` in your `.env` file or Portainer environment variables:

```env
ENABLE_ECHO_CANCEL=1
```

The container will automatically load the echo-cancel module and route audio through it
at startup. If the module fails to load (e.g. unsupported hardware), it falls back to the
default audio devices and logs a warning.

### Manual setup

If you are not using Docker, load the echo-cancel module manually:

```sh
pactl load-module module-echo-cancel aec_method=webrtc
```

Verify that the echo-cancelled devices are present:

```sh
pactl list short sources
pactl list short sinks
```

Then start the application pointing to the new devices (the exact names may differ on your
system — use `--list-input-devices` / `--list-output-devices` to find them):

```sh
python3 -m linux_voice_assistant ... \
    --audio-input-device 'echo cancelled' \
    --audio-output-device 'echo cancelled'
```

## Hardware echo cancellation

Some microphone boards include onboard DSP with built-in AEC, which is preferred over
software AEC when available:

- Seeed Respeaker Lite
- Satellite1 Hat

For these devices no additional configuration is needed.
