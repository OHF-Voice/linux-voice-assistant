# Additional Audio Settings

This document is still in an early stage and will be updated soon.

## Pipewire:

### Pipewire-based Volume Control:

If you set the environment variable `VOLUME_CONTROLLER` to `pipewire`, LVA will use Pipewire for volume control, which
often works with USB volume controls/displays. You may need a daemon to handle external volume buttons, such
as [alsa_volume_from_usb_hid](https://github.com/neildavis/alsa_volume_from_usb_hid)
or [its docker version](https://github.com/machineonamission/alsa_volume_from_usb_hid).

## Pulseaudio:

### Acoustic Echo Cancellation:

Enable the echo cancel PulseAudio module:

``` sh
pactl load-module module-echo-cancel \
  aec_method=webrtc \
  aec_args="analog_gain_control=0 digital_gain_control=1 noise_suppression=1"
```

Verify that the `echo-cancel-source` and `echo-cancel-sink` devices are present:

``` sh
pactl list short sources
pactl list short sinks
```

Use the new devices:

``` sh
# The device names may be different on your system.
# Double check with --list-input-devices and --list-output-devices
python3 -m linux_voice_assistant ... \
     --audio-input-device 'Echo-Cancel Source' \
     --audio-output-device 'pipewire/echo-cancel-sink'
```
