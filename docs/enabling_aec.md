# Enabling Acoustic Echo Cancellation

Acoustic Echo Cancellation (AEC) is a type of sound processing used to cancel out the noise coming out of your speaker and going into your mic. In LVA this functionality can be useful to allow LVA to listen to wake words even when audio is playing, particularly when a timer is playing. PulseAudio and PipeWire already provide built-in modules for AEC.

## Docker

Set `ENABLE_ECHO_CANCEL=1` in your `.env` file. The container will automatically load the PulseAudio AEC module and set `AUDIO_INPUT_DEVICE` to `aec_mic` at startup. If the module fails to load, a warning is logged and the application continues with the default audio devices.

```env
ENABLE_ECHO_CANCEL=1
```

For PipeWire or custom configurations, use the manual setup below and set `AUDIO_INPUT_DEVICE` directly.

## Manual Setup

### PulseAudio
```sh
pactl load-module module-echo-cancel source_name=aec_mic aec_method=webrtc
```

To make permanent, add to `/etc/pulse/default.pa`:
```
load-module module-echo-cancel source_name=aec_mic aec_method=webrtc
```

### PipeWire
```sh
pw-cli load-module libpipewire-module-echo-cancel '{ aec.method=webrtc source.props={ node.name=aec_mic } }'
```

To make permanent, add the following to the `context.modules` section of `/etc/pipewire/pipewire.conf`:
```
{ name = libpipewire-module-echo-cancel
    args = {
      aec.method = webrtc
      source.props = { node.name = aec_mic }
    }
}
```

Or if you are using a standard PipeWire installation without a custom `pipewire.conf`, create a new file inside `pipewire.conf.d/`:
```
context.modules = [
  { name = libpipewire-module-echo-cancel
    args = {
      aec.method = webrtc
      source.props = { node.name = aec_mic }
    }
  }
]
```

## Using the AEC Input

In the `.env` file:
```
AUDIO_INPUT_DEVICE="aec_mic"
```

## Changing Source and Sink for AEC

### PulseAudio
```sh
pactl load-module module-echo-cancel source_name=aec_mic aec_method=webrtc source_master=<mic-source> sink_master=<sink>
```

### PipeWire
```sh
pw-cli load-module libpipewire-module-echo-cancel '{ aec.method=webrtc source.props={ node.name=aec_mic node.description="Echo Cancelled Mic" } sink.props={ node.name=aec_sink } capture.props={ node.name=<your-mic-node-name> } playback.props={ node.name=<your-speaker-node-name> } }'
```

To make permanent, add to `context.modules` in `/etc/pipewire/pipewire.conf` or a file in `pipewire.conf.d/`:
```
{ name = libpipewire-module-echo-cancel
    args = {
      aec.method = webrtc
      source.props = {
        node.name = aec_mic
        node.description = "Echo Cancelled Mic"
      }
      sink.props = {
        node.name = aec_sink
      }
      capture.props = {
        node.name = <your-mic-node-name>
      }
      playback.props = {
        node.name = <your-speaker-node-name>
      }
    }
}
```