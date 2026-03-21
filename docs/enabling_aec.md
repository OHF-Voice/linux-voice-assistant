# Enabling Acoustic Echo Cancellation

Acoustic Echo Cancellation (AEC) is a type of sound processing used to cancel out the noise coming out of your speaker and going into your mic. In LVA this functionality can be useful to allow LVA to listen to wake words even when audio is playing, particularly when a timer is playing. PulseAudio and PipeWire already provide built-in modules for AEC.

## Enabling AEC

### PulseAudio
```sh
pactl load-module module-echo-cancel source_name=aec_mic aec_method=webrtc
```

### PipeWire

Inside of `pipewire.conf.d` add:
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
```
context.modules = [
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
]
```